#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RICERCA ARCHIVI FITS - cerca e scarica immagini di telescopi
============================================================
Programma con finestra (Ubuntu e Windows 11). Dato il nome di un oggetto
(es. "NGC 346", "IC 1234") o le sue coordinate (anche copiate da Wikipedia):
  1) interroga diversi archivi pubblici:
       - MAST (STScI): JWST, HST, GALEX, Swift UVOT, Pan-STARRS, Spitzer
       - IRSA (Caltech): Euclid Q1 e, a scelta, Spitzer / WISE / 2MASS ...
       - Chandra Data Archive (CXC)
       - ESO (telescopi a terra: VLT, VISTA, VST, ...)
  2) mostra una tabella: oggetto, telescopio, tipo, strumento, filtro,
     dimensione, se l'immagine contiene davvero l'oggetto (confrontando le
     coordinate con l'impronta dell'immagine) e la distanza dal centro;
  3) scarichi i file spuntati: vengono salvati in
         <cartella dati>/<OGGETTO>/<TELESCOPIO>/   (dalla 1.5: una cartella per
     oggetto, dentro una sottocartella per telescopio: JWST, HST, CHANDRA...)
     (le sottocartelle vengono create se mancano) e, dopo il download,
     il programma verifica con il WCS del file che l'oggetto sia nel campo;
  4) «Mosaici consigliati» (dalla 1.4): mosaici HLSP di livello 4 di Hubble e
     JWST con tre filtri pronti per un'immagine a colori con AutoAstroPipe;
  5) solo se lo chiedi (pulsanti / opzione), salva:
         la tabella completa in CSV, nel percorso che scegli;
         gli script scarica_<data>.sh (Ubuntu) e .bat (Windows) in
         <cartella dati>/_ricerche_archivi/<OGGETTO>/, rieseguibili:
         riprendono i file interrotti e saltano quelli già completi.

Opzioni da riga di comando:
  --autotest     prove senza rete (parser, impronte, script), poi esce
  --diagnostica  interroga ogni archivio per NGC 346 e prova le ricette dei mosaici
  --diagnostica-mosaici   prova solo le ricette dei mosaici consigliati
"""

import csv
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import numpy as np

# =============================================================================
# CONFIGURAZIONE
# =============================================================================
NOME_PROGRAMMA = "Ricerca archivi FITS"
VERSIONE = "1.11"
# lista condivisa con Atlante Celeste: oggetti da cercare con calma
FILE_LISTA_DA_CERCARE = Path.home() / ".lista_da_cercare_fits.json"
FILE_CONFIG = Path.home() / ".ricerca_archivi_fits.json"
CARTELLA_RICERCHE = "_ricerche_archivi"

RAGGIO_PREDEFINITO_ARCMIN = 1.0      # raggio del cono di ricerca
MAX_RISULTATI_ARCHIVIO = 2000        # righe massime chieste a ogni archivio
TIMEOUT_RETE = 120                   # secondi
LIMITE_ARCHIVIO = 180                # secondi massimi di attesa per ogni archivio (dalla 1.10)

ZOOM_PREDEFINITO = 1.0 if sys.platform == "win32" else 1.4
ZOOM_MIN, ZOOM_MAX, ZOOM_PASSO = 0.8, 2.6, 0.1

# Servizi
URL_MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
URL_IRSA_SIA = "https://irsa.ipac.caltech.edu/SIA"
URL_CXC_TAP = "https://cda.cfa.harvard.edu/cxctap"
URL_ESO_TAP = "https://archive.eso.org/tap_obs"
URL_ESO_FILE = "https://dataportal.eso.org/dataportal_new/file/"
COLLEZIONE_EUCLID = "euclid_DpdMerBksMosaic"

# Collezioni MAST considerate (immagini); le altre (TESS, Kepler, ...) sono serie temporali
COLLEZIONI_MAST = {"JWST", "HST", "HLA", "GALEX", "SWIFT", "PS1", "SPITZER_SHA"}

ARCHIVI = [  # chiave, etichetta nella finestra, attivo di default
    ("mast", "MAST: JWST, HST, GALEX, Swift, Pan-STARRS", True),
    ("euclid", "Euclid (IRSA, Q1)", True),
    ("irsa", "IRSA: Spitzer, WISE, 2MASS, Herschel...", False),
    ("chandra", "Chandra (raggi X)", True),
    ("eso", "ESO, telescopi a terra (VLT, VISTA, VST)", True),
]

COLONNE_TABELLA = [  # chiave, titolo, larghezza (caratteri), allineamento
    ("sel", "✔", 3, "center"),
    ("telescopio", "Telescopio", 11, "w"),
    ("tipo", "Tipo", 8, "w"),
    ("strumento", "Strumento", 11, "w"),
    ("filtro", "Filtro", 14, "w"),
    ("dimensione", "Dimensione", 10, "e"),
    ("contiene", "Contiene", 9, "center"),
    ("distanza", "Dist. (')", 8, "e"),
    ("data", "Data oss.", 10, "center"),
    ("livello", "Livello", 7, "center"),
    ("nome_file", "File", 38, "w"),
    ("archivio", "Archivio", 8, "w"),
]

COLONNE_CSV = ["oggetto", "ra_oggetto_deg", "dec_oggetto_deg", "archivio", "telescopio",
               "tipo", "strumento", "filtro", "livello", "dimensione_MB", "dimensione_esatta",
               "contiene_oggetto", "distanza_arcmin", "data_osservazione", "esposizione_s",
               "id_osservazione", "target_archivio", "nome_file", "cartella_destinazione",
               "url", "verificato_wcs"]


# =============================================================================
# NOMI E COORDINATE (funzioni pure, senza rete)
# =============================================================================
def nome_cartella_oggetto(nome):
    """'ngc 346' -> 'NGC_346', 'IC1234' -> 'IC_1234', 'Cartwheel Galaxy' -> 'Cartwheel_Galaxy'."""
    nome = nome.strip()
    m = re.match(r"^([A-Za-z]+)[\s_\-]*(\d[\w.+\-]*)$", nome)
    if m:
        return f"{m.group(1).upper()}_{m.group(2).upper()}"
    pulito = re.sub(r"[^\w.+\-]+", "_", nome, flags=re.UNICODE).strip("_")
    return pulito or "OGGETTO"


def cartella_esistente(genitore, nome):
    """Se in 'genitore' esiste già una cartella con lo stesso nome a meno di
    maiuscole/minuscole (es. 'NGC_346' e 'ngc_346'), usa quella."""
    genitore = Path(genitore)
    if genitore.is_dir():
        for p in genitore.iterdir():
            if p.is_dir() and p.name.lower() == nome.lower():
                return p
    return genitore / nome


def _normalizza_testo_coordinate(testo):
    t = testo.strip()
    for a, b in (("−", "-"), ("–", "-"), ("—", "-"), ("‐", "-"), ("+", "+"),
                 ("′", "'"), ("’", "'"), ("‘", "'"), ("´", "'"), ("ʹ", "'"),
                 ("″", '"'), ("”", '"'), ("“", '"'), ("ʺ", '"'), ("''", '"'),
                 ("°", "d"), ("º", "d"), (",", " "), (";", " ")):
        t = t.replace(a, b)
    # toglie parole come RA, Dec, AR, Declinazione, Right ascension...
    t = re.sub(r"[A-Za-zÀ-ÿ]{2,}", " ", t)
    t = t.replace("'", "m").replace('"', "s")
    return re.sub(r"\s+", " ", t).strip()


def _sessagesimale(gradi, minuti, secondi, segno_neg):
    v = abs(float(gradi)) + float(minuti or 0) / 60 + float(secondi or 0) / 3600
    return -v if segno_neg else v


def leggi_coordinate(testo):
    """Converte un testo in (ra_deg, dec_deg). Formati accettati, ad esempio:
       '00h 59m 05.090s −72° 10′ 33.24″'   (Wikipedia)
       '00:59:05.09 -72:10:33.2'           (ore per RA, gradi per Dec)
       '14.7712 -72.1759'                   (gradi decimali)
    Solleva ValueError se il testo non è interpretabile."""
    t = _normalizza_testo_coordinate(testo)
    if not t:
        raise ValueError("coordinate vuote")
    num = r"(\d+(?:\.\d*)?)"
    # 1) forma con unità: 00h59m05s -72d10m33s
    # i secondi dell'AR possono non avere la "s", ma non devono essere i gradi della Dec ("... 10d")
    sec_ra = r"(?:" + num + r"(?![\d.]*\s*d)\s*s?)?"
    m_ra = re.search(r"([+-]?)\s*" + num + r"\s*h\s*(?:" + num + r"\s*m)?\s*" + sec_ra, t)
    if m_ra:
        resto = t[m_ra.end():]
        m_dec = re.search(r"([+-]?)\s*" + num + r"\s*d\s*(?:" + num + r"\s*m)?\s*(?:" + num + r"\s*s?)?", resto)
        if not m_dec:
            raise ValueError("trovata l'ascensione retta ma non la declinazione")
        ra = _sessagesimale(m_ra.group(2), m_ra.group(3), m_ra.group(4), m_ra.group(1) == "-") * 15
        dec = _sessagesimale(m_dec.group(2), m_dec.group(3), m_dec.group(4), m_dec.group(1) == "-")
        return _controlla(ra, dec)
    # 2) forma con i due punti: hh:mm:ss ±dd:mm:ss
    parti = t.split()
    if len(parti) == 2 and ":" in parti[0]:
        a = parti[0].split(":")
        d = parti[1].lstrip("+-").split(":")
        ra = _sessagesimale(a[0], a[1] if len(a) > 1 else 0, a[2] if len(a) > 2 else 0, False) * 15
        dec = _sessagesimale(d[0], d[1] if len(d) > 1 else 0, d[2] if len(d) > 2 else 0,
                             parti[1].startswith("-"))
        return _controlla(ra, dec)
    # 3) sei numeri: hh mm ss dd mm ss
    numeri = re.findall(r"[+-]?\d+(?:\.\d*)?", t)
    if len(numeri) == 6:
        ra = _sessagesimale(numeri[0], numeri[1], numeri[2], False) * 15
        dec = _sessagesimale(numeri[3], numeri[4], numeri[5], numeri[3].startswith("-"))
        return _controlla(ra, dec)
    # 4) due numeri decimali in gradi
    if len(numeri) == 2:
        return _controlla(float(numeri[0]), float(numeri[1]))
    raise ValueError(f"formato non riconosciuto: '{testo}'")


def _controlla(ra, dec):
    if not (0 <= ra < 360) or not (-90 <= dec <= 90):
        raise ValueError(f"coordinate fuori intervallo (RA={ra:.4f}, Dec={dec:.4f})")
    return ra, dec


def formatta_coordinate(ra, dec):
    h = ra / 15
    hh, mm = int(h), int((h % 1) * 60)
    ss = ((h * 60) % 1) * 60
    s = "-" if dec < 0 else "+"
    d = abs(dec)
    dd, dm = int(d), int((d % 1) * 60)
    ds = ((d * 60) % 1) * 60
    return f"{hh:02d}h{mm:02d}m{ss:05.2f}s {s}{dd:02d}°{dm:02d}′{ds:04.1f}″  ({ra:.5f}°, {dec:+.5f}°)"


# =============================================================================
# IMPRONTA DELLE IMMAGINI (s_region) - funzioni pure
# =============================================================================
_PAROLE_FORME = {"POLYGON", "CIRCLE", "BOX", "POSITION"}


def leggi_s_region(testo):
    """Interpreta una regione STC-S (es. 'POLYGON ICRS 10 20 11 20 11 21',
    'CIRCLE ICRS 10 20 0.1', 'BOX ICRS 10 20 0.2 0.1', anche più forme di
    seguito o con 'UNION'). Restituisce una lista di (tipo, [numeri])."""
    if testo is None:
        return []
    testo = str(testo).strip()
    if not testo or testo.lower() in ("none", "nan", "--"):
        return []
    if testo.startswith("{") or testo.startswith("<"):        # formato pgsphere: non gestito
        return []
    testo = re.sub(r"[(),']", " ", testo)
    forme, corrente = [], None
    for tok in testo.split():
        su = tok.upper()
        if su in _PAROLE_FORME:
            corrente = (su, [])
            forme.append(corrente)
            continue
        try:
            v = float(tok)
        except ValueError:
            continue                                  # ICRS, FK5, J2000, UNION, ...
        if corrente is not None:
            corrente[1].append(v)
    return [(t, n) for t, n in forme if n]


def _proietta(ra, dec, ra0, dec0):
    """Proiezione gnomonica (gradi) attorno a (ra0, dec0)."""
    a, d, a0, d0 = map(math.radians, (ra, dec, ra0, dec0))
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    if cosc <= 0:
        return None
    x = math.cos(d) * math.sin(a - a0) / cosc
    y = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cosc
    return math.degrees(x), math.degrees(y)


def separazione_deg(ra1, dec1, ra2, dec2):
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = (math.sin((d2 - d1) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((a2 - a1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


def _punto_in_poligono(vertici):
    """Il punto (0, 0) è dentro il poligono? (ray casting)"""
    dentro = False
    n = len(vertici)
    for i in range(n):
        x1, y1 = vertici[i]
        x2, y2 = vertici[(i + 1) % n]
        if (y1 > 0) != (y2 > 0):
            xc = x1 + (0 - y1) * (x2 - x1) / (y2 - y1)
            if xc > 0:
                dentro = not dentro
    return dentro


def contiene_punto(forme, ra, dec):
    """True/False se l'impronta contiene il punto, None se non è determinabile."""
    if not forme:
        return None
    determinato = False
    for tipo, n in forme:
        if tipo == "CIRCLE" and len(n) >= 3:
            determinato = True
            if separazione_deg(ra, dec, n[0], n[1]) <= n[2]:
                return True
        elif tipo == "BOX" and len(n) >= 4:
            p = _proietta(ra, dec, n[0], n[1])
            determinato = True
            if p and abs(p[0]) <= n[2] / 2 and abs(p[1]) <= n[3] / 2:
                return True
        elif tipo == "POLYGON" and len(n) >= 6:
            vertici = []
            for i in range(0, len(n) - 1, 2):
                p = _proietta(n[i], n[i + 1], ra, dec)
                if p is None:
                    vertici = []
                    break
                vertici.append(p)
            if len(vertici) >= 3:
                determinato = True
                if _punto_in_poligono(vertici):
                    return True
    return False if determinato else None


# =============================================================================
# TELESCOPI E CARTELLE
# =============================================================================
def telescopio_da(archivio, collezione="", facility="", strumento=""):
    """Restituisce (nome telescopio, cartella, tipo 'Spaziale'/'Terra')."""
    c, f, s = (str(collezione or "").upper(), str(facility or "").upper(), str(strumento or "").upper())
    tutto = " ".join((c, f, s))
    if archivio == "chandra":
        return "Chandra", "CHANDRA", "Spaziale"
    if archivio == "eso":
        for chiave, nome in (("VISTA", "VISTA"), ("VST", "VST"), ("VLT", "VLT"), ("NTT", "NTT"),
                             ("APEX", "APEX"), ("2.2", "MPG_2.2m"), ("3.6", "ESO_3.6m"),
                             ("ALMA", "ALMA")):
            if chiave in f:
                return nome, nome, "Terra"
        nome = re.sub(r"[^\w]+", "_", f or "ESO").strip("_") or "ESO"
        return nome, nome, "Terra"
    regole = [  # parola chiave -> (nome, cartella, tipo)
        ("JWST", ("JWST", "JWST", "Spaziale")),
        ("HLA", ("HST", "HST", "Spaziale")),
        ("HST", ("HST", "HST", "Spaziale")),
        ("GALEX", ("GALEX", "GALEX", "Spaziale")),
        ("SWIFT", ("Swift", "SWIFT", "Spaziale")),
        ("PS1", ("Pan-STARRS", "PANSTARRS", "Terra")),
        ("GPC", ("Pan-STARRS", "PANSTARRS", "Terra")),
        ("SPITZER", ("Spitzer", "SPITZER", "Spaziale")),
        ("WISE", ("WISE", "WISE", "Spaziale")),
        ("2MASS", ("2MASS", "2MASS", "Terra")),
        ("HERSCHEL", ("Herschel", "HERSCHEL", "Spaziale")),
        ("DECAM", ("Blanco DECam", "BLANCO_DECAM", "Terra")),
        ("HSC", ("Subaru HSC", "SUBARU_HSC", "Terra")),
        ("MEGACAM", ("CFHT MegaCam", "CFHT_MEGACAM", "Terra")),
        ("EUCLID", ("Euclid", "EUCLID", "Spaziale")),
        ("VIS", ("Euclid", "EUCLID", "Spaziale")),
        ("NISP", ("Euclid", "EUCLID", "Spaziale")),
    ]
    for chiave, valore in regole:
        if chiave in tutto:
            return valore
    nome = re.sub(r"[^\w]+", "_", c or f or archivio.upper()).strip("_")
    return nome, nome.upper(), "Spaziale" if archivio in ("mast", "euclid", "irsa") else "?"


def formatta_dimensione(n_byte, esatta=True):
    if n_byte is None:
        return "?"
    prefisso = "" if esatta else "~"
    for unita, f in (("GB", 1024**3), ("MB", 1024**2), ("kB", 1024)):
        if n_byte >= f:
            return f"{prefisso}{n_byte / f:.1f} {unita}" if unita != "kB" else f"{prefisso}{n_byte / f:.0f} kB"
    return f"{prefisso}{n_byte} B"


def mjd_in_data(mjd):
    try:
        mjd = float(mjd)
        if not math.isfinite(mjd):
            return ""
        return (datetime(1858, 11, 17) + timedelta(days=mjd)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return ""


def nome_file_sicuro(nome):
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(nome)).strip(" .")
    return nome or "file.fits"


# =============================================================================
# ACCESSO AGLI ARCHIVI (rete)
# =============================================================================
def _val(riga, *nomi, predefinito=None):
    """Valore di una colonna (prova più nomi), None se manca o è mascherato."""
    for nome in nomi:
        try:
            if nome not in riga.colnames:
                continue
        except AttributeError:
            pass
        try:
            v = riga[nome]
        except (KeyError, IndexError, ValueError):
            continue
        if v is None or (hasattr(np, "ma") and v is np.ma.masked):
            continue
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        if isinstance(v, (float, np.floating)) and not math.isfinite(float(v)):
            continue
        if isinstance(v, str) and v.strip() in ("", "--", "nan", "None"):
            continue
        if hasattr(v, "item") and not isinstance(v, str):
            try:
                return v.item()
            except ValueError:           # colonna con più valori (es. imgscale di Chandra)
                return v
        return v
    return predefinito


def _risultato(**k):
    base = dict(archivio="", telescopio="", cartella_telescopio="", tipo="", strumento="",
                filtro="", livello="", dimensione=None, dimensione_esatta=True, s_region=None,
                ra_centro=None, dec_centro=None, data="", esposizione=None, id_oss="",
                target="", nome_file="", url="", contiene=None, distanza=None,
                verificato_wcs="", selezionato=False)
    base.update(k)
    return base


def url_da_dataurl(uri):
    """Indirizzi di download per il dataURL di un'osservazione MAST.
    Di solito è un URI «mast:JWST/product/...» da passare al servizio Download;
    per Pan-STARRS (e altre collezioni) è invece già un indirizzo web
    (https://ps1images.stsci.edu/...): passato al servizio Download dava
    «HTTP Error 400: Bad Request» (9/11). Si usa direttamente, MAST come riserva."""
    uri = str(uri)
    if uri.startswith(("http://", "https://")):
        out = [_https(uri), uri]
    else:
        out = [URL_MAST_DOWNLOAD + urllib.parse.quote(uri, safe=":/")]
    nome = uri.split("/")[-1].split("?")[0]
    m = re.match(r"rings\.v3\.skycell\.(\d{4})\.(\d{3})\.", nome)
    if m:          # archivio di Pan-STARRS: il percorso si ricava dal nome del file
        out.append(f"https://ps1images.stsci.edu/rings.v3.skycell/{m.group(1)}/{m.group(2)}/{nome}")
    return list(dict.fromkeys(out))


