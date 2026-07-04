#!/usr/bin/env python3
"""Generate a KiCad .kicad_pcb scaffold for the XIAO C3 IR+RF blaster hat.
Self-contained footprints (embedded geometry) + nets + board outline, placed per
the floorplan. Route in KiCad. Swap the XIAO socket for Seeed's official footprint."""
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad, DrillDefinition
from kiutils.items.common import Position, Net
from kiutils.items.gritems import GrLine, GrText

board = Board().create_new()
board.generator = "kiutils-ir-rf-hat"

# ---- nets ----
netnames = ["", "+5V", "+3V3", "GND", "IR_TX", "Q_DRAIN",
            "LED1_K", "LED2_K", "LED3_K",
            "CC_CSN", "CC_GDO0", "CC_SCK", "CC_MOSI", "CC_MISO", "CC_GDO2"]
board.nets = [Net(i, n) for i, n in enumerate(netnames)]
NET = {n: Net(i, n) for i, n in enumerate(netnames)}

def smd_pad(num, x, y, w, h, net):
    return Pad(number=str(num), type="smd", shape="roundrect",
               position=Position(x, y), size=Position(w, h),
               layers=["F.Cu", "F.Paste", "F.Mask"], roundrectRatio=0.25,
               net=net)

def tht_pad(num, x, y, dia, drill, net, shape="circle"):
    return Pad(number=str(num), type="thru_hole", shape=shape,
               position=Position(x, y), size=Position(dia, dia),
               drill=DrillDefinition(diameter=drill),
               layers=["*.Cu", "*.Mask"], net=net)

def mkfp(ref, val, x, y, pads, rot=0):
    fp = Footprint().create_new(library_id="hat:"+val, value=val, reference=ref)
    fp.position = Position(x, y, rot)
    fp.layer = "F.Cu"
    fp.pads = pads
    fp.reference = ref   # plain attr for silk labeling below
    return fp

fps = []
# --- IR: 3 THT LEDs along the front edge (y small), 2.54mm pad spacing ---
led_x = [5.0, 10.5, 16.0]
for i, lx in enumerate(led_x, 1):
    fps.append(mkfp(f"D{i}", "LED_D5.0mm",
        lx, 5.0,
        [tht_pad(2, lx, 5.0-1.27, 1.8, 0.9, NET[f"LED{i}_K"], "circle"),   # cathode
         tht_pad(1, lx, 5.0+1.27, 1.8, 0.9, NET["+5V"], "rect")]))         # anode->5V
# --- current resistors R1..R3 (1206) behind each LED ---
for i, lx in enumerate(led_x, 1):
    fps.append(mkfp(f"R{i}", "R_1206",
        lx, 8.5,
        [smd_pad(1, lx, 8.5-1.5, 1.0, 1.7, NET[f"LED{i}_K"]),
         smd_pad(2, lx, 8.5+1.5, 1.0, 1.7, NET["Q_DRAIN"])]))
# --- Q1 AO3400A SOT-23 ---
fps.append(mkfp("Q1", "SOT-23", 10.5, 12.5,
    [smd_pad(1, 10.5-0.95, 12.5-1.0, 0.9, 1.0, NET["IR_TX"]),     # gate (via R4)
     smd_pad(2, 10.5+0.95, 12.5-1.0, 0.9, 1.0, NET["GND"]),       # source
     smd_pad(3, 10.5,      12.5+1.0, 0.9, 1.0, NET["Q_DRAIN"])])) # drain
# --- R4 gate 100R, R5 pulldown 10k (0603) ---
fps.append(mkfp("R4", "R_0603", 7.0, 12.5,
    [smd_pad(1, 7.0-0.8, 12.5, 0.8, 0.9, NET["IR_TX"]),
     smd_pad(2, 7.0+0.8, 12.5, 0.8, 0.9, NET["IR_TX"])]))  # in series on IR_TX (gate side)
fps.append(mkfp("R5", "R_0603", 13.5, 12.5,
    [smd_pad(1, 13.5-0.8, 12.5, 0.8, 0.9, NET["IR_TX"]),
     smd_pad(2, 13.5+0.8, 12.5, 0.8, 0.9, NET["GND"])]))
# --- C1 100nF, C2 22uF bulk near LEDs ---
fps.append(mkfp("C1", "C_0603", 3.0, 11.0,
    [smd_pad(1, 3.0-0.8, 11.0, 0.8, 0.9, NET["+5V"]),
     smd_pad(2, 3.0+0.8, 11.0, 0.8, 0.9, NET["GND"])]))
fps.append(mkfp("C2", "C_0805", 18.0, 11.0,
    [smd_pad(1, 18.0-1.0, 11.0, 1.0, 1.3, NET["+5V"]),
     smd_pad(2, 18.0+1.0, 11.0, 1.0, 1.3, NET["GND"])]))
