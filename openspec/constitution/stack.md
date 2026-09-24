# Tech Stack

Decisions, with reasons and rejected alternatives. A proposal that introduces a new
dependency amends this file in the same change.

Selection criteria, in order: **(1)** serves a principle in `project.md`;
**(2)** runs on a laptop and a single modest server, since there is no cluster;
**(3)** open source, so the framework is reproducible by anyone;
**(4)** boring and maintained over novel.

---

## 1. Offline pipeline — Python

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for geospatial, EO and simulation |
| Environment | **uv** | Fast, lockfile by default (P2); manages Python itself |
| Vector geospatial | geopandas, shapely 2.x | Standard; shapely 2 is vectorised |
| Raster / EO | rasterio, rioxarray, xarray | xarray is the space-time cube substrate for fusion |
| OSM access | osmnx 2.x | Road networks and geocoding in one API |
| Terrain | Copernicus GLO-30 DEM | Free, global, 30 m; LiDAR is unavailable for Nairobi (P1) |
| Flood observation | Sentinel-1 via Google Earth Engine | All-weather SAR; no ground instrumentation needed (P1) |
| Rainfall | CHIRPS and GPM IMERG | Free, gauge-sparse-tolerant, established in East African studies |
| Traffic simulation | **SUMO** + sumolib + TraCI | Open source, scriptable, the standard in the reviewed literature |
| Spatial indexing | **H3** | Hierarchical hex grid; aligns flood rasters and traffic links without reprojection artefacts |
| Agents | Custom contract-net over TraCI | Mesa's scheduler fights TraCI's step loop; negotiation logic is small enough to own |
| Exploration | **JupyterLab** | Interactive work on EO rasters, calibration and KPI inspection — the tight loop that scripts cannot give |
| Notebook hygiene | **jupytext** + **nbstripout** | Notebooks paired to `.py:percent`; outputs stripped so diffs are reviewable |
| Notebook maps | **lonboard** + ipyleaflet | lonboard renders deck.gl layers in the notebook — the same layers as the web UI (§6) |
| Columnar data | **pyarrow** | Required by lonboard and by GeoParquet I/O (§4); dev extra until the pipeline writes GeoParquet |
| Notebook execution | **papermill**, `pytest --nbmake` | Parameterised runs; key notebooks executed in CI so they cannot rot |
| Testing | pytest | Offline by policy; network confined to `fetch_*` |

**Rejected:** ArcGIS and VISSIM (licensed, breaks P2 — and VISSIM licensing is
precisely what makes two reviewed studies hard to replicate); MATSim (heavier
calibration burden than SUMO for a CBD-scale network).

### Notebooks: where they belong

JupyterLab is for **labs and experimentation**, in `notebooks/`. It is not the
pipeline, and four rules keep the boundary honest:

1. **Pipeline code never imports a notebook.** Dependency runs one way: notebooks
   import `nairobi_dt`, installed editable into the same uv environment, so the lab
   kernel and the pipeline are the same code.
2. **Findings graduate.** A result worth keeping becomes a function in `src/` with a
   test. The notebook that discovered it stays as the record of how, not as the
   implementation.
3. **Outputs are stripped, sources are paired.** `nbstripout` on commit and jupytext
   pairing mean a reviewer sees a readable diff, not a wall of base64 PNG.
4. **Anything cited in the thesis is executed in CI.** Notebooks that produce a
   figure or number for the paper run under `pytest --nbmake` against pinned input
   artefacts. A notebook that cannot be re-executed is not evidence (P2).

Naming: `NN-topic.ipynb`, numbered by the pipeline step it explores —
`01-terrain-inspection.ipynb`, `02-sentinel1-flood-extents.ipynb`,
`03-demand-calibration.ipynb` — so the lab record maps onto the roadmap.

---

## 2. Services — Python

