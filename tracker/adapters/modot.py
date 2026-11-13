"""MoDOT Traveler Information work zones (any Missouri counties).

Source config:
  {"id": "modot", "type": "modot", "label": "MoDOT", "counties": ["ST. LOUIS", "ST. CHARLES", ...]}
Counties are MoDOT's COUNTY_NAME values (upper case; "ST. LOUIS CITY" for the city). Leave the list
empty to take the whole state.
"""
import re

from ..common import clean, dir_label, esri_geom, iso, norm_job, parse_date, query, ref_point, title_case

MODOT = "https://mapping.modot.org/arcgis/rest/services/TravelerInformation/TravelerInformationData/MapServer"
MODOT_LAYERS = {29: ("line", "closed"), 33: ("line", "high"), 34: ("line", "medium"), 35: ("line", "future"),
                22: ("point", "closed"), 26: ("point", "high"), 27: ("point", "medium"), 28: ("point", "future")}
MODOT_URL = "https://traveler.modot.org/map/"
MAX_ZONES = 14


def fetch(src, region):
    counties = [c.upper() for c in src.get("counties", [])]
    where = "1=1"
    if counties:
        names = ",".join("'" + c.replace("'", "''") + "'" for c in counties + [c.replace(".", "") for c in counties])
        where = f"COUNTY_NAME IN ({names})"
    return {str(lid): query(f"{src.get('service', MODOT)}/{lid}", where=where) for lid in MODOT_LAYERS}


ROUTE_PREFIX = {"IS": "I-", "US": "US ", "MO": "MO ", "RT": "Route ", "LP": "Loop ", "BU": "Bus. ", "SP": "Spur ",
                "OR": "Outer Road "}
IMPACT_ORDER = {"closed": 0, "high": 1, "medium": 2, "future": 3}
IMPACT_LABEL = {"closed": "Road closed", "high": "High impact", "medium": "Moderate impact", "future": "Planned"}


def modot_route(a):
    des, name = clean(a.get("DESIGNATION")).upper(), clean(a.get("TRAVELWAY_NAME"))
    if des in ROUTE_PREFIX and name:
        return ROUTE_PREFIX[des] + re.sub(r"^(IS|US|MO|RT|LP|BU|SP|OR)\s*", "", name.upper())
    return title_case(name) or "State route"


