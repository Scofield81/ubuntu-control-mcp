# Részletes telepítési útmutató

A fő [README](../README.md) a legrövidebb, "van már SSH kulcsom" útvonalat írja le.
Ez az oldal azoknak szól, akiknek ez nem működött, vagy nem tudják, mi az az SSH kulcs —
lépésről lépésre, azt is elmagyarázva, mit kell hova beírni.

## 1. A gép előkészítése

```bash
python3 --version
git --version
```

Ha bármelyik hiányzik, vagy a `python3 -m venv` "ensurepip is not available" hibát dob:

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
```

## 2. A kód letöltése (klónozás)

A repó **privát** — csak azok tudják letölteni, akiknek a GitHub-fiókod (`Scofield81`)
hozzáférést adott. Ezért a letöltéshez be kell azonosítanod magad a GitHub felé. Három
mód közül választhatsz — mindegyik ugyanoda vezet, csak a bejelentkezés módja más.

### A) SSH kulcs (ajánlott, ha rendszeresen dolgozol GitHub-bal)

Az SSH kulcs egy jelszó nélküli, gépenkénti azonosító. Ha még nincs:

```bash
ssh-keygen -t ed25519 -C "a-sajat-emailcimed@pelda.hu"
# Enterrel elfogadhatod az alapértelmezett fájlnevet és az üres jelszót is
cat ~/.ssh/id_ed25519.pub
```

A kiírt sort (egy `ssh-ed25519 AAAA...` kezdetű szöveg) másold be a GitHub-on ide:
**github.com → jobb felül a profilképed → Settings → SSH and GPG keys → New SSH key**
(illeszd be a "Key" mezőbe, adj neki egy nevet, pl. "Munkagép", majd mentsd).

Ezután:

```bash
git clone git@github.com:Scofield81/ubuntu-control-mcp.git
cd ubuntu-control-mcp
```

### B) GitHub CLI (`gh`) — ha nem akarsz SSH kulcsot bajlódni

A `gh` egy hivatalos GitHub parancssori eszköz, ami böngészőn keresztül jelentkeztet be
(nincs kézzel bemásolt kulcs/jelszó).

```bash
sudo apt install gh          # ha még nincs telepítve
gh auth login                # kövesd a kiírt lépéseket (böngésző megnyílik, kód beírása)
gh repo clone Scofield81/ubuntu-control-mcp
cd ubuntu-control-mcp
```

### C) Personal Access Token — ha se SSH kulcsot, se `gh`-t nem akarsz használni

Ez olyan, mint egy jelszó, amit a git parancsnak adsz meg a saját jelszavad helyett
(a GitHub 2021 óta nem fogadja el a sima jelszót git műveletekhez).

1. Nyisd meg: **github.com → profilkép → Settings → Developer settings → Personal
   access tokens → Tokens (classic) → Generate new token (classic)**.
2. Adj neki egy nevet (pl. "ubuntu-control-mcp letöltés"), jelöld be a **`repo`**
   jelölőnégyzetet, majd generáld le. A megjelenő hosszú karaktersorozatot (`ghp_...`
   kezdetű) **másold ki most** — a GitHub csak egyszer mutatja meg.
3. Klónozz vele:

   ```bash
   git clone https://github.com/Scofield81/ubuntu-control-mcp.git
   cd ubuntu-control-mcp
   ```

   Amikor a git felhasználónevet és jelszót kér: a felhasználónévhez írd be a GitHub
   felhasználóneved (`Scofield81`), **jelszóként pedig a kimásolt tokent** (nem a
   valódi GitHub-jelszavadat) — a git ezt elfogadja, és megjegyzi a következő
   alkalomra is.

## 3. Python-környezet és a program telepítése

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[pty]"
```

Ellenőrzés — ha ez kiírja a tool-listát, minden rendben:

```bash
ubuntu-control-mcp --list-tools
```

## Hibaelhárítás

| Hiba | Megoldás |
|---|---|
| `git@github.com: Permission denied (publickey)` | A nyilvános SSH kulcsod nincs feltöltve a GitHub-fiókodhoz, vagy más fiókhoz tartozik — ismételd meg az **A)** lépést. |
| `remote: Repository not found.` | Vagy elgépelted a repó nevét, vagy a fiókod nem kapott hozzáférést hozzá — ellenőrizd a GitHub-on, hogy be vagy-e jelentkezve a megfelelő fiókkal, és látod-e a repót. |
| `python3 -m venv` — `ensurepip is not available` | Hiányzik a `python3-venv` csomag — lásd az 1. lépést. |
| `pip install` közben `error: externally-managed-environment` | Elfelejtetted aktiválni a virtuális környezetet (`source .venv/bin/activate`) — futtasd újra, majd próbáld a `pip install`-t. |
| `Support for password authentication was removed` | A valódi GitHub-jelszavaddal próbálkoztál a **C)** módnál — jelszó helyett a generált tokent (`ghp_...`) add meg. |