def cerca_mast(ra, dec, raggio_deg, livelli, log):
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    from astroquery.mast import Observations
    coord = SkyCoord(ra * u.deg, dec * u.deg)
    log(f"MAST: interrogazione (livelli {livelli})...")
    obs = Observations.query_criteria(coordinates=coord, radius=f"{raggio_deg} deg",
                                      dataproduct_type="image", calib_level=livelli,
                                      intentType="science")
    if len(obs) == 0:
        return []
    tieni = [str(_val(r, "obs_collection", predefinito="")).upper() in COLLEZIONI_MAST
             and str(_val(r, "dataRights", predefinito="PUBLIC")).upper() == "PUBLIC"
             and _val(r, "dataURL") for r in obs]
    obs = obs[np.array(tieni, dtype=bool)]
    log(f"MAST: {len(obs)} osservazioni pubbliche con immagini")
    if len(obs) > MAX_RISULTATI_ARCHIVIO:
        obs = obs[:MAX_RISULTATI_ARCHIVIO]
    # dimensione dei file: dalla lista dei prodotti, a blocchi
    dimensioni = {}
    ids = [str(x) for x in obs["obsid"]]
    blocchi = [ids[i:i + 50] for i in range(0, len(ids), 50)]
    log(f"MAST: lettura delle dimensioni dei file ({len(blocchi)} blocchi in parallelo)...")

    def leggi_blocco(blocco):
        out = {}
        for p in Observations.get_product_list(blocco):
            uri, size = _val(p, "dataURI"), _val(p, "size")
            if uri and size is not None:
                out[str(uri)] = int(size)
                out["nome:" + str(uri).split("/")[-1]] = int(size)     # per i dataURL web (Pan-STARRS)
        return out

    with ThreadPoolExecutor(max_workers=6) as ex:
        for fut in as_completed([ex.submit(leggi_blocco, b) for b in blocchi]):
            try:
                dimensioni.update(fut.result())
            except Exception as e:
                log(f"MAST: dimensioni non disponibili per un blocco ({e})")
    risultati = []
    for r in obs:
        uri = str(_val(r, "dataURL"))
        indirizzi = url_da_dataurl(uri)
        tel, cart, tipo = telescopio_da("mast", _val(r, "obs_collection"), "", _val(r, "instrument_name"))
        risultati.append(_risultato(
            archivio="MAST", telescopio=tel, cartella_telescopio=cart, tipo=tipo,
            strumento=str(_val(r, "instrument_name", predefinito="")),
            filtro=str(_val(r, "filters", predefinito="")),
            livello=str(_val(r, "calib_level", predefinito="")),
            dimensione=dimensioni.get(uri, dimensioni.get("nome:" + uri.split("/")[-1].split("?")[0])),
            s_region=_val(r, "s_region"),
            ra_centro=_val(r, "s_ra"), dec_centro=_val(r, "s_dec"),
            data=mjd_in_data(_val(r, "t_min")), esposizione=_val(r, "t_exptime"),
            id_oss=str(_val(r, "obs_id", predefinito="")),
            target=str(_val(r, "target_name", predefinito="")),
            nome_file=nome_file_sicuro(uri.split("/")[-1].split("?")[0]),
            url=indirizzi[0], url_alternativi=indirizzi[1:]))
    return risultati


def _sia_irsa(ra, dec, raggio_deg, collezione, log, etichetta):
    import astropy.units as u
    import pyvo
    try:
        svc = pyvo.dal.SIA2Service(URL_IRSA_SIA)
    except Exception:
        time.sleep(5)                      # il servizio a volte non risponde al primo colpo
        svc = pyvo.dal.SIA2Service(URL_IRSA_SIA)
    argomenti = dict(pos=(ra * u.deg, dec * u.deg, raggio_deg * u.deg), maxrec=MAX_RISULTATI_ARCHIVIO)
    if collezione:
        argomenti["collection"] = collezione
    log(f"{etichetta}: interrogazione...")
    try:
        return svc.search(**argomenti).to_table()
    except Exception as e:
        log(f"{etichetta}: nessuna risposta ({type(e).__name__}), nuovo tentativo tra 5 s...")
        time.sleep(5)
        return pyvo.dal.SIA2Service(URL_IRSA_SIA).search(**argomenti).to_table()


def interroga_archivi(funzioni, ra, dec, raggio, livelli, log, ferma=None, limite=LIMITE_ARCHIVIO,
                      avviso_ogni=30):
    """Interroga gli archivi in parallelo, ognuno nel suo thread, e restituisce
    (chiave, risultati o eccezione) man mano che arrivano. Chi non risponde entro
    `limite` secondi riceve TimeoutError: la ricerca finisce lo stesso con gli
    altri archivi (prima un archivio bloccato, es. Euclid, fermava tutto).
    I thread sono 'daemon': un archivio bloccato non impedisce di chiudere il programma."""
    coda = queue.Queue()

    def esegui(k, f):
        try:
            coda.put((k, f(ra, dec, raggio, livelli, log)))
        except Exception as e:
            coda.put((k, e))

    for k, f in funzioni.items():
        threading.Thread(target=esegui, args=(k, f), daemon=True).start()
    attesi = set(funzioni)
    inizio = time.time()
    prossimo_avviso = avviso_ogni
    while attesi:
        if ferma is not None and ferma.is_set():
            for k in sorted(attesi):
                yield k, InterruptedError("ricerca interrotta")
            return
        try:
            k, ris = coda.get(timeout=0.5)
        except queue.Empty:
            trascorso = time.time() - inizio
            if trascorso >= limite:
                for k in sorted(attesi):
                    yield k, TimeoutError(f"nessuna risposta in {limite:.0f} s")
                return
            if trascorso >= prossimo_avviso:
                prossimo_avviso += avviso_ogni
                log(f"Aspetto ancora: {', '.join(sorted(attesi))} ({trascorso:.0f} s; "
                    f"al massimo {limite:.0f} s, oppure «Interrompi»)")
            continue
        if k in attesi:
            attesi.discard(k)
            yield k, ris


def livello_ammesso(valore, livelli):
    """Livello di calibrazione richiesto? (3 = immagini combinate e ottimizzate,
    2 = singole esposizioni calibrate). Se l'archivio non lo indica si tiene."""
    try:
        return int(float(valore)) in livelli
    except (TypeError, ValueError):
        return True


def _da_obscore(tab, archivio_chiave, archivio_nome, livelli, filtro_righe=None):
    risultati = []
    for r in tab:
        if filtro_righe and not filtro_righe(r):
            continue
        if not livello_ammesso(_val(r, "calib_level"), livelli):
            continue
        tipo_prod = str(_val(r, "dataproduct_type", predefinito="image")).lower()
        formato = str(_val(r, "access_format", predefinito="fits")).lower()
        if tipo_prod not in ("image", "cube") or ("fits" not in formato and "image" not in formato):
            continue
        url = _val(r, "access_url")
        if not url:
            continue
        url = str(url)
        stima = _val(r, "access_estsize")
        tel, cart, tipo = telescopio_da(archivio_chiave, _val(r, "obs_collection"),
                                        _val(r, "facility_name"), _val(r, "instrument_name"))
        nome = urllib.parse.urlparse(url).path.split("/")[-1] or f"{_val(r, 'obs_id', predefinito='file')}.fits"
        risultati.append(_risultato(
            archivio=archivio_nome, telescopio=tel, cartella_telescopio=cart, tipo=tipo,
            strumento=str(_val(r, "instrument_name", predefinito="")),
            filtro=str(_val(r, "energy_bandpassname", "filter", "band_name", predefinito="")),
            livello=str(_val(r, "calib_level", predefinito="")),
            dimensione=int(float(stima) * 1024) if stima is not None else None,
            dimensione_esatta=False, s_region=_val(r, "s_region"),
            ra_centro=_val(r, "s_ra"), dec_centro=_val(r, "s_dec"),
            data=mjd_in_data(_val(r, "t_min")), esposizione=_val(r, "t_exptime"),
            id_oss=str(_val(r, "obs_id", predefinito="")),
            target=str(_val(r, "target_name", predefinito="")),
            nome_file=nome_file_sicuro(nome), url=url))
    return risultati


def cerca_euclid(ra, dec, raggio_deg, livelli, log):
    tab = _sia_irsa(ra, dec, raggio_deg, COLLEZIONE_EUCLID, log, "Euclid")
    return _da_obscore(tab, "euclid", "IRSA", livelli)


def cerca_irsa(ra, dec, raggio_deg, livelli, log):
    tab = _sia_irsa(ra, dec, raggio_deg, None, log, "IRSA")
    # Euclid è già cercato a parte
    return _da_obscore(tab, "irsa", "IRSA", livelli,
                       lambda r: not str(_val(r, "obs_collection", predefinito="")).lower().startswith("euclid"))


def _tap(url, adql, maxrec=MAX_RISULTATI_ARCHIVIO):
    import pyvo
    return pyvo.dal.TAPService(url).run_sync(adql, maxrec=maxrec).to_table()


def condizione_riquadro(ra, dec, r, col_ra="ra", col_dec="dec"):
    """Condizione SQL per un riquadro attorno a (ra, dec) di semilato r gradi,
    gestendo il passaggio per RA = 0/360 e la vicinanza ai poli."""
    d1, d2 = max(dec - r, -90.0), min(dec + r, 90.0)
    cond = f"{col_dec} BETWEEN {d1:.6f} AND {d2:.6f}"
    if d1 <= -89.9 or d2 >= 89.9:
        return cond
    dra = r / max(math.cos(math.radians(max(abs(d1), abs(d2)))), 1e-3)
    if dra >= 180:
        return cond
    a1, a2 = ra - dra, ra + dra
    if a1 < 0:
        return cond + f" AND ({col_ra} >= {a1 + 360:.6f} OR {col_ra} <= {a2:.6f})"
    if a2 >= 360:
        return cond + f" AND ({col_ra} >= {a1:.6f} OR {col_ra} <= {a2 - 360:.6f})"
    return cond + f" AND {col_ra} BETWEEN {a1:.6f} AND {a2:.6f}"


def cerca_chandra(ra, dec, raggio_deg, livelli, log):
    """Immagini a campo intero del Chandra Data Archive (tabella cxc.image).
    Il centro dell'immagine può distare fino a ~0.3° dall'oggetto: l'impronta
    si ricava da dimensioni e scala dell'immagine (le immagini sono orientate Nord in alto)."""
    log("Chandra: interrogazione...")
    r_ricerca = raggio_deg + 0.35
    # il servizio CXC non supporta CIRCLE/CONTAINS: si chiede un riquadro in ra/dec
    # e poi si tengono solo le immagini con il centro entro r_ricerca
    tab = _tap(URL_CXC_TAP, "SELECT * FROM cxc.image WHERE " + condizione_riquadro(ra, dec, r_ricerca))
    risultati = []
    for r in tab:
        url = _val(r, "accref")
        if not url:
            continue
        try:
            if separazione_deg(ra, dec, float(_val(r, "ra")), float(_val(r, "dec"))) > r_ricerca:
                continue
        except (TypeError, ValueError):
            continue
        url = str(url)
        rc, dc = _val(r, "ra"), _val(r, "dec")
        scala = _val(r, "imgscale")
        assi = [int(x) for x in re.findall(r"\d+", str(_val(r, "naxis", predefinito="")))]
        s_region = None
        if rc is not None and dc is not None and scala is not None and len(assi) >= 2:
            sc = np.abs(np.ravel(np.asarray(scala, dtype=float)))
            sx, sy = (sc[0], sc[1] if len(sc) > 1 else sc[0])
            s_region = f"BOX ICRS {rc} {dc} {assi[0] * sx} {assi[1] * sy}"
        q = urllib.parse.urlparse(url)
        nome = urllib.parse.parse_qs(q.query).get("filename", [q.path.split("/")[-1]])[0]
        if not nome or "." not in nome:
            nome = f"chandra_{_val(r, 'obsid', predefinito='x')}_{_val(r, 'bandpass', predefinito='img')}.fits"
        bl, bh = _val(r, "bandlo"), _val(r, "bandhi")
        banda = str(_val(r, "bandpass", predefinito=""))
        if bl is not None and bh is not None:
            banda = f"{banda} {float(bl):g}-{float(bh):g} keV".strip()
        esp = _val(r, "exptime")
        dim = _val(r, "filesize")
        risultati.append(_risultato(
            archivio="CXC", telescopio="Chandra", cartella_telescopio="CHANDRA", tipo="Spaziale",
            strumento=str(_val(r, "instrument", predefinito="")), filtro=banda, livello="3",
            dimensione=int(dim) if dim is not None else None, s_region=s_region,
            ra_centro=rc, dec_centro=dc, data=mjd_in_data(_val(r, "date_obs")),
            esposizione=float(esp) * 1000 if esp is not None else None,
            id_oss=str(_val(r, "obsid", predefinito="")),
            target=str(_val(r, "name", predefinito="")),
            nome_file=nome_file_sicuro(nome), url=url))
    return risultati


def cerca_eso(ra, dec, raggio_deg, livelli, log):
    """Prodotti ridotti (immagini) dell'archivio ESO, solo quelli già pubblici."""
    log("ESO: interrogazione...")
    tab = _tap(URL_ESO_TAP, "SELECT * FROM ivoa.ObsCore WHERE dataproduct_type = 'image' AND "
               f"INTERSECTS(s_region, CIRCLE('', {ra:.6f}, {dec:.6f}, {raggio_deg:.5f})) = 1")
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    risultati = []
    for r in tab:
        dp_id = _val(r, "dp_id")
        if not dp_id:
            continue
        rilascio = str(_val(r, "obs_release_date", predefinito=""))[:10]
        if rilascio and rilascio > oggi:
            continue                                   # dati ancora riservati
        if not livello_ammesso(_val(r, "calib_level"), livelli):
            continue
        tel, cart, tipo = telescopio_da("eso", _val(r, "obs_collection"),
                                        _val(r, "facility_name"), _val(r, "instrument_name"))
        stima = _val(r, "access_estsize")
        risultati.append(_risultato(
            archivio="ESO", telescopio=tel, cartella_telescopio=cart, tipo=tipo,
            strumento=str(_val(r, "instrument_name", predefinito="")),
            filtro=str(_val(r, "filter", "energy_bandpassname", predefinito="")),
            livello=str(_val(r, "calib_level", predefinito="")),
            dimensione=int(float(stima) * 1024) if stima is not None else None,
            dimensione_esatta=False, s_region=_val(r, "s_region"),
            ra_centro=_val(r, "s_ra"), dec_centro=_val(r, "s_dec"),
            data=mjd_in_data(_val(r, "t_min")), esposizione=_val(r, "t_exptime"),
            id_oss=str(dp_id), target=str(_val(r, "target_name", predefinito="")),
            nome_file=nome_file_sicuro(f"{dp_id}.fits"),
            url=URL_ESO_FILE + urllib.parse.quote(str(dp_id))))
    return risultati


FUNZIONI_ARCHIVI = {"mast": cerca_mast, "euclid": cerca_euclid, "irsa": cerca_irsa,
                    "chandra": cerca_chandra, "eso": cerca_eso}


def completa_verifica(risultati, ra, dec):
    """Aggiunge 'contiene' (impronta) e 'distanza' (arcmin dal centro immagine)."""
    for r in risultati:
        forme = leggi_s_region(r["s_region"])
        r["contiene"] = contiene_punto(forme, ra, dec)
        try:
            r["distanza"] = separazione_deg(ra, dec, float(r["ra_centro"]), float(r["dec_centro"])) * 60
        except (TypeError, ValueError):
            r["distanza"] = None
    return risultati


# =============================================================================
# RISOLUZIONE DEI NOMI: da nome, nome comune (anche italiano) o link di Wikipedia
# alle coordinate. Più fonti in fila: se una non risponde si passa alla successiva.
# =============================================================================
INTESTAZIONI_HTTP = {"User-Agent": f"RicercaArchiviFITS/{VERSIONE} (programma di astronomia amatoriale)"}
TIMEOUT_NOMI = 12                    # secondi per ogni servizio di risoluzione dei nomi
FILE_OPENNGC = "openngc_compatto.csv"
FILE_ICONA = "ricerca_archivi.png"       # icona della finestra (dalla 1.9)
URL_SESAME = ["https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?",
              "http://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oI/SNV?"]
URL_NED_LOOKUP = "https://ned.ipac.caltech.edu/srs/ObjectLookup"
URL_WIKIDATA = "https://www.wikidata.org/w/api.php"

# Soprannomi famosi che i cataloghi non conoscono (o conoscono con un altro nome).
# Valore: un nome di catalogo da cercare, oppure coordinate (RA, Dec in gradi) e una descrizione.
SOPRANNOMI = {
    "pillars of creation": ((274.71958, -13.83944), "Pilastri della Creazione, nella Nebulosa Aquila (M 16)"),
    "pilastri della creazione": "pillars of creation",
    "colonne della creazione": "pillars of creation",
    "hubble deep field": ((189.20583, 62.21611), "Hubble Deep Field (HDF Nord)"),
    "hdf": "hubble deep field",
    "hubble ultra deep field": ((53.1625, -27.79139), "Hubble Ultra Deep Field"),
    "hudf": "hubble ultra deep field",
    "horsehead nebula": ((85.24583, -2.45833), "Nebulosa Testa di Cavallo (Barnard 33)"),
    "nebulosa testa di cavallo": "horsehead nebula",
    "testa di cavallo": "horsehead nebula",
    "barnard 33": "horsehead nebula",
    "cartwheel galaxy": ((9.42125, -33.71639), "Galassia Ruota di Carro (ESO 350-40)"),
    "galassia ruota di carro": "cartwheel galaxy",
    "cosmic cliffs": "NGC 3324",
    "scogliere cosmiche": "NGC 3324",
    "southern ring nebula": "NGC 3132",
    "southern ring": "NGC 3132",
    "nebulosa anello del sud": "NGC 3132",
    "anello del sud": "NGC 3132",
    "stephan's quintet": "NGC 7318",
    "stephans quintet": "NGC 7318",
    "quintetto di stephan": "NGC 7318",
    "phantom galaxy": "M 74",
    "galassia fantasma": "M 74",
    # nomi italiani dei Messier e degli oggetti più noti
    "nebulosa aquila": "M 16", "nebulosa dell'aquila": "M 16",
    "nebulosa di orione": "M 42", "grande nebulosa di orione": "M 42",
    "nebulosa granchio": "M 1", "nebulosa del granchio": "M 1",
    "galassia di andromeda": "M 31", "andromeda": "M 31",
    "galassia del triangolo": "M 33",
    "nebulosa anello": "M 57", "nebulosa ad anello": "M 57",
    "nebulosa laguna": "M 8", "nebulosa della laguna": "M 8",
    "nebulosa trifida": "M 20",
    "nebulosa omega": "M 17", "nebulosa cigno": "M 17",
    "nebulosa manubrio": "M 27",
    "galassia vortice": "M 51", "galassia whirlpool": "M 51",
    "galassia sombrero": "M 104",
    "galassia girandola": "M 101",
    "galassia sigaro": "M 82",
    "galassia di bode": "M 81",
    "pleiadi": "M 45",
    "presepe": "M 44",
    "nebulosa carena": "NGC 3372", "nebulosa della carena": "NGC 3372",
    "nebulosa tarantola": "NGC 2070",
    "nebulosa elica": "NGC 7293",
    "nebulosa occhio di gatto": "NGC 6543",
    "nebulosa nord america": "NGC 7000", "nebulosa nordamerica": "NGC 7000",
    "nebulosa rosetta": "NGC 2237",
    "nebulosa velo": "NGC 6960",
}

_CATALOGO_NGC = None


def percorso_dati(nome):
    """File di dati accanto al programma (o dentro l'eseguibile PyInstaller)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / nome


def _sessagesimale_deg(testo, ore):
    t = testo.strip()
    segno = -1 if t.startswith("-") else 1
    parti = [float(x) for x in t.lstrip("+-").split(":")] + [0, 0]
    v = parti[0] + parti[1] / 60 + parti[2] / 3600
    return segno * v * (15 if ore else 1)


def catalogo_openngc():
    """Catalogo OpenNGC (NGC, IC, Messier, nomi comuni) incluso: funziona senza Internet."""
    global _CATALOGO_NGC
    if _CATALOGO_NGC is None:
        righe = []
        p = percorso_dati(FILE_OPENNGC)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader((x for x in f if not x.startswith("#")), delimiter=";"):
                    try:
                        r["ra_deg"] = _sessagesimale_deg(r["ra"], True)
                        r["dec_deg"] = _sessagesimale_deg(r["dec"], False)
                    except (ValueError, IndexError):
                        continue
                    righe.append(r)
        _CATALOGO_NGC = righe
    return _CATALOGO_NGC


def _chiave_nome(testo):
    """Forma confrontabile di un nome: minuscole, senza accenti, 'the' e punteggiatura."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(testo)).encode("ascii", "ignore").decode().lower()
    t = t.replace("’", "'").strip()
    t = re.sub(r"^(the|la|il|lo|l')\s*", "", t)
    return re.sub(r"[^a-z0-9']+", " ", t).strip()


