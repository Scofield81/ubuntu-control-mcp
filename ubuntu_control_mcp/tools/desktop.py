"""Desktop control toolok (X11 / Wayland, a lokalis gepen).

    screenshot           - kepernyokep (kep + opcionalis mentes fajlba)
    desktop_ocr          - szoveg kinyerese a kepernyorol / kepbol (tesseract)
    mouse_move           - egermutato mozgatasa (x, y)
    mouse_click          - kattintas (bal/kozep/jobb, opcionalisan pozicio)
    mouse_double_click   - dupla kattintas
    mouse_scroll         - gorgetes
    keyboard_type        - szoveg begepelese
    keyboard_press       - billentyu(kombinacio), pl. 'ctrl+alt+t', 'Return'
    window_list          - ablakok listaja
    window_focus         - ablak elore hozasa (cim / azonosito alapjan)
    window_close         - ablak bezarasa
    launch_application   - alkalmazas inditasa (.desktop nev vagy parancs)

Fuggosegek a celgepen (ha hianyzik, a tool ertheto hibat ad):
    xdotool, wmctrl, scrot VAGY gnome-screenshot VAGY imagemagick(import) VAGY grim,
    tesseract-ocr (csak az OCR-hez).

Minden desktop tool NORMAL modot igenyel, es a config `desktop_enabled`
kapcsoloval globalisan kikapcsolhato.
"""

from __future__ import annotations

import base64
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import Image
from pydantic import Field

from ..audit import audit
from ..config import CONFIG, Mode
from ..executors import run
from ..permissions import PermissionDenied, RiskLevel, ask_permission, require_mode
from .common import Context, ToolInput, tool_errors

_MOUSE_BUTTONS = {"left": 1, "middle": 2, "right": 3}


def _require_desktop(what: str) -> None:
    require_mode(Mode.NORMAL, what=what)
    if not CONFIG.desktop_enabled:
        raise PermissionDenied(
            "A desktop control ki van kapcsolva ezen a szerveren "
            "(config: desktop_enabled=false vagy UBUNTU_CONTROL_DESKTOP_ENABLED=0)."
        )


async def _have(tool: str) -> bool:
    result = await run(f"command -v {tool}", timeout=10)
    return result.exit_code == 0


async def _run_x(command: str, timeout: int = 20):
    """Desktop parancs futtatasa a lokalis gepen, DISPLAY biztositasaval."""
    wrapped = (
        'export DISPLAY="${DISPLAY:-:0}"; '
        'export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"; ' + command
    )
    return await run(wrapped, timeout=timeout)


class EmptyInput(ToolInput):
    pass


class ScreenshotInput(ToolInput):
    save_path: Optional[str] = Field(
        default=None, description="Ha meg van adva, ide is elmentodik a PNG."
    )
    window: Optional[str] = Field(
        default=None, description="Csak az adott cimu / azonositoju ablakrol (ha tamogatott)."
    )
    return_image: bool = Field(
        default=True, description="Igaz = a kep visszakerul a valaszban is."
    )


class OcrInput(ToolInput):
    image_path: Optional[str] = Field(
        default=None, description="Meglevo kepfajl. Ures = eloszor keszit egy kepernyokepet."
    )
    lang: str = Field(default="eng", description="tesseract nyelvkod(ok), pl. 'eng', 'hun', 'hun+eng'.")


class MouseMoveInput(ToolInput):
    x: int = Field(..., ge=0, le=20000, description="Cel X koordinata (keppont).")
    y: int = Field(..., ge=0, le=20000, description="Cel Y koordinata (keppont).")


class MouseClickInput(ToolInput):
    button: str = Field(default="left", description="'left', 'middle' vagy 'right'.")
    x: Optional[int] = Field(default=None, ge=0, le=20000, description="Opcionalis X - elobb odamozog.")
    y: Optional[int] = Field(default=None, ge=0, le=20000, description="Opcionalis Y.")


class MouseScrollInput(ToolInput):
    direction: str = Field(default="down", description="'up' vagy 'down'.")
    amount: int = Field(default=3, ge=1, le=50, description="Gorgetesi lepesek szama.")