# --- CC1101 module: 8-pin header (2x4, 2.54mm) mid-board ---
cc_pins = [("VCC","+3V3"),("GND","GND"),("SCK","CC_SCK"),("MOSI","CC_MOSI"),
           ("MISO","CC_MISO"),("CSN","CC_CSN"),("GDO0","CC_GDO0"),("GDO2","CC_GDO2")]
cx, cy = 10.5, 22.0
ccpads = []
for idx, (nm, net) in enumerate(cc_pins):
    col = idx % 2; row = idx // 2
    px = cx + (col*2.54 - 1.27)
    py = cy + (row*2.54 - 3.81)
    ccpads.append(tht_pad(idx+1, px, py, 1.7, 1.0, NET[net], "rect" if idx==0 else "circle"))
fps.append(mkfp("M1", "CC1101_MODULE", cx, cy, ccpads))
# --- C3 100nF, C4 10uF near CC1101 ---
fps.append(mkfp("C3", "C_0603", 6.0, 22.0,
    [smd_pad(1, 6.0-0.8, 22.0, 0.8, 0.9, NET["+3V3"]),
     smd_pad(2, 6.0+0.8, 22.0, 0.8, 0.9, NET["GND"])]))
fps.append(mkfp("C4", "C_0805", 15.0, 22.0,
    [smd_pad(1, 15.0-1.0, 22.0, 1.0, 1.3, NET["+3V3"]),
     smd_pad(2, 15.0+1.0, 22.0, 1.0, 1.3, NET["GND"])]))
# --- XIAO socket: two 1x7 female headers at the far end (y large) ---
# Left row J1 pins = D0..D6 (GPIO2,3,4,5,6,7,21); Right row J2 = D7,D8,D9,D10,3V3,GND,5V
j1_nets = ["GND","CC_GDO2","IR_TX","CC_CSN","CC_GDO0","CC_SCK","CC_MOSI"]   # D0 unused->GND placeholder
j2_nets = ["CC_MISO","GND","GND","GND","+3V3","GND","+5V"]                   # D8/9/10 unused->GND placeholder
y0 = 32.0
J1x, J2x = 1.9, 19.1   # ~17.2mm row spacing — PROVISIONAL, replace w/ Seeed footprint
for jname, jx, jnets in [("J1", J1x, j1_nets), ("J2", J2x, j2_nets)]:
    pads = [tht_pad(k+1, jx, y0 + k*2.54, 1.7, 1.0, NET[jnets[k]], "rect" if k==0 else "circle")
            for k in range(7)]
    fps.append(mkfp(jname, "XIAO_SOCKET_1x7", jx, y0, pads))

# --- 2x M2 mounting holes (NPTH) at back corners, away from the antenna ---
def mnt_hole(ref, x, y):
    fp = Footprint().create_new(library_id="hat:MountingHole_M2", value="M2", reference=ref)
    fp.position = Position(x, y); fp.layer = "F.Cu"
    fp.pads = [Pad(number="", type="np_thru_hole", shape="circle",
                   position=Position(0,0), size=Position(3.2,3.2),
                   drill=DrillDefinition(diameter=2.2), layers=["*.Cu","*.Mask"])]
    return fp
fps.append(mnt_hole("H1", 2.5, 47.5))
fps.append(mnt_hole("H2", 18.5, 47.5))

board.footprints = fps

# ---- board outline (Edge.Cuts) 21 x 50 mm ----
W, H = 21.0, 50.0
corners = [(0,0),(W,0),(W,H),(0,H),(0,0)]
for (x1,y1),(x2,y2) in zip(corners, corners[1:]):
    board.graphicItems.append(GrLine(start=Position(x1,y1), end=Position(x2,y2),
                                     layer="Edge.Cuts", width=0.15))

# ---- silk ref labels for every footprint + section notes ----
for fp in fps:
    p = fp.position
    board.graphicItems.append(GrText(text=getattr(fp,'reference',''),
                                     position=Position(p.X, p.Y-2.6), layer="F.SilkS"))
notes = [("IR LEDs (aim at gear)", 10.5, 2.0), ("CC1101 - antenna off THIS edge", 10.5, 26.5),
         ("ANT KEEPOUT: no copper pour", 10.5, 28.2), ("XIAO ESP32-C3", 10.5, 30.5)]
for t, x, y in notes:
    board.graphicItems.append(GrText(text=t, position=Position(x, y), layer="F.SilkS"))
# antenna-keepout box on silk near the CC1101 antenna edge
kb = [(1,24),(20,24),(20,29),(1,29),(1,24)]
for (x1,y1),(x2,y2) in zip(kb, kb[1:]):
    board.graphicItems.append(GrLine(start=Position(x1,y1), end=Position(x2,y2),
                                     layer="F.SilkS", width=0.12))

out = "xiao-c3-ir-rf.kicad_pcb"
board.to_file(out)
print("wrote", out)

# round-trip validate (proxy for 'opens cleanly')
b2 = Board().from_file(out)
print("re-parsed OK:", len(b2.footprints), "footprints,", len(b2.nets), "nets,",
      len(b2.graphicItems), "edge segments")
