#!/usr/bin/env python3
"""Buduje runescript.ttf z plików svg/Letter=..., Name=..., Width=....svg

Wszystkie glify to outline (ścieżki ze stroke). Stroke jest ekspandowany do konturu
za pomocą shapely. Zachowujemy pozycję Y liter względem viewboxa (globalna,
jednakowa transformacja), a advance width bierzemy z parametru Width z nazwy pliku.
"""

import glob
import os
import re

from fontTools import agl
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path

try:
    from shapely.geometry import LineString, LinearRing, Polygon, MultiPolygon
    from shapely.ops import unary_union
except ImportError:
    raise SystemExit(
        "Brak biblioteki shapely — zainstaluj:\n"
        "  pip install shapely\n"
        "Shapely jest wymagana do ekspansji stroke do konturu."
    )

# --- stałe układu współrzędnych -------------------------------------------------
UPM = 1400          # units per em
VIEWBOX = 16        # rozmiar viewboxa w px
LEFT_PAD = 3.5      # główna część litery zaczyna się 3.5px od lewej -> viewbox x=3.5 => font x=0
SPACE_PX = 5        # advance spacji w px

# Wysokość inku (od najniższego do najwyższego piksela glifu) jako ułamek em.
# To globalna „siła powiększenia" czcionki: przy line-height:1 odległość między
# liniami bazowymi wynosi dokładnie 1 em, więc
#   < 1.0  -> między wierszami zostaje prześwit,
#   = 1.0  -> glify sąsiednich wierszy stykają się końcami,
#   > 1.0  -> glify nachodzą na sąsiedni wiersz (o (ratio-1) em).
# Skaluje wszystko jednorodnie — także advance, więc proporcje liter zostają.
INK_EM_RATIO = 1.1

SCALE = 100         # jednostek fontu na 1px viewboxa; przeliczane w resolve_metrics()

# Linia bazowa: svg-y, które trafia na baseline fontu (y=0). Litery na niej „siadają",
# dzięki czemu znaki z fallbacku (cyfry, spoza runów) wyrównują się z runami.
# None = auto: dolna krawędź inku liter (tak, jak wielkie litery siadają na baseline).
BASELINE_PX = None
# Ręczne dostrojenie pionu względem powyższego: dodatnie = litery w DÓŁ, ujemne = w GÓRĘ (px).
Y_SHIFT_PX = 0.5

# poniższe wylicza main() z geometrii glifów (patrz resolve_metrics):
_baseline = float(VIEWBOX)   # domyślnie dół viewboxa; nadpisywane po wczytaniu SVG
ASCENT = UPM                 # baseline -> najwyższy ink
DESCENT = 0                  # baseline -> najniższy ink (zwykle 0)

# --- domyślne parametry rozstawu (można nadpisać flagami CLI) --------------------
LETTER_SPACE_PX = 0  # całkowity odstęp doliczany do advance litery (px); dzielony po połowie L/P
MONO_PX = 9          # szerokość komórki w wariancie monospace (px)

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "svg")
# Artefakty lądują w public/ — Vite serwuje ten katalog spod „/" i kopiuje go
# do dist/ przy budowaniu. Pliki pobierane w runtime (fetch/FontFace) muszą tam
# leżeć, bo Vite nie widzi ich w grafie importów i inaczej by ich nie wydał.
PUBLIC_DIR = os.path.join(HERE, "public")
OUT = os.path.join(PUBLIC_DIR, "runescript.ttf")                 # wariant proporcjonalny
OUT_MONO = os.path.join(PUBLIC_DIR, "runescript-monospace.ttf")  # wariant monospace

# Strona statyczna dla GitHub Pages ląduje w dist/ — jeden samowystarczalny plik:
# fonty i lista znaków wklejone w HTML, więc nie ma żadnych pobrań w runtime.
DIST_DIR = os.path.join(HERE, "dist")
TEMPLATE_HTML = os.path.join(HERE, "index.html")

FNAME_RE = re.compile(
    r"Letter=(?P<letter>.+?),\s*Name=(?P<name>.*?),\s*Width=(?P<width>[\d.]+)\.svg$")
