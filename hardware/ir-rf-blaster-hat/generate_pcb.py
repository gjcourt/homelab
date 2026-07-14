#!/usr/bin/env python3
"""4-layer XIAO ESP32-C3 IR+RF blaster hat.
Stackup: F.Cu (signals+parts) / In1.Cu (GND plane) / In2.Cu (+5V plane) / B.Cu (signals).
Real library footprints. GND/+5V go to planes via stitching vias; signals route on F/B.
Zones are written UNFILLED by kiutils; fill with fill_zones.py (pcbnew LoadBoard) after."""
import math, sys, uuid
def uid(): return str(uuid.uuid4())
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Position, Net
from kiutils.items.brditems import Segment, Via, LayerToken
from kiutils.items.gritems import GrLine, GrText
from kiutils.items.zones import Zone, ZonePolygon, Hatch, FillSettings, KeepoutSettings

FPDIR = sys.argv[1] if len(sys.argv) > 1 else "fps"
OUT = sys.argv[2] if len(sys.argv) > 2 else "xiao-c3-ir-rf.kicad_pcb"

board = Board().create_new()
board.generator = "kiutils-ir4"

# ---- 4-layer stackup: insert In1.Cu (GND), In2.Cu (+5V) after F.Cu ----
rest = [L for L in board.layers if L.name not in ("F.Cu",)]
board.layers = [LayerToken(0, "F.Cu", "signal"),
                LayerToken(1, "In1.Cu", "signal"),
                LayerToken(2, "In2.Cu", "signal")] + rest

netnames = ["", "GND", "+5V", "+3V3", "IR_TX", "Q_GATE", "Q_DRAIN",
            "LED1_K", "LED2_K", "LED3_K",
            "CC_SCK", "CC_MOSI", "CC_MISO", "CC_CSN", "CC_GDO0", "CC_GDO2"]
board.nets = [Net(i, n) for i, n in enumerate(netnames)]
NUM = {n: i for i, n in enumerate(netnames)}

def load(name): return Footprint.from_file(f"{FPDIR}/{name}.kicad_mod")
def place(name, ref, val, x, y, rot=0):
    fp = load(name)
    fp.position = Position(x, y, rot if rot else None)
    fp.version = None; fp.generator = None; fp.tedit = None; fp.tstamp = uid()
    fp.properties["Reference"] = ref; fp.properties["Value"] = val
    for gi in fp.graphicItems:
        if getattr(gi, "type", None) == "reference": gi.text = ref
    board.footprints.append(fp)
    return fp
def setnet(fp, num, netname):
    for p in fp.pads:
        if p.number == str(num): p.net = Net(NUM[netname], netname); return
    raise SystemExit(f"{fp.properties['Reference']}: no pad {num}")
def abspad(fp, num):
    for p in fp.pads:
        if p.number == str(num):
            a = math.radians(fp.position.angle or 0); px, py = p.position.X, p.position.Y
            return (round(fp.position.X + px*math.cos(a) + py*math.sin(a), 4),
                    round(fp.position.Y - px*math.sin(a) + py*math.cos(a), 4))
    raise SystemExit("no pad")
def is_smd(fp, num):
    for p in fp.pads:
        if p.number == str(num): return str(p.type) == "smd"
    return False

# ================= placement (board 21 x 50 mm) =================
# --- IR: 3 LEDs (front y6), current R (y10), MOSFET + gate parts (y14) ---
LEDX = [4.5, 11.5, 18.5]
# LED footprint origin is pin 1, so shift left by half the 2.54mm pin gap to center the body on LEDX
D = [place("LED_D5.0mm", f"D{i+1}", "940nm", x-1.27, 6.0) for i, x in enumerate(LEDX)]
R = [place("R_1206_3216Metric", f"R{i+1}", "15R", x, 12.0) for i, x in enumerate(LEDX)]
Q1 = place("SOT-23", "Q1", "AO3400A", 11.5, 17.0)
R4 = place("R_0603_1608Metric", "R4", "100R", 7.5, 17.0)
R5 = place("R_0603_1608Metric", "R5", "10k", 15.5, 17.0)
C1 = place("C_0603_1608Metric", "C1", "100nF", 3.5, 17.0)
C2 = place("C_0805_2012Metric", "C2", "22uF", 19.5, 17.0)
# --- RF: CC1101 module (2x4 header) + decoupling; antenna off the y~19 edge region ---
M1 = place("PinHeader_2x04_P2.54mm_Vertical", "M1", "CC1101", 10.23, 25.0)
C3 = place("C_0603_1608Metric", "C3", "100nF", 4.5, 21.0)
C4 = place("C_0805_2012Metric", "C4", "10uF", 18.5, 21.0)
# --- XIAO socket: two 1x7 (provisional 17.2mm row spacing; verify vs Seeed) ---
J1 = place("PinSocket_1x07_P2.54mm_Vertical", "J1", "XIAO_L", 2.9, 32.0)
J2 = place("PinSocket_1x07_P2.54mm_Vertical", "J2", "XIAO_R", 20.1, 32.0)