def build(layers, src, region):
    keep = {c.upper().replace(".", "") for c in src.get("counties", [])}
    label = src.get("label", "MoDOT")
    recs = {}
    for lid, feats in layers.items():
        kind, cat = MODOT_LAYERS[int(lid)]
        for f in feats:
            a = f["attributes"]
            if keep and clean(a.get("COUNTY_NAME")).upper().replace(".", "") not in keep:
                continue
            rid = str(a.get("ID") or a.get("TI_WZ_ID") or a.get("OBJECTID"))
            r = recs.setdefault(rid, {"a": a, "cat": cat, "segs": []})
            segs = esri_geom(f.get("geometry"))
            if kind == "line":  # lines win over the marker points of the same zone
                r["segs"] = [s for s in r["segs"] if s["type"] == "line"] + segs
            elif not any(s["type"] == "line" for s in r["segs"]):
                r["segs"] += segs
            if IMPACT_ORDER[cat] < IMPACT_ORDER[r["cat"]]:
                r["cat"] = cat

    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = x = parent[parent[x]]
        return x

    first_by_job = {}
    for rid, r in recs.items():
        find(rid)
        job = norm_job(r["a"].get("JOB_NUMBER"))
        if job:
            parent[find(rid)] = find(first_by_job.setdefault(job, rid))
        opp = str(r["a"].get("OPPOSING_TI_WZ_ID") or "")
        if opp in recs:
            parent[find(rid)] = find(opp)
    groups = {}
    for rid in recs:
        groups.setdefault(find(rid), []).append(rid)

    out = []
    for members in groups.values():
        members.sort()
        rs = [recs[m] for m in members]
        a0 = rs[0]["a"]
        job = next((j for j in (norm_job(r["a"].get("JOB_NUMBER")) for r in rs) if j), "")
        starts = [d for d in (parse_date(r["a"].get("START_DATE")) for r in rs) if d]
        ends = [d for d in (parse_date(r["a"].get("END_DATE")) for r in rs) if d]
        cat = min((r["cat"] for r in rs), key=IMPACT_ORDER.get)
        lvls = [clean(r["a"].get("LEVEL_OF_IMPACT")) for r in rs]
        lvl = "All lanes closed" if any(x.upper() == "ALL LANES CLOSED" for x in lvls) else lvls[0]
        if lvl == "All lanes closed":
            cat = "closed"
        dirs = sorted({dir_label(r["a"].get("DIRECTION")) for r in rs} - {""})
        direction = "Both directions" if len(dirs) > 1 else (dirs[0] if dirs else "")
        frm, to = ref_point(a0.get("BEG_REF_PT")), ref_point(a0.get("END_REF_PT"))
        location = f"{frm} to {to}" if frm and to and frm != to else (f"Near {frm or to}" if frm or to else "")
        work = title_case(a0.get("WORK_TYPE"))
        # one contract often covers several roads; keep each zone so the card and the search can name them all
        zmap = {}
        for r in rs:
            a = r["a"]
            zf, zt = ref_point(a.get("BEG_REF_PT")), ref_point(a.get("END_REF_PT"))
            z = {"route": modot_route(a), "direction": dir_label(a.get("DIRECTION")),
                 "location": f"{zf} to {zt}" if zf and zt and zf != zt else (f"Near {zf or zt}" if zf or zt else ""),
                 "lanesClosed": clean(a.get("NUMBER_LANES_CLOSED"))}
            k = (z["route"], z["direction"], z["location"])
            if k not in zmap or z["lanesClosed"] > zmap[k]["lanesClosed"]:
                zmap[k] = z
        zones = sorted(zmap.values(), key=lambda z: (z["route"] != modot_route(a0), z["route"], z["location"]))
        routes = list(dict.fromkeys(z["route"] for z in zones))
        multi = len({(z["route"], z["location"]) for z in zones}) > 1
        st, et = clean(a0.get("START_TIME")), clean(a0.get("END_TIME"))
        route = modot_route(a0)
        contractor = title_case(a0.get("COMPANY_WORKING"))
        out.append({
            "id": src.get("idPrefix", "mo") + "-" + ("j" + job.lower() if job else members[0]), "source": label,
            "route": route,
            "direction": direction, "location": location,
            "counties": sorted({title_case(r["a"].get("COUNTY_NAME")) for r in rs}),
            "description": (f"{work or 'Road work'} on {', '.join(routes[:-1])} and {routes[-1]}: "
                            f"{len(zones)} work zones under one contract.") if len(routes) > 1 else
                           f"{work or 'Road work'} on {route}"
                           + (f" {direction.lower()}" if direction and direction != "Both directions" else "")
                           + (f", {location}" if location else "") + ".",
            "zones": zones[:MAX_ZONES] if multi else [],
            "workType": work, "lanesClosed": clean(a0.get("NUMBER_LANES_CLOSED")),
            "hours": f"{st} to {et}" + (" (incl. weekends)" if clean(a0.get("WEEKEND")).upper() in {"Y", "YES"} else "")
                     if st and et else "",
            "contractor": "" if contractor.upper() in {"MODOT", "MO DOT"} else contractor, "jobNumber": job,
            "impact": "Road closed" if cat == "closed" else (lvl.capitalize() if lvl else IMPACT_LABEL[cat]),
            "impactLevel": cat, "fullClosure": cat == "closed",
            "startDate": iso(min(starts)) if starts else None, "endDate": iso(max(ends)) if ends else None,
            "speedLimit": clean(a0.get("SPEED_LIMIT")),
            "segments": [s for r in rs for s in r["segs"]], "sourceUrl": src.get("url", MODOT_URL),
            "sources": [{"kind": "feed", "name": f"{label} traveler map"}],
        })
    return out