PATH_TAG_RE = re.compile(r"<path\b[^>]*>", re.DOTALL)   # każdy element <path ...>
D_ATTR_RE = re.compile(r'\bd="([^"]+)"', re.DOTALL)     # atrybut d wewnątrz taga
STROKE_WIDTH_RE = re.compile(r'\bstroke-width="([^"]+)"')
STROKE_LINEJOIN_RE = re.compile(r'\bstroke-linejoin="([^"]+)"')
STROKE_LINECAP_RE = re.compile(r'\bstroke-linecap="([^"]+)"')
STROKE_MITERLIMIT_RE = re.compile(r'\bstroke-miterlimit="([^"]+)"')
LETTER_UNI_RE = re.compile(r"^[Uu]\+?([0-9A-Fa-f]{4,6})$")  # zapis kodowy, np. U+002F

# Domyślne parametry obrysu — wartości domyślne wg specyfikacji SVG (nadpisywane
# atrybutami z <path>, jeśli występują). Trzymanie się defaultów SVG sprawia,
# że glif w foncie jest geometrycznie identyczny z podglądem pliku źródłowego.
STROKE_WIDTH_PX = 1.0    # SVG: stroke-width="1"
STROKE_LINEJOIN = "miter"   # SVG: ostre narożniki (nie zaokrąglone!)
STROKE_LINECAP = "butt"     # SVG: końce ścięte płasko
STROKE_MITERLIMIT = 4.0     # SVG: stroke-miterlimit="4"

# mapowanie nazw SVG -> stałe shapely (buffer join_style / cap_style)
_JOIN_STYLE = {"miter": 2, "round": 1, "bevel": 3}
_CAP_STYLE = {"butt": 2, "round": 1, "square": 3}


def to_font(x, y):
    """SVG (y w dół, origin lewy-górny) -> font (y w górę). Baseline na svg-y=_baseline,
    ta sama globalna transformacja dla wszystkich glifów (zachowuje wzajemne pozycje Y)."""
    return ((x - LEFT_PAD) * SCALE, (_baseline - y) * SCALE)


def ink_y_bounds(files):
    """Zwraca (min_y, max_y) współrzędnych svg-y ze wszystkich ścieżek — pionowy zasięg inku.

    Obrys rozlewa się o pół grubości poza linię środkową ścieżki, więc zasięg
    surowych punktów rozszerzamy o stroke-width/2 — inaczej baseline wypadłby
    w środku dolnej kreski zamiast pod nią."""
    ymin = ymax = None
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            svg = fh.read()
        for tag in PATH_TAG_RE.findall(svg):
            dm = D_ATTR_RE.search(tag)
            if not dm:
                continue
            half = stroke_params(tag)[0] / 2.0
            rec = RecordingPen()
            parse_path(dm.group(1), rec)
            for _op, args in rec.value:
                for (_x, y) in args:
                    lo, hi = y - half, y + half
                    ymin = lo if ymin is None else min(ymin, lo)
                    ymax = hi if ymax is None else max(ymax, hi)
    return ymin, ymax


def resolve_metrics(files, y_shift=Y_SHIFT_PX, ink_em_ratio=INK_EM_RATIO):
    """Ustala skalę, linię bazową i metryki pionowe (ascent/descent) z geometrii glifów.

    y_shift przesuwa litery w pionie: dodatnie = w dół, ujemne = w górę (px).
    ink_em_ratio ustala, jaki ułamek em zajmuje wysokość inku — czyli globalny
    rozmiar czcionki względem wiersza (patrz komentarz przy INK_EM_RATIO).
    Ustawia globalne SCALE, _baseline, ASCENT, DESCENT."""
    global SCALE, _baseline, ASCENT, DESCENT
    ymin, ymax = ink_y_bounds(files)
    if ymin is None:
        return

    # skala wynika z żądanego udziału inku w em — nie z rozmiaru viewboxa,
    # dzięki czemu zmiana grubości obrysu czy proporcji SVG nie rusza rozmiaru
    ink_px = ymax - ymin
    if ink_px > 0:
        SCALE = UPM * ink_em_ratio / ink_px

    base = float(ymax) if BASELINE_PX is None else float(BASELINE_PX)
    _baseline = base - y_shift                               # + y_shift => litery w dół
    ASCENT = round((_baseline - ymin) * SCALE)               # nad baseline (najwyższy ink)
    DESCENT = round(max(0.0, ymax - _baseline) * SCALE)      # pod baseline (rośnie przy y_shift>0)
    overlap = (ASCENT + DESCENT) / UPM
    print(f"Baseline: svg-y={_baseline:.2f}  y_shift={y_shift:+.2f}px  "
          f"ascent={ASCENT}  descent={DESCENT}  (ink svg-y {ymin:.2f}..{ymax:.2f})")
    print(f"Skala: {SCALE:.2f} jedn./px  ink/em={overlap:.3f}  "
          f"(przy line-height:1 wiersze {'nachodzą' if overlap > 1 else 'nie stykają się'} "
          f"o {abs(overlap - 1) * 100:.1f}% em)")


