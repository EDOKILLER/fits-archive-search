# -*- coding: utf-8 -*-
"""Icona di Ricerca archivi FITS: lente d'ingrandimento che inquadra una nebulosa,
davanti a una pila di fotogrammi FITS, su fondo verde petrolio stellato.
Disegnata a 2048 px e ridotta a 512 (bordi morbidi). © 2026 Edoardo Battistel"""
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

N = 2048
S = N / 512.0
random.seed(7)
rng = np.random.default_rng(7)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def s(v):
    return int(round(v * S))


def maschera_arrotondata(r=96):
    m = Image.new("L", (N, N), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, N - 1, N - 1], radius=s(r), fill=255)
    return m


def sfondo():
    y, x = np.mgrid[0:N, 0:N].astype(np.float32)
    cx, cy = N * 0.42, N * 0.38
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / (N * 0.85)
    d = np.clip(d, 0, 1)
    centro = np.array([18, 92, 102], np.float32)       # verde petrolio
    bordo = np.array([4, 22, 30], np.float32)
    img = centro[None, None, :] * (1 - d[..., None]) ** 1.4 + bordo[None, None, :] * (1 - (1 - d[..., None]) ** 1.4)
    # velo di nebulosa tenue in alto a destra
    neb = np.exp(-(((x - N * 0.78) / (N * 0.22)) ** 2 + ((y - N * 0.2) / (N * 0.16)) ** 2))
    img += neb[..., None] * np.array([20, 40, 45], np.float32)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def stelle(img, n=260, zona=None):
    strato = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(strato)
    for _ in range(n):
        x, y = random.uniform(0, N), random.uniform(0, N)
        if zona and zona(x, y):
            continue
        r = random.choice([1, 1, 1, 2, 2, 3]) * S * 0.45
        a = random.randint(110, 255)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(235, 250, 255, a))
    img.alpha_composite(strato)


def stella_croce(img, x, y, l, col=(255, 255, 255)):
    strato = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(strato)
    for k in range(10, 0, -1):
        w = s(0.35 * k)
        a = int(28 * (11 - k))
        d.line([x - s(l), y, x + s(l), y], fill=col + (a,), width=max(1, w))
        d.line([x, y - s(l), x, y + s(l)], fill=col + (a,), width=max(1, w))
    alone = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(alone).ellipse([x - s(9), y - s(9), x + s(9), y + s(9)], fill=col + (200,))
    img.alpha_composite(alone.filter(ImageFilter.GaussianBlur(s(5))))
    img.alpha_composite(strato.filter(ImageFilter.GaussianBlur(S * 0.6)))


