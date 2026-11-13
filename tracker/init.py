"""Start a region config from the USDOT WZDx Feed Registry: pick states, get every published work-zone feed.

  python -m tracker init --states KS,MO --id kc --title "Kansas City Road Work" [--bbox W,S,E,N]
  python -m tracker init --list [--states OH]      # show the registry's feeds without writing anything

The registry (data.transportation.gov dataset 69qe-yiui) lists the WZDx feeds state and local agencies publish.
If it can't be reached, a snapshot shipped in tracker/data/ is used. Feeds that need an API key get a
${<SOURCE>_API_KEY} placeholder in their URL; set that environment variable (or GitHub secret) to the key.
"""
import json
import os
import re
import sys

from .common import http_get

REGISTRY_URL = "https://data.transportation.gov/resource/69qe-yiui.json"
HERE = os.path.dirname(__file__)
PALETTE = ["#1e56a0", "#0d7566", "#2f6f2f", "#8a2f5c", "#1f6f8b", "#5b6b1e", "#6b4f2a", "#2b3a8f"]
KEY_SLOT = re.compile(r"(INSERT[-_ ]API[-_ ]KEY[-_ ]HERE|\[[^\]]*(?:KEY|Key|key)[^\]]*\]|<key>|%3ckey%3e|YOUR-[A-Z-]*KEY)", re.I)


def states():
    with open(os.path.join(HERE, "data", "states.json")) as f:
        return json.load(f)


def registry():
    """(records, where they came from)."""
    try:
        recs = http_get(REGISTRY_URL, {"$limit": 500})
        if isinstance(recs, list) and recs:
            return recs, "live registry"
    except Exception as e:  # noqa: BLE001
        print(f"(live registry unreachable: {str(e)[:80]}; using the snapshot shipped with the tracker)", file=sys.stderr)
    with open(os.path.join(HERE, "data", "wzdx_registry.json")) as f:
        return json.load(f)["records"], "registry snapshot of 2026-11-13"


def _truthy(v):
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def _url(r):
    u = r.get("url")
    return (u.get("url") if isinstance(u, dict) else u) or ""


def feeds_for(codes, recs, include_cwz=False):
    """Active WZDx feeds whose registry 'state' names one of the given states."""
    names = {states()[c]["name"].lower(): c for c in codes}
    out = []
    for r in recs:
        listed = [s.strip().lower() for s in str(r.get("state", "")).split(",")]
        hit = [names[s] for s in listed if s in names]
        if not hit or not _truthy(r.get("active", True)) or not _url(r):
            continue
        cwz = "cwz" in str(r.get("version", "")).lower()
        if cwz and not include_cwz:
            continue
        out.append({**r, "_states": hit, "_cwz": cwz})
    return out


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24] or "feed"


def source_for(r, i):
    org = r.get("issuingorganization") or "Agency"
    sid = slug(re.sub(r"\b(department of transportation|dept\.? of transportation)\b", "dot", org, flags=re.I))
    url, needs_key = _url(r), _truthy(r.get("needapikey"))
    env = re.sub(r"[^A-Z0-9]", "_", sid.upper()) + "_API_KEY"
    if needs_key:
        url = KEY_SLOT.sub("${" + env + "}", url) if KEY_SLOT.search(url) else (url + "${" + env + "}" if url.endswith("=") else url)
    label = re.sub(r"\s*\(CWZ\)\s*", "", org)
    src = {"id": sid, "type": "wzdx", "label": label, "color": PALETTE[i % len(PALETTE)], "url": url,
           "eventTypes": ["work-zone"]}
    notes = []
    if needs_key:
        key_url = r.get("apikeyurl")
        key_url = key_url.get("url") if isinstance(key_url, dict) else key_url
        notes.append(f"needs an API key: set {env}" + (f" (sign up: {key_url})" if key_url else ""))
    if r.get("note"):
        notes.append(r["note"])
    if r["_cwz"]:
        notes.append("Connected Work Zone (CWZ) feed; read with the WZDx adapter, check the results")
    if notes:
        src["_note"] = "; ".join(notes)
    return src


