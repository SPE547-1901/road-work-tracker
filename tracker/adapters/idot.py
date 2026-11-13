"""Illinois DOT (Getting Around Illinois) construction, closure and live-traffic layers, for any district.

Source config:
  {"id": "idot", "type": "idot", "label": "IDOT", "district": 8,
   "liveReports": true,                  # also read IDOT's live traffic reports (default true)
   "roadsLayer": ".../D8_Roads_2017/FeatureServer/0",   # optional: lets news-only reports be traced
   "bridgeRoutes": {"POPLAR ST BRG": "I-55/64"}}       # optional: bridge names -> route labels
Zones are kept when they fall inside one of the district's counties (IDOT's county layer has a DISTRICT field).
"""
import datetime as dt
import hashlib
import math
import re

from ..common import (bbox_dist, bbox_of, clean, county_for, dir_label, esri_geom, iso, merc, parse_date,
                      pretty_loc, query, route_family, simplify, title_case, today, unmerc)

IDOT = "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/ArcGIS/rest/services"
IDOT_CONSTRUCTION = IDOT + "/Road_Construction_Public/FeatureServer/2"
IDOT_CLOSURES = IDOT + "/ClosureIncidentExtents/FeatureServer/0"
IDOT_TRAFFIC = IDOT + "/Illinois_Roadway_Incidents/FeatureServer/0"
IDOT_COUNTIES = IDOT + "/Counties/FeatureServer/0"
IDOT_URL = "https://www.gettingaroundillinois.com/Traveler%20Information/index.html"
TRAFFIC_KEEP = re.compile(r"lane|ramp|closed|closure|reduced|detour|shoulder", re.I)
BRIDGE_ROUTES = {}  # add local ones in the config: "bridgeRoutes"


def fetch(src, region):
    district = int(src.get("district", 8))
    counties = query(IDOT_COUNTIES, where=f"DISTRICT={district}", fields="COUNTY_NAM", offset_deg=0.002, precision=4)
    if not counties:
        raise RuntimeError(f"IDOT county layer lists no counties for district {district}")
    xs = [p[0] for f in counties for r in f["geometry"]["rings"] for p in r]
    ys = [p[1] for f in counties for r in f["geometry"]["rings"] for p in r]
    env = {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}
    out = {
        "counties": counties,
        "construction": query(IDOT_CONSTRUCTION, env=env,
                              fields="OBJECTID,StartDate,EndDate,Comments,Location,GlobalID,IDOT_GlobalID"),
        "closures": query(IDOT_CLOSURES, env=env),
        "traffic": [],
    }
    if src.get("liveReports", True):
        out["traffic"] = query(IDOT_TRAFFIC, env=env, where="TRAFFIC_ITEM_TYPE_DESC IN ('CONSTRUCTION','ROAD_CLOSURE')",
                               fields="START_TIME,ROAD_CLOSED,TRAFFIC_ITEM_DESCRIPTION,TRAFFIC_ITEM_DESCRIPTION_NO_EX,"
                                      "LOCATION_DEFINED_ORIGIN_RDWY")
    return out


def idot_route(code):
    m = re.match(r"^\s*([IUSF])0*(\d+)([A-Z]?)", clean(code).upper())
    return ({"I": "I-", "U": "US ", "S": "IL ", "F": "FAP "}[m.group(1)] + m.group(2) + m.group(3)) if m else ""


def idot_place(loc):
    """'I064: O'FALLON-MP 15 TO IL 158' -> ("O'Fallon", 'MP 15 to IL 158')."""
    loc = re.sub(r"^[IUSF]\d{3}[A-Z]?:\s*", "", clean(loc).upper())
    town, _, rest = loc.partition("-") if "-" in loc else ("", "", loc)
    return pretty_loc(town), pretty_loc(rest)


def idot_restriction(comment):
    tail = clean(comment).partition("-----")[2].strip(" -")
    m = re.match(r"(\d+)-(\d+)\s+MAX\s+(WIDTH|HEIGHT|LENGTH)", tail, re.I)
    if m:
        return f"Max {m.group(3).lower()} {int(m.group(1))} ft {int(m.group(2))} in for oversize loads"
    return "Closed to oversize-permit loads" if tail.upper().startswith("CLOSED") else title_case(tail)


def gid(a, *names):
    v = next((clean(a.get(n)) for n in names if clean(a.get(n))), "")
    return re.sub(r"[^A-Za-z0-9]", "", v).lower()[:16] if v else str(a.get("OBJECTID"))


def idot_base(label, url, **kw):
    p = {"source": label, "direction": "", "nearTown": "", "workType": "", "lanesClosed": "", "hours": "",
         "detour": "", "restriction": "", "impactLevel": "medium", "fullClosure": False, "endDate": None,
         "sourceUrl": url}
    p.update(kw)
    p["bbox"] = bbox_of(p["segments"])
    return p


