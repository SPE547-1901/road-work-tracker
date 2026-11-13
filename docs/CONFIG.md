# Region config reference

A region is one JSON file in `config/regions/`. The quickest start is `python -m tracker init --states <codes> --id <name>`, which fills in every WZDx feed registered for those states. Paths in a config (`reports`, `news.file`) are relative to the repo root. Keys starting with `_` (such as `_about` and `_note`) are comments and are ignored.

```json
{
  "id": "kc",
  "title": "Kansas City Road Work",
  "subtitle": "Missouri DOT · Kansas DOT",
  "timezone": "America/Chicago",
  "bbox": [-95.10, 38.70, -94.20, 39.45],
  "home": [-94.85, 38.88, -94.40, 39.22],
  "map": {"tiles": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
          "attribution": "© OpenStreetMap contributors", "maxZoom": 18},
  "newsTitle": "Latest agency news",
  "footer": "Shown at the bottom of the page.",
  "sources": [ ... ],
  "news": { ... },
  "reports": "config/reports/kc.json",
  "endedKeepDays": 14
}
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Short name. Used for the cache folder and the page's saved filters. |
| `title`, `subtitle` | title yes | Page heading and browser title. |
| `timezone` | | IANA zone for "today" and for dates given as timestamps. Default `America/Chicago`. |
| `bbox` | yes | `[west, south, east, north]` in degrees. WZDx and ArcGIS sources keep only zones inside it; the map can't pan beyond it. |
| `home` | | The first map view, same format. Defaults to `bbox`. |
| `map.tiles` | | XYZ tile URL with `{z}`, `{x}`, `{y}`; optional `{s}` (subdomain) and `{r}` (`@2x` on high-density screens). |
| `map.attribution` | | Credit shown on every map; may contain a link. Required by most tile providers. |
| `map.maxZoom`, `map.subdomains` | | Defaults 18 and `"abc"`. |
| `newsTitle`, `footer` | | Page text. |
| `endedKeepDays` | | How long a project that dropped off its feed stays on the page as "finished". Default 14. |

## Sources

Every source has `id` (unique), `type`, and `label` (the agency name shown on cards and in the filter). Optional on all types:

| Key | Meaning |
|---|---|
| `color`, `darkColor` | Line colour on the map, in light and dark mode. Defaults come from a built-in palette. |
| `state` | Text for this agency's filter button, e.g. "Missouri". Defaults to the label. |
| `mapName` | Text of the card link to the agency's own map, e.g. "MoDOT traveler map". |
| `idPrefix` | Prefix for project ids. Keep it stable, because ids carry history between runs. |

Any string value may contain `${NAME}`, which is replaced with the environment variable `NAME`. Use this to keep API keys out of the repo.

### `wzdx`

The [WZDx Feed Registry](https://catalog.data.gov/dataset/work-zone-data-exchange-wzdx-feed-registry) lists the published feeds, and `python -m tracker init --list` shows them. A feed that needs a key reads it from an environment variable (see above).

```json
{"id": "iowa", "type": "wzdx", "label": "Iowa DOT",
 "url": "https://.../wzdx.geojson", "params": {"key": "${IOWA_KEY}"}, "headers": {},
 "eventTypes": ["work-zone"], "roadFilter": "^(I-|US )", "infoUrl": "https://511ia.org/"}
```

- `eventTypes`: add `"detour"` to show detours as their own cards.
- `roadFilter`: regex; keep only roads whose name matches.
- `infoUrl`: where the card's agency link points.
- Features that share road, description and dates (usually the two directions of one project) become one card. `vehicle_impact: all-lanes-closed` marks a full closure.

### `arcgis`

```json
{"id": "city", "type": "arcgis", "label": "City of Example",
 "layer": "https://services.arcgis.com/.../FeatureServer/0", "where": "STATUS <> 'Complete'",
 "fields": {"id": "GlobalID", "route": "STREET", "location": "LIMITS", "description": "DESCRIPTION",
            "start": "START_DATE", "end": "END_DATE", "direction": "DIRECTION", "workType": "TYPE",
            "lanes": "LANES", "hours": "HOURS", "detour": "DETOUR", "contractor": "CONTRACTOR",
            "county": "COUNTY", "town": "CITY"},
 "closed": {"field": "TYPE", "pattern": "full|road closed"},
 "groupBy": "PROJECT_NO", "infoUrl": "https://example.gov/closures"}
