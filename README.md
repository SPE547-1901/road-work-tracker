# Road Work Tracker

A map and list of current road construction and closures for any region, built from the work-zone feeds that state and local transportation agencies already publish.

Each project shows:
- a map with the work zone highlighted;
- start and end dates, with progress and "starts in / ends in" notes;
- description, lanes, hours, detours, restrictions, contractor;
- every source that reports it, including agency news releases matched to it by route.

It's a static site (one HTML file plus three JSON files) and a small Python pipeline that refreshes the JSON on a schedule. No server, database, or API keys are required for the built-in sources. GitHub Actions can run the refresh and host the site on GitHub Pages for free.

It works for any US state, metro area or group of states. One command starts a config from the federal registry of work-zone feeds, and two worked examples ship with it.

## Supported sources

| Type | What it reads | Configure with |
|---|---|---|
| `wzdx` | Any [Work Zone Data Exchange](https://www.transportation.gov/av/data/wzdx) feed (v2–v4). WZDx is the USDOT standard, and dozens of state and local DOTs publish one. Find yours in the [WZDx Feed Registry](https://catalog.data.gov/dataset/work-zone-data-exchange-wzdx-feed-registry). | feed URL, optional API key |
| `arcgis` | Any ArcGIS feature/map layer of work zones or street closures (common for cities and counties) | layer URL plus a field mapping |
| `modot` | Example of a state-specific adapter: Missouri DOT's Traveler Information map, any counties | county list |
| `idot` | Example of a state-specific adapter: Illinois DOT's construction, closure and live-traffic layers, any district | district number |

News comes from any RSS, Atom or JSON newsroom feed, plus an optional hand-kept file. You can also add news-only "reports" for closures an agency announced that no feed lists yet.

Adding another source type is one Python module; see [docs/ADAPTERS.md](docs/ADAPTERS.md).

## Quick start

Needs Python 3.9+ and nothing else.

```bash
# 1. start a config for your state(s) from the USDOT WZDx Feed Registry
python -m tracker init --states MO,KS --id kc --title "Kansas City Road Work" --bbox=-95.1,38.7,-94.2,39.45

# 2. fetch the feeds and write site/data/*.json
python -m tracker run --config config/regions/kc.json

# 3. look at it
python -m http.server 8000 -d site     # then open http://localhost:8000
```

`init` finds every active WZDx feed registered for the states you name (state DOTs, toll authorities, counties, cities) and writes `config/regions/<id>.json`. If you don't pass `--bbox`, it uses the states' combined extent. Feeds that need an API key get a `${NAME}` placeholder, and `init` prints where to sign up. To see what's registered before writing anything, run `python -m tracker init --list --states OH,PA`. If your state has no WZDx feed, add an `arcgis` source by hand; most agencies publish their closure map as an ArcGIS layer.

Two examples are in `config/regions/`:
- `example-wzdx.json`: the Kansas City metro across Missouri and Kansas, from both DOTs' WZDx feeds (no keys needed).
- `example-mo-il.json`: MoDOT and IDOT read through their own map services. This shows the state-specific `modot` and `idot` adapters, including IDOT's live traffic reports and news-only reports traced along a route.

Other commands:

```bash
python -m tracker validate --config config/regions/kc.json     # check a config
python -m tracker summary  --config config/regions/kc.json     # one line per current project
python -m tracker run --config ... --offline                   # rebuild from cached feed responses
python -m tracker run --config ... --only missouri-dot         # refresh one source; others keep their data
python -m unittest discover tests                              # offline tests
```

## Set it up for your area

1. Run `init` for your states, as above, or copy one of the examples.
2. Narrow `bbox` (the area to keep, `[west, south, east, north]` in degrees) and `home` (the first map view) to your area. Set `timezone`.
3. Add or remove sources. Every type and option is in [docs/CONFIG.md](docs/CONFIG.md).
4. Optional: add agency newsroom RSS feeds under `news.feeds`, with `keywords` to keep only items about your area.
5. Run `validate`, then `run`, then preview.

## Publish on GitHub Pages

1. Push this repo to GitHub.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**.
3. In **Settings → Secrets and variables → Actions → Variables**, add `REGION` = your config's id (the file name without `.json`). Until it's set, the workflow publishes the site but skips the data refresh.
4. If a feed needs an API key, add it under **Secrets** and pass it through in `.github/workflows/refresh.yml` (see the comment there). Reference it in the config as `"${NAME}"`.
5. Run the **Refresh data and publish** workflow once from the Actions tab. Scheduled runs are off by default (uncomment the `schedule:` block in `.github/workflows/refresh.yml` to run it daily); each run commits the new `site/data/*.json`, and redeploys.

Any static host works too (Netlify, Cloudflare Pages, S3): publish the `site/` folder and run the pipeline on a schedule.

## Map tiles

The page draws street maps from any XYZ tile service set in the config (`map.tiles`, `map.attribution`). The default is OpenStreetMap's standard tiles. They're fine for development and light personal use, but the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/) asks heavy or commercial sites to use another provider or host their own. For a busy public site, switch to a commercial or self-hosted tile service and update the attribution.

## How it works

```
 agency feeds ──> adapters ──> merge with last run ──> site/data/projects.json ─┐
 newsroom RSS ──> news matcher ─────────────────────> site/data/status.json ───┼─> site/index.html
 config/regions/<id>.json ──────────────────────────> site/data/region.json ───┘
```

- **Adapters** turn each feed into projects with a common shape: route, direction, location, dates, impact, geometry, and sources.
- **Merging** keeps a project's first-seen date and news links across runs. Projects that drop off a feed stay as "finished" for 14 days. If a feed fails, its projects from the last run are kept. A feed that suddenly lists less than 30% of what it had is treated as an outage for two runs before it's believed.
- **News** items are matched to projects that share a route number, narrowed by town or county names when there are many matches.
- **The page** is one file of HTML, CSS and JavaScript with no libraries. It has an SVG pan/zoom map, filters (agency, status, county, search, sort) and per-project cards. It re-checks the data every 5 minutes.

## Data and attribution

The tracker links to agencies' own pages and quotes only short summaries from their news feeds. Check each source's terms before a commercial launch. Agency data can lag what's happening on the road, so the page reminds people to check agency sites before they travel.

## License

MIT; see [LICENSE](LICENSE).
