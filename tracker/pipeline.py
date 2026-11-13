"""One refresh: fetch every configured source, merge with the last run, write the site's data files.

Outputs (in --out, default site/data):
  projects.json   {"generated": ts, "projects": [...]}
  status.json     {"lastRefresh", "sources": {id: {ok, label, projects | error}}, "counts", "feedHold", "news"}
  region.json     the public part of the region config (title, agencies, map settings) for the page
Raw feed responses are cached in --cache (default .cache/<region>) so --offline can rebuild without the network.
"""
import datetime as dt
import json
import os
import sys

from . import news as news_mod
from . import reports as reports_mod
from .adapters import get as get_adapter
from .common import bbox_dist, bbox_of, jdump, jload, now_iso, route_family, set_timezone, status_for, tz

HOLD_MIN, HOLD_RATIO, HOLD_RUNS = 8, 0.3, 2  # feed sanity check: a sudden collapse is an outage for 2 runs
ENDED_KEEP_DAYS = 14
PALETTE = ["#1e56a0", "#0d7566", "#2f6f2f", "#8a2f5c", "#1f6f8b", "#5b6b1e", "#6b4f2a", "#2b3a8f"]
PUBLIC_KEYS = ("id", "title", "subtitle", "bbox", "home", "map", "footer", "newsTitle", "about", "timezone")


def load_config(path):
    cfg = jload(path)
    if not cfg:
        raise SystemExit(f"can't read region config {path}")
    base = os.path.dirname(os.path.abspath(path))
    root = os.path.dirname(os.path.dirname(base)) if os.path.basename(base) == "regions" else base
    for key in ("reports",):
        if isinstance(cfg.get(key), str) and not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(root, cfg[key])
    if (cfg.get("news") or {}).get("file") and not os.path.isabs(cfg["news"]["file"]):
        cfg["news"]["file"] = os.path.join(root, cfg["news"]["file"])
    problems = validate(cfg)
    if problems:
        raise SystemExit("config problems:\n  " + "\n  ".join(problems))
    return cfg


def validate(cfg):
    out = []
    for k in ("id", "title", "bbox", "sources"):
        if not cfg.get(k):
            out.append(f"missing '{k}'")
    bb = cfg.get("bbox") or []
    if bb and (len(bb) != 4 or not (bb[0] < bb[2] and bb[1] < bb[3])):
        out.append("bbox must be [minLon, minLat, maxLon, maxLat]")
    seen = set()
    for i, s in enumerate(cfg.get("sources") or []):
        if not s.get("id") or not s.get("type"):
            out.append(f"sources[{i}] needs 'id' and 'type'")
            continue
        if s["id"] in seen:
            out.append(f"duplicate source id {s['id']!r}")
        seen.add(s["id"])
        try:
            get_adapter(s["type"])
        except KeyError as e:
            out.append(str(e))
    return out