def fotogramma(img, cx, cy, w, h, ang, colore_bordo, alfa, contenuto=None, scritta=None):
    """Un 'fotogramma' FITS: pannello scuro con bordo luminoso e righe d'intestazione."""
    W, H = s(w), s(h)
    p = Image.new("RGBA", (W + s(40), H + s(40)), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    o = s(20)
    # ombra
    om = Image.new("RGBA", p.size, (0, 0, 0, 0))
    ImageDraw.Draw(om).rounded_rectangle([o + s(6), o + s(10), o + W + s(6), o + H + s(10)], radius=s(18),
                                         fill=(0, 0, 0, int(140 * alfa)))
    p.alpha_composite(om.filter(ImageFilter.GaussianBlur(s(8))))
    d.rounded_rectangle([o, o, o + W, o + H], radius=s(18), fill=(10, 34, 44, int(235 * alfa)),
                        outline=colore_bordo + (int(255 * alfa),), width=s(4))
    # "immagine" nel fotogramma
    if contenuto is not None:
        c = contenuto.resize((W - s(28), H - s(64)), Image.LANCZOS)
        m = Image.new("L", c.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, c.size[0] - 1, c.size[1] - 1], radius=s(10), fill=int(255 * alfa))
        p.paste(c, (o + s(14), o + s(14)), m)
    # righe dell'intestazione
    for k in range(3):
        y0 = o + H - s(40) + k * s(10)
        lun = [0.62, 0.45, 0.3][k]
        d.rounded_rectangle([o + s(16), y0, o + s(16) + int((W - s(32)) * lun), y0 + s(5)], radius=s(3),
                            fill=colore_bordo + (int(170 * alfa),))
    if scritta:
        f = ImageFont.truetype(FONT, s(30))
        tw = d.textlength(scritta, font=f)
        d.rounded_rectangle([o + s(14), o + s(14), o + s(28) + tw, o + s(54)], radius=s(8),
                            fill=(8, 24, 32, 210))
        d.text((o + s(21), o + s(15)), scritta, font=f, fill=(255, 214, 120, int(255 * alfa)))
    p = p.rotate(ang, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(p, (int(s(cx) - p.size[0] / 2), int(s(cy) - p.size[1] / 2)))


def nebulosa(dim, seme=3, galassia=True):
    """Campo colorato: nebulosa magenta/blu/oro con una galassia a spirale e stelle."""
    r = np.random.default_rng(seme)
    y, x = np.mgrid[0:dim, 0:dim].astype(np.float32) / dim - 0.5
    img = np.zeros((dim, dim, 3), np.float32)
    img += np.array([6, 10, 28], np.float32)
    nuvole = [((-0.18, 0.12), 0.28, (190, 40, 160)), ((0.2, -0.15), 0.25, (40, 120, 255)),
              ((0.05, 0.25), 0.22, (255, 150, 40)), ((-0.25, -0.22), 0.2, (30, 200, 210))]
    for (cx, cy), rr, col in nuvole:
        g = np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * rr ** 2))
        img += g[..., None] * np.array(col, np.float32) * 0.55
    # filamenti
    rumore = r.normal(size=(6, 6)).astype(np.float32)
    rumore = np.array(Image.fromarray(((rumore - rumore.min()) / np.ptp(rumore) * 255).astype(np.uint8))
                      .resize((dim, dim), Image.BICUBIC)).astype(np.float32) / 255
    img *= (0.7 + 0.6 * rumore[..., None])
    if galassia:
        ang = np.arctan2(y * 1.9, x) + 0.6
        rad = np.sqrt(x ** 2 + (y * 1.9) ** 2)
        bracci = np.clip(np.cos(2 * ang - 9 * rad), 0, 1) ** 2 * np.exp(-rad / 0.22)
        nucleo = np.exp(-rad ** 2 / 0.006)
        img += (bracci * 260)[..., None] * np.array([0.8, 0.88, 1.0], np.float32)
        img += (nucleo * 380)[..., None] * np.array([1.0, 0.92, 0.75], np.float32)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(dim / 120))
    d = ImageDraw.Draw(im)
    for _ in range(dim // 14):
        px, py = r.uniform(0, dim, 2)
        rr = r.choice([1.0, 1.4, 2.0, 2.8]) * dim / 512 * 2
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=(255, 255, 255))
    return im