def _nome_leggibile(r):
    n = re.sub(r"^(NGC|IC)0*(\d+)(.*)$", r"\1 \2\3", r["nome"])
    parti = []
    if r.get("messier"):
        parti.append(f"M {int(r['messier'])}")
    parti.append(n)
    t = " / ".join(parti)
    return t + (f" ({r['nomi_comuni'].split(',')[0]})" if r.get("nomi_comuni") else "")


def cerca_catalogo_incluso(nome):
    """Cerca nel catalogo OpenNGC incluso: M 16, Messier 16, NGC 6611, IC 4703,
    oppure un nome comune inglese (Eagle Nebula). Restituisce (ra, dec, descrizione) o None."""
    cat = catalogo_openngc()
    if not cat:
        return None
    s = re.sub(r"[\s_\-.]+", "", str(nome).upper())
    m = re.match(r"^(?:M|MESSIER)(\d{1,3})$", s)
    if m:
        cand = [r for r in cat if r.get("messier") and int(r["messier"]) == int(m.group(1))]
    else:
        m = re.match(r"^(NGC|IC)(\d{1,4})([A-Z]?)$", s)
        if m:
            codice = f"{m.group(1)}{int(m.group(2)):04d}{m.group(3)}"
            cand = [r for r in cat if r["nome"] == codice]
        else:
            k = _chiave_nome(nome)
            if len(k) < 3:
                return None
            cand = [r for r in cat if r.get("nomi_comuni") and
                    any(_chiave_nome(c) == k for c in r["nomi_comuni"].split(","))]
    if not cand:
        return None

    def importanza(r):
        try:
            asse = float(r.get("asse_maggiore_arcmin") or 0)
        except ValueError:
            asse = 0
        return (bool(r.get("messier")), asse)
    r = max(cand, key=importanza)
    return r["ra_deg"], r["dec_deg"], f"{_nome_leggibile(r)} — catalogo OpenNGC incluso nel programma"


def cerca_soprannome(nome):
    """Soprannomi famosi e nomi italiani (tabella SOPRANNOMI)."""
    k = _chiave_nome(nome)
    v = SOPRANNOMI.get(k)
    for _ in range(3):                    # i rimandi tra soprannomi
        if isinstance(v, str) and _chiave_nome(v) in SOPRANNOMI:
            v = SOPRANNOMI[_chiave_nome(v)]
    if v is None:
        return None
    if isinstance(v, tuple):
        (ra, dec), descr = v
        return ra, dec, f"{descr} — tabella dei soprannomi del programma"
    r = cerca_catalogo_incluso(v)
    if r:
        return r[0], r[1], f"«{nome.strip()}» = {r[2]}"
    return None


def _scarica_testo(url, dati=None, timeout=TIMEOUT_NOMI):
    req = urllib.request.Request(url, data=dati, headers=INTESTAZIONI_HTTP)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read().decode("utf-8", "replace")


def leggi_risposta_sesame(testo):
    """Dalla risposta testuale di Sesame: (ra, dec, nome) oppure None."""
    m = re.search(r"^%J\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)", testo, re.M)
    if not m:
        return None
    n = re.search(r"^%I\.0\s+(.+)$", testo, re.M)
    return float(m.group(1)), float(m.group(2)), (n.group(1).strip() if n else "")


def cerca_sesame(nome):
    """Sesame (SIMBAD + NED + VizieR): server del CDS e poi mirror di Harvard."""
    errori = []
    for base in URL_SESAME:
        host = urllib.parse.urlparse(base).hostname
        try:
            r = leggi_risposta_sesame(_scarica_testo(base + urllib.parse.quote(nome)))
            if r:
                return r[0], r[1], f"{r[2] or nome} — Sesame ({host})"
            errori.append(f"{host}: nome sconosciuto")
        except Exception as e:
            errori.append(f"{host}: {type(e).__name__} {str(e)[:80]}")
    raise LookupError("; ".join(errori))


def leggi_risposta_ned(testo):
    d = json.loads(testo)
    pos = (d.get("Preferred") or {}).get("Position") or {}
    if pos.get("RA") is None or pos.get("Dec") is None:
        return None
    nome = ((d.get("Preferred") or {}).get("Name")) or ((d.get("Interpreted") or {}).get("Name")) or ""
    return float(pos["RA"]), float(pos["Dec"]), nome


def cerca_ned(nome):
    """NED (NASA/IPAC, Caltech): soprattutto galassie."""
    dati = urllib.parse.urlencode({"json": json.dumps({"name": {"v": nome}})}).encode()
    r = leggi_risposta_ned(_scarica_testo(URL_NED_LOOKUP, dati))
    if not r:
        raise LookupError("nome sconosciuto")
    return r[0], r[1], f"{r[2] or nome} — NED"


def link_wikipedia(testo):
    """(lingua, titolo) se il testo è un link di Wikipedia, altrimenti None."""
    m = re.match(r"^\s*https?://([a-z\-]+)(?:\.m)?\.wikipedia\.org/wiki/([^#?\s]+)", str(testo))
    if not m:
        return None
    return m.group(1), urllib.parse.unquote(m.group(2)).replace("_", " ")


