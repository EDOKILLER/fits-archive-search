#!/usr/bin/env bash
# =============================================================================
# Crea l'eseguibile "RicercaArchiviFITS" per Ubuntu (da fare UNA sola volta).
# L'eseguibile finale funziona senza Python, senza JupyterLab e senza pacchetti.
#
# Uso: metti questo file nella stessa cartella di ricerca_archivi.py, poi nel terminale:
#     cd <cartella con i due file>
#     bash crea_eseguibile_ubuntu.sh
# =============================================================================
set -e
cd "$(dirname "$(readlink -f "$0")")"

for f in ricerca_archivi.py openngc_compatto.csv ricerca_archivi.png; do
    [ -f "$f" ] || { echo "ERRORE: manca $f in questa cartella ($(pwd))."; exit 1; }
done

echo "=== 1/4  Strumenti di sistema (chiede la password) ==="
sudo apt update
sudo apt install -y python3-venv python3-tk

echo "=== 2/4  Ambiente Python temporaneo per la compilazione ==="
# l'ambiente viene riusato se esiste già (ricompilazioni molto più rapide)
[ -d .venv-build ] || python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install numpy astropy astroquery pyvo requests keyring pyinstaller

echo "=== 3/4  Creazione dell'eseguibile (qualche minuto) ==="
pyinstaller --noconfirm --clean --onefile --windowed \
    --name RicercaArchiviFITS \
    --add-data "openngc_compatto.csv:." \
    --add-data "ricerca_archivi.png:." \
    --collect-all astropy \
    --collect-all astroquery \
    --collect-all pyvo \
    --collect-all erfa \
    --collect-all keyring \
    --collect-data astropy_iers_data \
    --collect-submodules html5lib \
    --collect-submodules bs4 \
    --copy-metadata numpy \
    --copy-metadata astropy \
    --copy-metadata astroquery \
    --copy-metadata pyvo \
    --copy-metadata requests \
    --copy-metadata keyring \
    --copy-metadata pyerfa \
    --copy-metadata astropy-iers-data \
    --copy-metadata packaging \
    --copy-metadata pyyaml \
    ricerca_archivi.py
deactivate

echo "=== Verifica dell'eseguibile (autotest) ==="
if ! ./dist/RicercaArchiviFITS --autotest > autotest.txt 2>&1; then
    cat autotest.txt
    echo
    echo "L'autotest è FALLITO: i dettagli sono in $(pwd)/autotest.txt"
    echo "L'eseguibile non è stato installato. Invia autotest.txt per la correzione."
    rm -rf build RicercaArchiviFITS.spec
    exit 1
fi
cat autotest.txt

echo "=== Prova degli archivi online (diagnostica, serve Internet) ==="
./dist/RicercaArchiviFITS --diagnostica > diagnostica.txt 2>&1 || true
grep -E "^\[(ok|ERRORE|parziale|--)\]" diagnostica.txt || true
echo "(dettagli in $(pwd)/diagnostica.txt)"

echo "=== 4/4  Installazione nella home e icona nel menu ==="
DEST="$HOME/.local/bin"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/512x512/apps"
cp ricerca_archivi.png "$HOME/.local/share/icons/hicolor/512x512/apps/ricerca-archivi-fits.png"
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cp dist/RicercaArchiviFITS "$DEST/RicercaArchiviFITS"
chmod +x "$DEST/RicercaArchiviFITS"

# Tk dichiara la classe della finestra come "Ricercaarchivifits" (solo la prima maiuscola): StartupWMClass
# deve essere scritto così, altrimenti la barra di Ubuntu mostra un ingranaggio (visto con
# xwininfo -root -tree il 9/11). Tolgo il collegamento col nome vecchio per non avere doppioni.
SCRIVANIA="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Scrivania")"
rm -f "$HOME/.local/share/applications/ricerca-archivi-fits.desktop" "$SCRIVANIA/ricerca-archivi-fits.desktop"

cat > "$HOME/.local/share/applications/RicercaArchiviFITS.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ricerca archivi FITS
Comment=Cerca e scarica immagini FITS dagli archivi dei telescopi
Exec=$DEST/RicercaArchiviFITS
Icon=$HOME/.local/share/icons/hicolor/512x512/apps/ricerca-archivi-fits.png
StartupWMClass=Ricercaarchivifits
Terminal=false
Categories=Science;Astronomy;Education;
EOF
chmod +x "$HOME/.local/share/applications/RicercaArchiviFITS.desktop"
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true

# Copia anche sulla Scrivania (cartella Desktop o Scrivania, a seconda della lingua)
if [ -d "$SCRIVANIA" ]; then
    cp "$HOME/.local/share/applications/RicercaArchiviFITS.desktop" "$SCRIVANIA/"
    chmod +x "$SCRIVANIA/RicercaArchiviFITS.desktop"
    gio set "$SCRIVANIA/RicercaArchiviFITS.desktop" metadata::trusted true 2>/dev/null || true
fi

# Atlante Celeste, Atomo Celeste e Via Lattea 3D aprono Ricerca archivi dal percorso salvato nelle loro
# impostazioni: se punta a una versione vecchia, lo porto a quella appena installata.
python3 - "$DEST/RicercaArchiviFITS" <<'PYEOF' || true
import json, sys
from pathlib import Path
nuovo = sys.argv[1]
for f in sorted(Path.home().glob(".*.json")):
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    v = d.get("programma_ricerca")
    if v and v != nuovo and Path(v).name.lower().startswith(("ricercaarchivifits", "ricerca_archivi")):
        d["programma_ricerca"] = nuovo
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {f.name}: Ricerca archivi ora apre {nuovo} (prima: {v})")
PYEOF

# Pulizia dei file della compilazione (l'eseguibile resta in dist/ e in ~/.local/bin)
rm -rf .venv-build build RicercaArchiviFITS.spec

echo
echo "FATTO."
echo "  Eseguibile:  $DEST/RicercaArchiviFITS   (copia anche in $(pwd)/dist/)"
echo "  Avvio:       cerca 'Ricerca archivi FITS' nel menu delle applicazioni,"
echo "               oppure doppio clic sull'icona sulla Scrivania"
echo "               (la prima volta: tasto destro -> 'Consenti avvio')."
echo "  Il titolo della finestra mostra la versione: deve dire 'Ricerca archivi FITS $(grep -m1 '^VERSIONE' ricerca_archivi.py | cut -d'"' -f2)'."
