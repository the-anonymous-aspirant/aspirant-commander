# Valuation-statement document classifier — audit (Phase A)

Inventory and fingerprinting of every PDF the operator currently feeds into
`/valuation-statement/extract`, ahead of rewriting the classifier to be
content-only (system_3 task #1060).

Audit sample: `/data/aspirant/files/users/1/Värdeutlåtande/` on
`aspirant-cell`, snapshot 2026-06-23.

## Inventory

| File | Bytes | Category (proposed) | Current classifier verdict |
|---|---:|---|---|
| `Datavärdering.pdf` | 106 212 | `datavardering_br` | `DATAVARDERING` ✓ |
| `VärdeutlåtandeBR.pdf` | 78 638 | `vardeutlatande_northmill_br` | `DATAVARDERING` ✗ (Northmill layout mis-routed to UC parser) |
| `VärdeutlåtandeHok.pdf` | 77 642 | `vardeutlatande_northmill_smahus` | `UNKNOWN` ✗ |
| `UCB_BENGTSFORS_NARSIDAN_1-21_*.pdf` | 106 346 | `datavardering_smahus` | `UNKNOWN` ✗ (#1059 finding) |
| `FastighetPlusR_Bengtsfors_NARSIDAN_1-21_*.pdf` | 110 493 | `fastighetsutdrag_plus_r` | `FASTIGHETSUTDRAG` ✓ |
| `LGH utdrag.pdf` | 406 014 | `lgh_utdrag` | `LGH_UTDRAG` ✓ |

`Värdeutlåtande mall.docx` (the output template) is ignored — extract only
accepts PDFs.

## Fingerprints (page-1 text)

For each PDF below, the `pdftotext -layout -f 1 -l 1` output of the first
page is excerpted to the lines that drive classification. Captured-text
fixtures live under `tests/fixtures/classifier/` so unit tests can run
against the deterministic kernel without bundling the PDFs.

### `datavardering_br` — UC Bostad "Värdeutlåtande Bostadsrätt" (Datavärdering.pdf)

```
                                                          Värdeutlåtande
                                                             Bostadsrätt
Objektsinformation
Adress                                       Kommun                  Fastighetsrisk
…
Föreningens lägenhetsbeteckning              Månadsavgift            Våningsplan, tr
```

Distinguishing markers:

- Banner: `Värdeutlåtande` then `Bostadsrätt` on the next non-blank line.
- Table headers `Föreningsinformation` and `BRF Avgiftsanalys` appear on page 1.
- `Marknadsvärde, kr/kvm` in the Värde block.

### `datavardering_smahus` — UC Bostad "Värdeutlåtande Småhus" (UCB_BENGTSFORS_…)

```
                                                          Värdeutlåtande
                                                                Småhus
Objektsinformation
Fastighetsbeteckning                         Fastighetstyp           Fastighetsrisk
Bengtsfors Närsidan 1:21                     Småhus (220)            Ej aktiverad
…
Byggnadstyp
Friliggande
Taxeringsvärde     Totalyta   Boyta   Byggnadsår   Utrustningsstandard
…
```

Distinguishing markers:

- Banner: `Värdeutlåtande` then `Småhus` on the next non-blank line.
- Småhus-specific table columns: `Fastighetsbeteckning`, `Tomtyta`,
  `Byggnadstyp`.

### `vardeutlatande_northmill_br` — Northmill bank template, Bostadsrätt (VärdeutlåtandeBR.pdf)

```
VÄRDEUTLÅTANDE
Uppdragsgivare: Northmill Bank AB
Ändamål: Underlag för kreditprövning

Värderingsobjekt
    ●   Objekt: LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)
    ●   Adress: Hanna Rydhs gata 12
    ●   Kommun: Hägersten
    ●   Upplåtelseform: Bostadsrätt
```

Distinguishing markers:

- All-caps `VÄRDEUTLÅTANDE` banner (the UC variants use Title Case).
- `Uppdragsgivare: Northmill Bank AB`.
- `Upplåtelseform: Bostadsrätt`.

### `vardeutlatande_northmill_smahus` — Northmill template, Friköpt (VärdeutlåtandeHok.pdf)

```
VÄRDEUTLÅTANDE
Uppdragsgivare: Northmill Bank AB
Ändamål: Underlag för kreditprövning

Värderingsobjekt
    ●   Objekt: Vaggeryd Hok 2:139
    ●   Adress: Lillholmsvägen 12
    ●   Kommun: Hok
    ●   Upplåtelseform: Friköpt
```

Distinguishing markers: same banner as `vardeutlatande_northmill_br`, but
`Upplåtelseform: Friköpt`. "Hok" in the brief is a municipality, not a
document family.

### `fastighetsutdrag_plus_r` — Lantmäteriet "Fastighetsrapport Plus R"

```
                                                       Fastighetsrapport
                                                              Plus R
Fastighet
Beteckning                                            Totalareal
Bengtsfors NÄRSIDAN 1:21                              2 089
…
Ägare
Typ        Ägare                                       Andel  Inskrivning
Lagfart    Anna Chatarina Reinholdsson, …              1/1    2008-03-11
```

Distinguishing marker: `Fastighetsrapport` then `Plus R` on the banner.

### `lgh_utdrag` — Bostadsrättsförening lägenhetsförteckning

```
                                       Utskri'tsdatum: 2026-06-09
HSB Brf Långpannan i Stockholm

Lägenhetsuppgi9ter
Lgh-nr   Ska;teverkets lgh-nr   Antal rum   Lägenhetsyta   Våning
…
Bostadsrä;tsförening och fastighet
```

`pdftotext` renders the `ﬁ`/`ft` ligatures as stray glyphs (`9`, `;`,
`'`), so the fingerprints tolerate a 1–3 character wildcard at those
positions.

## CATEGORIES table

The classifier walks this table top-to-bottom and returns the first
category whose fingerprints **all** match. Per the operator correction
on epic #1060, fingerprinting is **content-only**: a category MUST NOT
key off issuer branding ("Northmill Bank", "UC Bostad"). Same content
type from a different bank lands in the same `DocumentType` so one
parser branch handles every issuer.

| # | `DocumentType` | Required fingerprints (all must match) | Extraction module (today) |
|---|---|---|---|
| 1 | `DATAVARDERING_SMAHUS` (prose) | `VÄRDEUTLÅTANDE`, `Värderingsobjekt`, `Upplåtelseform:\s*Friköpt` | `parsers.datavardering_smahus` (prose branch) |
| 2 | `VARDEUTLATANDE_NORTHMILL_BR` (prose) | `VÄRDEUTLÅTANDE`, `Värderingsobjekt`, `Upplåtelseform:\s*Bostadsrätt` | stub (follow-up) |
| 3 | `DATAVARDERING_BR` (UC tabular) | `Värdeutlåtande Bostadsrätt` on the banner | `parsers.datavardering` |
| 4 | `DATAVARDERING_SMAHUS` (UC tabular) | `Värdeutlåtande Småhus` on the banner | `parsers.datavardering_smahus` (tabular branch) |
| 5 | `FASTIGHETSUTDRAG_PLUS_R` | `Fastighetsrapport Plus R` on the banner | `parsers.fastighetsutdrag` (existing stub) |
| 6 | `LGH_UTDRAG` | `Lägenhetsuppgi.{1,3}ter`, `Bostadsrä.{1,3}tsförening` | `parsers.lgh_utdrag` |
| – | `UNKNOWN` | — | (empty result) |

`DATAVARDERING_SMAHUS` is the same enum value emitted from both the
prose-appraisal category (#1) and the UC tabular category (#4); the
parser branch dispatches between layouts on page-1 content.

The `VARDEUTLATANDE_NORTHMILL_BR` enum survives because its prose
appraisal layout has no parser yet — the BR side is a follow-up
parallel to `DATAVARDERING_SMAHUS`'s prose path. The previous
`VARDEUTLATANDE_NORTHMILL_SMAHUS` enum was retired by epic #1060's
operator correction in favour of the unified `DATAVARDERING_SMAHUS`.

## Phase C — remaining parser branches

The classifier returning the correct type is necessary but not
sufficient. Open work under #1060:

- Parser branch unification for `VARDEUTLATANDE_NORTHMILL_BR` (the
  Fastighetsbyrån prose appraisal of a Bostadsrätt) — same prose
  layout as the now-merged Småhus prose path, distinguished only by
  the `Upplåtelseform: Bostadsrätt` line.