# --- net assignment ---
for i in range(3):
    setnet(D[i], 1, "+5V"); setnet(D[i], 2, f"LED{i+1}_K")     # anode=+5V, cathode=LEDn_K
    setnet(R[i], 1, f"LED{i+1}_K"); setnet(R[i], 2, "Q_DRAIN")
setnet(Q1, 1, "Q_GATE"); setnet(Q1, 2, "GND"); setnet(Q1, 3, "Q_DRAIN")
setnet(R4, 1, "IR_TX"); setnet(R4, 2, "Q_GATE")
setnet(R5, 1, "Q_GATE"); setnet(R5, 2, "GND")
setnet(C1, 1, "+5V"); setnet(C1, 2, "GND"); setnet(C2, 1, "+5V"); setnet(C2, 2, "GND")
# CC1101 2x4 header pin order chosen for clean (monotonic) fan-out (generic module -> wire to match):
#   left col  1=+3V3 3=GDO2 5=CSN 7=GDO0   |   right col 2=GND 4=MISO 6=SCK 8=MOSI
for pin, net in [(1,"+3V3"),(2,"GND"),(3,"CC_GDO2"),(5,"CC_CSN"),(7,"CC_GDO0"),
                 (4,"CC_MISO"),(6,"CC_SCK"),(8,"CC_MOSI")]:
    setnet(M1, pin, net)
setnet(C3, 1, "+3V3"); setnet(C3, 2, "GND"); setnet(C4, 1, "+3V3"); setnet(C4, 2, "GND")
# XIAO J1 (left, D0..D6): GND, GDO2, IR_TX, CSN, GDO0, SCK, MOSI
for k, net in enumerate(["GND","CC_GDO2","IR_TX","CC_CSN","CC_GDO0","CC_SCK","CC_MOSI"]):
    setnet(J1, k+1, net)
# XIAO J2 (right, D7..5V): MISO, GND, GND, GND, 3V3, GND, 5V
for k, net in enumerate(["CC_MISO","GND","GND","GND","+3V3","GND","+5V"]):
    setnet(J2, k+1, net)

# ---- stitch every GND pad -> In1 plane, every +5V pad -> In2 plane (SMD needs a via;
#      THT pads already span all layers so the pour connects to them directly) ----
def via(p, netname, layers=("F.Cu","In1.Cu")):
    board.traceItems.append(Via(position=Position(round(p[0],4),round(p[1],4)),
                                size=0.7, drill=0.35, layers=list(layers),
                                net=NUM[netname], tstamp=uid()))
for fp in board.footprints:
    for p in fp.pads:
        n = getattr(p.net, "name", None)
        if n == "GND" and str(p.type) == "smd":
            via(abspad(fp, p.number), "GND", ("F.Cu","B.Cu"))
        elif n == "+5V" and str(p.type) == "smd":
            via(abspad(fp, p.number), "+5V", ("F.Cu","B.Cu"))

# ================= signal routing =================
def chamfer45(pts, cmax=1.0):
    """Replace each 90 degree orthogonal corner with two 45 degree bends."""
    if len(pts) < 3: return pts
    out = [pts[0]]
    for i in range(1, len(pts)-1):
        a, v, b = pts[i-1], pts[i], pts[i+1]
        vin = (v[0]-a[0], v[1]-a[1]); vout = (b[0]-v[0], b[1]-v[1])
        lin = math.hypot(*vin); lout = math.hypot(*vout)
        perp = abs(vin[0]*vout[0] + vin[1]*vout[1]) < 1e-6
        ortho = min(abs(vin[0]),abs(vin[1])) < 1e-6 and min(abs(vout[0]),abs(vout[1])) < 1e-6
        if perp and ortho and lin > 1e-6 and lout > 1e-6:
            c = min(cmax, lin/2, lout/2)
            out.append((v[0]-vin[0]/lin*c, v[1]-vin[1]/lin*c))
            out.append((v[0]+vout[0]/lout*c, v[1]+vout[1]/lout*c))
        else:
            out.append(v)
    out.append(pts[-1])
    return out
def route(points, net, layer="F.Cu", w=0.3):
    points = chamfer45(points)
    for a, b in zip(points, points[1:]):
        board.traceItems.append(Segment(start=Position(round(a[0],4),round(a[1],4)),
                                         end=Position(round(b[0],4),round(b[1],4)),
                                         width=w, layer=layer, net=NUM[net], tstamp=uid()))
def L(a, b, net, layer="F.Cu", w=0.3):
    route([a, (b[0], a[1]), b], net, layer, w)

def via_pt(p, net, layers=("F.Cu","B.Cu")):
    board.traceItems.append(Via(position=Position(round(p[0],4),round(p[1],4)),
                                size=0.7, drill=0.35, layers=list(layers), net=NUM[net], tstamp=uid()))