| Concern | Choice | Why |
|---|---|---|
| API and gateway | **FastAPI** + `websockets` | Async, typed, generates OpenAPI that feeds `contracts/`; confined to the adapter layer (§3) |
| Message bus | **Redpanda** (Kafka API) | Kafka semantics without JVM/ZooKeeper overhead; single binary in dev |
| Device protocol | **MQTT** (Mosquitto) | The protocol field sensors actually speak; bridges to Kafka |
| Simulation worker | SUMO in its own container, driven by **libsumo** | libsumo runs in-process — far faster than TraCI sockets at 1 Hz over a CBD network |
| Raster tiles | **TiTiler** | Serves COGs directly; no pre-rendering step for imagery or flood depth |
| Vector tiles | **Martin** | MVT straight from PostGIS; live link state without a tiling job |
| Observability | **OpenTelemetry → Prometheus + Grafana** | Latency classes are KPIs (P3), so tracing is measurement apparatus, not ops garnish |

**Rejected:** Kafka with ZooKeeper (operational weight); MQTT alone without Kafka
(no replay, and replay is needed to re-run scenarios deterministically);
TraCI-over-socket in production paths (protocol overhead dominates at 1 Hz).

---

## 3. API architecture — ports and adapters

The API service follows hexagonal architecture. The dependency rule is absolute:
**the domain imports nothing from FastAPI, SQLAlchemy, Redis, Kafka or SUMO.**
Adapters depend on the domain; the domain depends on nothing.

This is not architectural decoration. Two decisions in this document are still open
(D-1 Timescale, D-2 SUMO version), and a third — whether ISAC is emulated or the
sensing layer is reframed — is open in the thesis itself. Each of those swaps an
adapter. Under hexagonal boundaries none of them touches a domain rule or
invalidates a domain test.

### Layout

```
services/api/
├── domain/                  # pure Python; no framework imports
│   ├── model.py             # Link, TrafficState, FloodState, ControlCommand, ScenarioRun
│   ├── rules.py             # impassability threshold, routing avoidance, staleness
│   └── errors.py
├── ports/
│   ├── inbound.py           # use-case protocols the outside world calls
│   └── outbound.py          # protocols the domain calls: repositories, publishers, clock
├── application/             # use cases: orchestration only, no I/O
│   ├── ingest_traffic_state.py
│   ├── apply_flood_state.py
│   ├── issue_control_command.py
│   └── run_scenario.py
├── adapters/
│   ├── inbound/             # driving: REST, WebSocket, Kafka consumer, MQTT, CLI
│   └── outbound/            # driven: Redis, Postgres, Redpanda, MinIO, Zarr, libsumo
└── composition.py           # the only module that knows every concrete type
```

### Ports

| Port | Direction | Implementations |
|---|---|---|
| `TrafficStatePort`, `FloodStatePort` | inbound | REST, Kafka consumer, replay from GeoParquet |
| `ControlCommandPort` | inbound | REST, agent service |
| `LinkStateRepository` | outbound | Redis (hot), Postgres (durable), in-memory (tests) |
| `TimeSeriesRepository` | outbound | TimescaleDB, partitioned Postgres (D-1), in-memory |
| `EventPublisher` | outbound | Redpanda, in-memory recorder |
| `SimulationRunner` | outbound | libsumo worker, deterministic stub |
| `ArtifactStore` | outbound | MinIO, local filesystem |
| `Clock` | outbound | system, frozen (tests) |

A `Clock` port looks fussy until age of information is a KPI. Time must be
injectable, or latency tests are untestable and flaky by construction.

### Rules

- **Contract models stop at the boundary.** Pydantic models generated from
  `contracts/` belong to adapters. Adapters map them to domain dataclasses on the way
  in and back on the way out. A schema version bump must not ripple into domain code.
- **Use cases orchestrate; they do not perform I/O.** Every side effect goes through
  an outbound port.
- **`composition.py` is the only place concrete adapters are named.** FastAPI's
  `dependency_overrides` substitutes fakes in tests without a DI framework.