def public_region(cfg):
    pub = {k: cfg[k] for k in PUBLIC_KEYS if k in cfg}
    pub["agencies"] = []
    for i, s in enumerate(cfg["sources"]):
        lab = s.get("label", s["id"])
        if all(a["label"] != lab for a in pub["agencies"]):
            pub["agencies"].append({"label": lab, "color": s.get("color") or PALETTE[i % len(PALETTE)],
                                    "darkColor": s.get("darkColor"), "mapName": s.get("mapName") or f"{lab} map",
                                    "state": s.get("state", "")})
    pub.setdefault("map", {})
    pub["map"].setdefault("tiles", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    pub["map"].setdefault("attribution", "© OpenStreetMap contributors")
    pub["map"].setdefault("maxZoom", 18)
    return pub


def run(cfg, out_dir, cache_dir, offline=False, only=None):
    set_timezone(cfg.get("timezone", "America/Chicago"))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    now = now_iso()
    prev = jload(os.path.join(out_dir, "projects.json"), {}) or {}
    meta = jload(os.path.join(out_dir, "status.json"), {}) or {}
    stored = {p["id"]: p for p in prev.get("projects", [])}

    sources, projects, hold = {}, [], dict(meta.get("feedHold") or {})
    for src in cfg["sources"]:
        sid, label = src["id"], src.get("label", src["id"])
        if only and sid not in only:
            continue
        mod = get_adapter(src["type"])
        raw_path = os.path.join(cache_dir, sid + ".json")
        try:
            raw = jload(raw_path) if offline else mod.fetch(src, cfg)
            if raw is None:
                raise RuntimeError("no cached feed data (run once without --offline)")
            if not offline:
                jdump(raw, raw_path)
            ps = []
            for p in mod.build(raw, src, cfg):
                if p.get("segments"):
                    p["_src"] = sid
                    p.setdefault("bbox", bbox_of(p["segments"]))
                    ps.append(p)
            had = sum(1 for p in stored.values() if p.get("sourceId") == sid and not p.get("reportOnly")
                      and p.get("status") != "ended")
            if had >= HOLD_MIN and len(ps) < HOLD_RATIO * had and hold.get(sid, 0) < HOLD_RUNS:
                hold[sid] = hold.get(sid, 0) + 1
                raise RuntimeError(f"feed listed {len(ps)} projects against {had} stored; keeping the stored set "
                                   f"(hold {hold[sid]} of {HOLD_RUNS})")
            hold.pop(sid, None)
            projects += ps
            sources[sid] = {"ok": True, "label": label, "projects": len(ps)}
        except Exception as e:  # noqa: BLE001
            print(f"{sid} failed:", e, file=sys.stderr)
            sources[sid] = {"ok": False, "label": label, "error": str(e)[:200]}
    for p in projects:
        p["sourceId"] = p.pop("_src")

    # live-report cards are regrouped and re-stamped by the agency each day: a new one takes over the id (history,
    # news links) of the stored card at the same place on the same route, and keeps the earliest start seen
    new_ids = {p["id"] for p in projects}
    free = [o for o in stored.values() if o.get("liveReport") and o["id"] not in new_ids and o.get("bbox")]
    for p in projects:
        if p.get("liveReport") and p["id"] not in stored:
            fam = route_family(p["route"])
            near = sorted((bbox_dist(o["bbox"], p["bbox"]), o["id"]) for o in free if route_family(o.get("route")) & fam)
            if near and near[0][0] < 800:
                free.remove(stored[near[0][1]])
                p["id"] = near[0][1]
        old = stored.get(p["id"]) or {}
        if p.get("liveReport") and old.get("startDate") and (p.get("startDate") or "9999") > old["startDate"]:
            p["startDate"] = old["startDate"]
        if p.get("liveReportSince") and (old.get("liveReportSince") or "9999") < p["liveReportSince"]:
            p["liveReportSince"] = old["liveReportSince"]
            for x in p["sources"]:
                if x.get("live") and x.get("date"):
                    x["date"] = old["liveReportSince"]

    # news/social-only reports: stored ones still running plus the config file (file wins on the same key)
    specs = {}
    for p in stored.values():
        s = p.get("reportSpec")
        if p.get("reportOnly") and s:
            specs[(s["route"], s.get("from"), s.get("to"), s.get("direction", ""))] = s
    for s in (jload(cfg["reports"], {}) or {}).get("reports", []) if cfg.get("reports") else []:
        specs[(s["route"], s.get("from"), s.get("to"), s.get("direction", ""))] = s
    projects += reports_mod.build(list(specs.values()), projects, cfg)

    failed = {k for k, v in sources.items() if not v["ok"]}
    seen, out = set(), []
    for p in projects:
        old = stored.get(p["id"], {})
        seen.add(p["id"])
        p.update(firstSeen=old.get("firstSeen") or now, lastSeen=now, updates=old.get("updates", []))
        p["status"] = status_for(p)
        out.append(p)
    for pid, old in stored.items():
        if pid in seen:
            continue
        if (old.get("sourceId") in failed or (only and old.get("sourceId") not in only)) and not old.get("reportOnly"):
            out.append(old)
            continue
        try:
            age = (dt.datetime.now(tz()) - dt.datetime.fromisoformat(old.get("lastSeen"))).days
        except (TypeError, ValueError):
            age = ENDED_KEEP_DAYS
        if not old.get("liveReport") and not old.get("reportOnly") and age < cfg.get("endedKeepDays", ENDED_KEEP_DAYS):
            out.append(dict(old, status="ended", endedNote="No longer listed by the agency feed"))

    items = news_mod.collect(cfg, out, (meta.get("news") or {}).get("items"))
    news_mod.attach(out, items)

    counts = {}
    for p in out:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    out.sort(key=lambda p: p["id"])
    # the first run sees everything for the first time; the page doesn't call those "new"
    status = {"lastRefresh": now, "firstRefresh": meta.get("firstRefresh") or now, "sources": sources, "counts": counts, "feedHold": hold,
              "news": {"items": items, "checked": now}}
    jdump({"generated": now, "region": cfg["id"], "projects": out}, os.path.join(out_dir, "projects.json"))
    jdump(status, os.path.join(out_dir, "status.json"), pretty=True)
    jdump(public_region(cfg), os.path.join(out_dir, "region.json"), pretty=True)
    print(json.dumps({"sources": sources, "projects": len(out), "counts": counts, "news": len(items)}))
    return status