class KeyboardTypeInput(ToolInput):
    text: str = Field(..., min_length=1, max_length=10000, description="A begepelendo szoveg.")
    delay_ms: int = Field(default=12, ge=0, le=500, description="Kesleltetes karakterek kozott.")


class KeyboardPressInput(ToolInput):
    keys: str = Field(
        ..., description="Billentyu vagy kombinacio xdotool szintaxissal, pl. 'ctrl+alt+t', 'Return', 'alt+F4'."
    )
    repeat: int = Field(default=1, ge=1, le=50, description="Ismetlesek szama.")


class WindowFocusInput(ToolInput):
    query: str = Field(..., min_length=1, description="Ablak cim reszlet vagy pontos ablak-azonosito (0x...).")


class WindowCloseInput(ToolInput):
    query: str = Field(..., min_length=1, description="Ablak cim reszlet vagy azonosito.")
    force: bool = Field(default=False, description="Igaz = kikenyszeritett bezaras (xkill jellegu).")


class LaunchAppInput(ToolInput):
    application: str = Field(
        ..., min_length=1,
        description="'.desktop' nev (pl. 'firefox', 'org.gnome.Terminal') vagy futtathato parancs.",
    )
    arguments: list[str] = Field(default_factory=list, max_length=30, description="Argumentumok.")


