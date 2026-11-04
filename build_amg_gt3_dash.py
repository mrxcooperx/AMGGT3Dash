"""
Generates a SimHub Dash Studio dashboard replicating the iRacing Mercedes-AMG GT3 2020
dash (Bosch DDU 9 in its housing with the shift-light pod) for a tablet.

Layout is measured from in-game screenshots (front view, 502x424) and scaled by K, so
every box, label, LED and icon sits where it does in the car. SimHub scales the dash to
fit the tablet.

Two SimHub screens = the car's two dash pages (switch with SimHub's next/previous screen
controls), numbered in the bottom bar like the car:
  Page 1: T Oil, T Gear, Lap Time
  Page 2: tyre pressures + temperatures (fronts: pressure over temp, rears: temp over
          pressure)
Both pages: T Water, V Batt, LaptimeDiff, Speed, Gear, ABS / TC / MAP, Fuel; top row =
RPM bar (0-8), which becomes "PITSPEED ACTIVE <speed>" with the pit limiter on (blue
under the limit, red over); bottom bar with the headlight-flash indicator (lights up while
you flash), rain-light icon and page number; 10 shift lights in the iRacing manual's pattern (outside in: 2 green 6180,
4 green 6350, 2 yellow 6520, 4 yellow 6690, 2 red 6860, all red flashing 7030 rpm;
centre six alternate blue / yellow with the pit limiter on).

Run:  python build_amg_gt3_dash.py
Out:  "Mercedes-AMG GT3 2020.simhubdash"  -> SimHub > Dash Studio > Import
"""
import hashlib
import io
import json
import os
import shutil
import uuid
import zipfile

from PIL import Image, ImageDraw, ImageFont

NAME = "Mercedes-AMG GT3 2020"
FONT = "Arial"

# ── Screenshot -> dash coordinates ─────────────────────────────
# Positions below are in screenshot pixels (u, v); the dash is the screenshot area
# u 48..492, v 22..418 scaled by K.
K = 3.5
OX, OY = 48, 22
W, H = round((492 - OX) * K), round((418 - OY) * K)


def X(u):
    return (u - OX) * K


def Y(v):
    return (v - OY) * K


def S(n):
    """Size in screenshot pixels -> dash pixels."""
    return n * K


# ── Palette (sampled from the screenshots) ─────────────────────
HOUSING = "#FF16181B"
POD = "#FF0C0D0F"
SCREEN = "#FF000000"
EDGE = "#FFA8A8A8"
LABEL = "#FFC9C9C9"
VALUE = "#FFD6D6D6"
C_ABS = "#FFC9C14A"
C_TC = "#FF3FC0C8"
C_MAP = "#FFD9773A"
RPM_BLUE = "#FF2F6FD6"
RPM_TRACK = "#FF151515"
PIT_BLUE = "#FF2F5FB4"
PIT_RED = "#FFC62828"
PIT_TEXT = "#FFCFCFCF"
LED_OFF = "#FF3A3C40"
LED_GREEN = "#FF2BD84A"
LED_YELLOW = "#FFF6E61C"
LED_RED = "#FFFF2A2A"
LED_BLUE = "#FF2D6BFF"
ICON_GREEN = (46, 158, 58)
ICON_ORANGE = (185, 122, 42)

# ── Property paths ─────────────────────────────────────────────
ND = "DataCorePlugin.GameData.NewData."
TELE = "DataCorePlugin.GameRawData.Telemetry."
WEEKEND = "DataCorePlugin.GameRawData.SessionData.WeekendInfo."

_sid = 0
_FONTS = {}


def _font(bold, size):
    key = (bold, round(size))
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", max(1, round(size)))
    return _FONTS[key]


def text_width(s, size, bold=True):
    return _font(bold, size).getlength(s)


def _ids():
    global _sid
    _sid += 1
    return {"Id": str(uuid.uuid4()), "Sid": _sid}


def ncalc(expr):
    return {"Formula": {"Expression": expr}, "Mode": 2}


