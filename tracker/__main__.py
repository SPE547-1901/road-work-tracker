"""Command line: python -m tracker <command>

  init      start a region config from the WZDx Feed Registry for one or more states
            --states MO,KS --id kc [--title ...] [--bbox W,S,E,N] [--timezone America/Chicago]
            --list (show registered feeds, optionally for --states, without writing)
  The rest take --config config/regions/<region>.json:

  run       fetch every source and write site/data/{projects,status,region}.json
            --out DIR (default site/data)  --cache DIR (default .cache/<region id>)
            --offline (rebuild from the cached feed responses)  --only ID[,ID] (refresh some sources)
  validate  check the config and list its sources
  summary   one line per current project in --out: id|route|direction|location|counties|start..end|flags
            (flags: R news-only report, L live report, X full closure, P past its posted end)
"""
import argparse
import os
import sys

from . import init as init_mod
from . import pipeline
from .common import jload


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tracker", description="Road work tracker data pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "validate", "summary"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True, help="region config, e.g. config/regions/<region>.json")
        p.add_argument("--out", default="site/data")
    r = sub.choices["run"]
    r.add_argument("--cache")
    r.add_argument("--offline", action="store_true")
    r.add_argument("--only", help="comma-separated source ids to refresh; others keep their stored projects")
    i = sub.add_parser("init", help="start a region config from the WZDx Feed Registry")
    i.add_argument("--states", help="two-letter codes or names, comma-separated: MO,KS")
    i.add_argument("--id", help="short region name, used for the config file name")
    i.add_argument("--title")
    i.add_argument("--bbox", help="west,south,east,north; default: the states' combined extent")
    i.add_argument("--timezone", default="America/Chicago")
    i.add_argument("--list", action="store_true", help="list registered feeds and stop")
    i.add_argument("--include-cwz", action="store_true", help="also add Connected Work Zone (CWZ) feeds")
    i.add_argument("--force", action="store_true", help="overwrite an existing config")
    i.add_argument("--root", default=".", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.cmd == "init":
        return init_mod.cmd_init(a)
    cfg = pipeline.load_config(a.config)

    if a.cmd == "validate":
        print(f"{cfg['id']}: {cfg['title']} — {len(cfg['sources'])} sources")
        for s in cfg["sources"]:
            print(f"  {s['id']:<12} {s['type']:<8} {s.get('label', s['id'])}")
        return 0
    if a.cmd == "summary":
        for p in (jload(os.path.join(a.out, "projects.json"), {}) or {}).get("projects", []):
            if p.get("status") == "ended":
                continue
            flags = "".join(f for f, k in (("R", "reportOnly"), ("L", "liveReport"), ("X", "fullClosure"),
                                           ("P", "stillReported")) if p.get(k))
            print("|".join([p["id"], p["route"], p.get("direction", ""), (p.get("location") or "")[:60],
                            ",".join(p.get("counties", [])), f"{p.get('startDate') or ''}..{p.get('endDate') or ''}", flags]))
        return 0
    status = pipeline.run(cfg, a.out, a.cache or os.path.join(".cache", cfg["id"]), offline=a.offline,
                          only=set(a.only.split(",")) if a.only else None)
    return 0 if any(v["ok"] for v in status["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