def _quantita(entita, proprieta):
    try:
        return float(entita["claims"][proprieta][0]["mainsnak"]["datavalue"]["value"]["amount"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _riferimenti(entita, proprieta):
    out = []
    for c in (entita.get("claims", {}).get(proprieta) or []):
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            pass
    return out


def coordinate_wikidata(entita):
    """RA/Dec (P6257/P6258) di un elemento Wikidata, oppure None."""
    ra, dec = _quantita(entita, "P6257"), _quantita(entita, "P6258")
    return (ra % 360, dec) if ra is not None and dec is not None else None


def _etichetta(entita, lingua="it"):
    lab = entita.get("labels", {})
    for l in (lingua, "en", "it"):
        if l in lab:
            return lab[l]["value"]
    return entita.get("id", "")


def _entita_wikidata(**parametri):
    q = {"action": "wbgetentities", "format": "json", "props": "claims|labels|sitelinks", "languages": "it|en"}
    q.update(parametri)
    d = json.loads(_scarica_testo(URL_WIKIDATA + "?" + urllib.parse.urlencode(q)))
    return [e for e in d.get("entities", {}).values() if "missing" not in e]


def _da_entita(entita, lingua="it"):
    """Coordinate dell'elemento o, se mancano, dell'oggetto di cui fa parte (P361)."""
    c = coordinate_wikidata(entita)
    if c:
        return c[0], c[1], f"{_etichetta(entita, lingua)} — Wikidata"
    genitori = _riferimenti(entita, "P361") + _riferimenti(entita, "P706")
    if genitori:
        for g in _entita_wikidata(ids="|".join(genitori[:5])):
            c = coordinate_wikidata(g)
            if c:
                return c[0], c[1], (f"{_etichetta(entita, lingua)}: coordinate di «{_etichetta(g, lingua)}», "
                                    f"di cui fa parte — Wikidata")
    return None


def coordinate_wikitesto(testo):
    """Coordinate dai modelli {{RA|h|m|s}} e {{DEC|g|m|s}} del testo di una voce di Wikipedia."""
    mr = re.search(r"\{\{\s*RA\s*\|\s*([\d.]+)\s*\|\s*([\d.]*)\s*\|?\s*([\d.]*)", testo, re.I)
    md = re.search(r"\{\{\s*DEC\s*\|\s*([+\-−–]?\s*[\d.]+)\s*\|\s*([\d.]*)\s*\|?\s*([\d.]*)", testo, re.I)
    if not (mr and md):
        return None
    f = lambda x: float(x) if x else 0.0
    ra = (f(mr.group(1)) + f(mr.group(2)) / 60 + f(mr.group(3)) / 3600) * 15
    g = md.group(1).replace("−", "-").replace("–", "-").replace(" ", "")
    neg = g.startswith("-")
    dec = abs(f(g)) + f(md.group(2)) / 60 + f(md.group(3)) / 3600
    return ra, -dec if neg else dec


def cerca_wikipedia(testo):
    """Da un link di Wikipedia (o da un nome cercato su Wikidata in italiano e
    inglese) alle coordinate: Wikidata, poi il testo della voce, poi l'oggetto 'genitore'."""
    lw = link_wikipedia(testo)
    if lw:
        lingua, titolo = lw
        entita = _entita_wikidata(sites=f"{lingua}wiki", titles=titolo, normalize=1)
        for e in entita:
            r = _da_entita(e, lingua)
            if r and "fa parte" not in r[2]:
                return r
        # coordinate scritte nella voce (modelli RA/DEC)
        try:
            q = {"action": "parse", "page": titolo, "prop": "wikitext", "format": "json", "redirects": 1}
            d = json.loads(_scarica_testo(f"https://{lingua}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(q)))
            c = coordinate_wikitesto(d.get("parse", {}).get("wikitext", {}).get("*", ""))
            if c:
                return c[0], c[1], f"{titolo} — voce di Wikipedia ({lingua})"
        except Exception:
            pass
        for e in entita:
            r = _da_entita(e, lingua)
            if r:
                return r
        raise LookupError(f"la voce «{titolo}» non contiene coordinate")
    trovati = []
    for lingua in ("it", "en"):
        q = {"action": "wbsearchentities", "search": str(testo), "language": lingua, "uselang": lingua,
             "type": "item", "limit": 7, "format": "json"}
        d = json.loads(_scarica_testo(URL_WIKIDATA + "?" + urllib.parse.urlencode(q)))
        for x in d.get("search", []):
            if x["id"] not in trovati:
                trovati.append(x["id"])
        if trovati:
            break
    if not trovati:
        raise LookupError("nessuna voce trovata")
    entita = {e["id"]: e for e in _entita_wikidata(ids="|".join(trovati[:10]))}
    for qid in trovati:                    # prima chi ha coordinate proprie, nell'ordine di Wikidata
        e = entita.get(qid)
        if e and coordinate_wikidata(e):
            return _da_entita(e)
    for qid in trovati:
        e = entita.get(qid)
        r = _da_entita(e) if e else None
        if r:
            return r
    raise LookupError("le voci trovate non hanno coordinate celesti")


def con_tempo_massimo(funzione, secondi, *argomenti):
    """Esegue la funzione in un thread: se non finisce entro 'secondi' si rinuncia
    (alcune librerie aspettano minuti prima di arrendersi)."""
    esito = {}

    def corpo():
        try:
            esito["r"] = funzione(*argomenti)
        except Exception as e:
            esito["e"] = e
    t = threading.Thread(target=corpo, daemon=True)
    t.start()
    t.join(secondi)
    if t.is_alive():
        raise TimeoutError(f"nessuna risposta in {secondi} s")
    if "e" in esito:
        raise esito["e"]
    return esito.get("r")


def cerca_simbad(nome):
    return con_tempo_massimo(_cerca_simbad, 20, nome)


def _cerca_simbad(nome):
    from astroquery.simbad import Simbad
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    Simbad.TIMEOUT = TIMEOUT_NOMI
    t = Simbad.query_object(nome)
    if t is None or not len(t):
        raise LookupError("oggetto non trovato")
    nomi = {c.lower(): c for c in t.colnames}
    try:
        return float(t[nomi["ra"]][0]), float(t[nomi["dec"]][0]), f"{nome} — SIMBAD"
    except (TypeError, ValueError, KeyError):
        c = SkyCoord(str(t[nomi["ra"]][0]), str(t[nomi["dec"]][0]), unit=(u.hourangle, u.deg))
        return float(c.ra.deg), float(c.dec.deg), f"{nome} — SIMBAD"


def risolvi_nome(nome, log=None):
    """Coordinate di un oggetto dal nome. Prova in ordine:
      link di Wikipedia -> soprannomi e nomi italiani -> catalogo OpenNGC incluso
      -> Sesame (CDS, poi Harvard) -> NED -> Wikidata/Wikipedia -> SIMBAD.
    Restituisce (ra, dec, descrizione della fonte); solleva ValueError con
    l'elenco dei tentativi se nessuna fonte lo conosce."""
    log = log or (lambda m: None)
    testo = str(nome).strip()
    try:
        ra, dec = leggi_coordinate(testo)
        return ra, dec, "coordinate scritte nel campo del nome"
    except ValueError:
        pass
    fonti_nome = [("soprannomi del programma", cerca_soprannome),
                  ("catalogo OpenNGC incluso", cerca_catalogo_incluso),
                  ("NED", cerca_ned),
                  ("Wikidata / Wikipedia", cerca_wikipedia),
                  ("Sesame (CDS / Harvard)", cerca_sesame),
                  ("SIMBAD", cerca_simbad)]
    lw = link_wikipedia(testo)
    if lw:
        # prima la voce del link; se non ha coordinate si cerca il suo titolo con le altre fonti
        titolo = lw[1]
        fonti = [("Wikipedia (link)", cerca_wikipedia)] + [
            (f"{et} con «{titolo}»", (lambda _t, f=f: f(titolo))) for et, f in fonti_nome if f is not cerca_wikipedia]
    else:
        fonti = fonti_nome
    errori = []
    for etichetta, funzione in fonti:
        t0 = time.time()
        try:
            r = funzione(testo)
        except Exception as e:
            errori.append(f"  {etichetta}: {type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}")
            log(f"  {etichetta}: non risolto ({time.time() - t0:.0f} s)")
            continue
        if r:
            ra, dec, descr = r
            _controlla(ra, dec)
            return float(ra), float(dec), descr
        errori.append(f"  {etichetta}: nome sconosciuto")
        log(f"  {etichetta}: nome sconosciuto")
    raise ValueError(f"Non trovo le coordinate di «{testo}».\n\nTentativi:\n" + "\n".join(errori) +
                     "\n\nProva con un nome di catalogo (M 16, NGC 6611), con il link della voce di "
                     "Wikipedia, oppure incolla le coordinate nel campo «Coordinate».")


def leggi_lista_da_cercare():
    """Oggetti messi da parte (anche da Atlante Celeste): lista di dict nome, ra, dec, tipo."""
    try:
        d = json.loads(FILE_LISTA_DA_CERCARE.read_text(encoding="utf-8"))
        return [x for x in d if isinstance(x, dict) and x.get("nome")] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def scrivi_lista_da_cercare(lista):
    try:
        tmp = FILE_LISTA_DA_CERCARE.with_suffix(".tmp")
        tmp.write_text(json.dumps(lista, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FILE_LISTA_DA_CERCARE)
    except OSError:
        pass


def verifica_wcs(percorso, ra, dec):
    """Dopo il download: l'oggetto cade nel campo del file? (WCS dell'header)
    Restituisce 'sì', 'no' oppure un breve motivo se non verificabile."""
    try:
        from astropy.io import fits
        from astropy.wcs import WCS
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        import warnings
        coord = SkyCoord(ra * u.deg, dec * u.deg)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with fits.open(percorso, memmap=True, lazy_load_hdus=True) as hdul:
                for hdu in hdul:
                    h = hdu.header
                    if h.get("NAXIS", 0) < 2 or "CTYPE1" not in h:
                        continue
                    w = WCS(h, fobj=hdul, naxis=2)
                    if not w.has_celestial:
                        continue
                    return "sì" if w.celestial.footprint_contains(coord) else "no"
        return "WCS assente"
    except Exception as e:
        return f"non verificabile ({type(e).__name__})"


# =============================================================================
# MOSAICI HLSP CONSIGLIATI (novità della 1.4)
# Mosaici di livello 4 (High Level Science Products) preparati dai gruppi di
# ricerca: tanti oggetti in un campo, tre filtri per un'immagine a colori con
# AutoAstroPipe. Su MAST si cercano per "provenance name" (es. FRONTIER).
# =============================================================================
CARTELLA_MOSAICI = "MOSAICI_HLSP"       # usata fino alla 1.4: <cartella dati>/MOSAICI_HLSP/Mosaico_<nome>/
RAGGIO_MOSAICO_DEG = 0.02               # cono di ricerca attorno al centro del campo
MAX_OSSERVAZIONI_MOSAICO = 500

SCALE_HST = ["60mas", "30mas"]
SCALE_JWST = ["40mas", "20mas", "30mas"]


def _parte(provenienze, telescopio, filtri, scale=(), preferisci=()):
    """Una parte di una ricetta: da quale HLSP prendere quali filtri."""
    if isinstance(provenienze, str):
        provenienze = [provenienze]
    return {"provenienze": list(provenienze), "telescopio": telescopio,
            "filtri": [f.lower() for f in filtri], "scale": list(scale), "preferisci": list(preferisci)}


def _ricetta(gruppo, nome, cosa, ra, dec, parti, raggio=RAGGIO_MOSAICO_DEG, nota=""):
    return {"gruppo": gruppo, "nome": nome, "cosa": cosa, "ra": ra, "dec": dec,
            "raggio": raggio, "parti": parti, "nota": nota}


_HFF = ["hlsp_frontier", "FRONTIER", "HFF"]
_GLASS = ["GLASS-JWST", "GLASS-JWST-ERS", "GLASS"]
_LEGUS = ["hlsp_legus", "LEGUS"]
_PH_JWST = ["PHANGS-JWST"]
_F_HFF = ["F435W", "F606W", "F814W"]
_F_LEGUS = ["F435W|F438W", "F555W", "F814W"]       # «|» = vanno bene entrambi (ACS o WFC3)
_F_PH_MIRI = ["F770W", "F1130W", "F2100W"]
SOGLIA_SENZA_SPUNTA = 4 * 1024**3                    # file più grandi: elencati ma non spuntati

# Nomi dei progetti (provenance) verificati dalla diagnostica dell'8/11/2026 dalla rete di Edoardo:
# FRONTIER si chiama «hlsp_frontier»; HLF, PHANGS-HST e UNCOVER non hanno osservazioni su MAST
# (sostituiti da CANDELS, LEGUS e GLASS-JWST).
RICETTE_MOSAICI = [
    # ---------------------------------------------------------------- Hubble
    _ricetta("Hubble", "HFF Abell 2744", "Ammasso di Pandora: galassie e archi gravitazionali", 3.5875, -30.3972,
             [_parte(_HFF, "HST", _F_HFF, SCALE_HST, ["clu"])]),
    _ricetta("Hubble", "HFF MACS J0416", "Ammasso con tanti archi di lente gravitazionale", 64.0381, -24.0675,
             [_parte(_HFF, "HST", _F_HFF, SCALE_HST, ["clu"])]),
    _ricetta("Hubble", "HFF MACS J1149", "Ammasso della supernova Refsdal, vista quattro volte", 177.3988, 22.3985,
             [_parte(_HFF, "HST", _F_HFF, SCALE_HST, ["clu"])]),
    _ricetta("Hubble", "XDF", "eXtreme Deep Field: migliaia di galassie lontanissime", 53.1625, -27.7914,
             [_parte("XDF", "HST", ["F435W", "F606W", "F850LP"], SCALE_HST)]),
    _ricetta("Hubble", "CANDELS GOODS-S", "Campo profondo GOODS Sud, dal visibile all'infrarosso", 53.122, -27.805,
             [_parte(["hlsp_candels", "CANDELS"], "HST", ["F606W", "F814W", "F160W"], SCALE_HST, ["gs", "goods-s"])]),
    _ricetta("Hubble", "LEGUS NGC 628", "Galassia a spirale M 74 vista di faccia, ammassi stellari", 24.174, 15.784,
             [_parte(_LEGUS, "HST", _F_LEGUS, (), ["ngc628", "ngc0628"])]),
    _ricetta("Hubble", "LEGUS NGC 1566", "Spirale con nucleo attivo, bracci molto netti", 65.002, -54.938,
             [_parte(_LEGUS, "HST", _F_LEGUS, (), ["ngc1566"])]),
    # ---------------------------------------------------------------- JWST
    _ricetta("JWST", "GLASS-JWST Abell 2744", "L'ammasso di Pandora nell'infrarosso (file da ~2 GB)", 3.5875, -30.3972,
             [_parte(_GLASS, "JWST", ["F115W", "F200W", "F444W"], SCALE_JWST)]),
    _ricetta("JWST", "PHANGS-JWST NGC 628", "Polveri e gas di M 74 nel medio infrarosso (MIRI)", 24.174, 15.784,
             [_parte(_PH_JWST, "JWST", _F_PH_MIRI, (), ["ngc628", "ngc0628"])]),
    _ricetta("JWST", "PHANGS-JWST NGC 1566", "Filamenti di polvere nel medio infrarosso (MIRI)", 65.002, -54.938,
             [_parte(_PH_JWST, "JWST", _F_PH_MIRI, (), ["ngc1566"])]),
    _ricetta("JWST", "CEERS", "Campo profondo nell'Orsa Maggiore (file da ~1,2 GB)", 214.86, 52.88,
             [_parte("CEERS", "JWST", ["F115W", "F200W", "F444W"], SCALE_JWST)], raggio=0.15),
    _ricetta("JWST", "JADES GOODS-S", "Il campo più profondo di JWST (file da ~10 GB, non spuntati)", 53.16, -27.80,
             [_parte("JADES", "JWST", ["F090W", "F200W", "F444W"], SCALE_JWST)]),
    # ---------------------------------------------------------------- insieme
    _ricetta("Hubble + JWST", "Abell 2744 Hubble + JWST", "Blu e verde da Hubble, rosso da JWST", 3.5875, -30.3972,
             [_parte(_HFF, "HST", ["F435W", "F814W"], SCALE_HST, ["clu"]),
              _parte(_GLASS, "JWST", ["F444W"], SCALE_JWST)],
             nota="Scale diverse: AutoAstroPipe riproietta tutto sulla stessa griglia."),
    # ---------------------------------------------------------------- Roman
    _ricetta("Roman", "Roman: dati pubblici", "Immagini di Roman su MAST (formato ASDF, non FITS)", None, None,
             [], nota="Elencate senza spunta."),
]

AVVERTENZE_MOSAICI = (
    "• Alcuni mosaici sono enormi: GLASS ~2 GB e CEERS ~1,2 GB per filtro, JADES ~10 GB per filtro. "
    "I file oltre 4 GB vengono elencati senza spunta: spuntali tu se hai spazio e tempo. "
    "Per Hubble il programma sceglie prima 60 mas; in AutoAstroPipe si può ridurre la griglia.\n"
    "• I filtri ultravioletti di LEGUS (F275W, F336W) sono rumorosi: per i colori si usano F435W/F438W·F555W·F814W.\n"
    "• Livello 4 = prodotti dei gruppi di ricerca: più curati ma con nomi e formati meno uniformi. "
    "Se manca un filtro il registro lo dice.\n"
    "• Roman: le immagini sono in formato ASDF, che AutoAstroPipe per ora non legge.\n"
    "• I file finiscono in <cartella dati>/Mosaico_<nome>/<TELESCOPIO>/: in AutoAstroPipe scegli come cartella madre "
    "Mosaico_<nome> e lascia che copi i FITS dalle sottocartelle."
)

ESCLUSI_MOSAICO = {"wht", "exp", "rms", "err", "ctx", "seg", "bkg", "psf", "var", "mask", "cat", "weight",
                   "flag", "unc", "ivm", "cov", "model", "resid"}
POSITIVI_MOSAICO = ("drz", "drc", "sci", "i2d", "img")
_STRUMENTI_RE = re.compile(r"acswfc|acs|wfc3|uvis|wfpc2|nircam|niriss|miri|wfc|ir")


def filtri_bgr(ricetta):
    """'F435W · F606W · F814W' (dal blu al rosso)."""
    fl = [f.upper().replace("|", "/") for p in ricetta["parti"] for f in p["filtri"]]
    return " · ".join(fl) if fl else "—"


def _base_nome(nome):
    n = str(nome).lower()
    for est in (".fits.gz", ".fits", ".fit.gz", ".fit"):
        if n.endswith(est):
            return n[: -len(est)]
    return None


def _trova_parola(parola, testo):
    return re.search(r"(?<![a-z0-9])" + re.escape(parola) + r"(?![a-z0-9])", testo) is not None


def file_mosaico_valido(nome):
    """True se il nome è un'immagine FITS di scienza (non pesi, errori, maschere...)."""
    base = _base_nome(nome)
    if base is None:
        return False
    pezzi = base.split("_")[-1].split("-")
    if pezzi[-1] in ESCLUSI_MOSAICO:
        return False
    if pezzi[-1] in POSITIVI_MOSAICO:
        return True          # es. PHANGS «exp-drc-sci»: conta l'ultima parola
    return not any(t in ESCLUSI_MOSAICO for t in re.split(r"[_\-.]", base))


def _versione(base):
    m = re.findall(r"(?<![a-z])(?:v|dr)(\d+(?:[p.]\d+)*)", base)
    if not m:
        return ()
    return tuple(int(x) for x in re.split(r"[p.]", m[-1]) if x.isdigit())


def _punteggio(base, scale, preferisci):
    """Più piccolo = migliore: scala nell'ordine della ricetta, parole preferite,
    tipo drz/drc/sci/i2d, versione più recente, nome più corto."""
    rs = len(scale)
    for k, s in enumerate(scale):
        if re.search(r"(?<!\d)" + re.escape(s.lower()), base):
            rs = k
            break
    pref = 0 if not preferisci or any(p.lower() in base for p in preferisci) else 1
    tipo = 0 if any(_trova_parola(p, base) for p in POSITIVI_MOSAICO) else 1
    ver = tuple(-x for x in _versione(base))
    return (rs, pref, tipo, ver, len(base))


def _chiave_gruppo(base, filtri):
    """Nome senza filtro, strumento, scala e versione: file con la stessa chiave
    appartengono allo stesso campo (utile se ci sono più puntamenti, es. CEERS)."""
    k = base
    for f in (a for spec in filtri for a in spec.split("|")):
        k = re.sub(r"(?<![a-z0-9])" + re.escape(f) + r"(?![a-z0-9])", "{F}", k)
    k = re.sub(r"(?<!\d)\d+mas", "", k)
    k = re.sub(r"(?<![a-z])(?:v|dr)\d+(?:[p.]\d+)*", "", k)
    return _STRUMENTI_RE.sub("", k)


def scegli_file(prodotti, filtri, scale=(), preferisci=()):
    """prodotti: lista di dict con almeno 'nome' (e di solito 'uri', 'size').
    Un filtro può avere alternative separate da «|» (es. "f435w|f438w").
    Restituisce {filtro: prodotto scelto o None}. Funziona senza rete."""
    filtri = [f.lower() for f in filtri]
    candidati = {f: [] for f in filtri}
    for p in prodotti:
        if not file_mosaico_valido(p.get("nome", "")):
            continue
        base = _base_nome(p["nome"])
        for f in filtri:
            if any(_trova_parola(a, base) for a in f.split("|")):
                candidati[f].append((_punteggio(base, scale, preferisci), base, p))
    # campo (gruppo) che ha più filtri, poi quello col file migliore
    gruppi = {}
    for f, lista in candidati.items():
        for punt, base, p in lista:
            g = gruppi.setdefault(_chiave_gruppo(base, filtri), {"filtri": set(), "migliore": punt})
            g["filtri"].add(f)
            g["migliore"] = min(g["migliore"], punt)
    scelto_gruppo = min(gruppi, key=lambda k: (-len(gruppi[k]["filtri"]), gruppi[k]["migliore"])) if gruppi else None
    scelti = {}
    for f, lista in candidati.items():
        if not lista:
            scelti[f] = None
            continue
        nel_gruppo = [c for c in lista if _chiave_gruppo(c[1], filtri) == scelto_gruppo]
        scelti[f] = min(nel_gruppo or lista, key=lambda c: c[0])[2]
    return scelti


def _prodotti_da_tabella(tab):
    out = []
    for p in tab:
        uri = _val(p, "dataURI")
        nome = _val(p, "productFilename") or (str(uri).split("/")[-1] if uri else None)
        if not nome or not uri:
            continue
        out.append({"nome": str(nome), "uri": str(uri), "size": _val(p, "size"),
                    "obsid": str(_val(p, "obsID", "parent_obsid", predefinito=""))})
    return out


def provenienze_vicine(ra, dec, raggio_deg):
    """Nomi degli HLSP che coprono un punto (per la diagnostica e come ripiego)."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    from astroquery.mast import Observations
    obs = Observations.query_criteria(coordinates=SkyCoord(ra * u.deg, dec * u.deg), radius=f"{raggio_deg} deg",
                                      obs_collection="HLSP")
    conteggio = {}
    for r in obs:
        n = str(_val(r, "provenance_name", predefinito="?"))
        conteggio[n] = conteggio.get(n, 0) + 1
    return obs, conteggio


def _osservazioni_parte(parte, coord, raggio_deg, log, cache):
    from astroquery.mast import Observations
    for prov in parte["provenienze"]:
        obs = Observations.query_criteria(provenance_name=prov, coordinates=coord, radius=f"{raggio_deg} deg")
        if len(obs):
            log(f"  {prov}: {len(obs)} osservazioni HLSP nel campo")
            return obs, prov
    # ripiego: tutti gli HLSP del campo, tenendo quelli col nome simile
    if "hlsp" not in cache:
        cache["hlsp"] = provenienze_vicine(coord.ra.deg, coord.dec.deg, raggio_deg)
    obs, conteggio = cache["hlsp"]
    chiavi = [re.sub(r"[^a-z0-9]", "", p.lower()) for p in parte["provenienze"]]
    tieni = [any(c and c in re.sub(r"[^a-z0-9]", "", str(_val(r, "provenance_name", predefinito="")).lower())
                 for c in chiavi) for r in obs]
    if len(obs) and any(tieni):
        obs = obs[np.array(tieni, dtype=bool)]
        prov = str(_val(obs[0], "provenance_name"))
        log(f"  {parte['provenienze'][0]}: trovato col nome «{prov}» ({len(obs)} osservazioni)")
        return obs, prov
    log(f"  {parte['provenienze'][0]}: nessuna osservazione. HLSP presenti qui: "
        + (", ".join(f"{k} ({v})" for k, v in sorted(conteggio.items())) or "nessuno"))
    return None, parte["provenienze"][0]


def trova_file_ricetta(ricetta, log=print):
    """Interroga MAST per una ricetta. Restituisce (righe per la tabella, filtri mancanti)."""
    if ricetta["ra"] is None:
        return trova_file_roman(log)
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    from astroquery.mast import Observations
    coord = SkyCoord(ricetta["ra"] * u.deg, ricetta["dec"] * u.deg)
    righe, mancanti, cache = [], [], {}
    for parte in ricetta["parti"]:
        log(f"MAST: {ricetta['nome']}, {parte['telescopio']} {' '.join(f.upper() for f in parte['filtri'])}...")
        obs, prov = _osservazioni_parte(parte, coord, ricetta["raggio"], log, cache)
        if obs is None:
            mancanti.extend(f.upper() for f in parte["filtri"])
            continue
        if len(obs) > MAX_OSSERVAZIONI_MOSAICO:
            obs = obs[:MAX_OSSERVAZIONI_MOSAICO]
        strumenti = {str(_val(r, "obsid")): str(_val(r, "instrument_name", predefinito="")) for r in obs}
        url_oss = {str(_val(r, "obsid")): _val(r, "dataURL") for r in obs}
        ids = list(strumenti)
        blocchi = [ids[i:i + 50] for i in range(0, len(ids), 50)]
        prodotti = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for fut in as_completed([ex.submit(Observations.get_product_list, b) for b in blocchi]):
                try:
                    prodotti.extend(_prodotti_da_tabella(fut.result()))
                except Exception as e:
                    log(f"  elenco dei file non disponibile per un blocco ({e})")
        log(f"  {len(prodotti)} file nell'elenco, scelgo un mosaico per filtro")
        scelti = scegli_file(prodotti, parte["filtri"], parte["scale"], parte["preferisci"])
        for spec, p in scelti.items():
            if p is None:
                mancanti.append(spec.upper().replace("|", "/"))
                log(f"  manca {spec.upper().replace('|', '/')} in {prov}")
                continue
            base = _base_nome(p["nome"])
            f = next((a for a in spec.split("|") if _trova_parola(a, base)), spec.split("|")[0])
            dim = int(p["size"]) if p.get("size") is not None else None
            spunta = not (dim and dim > SOGLIA_SENZA_SPUNTA)
            candidati = url_candidati(p["uri"], p["nome"], url_oss.get(p["obsid"]))
            if len(candidati) > 1:          # HLSP vecchio: si prova quale indirizzo manda davvero il FITS
                buono, risposte = None, []
                ordine = list(range(len(candidati)))
                k_noto = cache.get("indice_buono_" + prov)
                if k_noto is not None and k_noto < len(ordine):      # lo schema che ha funzionato prima
                    ordine.remove(k_noto)
                    ordine.insert(0, k_noto)
                for k_u in ordine:
                    u = candidati[k_u]
                    ok, descr = prova_url(u, timeout=20)
                    risposte.append((u, descr))
                    if ok:
                        buono = u
                        cache["indice_buono_" + prov] = k_u
                        break
                if buono:
                    log(f"  {f.upper()}: file trovato in {buono[:140]}")
                    candidati = [buono] + [u for u in candidati if u != buono]
                else:
                    log(f"  {f.upper()}: nessun indirizzo ha risposto con un FITS; li riprovo al download")
                    if not cache.get("dettagli_" + prov):
                        cache["dettagli_" + prov] = True
                        for u, descr in risposte:
                            log(f"      {u[:120]}\n        -> {descr}")
            righe.append(_risultato(
                archivio="MAST", telescopio=parte["telescopio"], cartella_telescopio=parte["telescopio"],
                tipo="Spaziale", strumento=strumenti.get(p["obsid"], "") or prov, filtro=f.upper(), livello="4",
                dimensione=dim, ra_centro=ricetta["ra"], dec_centro=ricetta["dec"], target=prov,
                id_oss=p["obsid"], nome_file=nome_file_sicuro(p["nome"]), contiene=True, distanza=0.0,
                selezionato=spunta, url=candidati[0], url_alternativi=candidati[1:]))
            log(f"  {f.upper()}: {p['nome']} ({formatta_dimensione(dim)})")
            if not spunta:
                log(f"  attenzione: {p['nome']} supera 4 GB: elencato senza spunta")
            elif dim and dim > 2 * 1024**3:
                log(f"  attenzione: {p['nome']} supera 2 GB")
    return righe, mancanti


def trova_file_roman(log=print):
    """Immagini pubbliche di Roman su MAST (livello 3-4): elencate senza spunta."""
    from astroquery.mast import Observations
    log("MAST: immagini pubbliche di Roman (livelli 3 e 4)...")
    try:
        n = Observations.query_criteria_count(obs_collection="ROMAN", dataproduct_type="image", calib_level=[3, 4])
    except Exception as e:
        log(f"  Roman: MAST non ha ancora la collezione o non risponde ({type(e).__name__}: {str(e)[:120]})")
        return [], []
    if not n:
        log("  Roman: nessun dato pubblico per ora (il telescopio è in collaudo). Riprova fra qualche mese.")
        return [], []
    obs = Observations.query_criteria(obs_collection="ROMAN", dataproduct_type="image", calib_level=[3, 4])
    obs = obs[:MAX_RISULTATI_ARCHIVIO]
    righe = []
    for r in obs:
        uri = _val(r, "dataURL")
        if not uri or str(_val(r, "dataRights", predefinito="PUBLIC")).upper() != "PUBLIC":
            continue
        righe.append(_risultato(
            archivio="MAST", telescopio="Roman", cartella_telescopio="ROMAN", tipo="Spaziale",
            strumento=str(_val(r, "instrument_name", predefinito="")), filtro=str(_val(r, "filters", predefinito="")),
            livello=str(_val(r, "calib_level", predefinito="")), ra_centro=_val(r, "s_ra"), dec_centro=_val(r, "s_dec"),
            data=mjd_in_data(_val(r, "t_min")), id_oss=str(_val(r, "obs_id", predefinito="")),
            target=str(_val(r, "target_name", predefinito="")), nome_file=nome_file_sicuro(str(uri).split("/")[-1]),
            selezionato=False, url=URL_MAST_DOWNLOAD + urllib.parse.quote(str(uri), safe=":/")))
    log(f"  Roman: {len(righe)} immagini pubbliche (al massimo {MAX_RISULTATI_ARCHIVIO}), elencate senza spunta")
    if any(r["nome_file"].lower().endswith(".asdf") for r in righe):
        log("  Roman: i file sono in formato ASDF, non FITS: AutoAstroPipe per ora non li legge")
    return righe, []


def nome_cartella_mosaico(ricetta):
    return "Mosaico_" + nome_cartella_oggetto(ricetta["nome"].replace("+", " "))


# =============================================================================
# DOWNLOAD
# =============================================================================
def destinazione(base, r, cartella_oggetto):
    """<cartella dati>/<OGGETTO>/<TELESCOPIO>/<file> (dalla 1.5)."""
    cart_ogg = cartella_esistente(base, cartella_oggetto)
    cart_tel = cartella_esistente(cart_ogg, r["cartella_telescopio"] or "ALTRO")
    return cart_tel / r["nome_file"]


def destinazioni_vecchie(base, r, cartella_oggetto):
    """Dove la 1.4 e le precedenti salvavano lo stesso file."""
    base = Path(base)
    tel = r["cartella_telescopio"] or "ALTRO"
    out = [cartella_esistente(cartella_esistente(base, tel), cartella_oggetto) / r["nome_file"]]
    if str(cartella_oggetto).startswith("Mosaico_"):
        out.append(cartella_esistente(cartella_esistente(base, CARTELLA_MOSAICI), cartella_oggetto) / r["nome_file"])
    return out


def sposta_da_vecchia_cartella(base, r, cartella_oggetto):
    """Se il file (o il suo .part) è già stato scaricato nella struttura vecchia
    <TELESCOPIO>/<OGGETTO>/, lo sposta in <OGGETTO>/<TELESCOPIO>/ invece di
    riscaricarlo. Restituisce il percorso vecchio se ha spostato qualcosa."""
    nuovo = destinazione(base, r, cartella_oggetto)
    if nuovo.exists():
        return None
    for vecchio in destinazioni_vecchie(base, r, cartella_oggetto):
        for suffisso in ("", ".part"):
            v = vecchio.with_name(vecchio.name + suffisso)
            if v.is_file() and v != nuovo.with_name(nuovo.name + suffisso):
                nuovo.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(v), str(nuovo.with_name(nuovo.name + suffisso)))
                for cartella in (v.parent, v.parent.parent):      # toglie le cartelle vecchie rimaste vuote
                    try:
                        cartella.rmdir()
                    except OSError:
                        pass
                return v
    return None


def scarica_file(url, dest, dimensione_attesa, ferma, progresso):
    """Scarica con ripresa: i dati arrivano in '<file>.part', che viene
    rinominato solo a download completato. Se il .part esiste già si
    riprende dal punto in cui si era interrotto (header HTTP Range)."""
    dest = Path(dest)
    if dest.exists() and (not dimensione_attesa or dest.stat().st_size >= dimensione_attesa):
        if firma_valida(dest, dest.name):
            return "già presente"
        dest.unlink()                    # c'era un messaggio del server al posto del file: si riscarica
    dest.parent.mkdir(parents=True, exist_ok=True)
    parziale = dest.with_name(dest.name + ".part")
    gia = parziale.stat().st_size if parziale.exists() else 0
    intestazioni = {"User-Agent": f"{NOME_PROGRAMMA}/1.0 (Python)"}
    if gia:
        intestazioni["Range"] = f"bytes={gia}-"
    req = urllib.request.Request(url, headers=intestazioni)
    try:
        risposta = urllib.request.urlopen(req, timeout=TIMEOUT_RETE)
    except urllib.error.HTTPError as e:
        if e.code == 416 and gia:                     # il .part era già completo
            os.replace(parziale, dest)
            return "completato"
        raise
    with risposta:
        if gia and risposta.status != 206:            # il server non supporta la ripresa
            gia = 0
        totale = risposta.headers.get("Content-Length")
        totale = int(totale) + gia if totale and totale.isdigit() else dimensione_attesa
        with open(parziale, "ab" if gia else "wb") as f:
            ricevuti = gia
            while True:
                if ferma.is_set():
                    return "interrotto"
                blocco = risposta.read(1024 * 256)
                if not blocco:
                    break
                f.write(blocco)
                ricevuti += len(blocco)
                progresso(ricevuti, totale)
    if dimensione_attesa and ricevuti < 0.98 * dimensione_attesa:
        raise IOError(f"arrivati {formatta_dimensione(ricevuti)} su {formatta_dimensione(dimensione_attesa)}: "
                      "il .part resta e il prossimo tentativo riprende da lì")
    controlla_contenuto(parziale, dest.name)
    os.replace(parziale, dest)
    return "completato"


class ContenutoSbagliato(Exception):
    """Il server ha risposto con un messaggio (errore, pagina web) invece del file."""


FIRME_FILE = {  # estensione -> inizi possibili del file
    (".fits", ".fit", ".fts", ".fits.gz", ".fit.gz"): (b"SIMPLE", b"\x1f\x8b"),
    (".gz",): (b"\x1f\x8b",),
    (".asdf",): (b"#ASDF",),
    (".png",): (b"\x89PNG",),
    (".jpg", ".jpeg"): (b"\xff\xd8",),
}


def firma_valida(percorso, nome):
    """True se l'inizio del file corrisponde al suo tipo (FITS, gzip, ASDF, PNG,
    JPEG). I file di altro tipo (senza firma riconoscibile) vengono accettati."""
    nome = str(nome).lower()
    for estensioni, firme in FIRME_FILE.items():
        if nome.endswith(estensioni):
            with open(percorso, "rb") as f:
                inizio = f.read(16)
            return any(inizio.startswith(x) for x in firme)
    return True


def controlla_contenuto(percorso, nome):
    """Se il file scaricato non inizia come dovrebbe (es. un FITS senza 'SIMPLE'),
    il server ha mandato un messaggio d'errore al posto del file: lo cancella."""
    percorso = Path(percorso)
    n = percorso.stat().st_size
    if firma_valida(percorso, nome):
        return
    with open(percorso, "rb") as f:
        inizio = f.read(400)
    percorso.unlink()
    testo = inizio.decode("utf-8", "replace").strip().replace("\n", " ")[:120]
    raise ContenutoSbagliato(f"il server ha mandato {n} byte invece del file: «{testo}»")


def prova_url(url, timeout=30):
    """Legge i primi byte di un indirizzo. Restituisce (è un FITS?, descrizione breve
    della risposta) - la descrizione serve alla diagnostica."""
    req = urllib.request.Request(url, headers={**INTESTAZIONI_HTTP, "Range": "bytes=0-79"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            inizio = r.read(80)
            stato, tipo, finale = r.status, r.headers.get("Content-Type", ""), r.geturl()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}"
    if inizio.startswith(b"SIMPLE") or inizio.startswith(b"\x1f\x8b"):
        return True, "FITS"
    testo = inizio.decode("utf-8", "replace").replace("\n", " ").replace("\r", " ").strip()[:60]
    altrove = f" -> {finale[:70]}" if finale != url else ""
    return False, f"HTTP {stato} {tipo[:25]}{altrove}: «{testo}»"


def prova_url_fits(url, timeout=30):
    return prova_url(url, timeout)[0]


def _https(u):
    return "https://" + u.split("://", 1)[1] if u.startswith("http://") else u


def _indirizzi_statici_hlsp(nome_file):
    """Percorsi possibili nell'archivio statico di STScI per gli HLSP vecchi
    (https://archive.stsci.edu/pub/hlsp/<progetto>/... e /hlsps/...)."""
    n = nome_file.lower()
    m = re.match(r"hlsp_([a-z0-9\-]+)_", n)
    if not m:
        return []
    prog = m.group(1)
    sotto = []
    if prog == "frontier":
        mm = re.match(r"hlsp_frontier_hst_[a-z0-9]+-\d+mas(?:-selfcal)?_([a-z0-9]+)", n)
        mv = re.search(r"_(v\d+\.\d+(?:-epoch\d)?)_", n)
        if mm and mv:
            ver = [mv.group(1)] if "epoch" in mv.group(1) else [mv.group(1) + "-epoch2", mv.group(1) + "-epoch1", mv.group(1)]
            sotto += [f"frontier/{mm.group(1)}/images/hst/{v}" for v in ver]
    elif prog == "candels":
        mm = re.search(r"_(gs|gn|cos|egs|uds)-([a-z0-9]+)_", n)
        mv = re.search(r"_(v\d+\.\d+)_", n)
        campi = {"gs": "goods-s", "gn": "goods-n", "cos": "cosmos", "egs": "egs", "uds": "uds"}
        if mm and mv:
            sotto += [f"candels/{campi[mm.group(1)]}/{mm.group(1)}-{mm.group(2)}/{mv.group(1)}"]
    elif prog == "legus":
        mm = re.match(r"hlsp_legus_hst_[a-z0-9\-]+_([a-z0-9\-]+?)_f\d", n)
        if mm:
            sotto += [f"legus/{mm.group(1)}", f"legus/{mm.group(1).split('-')[0]}"]
    sotto.append(prog)
    out = []
    for radice in ("https://archive.stsci.edu/hlsps/", "https://archive.stsci.edu/pub/hlsp/"):
        out += [f"{radice}{x}/{nome_file}" for x in sotto]
    return out


def url_candidati(uri, nome_file, url_osservazione=None):
    """Indirizzi possibili per un prodotto MAST, dal più probabile.
    Gli HLSP vecchi (Frontier Fields, LEGUS, CANDELS...) hanno un dataURI
    «mast:HLSP/url/cgi-bin/getdata.cgi?dataset=...» che il servizio Download di
    MAST non sa servire (risponde con un errore di pochi byte). Il dataURL
    dell'osservazione punta invece all'Hubble Legacy Archive:
    http://hla.stsci.edu/cgi-bin/getdata.cgi?dataset=... (diagnostica dell'8/11)."""
    uri = str(uri)
    mast = URL_MAST_DOWNLOAD + urllib.parse.quote(uri, safe=":/")
    if not uri.startswith("mast:HLSP/url/"):
        if uri.startswith("mast:HLSP/"):      # riserva: l'archivio statico degli HLSP
            return [mast, "https://archive.stsci.edu/hlsps/" + uri[len("mast:HLSP/"):]]
        return [mast]
    resto = uri[len("mast:HLSP/url/"):]                     # cgi-bin/getdata.cgi?dataset=...
    m = re.search(r"dataset=([^&]+)", resto)
    dataset = urllib.parse.unquote(m.group(1)) if m else _base_nome(nome_file) or nome_file
    out = []
    u = str(url_osservazione or "")
    if u.startswith("http"):
        out += [_https(u), u]
    for host in ("https://hla.stsci.edu/", "http://hla.stsci.edu/"):
        out += [host + resto,
                f"{host}cgi-bin/getdata.cgi?dataset={dataset}&filename={nome_file}",
                f"{host}cgi-bin/getdata.cgi?download=1&filename={nome_file}"]
    out += _indirizzi_statici_hlsp(nome_file)
    out += ["https://archive.stsci.edu/" + resto, mast]
    return list(dict.fromkeys(out))


def _sh_quote(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def scrivi_script_download(cartella, base, righe, cartella_oggetto, nome_base):
    """Scrive .sh (Linux/macOS) e .bat (Windows) rieseguibili con curl."""
    cartella = Path(cartella)
    cartella.mkdir(parents=True, exist_ok=True)
    base = Path(base)
    voci = []
    for r in righe:
        rel = destinazione(base, r, cartella_oggetto).relative_to(base)
        voci.append((r["url"], rel, r["dimensione"] if r["dimensione_esatta"] else 0))

    sh = cartella / f"{nome_base}.sh"
    with open(sh, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write(f"# Download generato da '{NOME_PROGRAMMA}' il {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write("# Si può rilanciare quante volte serve: salta i file completi e riprende\n")
        f.write("# quelli interrotti (curl -C -). Uso:  bash " + sh.name + "\n\n")
        f.write(f"BASE={_sh_quote(base.as_posix())}\n")
        f.write('ERRORI="$(dirname "$0")/errori_download.txt"\n\n')
        f.write("scarica() {\n"
                "    local url=\"$1\" dest=\"$BASE/$2\" attesa=\"$3\"\n"
                "    if [ -f \"$dest\" ]; then\n"
                "        if [ \"$attesa\" -eq 0 ] || [ \"$(stat -c%s \"$dest\" 2>/dev/null || stat -f%z \"$dest\")\" -ge \"$attesa\" ]; then\n"
                "            echo \"già presente: $2\"; return 0\n"
                "        fi\n"
                "    fi\n"
                "    mkdir -p \"$(dirname \"$dest\")\"\n"
                "    echo \"scarico: $2\"\n"
                "    if curl -L --fail --retry 5 --retry-delay 10 -C - -o \"$dest.part\" \"$url\"; then\n"
                "        mv -f \"$dest.part\" \"$dest\"\n"
                "    else\n"
                "        echo \"ERRORE: $2  <-  $url\" | tee -a \"$ERRORI\"\n"
                "    fi\n"
                "}\n\n")
        for url, rel, attesa in voci:
            f.write(f"scarica {_sh_quote(url)} {_sh_quote(rel.as_posix())} {int(attesa or 0)}\n")
        f.write("\necho \"Fine. Eventuali errori sono in $ERRORI\"\n")
    try:
        sh.chmod(0o755)
    except OSError:
        pass

    bat = cartella / f"{nome_base}.bat"
    esc = lambda s: str(s).replace("%", "%%")            # per le righe "set"
    esc_call = lambda s: str(s).replace("%", "%%%%")     # "call" espande due volte i %
    with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("@echo off\nchcp 65001 >nul\nsetlocal\n")
        f.write(f"REM Download generato da '{NOME_PROGRAMMA}' il {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write("REM Si puo' rilanciare: salta i file completi e riprende quelli interrotti.\n")
        f.write(f'set "BASE={esc(base)}"\n')
        f.write('set "ERRORI=%~dp0errori_download.txt"\n\n')
        for url, rel, attesa in voci:
            f.write(f'call :scarica "{esc_call(url)}" "{esc_call(str(rel).replace("/", chr(92)))}" {int(attesa or 0)}\n')
        f.write("echo Fine. Eventuali errori sono in %ERRORI%\npause\ngoto :eof\n\n")
        f.write(":scarica\n"
                "set \"DEST=%BASE%\\%~2\"\n"
                "if exist \"%DEST%\" (\n"
                "    if \"%~3\"==\"0\" (echo gia' presente: %~2& goto :eof)\n"
                "    for %%A in (\"%DEST%\") do if %%~zA GEQ %~3 (echo gia' presente: %~2& goto :eof)\n"
                ")\n"
                "for %%D in (\"%DEST%\") do if not exist \"%%~dpD\" mkdir \"%%~dpD\"\n"
                "echo scarico: %~2\n"
                "curl.exe -L --fail --retry 5 --retry-delay 10 -C - -o \"%DEST%.part\" \"%~1\"\n"
                "if errorlevel 1 (echo ERRORE: %~2 ^<- \"%~1\">>\"%ERRORI%\"& goto :eof)\n"
                "move /y \"%DEST%.part\" \"%DEST%\" >nul\n"
                "goto :eof\n")
    return sh, bat


def scrivi_csv(percorso, risultati, nome_oggetto, ra, dec, base, cartella_oggetto):
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    with open(percorso, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLONNE_CSV, delimiter=";")
        w.writeheader()
        for r in risultati:
            contiene = {True: "sì", False: "no", None: "?"}[r["contiene"]]
            w.writerow({
                "oggetto": nome_oggetto, "ra_oggetto_deg": f"{ra:.6f}" if ra is not None else "",
                "dec_oggetto_deg": f"{dec:.6f}" if dec is not None else "",
                "archivio": r["archivio"], "telescopio": r["telescopio"], "tipo": r["tipo"],
                "strumento": r["strumento"], "filtro": r["filtro"], "livello": r["livello"],
                "dimensione_MB": f"{r['dimensione'] / 1024**2:.2f}" if r["dimensione"] else "",
                "dimensione_esatta": "sì" if r["dimensione_esatta"] else "stima",
                "contiene_oggetto": contiene,
                "distanza_arcmin": f"{r['distanza']:.2f}" if r["distanza"] is not None else "",
                "data_osservazione": r["data"],
                "esposizione_s": f"{float(r['esposizione']):.1f}" if r["esposizione"] is not None else "",
                "id_osservazione": r["id_oss"], "target_archivio": r["target"],
                "nome_file": r["nome_file"],
                "cartella_destinazione": str(destinazione(base, r, cartella_oggetto).parent),
                "url": r["url"], "verificato_wcs": r["verificato_wcs"]})


# =============================================================================
# IMPOSTAZIONI
# =============================================================================
def carica_config():
    try:
        with open(FILE_CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def salva_config(c):
    try:
        with open(FILE_CONFIG, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def cartella_dati_predefinita():
    for p in (Path.home() / "Scrivania" / "Astronomia", Path.home() / "Desktop" / "Astronomia",
              Path.home() / "Astronomia"):
        if p.is_dir():
            return p
    return Path.home() / "Astronomia"


# =============================================================================
# FINESTRA
# =============================================================================
class App(tk.Tk):
    FONT_DI_SISTEMA = ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont",
                       "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                       "TkIconFont", "TkTooltipFont")

    def __init__(self):
        super().__init__(className="RicercaArchiviFITS")    # nome della finestra per il dock di Ubuntu
        try:
            self._icona = tk.PhotoImage(file=str(percorso_dati(FILE_ICONA)))
            self.iconphoto(True, self._icona)                 # vale anche per la finestra dei mosaici
        except (tk.TclError, OSError):
            pass
        self.title(f"{NOME_PROGRAMMA} {VERSIONE}")
        self.geometry("1366x800")
        self.minsize(900, 560)
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass
        self.cfg = carica_config()
        self.zoom = min(max(float(self.cfg.get("zoom", ZOOM_PREDEFINITO)), ZOOM_MIN), ZOOM_MAX)
        self.coda = queue.Queue()
        self.risultati = []          # tutti i risultati della ricerca corrente
        self.visibili = []           # indici mostrati in tabella
        self.oggetto = None          # dict: nome, cartella, ra, dec
        self.occupato = False
        self.ferma = threading.Event()
        self.ordine = ("distanza", False)

        self._prepara_stile()
        self._costruisci()
        self._applica_zoom()
        for t in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(t, lambda e: self._cambia_zoom(+ZOOM_PASSO))
        for t in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(t, lambda e: self._cambia_zoom(-ZOOM_PASSO))
        self.bind_all("<Control-0>", lambda e: self._cambia_zoom(None))
        self.protocol("WM_DELETE_WINDOW", self._chiudi)
        self.after(100, self._controlla_coda)
        self._aggiorna_stato_pulsanti()
        self._aggiorna_lista()
        self.bind("<FocusIn>", lambda e: self._aggiorna_lista() if e.widget is self else None)
        self._oggetto_iniziale()

    # -------------------------------------------------------------- stile/zoom
    def _prepara_stile(self):
        self._font_base = {}
        for nome in self.FONT_DI_SISTEMA:
            try:
                self._font_base[nome] = tkfont.nametofont(nome).cget("size") or 10
            except tk.TclError:
                pass
        fam = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self.font_titolo = tkfont.Font(family=fam, weight="bold")
        self.font_grassetto = tkfont.Font(family=fam, weight="bold")
        self.font_log = tkfont.Font(family=tkfont.nametofont("TkFixedFont").actual()["family"])
        self.style = ttk.Style(self)
        # il tema 'clam' permette barre di scorrimento larghe su Linux e su Windows
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.option_add("*TCombobox*Listbox.font", "TkDefaultFont")

    @staticmethod
    def _scala(dim, f):
        n = max(1, round(abs(dim) * f))
        return -n if dim < 0 else n

    def _applica_zoom(self):
        z = self.zoom
        for nome, base in self._font_base.items():
            tkfont.nametofont(nome).configure(size=self._scala(base, z))
        base = self._font_base.get("TkDefaultFont", 10)
        self.font_titolo.configure(size=self._scala(base, 1.25 * z))
        self.font_grassetto.configure(size=self._scala(base, 1.1 * z))
        self.font_log.configure(size=self._scala(self._font_base.get("TkFixedFont", base), 0.95 * z))
        p = lambda v: max(1, round(v * z))
        alt_riga = tkfont.nametofont("TkDefaultFont").metrics("linespace") + p(8)
        s = self.style
        s.configure("TButton", padding=(p(8), p(4)))
        s.configure("Grande.TButton", font=self.font_grassetto, padding=(p(14), p(8)))
        s.configure("Treeview", rowheight=alt_riga)
        s.configure("Treeview.Heading", font=self.font_grassetto, padding=(p(4), p(4)))
        s.configure("TCheckbutton", padding=p(2))
        s.configure("TEntry", padding=p(3))
        s.configure("TLabelframe.Label", font=self.font_grassetto)
        # barre di scorrimento grandi (spessore e frecce)
        spessore = p(22)
        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", arrowsize=spessore, width=spessore)
        s.configure("TProgressbar", thickness=p(16))
        larg_car = tkfont.nametofont("TkDefaultFont").measure("0")
        for chiave, _, larghezza, _ in COLONNE_TABELLA:
            self.tabella.column(chiave, width=max(30, larghezza * larg_car + p(10)))
        self.lbl_zoom.configure(text=f"Testo {round(z * 100)}%")

    def _cambia_zoom(self, delta):
        self.zoom = ZOOM_PREDEFINITO if delta is None else min(max(round(self.zoom + delta, 2), ZOOM_MIN), ZOOM_MAX)
        self._applica_zoom()
        self.cfg["zoom"] = self.zoom
        salva_config(self.cfg)
        return "break"

    # ------------------------------------------------------------------ layout
    def _costruisci(self):
        radice = ttk.Frame(self, padding=8)
        radice.pack(fill=tk.BOTH, expand=True)

        # --- riga 1: zoom e cartella dati
        r1 = ttk.Frame(radice)
        r1.pack(fill=tk.X)
        ttk.Button(r1, text="A−", width=4, command=lambda: self._cambia_zoom(-ZOOM_PASSO)).pack(side=tk.LEFT)
        ttk.Button(r1, text="A+", width=4, command=lambda: self._cambia_zoom(+ZOOM_PASSO)).pack(side=tk.LEFT, padx=(4, 0))
        self.lbl_zoom = ttk.Label(r1, text="")
        self.lbl_zoom.pack(side=tk.LEFT, padx=(8, 20))
        ttk.Label(r1, text="Cartella dati:", font=self.font_grassetto).pack(side=tk.LEFT)
        self.var_base = tk.StringVar(value=self.cfg.get("cartella_dati", str(cartella_dati_predefinita())))
        ttk.Entry(r1, textvariable=self.var_base).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(r1, text="📁 Scegli...", command=self._scegli_base).pack(side=tk.LEFT)
        ttk.Button(r1, text="Apri", command=self._apri_base).pack(side=tk.LEFT, padx=(4, 0))

        # --- riga 2: ricerca
        rc = ttk.LabelFrame(radice, text="Ricerca", padding=8)
        rc.pack(fill=tk.X, pady=8)
        ttk.Label(rc, text="Oggetto (nome, soprannome o link di Wikipedia):").grid(row=0, column=0, sticky="w")
        self.var_nome = tk.StringVar(value=self.cfg.get("ultimo_nome", ""))
        e_nome = ttk.Entry(rc, textvariable=self.var_nome, width=22)
        e_nome.grid(row=0, column=1, sticky="ew", padx=6)
        e_nome.bind("<Return>", lambda e: self._cerca())
        ttk.Label(rc, text="Coordinate (facoltative, anche da Wikipedia):").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.var_coord = tk.StringVar()
        e_coord = ttk.Entry(rc, textvariable=self.var_coord, width=36)
        e_coord.grid(row=0, column=3, sticky="ew", padx=6)
        e_coord.bind("<Return>", lambda e: self._cerca())
        ttk.Label(rc, text="Raggio (′):").grid(row=0, column=4, sticky="w", padx=(12, 0))
        self.var_raggio = tk.StringVar(value=str(self.cfg.get("raggio", RAGGIO_PREDEFINITO_ARCMIN)))
        ttk.Spinbox(rc, from_=0.1, to=60, increment=0.5, textvariable=self.var_raggio, width=6).grid(row=0, column=5, padx=6)
        self.btn_cerca = ttk.Button(rc, text="🔍  Cerca", style="Grande.TButton", command=self._cerca)
        self.btn_cerca.grid(row=0, column=6, rowspan=2, sticky="nsew", padx=(12, 0))
        self.btn_mosaici = ttk.Button(rc, text="🌌  Mosaici consigliati", style="Grande.TButton",
                                      command=self._apri_mosaici)
        self.btn_mosaici.grid(row=0, column=7, rowspan=2, sticky="nsew", padx=(8, 0))
        rc.columnconfigure(1, weight=1)
        rc.columnconfigure(3, weight=2)

        fr_arch = ttk.Frame(rc)
        fr_arch.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.var_archivi = {}
        scelti = self.cfg.get("archivi", {})
        for chiave, etichetta, attivo in ARCHIVI:
            v = tk.BooleanVar(value=scelti.get(chiave, attivo))
            self.var_archivi[chiave] = v
            ttk.Checkbutton(fr_arch, text=etichetta, variable=v).pack(side=tk.LEFT, padx=(0, 12))
        fr_opz = ttk.Frame(rc)
        fr_opz.grid(row=2, column=0, columnspan=7, sticky="w", pady=(4, 0))
        self.var_liv2 = tk.BooleanVar(value=self.cfg.get("livello2", False))
        ttk.Checkbutton(fr_opz, text="Includi anche il livello 2 (esposizioni singole; di base solo livello 3, già combinate e ottimizzate)",
                        variable=self.var_liv2).pack(side=tk.LEFT, padx=(0, 16))
        self.var_tutti = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr_opz, text="Mostra anche i file che NON contengono l'oggetto",
                        variable=self.var_tutti, command=self._riempi_tabella).pack(side=tk.LEFT)
        fr_lista = ttk.Frame(rc)
        fr_lista.grid(row=3, column=0, columnspan=7, sticky="ew", pady=(6, 0))
        ttk.Label(fr_lista, text="⭐ Da cercare:").pack(side=tk.LEFT)
        self.var_lista = tk.StringVar()
        self.cb_lista = ttk.Combobox(fr_lista, textvariable=self.var_lista, state="readonly", width=46)
        self.cb_lista.pack(side=tk.LEFT, padx=6)
        self.cb_lista.bind("<<ComboboxSelected>>", lambda e: self._usa_da_lista())
        ttk.Button(fr_lista, text="Rimuovi dalla lista", command=self._rimuovi_da_lista).pack(side=tk.LEFT)
        self.lbl_lista = ttk.Label(fr_lista, text="", foreground="#555")
        self.lbl_lista.pack(side=tk.LEFT, padx=8)

        self.lbl_oggetto = ttk.Label(radice, text="Scrivi un nome (es. NGC 346) o incolla le coordinate, poi Cerca.",
                                     font=self.font_titolo)
        self.lbl_oggetto.pack(fill=tk.X)
        self.lbl_archivi = ttk.Label(radice, text="", foreground="#444")
        self.lbl_archivi.pack(fill=tk.X, pady=(2, 4))

        # --- barra filtri sopra la tabella
        rf = ttk.Frame(radice)
        rf.pack(fill=tk.X)
        ttk.Button(rf, text="☑ Seleziona visibili", command=lambda: self._seleziona_tutti(True)).pack(side=tk.LEFT)
        ttk.Button(rf, text="☐ Deseleziona tutti", command=lambda: self._seleziona_tutti(False)).pack(side=tk.LEFT, padx=4)
        ttk.Label(rf, text="Telescopio:").pack(side=tk.LEFT, padx=(16, 4))
        self.var_filtro_tel = tk.StringVar(value="Tutti")
        self.cb_tel = ttk.Combobox(rf, textvariable=self.var_filtro_tel, state="readonly", width=16, values=["Tutti"])
        self.cb_tel.pack(side=tk.LEFT)
        self.cb_tel.bind("<<ComboboxSelected>>", lambda e: self._riempi_tabella())
        ttk.Label(rf, text="Cerca nel testo:").pack(side=tk.LEFT, padx=(16, 4))
        self.var_testo = tk.StringVar()
        e_t = ttk.Entry(rf, textvariable=self.var_testo, width=14)
        e_t.pack(side=tk.LEFT)
        self.var_testo.trace_add("write", lambda *a: self._riempi_tabella())
        self.lbl_sel = ttk.Label(rf, text="", font=self.font_grassetto)
        self.lbl_sel.pack(side=tk.RIGHT)

        # --- in basso (impacchettati prima della tabella, così restano sempre visibili)
        basso = ttk.Frame(radice)
        basso.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.log = tk.Text(basso, height=4, wrap="word", font=self.font_log)
        sb_log = ttk.Scrollbar(basso, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=sb_log.set)
        fr_dl = ttk.Frame(basso)
        fr_dl.pack(fill=tk.X, pady=(0, 6))
        self.btn_scarica = ttk.Button(fr_dl, text="⬇  Scarica selezionati", style="Grande.TButton",
                                      command=self._scarica)
        self.btn_scarica.pack(side=tk.LEFT)
        self.btn_script = ttk.Button(fr_dl, text="Salva solo script .sh/.bat", command=self._solo_script)
        self.btn_script.pack(side=tk.LEFT, padx=6)
        self.btn_csv = ttk.Button(fr_dl, text="💾 Salva tabella CSV", command=self._salva_tabella)
        self.btn_csv.pack(side=tk.LEFT, padx=(0, 6))
        self.var_salva_script = tk.BooleanVar(value=bool(self.cfg.get("salva_script", False)))
        ttk.Checkbutton(fr_dl, text="Crea lo script .sh/.bat a ogni download", variable=self.var_salva_script,
                        command=self._ricorda_opzioni).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_ferma = ttk.Button(fr_dl, text="■ Interrompi", command=self._interrompi)
        self.btn_ferma.pack(side=tk.LEFT)
        fr_pr = ttk.Frame(fr_dl)
        fr_pr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
        self.lbl_prog = ttk.Label(fr_pr, text="")
        self.lbl_prog.pack(fill=tk.X)
        self.prog_file = ttk.Progressbar(fr_pr, mode="determinate")
        self.prog_file.pack(fill=tk.X)
        self.prog_tot = ttk.Progressbar(fr_pr, mode="determinate")
        self.prog_tot.pack(fill=tk.X, pady=(2, 0))
        sb_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- tabella
        ft = ttk.Frame(radice)
        ft.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        chiavi = [c[0] for c in COLONNE_TABELLA]
        self.tabella = ttk.Treeview(ft, columns=chiavi, show="headings", selectmode="browse")
        for chiave, titolo, _, allin in COLONNE_TABELLA:
            self.tabella.heading(chiave, text=titolo, command=lambda c=chiave: self._ordina(c))
            self.tabella.column(chiave, anchor=allin, stretch=(chiave == "nome_file"))
        sb_v = ttk.Scrollbar(ft, orient=tk.VERTICAL, command=self.tabella.yview)
        sb_h = ttk.Scrollbar(ft, orient=tk.HORIZONTAL, command=self.tabella.xview)
        self.tabella.configure(yscrollcommand=sb_v.set, xscrollcommand=sb_h.set)
        self.tabella.grid(row=0, column=0, sticky="nsew")
        sb_v.grid(row=0, column=1, sticky="ns")
        sb_h.grid(row=1, column=0, sticky="ew")
        ft.rowconfigure(0, weight=1)
        ft.columnconfigure(0, weight=1)
        self.tabella.tag_configure("no", foreground="#999999")
        self.tabella.tag_configure("sel", background="#d8ecff")
        self.tabella.bind("<Button-1>", self._clic_tabella)
        self.tabella.bind("<space>", self._spazio_tabella)
        # rotellina del mouse anche su Linux (Button-4/5)
        self.tabella.bind("<Button-4>", lambda e: self.tabella.yview_scroll(-3, "units"))
        self.tabella.bind("<Button-5>", lambda e: self.tabella.yview_scroll(3, "units"))

    # -------------------------------------------------------------- utilità
    def _scrivi_log(self, testo):
        self.log.insert(tk.END, f"{datetime.now():%H:%M:%S}  {testo}\n")
        self.log.see(tk.END)

    def _aggiorna_stato_pulsanti(self):
        n_sel = sum(1 for r in self.risultati if r["selezionato"])
        stato = tk.DISABLED if self.occupato else tk.NORMAL
        self.btn_cerca.configure(state=stato)
        self.btn_mosaici.configure(state=stato)
        if getattr(self, "btn_trova_mosaico", None) is not None:
            try:
                self.btn_trova_mosaico.configure(state=stato)
            except tk.TclError:
                self.btn_trova_mosaico = None
        self.btn_scarica.configure(state=tk.NORMAL if n_sel and not self.occupato else tk.DISABLED)
        self.btn_script.configure(state=tk.NORMAL if n_sel and not self.occupato else tk.DISABLED)
        self.btn_ferma.configure(state=tk.NORMAL if self.occupato else tk.DISABLED)
        self.btn_csv.configure(state=tk.NORMAL if self.risultati and not self.occupato else tk.DISABLED)

    def _scegli_base(self):
        d = filedialog.askdirectory(title="Cartella in cui salvare i dati",
                                    initialdir=self.var_base.get() if Path(self.var_base.get()).is_dir() else str(Path.home()))
        if d:
            self.var_base.set(d)
            self.cfg["cartella_dati"] = d
            salva_config(self.cfg)
            self._riempi_tabella()

    def _apri_base(self):
        p = Path(self.var_base.get())
        if not p.is_dir():
            messagebox.showinfo("Cartella", "La cartella non esiste ancora: verrà creata al primo download.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(p))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            messagebox.showerror("Errore", str(e))

    # -------------------------------------------------------------- lista da cercare
    def _aggiorna_lista(self):
        self.lista = leggi_lista_da_cercare()
        voci = [f"{x['nome']}" + (f"  ({x['tipo']})" if x.get("tipo") else "") for x in self.lista]
        self.cb_lista.configure(values=voci)
        self.lbl_lista.configure(text=f"{len(voci)} oggetti messi da parte (anche da Atlante Celeste)" if voci
                                 else "vuota: aggiungi oggetti da Atlante Celeste")
        if self.var_lista.get() not in voci:
            self.var_lista.set("")

    def _usa_da_lista(self):
        i = self.cb_lista.current()
        if 0 <= i < len(self.lista):
            x = self.lista[i]
            self.var_nome.set(x["nome"])
            if x.get("ra") is not None and x.get("dec") is not None:
                self.var_coord.set(f"{float(x['ra']):.6f} {float(x['dec']):+.6f}")
            else:
                self.var_coord.set("")
            self._scrivi_log(f"Dalla lista: {x['nome']} — premi Cerca")

    def _rimuovi_da_lista(self):
        i = self.cb_lista.current()
        if 0 <= i < len(self.lista):
            nome = self.lista[i]["nome"]
            lista = [x for x in leggi_lista_da_cercare() if x.get("nome") != nome]
            scrivi_lista_da_cercare(lista)
            self.var_lista.set("")
            self._aggiorna_lista()
            self._scrivi_log(f"Tolto dalla lista: {nome}")

    def _oggetto_iniziale(self):
        """Avvio da Atlante Celeste: --oggetto NOME --coordinate "RA DEC" -> ricerca immediata."""
        a = sys.argv
        if "--oggetto" in a:
            try:
                self.var_nome.set(a[a.index("--oggetto") + 1])
                self.var_coord.set(a[a.index("--coordinate") + 1] if "--coordinate" in a else "")
                self.after(600, self._cerca)
            except IndexError:
                pass

    def _base(self):
        return Path(self.var_base.get()).expanduser()

    # -------------------------------------------------------------- ricerca
    def _cerca(self):
        if self.occupato:
            return
        nome = self.var_nome.get().strip()
        testo_coord = self.var_coord.get().strip()
        if not nome and not testo_coord:
            messagebox.showinfo("Ricerca", "Scrivi il nome di un oggetto oppure le coordinate.")
            return
        try:
            raggio = float(self.var_raggio.get().replace(",", ".")) / 60
            if raggio <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Raggio", "Il raggio deve essere un numero positivo (primi d'arco).")
            return
        coordinate = None
        if testo_coord:
            try:
                coordinate = leggi_coordinate(testo_coord)
            except ValueError as e:
                messagebox.showerror("Coordinate non valide", str(e))
                return
        archivi = [k for k, v in self.var_archivi.items() if v.get()]
        if not archivi:
            messagebox.showinfo("Ricerca", "Seleziona almeno un archivio.")
            return
        livelli = [2, 3] if self.var_liv2.get() else [3]
        self.cfg.update({"ultimo_nome": nome, "raggio": self.var_raggio.get(), "livello2": self.var_liv2.get(),
                         "archivi": {k: v.get() for k, v in self.var_archivi.items()},
                         "cartella_dati": self.var_base.get()})
        salva_config(self.cfg)

        self.risultati, self.visibili = [], []
        self.tabella.delete(*self.tabella.get_children())
        self.occupato = True
        self.ferma.clear()
        self._aggiorna_stato_pulsanti()
        self.lbl_oggetto.configure(text="Ricerca in corso...")
        self.prog_tot.configure(mode="indeterminate")
        self.prog_tot.start(12)

        def lavoro():
            try:
                lw = link_wikipedia(nome) if nome else None
                nome_visibile = lw[1] if lw else nome
                if coordinate is None:
                    self.coda.put(("log", f"Ricerca delle coordinate di «{nome_visibile}»..."))
                    ra, dec, fonte = risolvi_nome(nome, lambda m: self.coda.put(("log", m)))
                    self.coda.put(("log", f"Trovato: {fonte}\n  {formatta_coordinate(ra, dec)}"))
                else:
                    ra, dec = coordinate
                cart = nome_cartella_oggetto(nome_visibile) if nome else f"RA{ra:07.3f}_DEC{dec:+07.3f}"
                self.coda.put(("oggetto", {"nome": nome_visibile or cart, "cartella": cart, "ra": ra, "dec": dec}))
                stati = {k: "..." for k in archivi}
                self.coda.put(("stati", dict(stati)))
                log = lambda m: self.coda.put(("log", m))
                for k, ris in interroga_archivi({k: FUNZIONI_ARCHIVI[k] for k in archivi}, ra, dec, raggio,
                                                livelli, log, self.ferma):
                    if isinstance(ris, TimeoutError):
                        stati[k] = "nessuna risposta"
                        log(f"{k}: {ris}. La ricerca continua senza; se capita spesso, togli la spunta "
                            f"a questo archivio.")
                    elif isinstance(ris, InterruptedError):
                        stati[k] = "interrotto"
                    elif isinstance(ris, Exception):
                        stati[k] = "errore"
                        log(f"{k}: ERRORE {type(ris).__name__}: {ris}")
                    else:
                        try:
                            ris = completa_verifica(ris, ra, dec)
                            stati[k] = f"{len(ris)} file"
                            self.coda.put(("risultati", ris))
                        except Exception as e:
                            stati[k] = "errore"
                            log(f"{k}: ERRORE {type(e).__name__}: {e}")
                    self.coda.put(("stati", dict(stati)))
                self.coda.put(("fine_ricerca", None))
            except Exception as e:
                self.coda.put(("errore", f"{type(e).__name__}: {e}"))

        threading.Thread(target=lavoro, daemon=True).start()

    def _controlla_coda(self):
        try:
            while True:
                tipo, dato = self.coda.get_nowait()
                if tipo == "log":
                    self._scrivi_log(dato)
                elif tipo == "oggetto":
                    self.oggetto = dato
                    coord = formatta_coordinate(dato['ra'], dato['dec']) if dato['ra'] is not None else ""
                    self.lbl_oggetto.configure(text=f"{dato['nome']}   {coord}")
                    self._scrivi_log(f"Oggetto: {dato['nome']} -> cartella '{dato['cartella']}'")
                elif tipo == "risultati":
                    self.risultati.extend(dato)
                    self._aggiorna_filtro_telescopi()
                    self._riempi_tabella()
                elif tipo == "stati":
                    etich = {k: re.split(r"[:(,]", e)[0].strip() for k, e, _ in ARCHIVI}
                    self.lbl_archivi.configure(text="   ".join(f"{etich[k]}: {v}" for k, v in dato.items()))
                elif tipo == "fine_ricerca":
                    self._fine_ricerca()
                elif tipo == "fine_mosaico":
                    self._fine_mosaico(*dato)
                elif tipo == "progresso":
                    self._mostra_progresso(*dato)
                elif tipo == "file_fatto":
                    self._file_fatto(*dato)
                elif tipo == "fine_download":
                    self._fine_download(dato)
                elif tipo == "errore":
                    self._scrivi_log("ERRORE: " + dato)
                    messagebox.showerror("Errore", dato)
                    self.occupato = False
                    self.prog_tot.stop()
                    self.prog_tot.configure(mode="determinate", value=0)
                    self.lbl_oggetto.configure(text="Ricerca non riuscita.")
                    self._aggiorna_stato_pulsanti()
        except queue.Empty:
            pass
        self.after(100, self._controlla_coda)

    def _fine_ricerca(self):
        self.occupato = False
        self.prog_tot.stop()
        self.prog_tot.configure(mode="determinate", value=0)
        n = len(self.risultati)
        n_dentro = sum(1 for r in self.risultati if r["contiene"] is True)
        if n == 0:
            self._scrivi_log("Nessun file trovato negli archivi selezionati.")
            messagebox.showinfo("Risultati", "Nessun file trovato. Prova ad aumentare il raggio "
                                             "o a selezionare altri archivi.")
        else:
            self._scrivi_log(f"Trovati {n} file, di cui {n_dentro} contengono sicuramente l'oggetto. "
                             f"Per salvare la tabella usa '💾 Salva tabella CSV'.")
        self._aggiorna_stato_pulsanti()

    # -------------------------------------------------------------- mosaici consigliati
    def _apri_mosaici(self):
        fin = getattr(self, "fin_mosaici", None)
        if fin is not None and fin.winfo_exists():
            fin.deiconify()
            fin.lift()
            return
        fin = tk.Toplevel(self)
        self.fin_mosaici = fin
        fin.title("Mosaici HLSP consigliati")
        fin.protocol("WM_DELETE_WINDOW", fin.withdraw)
        fin.geometry(f"{round(1100 * self.zoom / 1.4)}x{round(640 * self.zoom / 1.4)}")
        fin.minsize(700, 420)
        fr = ttk.Frame(fin, padding=10)
        fr.pack(fill=tk.BOTH, expand=True)
        ttk.Label(fr, text="Mosaici pronti di Hubble e JWST (livello 4) con tre filtri dal blu al rosso",
                  font=self.font_titolo).pack(fill=tk.X)
        ttk.Label(fr, text="Scegli un mosaico e premi «Trova i file»: i file finiscono nella tabella, già spuntati; "
                           "poi «Scarica selezionati».", wraplength=1400).pack(fill=tk.X, pady=(2, 6))
        basso = ttk.Frame(fr)
        basso.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        ttk.Label(basso, text=AVVERTENZE_MOSAICI, justify=tk.LEFT, foreground="#444",
                  wraplength=round(1000 * self.zoom)).pack(fill=tk.X, pady=(0, 8))
        self.btn_trova_mosaico = ttk.Button(basso, text="🔭  Trova i file", style="Grande.TButton",
                                            command=self._cerca_mosaico)
        self.btn_trova_mosaico.pack(side=tk.LEFT)
        ttk.Button(basso, text="Chiudi", command=fin.withdraw).pack(side=tk.LEFT, padx=8)
        ft = ttk.Frame(fr)
        ft.pack(fill=tk.BOTH, expand=True)
        colonne = [("telescopio", "Telescopio", 13), ("mosaico", "Mosaico", 26), ("cosa", "Cosa si vede", 50),
                   ("filtri", "Filtri B · G · R", 24)]
        self.tab_mosaici = ttk.Treeview(ft, columns=[c[0] for c in colonne], show="headings", selectmode="browse")
        larg = tkfont.nametofont("TkDefaultFont").measure("0")
        for chiave, titolo, n in colonne:
            self.tab_mosaici.heading(chiave, text=titolo)
            self.tab_mosaici.column(chiave, width=n * larg, stretch=(chiave == "cosa"), anchor="w")
        sb = ttk.Scrollbar(ft, orient=tk.VERTICAL, command=self.tab_mosaici.yview)
        self.tab_mosaici.configure(yscrollcommand=sb.set)
        self.tab_mosaici.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        for i, r in enumerate(RICETTE_MOSAICI):
            cosa = r["cosa"] + (f"  ({r['nota']})" if r["nota"] else "")
            self.tab_mosaici.insert("", tk.END, iid=str(i), values=(r["gruppo"], r["nome"], cosa, filtri_bgr(r)))
        self.tab_mosaici.selection_set("0")
        self.tab_mosaici.bind("<Double-1>", lambda e: self._cerca_mosaico())
        self.tab_mosaici.bind("<Return>", lambda e: self._cerca_mosaico())
        self.tab_mosaici.bind("<Button-4>", lambda e: self.tab_mosaici.yview_scroll(-3, "units"))
        self.tab_mosaici.bind("<Button-5>", lambda e: self.tab_mosaici.yview_scroll(3, "units"))
        self._aggiorna_stato_pulsanti()

    def _cerca_mosaico(self):
        if self.occupato:
            return
        sel = self.tab_mosaici.selection()
        if not sel:
            messagebox.showinfo("Mosaici", "Scegli un mosaico dall'elenco.", parent=self.fin_mosaici)
            return
        ricetta = RICETTE_MOSAICI[int(sel[0])]
        self.cfg["cartella_dati"] = self.var_base.get()
        salva_config(self.cfg)
        self.risultati, self.visibili = [], []
        self.tabella.delete(*self.tabella.get_children())
        self.occupato = True
        self.ferma.clear()
        self._aggiorna_stato_pulsanti()
        self.lbl_archivi.configure(text="")
        self.lbl_oggetto.configure(text=f"Ricerca del mosaico {ricetta['nome']}...")
        self.prog_tot.configure(mode="indeterminate")
        self.prog_tot.start(12)
        self.fin_mosaici.withdraw()

        def lavoro():
            try:
                self.coda.put(("oggetto", {"nome": ricetta["nome"], "cartella": nome_cartella_mosaico(ricetta),
                                           "ra": ricetta["ra"], "dec": ricetta["dec"]}))
                righe, mancanti = trova_file_ricetta(ricetta, lambda m: self.coda.put(("log", m)))
                self.coda.put(("risultati", righe))
                self.coda.put(("fine_mosaico", (ricetta, mancanti)))
            except Exception as e:
                self.coda.put(("errore", f"{type(e).__name__}: {e}"))

        threading.Thread(target=lavoro, daemon=True).start()

    def _fine_mosaico(self, ricetta, mancanti):
        self.occupato = False
        self.prog_tot.stop()
        self.prog_tot.configure(mode="determinate", value=0)
        n = len(self.risultati)
        if n == 0:
            self._scrivi_log(f"{ricetta['nome']}: nessun file trovato.")
            if ricetta["ra"] is not None:
                messagebox.showinfo("Mosaici", f"Per {ricetta['nome']} MAST non ha restituito mosaici.\n"
                                               "Il registro dice quali HLSP ci sono in quel campo.")
        else:
            tot = sum(r["dimensione"] or 0 for r in self.risultati if r["selezionato"])
            cartella = cartella_esistente(self._base(), self.oggetto["cartella"])
            self._scrivi_log(f"{ricetta['nome']}: {n} file, in tutto {formatta_dimensione(tot) if tot else '?'}. "
                             f"Dopo «Scarica selezionati» saranno in {cartella}")
            if mancanti:
                self._scrivi_log(f"Filtri non trovati: {', '.join(mancanti)} (si può fare l'immagine con quelli presenti)")
            if ricetta["ra"] is not None:
                self._scrivi_log(f"Poi in AutoAstroPipe scegli come cartella madre {cartella} "
                                 f"(i FITS delle sottocartelle HST/JWST vengono copiati lì)")
        self._aggiorna_filtro_telescopi()
        self._riempi_tabella()
        self._aggiorna_stato_pulsanti()

    def _cartella_ricerca(self):
        return self._base() / CARTELLA_RICERCHE / self.oggetto["cartella"]

    # -------------------------------------------------------------- tabella
    def _aggiorna_filtro_telescopi(self):
        tel = sorted({r["telescopio"] for r in self.risultati})
        self.cb_tel.configure(values=["Tutti"] + tel)

    def _passa_filtri(self, r):
        if not self.var_tutti.get() and r["contiene"] is False:
            return False
        t = self.var_filtro_tel.get()
        if t and t != "Tutti" and r["telescopio"] != t:
            return False
        testo = self.var_testo.get().strip().lower()
        if testo and testo not in " ".join(str(r[k]) for k in
                                           ("telescopio", "strumento", "filtro", "nome_file", "target")).lower():
            return False
        return True

    def _valori_riga(self, r):
        cont = {True: "sì", False: "no", None: "?"}[r["contiene"]]
        if r["verificato_wcs"] == "sì":
            cont = "sì ✓"
        elif r["verificato_wcs"] == "no":
            cont = "no ✗"
        return ("☑" if r["selezionato"] else "☐", r["telescopio"], r["tipo"], r["strumento"],
                r["filtro"], formatta_dimensione(r["dimensione"], r["dimensione_esatta"]), cont,
                f"{r['distanza']:.1f}" if r["distanza"] is not None else "", r["data"], r["livello"],
                r["nome_file"], r["archivio"])

    def _chiave_ordine(self, r, col):
        if col == "dimensione":
            return r["dimensione"] or 0
        if col == "distanza":
            return r["distanza"] if r["distanza"] is not None else 1e9
        if col == "sel":
            return not r["selezionato"]
        if col == "contiene":
            return {True: 0, None: 1, False: 2}[r["contiene"]]
        return str(r.get(col, "")).lower()

    def _ordina(self, col):
        attuale, inverso = self.ordine
        self.ordine = (col, not inverso if attuale == col else False)
        self._riempi_tabella()

    def _riempi_tabella(self):
        col, inverso = self.ordine
        indici = [i for i, r in enumerate(self.risultati) if self._passa_filtri(r)]
        indici.sort(key=lambda i: self._chiave_ordine(self.risultati[i], col), reverse=inverso)
        self.visibili = indici
        self.tabella.delete(*self.tabella.get_children())
        for i in indici:
            r = self.risultati[i]
            tag = ("sel",) if r["selezionato"] else (("no",) if r["contiene"] is False else ())
            self.tabella.insert("", tk.END, iid=str(i), values=self._valori_riga(r), tags=tag)
        for chiave, titolo, _, _ in COLONNE_TABELLA:
            freccia = (" ▼" if inverso else " ▲") if chiave == col else ""
            self.tabella.heading(chiave, text=titolo + freccia)
        self._aggiorna_conteggio()

    def _aggiorna_riga(self, i):
        r = self.risultati[i]
        if self.tabella.exists(str(i)):
            tag = ("sel",) if r["selezionato"] else (("no",) if r["contiene"] is False else ())
            self.tabella.item(str(i), values=self._valori_riga(r), tags=tag)

    def _aggiorna_conteggio(self):
        sel = [r for r in self.risultati if r["selezionato"]]
        tot = sum(r["dimensione"] or 0 for r in sel)
        ignote = sum(1 for r in sel if r["dimensione"] is None)
        testo = f"Mostrati {len(self.visibili)} di {len(self.risultati)}   |   Selezionati: {len(sel)} file, {formatta_dimensione(tot, True) if tot else '0'}"
        if ignote:
            testo += f" (+{ignote} di dimensione ignota)"
        self.lbl_sel.configure(text=testo)
        self._aggiorna_stato_pulsanti()

    def _cambia_selezione(self, i):
        self.risultati[i]["selezionato"] = not self.risultati[i]["selezionato"]
        self._aggiorna_riga(i)
        self._aggiorna_conteggio()

    def _clic_tabella(self, evento):
        if self.tabella.identify_region(evento.x, evento.y) != "cell":
            return
        riga = self.tabella.identify_row(evento.y)
        if riga and not self.occupato:
            self._cambia_selezione(int(riga))

    def _spazio_tabella(self, evento):
        riga = self.tabella.focus()
        if riga and not self.occupato:
            self._cambia_selezione(int(riga))
        return "break"

    def _seleziona_tutti(self, valore):
        indici = self.visibili if valore else range(len(self.risultati))
        for i in indici:
            self.risultati[i]["selezionato"] = valore
        self._riempi_tabella()

    # -------------------------------------------------------------- download
    def _selezionati(self):
        return [r for r in self.risultati if r["selezionato"]]

    def _salva_script(self, righe):
        nome_base = f"scarica_{datetime.now():%Y%m%d_%H%M}"
        sh, bat = scrivi_script_download(self._cartella_ricerca(), self._base(), righe,
                                         self.oggetto["cartella"], nome_base)
        self._scrivi_log(f"Script di download salvati: {sh}  e  {bat.name}")
        return sh, bat

    def _ricorda_opzioni(self):
        self.cfg["salva_script"] = self.var_salva_script.get()
        salva_config(self.cfg)

    def _salva_tabella(self):
        """Salva la tabella completa (con la verifica WCS dei file già scaricati)
        solo quando lo chiede l'utente, nel percorso che sceglie."""
        if not self.risultati or not self.oggetto:
            return
        cartella = self._cartella_ricerca()
        percorso = filedialog.asksaveasfilename(
            title="Salva la tabella dei risultati", defaultextension=".csv",
            initialdir=str(cartella if cartella.is_dir() else self._base()),
            initialfile=f"{self.oggetto['cartella']}_risultati_{datetime.now():%Y%m%d_%H%M}.csv",
            filetypes=[("Tabella CSV", "*.csv"), ("Tutti i file", "*.*")])
        if not percorso:
            return
        try:
            scrivi_csv(percorso, self.risultati, self.oggetto["nome"], self.oggetto["ra"], self.oggetto["dec"],
                       self._base(), self.oggetto["cartella"])
            self._scrivi_log(f"Tabella salvata: {percorso}")
        except OSError as e:
            messagebox.showerror("Errore", f"Impossibile salvare la tabella:\n{e}")

    def _solo_script(self):
        righe = self._selezionati()
        if not righe:
            return
        try:
            sh, bat = self._salva_script(righe)
            messagebox.showinfo("Script salvati", f"Script salvati in:\n{sh.parent}\n\n"
                                                  f"Ubuntu:   bash {sh.name}\nWindows:  doppio clic su {bat.name}")
        except OSError as e:
            messagebox.showerror("Errore", str(e))

    def _scarica(self):
        righe = self._selezionati()
        if not righe or self.occupato:
            return
        tot = sum(r["dimensione"] or 0 for r in righe)
        base = self._base()
        try:
            base.mkdir(parents=True, exist_ok=True)
            libero = shutil.disk_usage(base).free
        except OSError as e:
            messagebox.showerror("Cartella dati", f"Impossibile usare la cartella:\n{base}\n\n{e}")
            return
        if tot and tot > libero * 0.95:
            if not messagebox.askyesno("Spazio su disco", f"Servono circa {formatta_dimensione(tot)}, "
                                       f"liberi {formatta_dimensione(libero)}. Continuare comunque?"):
                return
        if self.var_salva_script.get():
            try:
                self._salva_script(righe)
            except OSError as e:
                self._scrivi_log(f"Script non salvati: {e}")
        self.occupato = True
        self.ferma.clear()
        self._aggiorna_stato_pulsanti()
        ogg = dict(self.oggetto)
        indici = [i for i, r in enumerate(self.risultati) if r["selezionato"]]
        self.prog_tot.configure(maximum=max(1, len(indici)), value=0)

        def lavoro():
            esiti = {"completato": 0, "già presente": 0, "interrotto": 0, "errore": 0}
            for n, i in enumerate(indici, 1):
                if self.ferma.is_set():
                    break
                r = self.risultati[i]
                try:
                    vecchio = sposta_da_vecchia_cartella(base, r, ogg["cartella"])
                    if vecchio:
                        self.coda.put(("log", f"{r['nome_file']}: già scaricato nella cartella vecchia, "
                                              f"spostato da {vecchio.parent}"))
                except OSError as e:
                    self.coda.put(("log", f"{r['nome_file']}: non riesco a spostarlo dalla cartella vecchia ({e})"))
                dest = destinazione(base, r, ogg["cartella"])
                self.coda.put(("progresso", (n, len(indici), r["nome_file"], 0, r["dimensione"])))
                ultimo = [0.0]

                def prog(ricevuti, totale, n=n, nome=r["nome_file"]):
                    adesso = time.monotonic()
                    if adesso - ultimo[0] > 0.25:
                        ultimo[0] = adesso
                        self.coda.put(("progresso", (n, len(indici), nome, ricevuti, totale)))
                esito, wcs = "errore", ""
                indirizzi = [r["url"]] + list(r.get("url_alternativi") or [])
                for k_url, url in enumerate(indirizzi):
                    if k_url:
                        self.coda.put(("log", f"{r['nome_file']}: provo un altro indirizzo ({url.split('?')[0][:90]})"))
                    for tentativo in range(3):
                        try:
                            esito = scarica_file(url, dest, r["dimensione"] if r["dimensione_esatta"] else None,
                                                 self.ferma, prog)
                            break
                        except ContenutoSbagliato as e:
                            self.coda.put(("log", f"{r['nome_file']}: {e}"))
                            esito = "errore"
                            break
                        except Exception as e:
                            self.coda.put(("log", f"{r['nome_file']}: tentativo {tentativo + 1} fallito ({e})"))
                            if self.ferma.wait(5 * (tentativo + 1)):
                                esito = "interrotto"
                                break
                    if esito != "errore":
                        break
                if esito in ("completato", "già presente") and dest.exists() and ogg["ra"] is not None:
                    wcs = verifica_wcs(dest, ogg["ra"], ogg["dec"])
                esiti[esito] = esiti.get(esito, 0) + 1
                self.coda.put(("file_fatto", (i, n, esito, wcs, str(dest))))
            self.coda.put(("fine_download", esiti))

        threading.Thread(target=lavoro, daemon=True).start()

    def _mostra_progresso(self, n, tot, nome, ricevuti, totale):
        if totale:
            self.prog_file.configure(maximum=totale, value=min(ricevuti, totale))
            testo = f"{formatta_dimensione(ricevuti)} di {formatta_dimensione(totale)}"
        else:
            self.prog_file.configure(maximum=1, value=0)
            testo = formatta_dimensione(ricevuti) if ricevuti else ""
        self.lbl_prog.configure(text=f"File {n}/{tot}: {nome}   {testo}")

    def _file_fatto(self, i, n, esito, wcs, dest):
        r = self.risultati[i]
        if wcs:
            r["verificato_wcs"] = wcs
        self.prog_tot.configure(value=n)
        self._aggiorna_riga(i)
        msg = f"{r['nome_file']}: {esito}"
        if wcs:
            msg += f" | oggetto nel campo (WCS): {wcs}"
        self._scrivi_log(msg + (f"  -> {Path(dest).parent}" if esito == "completato" else ""))

    def _fine_download(self, esiti):
        self.occupato = False
        self._aggiorna_stato_pulsanti()
        self.lbl_prog.configure(text="Download terminato" if not self.ferma.is_set() else "Download interrotto")
        riepilogo = ", ".join(f"{k}: {v}" for k, v in esiti.items() if v)
        self._scrivi_log("Fine download. " + riepilogo)
        if esiti.get("errore") or esiti.get("interrotto"):
            messagebox.showinfo("Download", f"{riepilogo}\n\nPer riprendere premi di nuovo 'Scarica': "
                                            f"i file parziali (.part) ripartono dal punto in cui si erano fermati.")

    def _interrompi(self):
        self.ferma.set()
        self._scrivi_log("Interruzione richiesta: i file parziali (.part) verranno ripresi al prossimo download.")

    def _chiudi(self):
        if self.occupato and not messagebox.askyesno("Uscire?", "C'è un'operazione in corso. Uscire comunque?\n"
                                                                "(i download parziali si potranno riprendere)"):
            return
        self.ferma.set()
        self.destroy()


# =============================================================================
# AUTOTEST E DIAGNOSTICA
# =============================================================================
def autotest():
    """Prove senza rete: coordinate, nomi, impronte, script, CSV, moduli e Tk."""
    import tempfile
    print(f"AUTOTEST {NOME_PROGRAMMA}")
    ok = True

    def verifica(cond, testo):
        nonlocal ok
        print(("[ok] " if cond else "[ERRORE] ") + testo)
        ok &= bool(cond)

    try:
        ra, dec = leggi_coordinate("00h 59m 05.090s −72° 10′ 33.24″")
        verifica(abs(ra - 14.771208) < 1e-4 and abs(dec + 72.17590) < 1e-4, f"coordinate Wikipedia -> {ra:.5f} {dec:.5f}")
        ra, dec = leggi_coordinate("RA 05h35m17.3s Dec -05d23m28s")
        verifica(abs(ra - 83.82208) < 1e-4 and abs(dec + 5.39111) < 1e-4, "coordinate con RA/Dec")
        verifica(leggi_coordinate("10:00:00 +02:30:00") == (150.0, 2.5), "coordinate con i due punti")
        verifica(leggi_coordinate("14.77 -72.18") == (14.77, -72.18), "coordinate decimali")
        verifica(nome_cartella_oggetto("ngc 346") == "NGC_346" and nome_cartella_oggetto("IC1234") == "IC_1234"
                 and nome_cartella_oggetto("Cartwheel Galaxy") == "Cartwheel_Galaxy", "nomi delle cartelle")
        f = leggi_s_region("POLYGON ICRS 14.7 -72.2 14.9 -72.2 14.9 -72.1 14.7 -72.1")
        verifica(contiene_punto(f, 14.8, -72.15) is True and contiene_punto(f, 15.5, -72.15) is False, "impronta poligono")
        verifica(contiene_punto(leggi_s_region("CIRCLE ICRS 10 20 0.1"), 10.05, 20) is True, "impronta cerchio")
        verifica(contiene_punto(leggi_s_region("BOX ICRS 10 20 0.2 0.2"), 10.0, 20.12) is False, "impronta rettangolo")
        verifica(contiene_punto(leggi_s_region(None), 1, 1) is None, "impronta assente")
        verifica(livello_ammesso(3, [3]) and not livello_ammesso("2", [3]) and livello_ammesso(None, [3]),
                 "filtro sul livello")
        q = condizione_riquadro(0.1, 10, 0.5)
        verifica("OR" in q and "359" in q and "BETWEEN 9.5" in q, "riquadro per Chandra (RA = 0)")
        verifica(telescopio_da("mast", "JWST")[1] == "JWST" and telescopio_da("eso", "", "ESO-VLT-U1")[1] == "VLT",
                 "nomi dei telescopi")
        with tempfile.TemporaryDirectory() as d:
            righe = [_risultato(archivio="MAST", telescopio="JWST", cartella_telescopio="JWST",
                                nome_file="prova_i2d.fits", url="https://example.org/a?b=1&c=%20",
                                dimensione=1234, contiene=True, distanza=0.5)]
            sh, bat = scrivi_script_download(Path(d) / "r", Path(d), righe, "NGC_346", "scarica_test")
            testo = sh.read_text(encoding="utf-8")
            verifica("NGC_346/JWST/prova_i2d.fits" in testo and "-C -" in testo, "script .sh (cartella OGGETTO/TELESCOPIO)")
            verifica("%%%%20" in bat.read_text(encoding="utf-8"), "script .bat")
            scrivi_csv(Path(d) / "r" / "t.csv", righe, "NGC 346", 14.77, -72.17, Path(d), "NGC_346")
            verifica((Path(d) / "r" / "t.csv").stat().st_size > 100, "tabella CSV")
            vecchio = Path(d) / "HST" / "M_1" / "a_drz.fits"
            vecchio.parent.mkdir(parents=True)
            vecchio.write_bytes(b"x" * 10)
            rh = _risultato(cartella_telescopio="HST", nome_file="a_drz.fits")
            spostato = sposta_da_vecchia_cartella(Path(d), rh, "M_1")
            nuovo = Path(d) / "M_1" / "HST" / "a_drz.fits"
            verifica(spostato == vecchio and nuovo.stat().st_size == 10 and not (Path(d) / "HST").exists()
                     and destinazione(Path(d), _risultato(cartella_telescopio="JWST", nome_file="b.fits"), "M_1")
                     == Path(d) / "M_1" / "JWST" / "b.fits",
                     "cartelle M_1/HST, M_1/JWST; file vecchio HST/M_1 spostato")
        r = cerca_catalogo_incluso("M16")
        verifica(r is not None and abs(r[0] - 274.70) < 0.05 and abs(r[1] + 13.81) < 0.05, f"catalogo incluso: M16 -> {r and r[2]}")
        r2 = cerca_catalogo_incluso("ngc 346")
        verifica(r2 is not None and abs(r2[0] - 14.77) < 0.05, "catalogo incluso: NGC 346")
        r3 = cerca_catalogo_incluso("Eagle Nebula")
        verifica(r3 is not None and abs(r3[0] - 274.7) < 0.1, "catalogo incluso: nome comune 'Eagle Nebula'")
        def _lento(*a):
            time.sleep(30)
            return []
        t0 = time.time()
        esiti = dict(interroga_archivi({"veloce": lambda *a: [1, 2], "lento": _lento, "rotto": lambda *a: 1 / 0},
                                       0, 0, 0.01, [3], lambda m: None, limite=2, avviso_ogni=1))
        verifica(esiti["veloce"] == [1, 2] and isinstance(esiti["lento"], TimeoutError)
                 and isinstance(esiti["rotto"], ZeroDivisionError) and time.time() - t0 < 5,
                 f"archivio bloccato: la ricerca finisce lo stesso ({time.time() - t0:.1f} s)")
        ps1 = url_da_dataurl("http://ps1images.stsci.edu/rings.v3.skycell/2571/023/rings.v3.skycell.2571.023.stk.i.unconv.fits")
        ps1b = url_da_dataurl("mast:PS1/product/rings.v3.skycell.2571.023.stk.i.unconv.fits")
        verifica(ps1[0].startswith("https://ps1images.stsci.edu/") and len(ps1) == 2
                 and ps1b[-1] == "https://ps1images.stsci.edu/rings.v3.skycell/2571/023/rings.v3.skycell.2571.023.stk.i.unconv.fits"
                 and url_da_dataurl("mast:JWST/product/x_i2d.fits") == [URL_MAST_DOWNLOAD + "mast:JWST/product/x_i2d.fits"],
                 "Pan-STARRS: indirizzo web usato direttamente (non passato a MAST)")
        icona = percorso_dati(FILE_ICONA)
        verifica(icona.exists() and icona.read_bytes()[:4] == b"\x89PNG", "icona del programma inclusa")
        for n in ("Pillars of Creation", "pilastri della creazione", "Nebulosa di Orione", "Cosmic Cliffs"):
            verifica(cerca_soprannome(n) is not None, f"soprannome '{n}' -> {cerca_soprannome(n)}")
        ra_p, dec_p, _ = risolvi_nome("Pillars of Creation")
        verifica(abs(ra_p - 274.72) < 0.01 and abs(dec_p + 13.84) < 0.01, "risolvi_nome('Pillars of Creation') senza rete")
        verifica(link_wikipedia("https://en.wikipedia.org/wiki/Pillars_of_Creation") == ("en", "Pillars of Creation")
                 and link_wikipedia("https://it.m.wikipedia.org/wiki/Nebulosa_Aquila#Storia") == ("it", "Nebulosa Aquila"),
                 "link di Wikipedia")
        verifica(leggi_risposta_sesame("# M 16\n%J 274.7000 -13.8070 = 18 18 48.00 -13 48 25.2\n%I.0 M  16\n")[:2]
                 == (274.7, -13.807), "risposta di Sesame")
        verifica(leggi_risposta_ned('{"Preferred": {"Name": "MESSIER 016", "Position": {"RA": 274.7, "Dec": -13.8}}}')[:2]
                 == (274.7, -13.8), "risposta di NED")
        c = coordinate_wikitesto("| ra = {{RA|18|18|48}}\n| dec = {{DEC|−13|49|}}")
        verifica(c and abs(c[0] - 274.7) < 1e-6 and abs(c[1] + 13.81667) < 1e-4, "coordinate dal testo di Wikipedia")
        e = {"claims": {"P6257": [{"mainsnak": {"datavalue": {"value": {"amount": "+274.7"}}}}],
                        "P6258": [{"mainsnak": {"datavalue": {"value": {"amount": "-13.8"}}}}]}}
        verifica(coordinate_wikidata(e) == (274.7, -13.8), "coordinate da Wikidata")
        global FILE_LISTA_DA_CERCARE
        vecchia = FILE_LISTA_DA_CERCARE
        with tempfile.TemporaryDirectory() as d:
            FILE_LISTA_DA_CERCARE = Path(d) / "lista.json"
            scrivi_lista_da_cercare([{"nome": "M 16", "ra": 274.7, "dec": -13.8, "tipo": "nebulosa"}])
            verifica(leggi_lista_da_cercare()[0]["nome"] == "M 16", "lista 'da cercare' condivisa con Atlante Celeste")
        FILE_LISTA_DA_CERCARE = vecchia
        autotest_mosaici(verifica)
        import astropy, pyvo
        from astroquery.mast import Observations   # noqa: F401
        from astropy.wcs import WCS                 # noqa: F401
        verifica(True, f"moduli: astropy {astropy.__version__}, pyvo {pyvo.__version__}, "
                       f"astroquery {__import__('astroquery').__version__}")
        radice = tk.Tk()
        radice.withdraw()
        st = ttk.Style(radice)
        st.theme_use("clam")
        radice.destroy()
        verifica(True, "finestra Tk")
    except Exception:
        traceback.print_exc()
        ok = False
    print("AUTOTEST SUPERATO" if ok else "AUTOTEST FALLITO")
    return 0 if ok else 1


def autotest_mosaici(verifica):
    """Prove senza rete della scelta dei mosaici, su nomi di file finti."""
    P = lambda *nomi: [{"nome": n, "uri": "mast:HLSP/x/" + n, "size": 1000, "obsid": "1"} for n in nomi]
    hff = P("hlsp_frontier_hst_acs-30mas_abell2744clu_f435w_v1.0-epoch1_drz.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f435w_v1.0-epoch1_drz.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f435w_v1.0-epoch1_wht.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744par_f435w_v1.0-epoch1_drz.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f606w_v1.0-epoch1_drz.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f814w_v1.0-epoch1_drz.fits",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f814w_v1.0-epoch1_drz.jpg",
            "hlsp_frontier_hst_acs-60mas_abell2744clu_f814w_v1.0-epoch1_exp.fits")
    s = scegli_file(hff, ["F435W", "F606W", "F814W"], SCALE_HST, ["clu"])
    verifica(s["f435w"]["nome"].startswith("hlsp_frontier_hst_acs-60mas_abell2744clu_f435w")
             and s["f435w"]["nome"].endswith("_drz.fits") and s["f814w"]["nome"].endswith("_drz.fits")
             and s["f606w"] is not None, "mosaici: HFF, scala 60 mas, campo dell'ammasso, niente pesi e jpg")
    s = scegli_file(P("hlsp_xdf_hst_acswfc-60mas_hudf_f850lp_v1_sci.fits", "hlsp_xdf_hst_acswfc-60mas_hudf_f850lpx_v1_sci.fits",
                      "hlsp_xdf_hst_acswfc-30mas_hudf_f850lp_v1_sci.fits", "hlsp_xdf_hst_acswfc-60mas_hudf_f850lp_v1_rms.fits"),
                    ["F850LP"], SCALE_HST)
    verifica(s["f850lp"]["nome"] == "hlsp_xdf_hst_acswfc-60mas_hudf_f850lp_v1_sci.fits", "mosaici: filtro come parola intera (F850LP)")
    s = scegli_file(P("hlsp_phangs-hst_hst_wfc3-uvis_ngc628mosaic_f438w_v1_exp-drc-sci.fits",
                      "hlsp_phangs-hst_hst_wfc3-uvis_ngc628mosaic_f438w_v1_err-drc-wht.fits",
                      "hlsp_phangs-hst_hst_acs-wfc_ngc628mosaic_f555w_v1_exp-drc-sci.fits.gz",
                      "hlsp_phangs-hst_hst_acs-wfc_ngc628mosaic_f814w_v1_exp-drc-sci.fits"), ["F438W", "F555W", "F814W"])
    verifica(all(s.values()) and s["f438w"]["nome"].endswith("exp-drc-sci.fits") and s["f555w"]["nome"].endswith(".gz"),
             "mosaici: PHANGS «exp-drc-sci» accettato, «wht» escluso, anche .fits.gz")
    s = scegli_file(P("hlsp_uncover_jwst_nircam_abell2744_f444w_v5.4_20mas_sci.fits",
                      "hlsp_uncover_jwst_nircam_abell2744_f444w_v5.4_40mas_sci.fits",
                      "hlsp_uncover_jwst_nircam_abell2744_f444w_v7.2_40mas_sci.fits",
                      "hlsp_uncover_jwst_nircam_abell2744_f444w_v7.2_40mas_err.fits"), ["F444W"], SCALE_JWST)
    verifica(s["f444w"]["nome"] == "hlsp_uncover_jwst_nircam_abell2744_f444w_v7.2_40mas_sci.fits",
             "mosaici: JWST 40 mas e versione più recente")
    s = scegli_file(P("hlsp_ceers_jwst_nircam_nircam2_f115w_dr0.6_i2d.fits", "hlsp_ceers_jwst_nircam_nircam1_f115w_dr0.6_i2d.fits",
                      "hlsp_ceers_jwst_nircam_nircam1_f200w_dr0.6_i2d.fits", "hlsp_ceers_jwst_nircam_nircam1_f444w_dr0.6_i2d.fits"),
                    ["F115W", "F200W", "F444W"], SCALE_JWST)
    verifica(all("nircam1_" in p["nome"] for p in s.values()), "mosaici: tre filtri dallo stesso puntamento (CEERS)")
    verifica(scegli_file(P("a_f435w_drz.fits"), ["F606W"])["f606w"] is None, "mosaici: filtro mancante -> None")
    c = url_candidati("mast:HLSP/url/cgi-bin/getdata.cgi?dataset=hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz",
                      "hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz.fits",
                      "http://archive.stsci.edu/pub/hlsp/frontier/abell2744/images/hst/v1.0-epoch2/"
                      "hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz.fits")
    c2 = url_candidati("mast:HLSP/url/cgi-bin/getdata.cgi?dataset=hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz",
                       "hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz.fits",
                       "http://hla.stsci.edu/cgi-bin/getdata.cgi?dataset=hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz")
    statici = _indirizzi_statici_hlsp("hlsp_candels_hst_acs_gs-tot_f606w_v1.0_drz.fits")
    verifica(c2[0] == "https://hla.stsci.edu/cgi-bin/getdata.cgi?dataset=hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz"
             and "https://archive.stsci.edu/pub/hlsp/frontier/abell2744/images/hst/v1.0-epoch2/"
                 "hlsp_frontier_hst_acs-60mas_abell2744_f435w_v1.0_drz.fits" in c2
             and "https://archive.stsci.edu/hlsps/candels/goods-s/gs-tot/v1.0/hlsp_candels_hst_acs_gs-tot_f606w_v1.0_drz.fits" in statici
             and any("legus/ngc628-c/" in x for x in _indirizzi_statici_hlsp("hlsp_legus_hst_acs_ngc628-c_f435w_v1_drc.fits"))
             and c2[-1].startswith(URL_MAST_DOWNLOAD), "mosaici: indirizzi HLA e archivio statico per gli HLSP vecchi")
    verifica(len(c) > 3 and c[-1].startswith(URL_MAST_DOWNLOAD)
             and url_candidati("mast:HLSP/phangs-jwst/v1p1/a.fits", "a.fits")
             == [URL_MAST_DOWNLOAD + "mast:HLSP/phangs-jwst/v1p1/a.fits", "https://archive.stsci.edu/hlsps/phangs-jwst/v1p1/a.fits"]
             and url_candidati("mast:JWST/product/b.fits", "b.fits") == [URL_MAST_DOWNLOAD + "mast:JWST/product/b.fits"],
             "mosaici: indirizzi alternativi per gli HLSP vecchi (HLSP/url/getdata.cgi)")
    with __import__("tempfile").TemporaryDirectory() as d:
        finto = Path(d) / "x.fits.part"
        finto.write_bytes(b"Error: file not found")
        try:
            controlla_contenuto(finto, "x.fits")
            ok_c = False
        except ContenutoSbagliato as e:
            ok_c = not finto.exists() and "21 byte" in str(e)
        buono = Path(d) / "y.fits.part"
        buono.write_bytes(b"SIMPLE  =                    T" + b" " * 50)
        controlla_contenuto(buono, "y.fits")
        altro = Path(d) / "z.part"
        altro.write_bytes(b"qualsiasi cosa")
        controlla_contenuto(altro, "z.txt")
        asdf = Path(d) / "r.asdf"
        asdf.write_bytes(b"#ASDF 1.0.0\n")
        verifica(ok_c and buono.exists() and altro.exists() and firma_valida(asdf, "r.asdf")
                 and not firma_valida(finto.with_name("y.fits.part") if False else buono, "y.png"),
                 "firme dei file: messaggio d'errore scartato; FITS, ASDF e file senza firma accettati")
        # file già presente ma non valido: va riscaricato (qui senza rete: deve sparire e dare errore di rete)
        vecchio = Path(d) / "v.fits"
        vecchio.write_bytes(b"Error")
        try:
            scarica_file("http://127.0.0.1:9/v.fits", vecchio, 5, threading.Event(), lambda *a: None)
        except Exception:
            pass
        verifica(not vecchio.exists(), "file già presente ma non valido: cancellato e riscaricato")
    s = scegli_file(P("hlsp_legus_hst_wfc3_ngc628-c_f438w_v1_drc.fits", "hlsp_legus_hst_acs_ngc628-c_f435w_v1_drc.fits",
                      "hlsp_legus_hst_acs_ngc628-c_f555w_v1_drc.fits", "hlsp_legus_hst_acs_ngc628-c_f814w_v1_drc.fits",
                      "hlsp_legus_hst_wfc3_ngc628-c_f275w_v1_drc.fits"), _F_LEGUS, (), ["ngc628"])
    verifica(all(s.values()) and "f435w" in s["f435w|f438w"]["nome"] + s["f435w|f438w"]["nome"].replace("f438w", "f435w"),
             "mosaici: filtri alternativi F435W/F438W (LEGUS)")
    ok_r = all(len([f for p in r["parti"] for f in p["filtri"]]) == 3 and -90 <= r["dec"] <= 90 and 0 <= r["ra"] < 360
               for r in RICETTE_MOSAICI if r["ra"] is not None)
    verifica(ok_r and len(RICETTE_MOSAICI) >= 12 and filtri_bgr(RICETTE_MOSAICI[0]) == "F435W · F606W · F814W"
             and nome_cartella_mosaico(RICETTE_MOSAICI[0]) == "Mosaico_HFF_Abell_2744",
             f"mosaici: {len(RICETTE_MOSAICI)} ricette con 3 filtri e coordinate valide")


def diagnostica_mosaici():
    """Prova le ricette dei mosaici consigliati dalla rete di casa (serve Internet)."""
    print(f"PROVA DELLE RICETTE DEI MOSAICI - {datetime.now():%Y-%m-%d %H:%M}")
    for r in RICETTE_MOSAICI:
        t0 = time.time()
        print(f"\n== {r['gruppo']}: {r['nome']}  ({filtri_bgr(r)})")
        try:
            righe, mancanti = trova_file_ricetta(r, print)
            tot = sum(x["dimensione"] or 0 for x in righe if x["selezionato"])
            esito = "ok" if righe and not mancanti else ("--" if not righe else "parziale")
            print(f"[{esito}] {r['nome']}: {len(righe)} file, {formatta_dimensione(tot) if tot else '?'}"
                  + (f", mancano {', '.join(mancanti)}" if mancanti else "") + f" ({time.time() - t0:.0f} s)")
            for x in righe[:4]:
                print(f"       {x['filtro']} | {formatta_dimensione(x['dimensione'])} | {x['nome_file']}")
                print(f"           {x['url']}")
        except Exception as e:
            print(f"[ERRORE] {r['nome']}: {type(e).__name__}: {str(e)[:300]} ({time.time() - t0:.0f} s)")
    return 0


def diagnostica():
    """Interroga davvero ogni archivio (serve Internet) e scrive cosa trova."""
    print(f"DIAGNOSTICA {NOME_PROGRAMMA} - {datetime.now():%Y-%m-%d %H:%M}")
    print("Risoluzione dei nomi: ogni fonte provata da sola")
    prove = [("NGC 346", cerca_catalogo_incluso), ("Pillars of Creation", cerca_soprannome),
             ("NGC 346", cerca_sesame), ("Pillars of Creation", cerca_sesame), ("NGC 346", cerca_ned),
             ("Pilastri della Creazione", cerca_wikipedia), ("Eagle Nebula", cerca_wikipedia),
             ("https://en.wikipedia.org/wiki/Pillars_of_Creation", cerca_wikipedia),
             ("NGC 346", cerca_simbad)]
    for nome, f in prove:
        t0 = time.time()
        try:
            r = f(nome)
            print(f"[ok] {f.__name__}('{nome}') -> {r[0]:.4f} {r[1]:+.4f}  {r[2]}  ({time.time() - t0:.1f} s)"
                  if r else f"[--] {f.__name__}('{nome}'): non trovato")
        except Exception as e:
            print(f"[ERRORE] {f.__name__}('{nome}'): {type(e).__name__}: {str(e)[:200]} ({time.time() - t0:.1f} s)")
    for prova in ("https://en.wikipedia.org/wiki/Pillars_of_Creation", "Pilastri della Creazione", "Horsehead Nebula"):
        t0 = time.time()
        try:
            r = risolvi_nome(prova)
            print(f"[ok] risolvi_nome('{prova}') -> {r[0]:.4f} {r[1]:+.4f}  {r[2]}  ({time.time() - t0:.1f} s)")
        except Exception as e:
            print(f"[ERRORE] risolvi_nome('{prova}'): {str(e)[:300]}")
    try:
        ra, dec, fonte = risolvi_nome("NGC 346")
        print(f"[ok] risolvi_nome('NGC 346') -> {ra:.5f} {dec:.5f} ({fonte})")
    except Exception as e:
        print(f"[ERRORE] risolvi_nome: {e}")
        ra, dec = 14.77083, -72.17583
    for chiave, etichetta, _ in ARCHIVI:
        t0 = time.time()
        try:
            ris = completa_verifica(FUNZIONI_ARCHIVI[chiave](ra, dec, 1 / 60, [2, 3], print), ra, dec)
            dentro = sum(1 for r in ris if r["contiene"])
            per_livello = {}
            for r in ris:
                per_livello[r["livello"] or "?"] = per_livello.get(r["livello"] or "?", 0) + 1
            print(f"[ok] {etichetta}: {len(ris)} file (livelli 2+3), {dentro} contengono l'oggetto, "
                  f"per livello {per_livello} ({time.time() - t0:.0f} s)")
            for r in ris[:3]:
                print(f"       {r['telescopio']} | {r['strumento']} | {r['filtro']} | "
                      f"{formatta_dimensione(r['dimensione'], r['dimensione_esatta'])} | "
                      f"contiene={r['contiene']} | {r['nome_file']}\n       {r['url']}")
        except Exception as e:
            print(f"[ERRORE] {etichetta}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print()
    diagnostica_mosaici()
    return 0


def _uscita_testo(nome):
    """Negli eseguibili senza console (Windows) stdout può non esistere:
    in quel caso si scrive in <nome>_risultato.txt nella cartella corrente."""
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = sys.stderr = open(Path.cwd() / f"{nome}_risultato.txt", "w", encoding="utf-8", buffering=1)
    try:
        sys.stdout.reconfigure(line_buffering=True)   # il file si riempie mentre la prova va avanti
    except (AttributeError, ValueError):
        pass


def main():
    if "--autotest" in sys.argv:
        _uscita_testo("autotest")
        sys.exit(autotest())
    if "--diagnostica-mosaici" in sys.argv:
        _uscita_testo("diagnostica_mosaici")
        sys.exit(diagnostica_mosaici())
    if "--diagnostica" in sys.argv:
        _uscita_testo("diagnostica")
        sys.exit(diagnostica())
    if getattr(sys, "frozen", False) or sys.stderr is None:
        try:
            log = open(Path.home() / ".ricerca_archivi_fits.log", "a", encoding="utf-8", buffering=1)
            log.write(f"\n--- avvio {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
            sys.stdout = sys.stderr = log
        except OSError:
            sys.stdout = sys.stderr = open(os.devnull, "w")
    import socket
    socket.setdefaulttimeout(TIMEOUT_RETE)     # nessuna connessione resta appesa per sempre
    print(f"{NOME_PROGRAMMA} {VERSIONE} - eseguibile: {sys.executable if getattr(sys, 'frozen', False) else __file__}")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
