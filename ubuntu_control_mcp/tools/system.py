"""Rendszer-informacios es -allapot toolok (tobbnyire SAFE modban is elerhetok).

    system_info      - statikus gepinformacio (OS, kernel, CPU modell, RAM, uptime)
    system_status    - pillanatkep: CPU/RAM/disk/load/legfontosabb folyamatok
    cpu_usage        - reszletes CPU hasznalat
    memory_usage     - reszletes memoria / swap hasznalat
    disk_usage       - fajlrendszerek kihasznaltsaga
    network_status   - interfeszek, IP-k, alapertelmezett atjaro, kapcsolat-teszt
    process_list     - futo folyamatok (szures, rendezes, lapozas)
    logs             - journalctl / logfajl olvasas
    service_status   - egy systemd szolgaltatas allapota
    set_mode         - a szerver engedely-modjanak valtoztatasa (ADMIN)
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import Field

from ..config import CONFIG, Mode
from ..executors import run
from ..formatting import ResponseFormat, as_json, command_result_text, human_bytes
from ..permissions import guard_command, require_mode
from .common import HostAwareInput, ToolInput, tool_errors

# psutil csak a lokalis gepen ertelmes; tavolinal parancsokra esunk vissza.
try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


class HostOnlyInput(HostAwareInput):
    pass


class ProcessListInput(HostAwareInput):
    filter: Optional[str] = Field(
        default=None, description="Reszszo-szures a parancssorra / folyamatnevre (pl. 'nginx')."
    )
    sort_by: str = Field(
        default="cpu", description="Rendezes: 'cpu', 'memory' vagy 'pid'."
    )
    limit: int = Field(default=20, ge=1, le=200, description="Max. talalat.")


class LogsInput(HostAwareInput):
    unit: Optional[str] = Field(
        default=None, description="systemd unit neve (pl. 'ssh', 'nginx'). Ures = teljes journal."
    )
    path: Optional[str] = Field(
        default=None, description="Konkret logfajl olvasasa unit helyett (pl. '/var/log/syslog')."
    )
    lines: int = Field(default=100, ge=1, le=5000, description="Utolso N sor.")
    since: Optional[str] = Field(
        default=None, description="Idokorlat journalctl szintaxissal (pl. '1 hour ago', 'today')."
    )
    grep: Optional[str] = Field(default=None, description="Csak az adott mintat tartalmazo sorok.")


class ServiceStatusInput(HostAwareInput):
    name: str = Field(..., min_length=1, description="A systemd szolgaltatas neve.")


class SetModeInput(ToolInput):
    mode: Mode = Field(..., description="Uj mod: 'safe', 'normal' vagy 'admin'.")
    persist: bool = Field(
        default=False, description="Igaz eseten a config fajlba is elmentodik."
    )


async def _cmd(host: Optional[str], command: str) -> str:
    """Rovid, read-only lekerdezes futtatasa es nyers stdout visszaadasa."""
    result = await run(command, host=host, timeout=30)
    return result.stdout.strip() or result.stderr.strip()


def register(mcp) -> None:
    @mcp.tool(
        name="system_info",
        annotations={
            "title": "Rendszer-informacio",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @tool_errors
    async def system_info(params: HostOnlyInput) -> str:
        """Statikus gepinformacio: OS, kernel, hostnev, CPU modell, magok, ossz RAM, uptime.

        Args:
            params (HostOnlyInput): host (opcionalis), response_format.

        Returns:
            str: Markdown vagy JSON a kovetkezo mezokkel: hostname, os, kernel,
            architecture, cpu_model, cpu_cores, total_memory, uptime, boot_time.
        """
        require_mode(Mode.SAFE, what="system_info")
        host = params.host

        if host is None and psutil is not None:
            import platform
            import time as _t

            freq = psutil.cpu_freq()
            data = {
                "hostname": platform.node(),
                "os": _read_os_release(),
                "kernel": platform.release(),
                "architecture": platform.machine(),
                "cpu_model": _local_cpu_model(),
                "cpu_cores_physical": psutil.cpu_count(logical=False),
                "cpu_cores_logical": psutil.cpu_count(logical=True),
                "cpu_freq_mhz": round(freq.current, 0) if freq else None,
                "total_memory": human_bytes(psutil.virtual_memory().total),
                "boot_time": _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(psutil.boot_time())),
                "uptime": _cmd_uptime_local(),
            }
        else:
            raw = await _cmd(
                host,
                "echo HOST=$(hostname); echo KERNEL=$(uname -r); echo ARCH=$(uname -m); "
                "echo OS=\"$(. /etc/os-release 2>/dev/null; echo $PRETTY_NAME)\"; "
                "echo CPU=\"$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)\"; "
                "echo CORES=$(nproc); echo MEM=$(grep MemTotal /proc/meminfo | awk '{print $2}'); "
                "echo UPTIME=\"$(uptime -p)\"",
            )
            data = _parse_kv(raw)
            if "MEM" in data:
                try:
                    data["MEM"] = human_bytes(int(data["MEM"]) * 1024)
                except ValueError:
                    pass

        return render_or_json("Rendszer-informacio", data, params.response_format)

    @mcp.tool(
        name="system_status",
        annotations={
            "title": "Rendszer-allapot pillanatkep",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    @tool_errors
    async def system_status(params: HostOnlyInput) -> str:
        """Gyors egeszseg-pillanatkep: CPU%, RAM, swap, disk (/), load average, top 5 folyamat.

        Args:
            params (HostOnlyInput): host (opcionalis), response_format.

        Returns:
            str: Osszefoglalo a legfontosabb terhelesi mutatokkal.
        """
        require_mode(Mode.SAFE, what="system_status")
        host = params.host

        if host is None and psutil is not None:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            disk = psutil.disk_usage("/")
            load = psutil.getloadavg()
            top = sorted(
                psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                key=lambda p: p.info.get("cpu_percent") or 0,
                reverse=True,
            )[:5]
            data = {
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "load_average": [round(x, 2) for x in load],
                "memory": f"{human_bytes(vm.used)} / {human_bytes(vm.total)} ({vm.percent}%)",
                "swap": f"{human_bytes(sw.used)} / {human_bytes(sw.total)} ({sw.percent}%)",
                "disk_root": f"{human_bytes(disk.used)} / {human_bytes(disk.total)} ({disk.percent}%)",
                "top_processes": [
                    f"{p.info['name']} (CPU {p.info.get('cpu_percent', 0):.0f}%, "
                    f"MEM {p.info.get('memory_percent') or 0:.1f}%)"
                    for p in top
                ],
            }
            return render_or_json("Rendszer-allapot", data, params.response_format)

        result = await run(
            "echo '== load / uptime =='; uptime; echo; echo '== memoria =='; free -h; "
            "echo; echo '== disk =='; df -h /; echo; echo '== top folyamatok =='; "
            "ps -eo pid,pcpu,pmem,comm --sort=-pcpu | head -n 6",
            host=host,
            timeout=30,
        )
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="cpu_usage",
        annotations={"title": "CPU hasznalat", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def cpu_usage(params: HostOnlyInput) -> str:
        """Reszletes CPU hasznalat: osszesitett es magonkenti szazalek, load average.

        Args:
            params (HostOnlyInput): host, response_format.
        Returns:
            str: CPU mutatok.
        """
        require_mode(Mode.SAFE, what="cpu_usage")
        if params.host is None and psutil is not None:
            per_core = psutil.cpu_percent(interval=1.0, percpu=True)
            data = {
                "overall_percent": round(sum(per_core) / len(per_core), 1),
                "per_core_percent": per_core,
                "load_average": [round(x, 2) for x in psutil.getloadavg()],
                "logical_cores": psutil.cpu_count(logical=True),
            }
            return render_or_json("CPU hasznalat", data, params.response_format)
        result = await run("top -bn2 -d 0.5 | grep '%Cpu' | tail -1; echo; cat /proc/loadavg",
                           host=params.host, timeout=20)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="memory_usage",
        annotations={"title": "Memoria hasznalat", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def memory_usage(params: HostOnlyInput) -> str:
        """RAM es swap reszletes hasznalata (total / used / free / available / cache).

        Args:
            params (HostOnlyInput): host, response_format.
        Returns:
            str: Memoria mutatok.
        """
        require_mode(Mode.SAFE, what="memory_usage")
        if params.host is None and psutil is not None:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            data = {
                "total": human_bytes(vm.total),
                "used": human_bytes(vm.used),
                "available": human_bytes(vm.available),
                "free": human_bytes(vm.free),
                "cached": human_bytes(getattr(vm, "cached", 0)),
                "percent": vm.percent,
                "swap_total": human_bytes(sw.total),
                "swap_used": human_bytes(sw.used),
                "swap_percent": sw.percent,
            }
            return render_or_json("Memoria hasznalat", data, params.response_format)
        result = await run("free -h; echo; swapon --show || true", host=params.host, timeout=20)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="disk_usage",
        annotations={"title": "Lemez kihasznaltsag", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def disk_usage(params: HostOnlyInput) -> str:
        """Csatolt fajlrendszerek kihasznaltsaga (df -h ekvivalens).

        Args:
            params (HostOnlyInput): host, response_format.
        Returns:
            str: Fajlrendszerek listaja meret / hasznalt / szabad / szazalek bontasban.
        """
        require_mode(Mode.SAFE, what="disk_usage")
        if params.host is None and psutil is not None:
            rows = []
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except PermissionError:
                    continue
                rows.append(
                    {
                        "device": part.device,
                        "mount": part.mountpoint,
                        "fstype": part.fstype,
                        "total": human_bytes(u.total),
                        "used": human_bytes(u.used),
                        "free": human_bytes(u.free),
                        "percent": u.percent,
                    }
                )
            if params.response_format == ResponseFormat.JSON:
                return as_json(rows)
            lines = ["# Lemez kihasznaltsag", ""]
            for r in rows:
                lines.append(
                    f"- **{r['mount']}** ({r['device']}, {r['fstype']}): "
                    f"{r['used']} / {r['total']} ({r['percent']}%), szabad: {r['free']}"
                )
            return "\n".join(lines)
        result = await run("df -hT -x tmpfs -x devtmpfs", host=params.host, timeout=20)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="network_status",
        annotations={"title": "Halozati allapot", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    @tool_errors
    async def network_status(params: HostOnlyInput) -> str:
        """Halozati interfeszek, IP-cimek, alapertelmezett atjaro, DNS es egy internet-teszt.

        Args:
            params (HostOnlyInput): host, response_format.
        Returns:
            str: Halozati osszefoglalo.
        """
        require_mode(Mode.SAFE, what="network_status")
        result = await run(
            "echo '== interfeszek =='; ip -brief addr; echo; echo '== utvonalak =='; ip route; "
            "echo; echo '== DNS =='; (resolvectl status 2>/dev/null | grep 'DNS Servers' || cat /etc/resolv.conf); "
            "echo; echo '== internet teszt =='; ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && echo 'ICMP OK' || echo 'ICMP FAIL'",
            host=params.host,
            timeout=25,
        )
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="process_list",
        annotations={"title": "Folyamatlista", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def process_list(params: ProcessListInput) -> str:
        """Futo folyamatok listaja szuressel, rendezessel es limittel.

        Args:
            params (ProcessListInput): filter, sort_by ('cpu'|'memory'|'pid'), limit, host,
            response_format.

        Returns:
            str: Folyamatok: pid, felhasznalo, CPU%, MEM%, parancs.
        """
        require_mode(Mode.SAFE, what="process_list")
        sort_flag = {"cpu": "-pcpu", "memory": "-pmem", "pid": "pid"}.get(params.sort_by, "-pcpu")
        grep = f" | grep -i {params.filter!r}" if params.filter else ""
        cmd = (
            f"ps -eo pid,user,pcpu,pmem,etimes,args --sort={sort_flag}{grep} "
            f"| head -n {params.limit + 1}"
        )
        result = await run(cmd, host=params.host, timeout=25)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="logs",
        annotations={"title": "Naplok olvasasa", "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    @tool_errors
    async def logs(params: LogsInput) -> str:
        """Rendszernaplok olvasasa: journalctl (unit szerint) vagy konkret logfajl.

        Args:
            params (LogsInput): unit VAGY path, lines, since, grep, host, response_format.

        Returns:
            str: A kert naplosorok.
        """
        require_mode(Mode.SAFE, what="logs")
        if params.path:
            base = f"tail -n {params.lines} {params.path!r}"
        else:
            parts = ["journalctl", "--no-pager", f"-n {params.lines}"]
            if params.unit:
                parts.append(f"-u {params.unit!r}")
            if params.since:
                parts.append(f"--since {params.since!r}")
            base = " ".join(parts)
        if params.grep:
            base += f" | grep -i --color=never {params.grep!r}"
        result = await run(base, host=params.host, timeout=40)
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="service_status",
        annotations={"title": "Szolgaltatas allapota", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def service_status(params: ServiceStatusInput) -> str:
        """Egy systemd szolgaltatas allapota (aktiv-e, engedelyezett-e, utolso naplosorok).

        Args:
            params (ServiceStatusInput): name, host, response_format.
        Returns:
            str: `systemctl status` kimenet.
        """
        require_mode(Mode.SAFE, what="service_status")
        result = await run(
            f"systemctl status {params.name!r} --no-pager -l | head -n 40; "
            f"echo; systemctl is-enabled {params.name!r} 2>/dev/null || true",
            host=params.host,
            timeout=25,
        )
        return command_result_text(result, fmt=params.response_format)

    @mcp.tool(
        name="set_mode",
        annotations={"title": "Engedely-mod valtasa", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    @tool_errors
    async def set_mode(params: SetModeInput) -> str:
        """A szerver engedely-modjanak valtoztatasa (SAFE / NORMAL / ADMIN).

        A modot LEFELE barmikor lehet allitani. FELFELE allitashoz mar ADMIN
        modban kell lenni - kulonben a UBUNTU_CONTROL_MODE kornyezeti valtozoval
        kell inditani a szervert. Ez szandekos: a modell ne tudja sajat magat
        feljebb emelni.

        Args:
            params (SetModeInput): mode, persist.
        Returns:
            str: Megerosites az uj modrol.
        """
        from ..config import level_value

        current = CONFIG.mode
        if level_value(params.mode) > level_value(current):
            require_mode(Mode.ADMIN, what="set_mode (felfele emeles)")

        CONFIG.mode = params.mode
        if params.persist:
            CONFIG.save()
        from ..audit import audit

        audit("set_mode", old=current.value, new=params.mode.value, persist=params.persist)
        return (
            f"A szerver mostantol '{params.mode.value.upper()}' modban van "
            f"(elozo: '{current.value.upper()}'). "
            + ("Elmentve a config fajlba." if params.persist else "Csak erre a munkamenetre.")
        )


# ---- lokalis segedfuggvenyek ---------------------------------------------


def render_or_json(title: str, data: dict, fmt: ResponseFormat) -> str:
    if fmt == ResponseFormat.JSON:
        return as_json(data)
    lines = [f"# {title}", ""]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"- **{key}**:")
            for item in value:
                lines.append(f"    - {item}")
        else:
            lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def _parse_kv(raw: str) -> dict:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _read_os_release() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "ismeretlen"


def _local_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "ismeretlen"


def _cmd_uptime_local() -> str:
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])
        from ..formatting import human_duration

        return human_duration(seconds)
    except OSError:
        return "ismeretlen"
