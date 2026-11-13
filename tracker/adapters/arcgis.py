"""Any ArcGIS feature or map service layer of road work, with a field mapping in the config.

Many cities, counties and DOTs publish work zones or street closures as an ArcGIS layer. Point this adapter at
the layer's REST URL and say which attribute holds what.

Source config:
  {"id": "city", "type": "arcgis", "label": "City of Example",
   "layer": "https://services.arcgis.com/.../FeatureServer/0",
   "where": "STATUS <> 'Complete'",          # optional server-side filter
   "fields": {                               # attribute names in the layer; leave out what it doesn't have
     "id": "GlobalID", "route": "STREET", "location": "LIMITS", "description": "DESCRIPTION",
     "start": "START_DATE", "end": "END_DATE", "direction": "DIRECTION", "workType": "WORK_TYPE",
     "lanes": "LANES_CLOSED", "hours": "WORK_HOURS", "detour": "DETOUR", "contractor": "CONTRACTOR",
     "county": "COUNTY", "town": "CITY"},
   "closed": {"field": "CLOSURE_TYPE", "pattern": "full|road closed"},   # optional: marks full closures
   "groupBy": "PROJECT_ID",                  # optional: merge features that share this attribute into one card
   "infoUrl": "https://example.gov/roadwork"}
Only features that intersect the region's bbox are requested.
"""
import hashlib
import re

from ..common import clean, dir_label, esri_geom, finish, iso, parse_date, pretty_loc, query, title_case, today


def fetch(src, region):
    if not src.get("layer"):
        raise RuntimeError("arcgis source needs a layer url")
    bb = region.get("bbox")
    env = {"xmin": bb[0], "ymin": bb[1], "xmax": bb[2], "ymax": bb[3]} if bb else None
    return query(src["layer"].rstrip("/"), where=src.get("where", "1=1"), env=env)


def build(feats, src, region):
    label, prefix = src.get("label", "Agency"), src.get("idPrefix", src.get("id", "gis"))
    F = src.get("fields") or {}
    closed_rule = src.get("closed") or {}
    closed_re = re.compile(closed_rule["pattern"], re.I) if closed_rule.get("pattern") else None
    t = today()

    def val(a, k):
        return clean(a.get(F[k])) if F.get(k) else ""

    groups = {}
    for f in feats or []:
        a = f.get("attributes") or {}
        segs = esri_geom(f.get("geometry"))
        if not segs:
            continue
        gk = clean(a.get(src["groupBy"])) if src.get("groupBy") else ""
        k = gk or val(a, "id") or str(a.get("OBJECTID") or a.get("FID") or len(groups))
        g = groups.setdefault(k, {"a": a, "segs": [], "attrs": []})
        g["segs"] += segs
        g["attrs"].append(a)

    out = []
    for k, g in groups.items():
        a = g["a"]
        route = title_case(val(a, "route")) or "Road"
        location = pretty_loc(val(a, "location")) if val(a, "location").isupper() else val(a, "location")
        starts = [d for d in (parse_date(x.get(F["start"])) for x in g["attrs"]) if d] if F.get("start") else []
        ends = [d for d in (parse_date(x.get(F["end"])) for x in g["attrs"]) if d] if F.get("end") else []
        full = bool(closed_re and any(closed_re.search(clean(x.get(closed_rule.get("field")))) for x in g["attrs"]))
        st = min(starts) if starts else None
        future = bool(st and st > t)
        work = title_case(val(a, "workType"))
        dirs = sorted({dir_label(x.get(F["direction"])) for x in g["attrs"]} - {""}) if F.get("direction") else []
        direction = "Both directions" if len(dirs) > 1 else (dirs[0] if dirs else "")
        desc = val(a, "description") or (f"{work or 'Road work'} on {route}" + (f", {location}" if location else "") + ".")
        county = title_case(val(a, "county"))
        out.append(finish({
            "id": f"{prefix}-{hashlib.sha1(k.encode()).hexdigest()[:14]}", "source": label, "route": route,
            "direction": direction, "location": location, "counties": [county] if county else [],
            "nearTown": title_case(val(a, "town")), "description": desc, "workType": work or "Road work",
            "impact": "Road closed" if full else ("Planned" if future else (work or "Road work")),
            "impactLevel": "closed" if full else ("future" if future else "medium"), "fullClosure": full,
            "lanesClosed": val(a, "lanes"), "hours": val(a, "hours"), "detour": val(a, "detour"),
            "contractor": title_case(val(a, "contractor")),
            "startDate": iso(st), "endDate": iso(max(ends)) if ends else None,
            "segments": g["segs"], "sources": [{"kind": "feed", "name": f"{label} map layer"}],
            "sourceUrl": src.get("infoUrl", ""),
        }))
    return out
