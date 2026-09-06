"""Egysegesitett parancs-futtatas: lokalis shell + tavoli SSH.

A tobbi modul ezt hasznalja - nem kell tudniuk, hogy lokalis vagy tavoli
gepen futnak. Minden tool, ami parancsot futtat, a `run()` fuggvenyt hivja.

SSH:
- paramiko-t hasznalunk (szinkron), thread-poolban futtatva, hogy ne blokkolja
  az asyncio esemenyhurkot.
- Kapcsolat-pool hostnevenkent (`_SSH_POOL`).
- Kulcs-alapu es jelszavas hitelesites is tamogatott; a host-kulcs elso
  csatlakozaskor bekerul a known_hosts-ba (paramiko AutoAddPolicy helyett
  sajat, naplozo policy).
"""

from __future__ import annotations

import asyncio
import io
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paramiko

from .audit import audit
from .config import CONFIG, HostConfig


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    host: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class ExecutionError(Exception):
    pass


def _wrap_sudo(command: str, sudo: bool) -> tuple[str, bool]:
    """sudo eseten -S -p '' elotag, hogy a jelszot stdin-rol olvassa."""
    if not sudo:
        return command, False
    return f"sudo -S -p '' bash -c {shlex.quote(command)}", True


# --------------------------------------------------------------------------
# Lokalis futtatas
# --------------------------------------------------------------------------


async def _run_local(
    command: str,
    *,
    sudo: bool,
    timeout: int,
    cwd: Optional[str],
    env: Optional[dict[str, str]],
    stdin_data: Optional[str],
) -> CommandResult:
    wrapped, needs_pw = _wrap_sudo(command, sudo)

    if needs_pw:
        password = CONFIG.local_sudo_password()
        if password is None:
            raise ExecutionError(
                "sudo jelszo szukseges, de nincs beallitva. Allitsd be a "
                f"{CONFIG.sudo_password_env} kornyezeti valtozot, vagy konfiguralj "
                "jelszo nelkuli sudo-t (NOPASSWD) a celgepen."
            )
        stdin_data = password + "\n" + (stdin_data or "")

    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-lc",
        wrapped,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=stdin_data.encode() if stdin_data else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ExecutionError(
            f"A parancs tullepte a {timeout} mp-es idokorlatot es le lett allitva."
        ) from exc

    return CommandResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode(errors="replace"),
        stderr=err.decode(errors="replace"),
        duration=time.monotonic() - start,
        host=None,
    )


# --------------------------------------------------------------------------
# Tavoli (SSH) futtatas
# --------------------------------------------------------------------------

_SSH_POOL: dict[str, paramiko.SSHClient] = {}
_SSH_LOCK = asyncio.Lock()


