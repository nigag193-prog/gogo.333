"""Weather cards drawn with Pillow, styled like the iOS Weather app (dark glass cards).

render_now(place, data, units)      -> PNG bytes   big temperature, next hours, 7-day forecast with range bars
render_details(place, data, units)  -> PNG bytes   UV, sunrise arc, feels like, humidity, rain, visibility, pressure,
                                                     cloud cover, wind compass, moon phase
`data` is the Open-Meteo answer used by weather.py. Everything is drawn at 2x and scaled down (smooth edges).
"""
import datetime
import io
import math

from PIL import Image, ImageDraw, ImageFont

S = 2
W = 900
_FONTS = {}
LIGHT = ("Poppins-Light.ttf", "Inter-Light.ttf", "NotoSans-Light.ttf", "DejaVuSans.ttf")
REG = ("Poppins-Regular.ttf", "Inter-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf")
BOLD = ("Poppins-Medium.ttf", "Inter-Medium.ttf", "NotoSans-Medium.ttf", "DejaVuSans-Bold.ttf")


def font(size, weight="reg"):
    key = (size, weight)
    if key not in _FONTS:
        f = None
        for name in {"light": LIGHT, "bold": BOLD}.get(weight, REG):
            try:
                f = ImageFont.truetype(name, int(size * S))
                break
            except OSError:
                continue
        _FONTS[key] = f or ImageFont.load_default(size=int(size * S))   # scalable built-in font as a last resort
    return _FONTS[key]


