# Ubuntu Control MCP

Komplett **Linux desktop agent** MCP szerver. Nem csak „futtasd ezt a bash
parancsot” – hanem „**kezeld az Ubuntu gépet**”: rendszerállapot, csomagkezelés,
persistent terminálok (PTY / SSH), és teljes desktop control (egér, billentyűzet,
ablakok, képernyőkép, OCR) – **beépített, kétrétegű engedély-rendszerrel**.

Lokális gép **és** tetszőleges számú távoli gép (SSH) egyszerre kezelhető.

---

## Miért más, mint egy `run_command(cmd)` tool?

| Sima megközelítés | Ubuntu Control MCP |
|---|---|
| Egyetlen `run_command` | ~50 fókuszált tool természetes munkamegosztással |
| Vak hozzáférés | `SAFE` / `NORMAL` / `ADMIN` mód + kockázat-alapú `ask_permission` |
| `exec()` – nincs állapot | Valódi persistent PTY: `htop`, REPL, `ssh`, debugger, `tail -f` |
| Csak terminál | Egér / billentyűzet / ablakok / képernyőkép / OCR |
| Csak lokális | Lokális + több távoli gép, SSH kulcs-generálással |

---

## Engedély-modell

### 1. réteg – mód-kapu

A szerver **egy** módban fut (`UBUNTU_CONTROL_MODE`, alap: `normal`). Minden tool
deklarálja a szükséges minimális módot; alacsonyabb módban meg sem próbál lefutni.

| Mód | Mit enged |
|------|-----------|
| **SAFE** | fájl- és könyvtárolvasás, `system_info` / `*_usage` / `process_list` / `logs`, `screenshot`, `desktop_ocr`, `apt_search` / `apt_list_upgradable` |
| **NORMAL** | a fentiek **+** `apt_install`, `snap_install`, fájlírás/szerkesztés, `terminal_*`, teljes desktop control, `host_run`, `shell_run` |
| **ADMIN** | a fentiek **+** `sudo`, `apt_upgrade` / `apt_remove` / `snap_remove`, tűzfal, felhasználók, `reboot` / `shutdown`, `set_mode` felfelé |

A mód **lefelé** bármikor állítható (`set_mode`), **felfelé** csak `ADMIN`-ból
vagy induláskor a környezeti változóval – a modell nem tudja saját magát feljebb emelni.

### 2. réteg – kockázat-kapu (`ask_permission`)

A módtól **függetlenül** interaktív megerősítést kér (MCP *elicitation*) minden
magas/közepes kockázatú művelet: `rm -rf`, `mkfs`/`dd`, `reboot`/`shutdown`,
felhasználó-kezelés, tűzfal, `apt purge/remove/upgrade`, `curl … | bash`,
rekurzív `chmod/chown`, fájl-felülírás, törlés, kényszerített ablak-bezárás, stb.

Ha a kliens nem támogatja az elicitet: a művelet **elutasításra kerül**, hacsak a
`UBUNTU_CONTROL_AUTO_APPROVE=1` nincs bekapcsolva (csak zárt, megbízható környezetben).

Minden művelet **auditálva** van (stderr + opcionális naplófájl), a nyilvánvaló
titkok maszkolva.

---

## Telepítés

```bash
git clone git@github.com:Scofield81/ubuntu-control-mcp.git
cd ubuntu-control-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[pty]"          # a [pty] extra a lokális terminálhoz kell (Linux)
```

Ellenőrzés:

```bash
ubuntu-control-mcp --list-tools
```