- **Domain tests run with no containers and no network** — in-memory fakes only, in
  milliseconds. Adapter tests run against real services via testcontainers and are
  marked `integration`.

### What lives in the domain

The impassability threshold, the routing avoidance formula, staleness limits per
latency class, and command validity all encode research decisions. They belong in
`domain/rules.py`, where they are unit-testable in isolation and citable in the
methodology chapter — not scattered across route handlers.

## 4. Data and storage

One store cannot serve a 250 ms control loop, a multi-terabyte-capable space-time
cube, and reproducible scenario analytics. Each tier is chosen for one job.

| Tier | Technology | Holds | Read latency |
|---|---|---|---|
| Hot state | **Redis** | Latest link state, active closures, agent state | sub-ms |
| Operational | **PostgreSQL 16 + PostGIS + TimescaleDB** | Live spatial state, sensor time series, control command log | ms |
| Fusion cube | **Zarr** on object storage | Aligned space-time cube (flood depth, rainfall, traffic) | seconds |
| Artefacts | **MinIO** (S3 API) | Terrain, 3D Tiles, COGs, scenario outputs, GeoParquet | seconds |
| Analytics | **DuckDB** over GeoParquet | KPI computation, cross-scenario comparison | seconds |

**Rules that follow from the latency budget:**

- The **L1 actuation path touches Redis only.** A Postgres round trip plus
  connection contention cannot be guaranteed inside 250 ms under load. Postgres
  receives the same writes asynchronously, for the record, not for the decision.
- The **fusion cube is Zarr, not Postgres.** It is a chunked N-dimensional array
  written and read by xarray; storing it as rows would be slower and lossy of
  structure. Postgres holds the per-link *derived* state that agents query.
- **Scenario outputs are immutable GeoParquet**, written once per run and analysed
  with DuckDB. KPI computation never queries the live database, so evaluation
  cannot perturb the thing being evaluated, and any run can be recomputed from its
  artefacts alone (P2).

**Retention.** Raw 1 Hz sensor data for 7 days; 1-minute continuous aggregates
indefinitely. Nairobi's CBD generates on the order of 10⁷ rows per day at full
instrumentation — small, but not worth keeping raw forever.

**Open decision (D-1): TimescaleDB licensing.** TimescaleDB is source-available
under the Timescale License, not OSI open source. At this data volume, PostgreSQL's
native declarative partitioning with BRIN indexes would serve equally well and keep
the stack wholly under the PostgreSQL licence. Timescale is retained for continuous
aggregates and compression, which materially simplify the KPI pipeline. If strict
licence purity is required for publication, swap to native partitioning — the schema
is designed so this is a migration, not a redesign.

**Rejected:** InfluxDB (a second database for what Timescale already does beside
PostGIS); MongoDB (no spatial-temporal advantage here); storing rasters as PostGIS
`raster` (slower than COG plus TiTiler, and awkward for xarray).

---

## 5. Runtime — TypeScript

| Concern | Choice | Why |
|---|---|---|
| Language | **TypeScript** (strict) | Contract types generate directly from `contracts/` |
| 3D globe | **CesiumJS** | Native 3D Tiles and quantized-mesh terrain; geospatially accurate by construction |
| Analytics rendering | **deck.gl** over **MapLibre GL** | GPU-rendered data layers for the analytics views (see §6) |
| Build | **Vite** | Fast, first-class TS, `vite-plugin-cesium` handles Cesium's asset quirks |
| UI framework | **React** + **Resium** | Declarative Cesium entities; keeps scene state out of imperative callbacks |
| Client state | **Zustand** | Small and unopinionated; Redux ceremony is unwarranted |
| Server state | **TanStack Query** | Caching and revalidation for artefact and scenario endpoints |
| Live transport | **WebSocket** (native), MQTT-over-WS as fallback | Direct broker access from the browser if the gateway becomes a bottleneck |
| Charts | **Observable Plot** | KPI time series; concise grammar, small bundle |
| Styling | **Tailwind CSS** | Operator UI; no design system to maintain |
| Testing | **Vitest** + **Playwright** | Unit and one end-to-end path: hazard appears → overlay updates |

