# XIAO ESP32-C3 IR + RF Blaster Hat — Design v0.2

A XIAO-carrier "hat" (Seeed XIAO 24GHz mmWave module form factor): the XIAO C3
sockets into 2×7 female headers; the board carries a **triple-IR-LED blaster**
(MOSFET-driven) **and a CC1101 sub-GHz RF transmitter** (TX-only, tunable). One
tiny universal blaster. IR LEDs are THT (hand-soldered, better range); everything
else SMD for JLCPCB assembly.

## 1. Requirements (locked)
- **IR:** transmit only, **3× 940 nm THT LEDs** (D3 optional/DNP), MOSFET switch.
- **RF:** transmit only, **CC1101 module** (tunable 315/433/868/915) — TX now,
  RX unused (you learn RF codes on the C6 rig, like IR).
- **Host:** XIAO ESP32-C3, socketed, powered via its own USB-C (5V/VBUS).
- **Assembly:** JLC SMT for passives + MOSFET + CC1101 module; hand-solder the
  3 IR LEDs + the female sockets.
- **Only fixed-code RF** is replayable (outlets/fans/doorbells) — not rolling-code.

## 2. Pin map (XIAO C3 — no strapping pins used)
| Net | XIAO pin | GPIO | Use |
|-----|----------|------|-----|
| IR_TX | D2 | 4 | MOSFET gate (IR LEDs) |
| CC_CSN | D3 | 5 | CC1101 chip-select |
| CC_GDO0 | D4 | 6 | CC1101 OOK data (remote_transmitter drives this) |
| CC_SCK | D5 | 7 | SPI clock |
| CC_MOSI | D6 | 21 | SPI MOSI |
| CC_MISO | D7 | 20 | SPI MISO (config/status) |
| CC_GDO2 | D1 | 3 | CC1101 aux (optional) |

Free: D0/GPIO2 (strap — leave), D10/GPIO10. Avoided strapping GPIO2/8/9.
**Power:** IR LEDs → **5V (VBUS)**; CC1101 module → **3V3** (⚠ not 5V); common GND.

## 3. Schematic

### IR section (MOSFET low-side switch, 3 parallel LEDs)
```
 +5V ─┬─────────┬─────────┬
     [D1]      [D2]      [D3]        3× TSAL6400 940nm (THT 5mm); D3 = DNP option
      │         │         │
     [R1]      [R2]      [R3]        15Ω 1206 each (DNP R3 with D3)
      └────┬────┴────┬────┘
           └─────────┴──── Q1 drain
   GPIO4 ─[R4 100Ω]─┤ Q1 AO3400A (SOT-23, N-ch)
                     ├ source → GND
        [R5 10k]─────┤ gate pulldown → GND
   C1 100nF + C2 22µF bulk  across 5V/GND at the LEDs
```

### RF section (CC1101 module, SPI + GDO0 data)
```
 +3V3 ─┬─ CC1101 VCC        C3 100nF + C4 10µF across 3V3/GND at the module
       │
 XIAO ─┼─ SCK  (GPIO7)
       ├─ MOSI (GPIO21)
       ├─ MISO (GPIO20)
       ├─ CSN  (GPIO5)
       ├─ GDO0 (GPIO6)  ← OOK data / remote_transmitter
       └─ GDO2 (GPIO3)  ← optional
   CC1101 ANT → module's own antenna (spring/IPEX) — keep board copper clear
```

### Net / connection table
| Net | Nodes |
|-----|-------|
| +5V | XIAO 5V, D1–D3 anodes, C1+, C2+ |
| +3V3 | XIAO 3V3, CC1101 VCC, C3+, C4+ |
| GND | XIAO GND, Q1 source, R5, CC1101 GND, C1–C4 − |
| IR_TX | XIAO D2 → R4 → Q1 gate (+ R5 pulldown) |
| Q1_DRAIN | R1,R2,R3 → Q1 drain |
| LEDn_K | Dn cathode → Rn |
| SCK/MOSI/MISO/CSN/GDO0/GDO2 | XIAO ↔ CC1101 (see pin map) |

## 4. BOM (JLCPCB / LCSC)
| Ref | Part | Value/MPN | Pkg | Mount | Notes |
|-----|------|-----------|-----|-------|-------|
| Q1 | N-ch MOSFET | AO3400A (C20917) | SOT-23 | SMT | basic, logic-level |
| D1,D2,D3 | 940nm IR LED | TSAL6400 | 5mm | **THT** | D3 = DNP option |
| R1,R2,R3 | Res | 15 Ω | 1206 | SMT | R3 = DNP with D3 |
| R4 | Res | 100 Ω | 0603 | SMT | gate series |
| R5 | Res | 10 kΩ | 0603 | SMT | gate pulldown |
| C1,C3 | Cap | 100 nF | 0603 | SMT | decoupling |
| C2 | Cap | 22 µF | 0805 | SMT | IR bulk (3-LED peaks) |
| C4 | Cap | 10 µF | 0805 | SMT | CC1101 bulk |
| M1 | CC1101 module | E07-M1101D or generic 8-pin | module | SMT/THT | 3V3! tunable |
| J1,J2 | Female header 1×7 | 2.54 mm | THT | hand | XIAO socket |
| — | M2 mounting holes | ×2 | — | — | optional |

## 5. Mechanical / floorplan
- **Outline ≈ 21 mm × ~45 mm** (a touch longer than the IR-only board to fit CC1101).
- **XIAO socket** at one end (USB-C overhangs the edge for access).
- **IR LEDs** along the *opposite* short edge (front), ~10–12 mm apart, toe-out ±15°.
- **CC1101 module** on one long side; **antenna end pointing off-board** with a
  ground/copper keepout under and around the antenna (no pour beneath it).
- **Use Seeed's official XIAO KiCad footprint** for the socket (exact row spacing) —
  don't trust a hand-measured value.
- 2-layer, 1.6 mm, GND pour on both layers *except* the antenna keepout.

## 6. Firmware (ESPHome)
`ir-rf-blaster-xiao-c3.yaml`: `remote_transmitter` (IR) on GPIO4; CC1101 needs an
**external component** to init it over SPI (frequency/modulation) and expose GDO0
as the OOK line for a second `remote_transmitter`/`remote.raw`. This is a known
community pattern, not a stock ESPHome component — flag as a firmware task.

## 7. KiCad deliverables (this pass)
- `xiao-c3-ir-rf.kicad_pcb` — board outline + all footprints placed + nets
  (ratsnest to route). **Verify on first open**; swap the XIAO socket for Seeed's
  official footprint. Hand-generated (no local KiCad to render-check).
- Route in KiCad → DRC → Gerbers/BOM/CPL for JLC.
