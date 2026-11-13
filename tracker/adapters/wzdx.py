"""Work Zone Data Exchange (WZDx) feeds: the USDOT standard many state and local DOTs publish.

Source config:
  {"id": "iowa", "type": "wzdx", "label": "Iowa DOT",
   "url": "https://example.gov/wzdx/feed.geojson",
   "params": {"apiKey": "${IOWA_WZDX_KEY}"},   # optional query parameters; ${VAR} reads an env variable
   "headers": {},                              # optional request headers
   "eventTypes": ["work-zone"],                # add "detour" to keep detours as their own cards
   "roadFilter": "^(I-|US |IA )",              # optional regex on the road name
   "infoUrl": "https://511ia.org/"}            # where a card's "agency map" link points
Handles WZDx v4.x (properties.core_details) and v2/v3 (flat properties). Zones are kept when any point falls
inside the region's bbox. Features that share road, description and dates (typically the two directions of one
project) become one card.
"""
import hashlib
import json
import os
import re

from ..common import clean, expand_env, finish, http_get, in_bbox, iso, parse_date, pretty_loc, rnd, simplify, title_case, today

IMPACT = {
    "all-lanes-closed": ("closed", "Road closed"),
    "some-lanes-closed": ("high", "Lane closures"),
    "some-lanes-closed-merge-left": ("high", "Lane closures, merge left"),
    "some-lanes-closed-merge-right": ("high", "Lane closures, merge right"),
    "some-lanes-closed-split": ("high", "Lane closures, traffic split"),
    "alternating-one-way": ("high", "Alternating one-way traffic"),
    "flagging": ("high", "Flagging"),
    "all-lanes-open": ("medium", "All lanes open"),
    "all-lanes-open-shift-left": ("medium", "Lanes shifted left"),
    "all-lanes-open-shift-right": ("medium", "Lanes shifted right"),
    "all-lanes-open-split": ("medium", "Lanes split"),
    "unknown": ("medium", "Work zone"),
}
DIRS = {"northbound": "Northbound", "southbound": "Southbound", "eastbound": "Eastbound", "westbound": "Westbound",
        "inner-loop": "Inner loop", "outer-loop": "Outer loop", "undefined": "", "unknown": ""}


def fetch(src, region):
    if not src.get("url"):
        raise RuntimeError("wzdx source needs a url")
    missing = [v for v in re.findall(r"\$\{(\w+)\}", json.dumps([src["url"], src.get("params"), src.get("headers")]))
               if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"set the environment variable {', '.join(missing)} (this feed needs an API key)")
    return http_get(expand_env(src["url"]), expand_env(src.get("params")), headers=expand_env(src.get("headers")))


def _props(f):
    p = f.get("properties") or {}
    core = p.get("core_details") or {}
    road = core.get("road_names") or p.get("road_names") or ([p["road_name"]] if p.get("road_name") else [])
    return {
        "event_type": core.get("event_type") or p.get("event_type") or "work-zone",
        "roads": list(dict.fromkeys(_road(r) for r in road if clean(r))),
        "direction": DIRS.get(clean(core.get("direction") or p.get("direction")).lower(),
                              title_case(core.get("direction") or p.get("direction"))),
        "name": clean(core.get("name") or p.get("name")),
        "description": clean(core.get("description") or p.get("description")),
        "start": parse_date(p.get("start_date")),
        "end": parse_date(p.get("end_date")),
        "impact": clean(p.get("vehicle_impact")).lower() or "unknown",
        "from": clean(p.get("beginning_cross_street")),
        "to": clean(p.get("ending_cross_street")),
        "work": ", ".join(title_case(str(t.get("type_name", "")).replace("-", " "))
                          for t in p.get("types_of_work") or [] if t.get("type_name")),
        "speed": (str(round(float(p["reduced_speed_limit_kph"]) * 0.621371 / 5) * 5) if p.get("reduced_speed_limit_kph")
                  else clean(p.get("reduced_speed_limit"))),
        "lanes": p.get("lanes") or [],
        "restrictions": [r.get("type") if isinstance(r, dict) else r for r in p.get("restrictions") or []],
        "id": clean(f.get("id") or p.get("road_event_id") or core.get("road_event_id")),
        "url": clean((p.get("feed_info") or {}).get("url")),
    }


