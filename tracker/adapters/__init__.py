"""Feed adapters. Each module has fetch(src, region) -> raw JSON and build(raw, src, region) -> [project].

An adapter may also define trace(src, route, frm, to) -> [[lon, lat], ...] so news-only reports that name a
route and two crossing roads can be drawn (see tracker/reports.py).

To add a source type, drop a module here and register it below. docs/ADAPTERS.md has the project schema.
"""
from . import arcgis, idot, modot, wzdx

REGISTRY = {"modot": modot, "idot": idot, "wzdx": wzdx, "arcgis": arcgis}


def get(kind):
    if kind not in REGISTRY:
        raise KeyError(f"unknown source type {kind!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[kind]
