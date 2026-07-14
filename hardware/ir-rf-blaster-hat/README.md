# XIAO ESP32-C3 IR + RF Blaster Hat — 4-layer, routed

A XIAO-carrier "hat": the XIAO ESP32-C3 sockets into two 1×7 female headers; the board
carries a **triple-IR-LED blaster** (MOSFET-driven) **and a CC1101 sub-GHz RF transmitter**
(TX-only). IR LEDs are THT (hand-soldered, better range); everything else SMD for JLCPCB
assembly. The CC1101 is a socketed **EBYTE E07-M1101D** module.

**Status (2026-07-14): 4-layer, fully routed, DRC-clean, fab-ready.** Generated headlessly
(`generate_pcb.py`, kiutils) + zone-filled + validated with `kicad-cli` (KiCad 9): **DRC 0
errors, 0 unconnected**. Board is **23 × 54 mm**. Gerbers/drill in
[`ir-rf-gerbers.zip`](ir-rf-gerbers.zip); renders [`ir-rf-top.png`](ir-rf-top.png) /
[`ir-rf-bottom.png`](ir-rf-bottom.png); module orientation [`e07-orientation.svg`](e07-orientation.svg).

## Floorplan

```
        NORTH (top)
   ┌──────────────────┐
   │  D1   D2   D3     │  3× 940 nm IR LED (THT), aimed north/off the top edge
   │  R  Q1  R   caps  │  MOSFET driver row
   │ ┌──────────────┐  │
USB┤ │ XIAO (front) │  │  XIAO horizontal — USB-C exits the WEST edge
   │ └──────────────┘  │
   │   [E07 on BACK]   │  E07-M1101D on a 2×4 female socket, flipped to B.Cu;
   │        ││         │  15×30 mm module body lies over the XIAO back and
   │        ▼▼         │  extends SOUTH — SMA/antenna off the south edge.
   └────────╨╨────────┘
        SOUTH (antenna)
```

- **XIAO** is on the **front**, rotated horizontal so **USB-C exits the west edge**; the two
  1×7 sockets run E-W (rows 17.2 mm apart, provisional — see below).
- **E07-M1101D CC1101** is on the **back**, on a **swappable 2×4 female socket**. Per the
  CDEBYTE datasheet the 2×4 header runs **4-across the module's 15 mm width**; the 30 mm body
  + SMA connector extend perpendicular to it, so with the header at the north end the
  **antenna points south, off the board edge**. An **all-layer copper keepout** clears the
  planes under the RF/antenna region (y 46–54).

## Frequency & the swappable socket

The **CC1101 chip** is multi-band (300–348 / 387–464 / 779–928 MHz), but any physical
**module** is impedance-matched + antenna-tuned to **one** band. The **E07-M1101D is a 433 MHz
module** (covers ~387–464 MHz) — the common band for US/EU fixed-code outlets, doorbells, many
fan remotes. 315 MHz gear (some garage openers/older remotes) or 868/915 MHz needs a
differently-tuned module.

The board is deliberately **band-agnostic**: the CC1101 slot is a plain 8-pin 2×4 socket, so
any same-pinout CC1101 module (or an SMA E07 with a swapped antenna) drops in. Pick the module
for your band; the PCB doesn't change.

## Stackup (4-layer)

| Layer      | Use                                                       |
| ---------- | --------------------------------------------------------- |
| **F.Cu**   | components + signals (IR section, N-row SPI, +3V3)         |
| **In1.Cu** | **GND plane** (solid) — clean return for the IR pulses    |
| **In2.Cu** | **+5V plane** — LED current                               |
| **B.Cu**   | signals (S-row SPI, GDO2 dogleg, gate)                    |

GND and +5V go to their planes via stitching vias (through-vias; the opposite plane's zone
clears around each). Only `+3V3` and the logic signals are routed as tracks. The E07 socket's
GND/VCC pins are THT and land directly on the planes.

## Pin maps

**XIAO ESP32-C3** — the E07 **north row** (odd pins, datasheet order) fans from **J1** (the
north XIAO row); the **south row** (even pins) fans from **J2**. Strapping pins GPIO8/9 (D8/D9)
are left as spares. `IR_TX` sits on GPIO2 (D0, a strapping pin) — safe here because the 10 kΩ
gate pulldown holds the MOSFET off through boot.