def union_bbox(codes, pad=0.25):
    bbs = [states()[c]["bbox"] for c in codes]
    return [round(min(b[0] for b in bbs) - pad, 2), round(min(b[1] for b in bbs) - pad, 2),
            round(max(b[2] for b in bbs) + pad, 2), round(max(b[3] for b in bbs) + pad, 2)]


def parse_states(text):
    table, out = states(), []
    by_name = {v["name"].lower(): k for k, v in table.items()}
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        code = part.upper() if part.upper() in table else by_name.get(part.lower())
        if not code:
            raise SystemExit(f"unknown state {part!r}; use two-letter codes like MO or names like Missouri")
        out.append(code)
    return out


def cmd_init(a):
    codes = parse_states(a.states)
    recs, origin = registry()
    if a.list:
        rows = feeds_for(codes, recs, include_cwz=True) if codes else [
            {**r, "_states": [], "_cwz": "cwz" in str(r.get("version", "")).lower()} for r in recs if _url(r)]
        print(f"WZDx feeds ({origin}):")
        for r in sorted(rows, key=lambda r: (str(r.get("state")).lower(), str(r.get("issuingorganization")))):
            key = " [API key]" if _truthy(r.get("needapikey")) else ""
            print(f"  {str(r.get('state')):<22} {str(r.get('issuingorganization'))[:44]:<44} v{r.get('version', '?')}{key}")
        return 0
    if not codes:
        raise SystemExit("give --states, e.g. --states MO,KS")
    if not a.id:
        raise SystemExit("give --id, a short name for the region (letters, digits, dashes)")
    rid = slug(a.id)
    path = os.path.join(a.root, "config", "regions", f"{rid}.json")
    if os.path.exists(path) and not a.force:
        raise SystemExit(f"{path} exists; pass --force to overwrite")
    bbox = [float(x) for x in a.bbox.split(",")] if a.bbox else union_bbox(codes)
    if len(bbox) != 4:
        raise SystemExit("--bbox is west,south,east,north in degrees")
    feeds = feeds_for(codes, recs, include_cwz=a.include_cwz)
    sources = [source_for(r, i) for i, r in enumerate(feeds)]
    seen = {}
    for s in sources:  # unique ids
        n = seen.get(s["id"], 0)
        seen[s["id"]] = n + 1
        if n:
            s["id"] = f"{s['id']}-{n + 1}"
    names = [states()[c]["name"] for c in codes]
    cfg = {
        "id": rid,
        "title": a.title or f"{' & '.join(names)} Road Work",
        "subtitle": " · ".join(dict.fromkeys(s["label"] for s in sources)) or "Add sources to config",
        "timezone": a.timezone,
        "bbox": bbox,
        "home": bbox,
        "map": {"tiles": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": "© OpenStreetMap contributors", "maxZoom": 18},
        "newsTitle": "Latest agency news",
        "footer": "Work zone data from the agencies listed above, refreshed on a schedule. Check the agency sites before you travel.",
        "sources": sources,
        "news": {"feeds": [], "file": f"config/news/{rid}.json"},
        "reports": f"config/reports/{rid}.json",
    }
    for sub, body in (("regions", cfg), ("news", {"items": []}), ("reports", {"reports": []})):
        p = os.path.join(a.root, "config", sub, f"{rid}.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if sub == "regions" or not os.path.exists(p):
            with open(p, "w") as f:
                json.dump(body, f, indent=2, ensure_ascii=False)
                f.write("\n")
    print(f"Wrote {path} ({origin}): {len(sources)} WZDx feed(s) for {', '.join(names)}")
    for s in sources:
        print(f"  {s['id']:<26} {s['label']}" + (f"  -> {s['_note']}" if s.get("_note") else ""))
    if not sources:
        print("  No WZDx feed is registered for these states. Add an 'arcgis', 'modot' or 'idot' source by hand;"
              " see docs/CONFIG.md.")
    print("Next: narrow 'bbox' and 'home' to your area, add newsroom feeds under 'news', then\n"
          f"  python -m tracker run --config config/regions/{rid}.json")
    return 0