def stroke_params(tag):
    """Czyta parametry obrysu z atrybutów tagu <path>, z domyślnymi wartościami SVG.
    Zwraca (width_px, join_style, cap_style, mitre_limit) — już w stałych shapely."""
    m = STROKE_WIDTH_RE.search(tag)
    try:
        width = float(m.group(1)) if m else STROKE_WIDTH_PX
    except ValueError:
        width = STROKE_WIDTH_PX

    m = STROKE_LINEJOIN_RE.search(tag)
    join = _JOIN_STYLE.get(m.group(1).strip() if m else STROKE_LINEJOIN, _JOIN_STYLE["miter"])

    m = STROKE_LINECAP_RE.search(tag)
    cap = _CAP_STYLE.get(m.group(1).strip() if m else STROKE_LINECAP, _CAP_STYLE["butt"])

    m = STROKE_MITERLIMIT_RE.search(tag)
    try:
        limit = float(m.group(1)) if m else STROKE_MITERLIMIT
    except ValueError:
        limit = STROKE_MITERLIMIT

    return width, join, cap, limit


def _flatten(p0, args, op, steps=8):
    """Zamienia segment krzywej na łamaną (SVG-e runiczne mają same proste,
    ale gdyby pojawiła się krzywa, nie chcemy po cichu gubić jej kształtu)."""
    if op == "curveTo" and len(args) == 3:
        c1, c2, p3 = args
        return [(
            (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * p3[0],
            (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * p3[1],
        ) for t in (i / steps for i in range(1, steps + 1))]
    if op == "qCurveTo" and len(args) == 2 and args[1] is not None:
        c, p2 = args
        return [(
            (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p2[0],
            (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p2[1],
        ) for t in (i / steps for i in range(1, steps + 1))]
    return [args[-1]]


def parse_subpaths(d):
    """Rozbija atrybut 'd' na OSOBNE subpathy (każde 'M' zaczyna nowy).

    To kluczowe dla poprawnego obrysu: łączenie wszystkich 'M' w jedną łamaną
    dorysowywało odcinki, których w źródle nie ma (np. runa Ansuz to trzy
    rozłączne kreski wychodzące ze wspólnego punktu).

    Zwraca listę (punkty_svg, closed) — closed=True dla subpathów zamkniętych 'Z'."""
    rec = RecordingPen()
    parse_path(d, rec)

    subpaths = []
    current = None

    def flush(closed):
        if current and len(current) >= 2:
            subpaths.append((current, closed))

    for op, args in rec.value:
        if op == "moveTo":
            flush(False)
            current = [args[0]]
        elif current is None:
            continue
        elif op == "lineTo":
            current.append(args[0])
        elif op in ("qCurveTo", "curveTo"):
            current.extend(_flatten(current[-1], args, op))
        elif op == "closePath":
            flush(True)
            current = None
        elif op == "endPath":
            flush(False)
            current = None
    flush(False)
    return subpaths


def _dedup(points):
    """Usuwa kolejne powtórzone punkty — shapely nie lubi zerowej długości segmentów."""
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def _rings_to_contours(geom):
    """Wyciąga obwiednie i otwory z Polygon/MultiPolygon, przelicza do przestrzeni fontu
    i ustawia kierunki pod regułę nonzero (obwiednia CCW, otwór CW)."""
    polys = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    elif hasattr(geom, "geoms"):   # GeometryCollection — bierzemy tylko wielokąty
        polys = [g for g in geom.geoms if isinstance(g, Polygon)]

    contours = []
    for poly in polys:
        for ring, want_ccw in [(poly.exterior, True)] + [(r, False) for r in poly.interiors]:
            pts = [to_font(*pt) for pt in ring.coords[:-1]]   # coords[-1] == coords[0]
            if len(pts) < 3:
                continue
            # to_font odwraca oś Y, więc orientacja z shapely nie jest miarodajna
            if (signed_area(pts) > 0) != want_ccw:
                pts.reverse()
            contours.append(pts)
    return contours


def extract_contours(d, stroke_width=None, join_style=None, cap_style=None, mitre_limit=None):
    """Zamienia obrys (stroke) ścieżki SVG na wypełnione kontury w przestrzeni fontu.

    Każdy subpath buforowany jest osobno (z geometrią złączy jak w SVG: mitre,
    butt caps), a wyniki sumowane przez unary_union — nakładające się kreski
    zlewają się w jeden kształt dokładnie tak, jak renderuje je przeglądarka."""
    if stroke_width is None:
        stroke_width = STROKE_WIDTH_PX
    if join_style is None:
        join_style = _JOIN_STYLE[STROKE_LINEJOIN]
    if cap_style is None:
        cap_style = _CAP_STYLE[STROKE_LINECAP]
    if mitre_limit is None:
        mitre_limit = STROKE_MITERLIMIT

    half = stroke_width / 2.0
    pieces = []
    for pts, closed in parse_subpaths(d):
        pts = _dedup(pts)
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])          # domknięcie: złącze mitre zamiast dwóch capów
        if len(pts) < 2:
            continue
        # LinearRing dla subpathów zamkniętych — złącze w punkcie startowym też jest mitre
        geom = LinearRing(pts) if (closed and len(pts) >= 4) else LineString(pts)
        pieces.append(geom.buffer(half, cap_style=cap_style,
                                  join_style=join_style, mitre_limit=mitre_limit))

    if not pieces:
        return []
    return _rings_to_contours(unary_union(pieces))


def signed_area(pts):
    """Pole ze znakiem (shoelace). >0 = CCW w układzie fontu (y w górę)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def point_in_polygon(pt, poly):
    """Ray casting. Zakładamy wielokąty proste (bez samoprzecięć)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xints = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xints:
                inside = not inside
        j = i
    return inside


def normalize_winding(contours, even_odd):
    """Ustawia kierunki konturów pod regułę nonzero (TrueType).

    even_odd=True  (fill-rule="evenodd" w źródle): odtwarzamy semantykę even-odd —
        kontury o parzystej głębokości zagnieżdżenia -> CCW, nieparzystej (dziury) -> CW.
    even_odd=False (domyślny nonzero): źródło ma już poprawne kierunki dla nonzero,
        więc zachowujemy autorską orientację bez zmian."""
    if not even_odd:
        return contours
    fixed = []
    for i, c in enumerate(contours):
        if len(c) < 3:
            fixed.append(c)
            continue
        # głębokość = w ilu innych konturach leży reprezentatywny punkt
        p = c[0]
        depth = 0
        for j, other in enumerate(contours):
            if j == i or len(other) < 3:
                continue
            if point_in_polygon(p, other):
                depth += 1
        want_ccw = (depth % 2 == 0)
        is_ccw = signed_area(c) > 0
        if want_ccw != is_ccw:
            c = list(reversed(c))
        fixed.append(c)
    return fixed


def build_glyph(contours):
    pen = TTGlyphPen(None)
    for c in contours:
        if len(c) < 2:
            continue
        pen.moveTo(c[0])
        for pt in c[1:]:
            pen.lineTo(pt)
        pen.closePath()
    return pen.glyph()


def layout(width_px, monospace, mono_px, letter_space_px):
    """Zwraca (advance_px, dx_px) dla litery o logicznej szerokości width_px.

    letter_space_px to CAŁKOWITY odstęp doliczany do advance, rozdzielony po połowie
    na lewą i prawą stronę (symetryczny bearing).

    - monospace: advance = mono_px + letter_space, litera wyśrodkowana w komórce.
    - proporcjonalnie: advance = width_px + letter_space, litera przesunięta w prawo
      o letter_space/2 (równy odstęp z lewej i z prawej)."""
    if monospace:
        total = mono_px + letter_space_px
        return total, (total - width_px) / 2.0
    return width_px + letter_space_px, letter_space_px / 2.0


def shift_contours(contours, dx_units):
    """Przesuwa wszystkie punkty konturów w poziomie o dx_units (jednostki fontu)."""
    if not dx_units:
        return contours
    return [[(x + dx_units, y) for (x, y) in c] for c in contours]


def glyph_name_for(letter):
    """Nazwa glifu wg standardu AGL (np. 'A', 'Aogonek', 'Lslash'), z fallbackiem na
    'uniXXXX'. Nazwy glifów w tablicy post muszą być ASCII — użycie samych znaków
    (np. 'Ą') wywala kompilację (post 2.0 koduje latin-1)."""
    cp = ord(letter)
    return agl.UV2AGL.get(cp) or "uni%04X" % cp


def resolve_letter(token):
    """Zamienia wartość pola Letter= na pojedynczy znak docelowy. Obsługuje:
      - literalny znak: 'A', 'Ą', '.', '-' (gdy da się go zapisać w nazwie pliku),
      - nazwę glifu AGL: 'slash', 'period', 'question', 'colon', 'asterisk'...
        (dla znaków ZABRONIONYCH w nazwach plików: / \\ : * ? " < > |),
      - zapis kodowy: 'U+002F' / 'u002F' (uniwersalny fallback dla dowolnego znaku).
    Zwraca znak (str dł. 1) albo None, gdy nie rozpoznano."""
    token = token.strip()
    if len(token) == 1:
        return token
    m = LETTER_UNI_RE.match(token)
    if m:
        return chr(int(m.group(1), 16))
    if token in agl.AGL2UV:
        return chr(agl.AGL2UV[token])
    return None


def build_font(svg_files, out_path, family_name, monospace, mono_px, letter_space):
    """Buduje jeden wariant fontu z listy plików SVG i zapisuje go do out_path.

    family_name musi być unikalne per wariant, żeby oba pliki dało się jednocześnie
    zainstalować w systemie bez konfliktu (np. 'RuneScript' vs 'RuneScript Monospace')."""
    glyphs = {}          # glyphName -> TTGlyph
    advances = {}        # glyphName -> advance width (units)
    cmap = {}            # codepoint -> glyphName
    order = [".notdef"]

    # advance spacji/.notdef: w monospace = stała komórka, inaczej = SPACE_PX; + odstęp
    space_adv_px = (mono_px if monospace else SPACE_PX) + letter_space

    # .notdef (pusty) i space
    glyphs[".notdef"] = TTGlyphPen(None).glyph()
    advances[".notdef"] = round(space_adv_px * SCALE)
    glyphs["space"] = TTGlyphPen(None).glyph()
    advances["space"] = round(space_adv_px * SCALE)
    cmap[0x20] = "space"
    order.append("space")

    mode = f"MONOSPACE {mono_px}px" if monospace else "proporcjonalny"
    print(f"\n=== {family_name} — {mode}, letter-space {letter_space}px -> {os.path.basename(out_path)} ===")
    print(f"{'litera':6} {'Width':>6} {'advance':>8} {'kontury':>8}")
    print("-" * 32)

    for path in svg_files:
        base = os.path.basename(path)
        m = FNAME_RE.search(base)
        if not m:
            print(f"POMIJAM (zła nazwa): {base}")
            continue
        letter = resolve_letter(m.group("letter"))
        if letter is None:
            print(f"POMIJAM (nierozpoznana nazwa znaku {m.group('letter')!r} — użyj AGL lub U+XXXX): {base}")
            continue
        width_px = float(m.group("width"))

        with open(path, "r", encoding="utf-8") as fh:
            svg = fh.read()

        # Czytamy WSZYSTKIE <path> (bazowy kształt + osobne diakrytyki dla Ą/Ć/Ź...),
        # każdy z jego własnym stroke-width; kontury łączymy w jeden glif.
        contours = []
        for tag in PATH_TAG_RE.findall(svg):
            dm = D_ATTR_RE.search(tag)
            if not dm:
                continue
            sw, join, cap, limit = stroke_params(tag)
            # kierunki konturów ustawia już extract_contours (obwiednia CCW / otwór CW)
            contours.extend(extract_contours(dm.group(1), stroke_width=sw,
                                             join_style=join, cap_style=cap,
                                             mitre_limit=limit))
        if not contours:
            print(f"POMIJAM (brak <path d>): {base}")
            continue

        adv_px, dx_px = layout(width_px, monospace, mono_px, letter_space)
        contours = shift_contours(contours, dx_px * SCALE)

        glyph_name = glyph_name_for(letter)  # 'A', 'Aogonek', 'Lslash', ...
        if glyph_name in glyphs:
            print(f"POMIJAM (duplikat litery {letter!r} U+{ord(letter):04X} -> {glyph_name}): {base}")
            continue
        glyphs[glyph_name] = build_glyph(contours)
        advances[glyph_name] = round(adv_px * SCALE)
        order.append(glyph_name)

        cp = ord(letter)
        cmap[cp] = glyph_name                 # wielka litera
        if letter.isalpha():
            cmap[ord(letter.lower())] = glyph_name  # mała litera -> ta sama runa

        print(f"{letter:6} {width_px:6.1f} {advances[glyph_name]:8d} {len(contours):8d}")

    # --- złożenie fontu ---------------------------------------------------------
    ps_name = family_name.replace(" ", "")
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)

    # hmtx: (advance, lsb) — lsb wyliczy się z glifu, ale podajemy jawnie left bearing
    metrics = {}
    glyf = fb.font["glyf"]
    for name in order:
        adv = advances.get(name, round(SPACE_PX * SCALE))
        g = glyf[name]
        if g.numberOfContours > 0:
            xmin = g.xMin
        else:
            xmin = 0
        metrics[name] = (adv, xmin)
    fb.setupHorizontalMetrics(metrics)

    fb.setupHorizontalHeader(ascent=ASCENT, descent=-DESCENT)
    fb.setupNameTable({
        "familyName": family_name,
        "styleName": "Regular",
        "uniqueFontIdentifier": f"{family_name} Regular; 1.0",
        "fullName": f"{family_name} Regular",
        "version": "Version 1.0",
        "psName": f"{ps_name}-Regular",
    })
    # fsSelection: bit6 REGULAR (0x40) — WYMAGANE przez Windows dla fontu Regular.
    # Bez tego bitu Windows odrzuca plik jako "nieprawidłowy typ czcionki".
    fb.setupOS2(
        version=4,
        fsSelection=0x0040,        # REGULAR
        usWeightClass=400,
        usWidthClass=5,
        achVendID="RUNE",
        fsType=0,                  # brak ograniczeń osadzania (Installable)
        sTypoAscender=ASCENT,
        sTypoDescender=-DESCENT,
        sTypoLineGap=0,
        usWinAscent=ASCENT,
        usWinDescent=DESCENT,
    )
    fb.font["head"].macStyle = 0   # Regular (spójne z fsSelection)
    fb.setupPost()
    fb.font["post"].isFixedPitch = 1 if monospace else 0  # sygnalizuje monospace aplikacjom
    fb.save(out_path)
    print("-" * 32)
    print(f"Zapisano: {out_path}  ({len(order)} glifów, {len(cmap)} kodów w cmap)")


def sorted_glyph_entries(files):
    """Zwraca posortowaną listę {char, name, width} dla wszystkich znaków z plików SVG:
    alfabety (A-Z, a-z, diakrytyki w porządku polskim) → cyfry (0-9) → znaki specjalne.

    'name' to pole Name= z nazwy pliku (nazwa runy, np. 'Ansuz') — używa go strona
    prezentacyjna jako podpis pod znakiem."""
    entries = []
    seen = set()
    for path in files:
        base = os.path.basename(path)
        m = FNAME_RE.search(base)
        if not m:
            continue
        letter = resolve_letter(m.group("letter"))
        if not letter or letter in seen:
            continue
        seen.add(letter)
        entries.append({
            "char": letter,
            "name": m.group("name").strip(),
            "width": float(m.group("width")),
        })

    # porządek alfabetów ASCII + polskie znaki w porządku zgodnym z polskim alfabetem
    # ASCII: A-Z, a-z
    # Polskie (w porządku polskim): Ą Ć Ę Ł Ń Ó Ś Ź Ż (i ich małe wersje)
    POLISH_ORDER = 'ĄąĆćĘęŁłŃńÓóŚśŹźŻż'
    def sort_key(entry):
        ch = entry["char"]
        if ('A' <= ch <= 'Z') or ('a' <= ch <= 'z'):
            return (0, ch)  # ASCII: A-Z naturalnie, a-z naturalnie
        elif ch in POLISH_ORDER:
            return (1, POLISH_ORDER.index(ch))  # polskie w ustalonym porządku
        elif '0' <= ch <= '9':
            return (2, ch)  # cyfry
        else:
            return (3, ch)  # znaki specjalne
    return sorted(entries, key=sort_key)


def sorted_characters(files):
    """Posortowana lista samych znaków (bez metadanych)."""
    return [e["char"] for e in sorted_glyph_entries(files)]


def write_characters_json(files, out_path):
    """Zapisuje JSON ze znakami do wczytania przez frontend.

    'characters' to płaska lista znaków, 'glyphs' te same znaki z nazwą runy
    i szerokością — strona prezentacyjna korzysta z 'glyphs'."""
    import json
    entries = sorted_glyph_entries(files)
    payload = {"characters": [e["char"] for e in entries], "glyphs": entries}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"Zapisano znaki do: {out_path}")


