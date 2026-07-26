#!/usr/bin/env python3
"""Buduje czcionki RuneScript z plików svg/Letter=..., Name=..., Width=....svg

Wszystkie glify to outline (ścieżki ze stroke). Obrys rozwijany jest do konturu
własnym strokerem o STAŁEJ TOPOLOGII: liczba konturów i punktów zależy wyłącznie
od źródłowej ścieżki, nigdy od grubości obrysu. To warunek konieczny fontu
variable — mastery muszą być interpolowalne punkt po punkcie (patrz stroke_path).

Zachowujemy pozycję Y liter względem viewboxa (globalna, jednakowa transformacja),
a advance width bierzemy z parametru Width z nazwy pliku.

Artefakty: runescript.ttf, runescript-monospace.ttf (statyczne) oraz
runescript-variable.ttf z osią wagi wght.
"""

import glob
import math
import os
import re

from fontTools import agl
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path

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
INK_EM_RATIO = 1.15

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

# --- oś wagi fontu variable ------------------------------------------------------
# Mastery bierzemy WPROST z plików: każdy SVG niesie własne stroke-width dla swojej
# wagi (Weight= w nazwie pliku). Nic nie skalujemy — to warunek postawiony wprost:
# dana waga ma być odwzorowaniem 1:1 swojego pliku źródłowego.
# Nazwy stylów wg skali OpenType; wagi wykrywamy z nazw plików.
WEIGHT_STYLES = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
    600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}
DEFAULT_WEIGHT = 400   # master domyślny osi i źródło wariantów statycznych

# --- domyślne parametry rozstawu (można nadpisać flagami CLI) --------------------
LETTER_SPACE_PX = 0  # całkowity odstęp doliczany do advance litery (px); dzielony po połowie L/P
MONO_PX = 9          # szerokość komórki w wariancie monospace (px)

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "svg")
# Gotowe pliki czcionki — stąd bierze je też strona statyczna (wkleja je do HTML).
FONTS_DIR = os.path.join(HERE, "fonts")
OUT = os.path.join(FONTS_DIR, "runescript.ttf")                 # wariant proporcjonalny
OUT_MONO = os.path.join(FONTS_DIR, "runescript-monospace.ttf")  # wariant monospace
OUT_VAR = os.path.join(FONTS_DIR, "runescript-variable.ttf")    # oś wagi wght
OUT_MONO_VAR = os.path.join(FONTS_DIR, "runescript-monospace-variable.ttf")

# Komplet wag zapisany statycznie — po jednym pliku na wagę i wariant. Przydaje się
# tam, gdzie font variable nie wchodzi w grę (starsze systemy, część edytorów,
# osadzanie w dokumentach).
STATIC_DIR = os.path.join(HERE, "static")

# Strona statyczna dla GitHub Pages ląduje w docs/ (Source: branch main, katalog
# /docs) — jeden samowystarczalny plik:
# fonty i lista znaków wklejone w HTML, więc nie ma żadnych pobrań w runtime.
DOCS_DIR = os.path.join(HERE, "docs")
TEMPLATE_HTML = os.path.join(HERE, "index.html")

FNAME_RE = re.compile(
    r"Letter=(?P<letter>.+?),\s*Name=(?P<name>.*?),\s*Width=(?P<width>[\d.]+)"
    r",\s*Weight=(?P<weight>\d+)\.svg$")
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


def to_font(x, y):
    """SVG (y w dół, origin lewy-górny) -> font (y w górę). Baseline na svg-y=_baseline,
    ta sama globalna transformacja dla wszystkich glifów (zachowuje wzajemne pozycje Y)."""
    return ((x - LEFT_PAD) * SCALE, (_baseline - y) * SCALE)


def group_by_weight(files):
    """{waga: [ścieżki plików]} — po jednym komplecie glifów na wagę."""
    groups = {}
    for path in files:
        m = FNAME_RE.search(os.path.basename(path))
        if not m:
            print(f"POMIJAM (zła nazwa, brak Weight=?): {os.path.basename(path)}")
            continue
        groups.setdefault(int(m.group("weight")), []).append(path)
    return dict(sorted(groups.items()))