class _LoggingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Elso csatlakozaskor elmentjuk a host kulcsot, de naplozzuk is."""

    def missing_host_key(self, client, hostname, key):  # noqa: D401
        audit(
            "ssh_hostkey",
            hostname=hostname,
            key_type=key.get_name(),
            fingerprint=key.get_fingerprint().hex(),
            action="trust_on_first_use",
        )
        client.get_host_keys().add(hostname, key.get_name(), key)


def _connect_ssh(host: HostConfig) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if known_hosts.is_file():
        try:
            client.load_host_keys(str(known_hosts))
        except OSError:
            pass
    client.set_missing_host_key_policy(_LoggingHostKeyPolicy())

    kwargs: dict = {
        "hostname": host.hostname,
        "port": host.port,
        "username": host.username,
        "timeout": 20,
        "allow_agent": True,
        "look_for_keys": host.key_path is None,
    }
    key_path = host.resolved_key_path()
    if key_path:
        if not key_path.is_file():
            raise ExecutionError(f"SSH kulcs nem talalhato: {key_path}")
        kwargs["key_filename"] = str(key_path)
    password = host.password()
    if password:
        kwargs["password"] = password

    try:
        client.connect(**kwargs)
    except paramiko.AuthenticationException as exc:
        raise ExecutionError(
            f"SSH hitelesites sikertelen ({host.username}@{host.hostname}). "
            "Ellenorizd a kulcsot / jelszot."
        ) from exc
    except (paramiko.SSHException, OSError) as exc:
        raise ExecutionError(f"SSH kapcsolat sikertelen: {exc}") from exc

    audit("ssh_connect", host=host.name, hostname=host.hostname, username=host.username)
    return client


async def get_ssh_client(host: HostConfig) -> paramiko.SSHClient:
    async with _SSH_LOCK:
        client = _SSH_POOL.get(host.name)
        if client is not None:
            transport = client.get_transport()
            if transport is not None and transport.is_active():
                return client
            _SSH_POOL.pop(host.name, None)
        client = await asyncio.to_thread(_connect_ssh, host)
        _SSH_POOL[host.name] = client
        return client


def _run_ssh_blocking(
    client: paramiko.SSHClient,
    command: str,
    *,
    sudo: bool,
    sudo_password: Optional[str],
    timeout: int,
    cwd: Optional[str],
    stdin_data: Optional[str],
) -> CommandResult:
    if cwd:
        command = f"cd {shlex.quote(cwd)} && {command}"
    wrapped, needs_pw = _wrap_sudo(command, sudo)
    if needs_pw:
        if not sudo_password:
            raise ExecutionError(
                "Tavoli sudo jelszo szukseges, de nincs beallitva a hosthoz "
                "(sudo_password_env). Alternativa: NOPASSWD sudo a celgepen."
            )
        stdin_data = sudo_password + "\n" + (stdin_data or "")

    start = time.monotonic()
    stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout, get_pty=False)
    if stdin_data:
        try:
            stdin.write(stdin_data)
            stdin.flush()
            stdin.channel.shutdown_write()
        except OSError:
            pass
    try:
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
    except Exception as exc:  # timeout is ide esik paramikonal
        raise ExecutionError(f"Tavoli parancs hiba / idotullepes: {exc}") from exc

    return CommandResult(
        exit_code=code,
        stdout=out,
        stderr=err,
        duration=time.monotonic() - start,
    )


async def _run_remote(
    host_name: str,
    command: str,
    *,
    sudo: bool,
    timeout: int,
    cwd: Optional[str],
    stdin_data: Optional[str],
) -> CommandResult:
    host = CONFIG.get_host(host_name)
    client = await get_ssh_client(host)
    result = await asyncio.to_thread(
        _run_ssh_blocking,
        client,
        command,
        sudo=sudo,
        sudo_password=host.sudo_password() or host.password(),
        timeout=timeout,
        cwd=cwd,
        stdin_data=stdin_data,
    )
    result.host = host_name
    return result


# --------------------------------------------------------------------------
# Publikus belepesi pont
# --------------------------------------------------------------------------


async def run(
    command: str,
    *,
    host: Optional[str] = None,
    sudo: bool = False,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    stdin_data: Optional[str] = None,
) -> CommandResult:
    """Parancs futtatasa lokalisan (host=None) vagy tavoli gepen (host=nev).

    A hivo felelossege, hogy elotte atfusson a mod-kapun (permissions.require_mode)
    es a kockazat-kapun (permissions.guard_command).
    """
    effective_timeout = CONFIG.clamp_timeout(timeout)
    audit("command", command=command, host=host or "local", sudo=sudo, timeout=effective_timeout)

    if host:
        result = await _run_remote(
            host,
            command,
            sudo=sudo,
            timeout=effective_timeout,
            cwd=cwd,
            stdin_data=stdin_data,
        )
    else:
        result = await _run_local(
            command,
            sudo=sudo,
            timeout=effective_timeout,
            cwd=cwd,
            env=env,
            stdin_data=stdin_data,
        )

    audit(
        "command_done",
        command=command,
        host=host or "local",
        exit_code=result.exit_code,
        duration=round(result.duration, 3),
    )
    return result


async def close_all_ssh() -> None:
    async with _SSH_LOCK:
        for name, client in list(_SSH_POOL.items()):
            try:
                client.close()
            except Exception:
                pass
            _SSH_POOL.pop(name, None)


def generate_ssh_keypair(name: str, comment: str = "ubuntu-control-mcp") -> dict[str, str]:
    """Uj ed25519 kulcspar generalasa a config kulcs-konyvtaraba.

    Visszaadja a privat kulcs eleresi utjat es a publikus kulcs szoveget.
    """
    from .config import DEFAULT_KEY_DIR

    DEFAULT_KEY_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = DEFAULT_KEY_DIR / f"{name}_ed25519"
    if priv_path.exists():
        raise ExecutionError(f"Mar letezik kulcs ezen a neven: {priv_path}")

    key = paramiko.Ed25519Key.generate()
    buf = io.StringIO()
    key.write_private_key(buf)
    priv_path.write_text(buf.getvalue(), encoding="utf-8")
    priv_path.chmod(0o600)

    pub_line = f"ssh-ed25519 {key.get_base64()} {comment}"
    (DEFAULT_KEY_DIR / f"{name}_ed25519.pub").write_text(pub_line + "\n", encoding="utf-8")

    audit("ssh_keygen", name=name, path=str(priv_path))
    return {"private_key_path": str(priv_path), "public_key": pub_line}