def write_static_page(files, out_path):
    """Generuje samowystarczalny docs/index.html na bazie index.html.

    Do <head> wstrzykujemy window.__RUNESCRIPT_EMBED__ z listą znaków oraz
    fontami jako data: URL — strona działa wtedy z dowolnego katalogu i bez
    żadnego fetcha (na GitHub Pages nie ma public/ ani serwera dev)."""
    import base64
    import json

    entries = sorted_glyph_entries(files)
    fonts = {}
    for name in ("runescript.ttf", "runescript-monospace.ttf"):
        with open(os.path.join(PUBLIC_DIR, name), "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        fonts[name] = "data:font/ttf;base64," + data

    embed = {
        "characters": {"characters": [e["char"] for e in entries], "glyphs": entries},
        "fonts": fonts,
    }
    # </ w treści JSON-a zakończyłoby przedwcześnie tag <script>
    payload = json.dumps(embed, ensure_ascii=False).replace("</", "<\\/")

    with open(TEMPLATE_HTML, "r", encoding="utf-8") as fh:
        html = fh.read()
    if "</head>" not in html:
        raise SystemExit(f"Brak </head> w {TEMPLATE_HTML} — nie wiem, gdzie wstrzyknąć dane")
    html = html.replace(
        "</head>",
        f"  <script>window.__RUNESCRIPT_EMBED__ = {payload};</script>\n</head>", 1)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Zapisano stronę statyczną do: {out_path}")


def main(mono_px=MONO_PX, letter_space=LETTER_SPACE_PX, y_shift=Y_SHIFT_PX,
         ink_em_ratio=INK_EM_RATIO):
    files = sorted(glob.glob(os.path.join(SVG_DIR, "*.svg")))
    if not files:
        raise SystemExit(f"Brak plików SVG w {SVG_DIR}")
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    # ustal skalę, linię bazową i metryki pionowe z geometrii (zanim zbudujemy glify):
    resolve_metrics(files, y_shift, ink_em_ratio)

    # zawsze budujemy dwa artefakty:
    build_font(files, OUT, "RuneScript",
               monospace=False, mono_px=mono_px, letter_space=letter_space)
    build_font(files, OUT_MONO, "RuneScript Monospace",
               monospace=True, mono_px=mono_px, letter_space=letter_space)

    # wygeneruj JSON z posorowanymi znakami do frontend:
    write_characters_json(files, os.path.join(PUBLIC_DIR, "characters.json"))

    # strona statyczna dla GitHub Pages — wszystko wbudowane w jeden plik:
    write_static_page(files, os.path.join(DIST_DIR, "index.html"))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Buduje runescript.ttf (proporcjonalny) oraz runescript-monospace.ttf.")
    ap.add_argument("--mono-width", type=float, default=MONO_PX, metavar="PX",
                    help=f"szerokość komórki wariantu monospace w px (domyślnie {MONO_PX})")
    ap.add_argument("--letter-space", type=float, default=LETTER_SPACE_PX, metavar="PX",
                    help=f"całkowity odstęp doliczany do advance litery w px (domyślnie {LETTER_SPACE_PX})")
    ap.add_argument("--y-shift", type=float, default=Y_SHIFT_PX, metavar="PX",
                    help=f"ręczne przesunięcie liter w pionie: + w dół, - w górę (domyślnie {Y_SHIFT_PX})")
    ap.add_argument("--ink-ratio", type=float, default=INK_EM_RATIO, metavar="R",
                    help="globalny rozmiar czcionki: wysokość inku jako ułamek em; "
                         f">1 = glify nachodzą na sąsiedni wiersz (domyślnie {INK_EM_RATIO})")
    a = ap.parse_args()
    main(mono_px=a.mono_width, letter_space=a.letter_space, y_shift=a.y_shift,
         ink_em_ratio=a.ink_ratio)