def ink_y_bounds(files):
    """Zwraca (min_y, max_y) svg-y faktycznego obrysu — pionowy zasięg inku.

    Mierzymy wyliczony kontur, a nie punkty ścieżki powiększone o pół grubości:
    złącze mitre potrafi wystawać poza tę granicę nawet kilkukrotnie, a przy
    grubym obrysie (master Bold) taki błąd oznaczałby przycięcie glifu przez
    usWinAscent/usWinDescent."""
    ymin = ymax = None
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            svg = fh.read()
        for tag in PATH_TAG_RE.findall(svg):
            dm = D_ATTR_RE.search(tag)
            if not dm:
                continue
            sw, limit = stroke_params(tag)
            for pts, closed in parse_subpaths(dm.group(1)):
                pts = _dedup(pts)
                if closed and len(pts) > 2 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                if len(pts) < 2:
                    continue
                for contour in stroke_pieces(pts, closed, sw, limit):
                    for (_x, y) in contour:
                        ymin = y if ymin is None else min(ymin, y)
                        ymax = y if ymax is None else max(ymax, y)
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


def vertical_extent(files):
    """(ascent, descent) w jednostkach fontu dla podanego kompletu plików (jednej wagi).

    Używa USTALONYCH już SCALE i _baseline, więc cięższa waga zmienia tylko zasięg
    pionowy — nie skalę rysunku. Dzięki temu mastery fontu variable różnią się
    grubością kreski, a nie wielkością liter."""
    ymin, ymax = ink_y_bounds(files)
    if ymin is None:
        return ASCENT, DESCENT
    return (round((_baseline - ymin) * SCALE),
            round(max(0.0, ymax - _baseline) * SCALE))