# ---------------------------------------------------------------- canvas helpers
class Canvas:
    def __init__(self, height, top, bottom):
        self.h = height
        self.img = Image.new("RGB", (W * S, height * S))
        d = ImageDraw.Draw(self.img)
        for y in range(height * S):
            t = y / (height * S - 1)
            d.line([(0, y), (W * S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        self.d = ImageDraw.Draw(self.img, "RGBA")          # RGBA mode on an RGB image = real transparency

    def text(self, x, y, s, size, fill=(255, 255, 255, 255), weight="reg", anchor="la"):
        self.d.text((x * S, y * S), str(s), font=font(size, weight), fill=fill, anchor=anchor)

    def width(self, s, size, weight="reg"):
        return font(size, weight).getlength(str(s)) / S

    def glass(self, x, y, w, h, r=34, alpha=46):
        ov = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], r * S,
                                             fill=(255, 255, 255, alpha), outline=(255, 255, 255, 30), width=S)
        base = self.img.convert("RGBA")
        base.alpha_composite(ov)
        self.img = base.convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def circle(self, cx, cy, r, fill=None, outline=None, width=1):
        self.d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=fill, outline=outline, width=int(width * S))

    def line(self, pts, fill, width=2):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=int(width * S), joint="curve")

    def poly(self, pts, fill):
        self.d.polygon([(x * S, y * S) for x, y in pts], fill=fill)

    def rrect(self, x0, y0, x1, y1, r, fill):
        self.d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], r * S, fill=fill)

    def png(self):
        out = self.img.resize((W, self.h), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------- weather vocabulary
def kind_of(code):
    code = int(code)
    if code == 0 or code == 1:
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    return "thunder"


TEXT = {"clear": "Clear", "partly": "Partly cloudy", "cloud": "Cloudy", "fog": "Fog", "rain": "Rain", "snow": "Snow", "thunder": "Thunderstorm"}


def sky(kind, is_day):
    if kind == "clear":
        return ((58, 123, 213), (109, 179, 242)) if is_day else ((12, 20, 40), (28, 42, 76))
    if kind == "partly":
        return ((72, 121, 186), (120, 160, 205)) if is_day else ((18, 27, 48), (40, 55, 85))
    if kind == "cloud":
        return ((84, 98, 118), (52, 62, 78)) if is_day else ((30, 38, 52), (18, 23, 33))
    if kind == "rain":
        return ((66, 80, 98), (33, 42, 56)) if is_day else ((26, 33, 46), (14, 18, 27))
    if kind == "snow":
        return ((133, 148, 168), (88, 102, 122))
    if kind == "fog":
        return ((120, 132, 145), (77, 88, 100))
    return ((50, 48, 78), (20, 18, 32))


def cloud(c, cx, cy, s, fill=(236, 241, 248, 255)):
    c.circle(cx - 0.34 * s, cy + 0.05 * s, 0.30 * s, fill)
    c.circle(cx + 0.02 * s, cy - 0.12 * s, 0.38 * s, fill)
    c.circle(cx + 0.38 * s, cy + 0.06 * s, 0.28 * s, fill)
    c.rrect(cx - 0.64 * s, cy + 0.05 * s, cx + 0.66 * s, cy + 0.34 * s, 0.14 * s, fill)


def sun(c, cx, cy, s):
    for i in range(8):
        a = i * math.pi / 4
        c.line([(cx + math.cos(a) * 0.66 * s, cy + math.sin(a) * 0.66 * s), (cx + math.cos(a) * 0.92 * s, cy + math.sin(a) * 0.92 * s)],
               (255, 214, 92, 255), max(2, 0.1 * s))
    c.circle(cx, cy, 0.46 * s, (255, 204, 64, 255))


def moon(c, cx, cy, s, shade=(22, 30, 52, 255)):
    c.circle(cx, cy, 0.5 * s, (240, 238, 220, 255))
    c.circle(cx + 0.24 * s, cy - 0.12 * s, 0.44 * s, shade)


def icon(c, kind, cx, cy, s, is_day=1, bg=(22, 30, 52, 255)):
    """s = about the half-width of the icon in px"""
    if kind == "clear":
        sun(c, cx, cy, s * 0.8) if is_day else moon(c, cx, cy, s * 1.3, bg)
    elif kind == "partly":
        (sun(c, cx - 0.3 * s, cy - 0.3 * s, s * 0.7) if is_day else moon(c, cx - 0.3 * s, cy - 0.3 * s, s * 1.1, bg))
        cloud(c, cx + 0.1 * s, cy + 0.15 * s, s * 0.85)
    elif kind == "cloud":
        cloud(c, cx, cy, s, (214, 222, 233, 255))
    elif kind == "fog":
        for i, w in enumerate((0.9, 0.7, 0.9)):
            c.rrect(cx - w * s, cy - 0.45 * s + i * 0.5 * s, cx + w * s, cy - 0.3 * s + i * 0.5 * s, 0.08 * s, (205, 214, 226, 235))
    elif kind in ("rain", "snow", "thunder"):
        cloud(c, cx, cy - 0.2 * s, s * 0.9, (196, 206, 220, 255) if kind != "thunder" else (130, 134, 160, 255))
        if kind == "rain":
            for dx in (-0.45, 0, 0.45):
                c.line([(cx + dx * s + 0.12 * s, cy + 0.5 * s), (cx + dx * s - 0.05 * s, cy + 0.9 * s)], (96, 190, 255, 255), max(2, 0.1 * s))
        elif kind == "snow":
            for dx, dy in ((-0.4, 0.62), (0.05, 0.85), (0.42, 0.62)):
                c.circle(cx + dx * s, cy + dy * s, 0.09 * s, (255, 255, 255, 255))
        else:
            c.poly([(cx + 0.1 * s, cy + 0.35 * s), (cx - 0.25 * s, cy + 0.8 * s), (cx, cy + 0.8 * s), (cx - 0.12 * s, cy + 1.2 * s),
                    (cx + 0.3 * s, cy + 0.65 * s), (cx + 0.05 * s, cy + 0.65 * s)], (255, 208, 70, 255))


def tcolor(t_c):
    stops = [(-10, (90, 169, 255)), (0, (106, 208, 224)), (10, (123, 224, 176)), (18, (196, 224, 123)),
             (25, (255, 207, 92)), (32, (255, 146, 72)), (38, (255, 92, 77))]
    if t_c <= stops[0][0]:
        return stops[0][1] + (255,)
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t_c <= b:
            f = (t_c - a) / (b - a)
            return tuple(int(ca[i] + (cb[i] - ca[i]) * f) for i in range(3)) + (255,)
    return stops[-1][1] + (255,)


def to_c(v, units):
    return (v - 32) * 5 / 9 if units == "f" else v


def deg(v):
    return f"{round(v)}°"


def day_name(iso, i):
    return "Today" if i == 0 else datetime.date.fromisoformat(iso).strftime("%a")


def hour_index(data):
    cur = data["current"]["time"][:13]
    for i, t in enumerate(data["hourly"]["time"]):
        if t.startswith(cur):
            return i
    return 0


def moon_info(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    syn = 29.530588853
    ref = datetime.datetime(2000, 1, 6, 18, 14, tzinfo=datetime.timezone.utc)
    age = ((now - ref).total_seconds() / 86400) % syn
    phase = 2 * math.pi * age / syn
    lit = (1 - math.cos(phase)) / 2
    names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
    return {"age": age, "phase": phase, "lit": lit, "waxing": age < syn / 2, "name": names[int((age / syn * 8) + 0.5) % 8],
            "to_full": (syn / 2 - age) % syn}


# ---------------------------------------------------------------- card 1: now + hours + days
def summary_line(data, units):
    h, i0 = data["hourly"], hour_index(data)
    for k in range(i0, min(i0 + 12, len(h["time"]))):
        pop = (h.get("precipitation_probability") or [0] * 999)[k] or 0
        if pop >= 40 and kind_of(h["weather_code"][k]) in ("rain", "thunder", "snow"):
            what = "Snow" if kind_of(h["weather_code"][k]) == "snow" else "Rain"
            when = "now" if k == i0 else f"around {h['time'][k][11:16]}"
            gust = data["current"].get("wind_gusts_10m")
            return f"{what} expected {when}." + (f" Wind gusts up to {round(gust)} {'mph' if units == 'f' else 'km/h'}." if gust else "")
    return f"{TEXT[kind_of(data['current'].get('weather_code', 0))]} conditions are expected for the next hours."


def render_now(place, data, units="c"):
    cur, daily, hourly = data["current"], data["daily"], data["hourly"]
    is_day = cur.get("is_day", 1)
    kind = kind_of(cur.get("weather_code", 0))
    top, bottom = sky(kind, is_day)
    c = Canvas(1640, top, bottom)
    c.text(W / 2, 70, place["name"], 60, anchor="ma")
    c.text(W / 2, 150, deg(cur["temperature_2m"]), 210, weight="light", anchor="ma")
    c.text(W / 2, 395, TEXT[kind], 40, fill=(255, 255, 255, 215), anchor="ma")
    c.text(W / 2, 450, f"H:{deg(daily['temperature_2m_max'][0])}   L:{deg(daily['temperature_2m_min'][0])}", 36, anchor="ma")
    # hours
    c.glass(30, 520, 840, 380)
    c.text(70, 548, summary_line(data, units)[:92], 27, fill=(255, 255, 255, 235))
    c.line([(70, 625), (830, 625)], (255, 255, 255, 50), 1.5)
    i0 = hour_index(data)
    for n in range(7):
        k = i0 + n
        if k >= len(hourly["time"]):
            break
        x = 112 + n * 113
        c.text(x, 650, "Now" if n == 0 else hourly["time"][k][11:13], 27, weight="bold", anchor="ma")
        icon(c, kind_of(hourly["weather_code"][k]), x, 745, 30, hourly["is_day"][k], bottom + (255,))
        pop = (hourly.get("precipitation_probability") or [0] * 999)[k] or 0
        if pop >= 20:
            c.text(x, 790, f"{round(pop)}%", 22, fill=(96, 205, 255, 255), weight="bold", anchor="ma")
        c.text(x, 830, deg(hourly["temperature_2m"][k]), 32, anchor="ma")
    # days
    n_days = min(7, len(daily["time"]))
    c.glass(30, 930, 840, 80 + n_days * 78 + 10)
    c.text(70, 955, "7-DAY FORECAST", 24, fill=(255, 255, 255, 150), weight="bold")
    lows, highs = daily["temperature_2m_min"][:n_days], daily["temperature_2m_max"][:n_days]
    lo_all, hi_all = min(lows), max(highs)
    span = max(1, hi_all - lo_all)
    for i in range(n_days):
        y = 1015 + i * 78
        c.line([(70, y - 8), (830, y - 8)], (255, 255, 255, 40), 1.2)
        c.text(70, y + 18, day_name(daily["time"][i], i), 34)
        dk = kind_of(daily["weather_code"][i])
        icon(c, dk, 285, y + 22, 20, 1, bottom + (255,))
        pop = daily["precipitation_probability_max"][i] or 0
        if pop >= 20:
            c.text(285, y + 45, f"{round(pop)}%", 18, fill=(96, 205, 255, 255), weight="bold", anchor="ma")
        c.text(540, y + 18, deg(lows[i]), 33, fill=(255, 255, 255, 140), anchor="ra")
        x0, x1 = 565, 770
        c.rrect(x0, y + 31, x1, y + 41, 5, (0, 0, 0, 70))
        a = x0 + (lows[i] - lo_all) / span * (x1 - x0)
        b = x0 + (highs[i] - lo_all) / span * (x1 - x0)
        b = max(b, a + 10)
        steps = max(2, int(b - a))
        for s_ in range(steps):
            t_ = lows[i] + (highs[i] - lows[i]) * s_ / steps
            c.rrect(a + s_ * (b - a) / steps, y + 31, a + (s_ + 1) * (b - a) / steps + 1, y + 41, 5 if s_ in (0, steps - 1) else 0,
                    tcolor(to_c(t_, units)))
        if i == 0:
            m = a + (min(max(cur["temperature_2m"], lows[0]), highs[0]) - lo_all) / span * (x1 - x0)
            c.circle(m, y + 36, 8, (255, 255, 255, 255), (20, 30, 50, 120), 2)
        c.text(795, y + 18, deg(highs[i]), 33)
    return c.png()


# ---------------------------------------------------------------- card 2: tiles
def tile(c, x, y, w, h, title):
    c.glass(x, y, w, h, 30, 40)
    c.text(x + 26, y + 22, title.upper(), 21, fill=(255, 255, 255, 140), weight="bold")


def uv_label(v):
    return "Low" if v < 3 else "Moderate" if v < 6 else "High" if v < 8 else "Very high" if v < 11 else "Extreme"


def render_details(place, data, units="c"):
    cur, daily, hourly = data["current"], data["daily"], data["hourly"]
    is_day = cur.get("is_day", 1)
    kind = kind_of(cur.get("weather_code", 0))
    top, bottom = sky(kind, is_day)
    H = 1840
    c = Canvas(H, top, bottom)
    c.text(W / 2, 40, place["name"], 48, anchor="ma")
    c.text(W / 2, 105, f"{deg(cur['temperature_2m'])}  |  {TEXT[kind]}", 32, fill=(255, 255, 255, 215), anchor="ma")
    L, R, TW, TH, GAP = 30, 460, 410, 260, 20
    rows = [150 + i * (TH + GAP) for i in range(6)]
    i0 = hour_index(data)
    # UV
    uv = (daily.get("uv_index_max") or [0])[0] or 0
    tile(c, L, rows[0], TW, TH, "UV index")
    c.text(L + 26, rows[0] + 60, round(uv), 68)
    c.text(L + 26, rows[0] + 140, uv_label(uv), 32, weight="bold")
    for k in range(TW - 52):
        c.d.rectangle([(L + 26 + k) * S, (rows[0] + 190) * S, (L + 27 + k) * S, (rows[0] + 202) * S],
                      fill=tuple(int(a + (b - a) * (k / (TW - 52))) for a, b in zip((88, 207, 98), (176, 70, 214))) + (255,))
    c.circle(L + 26 + min(uv / 11, 1) * (TW - 52), rows[0] + 196, 9, (255, 255, 255, 255), (30, 40, 60, 150), 2)
    c.text(L + 26, rows[0] + 218, "Maximum expected today", 21, fill=(255, 255, 255, 190))
    # sunrise
    sr, ss = daily["sunrise"][0], daily["sunset"][0]
    tile(c, R, rows[0], TW, TH, "Sunrise")
    c.text(R + 26, rows[0] + 56, sr[11:16], 58)
    mid_y, amp = rows[0] + 168, 38
    pts = [(R + 8 + k * (TW - 16) / 80, mid_y - amp * math.sin(math.pi * (k / 80 * 1.6 - 0.3))) for k in range(81)]
    c.line(pts, (255, 255, 255, 120), 3)
    c.line([(R, mid_y), (R + TW, mid_y)], (255, 255, 255, 110), 2)
    try:
        t_now = datetime.datetime.fromisoformat(cur["time"])
        a_, b_ = datetime.datetime.fromisoformat(sr), datetime.datetime.fromisoformat(ss)
        frac = (t_now - a_).total_seconds() / max(1, (b_ - a_).total_seconds())
    except ValueError:
        frac = 0.5
    frac = min(max(frac, -0.18), 1.18)
    px = R + 8 + (frac + 0.3) / 1.6 * (TW - 16)
    c.circle(px, mid_y - amp * math.sin(math.pi * frac), 9, (255, 255, 255, 255), (30, 40, 60, 150), 2)
    c.text(R + 26, rows[0] + 218, f"Sunset: {ss[11:16]}", 25)
    # feels like
    feels = cur.get("apparent_temperature", cur["temperature_2m"])
    tile(c, L, rows[1], TW, TH, "Feels like")
    c.text(L + 26, rows[1] + 60, deg(feels), 76)
    diff = feels - cur["temperature_2m"]
    c.text(L + 26, rows[1] + 195, "Similar to the actual temperature." if abs(diff) < 1.5 else
           ("It feels warmer than the actual temperature." if diff > 0 else "It feels colder than the actual temperature."), 22,
           fill=(255, 255, 255, 215))
    # humidity
    hum = cur.get("relative_humidity_2m", 0)
    t_c = to_c(cur["temperature_2m"], units)
    a_, b_ = 17.62, 243.12
    g_ = math.log(max(hum, 1) / 100) + a_ * t_c / (b_ + t_c)
    dew_c = b_ * g_ / (a_ - g_)
    dew = dew_c * 9 / 5 + 32 if units == "f" else dew_c
    tile(c, R, rows[1], TW, TH, "Humidity")
    c.text(R + 26, rows[1] + 60, f"{round(hum)}%", 76)
    c.text(R + 26, rows[1] + 190, f"The dew point is {deg(dew)} right now.", 22, fill=(255, 255, 255, 215))
    # rain
    rain = (daily.get("precipitation_sum") or [0])[0] or 0
    nxt = (daily.get("precipitation_sum") or [0, 0])[1] if len(daily["time"]) > 1 else 0
    tile(c, L, rows[2], TW, TH, "Precipitation")
    c.text(L + 26, rows[2] + 60, f"{rain:.1f}", 76)
    c.text(L + 26 + c.width(f"{rain:.1f}", 76) + 12, rows[2] + 108, "mm", 34, weight="bold")
    c.text(L + 26, rows[2] + 152, "Today", 30, weight="bold")
    c.text(L + 26, rows[2] + 195, f"Tomorrow: {nxt or 0:.1f} mm expected.", 22, fill=(255, 255, 255, 215))
    # visibility
    vis_m = (hourly.get("visibility") or [10000] * 999)[i0] or 0
    vis = vis_m / 1609.34 if units == "f" else vis_m / 1000
    tile(c, R, rows[2], TW, TH, "Visibility")
    c.text(R + 26, rows[2] + 60, f"{round(vis)}", 76)
    c.text(R + 26 + c.width(round(vis), 76) + 12, rows[2] + 108, "mi" if units == "f" else "km", 34, weight="bold")
    c.text(R + 26, rows[2] + 195, "Perfectly clear view." if vis_m >= 20000 else "Good visibility." if vis_m >= 8000 else "Reduced visibility.", 22,
           fill=(255, 255, 255, 215))
    # pressure (gauge)
    pr = cur.get("pressure_msl", 1013)
    tile(c, L, rows[3], TW, TH, "Pressure")
    gx, gy, gr = L + TW / 2, rows[3] + 150, 82
    for k in range(61):
        a = math.radians(135 + k * 4.5)
        on = k / 60 <= min(max((pr - 960) / 100, 0), 1)
        c.line([(gx + math.cos(a) * (gr - 14), gy + math.sin(a) * (gr - 14)), (gx + math.cos(a) * gr, gy + math.sin(a) * gr)],
               (255, 255, 255, 235 if on else 90), 3)
    c.text(gx, gy - 28, round(pr), 44, weight="bold", anchor="ma")
    c.text(gx, gy + 22, "hPa", 24, anchor="ma")
    c.text(L + 70, rows[3] + 222, "Low", 22, anchor="ma")
    c.text(L + TW - 70, rows[3] + 222, "High", 22, anchor="ma")
    # cloud cover
    cc = cur.get("cloud_cover", 0)
    tile(c, R, rows[3], TW, TH, "Cloud cover")
    c.text(R + 26, rows[3] + 60, f"{round(cc)}%", 76)
    c.rrect(R + 26, rows[3] + 175, R + TW - 26, rows[3] + 189, 7, (0, 0, 0, 70))
    c.rrect(R + 26, rows[3] + 175, R + 26 + (TW - 52) * cc / 100, rows[3] + 189, 7, (255, 255, 255, 230))
    c.text(R + 26, rows[3] + 205, "Clear skies." if cc < 20 else "Partly cloudy." if cc < 60 else "Mostly cloudy.", 22, fill=(255, 255, 255, 215))
    # wind (full width)
    ws = cur.get("wind_speed_10m", 0)
    gust = cur.get("wind_gusts_10m", ws)
    unit = "mph" if units == "f" else "km/h"
    wd = cur.get("wind_direction_10m", 0) or 0
    tile(c, L, rows[4], 2 * TW + 20, TH, "Wind")
    for i, (lab, val) in enumerate((("Wind", f"{round(ws)} {unit}"), ("Gusts", f"{round(gust)} {unit}"),
                                    ("Direction", f"{round(wd)}°"))):
        yy = rows[4] + 75 + i * 58
        c.text(L + 26, yy, lab, 29)
        c.text(L + 520, yy, val, 29, fill=(255, 255, 255, 150), anchor="ra")
        if i < 2:
            c.line([(L + 26, yy + 42), (L + 520, yy + 42)], (255, 255, 255, 45), 1.2)
    cx, cy, cr = L + 2 * TW + 20 - 160, rows[4] + 135, 100
    for k in range(120):
        a = math.radians(k * 3)
        ln = 14 if k % 10 == 0 else 8
        c.line([(cx + math.sin(a) * (cr - ln), cy - math.cos(a) * (cr - ln)), (cx + math.sin(a) * cr, cy - math.cos(a) * cr)],
               (255, 255, 255, 190 if k % 10 == 0 else 80), 2)
    for lab, ang in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        a = math.radians(ang)
        c.text(cx + math.sin(a) * (cr - 30), cy - math.cos(a) * (cr - 30), lab, 21, weight="bold", anchor="mm")
    a = math.radians(wd)
    c.line([(cx + math.sin(a) * 38, cy - math.cos(a) * 38), (cx + math.sin(a) * (cr - 12), cy - math.cos(a) * (cr - 12))], (255, 255, 255, 255), 5)
    c.circle(cx + math.sin(a) * (cr - 12), cy - math.cos(a) * (cr - 12), 9, (255, 255, 255, 255))
    c.text(cx, cy - 20, round(ws), 40, weight="bold", anchor="ma")
    c.text(cx, cy + 22, unit, 20, anchor="ma")
    # moon (full width)
    mi = moon_info()
    tile(c, L, rows[5], 2 * TW + 20, TH, mi["name"])
    for i, (lab, val) in enumerate((("Illumination", f"{round(mi['lit'] * 100)} %"), ("Moon age", f"{mi['age']:.0f} days"),
                                    ("Next full moon", f"{round(mi['to_full'])} d"))):
        yy = rows[5] + 75 + i * 58
        c.text(L + 26, yy, lab, 29)
        c.text(L + 520, yy, val, 29, fill=(255, 255, 255, 150), anchor="ra")
        if i < 2:
            c.line([(L + 26, yy + 42), (L + 520, yy + 42)], (255, 255, 255, 45), 1.2)
    mx, my, mr = L + 2 * TW + 20 - 160, rows[5] + 140, 90
    draw_moon(c, mx, my, mr, mi)
    return c.png()


def draw_moon(c, cx, cy, r, mi):
    dark, lit = (38, 48, 68, 255), (226, 226, 214, 255)
    c.circle(cx, cy, r, dark)
    p, waxing = mi["phase"], mi["waxing"]
    for k in range(-int(r * S), int(r * S) + 1):
        dy = k / S
        hc = math.sqrt(max(r * r - dy * dy, 0))
        xt = hc * math.cos(p) if waxing else -hc * math.cos(p)
        x0, x1 = (xt, hc) if waxing else (-hc, xt)
        if x1 > x0:
            c.d.line([((cx + x0) * S, (cy + dy) * S), ((cx + x1) * S, (cy + dy) * S)], fill=lit, width=1)
    for (dx, dy, rr) in ((-0.3, -0.35, 0.16), (0.25, -0.1, 0.12), (-0.1, 0.3, 0.2), (0.35, 0.4, 0.1), (-0.45, 0.05, 0.09), (0.05, -0.55, 0.08)):
        c.circle(cx + dx * r, cy + dy * r, rr * r, (120, 124, 130, 40))
    c.circle(cx, cy, r, None, (255, 255, 255, 40), 1.5)
