"""Fajlrendszer toolok.

    list_files    - konyvtar tartalma (SAFE)
    read_file     - szoveges fajl olvasasa, opcionalis sor-tartomany (SAFE)
    file_info     - fajl / konyvtar metaadatai (SAFE)
    write_file    - fajl letrehozasa / feluliras (NORMAL)
    edit_file     - pontos szoveg-csere egy fajlban (NORMAL)
    make_dir      - konyvtar letrehozasa (NORMAL)
    move_path     - atnevezes / athelyezes (NORMAL)
    delete_path   - torles - mindig megerositest ker (NORMAL, HIGH kockazat)

A `file_roots` config beallitassal a fajlmuveletek megadott konyvtarakra
korlatozhatok (path traversal ellen). Ures lista = nincs korlat.
Tavoli gepen a muveletek shell-parancsokkal futnak.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Optional

from pydantic import Field

from ..config import CONFIG, Mode
from ..executors import run
from ..formatting import ResponseFormat, as_json, human_bytes
from ..permissions import RiskLevel, ask_permission, require_mode
from .common import Context, HostAwareInput, tool_errors

MAX_READ_BYTES = 512 * 1024


def _check_root(path: str) -> str:
    """Path traversal elleni ellenorzes a lokalis muveletekhez."""
    resolved = Path(path).expanduser()
    try:
        resolved = resolved.resolve()
    except (OSError, RuntimeError):
        pass
    if CONFIG.file_roots:
        allowed = [Path(r).expanduser().resolve() for r in CONFIG.file_roots]
        if not any(_is_relative_to(resolved, root) for root in allowed):
            raise PermissionError(
                f"A(z) {resolved} kivul esik az engedelyezett konyvtarakon: "
                f"{', '.join(str(a) for a in allowed)}"
            )
    return str(resolved)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class ListFilesInput(HostAwareInput):
    path: str = Field(default=".", description="A listazando konyvtar.")
    all: bool = Field(default=False, description="Rejtett bejegyzesek is (ls -a).")
    long: bool = Field(default=True, description="Reszletes lista (ls -l).")


class ReadFileInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A beolvasando fajl utvonala.")
    start_line: Optional[int] = Field(default=None, ge=1, description="Elso sor (1-tol).")
    end_line: Optional[int] = Field(default=None, ge=1, description="Utolso sor (bezarolag).")


class FileInfoInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A vizsgalt fajl / konyvtar.")


class WriteFileInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A cel fajl utvonala.")
    content: str = Field(..., description="A teljes uj tartalom.")
    append: bool = Field(default=False, description="Igaz = hozzafuzes feluliras helyett.")
    create_parents: bool = Field(default=True, description="Hianyzo szulokonyvtarak letrehozasa.")


class EditFileInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A modositando fajl.")
    old_string: str = Field(..., min_length=1, description="A pontos, cserelendo szoveg.")
    new_string: str = Field(..., description="Az uj szoveg.")
    count: int = Field(default=1, ge=1, le=1000, description="Hany elofordulast cserel (elolrol).")


class MakeDirInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A letrehozando konyvtar.")


class MovePathInput(HostAwareInput):
    source: str = Field(..., min_length=1, description="Forras utvonal.")
    destination: str = Field(..., min_length=1, description="Cel utvonal.")


class DeletePathInput(HostAwareInput):
    path: str = Field(..., min_length=1, description="A torlendo fajl / konyvtar.")
    recursive: bool = Field(default=False, description="Konyvtar rekurziv torlese.")


def register(mcp) -> None:
    @mcp.tool(
        name="list_files",
        annotations={"title": "Konyvtar listazas", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def list_files(params: ListFilesInput) -> str:
        """Egy konyvtar tartalmanak listazasa.

        Args:
            params (ListFilesInput): path, all, long, host, response_format.
        Returns:
            str: A konyvtar bejegyzesei.
        """
        require_mode(Mode.SAFE, what="list_files")
        flags = "-h" + ("a" if params.all else "") + ("l" if params.long else "")
        if params.host is None:
            _check_root(params.path)
        result = await run(f"ls {flags} --group-directories-first {shlex.quote(params.path)}",
                           host=params.host, timeout=20)
        if result.exit_code != 0:
            return f"Hiba: {result.stderr.strip()}"
        return result.stdout.strip() or "(ures konyvtar)"

    @mcp.tool(
        name="read_file",
        annotations={"title": "Fajl olvasas", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def read_file(params: ReadFileInput) -> str:
        """Szoveges fajl beolvasasa, opcionalisan csak egy sor-tartomany.

        Args:
            params (ReadFileInput): path, start_line, end_line, host, response_format.
        Returns:
            str: A fajl tartalma (nagy fajlnal levagva).
        """
        require_mode(Mode.SAFE, what="read_file")
        if params.host is None:
            local = _check_root(params.path)
            data = Path(local).read_bytes()[:MAX_READ_BYTES]
            text = data.decode(errors="replace")
            if params.start_line or params.end_line:
                lines = text.splitlines()
                start = (params.start_line or 1) - 1
                end = params.end_line or len(lines)
                text = "\n".join(lines[start:end])
            return text
        if params.start_line or params.end_line:
            start = params.start_line or 1
            end = params.end_line or "$"
            cmd = f"sed -n '{start},{end}p' {shlex.quote(params.path)}"
        else:
            cmd = f"head -c {MAX_READ_BYTES} {shlex.quote(params.path)}"
        result = await run(cmd, host=params.host, timeout=30)
        return result.stdout if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="file_info",
        annotations={"title": "Fajl metaadat", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def file_info(params: FileInfoInput) -> str:
        """Fajl / konyvtar metaadatai: meret, tipus, jogosultsagok, tulajdonos, idobelyegek.

        Args:
            params (FileInfoInput): path, host, response_format.
        Returns:
            str: A metaadatok.
        """
        require_mode(Mode.SAFE, what="file_info")
        if params.host is None:
            local = _check_root(params.path)
            st = os.stat(local)
            data = {
                "path": local,
                "type": "konyvtar" if os.path.isdir(local) else "fajl",
                "size": human_bytes(st.st_size),
                "permissions": oct(st.st_mode)[-3:],
                "uid": st.st_uid,
                "gid": st.st_gid,
                "modified": __import__("time").strftime("%Y-%m-%d %H:%M:%S",
                                                        __import__("time").localtime(st.st_mtime)),
            }
            return as_json(data) if params.response_format == ResponseFormat.JSON else "\n".join(
                f"- **{k}**: {v}" for k, v in data.items()
            )
        result = await run(f"stat {shlex.quote(params.path)}", host=params.host, timeout=20)
        return result.stdout if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="write_file",
        annotations={"title": "Fajl iras", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def write_file(params: WriteFileInput, ctx: Context) -> str:
        """Fajl letrehozasa vagy felulirasa (vagy hozzafuzes).

        Letezo fajl felulirasa eseten megerositest ker.

        Args:
            params (WriteFileInput): path, content, append, create_parents, host.
        Returns:
            str: Megerosites (kiirt byte-ok szama).
        """
        require_mode(Mode.NORMAL, what="write_file")

        exists = await _path_exists(params.host, params.path)
        if exists and not params.append:
            await ask_permission(
                ctx,
                action=f"Letezo fajl felulirasa: {params.path}",
                details=f"A fajl mar letezik es teljesen felul lesz irva ({len(params.content)} byte uj tartalom).",
                risk=RiskLevel.MEDIUM,
                host=params.host,
            )

        if params.host is None:
            local = _check_root(params.path)
            target = Path(local)
            if params.create_parents:
                target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if params.append else "w"
            with target.open(mode, encoding="utf-8") as handle:
                handle.write(params.content)
            return f"OK - {len(params.content)} karakter irva ide: {local}"

        redirect = ">>" if params.append else ">"
        mkparent = f"mkdir -p $(dirname {shlex.quote(params.path)}) && " if params.create_parents else ""
        heredoc = f"{mkparent}cat {redirect} {shlex.quote(params.path)} <<'UCM_EOF'\n{params.content}\nUCM_EOF"
        result = await run(heredoc, host=params.host, timeout=30)
        return "OK - fajl kiirva." if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="edit_file",
        annotations={"title": "Fajl szerkesztes", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def edit_file(params: EditFileInput) -> str:
        """Pontos szoveg-csere egy meglevo fajlban (nem regex).

        Args:
            params (EditFileInput): path, old_string, new_string, count, host.
        Returns:
            str: Hany csere tortent.
        """
        require_mode(Mode.NORMAL, what="edit_file")
        if params.host is None:
            local = _check_root(params.path)
            original = Path(local).read_text(encoding="utf-8")
            if params.old_string not in original:
                return "Nem talalhato a megadott 'old_string' a fajlban - nem tortent valtozas."
            updated = original.replace(params.old_string, params.new_string, params.count)
            Path(local).write_text(updated, encoding="utf-8")
            n = original.count(params.old_string)
            return f"OK - {min(n, params.count)} elofordulas cserelve: {local}"

        # Tavoli: python egysoros a biztonsagos csereert.
        import base64 as _b

        o = _b.b64encode(params.old_string.encode()).decode()
        nw = _b.b64encode(params.new_string.encode()).decode()
        script = (
            f"python3 - <<'UCM_EOF'\n"
            f"import base64,sys,pathlib\n"
            f"p=pathlib.Path({params.path!r})\n"
            f"o=base64.b64decode('{o}').decode(); n=base64.b64decode('{nw}').decode()\n"
            f"t=p.read_text()\n"
            f"if o not in t: print('NOCHANGE'); sys.exit(0)\n"
            f"p.write_text(t.replace(o,n,{params.count})); print('OK')\n"
            f"UCM_EOF"
        )
        result = await run(script, host=params.host, timeout=30)
        return result.stdout.strip() or f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="make_dir",
        annotations={"title": "Konyvtar letrehozas", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def make_dir(params: MakeDirInput) -> str:
        """Konyvtar (es szuloi) letrehozasa.

        Args:
            params (MakeDirInput): path, host.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.NORMAL, what="make_dir")
        if params.host is None:
            local = _check_root(params.path)
            Path(local).mkdir(parents=True, exist_ok=True)
            return f"OK - konyvtar: {local}"
        result = await run(f"mkdir -p {shlex.quote(params.path)}", host=params.host, timeout=15)
        return "OK" if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="move_path",
        annotations={"title": "Athelyezes / atnevezes", "readOnlyHint": False,
                     "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def move_path(params: MovePathInput, ctx: Context) -> str:
        """Fajl / konyvtar atnevezese vagy athelyezese.

        Ha a cel mar letezik, megerositest ker.

        Args:
            params (MovePathInput): source, destination, host.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.NORMAL, what="move_path")
        if await _path_exists(params.host, params.destination):
            await ask_permission(
                ctx,
                action=f"Athelyezes letezo celra: {params.destination}",
                details="A cel mar letezik es felul lesz irva.",
                risk=RiskLevel.MEDIUM,
                host=params.host,
            )
        if params.host is None:
            src = _check_root(params.source)
            dst = _check_root(params.destination)
            os.replace(src, dst) if os.path.isfile(src) else __import__("shutil").move(src, dst)
            return f"OK - {src} -> {dst}"
        result = await run(f"mv {shlex.quote(params.source)} {shlex.quote(params.destination)}",
                           host=params.host, timeout=30)
        return "OK" if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"

    @mcp.tool(
        name="delete_path",
        annotations={"title": "Torles", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def delete_path(params: DeletePathInput, ctx: Context) -> str:
        """Fajl vagy konyvtar torlese. MINDIG megerositest ker.

        Args:
            params (DeletePathInput): path, recursive, host.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.NORMAL, what="delete_path")
        await ask_permission(
            ctx,
            action=f"Torles: {params.path}" + (" (rekurziv)" if params.recursive else ""),
            details="A torles nem vonhato vissza (nincs kuka).",
            risk=RiskLevel.HIGH,
            host=params.host,
        )
        if params.host is None:
            local = _check_root(params.path)
            if os.path.isdir(local):
                if not params.recursive:
                    return "Konyvtar - add meg a recursive=true kapcsolot."
                __import__("shutil").rmtree(local)
            else:
                os.remove(local)
            return f"OK - torolve: {local}"
        flag = "-rf" if params.recursive else "-f"
        result = await run(f"rm {flag} -- {shlex.quote(params.path)}", host=params.host, timeout=30)
        return "OK - torolve." if result.exit_code == 0 else f"Hiba: {result.stderr.strip()}"


async def _path_exists(host: Optional[str], path: str) -> bool:
    if host is None:
        return Path(path).expanduser().exists()
    result = await run(f"test -e {shlex.quote(path)} && echo yes || echo no", host=host, timeout=15)
    return result.stdout.strip() == "yes"
