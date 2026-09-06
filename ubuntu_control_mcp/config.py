"""Konfiguracio es engedely-modok.

A konfiguracio harom forrasbol jon, ebben a prioritasi sorrendben (a kesobbi
felulirja a korabbit):

1. Beepitett alapertelmezesek
2. JSON konfiguracios fajl  (alapertelmezett hely: ~/.config/ubuntu-control-mcp/config.json,
   vagy a UBUNTU_CONTROL_CONFIG kornyezeti valtozoval megadva)
3. Kornyezeti valtozok  (UBUNTU_CONTROL_*)

Titkokat (sudo jelszo, SSH jelszo) SOHA nem tarolunk a config fajlban nyersen -
csak a kornyezeti valtozo NEVET, ahonnan futasidoben olvassuk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class Mode(str, Enum):
    """Engedely-mod - meghatarozza, mely toolok hasznalhatok."""

    SAFE = "safe"
    NORMAL = "normal"
    ADMIN = "admin"


# Minel nagyobb a szam, annal tobb joga van a modnak.
_LEVEL_ORDER: dict[Mode, int] = {Mode.SAFE: 0, Mode.NORMAL: 1, Mode.ADMIN: 2}


def level_value(mode: Mode) -> int:
    return _LEVEL_ORDER[mode]


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "ubuntu-control-mcp" / "config.json"
DEFAULT_KEY_DIR = Path.home() / ".config" / "ubuntu-control-mcp" / "keys"


@dataclass
class HostConfig:
    """Egy tavoli SSH gep leirasa."""

    name: str
    hostname: str
    username: str
    port: int = 22
    key_path: Optional[str] = None
    # A jelszot / sudo jelszot csak kornyezeti valtozo neven keresztul kerjuk.
    password_env: Optional[str] = None
    sudo_password_env: Optional[str] = None
    description: str = ""

    def resolved_key_path(self) -> Optional[Path]:
        if not self.key_path:
            return None
        return Path(self.key_path).expanduser()

    def password(self) -> Optional[str]:
        return os.environ.get(self.password_env) if self.password_env else None

    def sudo_password(self) -> Optional[str]:
        if self.sudo_password_env:
            return os.environ.get(self.sudo_password_env)
        return None

    def to_public_dict(self) -> dict[str, Any]:
        """Titkok nelkuli reprezentacio - ez mehet vissza a modellnek."""
        return {
            "name": self.name,
            "hostname": self.hostname,
            "username": self.username,
            "port": self.port,
            "key_path": self.key_path,
            "has_password_env": bool(self.password_env),
            "has_sudo_password_env": bool(self.sudo_password_env),
            "description": self.description,
        }


@dataclass
class Config:
    mode: Mode = Mode.NORMAL
    config_path: Path = DEFAULT_CONFIG_PATH

    # Ha nincs elicit-tamogatas a kliensnel: true eseten a kockazatos muveletek
    # automatikusan engedelyezettek (csak zart, megbizhato kornyezetben ajanlott).
    auto_approve: bool = False

    # Lokalis sudo jelszo kornyezeti valtozo neve.
    sudo_password_env: str = "UBUNTU_CONTROL_SUDO_PASSWORD"

    # Parancs-idokorlatok masodpercben.
    default_timeout: int = 120
    max_timeout: int = 3600

    # Audit napló fajl (opcionalis).
    audit_log_path: Optional[Path] = None

    # Fajl-toolok gyokerkonyvtar-korlatozasa (ures = nincs korlat).
    file_roots: list[str] = field(default_factory=list)

    # Desktop control egyaltalan engedelyezett-e ezen a szerveren.
    desktop_enabled: bool = True

    hosts: dict[str, HostConfig] = field(default_factory=dict)

    # ---- szarmaztatott segedfuggvenyek -------------------------------------

    def local_sudo_password(self) -> Optional[str]:
        return os.environ.get(self.sudo_password_env)

    def get_host(self, name: str) -> HostConfig:
        if name not in self.hosts:
            known = ", ".join(sorted(self.hosts)) or "(egy sincs konfiguralva)"
            raise KeyError(
                f"Ismeretlen host: '{name}'. Elerheto hostok: {known}. "
                f"Adj hozza egyet a 'host_add' toollal vagy a config fajlban."
            )
        return self.hosts[name]

    def clamp_timeout(self, timeout: Optional[int]) -> int:
        if timeout is None:
            return self.default_timeout
        return max(1, min(int(timeout), self.max_timeout))

    # ---- betoltes / mentes ----------------------------------------------------

    @classmethod
    def load(cls) -> "Config":
        path_env = os.environ.get("UBUNTU_CONTROL_CONFIG")
        path = Path(path_env).expanduser() if path_env else DEFAULT_CONFIG_PATH

        raw: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:  # pragma: no cover - IO
                raise RuntimeError(f"Nem sikerult beolvasni a config fajlt ({path}): {exc}") from exc

        cfg = cls(config_path=path)

        if "mode" in raw:
            cfg.mode = Mode(str(raw["mode"]).lower())
        if "auto_approve" in raw:
            cfg.auto_approve = bool(raw["auto_approve"])
        if "sudo_password_env" in raw:
            cfg.sudo_password_env = str(raw["sudo_password_env"])
        if "default_timeout" in raw:
            cfg.default_timeout = int(raw["default_timeout"])
        if "max_timeout" in raw:
            cfg.max_timeout = int(raw["max_timeout"])
        if raw.get("audit_log_path"):
            cfg.audit_log_path = Path(str(raw["audit_log_path"])).expanduser()
        if "file_roots" in raw:
            cfg.file_roots = [str(p) for p in raw["file_roots"]]
        if "desktop_enabled" in raw:
            cfg.desktop_enabled = bool(raw["desktop_enabled"])

        for name, hraw in (raw.get("hosts") or {}).items():
            cfg.hosts[name] = HostConfig(
                name=name,
                hostname=str(hraw["hostname"]),
                username=str(hraw["username"]),
                port=int(hraw.get("port", 22)),
                key_path=hraw.get("key_path"),
                password_env=hraw.get("password_env"),
                sudo_password_env=hraw.get("sudo_password_env"),
                description=str(hraw.get("description", "")),
            )

        # Kornyezeti valtozok felulirjak a fajlt.
        if os.environ.get("UBUNTU_CONTROL_MODE"):
            cfg.mode = Mode(os.environ["UBUNTU_CONTROL_MODE"].lower())
        if os.environ.get("UBUNTU_CONTROL_AUTO_APPROVE"):
            cfg.auto_approve = os.environ["UBUNTU_CONTROL_AUTO_APPROVE"].lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if os.environ.get("UBUNTU_CONTROL_AUDIT_LOG"):
            cfg.audit_log_path = Path(os.environ["UBUNTU_CONTROL_AUDIT_LOG"]).expanduser()
        if os.environ.get("UBUNTU_CONTROL_DESKTOP_ENABLED"):
            cfg.desktop_enabled = os.environ["UBUNTU_CONTROL_DESKTOP_ENABLED"].lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        return cfg

    def save(self) -> None:
        """A jelenlegi allapot visszairasa a config fajlba (titkok nelkul)."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "mode": self.mode.value,
            "auto_approve": self.auto_approve,
            "sudo_password_env": self.sudo_password_env,
            "default_timeout": self.default_timeout,
            "max_timeout": self.max_timeout,
            "desktop_enabled": self.desktop_enabled,
            "file_roots": self.file_roots,
            "hosts": {
                name: {
                    "hostname": h.hostname,
                    "username": h.username,
                    "port": h.port,
                    "key_path": h.key_path,
                    "password_env": h.password_env,
                    "sudo_password_env": h.sudo_password_env,
                    "description": h.description,
                }
                for name, h in self.hosts.items()
            },
        }
        if self.audit_log_path:
            data["audit_log_path"] = str(self.audit_log_path)
        self.config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# Egyetlen, folyamat-szintu peldany.
CONFIG: Config = Config.load()


def reload_config() -> Config:
    global CONFIG
    CONFIG = Config.load()
    return CONFIG