**Rejected:** Three.js (would mean rebuilding geospatial correctness by hand);
Angular or Vue (no advantage here); Cesium ion hosted assets as a dependency (a paid
third-party service on the critical path breaks P2 — used only as a convenience
during development).

---

## 6. Rendering split: Cesium and deck.gl

deck.gl has official bindings for MapLibre, Mapbox, Google Maps and ArcGIS. **It has
no official CesiumJS binding.** Overlaying deck.gl on a Cesium globe requires
synchronising two cameras and two WebGL contexts by hand, which drifts at oblique
angles and on terrain. The two therefore occupy separate views rather than one
composited scene.

| View | Renderer | Purpose | Layers |
|---|---|---|---|
| **City twin** (3D) | CesiumJS | Operator situational awareness: buildings, terrain, flood surface, vehicles | 3D Tiles, terrain, time-dynamic depth overlay |
| **Analytics** (2D/2.5D) | deck.gl + MapLibre | Pattern inspection and KPI exploration | `H3HexagonLayer`, `TripsLayer`, `ArcLayer`, `HeatmapLayer`, `PathLayer` |

The analytics layers map directly onto existing design decisions:

- `H3HexagonLayer` renders the fusion grid natively — the same H3 indexing used in
  the pipeline, so no reprojection and no aggregation mismatch between what is
  computed and what is shown.
- `TripsLayer` animates vehicle trajectories from a scenario run, making rerouting
  around flooded links legible in a way a static map is not.
- `ArcLayer` shows origin-destination flow shifts between baseline and multi-agent
  control — a figure the results chapter needs.
- `HeatmapLayer` locates queue-length hotspots for the KPI discussion.

Import from scoped submodules (`@deck.gl/layers`, `@deck.gl/geo-layers`) rather than
the `deck.gl` umbrella package; the umbrella pulls in roughly a megabyte of unused
code.

**Reconsider if:** the analytics views come to need true 3D context. deck.gl can
render 3D Tiles itself through `Tile3DLayer`, which would allow one renderer for
both views at the cost of Cesium's superior terrain and globe handling. Not worth it
now; worth revisiting if maintaining two view components becomes the larger burden.

---

## 7. Web UI — responsive design

The operator console must be usable on a control-room display, a tablet in a
municipal office, and a phone in the field. Responsiveness here is not only layout
reflow: a 3D globe costs bandwidth, memory and battery that a field device may not
have, so the *content* adapts too, not merely its arrangement.

### Tiered experience

| Tier | Viewport | 3D twin | Layout |
|---|---|---|---|
| Console | ≥ 1280 px | Cesium, full detail | Persistent side panels, multi-pane |
| Tablet | 768–1279 px | Cesium, reduced pixel ratio | Collapsible panels, single map focus |
| Field | < 768 px | **Off by default**, loaded on request | 2D MapLibre map, KPI cards, hazard list |

The field tier is a deliberate downgrade. The questions that matter on a phone —
which links are closed, where is flooding now, what is the response time — are
answered better by a 2D map and a list than by a globe that takes fifteen seconds to
load over a congested cell. Cesium is code-split and dynamically imported, so the
field tier never downloads it unless the user asks.

### Principles

- **Mobile-first, fluid, content-driven breakpoints.** Tailwind's scale as the
  default, but breakpoints chosen where the layout actually breaks.
- **Container queries over viewport queries** for panels. A KPI panel should respond
  to the space it occupies, not to the window, since the same panel appears docked,
  drawered and full-screen.
- **Map canvas resizes via `ResizeObserver`**, not window resize events; panels
  collapse without the viewport changing.
- **Device pixel ratio is capped** on the tablet and field tiers. Rendering a
  textured city at DPR 3 on a mid-range phone is how a device gets hot and slow.