| Net      | XIAO pin | GPIO | Socket | → E07 pin      |
| -------- | -------- | ---- | ------ | -------------- |
| IR_TX    | D0       | 2    | J1.1   | (to MOSFET)    |
| CC_GDO0  | D1       | 3    | J1.2   | 3 (N row)      |
| CC_SCK   | D2       | 4    | J1.3   | 5 (N row)      |
| CC_MISO  | D3       | 5    | J1.4   | 7 (N row)      |
| CC_GDO2  | D4       | 6    | J1.5   | 8 (S, dogleg)  |
| CC_CSN   | D7       | 20   | J2.1   | 4 (S row)      |
| CC_MOSI  | D10      | 10   | J2.4   | 6 (S row)      |
| +3V3     | 3V3      | —    | J2.5   | 2 (VCC)        |

Power: IR LEDs → **5V (VBUS)**; CC1101 → **3V3**; common GND. J1.6/7 and J2.2/3 are spare.

**E07-M1101D socket (M1, 2×4 female)** — **fixed CDEBYTE pinout**, the module plugs straight in:

```
  1 GND    2 VCC(3V3)
  3 GDO0   4 CSN
  5 SCK    6 MOSI
  7 MISO   8 GDO2
```

> ⚠️ E07 logic is **3.3 V** — the datasheet warns 5 V TTL "may be at risk of burning down."
> The XIAO drives it at 3.3 V; keep it off VBUS.

## BOM

| Ref     | Part                | Value/MPN            | Pkg    | Mount | Footprint                  |
| ------- | ------------------- | -------------------- | ------ | ----- | -------------------------- |
| Q1      | N-ch MOSFET         | AO3400A (C20917)     | SOT-23 | SMT   | `SOT-23`                   |
| D1–D3   | 940 nm IR LED       | TSAL6400             | 5 mm   | THT   | `LED_D5.0mm`               |
| R1–R3   | Res (LED limit)     | 15 Ω                 | 1206   | SMT   | `R_1206_3216Metric`        |
| R4      | Res (gate)          | 100 Ω                | 0603   | SMT   | `R_0603_1608Metric`        |
| R5      | Res (pulldown)      | 10 kΩ                | 0603   | SMT   | `R_0603_1608Metric`        |
| C1,C3   | Cap                 | 100 nF               | 0603   | SMT   | `C_0603_1608Metric`        |
| C2      | Cap (IR bulk)       | 22 µF                | 0805   | SMT   | `C_0805_2012Metric`        |
| C4      | Cap (RF bulk)       | 10 µF                | 0805   | SMT   | `C_0805_2012Metric`        |
| M1      | **E07-M1101D** hdr  | 2×4 socket, 2.54 mm  | THT    | hand  | `PinSocket_2x04_P2.54mm`   |
| J1,J2   | XIAO socket         | 1×7, 2.54 mm         | THT    | hand  | `PinSocket_1x07_P2.54mm`   |

Module (not on the PCB BOM): **EBYTE E07-M1101D** (or E07-M1101D-SMA for an external antenna),
433 MHz, LCSC C108549 / Amazon.

## Before ordering (KiCad GUI)

1. **XIAO socket footprint is provisional** (two `PinSocket_1x07` at 17.2 mm row spacing) —
   swap for Seeed's official XIAO footprint and re-check the row pitch.
2. **3D-check the back-side stack**: the E07 socket pins poke through to the front under the
   XIAO — verify standoff heights (a stackable/tall XIAO socket or a low-profile E07 socket
   clears it). The 30 mm module body overhangs the south edge slightly; the SMA/antenna should
   sit in free air beyond the board.

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

> **Toolchain note:** `pcbnew` Python scripting crashes under amd64 emulation on Apple Silicon
> in `FootprintLoad` (`malloc(): unaligned tcache chunk`) — so the board is *generated* with
> **kiutils** (native, pure-Python) and *zone-filled* with **`pcbnew.LoadBoard`** (which does
> NOT crash), then `kicad-cli` runs DRC/export. `footprints/` holds the KiCad-9 footprints the
> generator places (offline-reproducible).
>
> One headless gotcha baked into the generator: kicad-cli places a **B.Cu** THT footprint's
> pads at the plain rotated local coords (no X-mirror), so `abspad()` must **not** mirror — the
> E07 even/odd pins land on the south/north rows accordingly.

## Firmware (ESPHome)

`../../firmware/esphome/ir-rf-blaster-xiao-c3.yaml`: `remote_transmitter` (IR) — note IR_TX is
now **GPIO2 (D0)** (was GPIO4); update the YAML pin. The CC1101 needs an **external component**
(SPI init + GDO0 OOK bridge) — a known community pattern, not stock ESPHome. Match the E07 fixed
pinout above: SCK=GPIO4, MISO=GPIO5, MOSI=GPIO10, CSN=GPIO20, GDO0=GPIO3, GDO2=GPIO6.
