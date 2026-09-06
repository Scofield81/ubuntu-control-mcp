"""Kozos epitokovek a toolokhoz: alap Pydantic modell, hibakezelo dekorator."""

from __future__ import annotations

import functools
import traceback
from typing import Optional

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field

from ..audit import audit
from ..executors import ExecutionError
from ..formatting import ResponseFormat
from ..permissions import PermissionDenied
from ..terminals import TerminalError

__all__ = ["Context", "ToolInput", "HostAwareInput", "tool_errors"]


class ToolInput(BaseModel):
    """Kozos alap minden tool bemenethez."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


class HostAwareInput(ToolInput):
    host: Optional[str] = Field(
        default=None,
        description=(
            "A cel gep neve a konfiguraciobol. Ures / None = a lokalis gep "
            "(ahol az MCP szerver fut). Tavoli gephez elobb 'host_add' vagy config."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="'markdown' ember-olvashato, 'json' gepi feldolgozashoz.",
    )


def tool_errors(func):
    """A varhato hibakat baratsagos, akcio-orientalt uzenette alakitja."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except PermissionDenied as exc:
            return f"⛔ Engedely megtagadva: {exc}"
        except TerminalError as exc:
            return f"Terminal hiba: {exc}"
        except ExecutionError as exc:
            return f"Vegrehajtasi hiba: {exc}"
        except KeyError as exc:
            return f"Konfiguracios hiba: {exc}"
        except FileNotFoundError as exc:
            return f"Nem talalhato: {exc}"
        except Exception as exc:  # vegso halo - ne szivarogjon ki stack trace a kliensnek
            audit("tool_exception", tool=func.__name__, error=repr(exc),
                  trace=traceback.format_exc()[-2000:])
            return f"Varatlan hiba a(z) '{func.__name__}' toolban: {type(exc).__name__}: {exc}"

    return wrapper