def lente(img, cx, cy, R):
    cx, cy, R = s(cx), s(cy), s(R)
    # manico (dietro alla ghiera)
    ang = math.radians(45)
    x0, y0 = cx + (R + s(6)) * math.cos(ang), cy + (R + s(6)) * math.sin(ang)
    x1, y1 = cx + (R + s(118)) * math.cos(ang), cy + (R + s(118)) * math.sin(ang)
    ombra = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(ombra).line([x0 + s(10), y0 + s(14), x1 + s(10), y1 + s(14)], fill=(0, 0, 0, 150), width=s(56))
    img.alpha_composite(ombra.filter(ImageFilter.GaussianBlur(s(10))))
    man = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    dm = ImageDraw.Draw(man)
    dm.line([x0, y0, x1, y1], fill=(120, 72, 28, 255), width=s(54))
    dm.line([x0, y0, x1, y1], fill=(196, 128, 52, 255), width=s(40))
    dm.line([x0 - s(8), y0 + s(8), x1 - s(8), y1 + s(8)], fill=(240, 186, 110, 200), width=s(8))
    for e in (x1, y1),:
        dm.ellipse([e[0] - s(27), e[1] - s(27), e[0] + s(27), e[1] + s(27)], fill=(150, 92, 36, 255))
    # raccordo metallico
    xr, yr = cx + (R + s(40)) * math.cos(ang), cy + (R + s(40)) * math.sin(ang)
    dm.line([x0, y0, xr, yr], fill=(205, 215, 222, 255), width=s(64))
    dm.line([x0 - s(10), y0 + s(10), xr - s(10), yr + s(10)], fill=(250, 252, 255, 220), width=s(10))
    img.alpha_composite(man)
    # ombra della ghiera
    om = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(om).ellipse([cx - R - s(28) + s(12), cy - R - s(28) + s(18), cx + R + s(28) + s(12),
                                cy + R + s(28) + s(18)], fill=(0, 0, 0, 170))
    img.alpha_composite(om.filter(ImageFilter.GaussianBlur(s(14))))
    # vetro con la nebulosa
    vetro = nebulosa(2 * R, seme=11).convert("RGBA")
    m = Image.new("L", (2 * R, 2 * R), 0)
    ImageDraw.Draw(m).ellipse([0, 0, 2 * R - 1, 2 * R - 1], fill=255)
    img.paste(vetro, (cx - R, cy - R), m)
    # vignettatura e riflesso del vetro
    y, x = np.mgrid[0:2 * R, 0:2 * R].astype(np.float32) - R
    rr = np.sqrt(x ** 2 + y ** 2) / R
    vign = (np.clip((rr - 0.7) / 0.3, 0, 1) ** 2 * 150).astype(np.uint8)
    vl = Image.new("RGBA", (2 * R, 2 * R), (0, 10, 20, 0))
    vl.putalpha(Image.fromarray(vign).point(lambda v: v) )
    tmp = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    tmp.paste(vl, (cx - R, cy - R), m)
    img.alpha_composite(tmp)
    rif = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    dr = ImageDraw.Draw(rif)
    dr.arc([cx - R * 0.84, cy - R * 0.84, cx + R * 0.84, cy + R * 0.84], 205, 250, fill=(255, 255, 255, 120),
           width=s(10))
    img.alpha_composite(rif.filter(ImageFilter.GaussianBlur(s(4))))
    # ghiera metallica
    g = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    dg = ImageDraw.Draw(g)
    dg.ellipse([cx - R - s(28), cy - R - s(28), cx + R + s(28), cy + R + s(28)], outline=(150, 162, 172, 255),
               width=s(30))
    dg.ellipse([cx - R - s(24), cy - R - s(24), cx + R + s(24), cy + R + s(24)], outline=(222, 230, 236, 255),
               width=s(16))
    dg.arc([cx - R - s(22), cy - R - s(22), cx + R + s(22), cy + R + s(22)], 190, 280, fill=(255, 255, 255, 255),
           width=s(8))
    dg.ellipse([cx - R - s(2), cy - R - s(2), cx + R + s(2), cy + R + s(2)], outline=(90, 100, 110, 255), width=s(5))
    img.alpha_composite(g)
    # alone luminoso attorno alla ghiera
    al = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(al).ellipse([cx - R - s(36), cy - R - s(36), cx + R + s(36), cy + R + s(36)],
                               outline=(120, 240, 230, 90), width=s(10))
    img.alpha_composite(al.filter(ImageFilter.GaussianBlur(s(9))))


def crea(cartella):
    cartella = Path(cartella)
    img = sfondo()
    stelle(img)
    # pila di fotogrammi FITS (l'archivio), in alto a sinistra
    pic1 = nebulosa(400, seme=21, galassia=False)
    pic2 = nebulosa(400, seme=5)
    fotogramma(img, 138, 168, 196, 176, 16, (110, 200, 200), 0.75, nebulosa(400, seme=33, galassia=False))
    fotogramma(img, 152, 156, 196, 176, 6, (150, 225, 220), 0.9, pic1)
    fotogramma(img, 166, 146, 196, 176, -5, (255, 214, 120), 1.0, pic2, scritta="FITS")
    stella_croce(img, s(432), s(84), 30)
    stella_croce(img, s(70), s(408), 22, (190, 240, 255))
    # lente
    lente(img, 306, 292, 106)
    # angoli arrotondati
    fondo = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    fondo.paste(img, (0, 0), maschera_arrotondata())
    finale = fondo.resize((512, 512), Image.LANCZOS)
    finale.save(cartella / "ricerca_archivi.png", optimize=True)
    finale.save(cartella / "ricerca_archivi.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                        (128, 128), (256, 256)])
    return cartella / "ricerca_archivi.png"


if __name__ == "__main__":
    print(crea(sys.argv[1] if len(sys.argv) > 1 else "."))
