# Writing an adapter

An adapter is a module in `tracker/adapters/` with two functions, registered in `tracker/adapters/__init__.py`:

```python
def fetch(src, region):
    """Download the feed. Return JSON-serialisable data; it is cached for --offline runs."""

def build(raw, src, region):
    """Turn the cached data into a list of projects (see the shape below)."""
```

- `src` is the source's block from the region config.
- `region` is the whole config; `region["bbox"]` is the area to keep.
- Optionally, add `trace(src, route, frm, to)` returning `[[lon, lat], ...]` so news-only reports can be drawn along a route.

Useful helpers in `tracker/common.py`:
- `http_get(url, params, headers=..., text=True)`
- `query(layer, where, env=...)`: a paged ArcGIS query in lon/lat
- `esri_geom()`: ArcGIS geometry to segments
- `simplify()`, `in_bbox()`, `bbox_of()`, `county_for()`
- `parse_date()`: epoch ms or common date strings
- `pretty_loc()`: DOT shorthand to readable text
- `title_case()`, `dir_label()`
- `finish(project)`: fills defaults and the bbox

Test with a fixture file and `unittest`, as `tests/test_tracker.py` does for WZDx, so the tests stay offline.

## Project shape

| Field | Type | Notes |
|---|---|---|
| `id` | string | **Stable across runs.** History (first seen, news links) is keyed on it. Prefix it with the source's `idPrefix` or `id`. |
| `source` | string | The source's `label`; picks the colour and filter button. |
| `route`, `direction`, `location` | string | e.g. `I-55/70`, `Westbound`, `US 40 to IL 157` |
| `counties`, `nearTown` | string[], string | Drive the county filter and news matching. Optional. |
| `description`, `workType`, `impact` | string | |
| `impactLevel` | `closed` \| `high` \| `medium` \| `future` | Sorting and the "biggest impact" order |
| `fullClosure` | bool | Red on the map, counted in "Road closures" |
| `lanesClosed`, `hours`, `detour`, `restriction`, `speedLimit`, `contractor`, `jobNumber` | string | Empty when unknown |
| `startDate`, `endDate` | `YYYY-MM-DD` or null | In the region's time zone |
| `segments` | array | `{type:"line", coords:[[lon,lat],…]}` or `{type:"point", coord:[lon,lat]}`. Round to 5 decimals. |
| `zones` | array? | Multi-zone jobs: `{route, direction, location, lanesClosed}`, listed on the card and searchable |
| `sources` | array | `{kind:"feed"|"news"|"social", name, url?, date?, summary?, live?}` |
| `sourceUrl` | url | Where the card's agency link points |
| `liveReport`, `liveReportSince`, `stillReported`, `postedEnd` | | For feeds of live incidents without end dates (see the `idot` adapter) |

The pipeline adds `sourceId`, `bbox`, `status`, `firstSeen`, `lastSeen` and `updates`.

## status.json

`{lastRefresh, firstRefresh, sources: {id: {ok, label, projects | error}}, counts: {active, upcoming, ended}, feedHold: {id: n}, news: {items, checked}}`
