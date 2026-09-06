"""Egyszeru, kozpontositott audit-naplozas.

Minden potencialisan hatassal biro muvelet (parancs, csomagmuvelet, sudo,
desktop esemeny, engedely-kereses eredmenye) ide kerul. A napló mindig a
stderr-re megy (stdio MCP-nel a stdout tilos), es opcionalisan fajlba is.

A naplozott parancsokbol a nyilvanvalo titkokat maszkoljuk.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from .config import CONFIG

_SECRET_PATTERNS = [
    re.compile(r"(--password[=\s]+)(\S+)", re.IGNORECASE),
    re.compile(r"(-p\s*)(\S{3,})", re.IGNORECASE),
    re.compile(r"(PASS(?:WORD)?=)(\S+)", re.IGNORECASE),
    re.compile(r"(token[=:\s]+)(\S+)", re.IGNORECASE),
]


def mask_secrets(text: str) -> str:
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(r"\1***", out)
    return out


def audit(event: str, **fields: Any) -> None:
    """Egy audit-sor kiirasa. `event` peldaul: 'command', 'sudo', 'permission'."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
    }
    for key, value in fields.items():
        if isinstance(value, str):
            value = mask_secrets(value)
        record[key] = value

    line = json.dumps(record, ensure_ascii=False)
    print(f"[ubuntu-control-mcp][audit] {line}", file=sys.stderr, flush=True)

    if CONFIG.audit_log_path:
        try:
            CONFIG.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with CONFIG.audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # pragma: no cover - IO hiba nem allithatja meg a szervert
            print(
                f"[ubuntu-control-mcp][audit] FIGYELEM: nem sikerult a fajlba iras: {exc}",
                file=sys.stderr,
                flush=True,
            )
