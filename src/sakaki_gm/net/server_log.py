"""Buffer and fan out table-server log lines to the host desk UI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable


class ServerLogBridge:
    """Collect server process / logger lines for a host desk tab."""

    def __init__(self, *, maxlen: int = 800):
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=int(maxlen))
        self._listeners: list[Callable[[str], None]] = []
        self._handler: logging.Handler | None = None

    def emit_line(self, line: str) -> None:
        text = str(line or "").rstrip("\n")
        if not text:
            return
        with self._lock:
            self._lines.append(text)
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(text)
            except Exception:
                pass

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def subscribe(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners = [
                item for item in self._listeners if item is not callback
            ]

    def as_logging_handler(self) -> logging.Handler:
        """Optional: also capture ``sakaki_gm.table`` logger records."""
        bridge = self

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    msg = self.format(record)
                except Exception:
                    msg = record.getMessage()
                bridge.emit_line(msg)

        handler = _Handler(level=logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        self._handler = handler
        return handler