def stroke_params(tag):
    """Czyta parametry obrysu z atrybutów tagu <path>, z domyślnymi wartościami SVG.
    Zwraca (width_px, mitre_limit).

    Obsługujemy wyłącznie miter + butt (domyślne w SVG). Zaokrąglone złącza czy
    zakończenia wymagałyby wstawiania łuków o liczbie punktów zależnej od promienia,
    co zerwałoby interpolowalność masterów — dlatego zamiast po cichu je przybliżać,
    ostrzegamy."""
    m = STROKE_WIDTH_RE.search(tag)
    try:
        width = float(m.group(1)) if m else STROKE_WIDTH_PX
    except ValueError:
        width = STROKE_WIDTH_PX

    m = STROKE_MITERLIMIT_RE.search(tag)
    try:
        limit = float(m.group(1)) if m else STROKE_MITERLIMIT
    except ValueError:
        limit = STROKE_MITERLIMIT

    for regex, default, what in ((STROKE_LINEJOIN_RE, STROKE_LINEJOIN, "stroke-linejoin"),
                                 (STROKE_LINECAP_RE, STROKE_LINECAP, "stroke-linecap")):
        m = regex.search(tag)
        if m and m.group(1).strip() != default:
            print(f"  UWAGA: {what}=\"{m.group(1).strip()}\" nie jest obsługiwane "
                  f"(używam {default!r}) — łuki zerwałyby interpolację fontu variable")

    return width, limit


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
    """Usuwa kolejne powtórzone punkty — segment zerowej długości nie ma kierunku,
    więc nie dałoby się na nim wyznaczyć normalnej do odsunięcia obrysu."""
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def _unit(p0, p1):
    """Znormalizowany kierunek odcinka albo None dla odcinka zerowej długości."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 1e-12 else None


def stroke_pieces(pts, closed, stroke_width, mitre_limit):
    """Rozkłada obrys łamanej na WYPUKŁE kawałki (układ SVG, y w dół).

    Każdy segment daje prostokąt, każde złącze — klin (mitre albo bevel).
    Suma tych kawałków pod regułą nonzero to dokładnie obszar, który SVG maluje
    jako stroke: prostokąty pokrywają kreski, kliny domykają narożniki.

    Dlaczego nie jeden kontur „obwiednia + otwór": przy ostrym zagięciu punkt mitre
    po WEWNĘTRZNEJ stronie wybiega daleko poza narożnik i wycina w kresce wcięcie.
    Rozkład na wypukłe kawałki nie ma tego problemu, bo żaden kawałek nie sięga
    poza swój segment — a przy okazji znikają wszystkie przypadki brzegowe: mały
    romb wychodzi pełny, bo prostokąty się na siebie nakładają, a ścieżka
    krzyżująca się ze sobą (Dagaz) nie wymaga osobnej obsługi.

    Liczba kawałków i punktów zależy WYŁĄCZNIE od kształtu ścieżki (wybór
    mitre/bevel to funkcja samego kąta), więc mastery o różnych grubościach
    pozostają interpolowalne punkt po punkcie.

    Kawałki wychodzą w jednolitej orientacji (ujemne pole w układzie SVG), którą
    ustalamy z kierunku skrętu — a nie z pomiaru pola gotowego kawałka, bo przy
    złączu bliskim prostemu pole dąży do zera i pomiar bywa niestabilny."""
    half = stroke_width / 2.0
    n = len(pts)
    seg_count = n if closed else n - 1
    dirs = [_unit(pts[i], pts[(i + 1) % n]) for i in range(seg_count)]

    pieces = []

    # prostokąt na każdy segment (zakończenia butt powstają same: prostokąt
    # kończy się równo na końcu segmentu)
    for i, direction in enumerate(dirs):
        if direction is None:
            continue
        nx, ny = -direction[1] * half, direction[0] * half
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        pieces.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                       (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])

    # klin na każdym złączu wewnętrznym (dla ścieżki zamkniętej — na każdym)
    joints = range(n) if closed else range(1, n - 1)
    for i in joints:
        d1 = dirs[(i - 1) % seg_count] if closed else dirs[i - 1]
        d2 = dirs[i % seg_count] if closed else dirs[i]
        if d1 is None or d2 is None:
            continue
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        # klin siada po ZEWNĘTRZNEJ stronie skrętu; po wewnętrznej prostokąty
        # sąsiednich segmentów i tak już na siebie zachodzą
        side = -1.0 if cross > 0 else 1.0
        n1 = (-d1[1] * side, d1[0] * side)
        n2 = (-d2[1] * side, d2[0] * side)
        px, py = pts[i]
        a = (px + n1[0] * half, py + n1[1] * half)
        b = (px + n2[0] * half, py + n2[1] * half)

        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        length = math.hypot(mx, my)
        if length < 1e-12:
            apex = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)      # zawrócenie o 180°
        else:
            mx, my = mx / length, my / length
            k = 1.0 / max(mx * n1[0] + my * n1[1], 1e-12)
            if k <= mitre_limit:
                apex = (px + mx * half * k, py + my * half * k)
            else:
                # przekroczony limit -> bevel, czyli wierzchołek na krawędzi AB
                # (SVG robi dokładnie to samo); liczba punktów bez zmian
                apex = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

        wedge = [(px, py), a, apex, b]
        if cross > 0:
            wedge.reverse()          # jednolita orientacja z prostokątami
        pieces.append(wedge)

    return pieces


def stroke_path(pts, closed, stroke_width, mitre_limit):
    """Kawałki obrysu przeliczone do przestrzeni fontu.

    to_font odwraca oś Y, więc kawałki ujemne w układzie SVG stają się dodatnie
    (CCW) — czyli takie, jakich oczekuje reguła nonzero w TrueType."""
    return [[to_font(*p) for p in piece]
            for piece in stroke_pieces(pts, closed, stroke_width, mitre_limit)]


def extract_contours(d, stroke_width=None, mitre_limit=None):
    """Zamienia obrys (stroke) ścieżki SVG na wypełnione kontury w przestrzeni fontu.

    Grubość bierze się wprost z atrybutu stroke-width danego pliku — każda waga ma
    własny komplet SVG, więc nie ma tu żadnego skalowania."""
    if stroke_width is None:
        stroke_width = STROKE_WIDTH_PX
    if mitre_limit is None:
        mitre_limit = STROKE_MITERLIMIT

    width = stroke_width
    contours = []
    for pts, closed in parse_subpaths(d):
        pts = _dedup(pts)
        if closed and len(pts) > 2 and pts[0] == pts[-1]:
            pts = pts[:-1]               # pierścień bez powtórzonego punktu startowego
        if len(pts) < 2:
            continue
        contours.extend(stroke_path(pts, closed, width, mitre_limit))
    return contours


def signed_area(pts):
    """Pole ze znakiem (shoelace). >0 = CCW w układzie fontu (y w górę)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


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