Ha ez kiírja a tool-listát, kész is vagy — irány a [Futtatás](#futtatás) szakasz.

> **Nincs SSH kulcsod a GitHub-hoz, vagy hibát kapsz a fenti lépések valamelyikén?**
> A **[docs/INSTALL.md](docs/INSTALL.md)** részletesen, lépésről lépésre leírja a
> letöltés minden módját (SSH kulcs beállítása, `gh` CLI, jelszó helyett token) és a
> leggyakoribb hibák elhárítását.

Desktop control a célgépen (opcionális, csak amire szükség van):

```bash
sudo apt install -y xdotool wmctrl scrot tesseract-ocr
# Wayland alatt: grim (képernyőkép). Az xdotool/wmctrl X11-et igényel (XWayland részleges).
```

## Futtatás

```bash
UBUNTU_CONTROL_MODE=normal ubuntu-control-mcp
```

MCP kliens (stdio) konfiguráció, pl. Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ubuntu-control": {
      "command": "/abszolut/ut/.venv/bin/ubuntu-control-mcp",
      "env": {
        "UBUNTU_CONTROL_MODE": "normal",
        "UBUNTU_CONTROL_SUDO_PASSWORD": "…",
        "UBUNTU_CONTROL_AUDIT_LOG": "~/.local/state/ubuntu-control-mcp/audit.log"
      }
    }
  }
}
```

HTTP transzport: `UBUNTU_CONTROL_TRANSPORT=http UBUNTU_CONTROL_PORT=8000 ubuntu-control-mcp`
(a szerver `127.0.0.1`-re köt).

## Konfiguráció

Alap hely: `~/.config/ubuntu-control-mcp/config.json` (vagy `UBUNTU_CONTROL_CONFIG`).
Lásd [`config.example.json`](config.example.json). A környezeti változók felülírják a fájlt:
`UBUNTU_CONTROL_MODE`, `UBUNTU_CONTROL_AUTO_APPROVE`, `UBUNTU_CONTROL_AUDIT_LOG`,
`UBUNTU_CONTROL_DESKTOP_ENABLED`, `UBUNTU_CONTROL_SUDO_PASSWORD`.

> **Titkok**: a config fájl SOHA nem tárol jelszót. A hostoknál csak a környezeti
> változó **nevét** adod meg (`password_env`, `sudo_password_env`).

---

## Távoli gépek

```
host_generate_key   → ed25519 kulcspár, a publikus kulcsot a távoli gépre másolod
host_add            → gép felvétele (name, hostname, username, key_path / *_env)
host_test           → kapcsolat + `id` / `uname -a`
host_list / host_remove
```

Ezután bármelyik tool `host` mezőjébe beírod a gép nevét; üresen hagyva a lokális gépen fut.
Az első csatlakozáskor a host-kulcs *trust-on-first-use* módon elmentődik és auditálódik.

---

## Tool-katalógus (összesen 55 tool)

| Kategória | Tool-ok | Db |
|---|---|--:|
| Rendszer | `system_info` · `system_status` · `cpu_usage` · `memory_usage` · `disk_usage` · `network_status` · `process_list` · `logs` · `service_status` · `set_mode` | 10 |
| Csomagok | `apt_update` · `apt_list_upgradable` · `apt_upgrade` · `apt_install` · `apt_remove` · `apt_search` · `apt_show` · `snap_list` · `snap_install` · `snap_remove` | 10 |
| Terminál | `terminal_open` · `terminal_write` · `terminal_read` · `terminal_wait` · `terminal_ctrl_c` · `terminal_resize` · `terminal_close` · `terminal_list` | 8 |
| Desktop | `screenshot` · `desktop_ocr` · `mouse_move` · `mouse_click` · `mouse_double_click` · `mouse_scroll` · `keyboard_type` · `keyboard_press` · `window_list` · `window_focus` · `window_close` · `launch_application` | 12 |
| Fájlok | `list_files` · `read_file` · `file_info` · `write_file` · `edit_file` · `make_dir` · `move_path` · `delete_path` | 8 |
| Gépek / shell | `host_list` · `host_test` · `host_add` · `host_remove` · `host_generate_key` · `host_run` · `shell_run` | 7 |

Teljes, mindig aktuális lista: `ubuntu-control-mcp --list-tools`

---

## Példák

> **„Frissítsd az Ubuntut.”**
> `apt_update` → `apt_list_upgradable` (megmutatja) → *(megerősítés)* → `apt_upgrade` → hátralévő frissítések ellenőrzése

> **„Indítsd el a dev szervert és hagyd futni.”**
> `terminal_open` → `terminal_write("npm run dev\n")` → `terminal_wait(pattern="localhost:\\d+")`

> **„SSH-zz be a prod gépre és nézd meg az api logját.”**
> `terminal_open(host="prod")` → `terminal_write("journalctl -u api -n 100 -f\n")` → `terminal_read`

> **„Készíts képernyőképet és mondd meg, mi van a képen.”**
> `screenshot` → (a kép visszakerül a válaszban) / `desktop_ocr`

---

## Fejlesztés

```bash
pip install -e ".[dev]"
python -m py_compile $(git ls-files '*.py')
ruff check .
```

Kiértékelés: [`evaluation.xml`](evaluation.xml) – 10 kérdés, ami a szerver
használhatóságát teszteli egy referencia Ubuntu környezeten.

---

## Visszajelzés, hibajelentés, ötletek

Ha hibát találsz, vagy javaslatod van egy új funkcióhoz/tool-hoz, nyiss egy
**[GitHub Issue-t](../../issues)** ebben a repóban. Kód-hozzájárulást (pull request) egyelőre
nem fogadunk — lásd [CONTRIBUTING.md](CONTRIBUTING.md) és a [Licenc](#licenc) szakaszt.

## Licenc

A forráskód nyilvánosan olvasható ezen a repón, de **nem MIT/nyílt forráskódú licenc** alatt áll.
Letölthető, telepíthető és szabadon használható, de **továbbterjesztése, kereskedelmi
forgalomba hozatala és módosított változatának közzététele a szerző előzetes, írásos engedélye
nélkül nem megengedett.** A teljes feltételek: [`LICENSE`](LICENSE).
