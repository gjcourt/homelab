#!/usr/bin/env python3
"""4-layer XIAO ESP32-C3 IR+RF blaster hat -- official-footprint re-layout.
Floorplan: LEDs NORTH; XIAO (official Seeed XIAO-ESP32-C3-DIP footprint) horizontal with
USB-C off the WEST edge (front); E07-M1101D CC1101 on a swappable 2x04 FEMALE socket flipped
to the BACK, module body over the XIAO back, SMA/antenna off the SOUTH edge. 4-layer:
F.Cu (parts+sig) / In1.Cu (GND) / In2.Cu (+5V) / B.Cu (sig+E07 socket).
Zones written UNFILLED; fill with fill.py (pcbnew LoadBoard) after."""
import math, sys, uuid
def uid(): return str(uuid.uuid4())
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Position, Net, Effects, Justify
from kiutils.items.brditems import Segment, Via, LayerToken
from kiutils.items.gritems import GrLine, GrText
from kiutils.items.zones import Zone, ZonePolygon, Hatch, FillSettings, KeepoutSettings

FPDIR = sys.argv[1] if len(sys.argv) > 1 else "fps"
OUT = sys.argv[2] if len(sys.argv) > 2 else "xiao-c3-ir-rf.kicad_pcb"
DEBUG = "--debug" in sys.argv

board = Board().create_new()
board.generator = "kiutils-ir4"

# ---- 4-layer stackup: insert In1.Cu (GND), In2.Cu (+5V) after F.Cu ----
rest = [Lz for Lz in board.layers if Lz.name not in ("F.Cu",)]
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
def flip_to_back(fp):
    """Move a THT footprint to the bottom side. THT holes are all-layer so positions are
    unchanged (kicad-cli uses the plain rotated local coords -- abspad must NOT mirror)."""
    fp.layer = "B.Cu"
    def sw(l):
        if l.startswith("F."): return "B." + l[2:]
        if l.startswith("B."): return "F." + l[2:]
        return l
    for p in fp.pads:
        if p.layers: p.layers = [sw(l) for l in p.layers]   # SMD copper F<->B (THT is *.Cu)
    for gi in fp.graphicItems:
        if getattr(gi, "layer", None): gi.layer = sw(gi.layer)
        if hasattr(gi, "effects"):
            if gi.effects is None: gi.effects = Effects()
            if gi.effects.justify is None: gi.effects.justify = Justify()
            gi.effects.justify.mirror = True
    return fp
def setnet(fp, num, netname):
    hit = False
    for p in fp.pads:                       # hybrid footprints have >1 pad per number
        if p.number == str(num): p.net = Net(NUM[netname], netname); hit = True
    if not hit: raise SystemExit(f"{fp.properties['Reference']}: no pad {num}")
def abspad(fp, num):
    cand = [p for p in fp.pads if p.number == str(num)]
    if not cand: raise SystemExit(f"no pad {num}")
    p = next((q for q in cand if str(q.type) == "thru_hole"), cand[0])  # prefer the hole
    a = math.radians(fp.position.angle or 0); px, py = p.position.X, p.position.Y
    return (round(fp.position.X + px*math.cos(a) + py*math.sin(a), 4),
            round(fp.position.Y - px*math.sin(a) + py*math.cos(a), 4))

