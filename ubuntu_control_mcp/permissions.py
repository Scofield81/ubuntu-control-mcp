"""Engedely-szintek kikenyszeritese es interaktiv megerosites (ask_permission).

Ket, egymastol fuggetlen vedelmi reteg:

1. MOD-KAPU (SAFE / NORMAL / ADMIN)
   Minden tool deklaralja, milyen minimalis modot igenyel. Ha a szerver
   alacsonyabb modban fut, a tool egyaltalan nem hajlando lefutni.

2. KOCKAZAT-KAPU (ask_permission)
   Bizonyos muveletek - fuggetlenul a modtol - explicit felhasznaloi
   megerositest kernek a kliensen keresztul (MCP elicitation). Ha a kliens
   nem tamogatja az elicitet es az auto_approve ki van kapcsolva, a muvelet
   elutasitasra kerul.
"""

from __future__ import annotations

import re
import shlex
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .audit import audit
from .config import CONFIG, Mode, level_value


class PermissionDenied(Exception):
    """A muvelet nem engedelyezett (mod-kapu vagy felhasznaloi elutasitas)."""


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Parancs-minta -> (kockazat, emberi magyarazat). Az elso talalat szamit.
_RISK_RULES: list[tuple[re.Pattern[str], RiskLevel, str]] = [
    (re.compile(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b"), RiskLevel.HIGH,
     "rekurziv, kikenyszeritett torles (rm -rf)"),
    (re.compile(r"\b(mkfs|fdisk|parted|sgdisk|wipefs)\b"), RiskLevel.HIGH,
     "particio / fajlrendszer muvelet"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), RiskLevel.HIGH, "dd iras blokkeszkozre"),
    (re.compile(r"\b(shutdown|poweroff|halt|reboot|init\s+[06])\b"), RiskLevel.HIGH,
     "a gep leallitasa / ujrainditasa"),
    (re.compile(r"\b(userdel|groupdel|passwd|usermod|adduser|useradd)\b"), RiskLevel.HIGH,
     "felhasznalo-kezeles"),
    (re.compile(r"\b(ufw|iptables|nft|firewall-cmd)\b"), RiskLevel.HIGH, "tuzfal-modositas"),
    (re.compile(r"\b(chown|chmod)\s+-[a-z]*R"), RiskLevel.HIGH, "rekurziv jogosultsag-valtoztatas"),
    (re.compile(r":\(\)\s*\{.*\};:"), RiskLevel.HIGH, "fork bomb gyanus mintazat"),
    (re.compile(r"\bapt(-get)?\b.*\b(purge|autoremove)\b"), RiskLevel.HIGH,
     "csomag(ok) es konfiguracio teljes eltavolitasa"),
    (re.compile(r">\s*/dev/sd[a-z]"), RiskLevel.HIGH, "iras nyers blokkeszkozre"),
    (re.compile(r"\bcurl\b.*\|\s*(sudo\s+)?(bash|sh)\b"), RiskLevel.HIGH,
     "letoltott szkript azonnali futtatasa"),
    (re.compile(r"\bwget\b.*\|\s*(sudo\s+)?(bash|sh)\b"), RiskLevel.HIGH,
     "letoltott szkript azonnali futtatasa"),

    (re.compile(r"\bapt(-get)?\b.*\b(remove|upgrade|dist-upgrade|full-upgrade)\b"),
     RiskLevel.MEDIUM, "csomagok eltavolitasa / rendszerfrissites"),
    (re.compile(r"\bsnap\s+remove\b"), RiskLevel.MEDIUM, "snap csomag eltavolitasa"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask|restart)\b"), RiskLevel.MEDIUM,
     "rendszerszolgaltatas leallitasa / tiltasa"),
    (re.compile(r"\bkill(all)?\s+-9\b"), RiskLevel.MEDIUM, "folyamat kilovese (SIGKILL)"),
    (re.compile(r"\b(chown|chmod)\b"), RiskLevel.MEDIUM, "jogosultsag / tulajdonos valtoztatas"),
    (re.compile(r"\brm\s+-[a-z]*r\b"), RiskLevel.MEDIUM, "rekurziv torles"),
    (re.compile(r"\bmount\b|\bumount\b"), RiskLevel.MEDIUM, "fajlrendszer csatolas / lecsatolas"),
    (re.compile(r"\bcrontab\b"), RiskLevel.MEDIUM, "utemezett feladat modositasa"),
]


