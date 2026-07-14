# XIAO ESP32-C3 IR + RF Blaster Hat — 4-layer, routed

A XIAO-carrier "hat": the XIAO ESP32-C3 sockets into two 1×7 female headers; the
board carries a **triple-IR-LED blaster** (MOSFET-driven) **and a CC1101 sub-GHz RF
transmitter** (TX-only, tunable). IR LEDs are THT (hand-soldered, better range);
everything else SMD for JLCPCB assembly.

**Status (2026-07-13): 4-layer, fully routed, DRC-clean, fab-ready.** Generated
headlessly (`generate_pcb.py`, kiutils) + zone-filled + validated with `kicad-cli`
(KiCad 9): **DRC 0 errors, 0 unconnected** (only cosmetic silk warnings). Board is
**22 × 50 mm**. Gerbers/drill in [`ir-rf-gerbers.zip`](ir-rf-gerbers.zip); top render
[`ir-rf-top.png`](ir-rf-top.png).

> Two things to settle in the KiCad GUI before ordering (both flagged from the start):
> 1. **XIAO socket footprint is provisional** (two `PinSocket_1x07` at 17.2 mm row
>    spacing) — swap for Seeed's official XIAO footprint and re-check the row pitch.
> 2. **Antenna keepout** — the inner GND/+5V planes fill the whole board; add a copper
>    **keepout** (all layers) under wherever your CC1101 module's antenna sits. A silk
>    reminder marks the RF edge; the exact position is module-specific.

## Stackup (4-layer)

| Layer      | Use                                                     |
| ---------- | ------------------------------------------------------- |
| **F.Cu**   | components + signals (IR section, SPI left col, MOSI)   |
| **In1.Cu** | **GND plane** (solid) — clean return for the IR pulses  |
| **In2.Cu** | **+5V plane** — LED current                             |
| **B.Cu**   | signals (SPI SCK/MISO, IR_TX, +3V3 tail)                |

GND and +5V go to their planes via stitching vias (through-vias; the opposite plane's
zone clears around each). Only `+3V3` and the logic signals are routed as tracks.

## Pin maps

**XIAO ESP32-C3** (no strapping pins used):

| Net    | XIAO pin | GPIO | Socket |
| ------ | -------- | ---- | ------ |
| IR_TX  | D2       | 4    | J1.3   |
| CC_CSN | D3       | 5    | J1.4   |
| CC_GDO0| D4       | 6    | J1.5   |
| CC_SCK | D5       | 7    | J1.6   |
| CC_MOSI| D6       | 21   | J1.7   |
| CC_MISO| D7       | 20   | J2.1   |
| CC_GDO2| D1       | 3    | J1.2   |

Power: IR LEDs → **5V (VBUS)**; CC1101 → **3V3**; common GND. `J1.1`/`J2.2-4,6` = GND.

**CC1101 header (M1, 2×4)** — pin order **chosen for a clean fan-out**; this is a
generic 8-pin placeholder, so **wire your module to match** (or re-map + re-route):

```
  1 +3V3   2 GND
  3 GDO2   4 MISO
  5 CSN    6 SCK
  7 GDO0   8 MOSI
```

## BOM

| Ref     | Part                | Value/MPN        | Pkg    | Mount | Footprint                  |
| ------- | ------------------- | ---------------- | ------ | ----- | -------------------------- |
| Q1      | N-ch MOSFET         | AO3400A (C20917) | SOT-23 | SMT   | `SOT-23`                   |
| D1–D3   | 940 nm IR LED       | TSAL6400         | 5 mm   | THT   | `LED_D5.0mm`               |
| R1–R3   | Res                 | 15 Ω             | 1206   | SMT   | `R_1206_3216Metric`        |
| R4      | Res (gate)          | 100 Ω            | 0603   | SMT   | `R_0603_1608Metric`        |
| R5      | Res (pulldown)      | 10 kΩ            | 0603   | SMT   | `R_0603_1608Metric`        |
| C1,C3   | Cap                 | 100 nF           | 0603   | SMT   | `C_0603_1608Metric`        |
| C2      | Cap (IR bulk)       | 22 µF            | 0805   | SMT   | `C_0805_2012Metric`        |
| C4      | Cap (RF bulk)       | 10 µF            | 0805   | SMT   | `C_0805_2012Metric`        |
| M1      | CC1101 module hdr   | 2×4, 2.54 mm     | THT    | hand  | `PinHeader_2x04_P2.54mm`   |
| J1,J2   | XIAO socket         | 1×7, 2.54 mm     | THT    | hand  | `PinSocket_1x07_P2.54mm`   |

## Regenerate / validate / order (headless)

```sh
# 1. generate the board (native, kiutils) - writes zones UNFILLED
python3 generate_pcb.py footprints xiao-c3-ir-rf.kicad_pcb

# 2. fill zones + validate + export (KiCad 9; amd64 image on Apple Silicon)
docker run --rm --platform linux/amd64 -v "$PWD:/w" -w /w kicad/kicad:9.0 sh -c '
  python3 /w/fill_zones.py xiao-c3-ir-rf.kicad_pcb xiao-c3-ir-rf.kicad_pcb
  kicad-cli pcb drc xiao-c3-ir-rf.kicad_pcb
  kicad-cli pcb export gerbers -o g/ xiao-c3-ir-rf.kicad_pcb
  kicad-cli pcb export drill   -o g/ xiao-c3-ir-rf.kicad_pcb'
```

> **Toolchain note:** `pcbnew` Python scripting crashes under amd64 emulation on
> Apple Silicon in `FootprintLoad` (`malloc(): unaligned tcache chunk`) — so the board
> is *generated* with **kiutils** (native, pure-Python) and *zone-filled* with
> **`pcbnew.LoadBoard`** (which does NOT crash), then `kicad-cli` runs DRC/export.
> `footprints/` holds the KiCad-9 footprints the generator places (offline-reproducible).

## Firmware (ESPHome)

`../../firmware/esphome/ir-rf-blaster-xiao-c3.yaml`: `remote_transmitter` (IR) on GPIO4;
the CC1101 needs an **external component** (SPI init + GDO0 OOK bridge) — a known
community pattern, not stock ESPHome. Match the SPI pins + the CC1101 header order above.