- **Touch targets ≥ 44 px, and map picking uses a wider hit tolerance under
  `pointer: coarse`.** Selecting a road link with a fingertip is not selecting it
  with a mouse.
- **Update rate degrades gracefully.** On a constrained network or a small viewport,
  the client subscribes to aggregated state at a lower cadence rather than per-link
  state at 1 Hz. The L2 budget governs the decision loop, not the display.

### Accessibility

WCAG 2.2 AA is the target, with three consequences that matter for this domain:

- **Colour is never the sole encoding of hazard.** Flood depth uses a
  colourblind-safe sequential ramp *and* a hatch pattern, with depth values
  available as text. An operator with deuteranopia must read the same map.
- **`prefers-reduced-motion` disables trajectory animation.** The `TripsLayer`
  replay is informative but vestibularly hostile; a static path with a time slider
  replaces it.
- **The hazard state has a text equivalent.** A live region listing closed links and
  current depths, readable by a screen reader, independent of any canvas.

### Testing and budgets

- **Playwright runs the viewport matrix** — 390×844, 820×1180, 1920×1080 — over the
  end-to-end path: hazard appears → overlay updates → link list reflects closure.
- **Performance budgets enforced in CI:** initial JS ≤ 250 kB gzipped for the field
  tier excluding Cesium; time to interactive ≤ 3 s on a simulated 4G profile.
  Cesium's weight is precisely why it is not in the field bundle.
- **Lighthouse accessibility ≥ 95** as a merge gate.

## 8. Tiling and 3D content

| Artefact | Source | Pipeline | Output |
|---|---|---|---|
| Terrain | Copernicus GLO-30 | GDAL → quantized-mesh | Cesium terrain tileset |
| Buildings | OSM 3D footprints, extruded by level count | py3dtiles / pg2b3dm | 3D Tiles (b3dm) |
| Imagery | Sentinel-2 cloud-free composite | COG in MinIO → TiTiler | XYZ tiles on demand |
| Road network | OSM → SUMO `netconvert` | sumolib export | GeoJSON for rendering; `.net.xml` for simulation |
| Link state | PostGIS | Martin | MVT, live |
| Flood surfaces | Modelled depth grids | COG per timestep → TiTiler | Time-dynamic overlay |

OSM building heights in Nairobi are incomplete. Missing heights are estimated from
level counts, and where those are absent too, from a land-use prior — **with the
estimate flagged per building** in the tileset (P1, P4). Visualisation never implies
precision the data lacks.

---

## 9. Contracts

Schemas in `contracts/` are the single definition of anything crossing a boundary.

| Contract | Carries | Latency class |
|---|---|---|
| `traffic_state` | Per-link counts, speeds, queue lengths, observation age | L2 |
| `flood_state` | Per-link depth, impassability flag, source, confidence | L3 |
| `control_command` | Signal plan changes, link closures, EMV pre-emption | L1 |
| `scenario_run` | Scenario id, seed, control strategy, artefact versions | — |
| `kpi_result` | KPI values with scenario and baseline identifiers | — |

JSON Schema is the authority. `datamodel-code-generator` produces Pydantic models;
`json-schema-to-typescript` produces TS types. Every message carries a schema
version and an observation timestamp — age of information is a KPI, so it cannot be
an afterthought.

---

## 10. Containerisation

Everything runs in containers, including the pipeline. A container image plus a
lockfile plus pinned data artefacts is what makes a result reproducible years later
(P2); "works on my laptop" is not a methodology.

### Services