def register(mcp) -> None:
    @mcp.tool(
        name="screenshot",
        annotations={"title": "Kepernyokep", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def screenshot(params: ScreenshotInput):
        """Kepernyokep keszitese a lokalis asztalrol.

        Sorrendben probalja: gnome-screenshot, scrot, imagemagick 'import', grim (Wayland).

        Args:
            params (ScreenshotInput): save_path, window, return_image.

        Returns:
            Image | str: A PNG kep (return_image eseten), kulonben szoveges megerosites
            a mentesi utrol.
        """
        _require_desktop("screenshot")
        tmp = Path(tempfile.gettempdir()) / f"ucm-shot-{int(time.time())}.png"

        if await _have("gnome-screenshot"):
            cmd = f"gnome-screenshot -f {tmp}"
            if params.window:
                cmd = f"gnome-screenshot -w -f {tmp}"
        elif await _have("scrot"):
            cmd = f"scrot -o {tmp}" + (" -u" if params.window else "")
        elif await _have("import"):
            target = "-window root" if not params.window else f"-window {params.window!r}"
            cmd = f"import {target} {tmp}"
        elif await _have("grim"):
            cmd = f"grim {tmp}"
        else:
            return (
                "Nincs telepitve kepernyokep-keszito eszkoz. Telepitsd valamelyiket: "
                "gnome-screenshot / scrot / imagemagick / grim."
            )

        result = await _run_x(cmd, timeout=25)
        if result.exit_code != 0 or not tmp.exists():
            return f"A kepernyokep keszitese sikertelen: {result.stderr or result.stdout}"

        data = tmp.read_bytes()
        audit("screenshot", bytes=len(data), save_path=params.save_path)

        if params.save_path:
            Path(params.save_path).expanduser().write_bytes(data)
        if params.window:
            pass

        if params.return_image:
            try:
                tmp.unlink()
            except OSError:
                pass
            return Image(data=data, format="png")

        b64 = base64.b64encode(data).decode()
        return (
            f"Kepernyokep kesz ({len(data)} byte)."
            + (f" Mentve ide: {params.save_path}." if params.save_path else "")
            + f"\nbase64 (PNG):\n{b64[:120]}..."
        )

    @mcp.tool(
        name="desktop_ocr",
        annotations={"title": "Kepernyo OCR", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def desktop_ocr(params: OcrInput) -> str:
        """Szoveg kinyerese a kepernyorol (vagy egy kepfajlbol) tesseract OCR-rel.

        Args:
            params (OcrInput): image_path, lang.
        Returns:
            str: A felismert szoveg.
        """
        _require_desktop("desktop_ocr")
        if not await _have("tesseract"):
            return "A 'tesseract-ocr' nincs telepitve. Telepitsd: apt_install ['tesseract-ocr']."

        image_path = params.image_path
        cleanup = None
        if not image_path:
            tmp = Path(tempfile.gettempdir()) / f"ucm-ocr-{int(time.time())}.png"
            for tool, cmd in (
                ("gnome-screenshot", f"gnome-screenshot -f {tmp}"),
                ("scrot", f"scrot -o {tmp}"),
                ("import", f"import -window root {tmp}"),
                ("grim", f"grim {tmp}"),
            ):
                if await _have(tool):
                    await _run_x(cmd, timeout=25)
                    break
            image_path = str(tmp)
            cleanup = tmp

        result = await _run_x(f"tesseract {image_path!r} - -l {params.lang}", timeout=40)
        if cleanup:
            try:
                cleanup.unlink()
            except OSError:
                pass
        text = result.stdout.strip()
        return text or f"(nem sikerult szoveget felismerni) {result.stderr}"

    @mcp.tool(
        name="mouse_move",
        annotations={"title": "Eger mozgatas", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def mouse_move(params: MouseMoveInput) -> str:
        """Az egermutato mozgatasa abszolut kepernyo-koordinatara.

        Args:
            params (MouseMoveInput): x, y.
        Returns:
            str: Megerosites.
        """
        _require_desktop("mouse_move")
        result = await _run_x(f"xdotool mousemove {params.x} {params.y}")
        return _ok(result, f"Eger: ({params.x}, {params.y})")

    @mcp.tool(
        name="mouse_click",
        annotations={"title": "Eger kattintas", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def mouse_click(params: MouseClickInput) -> str:
        """Kattintas a megadott (vagy a jelenlegi) pozicion.

        Args:
            params (MouseClickInput): button ('left'|'middle'|'right'), x, y.
        Returns:
            str: Megerosites.
        """
        _require_desktop("mouse_click")
        btn = _MOUSE_BUTTONS.get(params.button)
        if btn is None:
            return f"Ismeretlen gomb: {params.button}. Hasznalj: left, middle, right."
        move = f"xdotool mousemove {params.x} {params.y} && " if params.x is not None and params.y is not None else ""
        result = await _run_x(f"{move}xdotool click {btn}")
        return _ok(result, f"Kattintas ({params.button})")

    @mcp.tool(
        name="mouse_double_click",
        annotations={"title": "Eger dupla kattintas", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def mouse_double_click(params: MouseClickInput) -> str:
        """Dupla kattintas a megadott (vagy jelenlegi) pozicion.

        Args:
            params (MouseClickInput): button, x, y.
        Returns:
            str: Megerosites.
        """
        _require_desktop("mouse_double_click")
        btn = _MOUSE_BUTTONS.get(params.button, 1)
        move = f"xdotool mousemove {params.x} {params.y} && " if params.x is not None and params.y is not None else ""
        result = await _run_x(f"{move}xdotool click --repeat 2 --delay 120 {btn}")
        return _ok(result, "Dupla kattintas")

    @mcp.tool(
        name="mouse_scroll",
        annotations={"title": "Eger gorgetes", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def mouse_scroll(params: MouseScrollInput) -> str:
        """Fuggoleges gorgetes az egermutato jelenlegi pozicion.

        Args:
            params (MouseScrollInput): direction ('up'|'down'), amount.
        Returns:
            str: Megerosites.
        """
        _require_desktop("mouse_scroll")
        btn = 4 if params.direction == "up" else 5
        result = await _run_x(f"xdotool click --repeat {params.amount} {btn}")
        return _ok(result, f"Gorgetes {params.direction} x{params.amount}")

    @mcp.tool(
        name="keyboard_type",
        annotations={"title": "Szoveg begepelese", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def keyboard_type(params: KeyboardTypeInput) -> str:
        """Szoveg begepelese az aktualisan fokuszalt ablakba (xdotool type).

        Args:
            params (KeyboardTypeInput): text, delay_ms.
        Returns:
            str: Megerosites.
        """
        _require_desktop("keyboard_type")
        import shlex as _s

        result = await _run_x(f"xdotool type --delay {params.delay_ms} -- {_s.quote(params.text)}")
        return _ok(result, f"Begepelve {len(params.text)} karakter")

    @mcp.tool(
        name="keyboard_press",
        annotations={"title": "Billentyu lenyomas", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def keyboard_press(params: KeyboardPressInput) -> str:
        """Billentyu vagy kombinacio lenyomasa (xdotool key), pl. 'ctrl+alt+t', 'Return', 'alt+F4'.

        Args:
            params (KeyboardPressInput): keys, repeat.
        Returns:
            str: Megerosites.
        """
        _require_desktop("keyboard_press")
        if any(ch in params.keys for ch in ";|&`$\n\"'"):
            return "A billentyu-kifejezes ervenytelen karaktert tartalmaz."
        result = await _run_x(f"xdotool key --repeat {params.repeat} {params.keys}")
        return _ok(result, f"Billentyu: {params.keys} x{params.repeat}")

    @mcp.tool(
        name="window_list",
        annotations={"title": "Ablakok listaja", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def window_list(params: EmptyInput) -> str:
        """A nyitott ablakok listaja (wmctrl -l): azonosito, asztal, gep, cim.

        Returns:
            str: Ablaklista.
        """
        _require_desktop("window_list")
        if not await _have("wmctrl"):
            return "A 'wmctrl' nincs telepitve. Telepitsd: apt_install ['wmctrl']."
        result = await _run_x("wmctrl -l")
        return result.stdout.strip() or "(nincs listazhato ablak)"

    @mcp.tool(
        name="window_focus",
        annotations={"title": "Ablak fokusz", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def window_focus(params: WindowFocusInput) -> str:
        """Egy ablak elore hozasa es fokuszalasa cim-reszlet vagy azonosito alapjan.

        Args:
            params (WindowFocusInput): query.
        Returns:
            str: Megerosites.
        """
        _require_desktop("window_focus")
        if params.query.startswith("0x"):
            result = await _run_x(f"xdotool windowactivate {params.query}")
        else:
            result = await _run_x(f"wmctrl -a {params.query!r}")
        return _ok(result, f"Ablak fokuszalva: {params.query}")

    @mcp.tool(
        name="window_close",
        annotations={"title": "Ablak bezaras", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def window_close(params: WindowCloseInput, ctx: Context) -> str:
        """Egy ablak bezarasa. 'force' eseten megerositest ker (nem mentett munka veszhet).

        Args:
            params (WindowCloseInput): query, force.
        Returns:
            str: Megerosites.
        """
        _require_desktop("window_close")
        if params.force:
            await ask_permission(
                ctx,
                action=f"Ablak KIKENYSZERITETT bezarasa: {params.query}",
                details="A force bezaras kilovi az ablakhoz tartozo folyamatot - nem mentett adat elveszhet.",
                risk=RiskLevel.MEDIUM,
            )
            result = await _run_x(f"xdotool search --name {params.query!r} windowkill")
        else:
            result = await _run_x(f"wmctrl -c {params.query!r}")
        return _ok(result, f"Ablak bezarva: {params.query}")

    @mcp.tool(
        name="launch_application",
        annotations={"title": "Alkalmazas inditasa", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def launch_application(params: LaunchAppInput) -> str:
        """Grafikus (vagy barmilyen) alkalmazas inditasa a hatterben, levalasztva.

        Eloszor 'gtk-launch <nev>' (.desktop), kudarc eseten kozvetlen parancsindtas.

        Args:
            params (LaunchAppInput): application, arguments.
        Returns:
            str: Megerosites + PID (ha elerheto).
        """
        _require_desktop("launch_application")
        import shlex as _s

        app = params.application
        args = " ".join(_s.quote(a) for a in params.arguments)
        safe_app = _s.quote(app)

        if await _have("gtk-launch") and "/" not in app and not params.arguments:
            launch = f"setsid gtk-launch {safe_app} >/dev/null 2>&1 & echo $!"
        else:
            launch = f"setsid {safe_app} {args} >/dev/null 2>&1 & echo $!"

        result = await _run_x(launch, timeout=15)
        audit("launch_application", application=app, args=params.arguments)
        pid = result.stdout.strip()
        return f"Elinditva: {app}" + (f" (PID {pid})" if pid.isdigit() else "")


def _ok(result, label: str) -> str:
    if result.exit_code == 0:
        return f"OK - {label}"
    return f"HIBA - {label}: {result.stderr.strip() or result.stdout.strip() or 'ismeretlen hiba'}"