# ================= placement (board 23 x 54 mm) =================
BW, BH = 23.0, 54.0
# --- IR: 3 LEDs NORTH (y6), current R (y11), MOSFET + gate parts (y15.5) ---
LEDX = [4.5, 11.5, 18.5]
D = [place("LED_D5.0mm", f"D{i+1}", "940nm", x+1.27, 6.0, 180) for i, x in enumerate(LEDX)]
R = [place("R_1206_3216Metric", f"R{i+1}", "15R", x, 11.0) for i, x in enumerate(LEDX)]
C1 = place("C_0603_1608Metric", "C1", "100nF", 2.5, 15.5)
R4 = place("R_0603_1608Metric", "R4", "100R", 6.5, 15.5)
Q1 = place("SOT-23", "Q1", "AO3400A", 11.5, 15.5)
R5 = place("R_0603_1608Metric", "R5", "10k", 16.5, 15.5)
C2 = place("C_0805_2012Metric", "C2", "22uF", 20.5, 15.5)
# --- XIAO: official Seeed XIAO-ESP32-C3-DIP footprint, rot180 so USB-C exits the WEST edge.
#     Rows 15.24 mm apart; NORTH row (y23.38) = 5V/GND/3V3/D10/D9/D8/D7 (power west, D7 east);
#     SOUTH row (y38.62) = D0..D6. ---
A1 = place("XIAO-ESP32-C3-DIP", "A1", "XIAO_ESP32C3", 11.5, 31.0, 180)
# strip the vendor footprint's own silk (it overhangs the west edge at the USB end and crosses
# its castellated pads -> cosmetic silk DRC noise); pads + our own labels are enough
A1.graphicItems = [gi for gi in A1.graphicItems
                   if not str(getattr(gi, "layer", "")).endswith("SilkS")]
# --- E07-M1101D CC1101 on a swappable 2x04 female socket, flipped to the BACK. rot90 ->
#     antenna south. NORTH row (y30) = odd pins GND/GDO0/SCK/MISO; SOUTH row (y32.54) = even
#     pins VCC/CSN/MOSI/GDO2 (module body is on the even side -> extends south). ---
M1 = place("PinSocket_2x04_P2.54mm_Vertical", "M1", "E07-M1101D", 7.69, 30.0, 90)
flip_to_back(M1)
# 3V3 decaps in the front SOUTH strip (below the XIAO, above the antenna keepout); rot270 puts
# pin1 (+3V3) north toward the bus, GND south -> plane via.
C3 = place("C_0603_1608Metric", "C3", "100nF", 9.0, 44.0, 270)
C4 = place("C_0805_2012Metric", "C4", "10uF", 12.5, 44.0, 270)

# --- net assignment ---
for i in range(3):
    setnet(D[i], 1, "+5V"); setnet(D[i], 2, f"LED{i+1}_K")
    setnet(R[i], 1, f"LED{i+1}_K"); setnet(R[i], 2, "Q_DRAIN")
setnet(Q1, 1, "Q_GATE"); setnet(Q1, 2, "GND"); setnet(Q1, 3, "Q_DRAIN")
setnet(R4, 1, "IR_TX"); setnet(R4, 2, "Q_GATE")
setnet(R5, 1, "Q_GATE"); setnet(R5, 2, "GND")
setnet(C1, 1, "+5V"); setnet(C1, 2, "GND"); setnet(C2, 1, "+5V"); setnet(C2, 2, "GND")
setnet(C3, 1, "+3V3"); setnet(C3, 2, "GND"); setnet(C4, 1, "+3V3"); setnet(C4, 2, "GND")
# E07-M1101D FIXED CDEBYTE pinout: 1 GND 2 VCC(3V3) 3 GDO0 4 CSN 5 SCK 6 MOSI 7 MISO 8 GDO2
for pin, net in [(1,"GND"),(2,"+3V3"),(3,"CC_GDO0"),(4,"CC_CSN"),(5,"CC_SCK"),
                 (6,"CC_MOSI"),(7,"CC_MISO"),(8,"CC_GDO2")]:
    setnet(M1, pin, net)
# XIAO official pad map (pad#=signal): 1 D0, 2 D1, 3 D2, 4 D3, 5 D4, 6 D5, 7 D6, 8 D7, 9 D8,
#   10 D9, 11 D10, 12 3V3, 13 GND, 14 5V(VBUS). Strapping = D0/D8/D9 (pads 1/9/10) -> kept off
#   critical lines (IR_TX on D0 is safe: 10k gate pulldown holds the FET off through boot).
XIAO = {1:"IR_TX", 3:"CC_CSN", 4:"CC_MOSI", 5:"CC_GDO2", 7:"CC_MISO", 8:"CC_SCK",
        11:"CC_GDO0", 12:"+3V3", 13:"GND", 14:"+5V"}
