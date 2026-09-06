"""MCP toolok temak szerint csoportositva.

Minden almodul definial egy `register(mcp)` fuggvenyt, amit a server.py hiv meg.
"""

from . import desktop, files, hosts, packages, system, terminal

REGISTRARS = [
    system.register,
    packages.register,
    terminal.register,
    desktop.register,
    files.register,
    hosts.register,
]

__all__ = ["REGISTRARS"]
