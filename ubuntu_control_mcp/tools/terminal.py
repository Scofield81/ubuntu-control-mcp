"""Persistent terminal toolok.

    terminal_open    - uj interaktiv shell (lokalis PTY vagy tavoli SSH shell)
    terminal_write   - szoveg / parancs kuldese (opcionalisan Enterrel)
    terminal_read    - a felgyult kimenet leolvasasa
    terminal_wait    - varakozas mintara VAGY a kimenet elcsendesedesere
    terminal_ctrl_c  - vezerlobillentyu kuldese (ctrl-c, ctrl-d, up, tab, ...)
    terminal_resize  - a PTY meret allitasa
    terminal_close   - a munkamenet bezarasa
    terminal_list    - a nyitott terminalok listaja

Tipikus hasznalat:
    "Nyiss egy terminalt es inditsd el a szervert, majd hagyd futni."
    -> terminal_open -> terminal_write("npm run dev\\n") -> terminal_wait(pattern="listening")
    "SSH-zz be a prod gepre es nezd meg a logokat."
    -> terminal_open(host="prod") -> terminal_write("journalctl -u api -n 50\\n") -> terminal_read
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..config import Mode
from ..terminals import TERMINALS
from ..permissions import require_mode
from .common import HostAwareInput, ToolInput, tool_errors


class TerminalOpenInput(HostAwareInput):
    shell: Optional[str] = Field(
        default=None, description="Shell binaris (alap: a felhasznalo $SHELL vagy /bin/bash). Csak lokalisan."
    )
    cwd: Optional[str] = Field(default=None, description="Kezdeti munkakonyvtar (csak lokalisan).")


class TerminalWriteInput(ToolInput):
    terminal_id: str = Field(..., description="A 'terminal_open' altal adott azonosito.")
    data: str = Field(..., description="A kuldendo szoveg.")
    enter: bool = Field(default=True, description="Igaz = a szoveg vegere Enter (\\r) kerul.")
    read_after: bool = Field(
        default=True, description="Igaz = rovid varakozas utan visszaadja az uj kimenetet is."
    )


class TerminalReadInput(ToolInput):
    terminal_id: str = Field(..., description="A terminal azonositoja.")
    drain_seconds: float = Field(
        default=0.5, ge=0.1, le=10, description="Ennyi masodpercig gyujti a kimenetet."
    )


class TerminalWaitInput(ToolInput):
    terminal_id: str = Field(..., description="A terminal azonositoja.")
    pattern: Optional[str] = Field(
        default=None, description="Regex, amire varunk a kimenetben. Ures = a kimenet elcsendesedesere varunk."
    )
    timeout: float = Field(default=30.0, ge=1, le=1800, description="Max. varakozas mp-ben.")
    idle: float = Field(
        default=1.5, ge=0.2, le=60,
        description="Ha nincs pattern: ennyi mp nyugalom utan visszater.",
    )


class TerminalKeyInput(ToolInput):
    terminal_id: str = Field(..., description="A terminal azonositoja.")
    key: str = Field(
        ..., description="Vezerlobillentyu: ctrl-c, ctrl-d, ctrl-z, ctrl-l, enter, tab, esc, up, down, left, right."
    )


class TerminalResizeInput(ToolInput):
    terminal_id: str = Field(..., description="A terminal azonositoja.")
    cols: int = Field(..., ge=20, le=500, description="Oszlopok szama.")
    rows: int = Field(..., ge=5, le=200, description="Sorok szama.")


class TerminalCloseInput(ToolInput):
    terminal_id: str = Field(..., description="A bezarando terminal azonositoja.")


class TerminalListInput(ToolInput):
    pass


def register(mcp) -> None:
    @mcp.tool(
        name="terminal_open",
        annotations={"title": "Terminal nyitasa", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def terminal_open(params: TerminalOpenInput) -> str:
        """Uj, hosszu eletu interaktiv shell munkamenet nyitasa.

        Lokalis (host=None): valos PTY, a curses/kepernyokezelo programok is mukodnek.
        Tavoli (host=nev): SSH interaktiv shell a konfiguralt gepen.

        Args:
            params (TerminalOpenInput): host, shell, cwd, response_format.

        Returns:
            str: A letrejott terminal azonositoja + a kezdeti prompt kimenete.
        """
        require_mode(Mode.NORMAL, what="terminal_open")
        term = await TERMINALS.open(
            host=params.host, shell=params.shell, cwd=params.cwd
        )
        initial = term._buffer
        return f"Terminal letrehozva: {term.id} (cel: {term.host or 'lokalis'})\n\n--- kezdeti kimenet ---\n{initial}"

    @mcp.tool(
        name="terminal_write",
        annotations={"title": "Terminal iras", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def terminal_write(params: TerminalWriteInput) -> str:
        """Szoveg / parancs kuldese egy nyitott terminalba.

        Args:
            params (TerminalWriteInput): terminal_id, data, enter, read_after.

        Returns:
            str: Megerosites, es ha read_after igaz, az uj kimenet.
        """
        require_mode(Mode.NORMAL, what="terminal_write")
        term = TERMINALS.get(params.terminal_id)
        payload = params.data + ("\r" if params.enter else "")
        await term.write(payload)
        if not params.read_after:
            return f"Elkuldve a(z) {params.terminal_id} terminalba ({len(payload)} karakter)."
        out = await term.read(0.6)
        return out or "(nincs azonnali kimenet - probald a terminal_read / terminal_wait toolt)"

    @mcp.tool(
        name="terminal_read",
        annotations={"title": "Terminal olvasas", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def terminal_read(params: TerminalReadInput) -> str:
        """Az adott terminal ota felgyult uj kimenetenek leolvasasa.

        Args:
            params (TerminalReadInput): terminal_id, drain_seconds.
        Returns:
            str: Az uj kimenet (ures, ha nem tortent semmi).
        """
        require_mode(Mode.SAFE, what="terminal_read")
        term = TERMINALS.get(params.terminal_id)
        out = await term.read(params.drain_seconds)
        return out or "(nincs uj kimenet)"

    @mcp.tool(
        name="terminal_wait",
        annotations={"title": "Terminal varakozas", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def terminal_wait(params: TerminalWaitInput) -> str:
        """Varakozas: amig egy regex minta megjelenik, VAGY amig a kimenet elcsendesedik.

        Hasznos hosszu parancsoknal (build, telepites) vagy szerver-inditasnal
        ("Listening on port 3000").

        Args:
            params (TerminalWaitInput): terminal_id, pattern, timeout, idle.
        Returns:
            str: Az osszegyujtott kimenet + hogy a mintat sikerult-e megtalalni.
        """
        require_mode(Mode.SAFE, what="terminal_wait")
        output, matched = await TERMINALS.wait_for(
            params.terminal_id,
            pattern=params.pattern,
            timeout=params.timeout,
            idle=params.idle,
        )
        status = (
            "MINTA MEGTALALVA" if matched
            else ("idozites lejart" if params.pattern else "a kimenet elcsendesedett")
        )
        return f"[{status}]\n\n{output}"

    @mcp.tool(
        name="terminal_ctrl_c",
        annotations={"title": "Terminal vezerlobillentyu", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def terminal_ctrl_c(params: TerminalKeyInput) -> str:
        """Vezerlobillentyu / eszkapszekvencia kuldese (ctrl-c, ctrl-d, up, tab, ...).

        A nev 'ctrl_c', de barmelyik tamogatott billentyu megadhato a 'key' mezoben.

        Args:
            params (TerminalKeyInput): terminal_id, key.
        Returns:
            str: Megerosites + rovid kimenet.
        """
        require_mode(Mode.NORMAL, what="terminal_ctrl_c")
        await TERMINALS.send_key(params.terminal_id, params.key)
        term = TERMINALS.get(params.terminal_id)
        out = await term.read(0.5)
        return f"'{params.key}' elkuldve.\n\n{out}".rstrip()

    @mcp.tool(
        name="terminal_resize",
        annotations={"title": "Terminal atmeretezes", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def terminal_resize(params: TerminalResizeInput) -> str:
        """A terminal (PTY) meretenek allitasa - fontos a curses programoknak (htop, vim).

        Args:
            params (TerminalResizeInput): terminal_id, cols, rows.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.NORMAL, what="terminal_resize")
        term = TERMINALS.get(params.terminal_id)
        term.resize(params.cols, params.rows)
        return f"A(z) {params.terminal_id} terminal merete most {params.cols}x{params.rows}."

    @mcp.tool(
        name="terminal_close",
        annotations={"title": "Terminal bezarasa", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def terminal_close(params: TerminalCloseInput) -> str:
        """Egy terminal munkamenet bezarasa es a folyamatainak leallitasa.

        Args:
            params (TerminalCloseInput): terminal_id.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.NORMAL, what="terminal_close")
        await TERMINALS.close(params.terminal_id)
        return f"A(z) {params.terminal_id} terminal bezarva."

    @mcp.tool(
        name="terminal_list",
        annotations={"title": "Terminalok listaja", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def terminal_list(params: TerminalListInput) -> str:
        """A jelenleg nyitott terminal munkamenetek listaja allapottal.

        Returns:
            str: id, tipus, cel, el-e, tetlensegi ido, kor.
        """
        require_mode(Mode.SAFE, what="terminal_list")
        items = TERMINALS.list()
        if not items:
            return "Nincs nyitott terminal."
        lines = ["# Nyitott terminalok", ""]
        for it in items:
            lines.append(
                f"- **{it['id']}** ({it['kind']}, {it['host']}): "
                f"{'el' if it['alive'] else 'HALOTT'}, "
                f"tetlen {it['idle_seconds']}s, kor {it['age_seconds']}s"
            )
        return "\n".join(lines)
