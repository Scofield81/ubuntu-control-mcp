"""Ubuntu Control MCP - komplett Linux desktop agent MCP szerver.

A csomag egy MCP (Model Context Protocol) szervert biztosit, amivel egy LLM
ugynok teljes korwhen kezelhet egy Ubuntu / Debian alapu gepet - lokalisan vagy
tavolrol SSH-n keresztul.

Fo modulok:
    config      - konfiguracio betoltese, engedely-modok (SAFE / NORMAL / ADMIN)
    audit       - minden muvelet naplozasa (stderr + opcionalis fajl)
    permissions - engedely-szintek kikenyszeritese + interaktiv ask_permission
    executors   - egysegesitett parancs-futtatas (lokalis + SSH)
    terminals   - persistent PTY / SSH shell kezeles
    tools/*     - a tenyleges MCP toolok temak szerint
"""

__version__ = "0.1.0"
