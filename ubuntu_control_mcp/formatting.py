"""Kimenet-formazo segedfuggvenyek (JSON / Markdown), ujrahasznalt tobb toolban."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def human_bytes(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} EiB"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}n")
    if hours:
        parts.append(f"{hours}o")
    if minutes:
        parts.append(f"{minutes}p")
    parts.append(f"{secs}mp")
    return " ".join(parts)


def as_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def kv_markdown(title: str, data: dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    for key, value in data.items():
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def render(title: str, data: Any, fmt: ResponseFormat) -> str:
    """Altalanos: dict -> kulcs/ertek Markdown, barmi mas -> JSON."""
    if fmt == ResponseFormat.JSON:
        return as_json(data)
    if isinstance(data, dict):
        return kv_markdown(title, data)
    return as_json(data)


def command_result_text(
    result: "Any",
    *,
    fmt: ResponseFormat = ResponseFormat.MARKDOWN,
    max_output: int = 20000,
) -> str:
    """Egy executors.CommandResult ember/gep-baratsagos megjelenitese."""
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    truncated = False
    if len(stdout) > max_output:
        stdout = stdout[-max_output:]
        truncated = True
    if len(stderr) > max_output:
        stderr = stderr[-max_output:]
        truncated = True

    if fmt == ResponseFormat.JSON:
        return as_json(
            {
                "exit_code": result.exit_code,
                "duration_seconds": round(result.duration, 3),
                "host": result.host or "local",
                "stdout": stdout,
                "stderr": stderr,
                "truncated": truncated,
            }
        )

    status = "OK" if result.exit_code == 0 else f"HIBA (exit {result.exit_code})"
    lines = [
        f"# Parancs eredmeny - {status}",
        f"- **Cel**: {result.host or 'lokalis'}",
        f"- **Idotartam**: {human_duration(result.duration)}",
    ]
    if truncated:
        lines.append("- **Figyelem**: a kimenet le lett vagva (csak a vege lathato).")
    if stdout.strip():
        lines += ["", "## stdout", "```", stdout.rstrip(), "```"]
    if stderr.strip():
        lines += ["", "## stderr", "```", stderr.rstrip(), "```"]
    if not stdout.strip() and not stderr.strip():
        lines += ["", "_(nincs kimenet)_"]
    return "\n".join(lines)
