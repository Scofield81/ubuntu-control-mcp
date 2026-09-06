"""Persistent terminal (PTY / SSH shell) kezeles.

Egy "terminal" egy hosszu eletu interaktiv shell munkamenet, amivel az ugynok
tobb lepesben dolgozhat: htop, REPL-ek, ssh, debuggerek, vi, tail -f, stb.

- Lokalis terminal: valos PTY (`ptyprocess`), igy a teljes kepernyokezelo
  (curses) programok is mukodnek.
- Tavoli terminal: paramiko `invoke_shell()` interaktiv csatorna.

A modell ezekkel a toolokkal dolgozik:
    terminal_open / terminal_write / terminal_read / terminal_wait /
    terminal_ctrl_c / terminal_resize / terminal_close / terminal_list
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .audit import audit
from .config import CONFIG


class TerminalError(Exception):
    pass


_CONTROL_KEYS = {
    "ctrl-c": "\x03",
    "ctrl-d": "\x04",
    "ctrl-z": "\x1a",
    "ctrl-l": "\x0c",
    "enter": "\r",
    "tab": "\t",
    "esc": "\x1b",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
}


@dataclass
class _BaseTerminal:
    id: str
    kind: str  # "local" | "ssh"
    host: Optional[str]
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    _buffer: str = ""

    def _touch(self) -> None:
        self.last_activity = time.time()

    async def write(self, data: str) -> None:  # pragma: no cover - absztrakt
        raise NotImplementedError

    async def read(self, drain_seconds: float = 0.4) -> str:  # pragma: no cover
        raise NotImplementedError

    def is_alive(self) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def info(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "host": self.host or "local",
            "alive": self.is_alive(),
            "idle_seconds": round(time.time() - self.last_activity, 1),
            "age_seconds": round(time.time() - self.created_at, 1),
        }


class LocalTerminal(_BaseTerminal):
    def __init__(self, term_id: str, command: list[str], cwd: Optional[str], env: dict[str, str]):
        super().__init__(id=term_id, kind="local", host=None)
        try:
            from ptyprocess import PtyProcess
        except ImportError as exc:  # pragma: no cover
            raise TerminalError(
                "A lokalis terminalhoz a 'ptyprocess' csomag kell "
                "(pip install ubuntu-control-mcp[pty]). Linux/macOS szukseges."
            ) from exc

        full_env = {**os.environ, **env, "TERM": env.get("TERM", "xterm-256color")}
        self._proc = PtyProcess.spawn(command, cwd=cwd, env=full_env, dimensions=(30, 120))

    async def write(self, data: str) -> None:
        self._touch()
        await asyncio.to_thread(self._proc.write, data.encode())

    async def read(self, drain_seconds: float = 0.4) -> str:
        self._touch()
        chunks: list[str] = []
        deadline = time.monotonic() + drain_seconds
        while time.monotonic() < deadline:
            try:
                data = await asyncio.to_thread(self._proc.read, 65536)
            except EOFError:
                break
            if not data:
                break
            chunks.append(data.decode(errors="replace"))
            await asyncio.sleep(0.05)
        out = "".join(chunks)
        self._buffer += out
        return out

    def is_alive(self) -> bool:
        return self._proc.isalive()

    def resize(self, cols: int, rows: int) -> None:
        self._proc.setwinsize(rows, cols)

    async def close(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass


class SSHTerminal(_BaseTerminal):
    def __init__(self, term_id: str, host_name: str, channel):
        super().__init__(id=term_id, kind="ssh", host=host_name)
        self._chan = channel
        self._chan.settimeout(0.0)

    async def write(self, data: str) -> None:
        self._touch()
        await asyncio.to_thread(self._chan.sendall, data.encode())

    async def read(self, drain_seconds: float = 0.4) -> str:
        self._touch()
        chunks: list[str] = []
        deadline = time.monotonic() + drain_seconds
        while time.monotonic() < deadline:
            got = False
            while self._chan.recv_ready():
                chunks.append(self._chan.recv(65536).decode(errors="replace"))
                got = True
            while self._chan.recv_stderr_ready():
                chunks.append(self._chan.recv_stderr(65536).decode(errors="replace"))
                got = True
            if not got:
                await asyncio.sleep(0.05)
        out = "".join(chunks)
        self._buffer += out
        return out

    def is_alive(self) -> bool:
        return not self._chan.closed and not self._chan.exit_status_ready()

    def resize(self, cols: int, rows: int) -> None:
        self._chan.resize_pty(width=cols, height=rows)

    async def close(self) -> None:
        try:
            self._chan.close()
        except Exception:
            pass


class TerminalManager:
    def __init__(self, max_terminals: int = 16):
        self._terms: dict[str, _BaseTerminal] = {}
        self._max = max_terminals

    async def open(
        self,
        *,
        host: Optional[str] = None,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> _BaseTerminal:
        if len(self._terms) >= self._max:
            raise TerminalError(
                f"Elerted a parhuzamos terminalok maximumat ({self._max}). "
                "Zarj be egyet a 'terminal_close' toollal."
            )
        term_id = f"term-{uuid.uuid4().hex[:8]}"
        env = env or {}

        if host:
            from .executors import get_ssh_client

            hcfg = CONFIG.get_host(host)
            client = await get_ssh_client(hcfg)
            chan = await asyncio.to_thread(
                client.invoke_shell, "xterm-256color", 120, 30
            )
            term: _BaseTerminal = SSHTerminal(term_id, host, chan)
        else:
            command = [shell or os.environ.get("SHELL", "/bin/bash")]
            term = LocalTerminal(term_id, command, cwd, env)

        self._terms[term_id] = term
        audit("terminal_open", terminal=term_id, host=host or "local", shell=shell)
        # A kezdeti prompt begyujtese.
        await asyncio.sleep(0.3)
        await term.read(0.5)
        return term

    def get(self, term_id: str) -> _BaseTerminal:
        if term_id not in self._terms:
            known = ", ".join(self._terms) or "(egy sincs nyitva)"
            raise TerminalError(f"Ismeretlen terminal: '{term_id}'. Nyitott: {known}")
        return self._terms[term_id]

    async def close(self, term_id: str) -> None:
        term = self.get(term_id)
        await term.close()
        self._terms.pop(term_id, None)
        audit("terminal_close", terminal=term_id)

    async def close_all(self) -> None:
        for term_id in list(self._terms):
            try:
                await self.close(term_id)
            except Exception:
                pass

    def list(self) -> list[dict]:
        return [t.info() for t in self._terms.values()]

    async def send_key(self, term_id: str, key: str) -> None:
        term = self.get(term_id)
        seq = _CONTROL_KEYS.get(key.lower().strip())
        if seq is None:
            raise TerminalError(
                f"Ismeretlen vezerlobillentyu: '{key}'. Elerheto: {', '.join(_CONTROL_KEYS)}"
            )
        await term.write(seq)
        audit("terminal_key", terminal=term_id, key=key)

    async def wait_for(
        self,
        term_id: str,
        *,
        pattern: Optional[str] = None,
        timeout: float = 30.0,
        idle: float = 1.0,
    ) -> tuple[str, bool]:
        """Var, amig megjelenik `pattern`, VAGY `idle` mp-ig nincs uj kimenet.

        Visszaad: (osszegyujtott_kimenet, matched).
        """
        import re

        term = self.get(term_id)
        collected: list[str] = []
        deadline = time.monotonic() + timeout
        last_output = time.monotonic()
        rx = re.compile(pattern) if pattern else None

        while time.monotonic() < deadline:
            chunk = await term.read(0.4)
            if chunk:
                collected.append(chunk)
                last_output = time.monotonic()
                if rx and rx.search("".join(collected)):
                    return "".join(collected), True
            else:
                if not rx and (time.monotonic() - last_output) >= idle:
                    return "".join(collected), False
            if not term.is_alive():
                return "".join(collected), bool(rx and rx.search("".join(collected)))
        return "".join(collected), False


# Folyamat-szintu peldany.
TERMINALS = TerminalManager()