for pad, net in XIAO.items(): setnet(A1, pad, net)

# ---- stitch every SMD GND pad -> In1 plane, every SMD +5V pad -> In2 plane
#      (THT pads already span all layers so the pour connects to them directly) ----
def via(p, netname, layers=("F.Cu","B.Cu")):
    board.traceItems.append(Via(position=Position(round(p[0],4),round(p[1],4)),
                                size=0.7, drill=0.35, layers=list(layers),
                                net=NUM[netname], tstamp=uid()))
for fp in board.footprints:
    if fp is A1: continue                   # XIAO power pins are THT -> straight to the planes
    for p in fp.pads:
        n = getattr(p.net, "name", None)
        if n in ("GND", "+5V") and str(p.type) == "smd":
            via(abspad(fp, p.number), n)

# ================= signal routing =================
def chamfer45(pts, cmax=1.0):
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

if DEBUG:
    for fp in board.footprints:
        print(fp.properties["Reference"], getattr(fp, "layer", "F.Cu"),
              {p.number: abspad(fp, p.number) for p in fp.pads})
    sys.exit(0)

# --- IR driver (F.Cu) ---
for i in range(3):
    route([abspad(D[i], 2), abspad(R[i], 1)], f"LED{i+1}_K")        # cathode -> R (straight)
qd = abspad(Q1, 3); r2 = [abspad(R[i], 2) for i in range(3)]
BUS = 13.0
route([(r2[0][0], BUS), (r2[2][0], BUS)], "Q_DRAIN")               # straight drain bus
for p in r2: route([(p[0], BUS), p], "Q_DRAIN")                    # stubs up to each R
route([qd, (qd[0], BUS)], "Q_DRAIN")                              # drain down to bus
g = abspad(Q1, 1); r51 = abspad(R5, 1)
route([abspad(R4, 2), g], "Q_GATE")                               # R4.2 -> gate (F.Cu)
via(g, "Q_GATE"); via(r51, "Q_GATE")
route([g, r51], "Q_GATE", "B.Cu")                                 # gate -> R5 (B.Cu, clears drain)
# IR_TX: XIAO D0 (south-west) up the west edge, in along y17.5 (south of the driver row and
# clear of C1's GND via), then up into R4.1 (B.Cu)
r41 = abspad(R4, 1); d0 = abspad(A1, 1)
route([d0, (1.6, 37.0), (1.6, 17.5), (r41[0], 17.5), r41], "IR_TX", "B.Cu")
via(r41, "IR_TX")                                                 # B.Cu IR_TX -> F.Cu R4.1 pad

# --- SPI: E07 NORTH row from XIAO NORTH (from north, F.Cu); SOUTH row from XIAO SOUTH (from
#     south, B.Cu). MISO is the one north-row net fed from the south -> clean EAST-side dogleg. ---
route([abspad(A1,11), abspad(M1,3)], "CC_GDO0", "F.Cu")           # D10 -> E07.3 GDO0 (N row)
route([abspad(A1,8),  abspad(M1,5)], "CC_SCK",  "F.Cu")           # D7  -> E07.5 SCK  (N row)
route([abspad(A1,3),  abspad(M1,4)], "CC_CSN",  "B.Cu")           # D2  -> E07.4 CSN  (S row)
route([abspad(A1,4),  abspad(M1,6)], "CC_MOSI", "B.Cu")           # D3  -> E07.6 MOSI (S row)
route([abspad(A1,5),  abspad(M1,8)], "CC_GDO2", "B.Cu")           # D4  -> E07.8 GDO2 (S row)
d6 = abspad(A1,7); m7 = abspad(M1,7)                              # D6 -> E07.7 MISO (N row)
route([d6, (20.6, 34.0), (20.6, m7[1]), m7], "CC_MISO", "B.Cu")   # up the east side, in from E