def js(expr):
    return {"Formula": {"JSExt": 0, "Interpreter": 1, "Expression": expr}, "Mode": 2}


def bindings(**kw):
    out = {}
    for prop, b in kw.items():
        if b is not None:
            b = dict(b)
            b["TargetPropertyName"] = prop
            out[prop] = b
    return out


def rect(name, x, y, w, h, color, border=0, border_color=None, radius=0, binds=None):
    # SimHub reads border widths and corner radii as whole numbers - a value like 49.0
    # makes the whole dashboard fail to load
    border, radius = int(round(border)), int(round(radius))
    style = {}
    if border:
        style.update({"BorderColor": border_color, "BorderTop": border, "BorderBottom": border,
                      "BorderLeft": border, "BorderRight": border})
    if radius:
        style.update({"RadiusTopLeft": radius, "RadiusTopRight": radius,
                      "RadiusBottomLeft": radius, "RadiusBottomRight": radius})
    r = {
        "$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.RectangleItem, SimHub.Plugins",
        "IsRectangleItem": True, "BackgroundColor": color, "BorderStyle": style,
        "Height": round(h, 2), "Left": round(x, 2), "Top": round(y, 2), "Visible": True, "Width": round(w, 2),
        "Name": name, **_ids(),
    }
    if binds:
        r["Bindings"] = binds
    return r


def text(name, x, y, w, h, txt, size, color=VALUE, halign=1, valign=1, bg="#00FFFFFF", bold=True, binds=None):
    # SimHub clips text taller than its box: grow the box (keeping its centre) to fit Arial
    need = size * 1.2
    if h < need:
        y -= (need - h) / 2
        h = need
    t = {
        "$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.TextItem, SimHub.Plugins",
        "IsTextItem": True, "Font": FONT, "FontWeight": "Bold" if bold else "Normal", "TextPadding": {},
        "FontStyle": "Normal", "FontSize": round(size, 1), "Text": txt, "TextColor": color,
        "HorizontalAlignment": halign, "VerticalAlignment": valign,   # 0 left/top, 1 centre, 2 right/bottom
        "BackgroundColor": bg, "BorderStyle": {},
        "Height": round(h, 2), "Left": round(x, 2), "Top": round(y, 2), "Visible": True, "Width": round(w, 2),
        "Name": name, **_ids(),
    }
    if binds:
        t["Bindings"] = binds
    return t


def image(name, img_name, x, y, w, h, opacity=None, binds=None):
    i = {
        "$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.ImageItem, SimHub.Plugins",
        "Image": img_name, "AutoSize": False, "BackgroundColor": "#00FFFFFF",
        "Height": round(h, 2), "Left": round(x, 2), "Top": round(y, 2), "Visible": True, "Width": round(w, 2),
        "Name": name, **_ids(),
    }
    if opacity is not None:
        i["Opacity"] = float(opacity)
    if binds:
        i["Bindings"] = binds
    return i


def layer(name, children, binds=None):
    xs = [c["Left"] for c in children]
    ys = [c["Top"] for c in children]
    lay = {
        "$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.Layer, SimHub.Plugins",
        "Height": max(c["Top"] + c["Height"] for c in children) - min(ys),
        "Width": max(c["Left"] + c["Width"] for c in children) - min(xs),
        "BackgroundColor": "#00FFFFFF", "BorderStyle": {}, "Childrens": children, "Group": True,
        "Left": min(xs), "Top": min(ys), "Visible": True, "Name": name, **_ids(),
    }
    if binds:
        lay["Bindings"] = binds
    return lay


LABEL_SIZE = S(15.4)          # label cap height ~11 px in the screenshot


