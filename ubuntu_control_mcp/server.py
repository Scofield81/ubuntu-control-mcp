"""Az MCP szerver osszeallitasa.

Minden tool-modul `register(mcp)` fuggvenyet meghivjuk. A szerver stdio
transzporton fut (lokalis MCP kliens indit egy alfolyamatkent), de HTTP-re is
valthato a UBUNTU_CONTROL_TRANSPORT=http kornyezeti valtozoval.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from .config import CONFIG
from .tools import REGISTRARS

INSTRUCTIONS = """\
Ubuntu Control MCP - egy Ubuntu/Debian gep teljes koru kezelese (lokalis + tavoli SSH).

Engedely-modok (a szerver egy modban fut, lasd 'system_info'):
  SAFE   - csak olvasas: rendszer-info, folyamatok, naplok, kepernyokep, fajlolvasas
  NORMAL - + csomagtelepites, szolgaltatas-kezeles, fajliras, terminalok, desktop control
  ADMIN  - + sudo, rendszerfrissites/-eltavolitas, tuzfal, felhasznalok, ujrainditas

Ket vedelmi reteg dolgozik:
  1) A mod-kapu eldonti, egy tool egyaltalan futhat-e.
  2) A kockazatos parancsok (rm -rf, reboot, tuzfal, remote/purge, curl|bash, ...)
     a modtol fuggetlenul interaktiv megerositest kernek a felhasznalotol.

Ajanlott munkafolyamatok:
  - "Frissitsd az Ubuntut": apt_update -> apt_list_upgradable -> apt_upgrade
  - Hosszu folyamat / szerver / SSH / REPL: terminal_open -> terminal_write -> terminal_wait
  - Tavoli gep: eloszor host_add (vagy config), majd add meg a 'host' mezot a toolokban.

Mindig a legspecifikusabb toolt hasznald (system_*, apt_*, ...); a 'shell_run' /
'host_run' a menekulesi ut egyedi parancsokhoz.
"""


def build_server() -> FastMCP:
    mcp = FastMCP("ubuntu_control_mcp", instructions=INSTRUCTIONS)
    for register in REGISTRARS:
        register(mcp)
    return mcp


mcp = build_server()


def _startup_banner() -> None:
    tool_count = "?"
    try:
        # FastMCP belso tarolo - csak informativ celbol.
        tool_count = len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        pass
    print(
        f"[ubuntu-control-mcp] indul | mod={CONFIG.mode.value.upper()} "
        f"| toolok={tool_count} | config={CONFIG.config_path} "
        f"| hostok={', '.join(CONFIG.hosts) or '(nincs)'} "
        f"| desktop={'be' if CONFIG.desktop_enabled else 'ki'}",
        file=sys.stderr,
        flush=True,
    )


def run() -> None:
    _startup_banner()
    transport = os.environ.get("UBUNTU_CONTROL_TRANSPORT", "stdio").lower()
    try:
        if transport == "http":
            port = int(os.environ.get("UBUNTU_CONTROL_PORT", "8000"))
            mcp.settings.port = port
            mcp.settings.host = os.environ.get("UBUNTU_CONTROL_HOST", "127.0.0.1")
            mcp.run(transport="streamable-http")
        else:
            mcp.run()
    finally:
        _cleanup()


def _cleanup() -> None:
    import asyncio

    from .executors import close_all_ssh
    from .terminals import TERMINALS

    try:
        asyncio.run(_async_cleanup(close_all_ssh, TERMINALS))
    except Exception:
        pass


async def _async_cleanup(close_all_ssh, terminals) -> None:
    await terminals.close_all()
    await close_all_ssh()
