"""Shared Agent Payload Store abstraction (FilePayloadStore, RedisPayloadStore, InMemoryPayloadStore)."""

from __future__ import annotations

import abc
import asyncio
import copy
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _compose_composite_key(tenant_id: str, payload_key: str) -> str:
    clean_tenant = (tenant_id or "default").strip() or "default"
    clean_key = (payload_key or "latest").strip() or "latest"
    return f"agent:payload:{clean_tenant}:{clean_key}"


def _build_record(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    *,
    tenant_id: str,
    payload_key: str,
) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    enriched_meta = {
        "updated_at": now_iso,
        "tenant_id": tenant_id or "default",
        "payload_key": payload_key or "latest",
        **metadata,
    }
    return {
        "payload": payload,
        "metadata": enriched_meta,
    }


class PayloadStore(abc.ABC):
    """Abstract interface for storing and retrieving shared agent JSON payloads."""

    backend_name: str = "abstract"

    @abc.abstractmethod
    async def store(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any]:
        """Store (overwrite) the latest JSON payload for the given tenant and payload_key."""

    @abc.abstractmethod
    async def get_latest(
        self,
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any] | None:
        """Retrieve the latest stored record ({'payload': ..., 'metadata': ...}) or None."""


class InMemoryPayloadStore(PayloadStore):
    """In-memory payload store for unit testing and isolated ephemeral runs."""

    backend_name = "memory"

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def store(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any]:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        record = _build_record(
            payload,
            metadata,
            tenant_id=tenant_id,
            payload_key=payload_key,
        )
        async with self._lock:
            self._records[composite_key] = copy.deepcopy(record)
        return record

    async def get_latest(
        self,
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any] | None:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        async with self._lock:
            record = self._records.get(composite_key)
            return copy.deepcopy(record) if record is not None else None


class FilePayloadStore(PayloadStore):
    """Project-level shared JSON file store (srv/data/agent_payloads.json) with atomic writes."""

    backend_name = "file"

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path).resolve()
        self._lock = asyncio.Lock()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._atomic_write_dict({})

    def _read_dict(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            raw = self.file_path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read payload store file %s: %s", self.file_path, exc)
            return {}

    def _atomic_write_dict(self, data: dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.file_path.parent),
            prefix=f".{self.file_path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(serialized)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self.file_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    async def store(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any]:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        record = _build_record(
            payload,
            metadata,
            tenant_id=tenant_id,
            payload_key=payload_key,
        )
        async with self._lock:
            all_records = self._read_dict()
            all_records[composite_key] = record
            self._atomic_write_dict(all_records)
        return record

    async def get_latest(
        self,
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any] | None:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        async with self._lock:
            all_records = self._read_dict()
            record = all_records.get(composite_key)
            return copy.deepcopy(record) if isinstance(record, dict) else None


class RedisPayloadStore(PayloadStore):
    """Redis-backed payload store for multi-instance Cloud Foundry deployments."""

    backend_name = "redis"

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import redis.asyncio as aioredis  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError(
                    "RedisPayloadStore requires the 'redis' package to be installed."
                ) from exc
            self._client = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._client

    async def store(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any]:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        record = _build_record(
            payload,
            metadata,
            tenant_id=tenant_id,
            payload_key=payload_key,
        )
        client = self._get_client()
        await client.set(composite_key, json.dumps(record, ensure_ascii=False))
        return record

    async def get_latest(
        self,
        *,
        tenant_id: str = "default",
        payload_key: str = "latest",
    ) -> dict[str, Any] | None:
        composite_key = _compose_composite_key(tenant_id, payload_key)
        client = self._get_client()
        raw = await client.get(composite_key)
        if not raw:
            return None
        return dict(json.loads(raw))


_active_store: PayloadStore | None = None


def get_payload_store(settings: Settings | None = None) -> PayloadStore:
    """Return the active singleton PayloadStore instance based on configuration."""
    global _active_store
    if _active_store is not None:
        return _active_store

    cfg = settings or get_settings()
    backend = cfg.payload_store_backend

    if backend == "memory":
        _active_store = InMemoryPayloadStore()
    elif backend == "redis":
        redis_url = cfg.effective_redis_url
        if not redis_url:
            raise RuntimeError("PAYLOAD_STORE_BACKEND='redis' set, but no REDIS_URL or VCAP_SERVICES Redis binding found.")
        _active_store = RedisPayloadStore(redis_url)
    elif backend == "file":
        _active_store = FilePayloadStore(cfg.payload_store_file_path)
    else:
        # "auto": Use Redis if bound/configured, otherwise project-level FilePayloadStore
        redis_url = cfg.effective_redis_url
        if redis_url:
            logger.info("Initializing RedisPayloadStore from configured Redis binding.")
            _active_store = RedisPayloadStore(redis_url)
        else:
            logger.info(
                "Initializing project-level FilePayloadStore at %s",
                cfg.payload_store_file_path,
            )
            _active_store = FilePayloadStore(cfg.payload_store_file_path)

    return _active_store


def reset_payload_store(custom_store: PayloadStore | None = None) -> None:
    """Reset or override the active PayloadStore singleton (used in tests)."""
    global _active_store
    _active_store = custom_store
