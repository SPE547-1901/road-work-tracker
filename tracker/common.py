"""Shared helpers: HTTP, ArcGIS queries, text clean-up, dates, geometry (standard library only)."""
import datetime as dt
import json
import math
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

USER_AGENT = "road-work-tracker/1.0 (+https://github.com/)"
_TZ = ZoneInfo("America/Chicago")


def set_timezone(name):
    global _TZ
    _TZ = ZoneInfo(name)


def tz():
    return _TZ


def today():
    return dt.datetime.now(_TZ).date()


def now_iso():
    return dt.datetime.now(_TZ).isoformat(timespec="minutes")


def _ctx():
    ca = os.environ.get("SSL_CERT_FILE")
    return ssl.create_default_context(cafile=ca) if ca and os.path.exists(ca) else ssl.create_default_context()


def http_get(url, params=None, binary=False, tries=3, text=False, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r:
                data = r.read()
            if binary:
                return data
            return data.decode("utf-8", "replace") if text else json.loads(data)
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed: {url[:150]} :: {err}")


def query(layer, where="1=1", fields="*", env=None, offset_deg=None, precision=5):
    """All features of an ArcGIS layer query (paged), lon/lat, slimmed on the server."""
    p = {"where": where, "outFields": fields, "returnGeometry": "true", "outSR": 4326, "f": "json",
         "geometryPrecision": precision, "resultRecordCount": 1000}
    if offset_deg:
        p["maxAllowableOffset"] = offset_deg
    if env:
        p.update(geometry=json.dumps({**env, "spatialReference": {"wkid": 4326}}),
                 geometryType="esriGeometryEnvelope", inSR=4326, spatialRel="esriSpatialRelIntersects")
    feats = []
    while True:
        page = http_get(layer + "/query", {**p, "resultOffset": len(feats)})
        if "error" in page:
            raise RuntimeError(f"{layer}: {page['error']}")
        feats += page.get("features", [])
        if not page.get("exceededTransferLimit") or not page.get("features"):
            return feats


def expand_env(v):
    """'${NAME}' in config values -> the NAME environment variable (for API keys kept out of the repo)."""
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: expand_env(x) for k, x in v.items()}
    if isinstance(v, list):
        return [expand_env(x) for x in v]
    return v


def jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jdump(obj, path, pretty=False):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1 if pretty else None, separators=None if pretty else (",", ":"))


# ------------------------------------------------------------------ text
def clean(s):
    s = re.sub(r"\s+", " ", str(s)).strip() if s is not None else ""
    return "" if s.upper() in {"NULL", "NONE", "N/A"} else s


def title_case(s):
    s = clean(s)
    if not (s.isupper() or s.islower()):
        return s
    out = []
    for i, w in enumerate(s.lower().split(" ")):
        if re.fullmatch(r"(i|us|mo|il|nb|sb|eb|wb|n|s|e|w|[a-z]?\d+[a-z]?)", w):
            out.append(w.upper())
        elif i and w in {"of", "and", "to", "at", "the", "on", "in", "for"}:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


DATE_FORMATS = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"]


