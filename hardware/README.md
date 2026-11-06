# TripleTEC driver hardware design files

Contract deliverable for the two TripleTEC driver boards: the MCU board and the Power board. It was delivered as EasyEDA Pro projects (`.eprj2`) plus schematic PDFs. The design was last saved on 2026-09-14 with EasyEDA Pro 3.2.149, and the PDFs were exported on 2026-10-17.

| Path | Contents |
| --- | --- |
| `original/` | Delivered files, unmodified: two `.eprj2` projects and two schematic PDFs |
| `MCU/`, `Power/` | BOM, pick-and-place CSV, PCB render (SVG and PNG), EasyEDA's own preview, and the decoded design documents |
| `tools/` | Scripts that regenerate everything in `MCU/` and `Power/` from `original/` |

## What the `.eprj2` files contain

Each `.eprj2` file is an SQLite database. The design itself is stored as an edit history in `history_data`. Each record is gzipped JSON encrypted with AES-128-GCM. The key is the hex-decoded `key` column of the matching `project_history_*` row, and the IV is that row's `uuid`, hex-decoded. All records decrypt and pass GCM authentication.

Replaying the history in order rebuilds the final state of every document. The rebuilt PCB and every schematic page match the versions recorded in `project_structures`. The `documents/` folders hold that final state in EasyEDA's line format (`{"type":...}||{payload}|`) with coordinates in mil. Designer account names are redacted there; the originals still contain them.

Things to know:

- The renders and CSVs are a reconstruction, not fabrication output. Generate Gerbers with EasyEDA Pro and check them against these files before ordering boards.
- Copper pour fills are the last ones EasyEDA calculated. Pour fill coordinates are in 10-mil units; everything else is in mil.
- The Power board's 3.5 oz copper appears only as a silkscreen note. Both stackups in the design say 1.379 mil (about 1 oz), so heavy copper must be specified when ordering.
- `TripleTECDriver_Power.eprj2` started as a copy of the MCU project. It still holds old MCU pour fills and its deleted MCU and NTC sheets; the tools ignore both.
- Each original holds one EasyEDA local account record whose password field equals its username, which looks like a placeholder rather than a credential.

## Thermistor inputs (MCU board CN1-CN3)

| Item | Value | Source |
| --- | --- | --- |
| Connectors | CN1, CN2, CN3, one per channel: NTC1, NTC2, NTC3 | design |
| Part | Megastar ZX-XH2.54-2PWZ, LCSC C7429641 | design BOM |
| Type | XH-style 2-pin wire-to-board header, through-hole, right-angle | LCSC listing; the footprint outline overhangs the board edge |
| Pitch | 2.50 mm, even though "2.54" is in the part name | footprint pad spacing; LCSC listing |
| Pinout | Pin 1 = NTCx signal, pin 2 = AGND | decoded netlist |
| Footprint | Pin 1 pad 1.52 x 1.50 mm (square), pin 2 pad 1.50 mm round, 0.90 mm plated holes | decoded footprint |
| Placement | Top edge of the MCU board, 5 mm inside the edge, at x = 30 / 45 / 60 mm | decoded placement |
| Ratings | 250 V; 2 A or 3 A (the LCSC listings disagree); -25 to +85 °C; tin-plated brass; UL94V-0 | LCSC listing, not yet checked against the Megastar datasheet |
| Mating side | XH-series 2-pin housing and crimps, for example JST XHP-2 with SXH-001T-P0.6. Compatibility with the Megastar header is assumed. | not verified |

Thermistor, from the note on the NTC schematic sheet: 100 kΩ at 25 °C, B = 3950, specified for -10 to 110 °C at ±0.4 °C, roughly 550.6 kΩ down to 6.8 kΩ over that range.

Measurement circuit:

- **Divider:** AVCC → 100 kΩ 0.1 % pull-up (R22/R23/R24) → NTCx node → thermistor → AGND.
- **ADC input:** each NTCx node passes through a 1 kΩ / 10 nF filter (R27-R29, C22-C24; about 16 kHz) to ADS1220 (U8) AIN0/AIN1/AIN2.
- **Mid-rail reference:** a 100 kΩ / 100 kΩ divider (R21/R26) gives AVCC/2, filtered by 1 kΩ / 10 nF (R30/C25), on AIN3.
- **Supplies:** AVCC is 3.3 V through ferrite L1. AGND joins GND through 0 Ω R25.

This matches the firmware: `sensors/ads1220.h` uses gain 1, the internal 2.048 V reference and AINx-AIN3 inputs, and `sensors/ntc.c` uses 100 kΩ / B3950. The NTC sheet also has a stray "增益 32倍" (gain 32×) note. At gain 32 the expected differential range, roughly -1.4 to +1.1 V, would saturate, so treat that note as stale.

## Regenerating

You need Python 3 with `cryptography`. Rasterizing to PNG is optional and also needs Node.js with Playwright.

```sh
python3 tools/decrypt_all.py original/TripleTECDriver_MCU.eprj2 /tmp/mcu_dec
python3 tools/reconstruct.py original/TripleTECDriver_MCU.eprj2 /tmp/mcu_dec/records.jsonl /tmp/mcu_docs
python3 tools/pcbtools.py /tmp/mcu_docs /tmp/mcu_out MCU
node tools/raster.js /tmp/mcu_out/MCU_pcb.svg /tmp/mcu_out/MCU_PCB_decoded.png 1600 2
```

The `documents/` folders here were filtered to drop deleted documents and redact account names. `reconstruct.py` on its own writes every document unredacted.