# --- +3V3 (F.Cu): XIAO 3V3 down the west side of the E07 to VCC, then straight south through
#     the XIAO D1/D2 pad gap (x=7.69) to the two decaps in the south strip. ---
vcc = abspad(M1,2); c31 = abspad(C3,1); c41 = abspad(C4,1); v33 = abspad(A1,12)
route([v33, (5.6, 26.0), (5.6, 32.54), vcc], "+3V3")            # 3V3 -> west of E07 -> VCC
route([vcc, (7.69, 41.0), c31], "+3V3")                         # VCC -> south (XIAO gap) -> C3
route([c31, c41], "+3V3")                                        # C3 -> C4

# ================= board outline 23 x 54 =================
corners = [(0,0),(BW,0),(BW,BH),(0,BH)]
for a, b in zip(corners, corners[1:]+corners[:1]):
    board.graphicItems.append(GrLine(start=Position(*a),end=Position(*b),
                                     layer="Edge.Cuts",width=0.15,tstamp=uid()))
# silk hints
board.graphicItems.append(GrText(text="USB", position=Position(1.5, 21.0, 90),
                                 layer="F.SilkS", tstamp=uid()))
board.graphicItems.append(GrText(text="E07 CC1101 -> BACK",
                                 position=Position(11.5, 49.0), layer="F.SilkS", tstamp=uid()))
# E07 module body footprint on the BACK (15 x 30 mm), header at y30-32.5, body extends SOUTH;
# on B.Fab (no silk DRC), clear of the header pads.
mb = [(4.0,33.4),(19.0,33.4),(19.0,53.4),(4.0,53.4)]
for a, b in zip(mb, mb[1:]+mb[:1]):
    board.graphicItems.append(GrLine(start=Position(*a),end=Position(*b),
                                     layer="B.Fab",width=0.12,tstamp=uid()))
board.graphicItems.append(GrText(text="ANT v", position=Position(11.5, 51.5),
                                 layer="B.Fab", tstamp=uid(),
                                 effects=Effects(justify=Justify(mirror=True))))

# ================= zones =================
def zone(net, layer, poly):
    z = Zone(net=NUM[net] if net else 0, netName=net or "", layers=[layer], tstamp=uid(),
             hatch=Hatch(style="edge", pitch=0.508), minThickness=0.25,
             fillSettings=FillSettings(yes=True, thermalGap=0.508, thermalBridgeWidth=0.508),
             polygons=[ZonePolygon(coordinates=[Position(x,y) for (x,y) in poly])])
    board.zones.append(z); return z
INSET = 0.4
full = [(INSET,INSET),(BW-INSET,INSET),(BW-INSET,BH-INSET),(INSET,BH-INSET)]
zone("GND", "In1.Cu", full)     # inner GND plane
zone("+5V", "In2.Cu", full)     # inner +5V plane
# antenna keepout: no copper on ANY layer under the E07 RF/antenna region off the south edge
ko = [(3.5,46.0),(19.5,46.0),(19.5,BH-INSET),(3.5,BH-INSET)]
kz = Zone(net=0, netName="", layers=["F.Cu","In1.Cu","In2.Cu","B.Cu"], tstamp=uid(),
          hatch=Hatch(style="edge", pitch=0.508),
          keepoutSettings=KeepoutSettings(tracks="not_allowed", vias="not_allowed",
                                          pads="not_allowed", copperpour="not_allowed",
                                          footprints="allowed"),
          polygons=[ZonePolygon(coordinates=[Position(x,y) for (x,y) in ko])])
board.zones.append(kz)

board.to_file(OUT)
print(f"wrote {OUT}: {len(board.footprints)} fps, {len(board.zones)} zones, "
      f"{len([t for t in board.traceItems if isinstance(t,Via)])} vias, "
      f"layers={[Lz.name for Lz in board.layers if Lz.name.endswith('.Cu')]}")