def parse_date(v):
    """Local date (region time zone) from epoch ms or a date string."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)) or re.fullmatch(r"\d{11,14}", str(v)):
        return dt.datetime.fromtimestamp(int(v) / 1000, tz=dt.timezone.utc).astimezone(tz()).date()
    s = clean(v)
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.match(r"\d{4}-\d{2}-\d{2}", s)
    return dt.date.fromisoformat(m.group(0)) if m else None


def iso(d):
    return d.isoformat() if d else None


DIRS = {"N": "Northbound", "S": "Southbound", "E": "Eastbound", "W": "Westbound", "B": "Both directions",
        "NB": "Northbound", "SB": "Southbound", "EB": "Eastbound", "WB": "Westbound"}
DIR_SUFFIX = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}
KEEP_UPPER = re.compile(r"(I-\d+\w?|US|MO|IL|MP|NB|SB|EB|WB|\d+\w*)")
SMALL = {"to", "of", "at", "and", "under", "over", "the", "with", "from", "near", "only", "mi"}
SUBS = [(r"\bCO LINE\b", "county line"), (r"\bRP\b", "ramp"), (r"\bCO\b(?= (I-|US |MO |IL |Route ))", "crossover"),
        (r"\bPED PED\b", "pedestrian bridge"), (r"\bBRG\b", "Bridge"), (r"\bRVR\b", "River"), (r"\bMI\b", "mi"),
        (r"\bONLY\b", "only"), (r"\bJCT W/", "junction with "), (r"\bJCT\b", "junction")]


def dir_label(d):
    d = clean(d).upper()
    return DIRS.get(d, title_case(d)) if d else ""


def pretty_loc(text):
    """DOT shorthand -> readable text: 'RP IS270E TO MO364W' -> 'Ramp I-270 EB to MO 364 WB'."""
    t = re.sub(r"^[IUSF]\d{3}[A-Z]?:\s*", "", clean(text).upper())
    if not t:
        return ""
    d = lambda m: (" " + DIR_SUFFIX[m.group(2)]) if m.group(2) else ""  # noqa: E731
    t = re.sub(r"\bIS\s?(\d+)([NSEW])?\b", lambda m: "I-" + m.group(1) + d(m), t)
    t = re.sub(r"\bUS\s?(\d+)([NSEW])?\b", lambda m: "US " + m.group(1) + d(m), t)
    t = re.sub(r"\bMO\s?(\d+)([NSEW])?\b", lambda m: "MO " + m.group(1) + d(m), t)
    t = re.sub(r"\bIL[- ]?(\d+)\b", r"IL \1", t)
    t = re.sub(r"\b(?:INT\s+|I)(\d{2,3})\b", r"I-\1", t)
    t = re.sub(r"\bRT\s?([A-Z]{1,2})\b", r"Route \1", t)
    for pat, rp in SUBS:
        t = re.sub(pat, rp, t)
    words, prev = [], ""
    for w in t.split(" "):
        parts = []
        for x in re.split(r"([-/])", w):
            if x in ("-", "/") or KEEP_UPPER.fullmatch(x) or (prev == "Route" and re.fullmatch(r"[A-Z]{1,2}", x)):
                parts.append(x)
            elif x.lower() in SMALL:
                parts.append(x.lower())
            else:
                parts.append(x if x.islower() else x[:1].upper() + x[1:].lower())
        prev = "".join(parts)
        words.append(prev)
    out = re.sub(r"O'([a-z])", lambda m: "O'" + m.group(1).upper(), " ".join(words))
    return out[:1].upper() + out[1:]


def ref_point(v):
    """MoDOT reference: '.05 mile(s) before RT TT S' -> 'Route TT'."""
    s = clean(v)
    if not s:
        return ""
    m = re.search(r"(?:before|after|at|of)\s+(.+)$", s, re.I)
    ref = re.sub(r"\s+[NSEW]$", "", (m.group(1) if m else s).strip().upper())
    ref = re.sub(r"^RR\s+", "rail crossing ", re.sub(r"^(CRD|CST|LRD|PRD)\s+", "", ref))
    return pretty_loc(ref)


def norm_job(v):
    """'J6I3033', '6i3033' -> '6I3033'; placeholders like '00000000' -> ''."""
    j = re.sub(r"[^A-Za-z0-9]", "", clean(v)).upper()
    if not j or set(j) <= {"0"} or j in {"NA", "NONE", "TBD"}:
        return ""
    return j[1:] if j.startswith("J") and len(j) >= 6 else j


def route_family(route):
    return set(re.findall(r"(\d+)", clean(route)))


# ------------------------------------------------------------------ geometry
R = 6378137.0


def merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(max(min(lat, 85), -85)) / 2))


def unmerc(x, y):
    return [round(math.degrees(x / R), 5), round(math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2), 5)]


def simplify(coords, tol=6.0):
    """Douglas-Peucker in mercator metres."""
    if len(coords) < 3:
        return coords
    pts = [merc(*c) for c in coords]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        best, idx = 0.0, None
        for i in range(a + 1, b):
            dd = abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L
            if dd > best:
                best, idx = dd, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [c for c, k in zip(coords, keep) if k]


def rnd(c):
    return [round(c[0], 5), round(c[1], 5)]


def esri_geom(g):
    if not g:
        return []
    if "paths" in g:
        return [{"type": "line", "coords": [rnd(c) for c in simplify(p)]} for p in g["paths"] if len(p) >= 2]
    if g.get("x") is not None:
        return [{"type": "point", "coord": rnd([g["x"], g["y"]])}]
    return [{"type": "point", "coord": rnd(p)} for p in g.get("points", [])]


def seg_pts(segs):
    for s in segs:
        yield from (s["coords"] if s["type"] == "line" else [s["coord"]])


def bbox_of(segs):
    pts = list(seg_pts(segs))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def bbox_dist(b1, b2):
    (ax0, ay0), (ax1, ay1) = merc(b1[0], b1[1]), merc(b1[2], b1[3])
    (bx0, by0), (bx1, by1) = merc(b2[0], b2[1]), merc(b2[2], b2[3])
    return math.hypot(max(bx0 - ax1, ax0 - bx1, 0), max(by0 - ay1, ay0 - by1, 0))



def pip(x, y, rings):
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
                inside = not inside
    return inside


def county_for(segs, counties, name_field="COUNTY_NAM"):
    """Name of the first polygon (ArcGIS feature with rings) holding the zone's middle, start or end point."""
    pts = list(seg_pts(segs))
    for x, y in (pts[len(pts) // 2], pts[0], pts[-1]) if pts else ():
        for c in counties:
            if pip(x, y, c["geometry"]["rings"]):
                return title_case(c["attributes"][name_field])
    return None


def in_bbox(segs, bb):
    """True if any point of the zone falls inside [minLon, minLat, maxLon, maxLat]."""
    return any(bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3] for x, y in seg_pts(segs))


def status_for(p):
    t = today().isoformat()
    if p.get("startDate") and p["startDate"] > t:
        return "upcoming"
    if p.get("endDate") and p["endDate"] < t and not p.get("stillReported"):
        return "ended"
    return "active"


def finish(p):
    """Fill the fields every project carries, so the page never has to guess."""
    base = {"direction": "", "location": "", "counties": [], "nearTown": "", "description": "", "workType": "",
            "impact": "", "impactLevel": "medium", "fullClosure": False, "lanesClosed": "", "hours": "", "detour": "",
            "restriction": "", "startDate": None, "endDate": None, "sources": [], "sourceUrl": ""}
    for k, v in base.items():
        p.setdefault(k, v)
    p["bbox"] = bbox_of(p["segments"])
    return p