def box(name, u0, v0, u1, v1, label=None, edge=EDGE, value=None):
    """DDU-style box: thin outline with the label centred in a notch on the top edge.
    value = (placeholder, size_px, binding, bold, field) where field (e.g. "000.0") makes
    the value right-aligned inside a field of that width centred in the box - that's how
    the DDU lines numbers up (e.g. speed "  0" sits right of centre)."""
    x, y, w, h = X(u0), Y(v0), S(u1 - u0), S(v1 - v0)
    kids = [rect(f"{name}_box", x, y, w, h, SCREEN, border=round(S(0.9)), border_color=edge)]
    if label:
        lw = text_width(label, LABEL_SIZE) + S(3)
        kids.append(text(f"{name}_label", x + (w - lw) / 2, y - LABEL_SIZE * 0.2, lw, LABEL_SIZE * 1.2,
                         label, LABEL_SIZE, LABEL, bg=SCREEN))
    if value:
        ph, size, binding, bold, field = value
        top = y + (LABEL_SIZE * 0.9 if label else 0)
        vh = y + h - top
        if field:
            fw = text_width(field, size, bold) + 2
            kids.append(text(f"{name}_value", x + (w - fw) / 2, top, fw, vh, ph, size, VALUE, halign=2,
                             bold=bold, binds=bindings(Text=binding)))
        else:
            kids.append(text(f"{name}_value", x, top, w, vh, ph, size, VALUE, bold=bold,
                             binds=bindings(Text=binding)))
    return layer(name, kids)


# ── JS snippets ────────────────────────────────────────────────
UNITS_JS = f"""var u = $prop('{TELE}DisplayUnits');
var imperial = (u !== null && u !== undefined) ? (u == 0)
  : String($prop('{ND}SpeedLocalUnit') || '').toUpperCase() == 'MPH';
"""


def temp_js(raw_names, nd=None):
    """iRacing reports C; F in imperial like the car. First raw channel that exists."""
    names = ", ".join(f"'{TELE}{n}'" for n in raw_names)
    fallback = (f"var v = $prop('{ND}{nd}');\n  return (v === null || v === undefined) ? '-' : format(v, '0');"
                if nd else "return '-';")
    return UNITS_JS + f"""var names = [{names}], c = null;
for (var i = 0; i < names.length && (c === null || c === undefined); i++) c = $prop(names[i]);
if (c === null || c === undefined) {{
  {fallback}
}}
return format(imperial ? c * 9 / 5 + 32 : c, '0');"""


VOLT_JS = f"""var v = $prop('{TELE}Voltage');
return (v === null || v === undefined) ? '-' : format(v, '0.0');"""

LAP_TIME_JS = f"""var s = timespantoseconds($prop('{ND}LastLapTime'));
if (!(s > 0)) return '00:00.00';
var m = Math.floor(s / 60), r = s - m * 60;
return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r.toFixed(2);"""

# delta to best lap: iRacing's own channel first, SimHub's session-best delta otherwise;
# blank until there's a best lap (as in the car)
DELTA_JS = f"""var d = $prop('{TELE}LapDeltaToBestLap');
if (d === null || d === undefined || $prop('{TELE}LapDeltaToBestLap_OK') === false)
  d = $prop('PersistantTrackerPlugin.SessionBestLiveDeltaSeconds');
if (d === null || d === undefined) return '';
return (d >= 0 ? '+' : '-') + format(Math.abs(d), '0.00');"""

SPEED_JS = f"return format($prop('{ND}SpeedLocal') || 0, '0');"

# Headlight flash indicator: full brightness while flashing, dim the rest of the time
HEADLIGHT_DIM = 22
HEADLIGHT_JS = f"return $prop('{TELE}dcHeadlightFlash') ? 100 : {HEADLIGHT_DIM};"

FUEL_JS = UNITS_JS + f"""var f = $prop('{ND}Fuel');
if (f === null || f === undefined) return '-';
return format(imperial ? f * 0.264172 : f, '0.0');"""


def dc_js(names):
    lst = ", ".join(f"'{TELE}{n}'" for n in names)
    return f"""var names = [{lst}];
for (var i = 0; i < names.length; i++) {{
  var v = $prop(names[i]);
  if (v !== null && v !== undefined) return format(v, '0');
}}
return '-';"""