| Container | Image basis | Role |
|---|---|---|
| `web` | node:22 build → nginx:alpine | Serves the built TS bundle (Cesium code-split, §7); proxies `/api` and `/ws` |
| `api` | python:3.11-slim | FastAPI gateway, WebSocket fan-out; hexagonal (§3) |
| `sim` | eclipse-sumo base | SUMO + libsumo scenario worker |
| `agents` | python:3.11-slim | Contract-net agents; consumes state, emits commands |
| `ingest` | python:3.11-slim | MQTT → Kafka bridge, schema validation |
| `pipeline` | python:3.11-slim + GDAL | Offline steps; run on demand, not long-lived |
| `lab` | same image as `pipeline`, plus JupyterLab | Experimentation; mounts `notebooks/` and `data/`, reaches MinIO and Postgres |
| `titiler` | ghcr.io/developmentseed/titiler | Raster tiles from COGs |
| `martin` | ghcr.io/maplibre/martin | Vector tiles from PostGIS |
| `db` | timescale/timescaledb-ha (PostGIS included) | Operational store |
| `redis` | redis:7-alpine | Hot state |
| `broker` | redpandadata/redpanda | Event log |
| `mqtt` | eclipse-mosquitto | Device protocol |
| `minio` | minio/minio | Artefacts and tilesets |
| `otel`, `prometheus`, `grafana` | upstream | Latency and KPI measurement |

### Conventions

- **Multi-stage builds.** Build dependencies (GDAL headers, node toolchain) never
  reach the runtime image.
- **Pinned by digest,** not by tag. `python:3.11-slim` moves; a digest does not. The
  reproducibility claim depends on this.
- **Non-root users** in every image; read-only root filesystems where the service
  permits.
- **Healthchecks on every service,** with `depends_on: condition: service_healthy`.
  The pipeline must not start before Postgres accepts connections.
- **Named volumes** for Postgres, MinIO and Redpanda. Bind mounts only for code in
  development.
- **`.dockerignore`** excludes `data/`, `.venv`, `node_modules` — without it the
  build context reaches gigabytes and every build crawls.

### Compose layering

```
docker-compose.yml           # base: all services, production defaults
docker-compose.dev.yml       # bind mounts, hot reload, exposed debug ports
docker-compose.gpu.yml       # optional: CUDA for ML inference, if it arrives
```

```bash
docker compose up -d                                    # production-like
docker compose -f docker-compose.yml -f docker-compose.dev.yml up   # development
```

### The simulation container

SUMO is the awkward one: it needs the network file, the demand file and a writable
output path, and it must not be rebuilt for each scenario. It therefore mounts
artefacts read-only from MinIO-synced volumes and writes results to a run-scoped
output volume keyed by `scenario_run.id`. Scenario runs are jobs, not services —
started through the API, torn down on completion.

**Open decision (D-2): SUMO base image.** Eclipse publishes container images, but
the pipeline pins a specific SUMO version because simulation results are
version-sensitive. Confirm which image and version before Step 3, and record the
version in every `scenario_run` record.

---

## 11. Development environment

```bash
git clone <repo> && cd nairobi-dt
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
docker compose exec pipeline uv run pytest
docker compose up lab                                    # JupyterLab on http://localhost:8888
docker compose exec web npm test                          # includes the Playwright viewport matrix
```

Host-side tooling is optional and exists only for convenience:

```bash
uv sync --extra dev          # Python, outside containers
npm --prefix web ci          # TypeScript, outside containers
```

Ubuntu is the reference platform. QGIS is a manual inspection tool, not a pipeline
dependency — nothing in the build may require it, and nothing in any container
depends on it.
### Continuous integration

**GitHub Actions** (`.github/workflows/ci.yml`) runs on every push to `main` and every
pull request, as three jobs: ruff, mypy and the offline pytest suite; the notebooks
under `pytest --nbmake`; and `openspec validate --all --strict`. Actions are pinned
by commit SHA and uv, Python and OpenSpec by version (P2). The notebook job is the
only one with network access to OpenStreetMap; its osmnx responses are cached and
keyed on `config/study_area.yaml`.

*Rejected:* GitLab CI and self-hosted runners (the repository lives on GitHub, and
there is no server to host a runner). Container builds join CI when the compose
files land.