def classify_command(command: str, *, sudo: bool) -> tuple[RiskLevel, str]:
    """Egy shell parancs kockazati besorolasa."""
    text = command.strip()
    for pattern, level, reason in _RISK_RULES:
        if pattern.search(text):
            return level, reason
    if sudo or re.search(r"(^|\s)sudo\s", text):
        return RiskLevel.MEDIUM, "sudo (rendszergazdai) jogosultsag"
    return RiskLevel.LOW, ""


# ---- 1. reteg: mod-kapu ----------------------------------------------------


def require_mode(needed: Mode, *, what: str) -> None:
    """Kivetelt dob, ha a szerver aktualis modja nem eri el a szukseges szintet."""
    if level_value(CONFIG.mode) < level_value(needed):
        audit(
            "permission",
            result="blocked_by_mode",
            needed=needed.value,
            current=CONFIG.mode.value,
            what=what,
        )
        raise PermissionDenied(
            f"'{what}' legalabb '{needed.value.upper()}' modot igenyel, "
            f"de a szerver most '{CONFIG.mode.value.upper()}' modban fut. "
            f"Modvaltas: allitsd a UBUNTU_CONTROL_MODE kornyezeti valtozot, "
            f"vagy hasznald a 'set_mode' toolt (ha engedelyezett)."
        )


# ---- 2. reteg: interaktiv megerosites ------------------------------------


class _Approval(BaseModel):
    """A felhasznalo valasza a megerosito kerdesre."""

    approve: bool = Field(description="Igaz = a muvelet vegrehajthato. Hamis = elutasitva.")
    note: Optional[str] = Field(
        default=None, description="Opcionalis megjegyzes / feltetel a felhasznalotol."
    )


async def ask_permission(
    ctx,
    *,
    action: str,
    details: str,
    risk: RiskLevel,
    host: Optional[str] = None,
) -> None:
    """Interaktiv megerosites kerese a felhasznalotol a kliensen keresztul.

    Sikeres jovahagyas eseten visszater; minden mas esetben PermissionDenied.
    """
    target = f"tavoli gep: {host}" if host else "lokalis gep"

    if CONFIG.auto_approve:
        audit("permission", result="auto_approved", action=action, risk=risk.value, target=target)
        return

    message = (
        f"⚠️  MEGEROSITES SZUKSEGES ({risk.value.upper()} kockazat)\n\n"
        f"Muvelet: {action}\n"
        f"Cel: {target}\n\n"
        f"Reszletek:\n{details}\n\n"
        f"Engedelyezed a vegrehajtast?"
    )

    try:
        result = await ctx.elicit(message=message, schema=_Approval)
    except Exception as exc:  # a kliens nem tamogatja az elicitet
        audit("permission", result="no_elicit", action=action, error=str(exc))
        raise PermissionDenied(
            "A muvelet megerositest igenyel, de a jelenlegi MCP kliens nem tamogatja "
            "az interaktiv kerdest (elicitation). Lehetosegek: (a) futtasd a szervert "
            "olyan klienssel, ami tamogatja; vagy (b) kapcsold be a UBUNTU_CONTROL_AUTO_APPROVE=1 "
            "beallitast megbizhato kornyezetben."
        ) from exc

    action_str = getattr(result, "action", "decline")
    data = getattr(result, "data", None)
    approved = action_str == "accept" and bool(getattr(data, "approve", False))
    note = getattr(data, "note", None) if data else None

    audit(
        "permission",
        result="approved" if approved else "denied",
        action=action,
        risk=risk.value,
        target=target,
        note=note,
    )

    if not approved:
        raise PermissionDenied(
            f"A felhasznalo elutasitotta a muveletet: {action}."
            + (f" Megjegyzes: {note}" if note else "")
        )


async def guard_command(
    ctx,
    command: str,
    *,
    sudo: bool = False,
    host: Optional[str] = None,
    context: str = "shell parancs",
) -> None:
    """A parancs-futtatas kozponti kapuja: kockazat-besorolas + szukseg eseten kerdes.

    A hivo mar atesett a mod-kapun (require_mode). Itt csak a dinamikus,
    tartalom-alapu kockazatot kezeljuk.
    """
    risk, reason = classify_command(command, sudo=sudo)
    audit("command_check", command=command, sudo=sudo, host=host, risk=risk.value, reason=reason)

    if risk == RiskLevel.LOW:
        return

    try:
        preview = " ".join(shlex.split(command))
    except ValueError:
        preview = command

    await ask_permission(
        ctx,
        action=f"{context}: {preview[:400]}",
        details=(reason or "megnovelt kockazatu muvelet") + (f"\nsudo: {'igen' if sudo else 'nem'}"),
        risk=risk,
        host=host,
    )