def build_font(svg_files, out_path, family_name, monospace, mono_px, letter_space,
               style_name="Regular", weight_class=400,
               ascent=None, descent=None, verbose=True):
    """Buduje jeden wariant fontu z listy plików SVG. Zwraca gotowy TTFont;
    zapisuje go dodatkowo do out_path, o ile out_path nie jest None.

    family_name musi być unikalne per wariant, żeby oba pliki dało się jednocześnie
    zainstalować w systemie bez konfliktu (np. 'RuneScript' vs 'RuneScript Monospace').

    svg_files to komplet plików JEDNEJ wagi — grubość obrysu niesie sam plik.
    ascent/descent pozwalają narzucić wspólne metryki pionowe wszystkim masterom
    (domyślnie globalne ASCENT/DESCENT z resolve_metrics)."""
    if ascent is None:
        ascent = ASCENT
    if descent is None:
        descent = DESCENT

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
    if verbose:
        target = os.path.basename(out_path) if out_path else "(w pamięci)"
        print(f"\n=== {family_name} {style_name} — {mode}, "
              f"letter-space {letter_space}px -> {target} ===")
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
            sw, limit = stroke_params(tag)
            # kierunki konturów ustawia już extract_contours (obwiednia CCW / otwór CW)
            contours.extend(extract_contours(dm.group(1), stroke_width=sw,
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

        if verbose:
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

    fb.setupHorizontalHeader(ascent=ascent, descent=-descent)

    # Nazewnictwo wg schematu RIBBI + rodzina typograficzna (name ID 16/17).
    # Klasyczna rodzina OpenType mieści tylko cztery style (Regular/Bold/Italic/
    # BoldItalic), więc przy siedmiu wagach pozostałe trafiają do własnych podrodzin
    # w name ID 1, a pełną rodzinę podaje name ID 16. Bez tego Windows pokazuje wagi
    # jako osobne, konkurujące ze sobą czcionki i gubi je w menu wyboru kroju.
    is_bold = style_name == "Bold"
    ribbi = style_name in ("Regular", "Bold")
    ps_style = style_name.replace(" ", "")
    fb.setupNameTable({
        "familyName": family_name if ribbi else f"{family_name} {style_name}",
        "styleName": style_name if ribbi else "Regular",
        "typographicFamily": family_name,
        "typographicSubfamily": style_name,
        "uniqueFontIdentifier": f"{family_name} {style_name}; 1.0",
        "fullName": f"{family_name} {style_name}",
        "version": "Version 1.0",
        "psName": f"{ps_name}-{ps_style}",
    })
    # fsSelection: bit5 BOLD (0x20) albo bit6 REGULAR (0x40) — jeden z nich MUSI być
    # ustawiony, inaczej Windows odrzuca plik jako "nieprawidłowy typ czcionki".
    fb.setupOS2(
        version=4,
        fsSelection=0x0020 if is_bold else 0x0040,
        usWeightClass=weight_class,
        usWidthClass=5,
        achVendID="RUNE",
        fsType=0,                  # brak ograniczeń osadzania (Installable)
        sTypoAscender=ascent,
        sTypoDescender=-descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=descent,
    )
    fb.font["head"].macStyle = 1 if is_bold else 0   # spójne z fsSelection
    fb.setupPost()
    fb.font["post"].isFixedPitch = 1 if monospace else 0  # sygnalizuje monospace aplikacjom

    if out_path:
        fb.save(out_path)
        if verbose:
            print("-" * 32)
            print(f"Zapisano: {out_path}  ({len(order)} glifów, {len(cmap)} kodów w cmap)")
    return fb.font


def build_variable_font(groups, out_path, family_name, monospace, mono_px,
                        letter_space):
    """Skleja mastery w jeden font variable z osią wght.

    groups: {waga: [pliki SVG tej wagi]} — każdy master powstaje 1:1 ze swojego
    kompletu plików, bez skalowania czegokolwiek. Interpolację umożliwia to, że
    ścieżki (atrybut d) są we wszystkich wagach identyczne, a stroker wyprowadza
    liczbę punktów wyłącznie z kształtu ścieżki."""
    import tempfile
    from fontTools.designspaceLib import (AxisDescriptor, DesignSpaceDocument,
                                          InstanceDescriptor, SourceDescriptor)
    from fontTools.otlLib.builder import buildStatTable
    from fontTools import varLib

    weights = sorted(groups)
    default_wght = DEFAULT_WEIGHT if DEFAULT_WEIGHT in groups else weights[len(weights) // 2]

    # Metryki pionowe biorą się z NAJCIĘŻSZEJ wagi i są wspólne dla wszystkich:
    # hhea/OS/2 nie interpolują się bez tabeli MVAR, więc muszą objąć skrajną wagę,
    # inaczej najgrubszy wariant zostałby przycięty przez usWinAscent/usWinDescent.
    ascent, descent = vertical_extent(groups[weights[-1]])

    print(f"\n=== {family_name} — VARIABLE, oś wght {weights[0]}..{weights[-1]} "
          f"(domyślnie {default_wght}) -> {os.path.basename(out_path)} ===")
    print(f"Metryki pionowe z wagi {weights[-1]}: ascent={ascent} descent={descent}")

    doc = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name, axis.tag = "Weight", "wght"
    axis.minimum, axis.default, axis.maximum = weights[0], default_wght, weights[-1]
    doc.addAxis(axis)

    tmpdir = tempfile.mkdtemp(prefix="runescript-masters-")
    try:
        for wght in weights:
            style = WEIGHT_STYLES.get(wght, str(wght))
            font = build_font(groups[wght], None, family_name, monospace, mono_px,
                              letter_space, style_name=style, weight_class=wght,
                              ascent=ascent, descent=descent, verbose=False)
            path = os.path.join(tmpdir, f"master-{wght}.ttf")
            font.save(path)
            contours = sum(g.numberOfContours for g in font["glyf"].glyphs.values()
                           if g.numberOfContours > 0)
            points = sum(len(g.getCoordinates(font["glyf"])[0])
                         for g in font["glyf"].glyphs.values() if g.numberOfContours > 0)
            print(f"  master {style:10} wght={wght:<4} konturów={contours:<5} punktów={points}")

            source = SourceDescriptor()
            source.path = path
            source.name = f"master_{wght}"
            source.location = {"Weight": wght}
            if wght == default_wght:
                # domyślny master oddaje reszcie tabele niezmienne (name, cmap, post...)
                source.copyLib = source.copyInfo = True
                source.copyGroups = source.copyFeatures = True
            doc.addSource(source)

        for wght in weights:
            inst = InstanceDescriptor()
            inst.familyName = family_name
            inst.styleName = WEIGHT_STYLES.get(wght, str(wght))
            inst.name = f"{family_name} {inst.styleName}"
            inst.location = {"Weight": wght}
            doc.addInstance(inst)

        # optimize=False: domyślna optymalizacja IUP wyrzuca delty odtwarzalne
        # interpolacją, dopuszczając przy tym pół jednostki błędu na punkt.
        # Przy wymogu odwzorowania 1:1 nie chcemy tej tolerancji — a że glify są
        # z samych narożników, IUP i tak nie miałby czego uprościć, więc nic nie
        # kosztuje. (Reszta odchyłki instancji od mastera, ok. 0.5 jednostki na
        # 1400 UPM, to już kwantyzacja F2Dot14 pozycji na osi — cecha formatu,
        # nie do usunięcia. Pliki w static/ są jej wolne i są dokładne.)
        vf, _model, _masters = varLib.build(doc, optimize=False)
    finally:
        for name in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, name))
        os.rmdir(tmpdir)

    # STAT jest wymagana przez OpenType 1.8+ — bez niej Windows i aplikacje Adobe
    # potrafią nie pokazać osi albo źle nazwać instancje.
    buildStatTable(vf, [{
        "tag": "wght",
        "name": "Weight",
        "values": [
            {"value": w, "name": WEIGHT_STYLES.get(w, str(w)),
             **({"flags": 0x2} if w == default_wght else {})}
            for w in weights
        ],
    }])

    vf.save(out_path)
    axes = ", ".join(f"{a.axisTag} {a.minValue:g}..{a.maxValue:g}" for a in vf["fvar"].axes)
    print(f"Zapisano: {out_path}  (osie: {axes}; "
          f"instancje: {len(vf['fvar'].instances)}; "
          f"gvar dla {len(vf['gvar'].variations)} glifów)")
    return vf


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


def build_static_family(groups, ascent, descent, mono_px, letter_space):
    """Zapisuje do static/ komplet wag statycznie — każda waga w obu wariantach.

    Wszystkie dostają te same metryki pionowe, więc przełączenie wagi w tekście
    nie rusza interlinii. Nazwy plików idą konwencją Rodzina-Styl.ttf, żeby
    instalatory systemowe grupowały je w jedną rodzinę."""
    os.makedirs(STATIC_DIR, exist_ok=True)
    variants = [("RuneScript", "RuneScript", False),
                ("RuneScript Monospace", "RuneScriptMonospace", True)]

    print(f"\n=== komplet wag statycznie -> {os.path.basename(STATIC_DIR)}/ ===")
    written = []
    for weight in sorted(groups):
        style = WEIGHT_STYLES.get(weight, str(weight))
        for family, prefix, monospace in variants:
            path = os.path.join(STATIC_DIR, f"{prefix}-{style}.ttf")
            build_font(groups[weight], path, family, monospace=monospace,
                       mono_px=mono_px, letter_space=letter_space,
                       style_name=style, weight_class=weight,
                       ascent=ascent, descent=descent, verbose=False)
            written.append((os.path.basename(path), os.path.getsize(path)))

    for name, size in written:
        print(f"  {name:36} {size / 1024:6.1f} kB")
    print(f"Zapisano {len(written)} plików do: {STATIC_DIR}")


def master_stroke_widths(groups):
    """{waga: grubość obrysu} — reprezentatywna grubość każdego mastera.

    Bierzemy wartość najczęstszą w komplecie danej wagi: pojedyncza ścieżka
    (np. diakrytyk) bywa celowo cieńsza i nie powinna reprezentować całej wagi."""
    import collections
    widths = {}
    for weight, files in groups.items():
        counter = collections.Counter()
        for path in files:
            with open(path, "r", encoding="utf-8") as fh:
                svg = fh.read()
            for tag in PATH_TAG_RE.findall(svg):
                if D_ATTR_RE.search(tag):
                    counter[stroke_params(tag)[0]] += 1
        if counter:
            widths[weight] = counter.most_common(1)[0][0]
    return widths


def write_static_page(groups, out_path):
    """Generuje samowystarczalny docs/index.html na bazie index.html.

    Do <head> wstrzykujemy window.__RUNESCRIPT_EMBED__ z listą znaków, fontami
    jako data: URL oraz opisem masterów — strona działa wtedy z dowolnego katalogu
    i bez żadnego fetcha, także otwarta prosto z dysku."""
    import base64
    import json

    entries = sorted_glyph_entries(groups[DEFAULT_WEIGHT])
    fonts = {}
    for name in ("runescript.ttf", "runescript-monospace.ttf",
                 "runescript-variable.ttf", "runescript-monospace-variable.ttf"):
        with open(os.path.join(FONTS_DIR, name), "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        fonts[name] = "data:font/ttf;base64," + data

    widths = master_stroke_widths(groups)
    embed = {
        "characters": {"characters": [e["char"] for e in entries], "glyphs": entries},
        "fonts": fonts,
        # mastery osi wght — podgląd rozstawia je na skali wg grubości obrysu
        "masters": [{"weight": w,
                     "strokeWidth": widths.get(w),
                     "style": WEIGHT_STYLES.get(w, str(w))}
                    for w in sorted(groups)],
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
    os.makedirs(FONTS_DIR, exist_ok=True)

    groups = group_by_weight(files)
    if not groups:
        raise SystemExit("Żaden plik nie pasuje do wzorca "
                         "'Letter=..., Name=..., Width=..., Weight=....svg'")
    if DEFAULT_WEIGHT not in groups:
        raise SystemExit(f"Brak wagi domyślnej {DEFAULT_WEIGHT} — mam tylko {sorted(groups)}")

    counts = {w: len(f) for w, f in groups.items()}
    print("Wagi w źródle: " + ", ".join(f"{w} ({n} glifów)" for w, n in counts.items()))
    if len(set(counts.values())) > 1:
        print("  UWAGA: wagi mają różną liczbę glifów — mastery muszą mieć identyczny "
              "komplet, inaczej varLib odrzuci font")

    base = groups[DEFAULT_WEIGHT]
    weights = sorted(groups)

    # Skalę i linię bazową ustalamy RAZ, z wagi domyślnej. Gdyby liczyć je per waga,
    # cięższy master wychodziłby w innej skali i oś zmieniałaby wielkość liter,
    # zamiast samej grubości kreski.
    resolve_metrics(base, y_shift, ink_em_ratio)

    # Metryki pionowe wspólne dla CAŁEJ rodziny, wzięte z najcięższej wagi.
    # Gdyby każda waga miała własne, przełączenie na grubszą przesuwałoby wiersze,
    # a najcięższa i tak zostałaby przycięta przez usWinAscent/usWinDescent.
    ascent, descent = vertical_extent(groups[weights[-1]])
    print(f"Metryki wspólne dla rodziny (z wagi {weights[-1]}): "
          f"ascent={ascent} descent={descent}")

    # warianty statyczne w katalogu głównym fontów — z wagi domyślnej
    build_font(base, OUT, "RuneScript", monospace=False, mono_px=mono_px,
               letter_space=letter_space, ascent=ascent, descent=descent)
    build_font(base, OUT_MONO, "RuneScript Monospace", monospace=True, mono_px=mono_px,
               letter_space=letter_space, ascent=ascent, descent=descent)

    # warianty variable — po jednym masterze na wagę obecną w źródle
    build_variable_font(groups, OUT_VAR, "RuneScript Variable",
                        monospace=False, mono_px=mono_px, letter_space=letter_space)
    build_variable_font(groups, OUT_MONO_VAR, "RuneScript Monospace Variable",
                        monospace=True, mono_px=mono_px, letter_space=letter_space)

    # komplet wag statycznie
    build_static_family(groups, ascent, descent, mono_px, letter_space)

    # strona statyczna dla GitHub Pages — wszystko wbudowane w jeden plik:
    write_static_page(groups, os.path.join(DOCS_DIR, "index.html"))


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