def closure_desc(ctype, route, town, where, detour):
    return (f"{ctype or 'Closure'} on {route or 'a state route'}" + (f" near {town}" if town else "")
            + (f", {where}" if where else "") + "." + (" A detour is posted." if detour else ""))


def live_sentence(c):
    w = c["where"]
    if not w:
        return c["impact"] + "."
    if w.lower().startswith("closed"):
        return w + "."
    lead = "on the " if w.lower().startswith("ramp") else ""
    return f"{c['impact']} {lead}{w[:1].lower() + w[1:]}."


def build(layers, src, region):
    live_name = f"{src.get('label', 'IDOT')} live traffic reports"
    counties, projects = layers["counties"], []
    label, prefix = src.get("label", "IDOT"), src.get("idPrefix", "il")
    url = src.get("url", IDOT_URL)
    bridges = {**BRIDGE_ROUTES, **{k.upper(): v for k, v in (src.get("bridgeRoutes") or {}).items()}}
    t_iso = iso(today())

    def base(**kw):
        return idot_base(label, url, **kw)

    for f in layers["construction"]:
        a, segs = f["attributes"], esri_geom(f.get("geometry"))
        county = county_for(segs, counties)
        if not county:
            continue
        comment = clean(a.get("Comments"))
        route = idot_route(comment)
        town, where = idot_place(a.get("Location"))
        st = parse_date(a.get("StartDate"))
        future = bool(st and st > today())
        projects.append(base(
            id=prefix + "-c" + gid(a, "IDOT_GlobalID", "GlobalID"), route=route or "State route", location=where,
            counties=[county], nearTown=town, workType="Road construction",
            description=f"Road construction on {route or 'a state route'}" + (f" near {town}" if town else "")
                        + (f", {where}" if where else "") + ".",
            restriction=idot_restriction(comment), impact="Planned" if future else "Construction",
            impactLevel="future" if future else "medium", startDate=iso(st), endDate=iso(parse_date(a.get("EndDate"))),
            segments=segs, sources=[{"kind": "feed", "name": f"{label} construction layer"}], _code=comment[:5].strip(" :")))

    # sorted so the same closure always leads its card, whatever order IDOT returns the rows in
    for f in sorted(layers["closures"], key=lambda f: [clean(f["attributes"].get(k)) for k in
                                                       ("Route1", "Location", "StartDate")]):
        a, segs = f["attributes"], esri_geom(f.get("geometry"))
        county = county_for(segs, counties)
        if not county:
            continue
        ctype, code = title_case(a.get("ConstructionType")), clean(a.get("Route1"))
        town, where = idot_place(a.get("Location"))
        st, en = iso(parse_date(a.get("StartDate"))), iso(parse_date(a.get("EndDate")))
        full = bool(re.search(r"road closed", ctype, re.I))
        detour, hours, dirn = clean(a.get("DetourRoute")), clean(a.get("WorkTimes1")), clean(a.get("Route1Direction"))
        line = f"{ctype}: {where}" if where else ctype
        direction = "Both directions" if "/" in dirn else dir_label(dirn)
        bb = bbox_of(segs)
        host = next((p for p in projects if p.get("_code") == code and bb and bbox_dist(p["bbox"], bb) < 600), None)
        if host:
            host["segments"] += segs
            host["bbox"] = bbox_of(host["segments"])
            host["fullClosure"] |= full
            host["impactLevel"] = "closed" if host["fullClosure"] else host["impactLevel"]
            host["impact"] = "Road closed" if host["fullClosure"] else ctype or host["impact"]
            host["lanesClosed"] = "; ".join(x for x in (host["lanesClosed"], line) if x)
            host["detour"] = host["detour"] or detour
            host["hours"] = host["hours"] or hours
            if all(x["name"] != f"{label} closure layer" for x in host["sources"]):
                host["sources"].append({"kind": "feed", "name": f"{label} closure layer"})
            host["startDate"] = min((x for x in (host["startDate"], st) if x), default=None)
            host["endDate"] = max((x for x in (host["endDate"], en) if x), default=None)
            if host.get("_closure"):  # two closures sharing a card: say so instead of keeping the first one's label
                if direction and direction != host["direction"]:
                    host["direction"] = "Both directions" if host["direction"] else direction
                if where and where not in host["location"].split("; "):
                    host["location"] = "; ".join(x for x in (host["location"], where) if x)
                host["description"] = closure_desc(*host["_closure"], host["location"], host["detour"])
            continue
        route = idot_route(code)
        # the layer has no lasting id (GlobalID is empty, OBJECTID is renumbered), so key on what the closure is
        key = hashlib.sha1(f"{code}|{clean(a.get('Location')).upper()}|{st}".encode()).hexdigest()[:12]
        projects.append(base(
            id=prefix + "-x" + key, route=route or "State route", location=where, counties=[county],
            nearTown=town, direction=direction, workType=ctype,
            description=closure_desc(ctype, route, town, where, detour), _closure=(ctype, route, town),
            lanesClosed=line, hours=hours, detour=detour, impact="Road closed" if full else ctype or "Closure",
            impactLevel="closed" if full else ("future" if st and st > t_iso else "high"), fullClosure=full,
            startDate=st, endDate=en, segments=segs, sources=[{"kind": "feed", "name": f"{label} closure layer"}],
            _code=code))

    # live traffic reports: attach to the agency project they describe, else group into their own cards
    cands = []
    for f in layers.get("traffic", []):
        a = f["attributes"]
        desc, short = clean(a.get("TRAFFIC_ITEM_DESCRIPTION")), clean(a.get("TRAFFIC_ITEM_DESCRIPTION_NO_EX"))
        rdwy, closed = clean(a.get("LOCATION_DEFINED_ORIGIN_RDWY")), clean(a.get("ROAD_CLOSED")).lower() == "true"
        toks = list(dict.fromkeys({"I": "I-", "US": "US ", "IL": "IL "}[k] + n
                                  for k, n in re.findall(r"\b(I|US|IL)-(\d+\w?)", rdwy)))
        bridge = next((v for k, v in bridges.items() if k in rdwy.upper()), None)
        if bridge:
            toks = [bridge]
        elif not toks and re.search(r"\b(Brg|Bridge)\b", rdwy):
            toks = [re.sub(r"\bBrg\b", "Bridge", rdwy)]
        if not toks or not desc or desc.rstrip(".").lower() in {"closed", "roadwork"} or \
                not (closed or TRAFFIC_KEEP.search(short) or TRAFFIC_KEEP.search(desc)):
            continue
        segs = esri_geom(f.get("geometry"))
        county = county_for(segs, counties)
        if not county:
            continue
        bb, st = bbox_of(segs), parse_date(a.get("START_TIME"))
        ints = [t for t in toks if t.startswith("I-")]
        route = ints[0] + "/" + "/".join(t[2:] for t in ints[1:]) if len(ints) > 1 else toks[0]
        where = desc.partition(" - ")[0] if " - " in desc else ""
        where = re.sub(r"\b(I|US|IL)-(\d+)", lambda m: {"I": "I-", "US": "US ", "IL": "IL "}[m.group(1)] + m.group(2), where)
        where = re.sub(r"\bBrg\b", "Bridge", re.sub(r"/Exit (\d+)", r" (Exit \1)", where))
        impact = "Road closed" if closed else (re.sub(r"Roadwork\.\s*", "", short).strip().rstrip(".") or "Roadwork")
        fam = route_family(route)
        host = next((p for p in projects if bbox_dist(p["bbox"], bb) < 400 and route_family(p["route"]) & fam), None) \
            or next((p for p in projects if bbox_dist(p["bbox"], bb) < 400), None) \
            or next((p for p in projects if st and p.get("startDate") and bbox_dist(p["bbox"], bb) < 3000
                     and route_family(p["route"]) & fam
                     and abs((dt.date.fromisoformat(p["startDate"]) - st).days) <= 3), None)
        if host:
            if all(x["name"] != live_name for x in host["sources"]):
                host["sources"].append({"kind": "feed", "name": live_name, "live": True, "date": iso(st),
                                        "summary": (where + ": " if where else "") + (re.sub(r"Roadwork\.\s*", "", short) or desc)})
            host["liveReportSince"] = iso(st)
            if re.search(r"\b(Brg|Bridge)\b", rdwy):
                host["aka"] = re.sub(r"\bBrg\b", "Bridge", re.sub(r"\bSt\b", "Street", rdwy))
            if host.get("endDate") and host["endDate"] < t_iso:
                host["stillReported"], host["postedEnd"] = True, host["endDate"]
            continue
        dirm = re.search(r"\b(North|South|East|West)bound\b", rdwy, re.I)
        cands.append({"route": route, "rdwy": rdwy, "dir": dirm.group(1).title() + "bound" if dirm else "",
                      "where": where, "impact": impact[:1].upper() + impact[1:], "closed": closed, "st": st,
                      "segs": segs, "bb": bb, "county": county, "major": bool(ints) and "lane" in impact.lower()})

    groups = []
    for c in cands:
        g = next((g for g in groups if any(bbox_dist(c["bb"], x["bb"]) < 800 for x in g)), None)
        g.append(c) if g else groups.append([c])
    for g in groups:
        g.sort(key=lambda c: (not c["major"], not c["route"].startswith("I-"), c["route"]))
        lead, n = g[0], len(g)
        wheres = list(dict.fromkeys(f"{c['route']} {c['dir'].lower()}: {c['where'] or c['impact']}" for c in g))
        closed, sts = any(c["closed"] for c in g), [c["st"] for c in g if c["st"]]
        key = hashlib.sha1("|".join(sorted(c["rdwy"] + c["where"] for c in g)).encode()).hexdigest()[:14]
        projects.append(base(
            id=prefix + "-r" + key, route=lead["route"], direction=lead["dir"] if n == 1 else "",
            location=lead["where"] if n == 1 else f"{n} reports near {lead['where'] or lead['route']}",
            counties=sorted({c["county"] for c in g}), workType="Roadwork",
            description=(live_sentence(lead) if n == 1 else "Roadwork at " + "; ".join(wheres) + ".")
                        + f" From {label}'s live traffic reports; no end date is posted.",
            lanesClosed="; ".join(dict.fromkeys(c["impact"] for c in g)),
            impact="Road closed" if closed else lead["impact"], impactLevel="closed" if closed else "high",
            fullClosure=closed, startDate=iso(min(sts)) if sts else None, liveReport=True,
            segments=[s for c in g for s in c["segs"]], sources=[{"kind": "feed", "name": live_name, "live": True}]))
    for p in projects:
        p.pop("_code", None)
        p.pop("_closure", None)
    return projects