def tyre_js(corner, what):
    """Tyre pressure (psi or bar) / temperature (F or C). iRacing exposes several tyre
    channels and which ones are live varies: first that exists and isn't 0."""
    c, n = corner
    if what == "press":
        raw, nd, out, fmt = (f"['{TELE}{c}pressure', '{TELE}{c}press', '{TELE}{c}coldPressure']",
                             f"TyrePressure{n}",
                             "return imperial ? format(v * 0.145038, '0.0') : format(v / 100, '0.00');", "0.0")
    else:
        raw, nd, out, fmt = (f"['{TELE}{c}tempM', '{TELE}{c}tempCM']", f"TyreTemperature{n}",
                             "return format(imperial ? v * 9 / 5 + 32 : v, '0');", "0")
    return UNITS_JS + f"""var names = {raw}, v = null;
for (var i = 0; i < names.length; i++) {{
  var x = $prop(names[i]);
  if (x !== null && x !== undefined && Number(x) != 0) {{ v = Number(x); break; }}
}}
if (v === null) {{
  var s = $prop('{ND}{nd}');
  return (s === null || s === undefined) ? '-' : format(s, '{fmt}');
}}
{out}"""


PIT_LIMIT_KMH = f"""var lim = $prop('{ND}PitLimiterSpeed');
if (!(lim > 0)) {{
  var s = $prop('{WEEKEND}TrackPitSpeedLimit');   // iRacing: "72.00 kph"
  lim = s ? parseFloat(s) : 0;
  if (s && String(s).indexOf('mph') >= 0) lim *= 1.609344;
}}
"""
PIT_ON_EXPR = f"[{ND}PitLimiterOn]"
PIT_COLOR_JS = PIT_LIMIT_KMH + f"""var spd = $prop('{ND}SpeedKmh') || 0;
return (lim > 0 && spd > lim + 0.5) ? '{PIT_RED}' : '{PIT_BLUE}';"""
PIT_SPEED_JS = (f"return format($prop('{ND}SpeedLocal') || 0, '0') + "
                f"String($prop('{ND}SpeedLocalUnit') || 'mph').toLowerCase().replace('kmh', 'km/h');")

# Shift lights, iRacing manual pattern. d = pair number from the outside (0..4).
SHIFT_RPM = [6180, 6350, 6520, 6690, 6860]
SHIFT_COLOR = [LED_GREEN, LED_GREEN, LED_YELLOW, LED_YELLOW, LED_RED]
FLASH_RPM = 7030
SHIFT_LED_JS = """var d = {d}, i = {i};
var rpm = $prop('{nd}Rpms') || 0;
if ($prop('{nd}PitLimiterOn')) {{
  if (d < 2) return '{off}';                      // outer four stay dark
  return ((i % 2 == 0) == blink('amgpit', 300, true)) ? '{yellow}' : '{blue}';
}}
if (rpm >= {flash}) return blink('amgshift', 100, true) ? '{red}' : '{off}';
return rpm >= {on} ? '{color}' : '{off}';"""

RPM_MAX = 8000
SAMPLE_RPM = 3200

TYRES = [("LF", "FrontLeft"), ("RF", "FrontRight"), ("LR", "RearLeft"), ("RR", "RearRight")]


