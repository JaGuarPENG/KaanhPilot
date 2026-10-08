"""Append-only JSONL events with bounded, single-process file rotation."""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class AuditSink(Protocol):
    def record(self, event: str, **data) -> None: ...
    def close(self) -> None: ...


class NullAudit:
    def record(self, event: str, **data):
        pass

    def close(self):
        pass


class RaisingFileHandler(RotatingFileHandler):
    def handleError(self, record):
        # Never let logging's default error handler print the unredacted record.
        raise


class JsonlAudit:
    def __init__(self, file: str | Path, *, max_bytes: int = 10 * 1024 * 1024,
                 backups: int = 5, include_text: bool = True, secrets=()):
        if max_bytes < 1 or backups < 1:
            raise ValueError("Log size and backup count must be positive")
        self.path = Path(file).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = RaisingFileHandler(
            self.path, mode="a", maxBytes=max_bytes, backupCount=backups,
            encoding="utf-8", errors="backslashreplace",
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.include_text = include_text
        self.secrets = tuple(value for value in secrets if value)
        self.run_id = uuid4().hex
        self.sequence = 0
        self.lock = threading.RLock()
        self.warned = False
        self.closed = False

    def _clean(self, value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                normalized = str(key).lower().replace("_", "").replace("-", "")
                if not self.include_text and normalized in {"text", "reply", "transcript"}:
                    continue
                if any(word in normalized for word in
                       ("apikey", "accesskey", "authorization", "password", "secret", "token")):
                    result[key] = "[REDACTED]"
                else:
                    result[key] = self._clean(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
        return value

    def record(self, event: str, **data):
        with self.lock:
            if self.closed:
                return
            self.sequence += 1
            try:
                row = {
                    **self._clean(data),
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "event": event, "run_id": self.run_id, "pid": os.getpid(),
                    "sequence": self.sequence,
                }
                line = json.dumps(row, ensure_ascii=False, allow_nan=False)
                self.handler.handle(logging.LogRecord(
                    "voice_agent.audit", logging.INFO, "", 0, line, (), None
                ))
                self.warned = False
            except (OSError, ValueError, TypeError):
                # A post-execution disk error must not cause callers to retry a physical action.
                if not self.warned:
                    logging.getLogger(__name__).warning(
                        "日志写入失败；程序继续运行，请检查日志目录权限和磁盘空间。"
                    )
                    self.warned = True

    def close(self):
        with self.lock:
            if not self.closed:
                self.handler.close()
                self.closed = True