# ------------------------------------------------------------------ news-only reports
_ROADS = {}
_ROADS_LAYER = [None]


def il_code(route):
    m = re.match(r"^\s*(I|US|IL)[- ]?(\d+)", clean(route).upper())
    return {"I": "I", "US": "U", "IL": "S"}[m.group(1)] + m.group(2).zfill(3) if m else None


def road_feats(code):
    if code not in _ROADS:
        fields = ("MARKED_RT", "MARKED_RT2", "MARKED_RT3", "MARKED_RT4")
        _ROADS[code] = [f for f in query(_ROADS_LAYER[0], where=" OR ".join(f"{x}='{code}'" for x in fields),
                                         fields=",".join(fields)) if f.get("geometry")]
    return _ROADS[code]


def trace_il(route, frm, to):
    """Centerline of `route` between its junctions with `frm` and `to`, as lon/lat, or None."""
    main_c, a_c, b_c = il_code(route), il_code(frm), il_code(to)
    if not (main_c and a_c and b_c):
        return None
    paths = [[merc(*p) for p in path] for f in road_feats(main_c) for path in f["geometry"]["paths"]]
    segs = [(p[i], p[i + 1]) for p in paths for i in range(len(p) - 1)]
    if not segs:
        return None
    xs, ys = [x for s in segs for x, _ in s], [y for s in segs for _, y in s]
    box = (min(xs) - 200, min(ys) - 200, max(xs) + 200, max(ys) + 200)

    def seg_dist(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1e-9)))
        return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)

    def junctions(code):
        clusters = []
        for f in road_feats(code):
            if main_c in f["attributes"].values():
                continue
            for q in (merc(*q) for path in f["geometry"]["paths"] for q in path):
                if not (box[0] <= q[0] <= box[2] and box[1] <= q[1] <= box[3]):
                    continue
                if min(seg_dist(q, a, b) for a, b in segs) < 150:
                    c = next((c for c in clusters if math.hypot(q[0] - c[0][0], q[1] - c[0][1]) < 1500), None)
                    c.append(q) if c else clusters.append([q])
        return [(sum(x for x, _ in c) / len(c), sum(y for _, y in c) / len(c)) for c in clusters]

    ja, jb = junctions(a_c), junctions(b_c)
    if not (ja and jb):
        return None
    (ax, ay), (bx, by) = min(((a, b) for a in ja for b in jb), key=lambda ab: math.dist(*ab))
    L = math.hypot(bx - ax, by - ay) or 1
    ux, uy = (bx - ax) / L, (by - ay) / L
    pts = sorted(((x - ax) * ux + (y - ay) * uy, x, y) for p in paths for x, y in p
                 if -50 <= (x - ax) * ux + (y - ay) * uy <= L + 50 and abs((y - ay) * ux - (x - ax) * uy) < max(400, 0.15 * L))
    return simplify([unmerc(x, y) for _, x, y in pts], 15.0) if len(pts) > 1 else None


def trace(src, route, frm, to):
    """Line for a news-only report: `route` between its junctions with `frm` and `to` (needs roadsLayer)."""
    if not src.get("roadsLayer"):
        return None
    _ROADS_LAYER[0] = src["roadsLayer"]
    return trace_il(route, frm, to)