```

To find field names, open the layer URL in a browser; the REST page lists them. Dates can be epoch milliseconds (the ArcGIS default) or common text formats.

### `modot` and `idot` (state-specific examples)

These two show how to read a state's own map service when its WZDx feed is missing or thinner than its map. Both states also publish WZDx feeds, which `init` picks up. Use them as models for an adapter for your state (see [ADAPTERS.md](ADAPTERS.md)).

#### `modot`

`{"id": "modot", "type": "modot", "label": "MoDOT", "counties": ["JACKSON", "CLAY", "PLATTE"]}`

Counties use MoDOT's `COUNTY_NAME` spelling, in upper case (`"ST. LOUIS CITY"` for the city). An empty list takes the whole state. Multi-road contracts become one card listing every work zone.

#### `idot`

`{"id": "idot", "type": "idot", "label": "IDOT", "district": 1, "liveReports": true}`

- `district`: IDOT district number (1–9).
- `liveReports`: also read IDOT's live traffic reports. These have no end dates, so they're attached to the project they describe or grouped into "live report" cards.
- `roadsLayer`: an IDOT district road-inventory layer. It lets news-only reports be traced along a route between two crossing roads; `example-mo-il.json` has District 8's.
- `bridgeRoutes`: bridge names in live reports mapped to route labels.

## News

```json
"news": {
  "feeds": [{"agency": "IDOT", "url": "https://illinois-department-of-transportation.prezly.com/feed.rss",
             "keywords": ["Madison County", "St. Clair County", "District 8"]}],
  "file": "config/news/kc.json",
  "maxAgeDays": 30, "maxItems": 30
}
```

- **Feeds** can be RSS 2.0, Atom, or JSON (Prezly newsrooms and JSON Feed). With `keywords`, only items whose title or summary contains one are kept, which is how you narrow a statewide newsroom to your area.
- **Topic:** items mentioning closures, lanes, ramps, detours and similar are "roadwork" and listed first. Meetings, ceremonies and hiring are "other".
- **Matching:** an item is attached to projects on a route it names (I-55, US 40, IL 157, MO 100, …; lettered routes such as Route TT are not matched). With more than 6 matches it is narrowed to projects whose town or county the item mentions.
- **Hand-kept file:** `{"items": [...]}` with the fields `kind` (`news`/`social`), `agency`, `date`, `title`, `url`, `summary`, `topic`, `projectIds`, `vsFeed`. Items there win over feed items with the same URL. Leave out `projectIds` to have them matched automatically.

## Reports (news-only closures)

`config/reports/<id>.json` holds `{"reports": [...]}`. A report draws a closure that an agency announced but no feed lists:

```json
{"source": "IDOT", "route": "I-55/70", "from": "US 40", "to": "IL 157", "direction": "Westbound",
 "start": "2026-11-02", "end": "2026-11-14", "impact": "One lane", "counties": ["Madison"],
 "workType": "Pavement repairs", "description": "In your own words.",
 "coords": [[-90.0119, 38.6805], [-89.9171, 38.7190]],
 "sources": [{"kind": "news", "name": "IDOT news release", "url": "https://...", "date": "2026-10-30"}]}
```

Give the line as `coords` (lon/lat pairs), or set `"trace": "<source id>"` to have that source's adapter trace it. Only the `idot` adapter can trace, and only with `roadsLayer` set. Reports drop off after their end date.