# ── Static icons (drawn 4x, downsampled) ───────────────────────
def draw_headlight(w, h):
    """Low-beam symbol: four slanted beams + a 'D' lamp body."""
    s = 4
    im = Image.new("RGBA", (round(w) * s, round(h) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    W_, H_ = im.size
    lw = max(2, round(W_ * 0.055))
    body_x = W_ * 0.42
    d.line([(body_x, H_ * 0.12), (body_x, H_ * 0.88)], fill=ICON_GREEN + (255,), width=lw)
    d.arc([body_x - W_ * 0.5, H_ * 0.12, W_ * 0.97, H_ * 0.88], -90, 90, fill=ICON_GREEN + (255,), width=lw)
    for k in range(4):
        y0 = H_ * (0.24 + k * 0.17)
        d.line([(W_ * 0.04, y0 + H_ * 0.05), (W_ * 0.34, y0 - H_ * 0.03)], fill=ICON_GREEN + (255,), width=lw)
    return im.resize((round(w), round(h)), Image.LANCZOS)


def draw_rainlight(w, h):
    """Rear fog / rain light symbol: lamp body facing left, wavy line, three beams."""
    s = 4
    im = Image.new("RGBA", (round(w) * s, round(h) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    W_, H_ = im.size
    lw = max(2, round(W_ * 0.06))
    body_x = W_ * 0.42
    d.line([(body_x, H_ * 0.12), (body_x, H_ * 0.88)], fill=ICON_ORANGE + (255,), width=lw)
    d.arc([W_ * 0.03, H_ * 0.12, body_x * 2 - W_ * 0.03, H_ * 0.88], 90, 270, fill=ICON_ORANGE + (255,), width=lw)
    pts = []
    for k in range(41):
        t = k / 40
        pts.append((W_ * 0.72 + W_ * 0.06 * ((-1) ** int(t * 6) * (t * 6 % 1 - 0.5)), H_ * (0.06 + 0.88 * t)))
    d.line(pts, fill=ICON_ORANGE + (255,), width=lw)
    for k in range(3):
        y0 = H_ * (0.3 + k * 0.2)
        d.line([(W_ * 0.5, y0), (W_ * 0.97, y0)], fill=ICON_ORANGE + (255,), width=lw)
    return im.resize((round(w), round(h)), Image.LANCZOS)


# ── Build ──────────────────────────────────────────────────────
def build_screen(page):
    items = []

    # Housing, shift-light pod, screen
    items.append(rect("Housing", 0, 0, W, H, HOUSING, radius=S(14)))
    items.append(rect("LedPod", X(176), Y(33), S(188), S(29), POD, radius=S(14)))
    items.append(rect("Screen", X(63), Y(90), S(416), S(320), SCREEN, radius=S(3)))

    # Shift lights: 10 LEDs (two dim outer pairs on the housing, six in the pod)
    led_u = [103, 135, 192, 224, 256, 286, 318, 349, 405, 436]
    led_v = [55, 52, 47, 47, 47, 47, 47, 47, 52, 55]
    leds = []
    for i, (u, v) in enumerate(zip(led_u, led_v)):
        d = min(i, 9 - i)
        expr = SHIFT_LED_JS.format(d=d, i=i - 2, nd=ND, off=LED_OFF, yellow=LED_YELLOW, blue=LED_BLUE,
                                   red=LED_RED, flash=FLASH_RPM, on=SHIFT_RPM[d], color=SHIFT_COLOR[d])
        r = S(8.5)
        leds.append(rect(f"ShiftLED{i + 1:02d}", X(u) - r, Y(v) - r, 2 * r, 2 * r, LED_OFF, radius=r,
                         binds=bindings(BackgroundColor=js(expr))))
    items.append(layer("ShiftLights", leds))

    # Top row: RPM bar 0-8 (normal state)
    u0, u1 = 72, 470
    span = S(u1 - u0)
    rpm = [rect("Rpm_track", X(u0), Y(121), span, S(17), RPM_TRACK),
           rect("Rpm_fill", X(u0), Y(121), span * SAMPLE_RPM / RPM_MAX, S(17), RPM_BLUE,
                binds=bindings(Width=ncalc(f"max(0, min(1, [{ND}Rpms] / {RPM_MAX})) * {round(span, 2)}"))),
           rect("Rpm_base", X(u0), Y(119), span, S(1.2), EDGE)]
    for k in range(9):
        x = X(u0) + span * k / 8
        rpm.append(rect(f"Rpm_tick{k}", x - S(0.6), Y(113), S(1.2), S(7), EDGE))
        rpm.append(text(f"Rpm_num{k}", x - S(10), Y(98), S(20), S(14), str(k), S(13), LABEL))
    items.append(layer("RpmBar", rpm))

    # Row 2: T Water, V Batt, LaptimeDiff, Speed
    items.append(box("TWater", 67, 160, 133, 217, "T Water",
                     value=("171", S(28.5), js(temp_js(["WaterTemp"], "WaterTemperature")), True, "000")))
    items.append(box("VBatt", 141, 160, 206, 217, "V Batt",
                     value=("14.2", S(28.5), js(VOLT_JS), True, "00.0")))
    items.append(box("LaptimeDiff", 213, 151, 328, 212, "LaptimeDiff",
                     value=("", S(30), js(DELTA_JS), True, None)))
    items.append(box("Speed", 335, 150, 475, 210, "Speed",
                     value=("0", S(39), js(SPEED_JS), False, "000")))

    # Row 3, left: page-specific
    if page == 1:
        items.append(box("TOil", 67, 224, 133, 281, "T Oil",
                         value=("174", S(28.5), js(temp_js(["OilTemp"], "OilTemperature")), True, "000")))
        # iRacing has no gearbox temperature channel for most cars - '-' when missing
        items.append(box("TGear", 141, 224, 206, 281, "T Gear",
                         value=("121", S(28.5), js(temp_js(["GearboxTemp", "GearboxOilTemp", "TransmissionTemp"])),
                                True, "000")))
        items.append(box("LapTime", 67, 285, 206, 342, "Lap Time",
                         value=("00:00.00", S(29), js(LAP_TIME_JS), True, None)))
    else:
        rows = [(0, "press", 239), (0, "temp", 272), (2, "temp", 305), (2, "press", 338)]
        col_right = [128, 200]
        tyre = []
        for base, what, v in rows:
            for col in range(2):
                corner = TYRES[base + col]
                tyre.append(text(f"Tyre_{corner[0]}_{what}", X(col_right[col] - 64), Y(v - 16), S(64), S(32),
                                 "23.0" if what == "press" else "94", S(28.5), VALUE, halign=2,
                                 binds=bindings(Text=js(tyre_js(corner, what)))))
        items.append(layer("Tyres", tyre))

    # Gear
    items.append(box("Gear", 225, 221, 316, 347, "Gear",
                     value=("N", S(91), ncalc(f"isnull([{ND}Gear],'N')"), True, None)))

    # ABS / TC / MAP
    for name, (a, b), edge, ph, names in [
            ("ABS", (335, 377), C_ABS, "10", ["dcABS"]),
            ("TC", (385, 427), C_TC, "10", ["dcTractionControl"]),
            ("MAP", (434, 476), C_MAP, "3", ["dcEngineMap", "dcThrottleShape", "dcFuelMixture"])]:
        items.append(box(name, a, 218, b, 275, name, edge=edge,
                         value=(ph, S(28), js(dc_js(names)), True, None)))

    # Fuel
    items.append(box("Fuel", 336, 284, 477, 343, "Fuel",
                     value=("14.0", S(39), js(FUEL_JS), False, "000.0")))

    # Bottom bar: headlight + rain-light icons, page number
    items.append(layer("BottomBar", [
        rect("Bottom_box", X(67), Y(352), S(411), S(52), SCREEN, border=round(S(0.9)), border_color=EDGE),
        # lit while you flash the headlights (iRacing dcHeadlightFlash), dim otherwise
        image("Headlight_icon", "amg_headlight", X(234), Y(361), S(40), S(32), opacity=HEADLIGHT_DIM,
              binds=bindings(Opacity=js(HEADLIGHT_JS))),
        image("Rainlight_icon", "amg_rainlight", X(326), Y(361), S(31), S(32)),
        text("PageNumber", X(440), Y(360), S(26), S(20), str(page), S(15), LABEL, halign=2, bold=False),
    ]))

    # Pit limiter: the top row becomes the PITSPEED bar (blue under the limit, red over)
    items.append(layer("PitSpeedBar", [
        rect("Pit_bg", X(66), Y(94), S(410), S(54), PIT_BLUE, border=round(S(1)), border_color="#FF8FA8D8",
             binds=bindings(BackgroundColor=js(PIT_COLOR_JS))),
        text("Pit_text", X(78), Y(98), S(292), S(46), "PITSPEED ACTIVE", S(28), PIT_TEXT, halign=0),
        text("Pit_speed", X(330), Y(98), S(134), S(46), "0mph", S(28), PIT_TEXT, halign=2,
             binds=bindings(Text=js(PIT_SPEED_JS))),
    ], binds=bindings(Visible=ncalc(PIT_ON_EXPR))))
    return items


def main():
    images = {"amg_headlight": draw_headlight(S(40), S(32)), "amg_rainlight": draw_rainlight(S(31), S(32))}
    meta_images, blobs = [], {}
    for name, im in images.items():
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        blobs[name + ".png"] = data
        meta_images.append({"Name": name, "Extension": ".png", "Modified": False, "Optimized": True,
                            "Width": im.width, "Height": im.height, "Length": len(data),
                            "MD5": hashlib.md5(data).hexdigest()})

    metadata = {
        "ScreenCount": 2.0, "InGameScreensIndexs": [0, 1], "IdleScreensIndexs": [0], "MainPreviewIndex": 0,
        "IsOverlay": False, "ShowInTaskBar": True, "Width": float(W), "Height": float(H),
        "OverlaySizeWarning": True, "MetadataVersion": 2.0, "EnableOnDashboardMessaging": True,
        "PitScreensIndexs": [], "Title": NAME, "Author": "",
        "Description": "Mercedes-AMG GT3 2020 (iRacing, Bosch DDU 9) dash: page 1 engine temps + lap time, "
                       "page 2 tyres, RPM / PITSPEED bar, shift lights.",
    }
    dash = {
        "Version": 2,
        "Id": "a3b6e2d0-7c41-4f0a-9b1e-4d6703020001",  # fixed so re-imports replace the old copy
        "BaseHeight": H, "BaseWidth": W, "BackgroundColor": "#FF000000",
        "Screens": [{
            "InGameScreen": True, "IdleScreen": page == 1, "PitScreen": False,
            "ScreenId": f"a3b6e2d0-7c41-4f0a-9b1e-4d670302{page}002",
            "IsForegroundLayer": False, "IsBackgroundLayer": False, "BackgroundColor": "#FF000000",
            "Items": build_screen(page),
            "Id": f"a3b6e2d0-7c41-4f0a-9b1e-4d670302{page}003", "Name": f"Page {page}", "Sid": 0,
        } for page in (1, 2)],
        "SnapToGrid": False, "HideLabels": False, "ShowForeground": True, "ForegroundOpacity": 50.0,
        "ShowBackground": True, "BackgroundOpacity": 50.0, "ShowBoundingRectangles": False, "GridSize": 10,
        "Images": meta_images, "Metadata": metadata, "ShowOnScreenControls": True, "ShowInTaskBar": True,
        "IsOverlay": False, "EnableClickThroughOverlay": True, "EnableOnDashboardMessaging": True,
    }

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, NAME)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)  # generated output - start clean
    os.makedirs(out_dir)
    base = os.path.join(out_dir, NAME)
    with open(base + ".djson", "w", encoding="utf-8") as f:
        json.dump(dash, f, indent=2, ensure_ascii=False)
    with open(base + ".djson.metadata", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    with open(base + ".djson.carclasses", "w", encoding="utf-8") as f:
        f.write("[]")
    with zipfile.ZipFile(base + ".djson.ressources", "w", zipfile.ZIP_DEFLATED) as z:
        for fn, data in blobs.items():
            z.writestr(fn, data)
    print("Wrote", base + ".djson", f"({W}x{H})")

    pkg = os.path.join(here, NAME + ".simhubdash")
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(out_dir)):
            z.write(os.path.join(out_dir, fn), f"{NAME}/{fn}")
    print("Wrote", pkg)


if __name__ == "__main__":
    main()
