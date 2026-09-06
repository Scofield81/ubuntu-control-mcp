"""Csomagkezeles: APT es Snap.

    apt_update       - csomaglistak frissitese (apt update)
    apt_list_upgradable - mi frissitheto
    apt_upgrade      - telepitett csomagok frissitese (ADMIN)
    apt_install      - uj csomag(ok) telepitese (NORMAL)
    apt_remove       - csomag(ok) eltavolitasa (ADMIN)
    apt_search       - csomag kereses
    apt_show         - csomag reszletek
    snap_list        - telepitett snapok
    snap_install     - snap telepitese (NORMAL)
    snap_remove      - snap eltavolitasa (ADMIN)

A "frissitsd az Ubuntut" tipusu keresre az ajanlott folyamat:
    apt_update -> apt_list_upgradable -> (felhasznaloi jovahagyas) -> apt_upgrade
"""

from __future__ import annotations

from pydantic import Field, field_validator

from ..config import Mode
from ..executors import run
from ..formatting import command_result_text
from ..permissions import RiskLevel, ask_permission, guard_command, require_mode
from .common import Context, HostAwareInput, tool_errors

# Csomagnev-ellenorzo: csak biztonsagos karakterek, hogy ne lehessen parancsot injektalni.
_PKG_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.+-_:/")


def _validate_packages(values: list[str]) -> list[str]:
    clean = []
    for value in values:
        value = value.strip()
        if not value or any(ch not in _PKG_OK for ch in value):
            raise ValueError(f"Ervenytelen csomagnev: {value!r}")
        clean.append(value)
    if not clean:
        raise ValueError("Legalabb egy csomagnev szukseges.")
    return clean


class AptSimpleInput(HostAwareInput):
    pass


class AptSearchInput(HostAwareInput):
    query: str = Field(..., min_length=2, max_length=100, description="Kereso kifejezes.")

    @field_validator("query")
    @classmethod
    def _no_shell_meta(cls, v: str) -> str:
        if any(ch in v for ch in ";|&`$<>()\n"):
            raise ValueError("A kereso kifejezes nem tartalmazhat shell meta-karaktereket.")
        return v


class AptPackagesInput(HostAwareInput):
    packages: list[str] = Field(
        ..., min_length=1, max_length=50, description="Csomagnevek listaja."
    )

    @field_validator("packages")
    @classmethod
    def _v(cls, v: list[str]) -> list[str]:
        return _validate_packages(v)


class AptUpgradeInput(HostAwareInput):
    full: bool = Field(
        default=False,
        description="Igaz = 'full-upgrade' (csomagok eltavolitasat is engedi). Hamis = sima upgrade.",
    )


