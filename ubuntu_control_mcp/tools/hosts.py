"""Tavoli gepek (SSH) kezelese.

    host_list          - konfiguralt gepek listaja (titkok nelkul)
    host_test          - SSH kapcsolat + alap parancs teszt egy gepen
    host_add           - uj gep hozzaadasa a konfiguraciohoz (ADMIN)
    host_remove        - gep torlese a konfiguraciobol (ADMIN)
    host_generate_key  - uj ed25519 SSH kulcspar generalasa (NORMAL)
    host_run           - egyszeri parancs futtatasa egy gepen (a kockazat-kapun at)

Biztonsag:
- Jelszot SOHA nem tarolunk a config fajlban. A 'host_add' csak a kornyezeti
  valtozo NEVET fogadja el (password_env / sudo_password_env), a tenyleges
  erteket futasidoben abbol olvassuk.
- Uj host felvetele es torlese ADMIN modot igenyel.
- A 'host_run' minden parancsot atenged a permissions.guard_command kapun,
  igy a kockazatos muveletek itt is megerositest kernek.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, field_validator

from ..audit import audit
from ..config import CONFIG, HostConfig, Mode
from ..executors import close_all_ssh, generate_ssh_keypair, run
from ..formatting import as_json, command_result_text
from ..permissions import guard_command, require_mode
from .common import Context, HostAwareInput, ToolInput, tool_errors


class EmptyInput(ToolInput):
    pass


class HostNameInput(ToolInput):
    name: str = Field(..., min_length=1, max_length=64, description="A gep neve a konfiguracioban.")


class HostAddInput(ToolInput):
    name: str = Field(..., min_length=1, max_length=64, description="Rovid azonosito (pl. 'prod').")
    hostname: str = Field(..., min_length=1, description="IP-cim vagy DNS nev.")
    username: str = Field(..., min_length=1, description="SSH felhasznalonev.")
    port: int = Field(default=22, ge=1, le=65535, description="SSH port.")
    key_path: Optional[str] = Field(
        default=None, description="Privat kulcs eleresi ut (pl. a 'host_generate_key' altal adott)."
    )
    password_env: Optional[str] = Field(
        default=None,
        description="Kornyezeti valtozo NEVE, amiben az SSH jelszo van. A jelszot magat NE ird ide.",
    )
    sudo_password_env: Optional[str] = Field(
        default=None, description="Kornyezeti valtozo NEVE a tavoli sudo jelszohoz."
    )
    description: str = Field(default="", max_length=200, description="Szabad szoveges leiras.")

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("A nev csak betut, szamot, '-' es '_' karaktert tartalmazhat.")
        return v

    @field_validator("password_env", "sudo_password_env")
    @classmethod
    def _looks_like_env_name(cls, v: Optional[str]) -> Optional[str]:
        if v and (" " in v or not v.replace("_", "").isalnum()):
            raise ValueError(
                "Ez a mezo egy KORNYEZETI VALTOZO NEVE, nem a jelszo. "
                "Pl.: 'PROD_SSH_PASSWORD'."
            )
        return v


class HostGenKeyInput(ToolInput):
    name: str = Field(..., min_length=1, max_length=64, description="Kulcs neve (pl. a host neve).")
    comment: str = Field(default="ubuntu-control-mcp", description="Kulcs komment.")


class RunInput(HostAwareInput):
    command: str = Field(..., min_length=1, description="A futtatando shell parancs.")
    sudo: bool = Field(default=False, description="sudo-val futtassa-e.")
    timeout: Optional[int] = Field(default=None, ge=1, le=3600, description="Idokorlat mp-ben.")
    cwd: Optional[str] = Field(default=None, description="Munkakonyvtar.")


class HostRunInput(RunInput):
    model_config = {**RunInput.model_config, "validate_default": True}

    @field_validator("host")
    @classmethod
    def _host_required(cls, v: Optional[str]) -> str:
        if not v:
            raise ValueError(
                "A 'host_run' toolhoz kotelezo a 'host'. Lokalis futtatashoz hasznald a 'shell_run'-t."
            )
        return v


def register(mcp) -> None:
    @mcp.tool(
        name="host_list",
        annotations={"title": "Gepek listaja", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def host_list(params: EmptyInput) -> str:
        """A konfiguralt tavoli gepek listaja (titkok nelkul).

        Returns:
            str: Minden gephez: nev, hostname, felhasznalo, port, van-e kulcs / jelszo-env.
        """
        require_mode(Mode.SAFE, what="host_list")
        if not CONFIG.hosts:
            return "Nincs konfiguralt tavoli gep. Adj hozza egyet a 'host_add' toollal."
        return as_json([h.to_public_dict() for h in CONFIG.hosts.values()])

    @mcp.tool(
        name="host_test",
        annotations={"title": "Gep kapcsolat-teszt", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    @tool_errors
    async def host_test(params: HostNameInput) -> str:
        """SSH kapcsolat tesztelese egy geppel: bejelentkezes + 'id' es 'uname -a'.

        Args:
            params (HostNameInput): name.
        Returns:
            str: A kapcsolat eredmenye es az alap parancsok kimenete.
        """
        require_mode(Mode.SAFE, what="host_test")
        CONFIG.get_host(params.name)  # KeyError, ha nincs
        result = await run("id; echo '---'; uname -a; echo '---'; uptime -p",
                           host=params.name, timeout=25)
        return command_result_text(result)

    @mcp.tool(
        name="host_add",
        annotations={"title": "Gep hozzaadasa", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def host_add(params: HostAddInput, ctx: Context) -> str:
        """Uj tavoli gep hozzaadasa a konfiguraciohoz es mentese a config fajlba.

        A jelszot NEM ide adod meg: a 'password_env' / 'sudo_password_env' mezobe
        egy kornyezeti valtozo NEVET irsz, amit a szerver kornyezeteben beallitasz.

        Args:
            params (HostAddInput): name, hostname, username, port, key_path,
            password_env, sudo_password_env, description.

        Returns:
            str: Megerosites + a mentett (titok nelkuli) rekord.
        """
        require_mode(Mode.ADMIN, what="host_add")
        from ..permissions import RiskLevel, ask_permission

        await ask_permission(
            ctx,
            action=f"Uj SSH gep felvetele: {params.username}@{params.hostname}:{params.port} ('{params.name}')",
            details="A gep bekerul a config fajlba, es a kesobbi toolok elerik SSH-n.",
            risk=RiskLevel.MEDIUM,
        )

        CONFIG.hosts[params.name] = HostConfig(
            name=params.name,
            hostname=params.hostname,
            username=params.username,
            port=params.port,
            key_path=params.key_path,
            password_env=params.password_env,
            sudo_password_env=params.sudo_password_env,
            description=params.description,
        )
        CONFIG.save()
        audit("host_add", name=params.name, hostname=params.hostname, username=params.username)
        return "Gep hozzaadva es elmentve:\n" + as_json(CONFIG.hosts[params.name].to_public_dict())

    @mcp.tool(
        name="host_remove",
        annotations={"title": "Gep torlese", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def host_remove(params: HostNameInput, ctx: Context) -> str:
        """Egy tavoli gep torlese a konfiguraciobol.

        Args:
            params (HostNameInput): name.
        Returns:
            str: Megerosites.
        """
        require_mode(Mode.ADMIN, what="host_remove")
        from ..permissions import RiskLevel, ask_permission

        CONFIG.get_host(params.name)
        await ask_permission(
            ctx,
            action=f"SSH gep torlese a konfiguraciobol: {params.name}",
            details="A kapcsolodasi adatok torlodnek a config fajlbol (a tavoli gep nem valtozik).",
            risk=RiskLevel.MEDIUM,
        )
        CONFIG.hosts.pop(params.name, None)
        CONFIG.save()
        await close_all_ssh()
        audit("host_remove", name=params.name)
        return f"'{params.name}' torolve a konfiguraciobol."

    @mcp.tool(
        name="host_generate_key",
        annotations={"title": "SSH kulcs generalas", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def host_generate_key(params: HostGenKeyInput) -> str:
        """Uj ed25519 SSH kulcspar generalasa a szerver kulcs-konyvtaraba.

        A privat kulcs helyben marad; a publikus kulcsot kell felmasolni a
        tavoli gep ~/.ssh/authorized_keys fajljaba (pl. 'ssh-copy-id' vagy kezzel).
        Ezutan a 'host_add' hivasnal add meg a privat kulcs utjat a 'key_path'-ban.

        Args:
            params (HostGenKeyInput): name, comment.
        Returns:
            str: A privat kulcs utja + a publikus kulcs sora (ezt kell telepiteni).
        """
        require_mode(Mode.NORMAL, what="host_generate_key")
        info = generate_ssh_keypair(params.name, params.comment)
        return (
            "Uj SSH kulcspar letrejott.\n\n"
            f"Privat kulcs (helyben, ezt add meg a host_add 'key_path' mezoben):\n  {info['private_key_path']}\n\n"
            f"Publikus kulcs (ezt masold a tavoli gep ~/.ssh/authorized_keys fajljaba):\n  {info['public_key']}\n\n"
            "Tipp: ssh-copy-id -i <privat_kulcs>.pub felhasznalo@gep"
        )

    @mcp.tool(
        name="host_run",
        annotations={"title": "Parancs tavoli gepen", "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def host_run(params: HostRunInput, ctx: Context) -> str:
        """Egyszeri shell parancs futtatasa egy KONFIGURALT tavoli gepen.

        A parancs atmegy a kockazat-kapun: a magas kockazatu muveletek (rm -rf,
        reboot, tuzfal, stb.) megerositest kernek.

        Args:
            params (HostRunInput): host (kotelezo), command, sudo, timeout, cwd, response_format.
        Returns:
            str: A parancs kimenete.
        """
        require_mode(Mode.NORMAL, what="host_run")
        await guard_command(ctx, params.command, sudo=params.sudo, host=params.host,
                            context="tavoli parancs")
        result = await run(params.command, host=params.host, sudo=params.sudo,
                           timeout=params.timeout, cwd=params.cwd)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="shell_run",
        annotations={"title": "Parancs a lokalis gepen", "readOnlyHint": False,
                     "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def shell_run(params: RunInput, ctx: Context) -> str:
        """Egyszeri shell parancs futtatasa a LOKALIS gepen (ahol az MCP szerver fut).

        Ugyanaz a kockazat-kapu vonatkozik ra, mint a 'host_run'-ra. Altalaban
        celszerubb a specifikus toolokat hasznalni (system_*, apt_*, stb.), de
        ez a menekulesi ut barmilyen egyedi parancshoz.

        Args:
            params (HostRunInput): command, sudo, timeout, cwd, response_format.
            (A 'host' mezot hagyd uresen - ha kitoltod, tavoli gepre megy.)
        Returns:
            str: A parancs kimenete.
        """
        require_mode(Mode.NORMAL, what="shell_run")
        host = params.host or None
        await guard_command(ctx, params.command, sudo=params.sudo, host=host,
                            context="lokalis parancs")
        result = await run(params.command, host=host, sudo=params.sudo,
                           timeout=params.timeout, cwd=params.cwd)
        return command_result_text(result, fmt=params.response_format)