def _road(name):
    """Readable road name; DOT shorthand in capitals ('RP IS270E TO MO364W') is expanded."""
    n = clean(name)
    return pretty_loc(n) if n.isupper() else n


def _segments(g):
    if not g:
        return []
    t, c = g.get("type"), g.get("coordinates") or []
    if t == "LineString":
        return [{"type": "line", "coords": [rnd(x) for x in simplify(c)]}] if len(c) >= 2 else []
    if t == "MultiLineString":
        return [{"type": "line", "coords": [rnd(x) for x in simplify(p)]} for p in c if len(p) >= 2]
    if t == "Point":
        return [{"type": "point", "coord": rnd(c)}]
    if t == "MultiPoint":
        # WZDx uses MultiPoint for begin/end points of a zone; draw a straight line between them
        return [{"type": "line", "coords": [rnd(x) for x in c]}] if len(c) >= 2 else [{"type": "point", "coord": rnd(c[0])}] if c else []
    return []


def _lanes_text(lanes):
    closed = [l for l in lanes if clean(l.get("status")).lower() == "closed" and clean(l.get("type")).lower() in
              ("general", "exit-lane", "merge-lane", "left-turning-lane", "right-turning-lane", "")]
    return f"{len(closed)}" if closed else ""


def build(feed, src, region):
    label, prefix = src.get("label", "WZDx"), src.get("idPrefix", src.get("id", "wz"))
    keep_types = set(src.get("eventTypes") or ["work-zone"])
    road_re = re.compile(src["roadFilter"]) if src.get("roadFilter") else None
    bb = region.get("bbox")
    t = today()
    groups = {}
    for f in (feed or {}).get("features", []):
        p = _props(f)
        if p["event_type"] not in keep_types:
            continue
        segs = _segments(f.get("geometry"))
        if not segs or (bb and not in_bbox(segs, bb)):
            continue
        road = p["roads"][0] if p["roads"] else "Road"
        if road_re and not road_re.search(road):
            continue
        key = (road, p["description"] or p["name"], iso(p["start"]), iso(p["end"]))
        groups.setdefault(key, []).append((p, segs))

    out = []
    for (road, _, _, _), members in groups.items():
        members.sort(key=lambda m: m[0]["id"])
        p0 = members[0][0]
        lvl, imp = min((IMPACT.get(m[0]["impact"], IMPACT["unknown"]) for m in members),
                       key=lambda x: ("closed", "high", "medium").index(x[0]))
        dirs = sorted({m[0]["direction"] for m in members} - {""})
        direction = "Both directions" if len(dirs) > 1 else (dirs[0] if dirs else "")
        location = f"{p0['from']} to {p0['to']}" if p0["from"] and p0["to"] else (p0["from"] or p0["to"])
        future = bool(p0["start"] and p0["start"] > t)
        work, desc = p0["work"], p0["description"]
        if desc.isupper() and len(desc) < 60:  # some feeds put a work type in capitals here
            work, desc = work or desc.capitalize(), ""
        if not desc:
            desc = (f"{work or 'Road work'} on {road}" + (f" {direction.lower()}" if direction and direction != "Both directions" else "")
                    + (f", {location}" if location else "") + ".")
        key = hashlib.sha1("|".join(m[0]["id"] or repr(m[1][:1]) for m in members).encode()).hexdigest()[:14]
        speed = p0["speed"]
        restr = ", ".join(sorted({str(r).replace("-", " ") for m in members for r in m[0]["restrictions"] if r}))
        out.append(finish({
            "id": f"{prefix}-{key}", "source": label, "route": road, "direction": direction, "location": location,
            "description": desc, "workType": work or ("Detour" if p0["event_type"] == "detour" else "Work zone"),
            "impact": "Planned" if future and lvl != "closed" else imp,
            "impactLevel": "future" if future and lvl != "closed" else lvl, "fullClosure": lvl == "closed",
            "lanesClosed": _lanes_text(p0["lanes"]), "restriction": restr.capitalize(),
            "speedLimit": speed or "",
            "startDate": iso(p0["start"]), "endDate": iso(p0["end"]),
            "segments": [s for m in members for s in m[1]],
            "sources": [{"kind": "feed", "name": f"{label} work zone feed (WZDx)"}],
            "sourceUrl": src.get("infoUrl") or p0["url"] or "",
        }))
    return out