def register(mcp) -> None:
    @mcp.tool(
        name="apt_update",
        annotations={"title": "APT listak frissitese", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    @tool_errors
    async def apt_update(params: AptSimpleInput) -> str:
        """Csomaglistak frissitese ('apt-get update'). Nem telepit es nem frissit semmit.

        Args:
            params (AptSimpleInput): host, response_format.
        Returns:
            str: Az apt update kimenete.
        """
        require_mode(Mode.NORMAL, what="apt_update")
        result = await run("apt-get update", host=params.host, sudo=True, timeout=180)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="apt_list_upgradable",
        annotations={"title": "Frissitheto csomagok", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def apt_list_upgradable(params: AptSimpleInput) -> str:
        """Lista arrol, mely csomagok frissithetok most (nem futtat frissitest).

        Args:
            params (AptSimpleInput): host, response_format.
        Returns:
            str: Frissitheto csomagok (nev, jelenlegi -> uj verzio).
        """
        require_mode(Mode.SAFE, what="apt_list_upgradable")
        result = await run("apt list --upgradable 2>/dev/null", host=params.host, timeout=60)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="apt_upgrade",
        annotations={"title": "APT frissites", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def apt_upgrade(params: AptUpgradeInput, ctx: Context) -> str:
        """A telepitett csomagok frissitese. RENDSZERSZINTU valtoztatas - megerositest ker.

        Ajanlott elotte az 'apt_update' es 'apt_list_upgradable' lefuttatasa,
        hogy lasd, mi fog valtozni.

        Args:
            params (AptUpgradeInput): full (bool), host, response_format.
        Returns:
            str: A frissites kimenete + a vegso allapot ellenorzese.
        """
        require_mode(Mode.ADMIN, what="apt_upgrade")
        sub = "full-upgrade" if params.full else "upgrade"
        cmd = f"DEBIAN_FRONTEND=noninteractive apt-get {sub} -y"
        await guard_command(ctx, cmd, sudo=True, host=params.host, context="APT frissites")
        result = await run(cmd, host=params.host, sudo=True, timeout=1800)
        check = await run("apt list --upgradable 2>/dev/null | wc -l", host=params.host, timeout=30)
        text = command_result_text(result, fmt=params.response_format)
        return text + f"\n\n---\nHatralevo frissitheto csomagok szama: {check.stdout.strip()}"

    @mcp.tool(
        name="apt_install",
        annotations={"title": "APT telepites", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    @tool_errors
    async def apt_install(params: AptPackagesInput, ctx: Context) -> str:
        """Egy vagy tobb csomag telepitese APT-vel.

        Args:
            params (AptPackagesInput): packages (lista), host, response_format.
        Returns:
            str: A telepites kimenete.
        """
        require_mode(Mode.NORMAL, what="apt_install")
        pkgs = " ".join(params.packages)
        cmd = f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}"
        await guard_command(ctx, cmd, sudo=True, host=params.host, context="APT telepites")
        result = await run(cmd, host=params.host, sudo=True, timeout=900)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="apt_remove",
        annotations={"title": "APT eltavolitas", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def apt_remove(params: AptPackagesInput, ctx: Context) -> str:
        """Egy vagy tobb csomag eltavolitasa. RENDSZERSZINTU valtoztatas - megerositest ker.

        Args:
            params (AptPackagesInput): packages (lista), host, response_format.
        Returns:
            str: Az eltavolitas kimenete.
        """
        require_mode(Mode.ADMIN, what="apt_remove")
        pkgs = " ".join(params.packages)
        cmd = f"DEBIAN_FRONTEND=noninteractive apt-get remove -y {pkgs}"
        await ask_permission(
            ctx,
            action=f"APT csomag eltavolitas: {pkgs}",
            details="Az 'apt-get remove' eltavolitja a csomagokat (a konfiguraciot meghagyja).",
            risk=RiskLevel.MEDIUM,
            host=params.host,
        )
        result = await run(cmd, host=params.host, sudo=True, timeout=600)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="apt_search",
        annotations={"title": "APT kereses", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def apt_search(params: AptSearchInput) -> str:
        """Csomag kereses nev / leiras alapjan ('apt-cache search').

        Args:
            params (AptSearchInput): query, host, response_format.
        Returns:
            str: Talalati lista.
        """
        require_mode(Mode.SAFE, what="apt_search")
        result = await run(f"apt-cache search {params.query!r} | head -n 60",
                           host=params.host, timeout=45)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="apt_show",
        annotations={"title": "APT csomag reszletek", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def apt_show(params: AptPackagesInput) -> str:
        """Reszletes informacio egy vagy tobb csomagrol ('apt-cache show').

        Args:
            params (AptPackagesInput): packages, host, response_format.
        Returns:
            str: Csomag metaadatok.
        """
        require_mode(Mode.SAFE, what="apt_show")
        result = await run(f"apt-cache show {' '.join(params.packages)}",
                           host=params.host, timeout=30)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="snap_list",
        annotations={"title": "Snap lista", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def snap_list(params: AptSimpleInput) -> str:
        """Telepitett snap csomagok listaja.

        Args:
            params (AptSimpleInput): host, response_format.
        Returns:
            str: Snap lista.
        """
        require_mode(Mode.SAFE, what="snap_list")
        result = await run("snap list", host=params.host, timeout=30)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="snap_install",
        annotations={"title": "Snap telepites", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    @tool_errors
    async def snap_install(params: AptPackagesInput, ctx: Context) -> str:
        """Snap csomag telepitese.

        Args:
            params (AptPackagesInput): packages, host, response_format.
        Returns:
            str: A telepites kimenete.
        """
        require_mode(Mode.NORMAL, what="snap_install")
        cmd = f"snap install {' '.join(params.packages)}"
        await guard_command(ctx, cmd, sudo=True, host=params.host, context="Snap telepites")
        result = await run(cmd, host=params.host, sudo=True, timeout=600)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="snap_remove",
        annotations={"title": "Snap eltavolitas", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def snap_remove(params: AptPackagesInput, ctx: Context) -> str:
        """Snap csomag eltavolitasa. Megerositest ker.

        Args:
            params (AptPackagesInput): packages, host, response_format.
        Returns:
            str: Az eltavolitas kimenete.
        """
        require_mode(Mode.ADMIN, what="snap_remove")
        pkgs = " ".join(params.packages)
        await ask_permission(
            ctx,
            action=f"Snap eltavolitas: {pkgs}",
            details="A 'snap remove' torli a snapot es az adatait (a snap keszit elotte snapshotot).",
            risk=RiskLevel.MEDIUM,
            host=params.host,
        )
        result = await run(f"snap remove {pkgs}", host=params.host, sudo=True, timeout=300)
        return command_result_text(result, fmt=params.response_format)
