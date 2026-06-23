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

| # | `DocumentType` | Required fingerprints (all must match) | Extraction module |
|---|---|---|---|
| 1 | `DATAVARDERING_SMAHUS` (prose) | `VÄRDEUTLÅTANDE`, `Värderingsobjekt`, `Upplåtelseform:\s*Friköpt` | `parsers.smahus` (prose strategies) |
| 2 | `DATAVARDERING_BR` (prose) | `VÄRDEUTLÅTANDE`, `Värderingsobjekt`, `Upplåtelseform:\s*Bostadsrätt` | `parsers.bostadsratt` (prose strategies) |
| 3 | `DATAVARDERING_BR` (UC tabular) | `Värdeutlåtande Bostadsrätt` on the banner | `parsers.bostadsratt` (UC-tabular strategies) |
| 4 | `DATAVARDERING_SMAHUS` (UC tabular) | `Värdeutlåtande Småhus` on the banner | `parsers.smahus` (UC-tabular strategies) |
| 5 | `FASTIGHETSUTDRAG` | `Fastighetsrapport Plus R` on the banner | `parsers.fastighetsutdrag` (stub) |
| 6 | `LGH_UTDRAG` | `Lägenhetsuppgi.{1,3}ter`, `Bostadsrä.{1,3}tsförening` | `parsers.lgh_utdrag` |
| – | `UNKNOWN` | — | (empty result) |

Each property-type DocumentType (`DATAVARDERING_BR` /
`DATAVARDERING_SMAHUS`) is emitted from two categories — the UC
tabular variant and the Fastighetsbyrån prose variant. The parser
dispatches between layouts inside a per-slot strategy chain (see
`parsers/_strategy.py`): each docx-template slot lists its tactics
in priority order, and the first non-None match wins. A new issuer's
layout means appending one strategy per affected slot — never a new
parser branch and never a new DocumentType.

## Extractor strategy library (post-#1079)

The per-slot extractor shape is the durable surface that hardens
the classifier against new sample shapes:

- `parsers/_strategy.py` — `Strategy` (named extract callable +
  confidence), `SlotExtractor` (priority-ordered strategy tuple,
  returns `not_found` if every strategy misses — **never** a broken
  default), `run_slots()` (walks the inventory).
- `parsers/_context.py` — `ParseContext` (page-1 text + words + full
  multi-page text), `build_context()` (single pdfplumber open per
  parse, immutable view for the slot walk).
- `parsers/bostadsratt.py` + `parsers/smahus.py` — declare the slot
  inventory (operator-pinned, mirrors `TemplateFields`) and the
  strategy chain per slot. UC-tabular strategies sit before prose
  strategies in each chain.

### Golden-test harness

`tests/test_golden_pipeline.py` runs every `tests/fixtures/golden/<sample>.expected.json`
through `classify_pdf` + `extract_document` against the operator's
real PDF in the sample directory (`VALUATION_SAMPLE_DIR`, default
`/tmp/vardeutlatande`). It asserts the classified `document_type`,
every slot's value, and the comparable-sales row count match the
golden exactly. Adding a new sample = drop PDF + author its
`.expected.json` capturing every slot value — the test fails until
the strategy chain handles the new layout. A coverage guard
(`test_every_parser_backed_document_type_has_a_golden`) flags any
parser-backed DocumentType added without a golden.