# --- IR section (F.Cu): straight diagonals wherever the line is clear ---
for i in range(3):
    route([abspad(D[i], 2), abspad(R[i], 1)], f"LED{i+1}_K")       # LED cathode -> R (straight)
# Q_DRAIN: straight bus below the resistors + short perpendicular stubs (clears the gate pad)
qd = abspad(Q1, 3); r2 = [abspad(R[i], 2) for i in range(3)]
route([(r2[0][0], 13.4), (r2[2][0], 13.4)], "Q_DRAIN")            # straight bus
for p in r2: route([(p[0], 13.4), p], "Q_DRAIN")                  # stubs up to each R
route([qd, (qd[0], 13.4)], "Q_DRAIN")                            # drain down to bus
# Q_GATE: R4.2 -> Q1.gate (F.Cu); gate -> R5.1 on B.Cu (clears the drain pad between them)
g = abspad(Q1, 1); r51 = abspad(R5, 1)
route([abspad(R4, 2), g], "Q_GATE")
via_pt(g, "Q_GATE"); via_pt(r51, "Q_GATE")
route([g, r51], "Q_GATE", "B.Cu")
# IR_TX: R4.1 (SMD) via to B.Cu, run down the far-left edge to XIAO D2 (J1.3)
r41 = abspad(R4, 1); j13 = abspad(J1, 3)
via_pt(r41, "IR_TX")
route([r41, (r41[0], 19.5), (0.9, 19.5), (0.9, j13[1]), j13], "IR_TX", "B.Cu")

# --- +3V3: local M1.1<->C3<->C4 on F.Cu, long run to XIAO 3V3 (J2.5) down the right on B.Cu ---
m11 = abspad(M1,1); c31 = abspad(C3,1); c41 = abspad(C4,1); j25 = abspad(J2,5)
route([m11, (m11[0], 23.0), (c31[0], 23.0), c31], "+3V3")          # M1.VCC up + over to C3 (above M1)
route([c31, (c31[0], 19.5), (c41[0], 19.5), c41], "+3V3")          # C3 -> C4 via y19.5 (clears cap GND pads)
route([c41, (c41[0], j25[1]-(j25[0]-c41[0])), j25], "+3V3")        # C4 -> XIAO 3V3: straight drop + one 45 into the pad

# --- SPI: monotonic fan -> straight lines don't cross. Left col F.Cu, right col + MISO B.Cu ---
route([abspad(J1,2), abspad(M1,3)], "CC_GDO2", "F.Cu")            # upper J1 -> upper M1 (monotonic)
route([abspad(J1,4), abspad(M1,5)], "CC_CSN",  "F.Cu")
route([abspad(J1,5), abspad(M1,7)], "CC_GDO0", "F.Cu")
_j16 = abspad(J1,6); _m6 = abspad(M1,6)
_lane = abspad(M1,8)[0] + 1.6
route([_j16, (_lane, _j16[1]), (_lane, _m6[1]), _m6], "CC_SCK", "B.Cu")  # around M1's right side, clear of the left-col THT pads
route([abspad(J1,7), abspad(M1,8)], "CC_MOSI", "F.Cu")  # F.Cu so it can't cross SCK (B.Cu); clears the left-col fan (stays below GDO0)
route([abspad(J2,1), abspad(M1,4)], "CC_MISO", "B.Cu")

# ================= board outline 21 x 50 =================
BW, BH = 23.0, 50.0
corners = [(0,0),(BW,0),(BW,BH),(0,BH)]
for a, b in zip(corners, corners[1:]+corners[:1]):
    board.graphicItems.append(GrLine(start=Position(*a),end=Position(*b),
                                     layer="Edge.Cuts",width=0.15,tstamp=uid()))

# ================= zones =================
def zone(net, layer, poly, keepout=False):
    z = Zone(net=NUM[net] if net else 0, netName=net or "", layers=[layer], tstamp=uid(),
             hatch=Hatch(style="edge", pitch=0.508), minThickness=0.25,
             fillSettings=FillSettings(yes=True, thermalGap=0.508, thermalBridgeWidth=0.508),
             polygons=[ZonePolygon(coordinates=[Position(x,y) for (x,y) in poly])])
    board.zones.append(z)
    return z
INSET = 0.4
full = [(INSET,INSET),(BW-INSET,INSET),(BW-INSET,BH-INSET),(INSET,BH-INSET)]
zone("GND", "In1.Cu", full)     # inner GND plane
zone("+5V", "In2.Cu", full)     # inner +5V plane


board.to_file(OUT)
print(f"wrote {OUT}: {len(board.footprints)} fps, {len(board.zones)} zones, "
      f"{len([t for t in board.traceItems if isinstance(t,Via)])} plane-vias, "
      f"copper-layers={[L.name for L in board.layers if L.name.endswith('.Cu')]}")
