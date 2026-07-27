# RuneScript

Czcionka runiczna mapująca alfabet łaciński na runy — nie przez przypadkowe podobieństwo kształtu, lecz przez skojarzenie litery z historycznym brzmieniem runy.

## Idea

Każda runa została przypisana do współczesnej litery alfabetu łacińskiego na podstawie dźwięku, który historycznie jej odpowiadał. Tam, gdzie litery nie miały własnych run, glify powstały przez połączenie blisko brzmiących znaków.

**Kuruz** (Q) łączy **Uruz** z **Cén** i **Kaunan**. Cén i Kaunan to ta sama runa w różnych historycznych formach regionalnych — dwie wersje posłużyły do rozróżnienia **C** i **K**.

**Skald** (X) to miks **S** i **K**. Nazwa nawiązuje do nordyckich pieśni; mimo zamiany kolejności liter (K·S → S·K) dobrze oddaje brzmienie *iks*.

## Cyfry

Runy nie miały zapisu cyfr arabskich, więc powstały od zera. Cyfry dzielą się na trzy grupy oraz zero. Bazą każdej cyfry jest pionowa linia, do której dodaje się określony modyfikator:

| Grupa | Modyfikator | Cyfry |
| --- | --- | --- |
| 1–3 | jedna linia prostopadła | przesuwana w górę |
| 4–6 | dwie linie ukośne | przesuwane w górę |
| 7–9 | dwie linie prostopadłe | przesuwane w górę |

W każdej grupie kolejną cyfrę otrzymuje się przez przesunięcie jednej linii o jedną pozycję w górę, zaczynając od najniższego punktu.

**Zero** łączy wszystkie grupy: ma linię ukośną i prostopadłą — symbol sumy i mnożnika.

## Znaki specjalne

Inspiracją są legendy o wikingach i mitologia:

- **wykrzyknik** — topór (skojarzenie z groźbą)
- **kratka (#)** — swarzyca, słowiański akcent
- **pytajnik** — drogowskaz z gwiazdą, od powiedzenia *„kto pyta, nie błądzi”*

Niektóre znaki (np. klamry) zostały jedynie dostosowane stylistyką do czcionki — bez nadmiernej reinterpretacji.

Znak **bar** (`|`) jest wypozycjonowany tak, by przy `line-height: 1` łączył się ze swoimi kopiami między liniami.

## Łączenie glifów

Kształty zaprojektowano tak, by przy zerowym letter-spacingu glify łączyły się w spójny wzór. Dla czytelniejszego stylu wystarczy dodać letter-spacing.

## Warianty

RuneScript to **variable font** z płynną regulacją grubości (`wght` 100–900). W folderze `static/` leżą standardowe pliki dla każdej wagi — jako fallback tam, gdzie variable fonts nie są wspierane.

Dostępne są też warianty **monospace**.

| Plik | Opis |
| --- | --- |
| `fonts/runescript-variable.ttf` | Variable, proporcjonalny |
| `fonts/runescript-monospace-variable.ttf` | Variable, monospace |
| `fonts/runescript.ttf` | Statyczny (Regular) |
| `fonts/runescript-monospace.ttf` | Statyczny monospace (Regular) |
| `static/RuneScript-*.ttf` | Statyczne wagi Thin → Black |
| `static/RuneScriptMonospace-*.ttf` | Statyczne wagi monospace |

## Alfabet

| Litera | Runa |
| --- | --- |
| A | Ansuz |
| B | Berkanan |
| C | Cén |
| D | Dagaz |
| E | Ehwaz |
| F | Fehu |
| G | Gebo |
| H | Haglaz |
| I | Isaz |
| J | Jera |
| K | Kaunan |
| L | Laguz |
| M | Mannaz |
| N | Naudiz |
| O | Opala |
| P | Peorð |
| Q | Kuruz |
| R | Raido |
| S | Sowilo |
| T | Tiwaz |
| U | Uruz |
| V | Vend |
| W | Wynn |
| X | Skald |
| Y | Yr |
| Z | Zona |

Polskie znaki diakrytyczne używają wariantu *stung* odpowiadającej runy (np. Ą → Ansuz stung, Ł → Laguz stung).

## Użycie

```css
@font-face {
  font-family: "RuneScript";
  src: url("fonts/runescript-variable.ttf") format("truetype");
  font-weight: 100 900;
  font-style: normal;
}

body {
  font-family: "RuneScript", sans-serif;
  letter-spacing: 0; /* wzór łączący się */
  /* letter-spacing: 0.15em; — czytelniejszy styl */
}
```

Podgląd glifów: otwórz `index.html` lub `docs/index.html` w przeglądarce.

## Budowa

Źródła glifów są w `svg/`. Czcionkę buduje `build_font.py`:

```bash
pip install -r requirements.txt
python build_font.py
```
