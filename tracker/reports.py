"""News/social-only reports: closures an agency announced that no data feed lists yet.

Region config: "reports": "config/reports/<region>.json", holding {"reports": [...]}. Each report:
  {"source": "IDOT",                       # agency label shown on the card
   "route": "I-55/70", "from": "US 40", "to": "IL 157", "direction": "Westbound",
   "start": "2026-11-02", "end": "2026-11-14", "impact": "One lane", "counties": ["Madison"],
   "workType": "Pavement repairs", "description": "In your own words.",
   "coords": [[-90.01, 38.68], [-89.92, 38.72]],    # a line to draw, or
   "trace": "idot",                                 # the id of a source whose adapter can trace route from->to
   "sources": [{"kind": "social", "name": "@IDOTDistrict8 on X", "url": "...", "date": "2026-10-30",
                "summary": "..."}]}
Reports past their end date are dropped. Reports already stored carry forward while still running.
"""
import hashlib
import sys

from .adapters import get as get_adapter
from .common import bbox_dist, bbox_of, clean, route_family, rnd, today


def build(specs, projects, cfg):
    srcs = {s["id"]: s for s in cfg.get("sources", [])}
    out, t = [], today().isoformat()
    for r in specs:
        if (r.get("end") or "9999") < t:
            continue
        coords = [rnd(c) for c in r.get("coords") or []]
        if not coords and r.get("trace") in srcs:
            src = srcs[r["trace"]]
            fn = getattr(get_adapter(src["type"]), "trace", None)
            try:
                coords = fn(src, r["route"], r["from"], r["to"]) if fn else None
            except Exception as e:  # noqa: BLE001
                print("trace failed:", r.get("route"), e, file=sys.stderr)
        if not coords:
            print("report skipped (no geometry):", r.get("route"), r.get("from"), r.get("to"), file=sys.stderr)
            continue
        segs = [{"type": "line", "coords": coords}] if len(coords) > 1 else [{"type": "point", "coord": coords[0]}]
        bb = bbox_of(segs)
        key = hashlib.sha1(f"{r['route']}|{r.get('from')}|{r.get('to')}|{r.get('direction', '')}".lower().encode()).hexdigest()[:12]
        loc = f"{r['from']} to {r['to']}" if r.get("from") and r.get("to") else clean(r.get("location"))
        out.append({
            "id": "rp-" + key, "source": r.get("source") or (srcs.get(r.get("trace")) or {}).get("label", "Agency"),
            "route": r["route"], "direction": r.get("direction", ""), "location": loc,
            "counties": r.get("counties", []), "nearTown": r.get("nearTown", ""),
            "description": r.get("description", ""), "workType": r.get("workType", "Roadwork"),
            "lanesClosed": r.get("impact", ""), "impact": r.get("impact", "Lane closure"),
            "impactLevel": "closed" if r.get("fullClosure") else "high", "fullClosure": bool(r.get("fullClosure")),
            "startDate": r.get("start"), "endDate": r.get("end"), "segments": segs, "bbox": bb,
            "reportOnly": True, "reportSpec": r, "sources": r.get("sources", []),
            "sourceUrl": (r.get("sources") or [{}])[0].get("url", ""),
            "relatedIds": [p["id"] for p in projects if p.get("bbox") and bbox_dist(p["bbox"], bb) < 300
                           and route_family(p["route"]) & route_family(r["route"])],
        })
    return out
