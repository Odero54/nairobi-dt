# nairobi-dt

A 5G-enabled autonomous digital twin for concurrent urban traffic and flash flood
mitigation in Nairobi, with the central business district as the analysis core and
Nairobi County as the ingestion extent.

The twin's **interface** is fully web-based — TypeScript, CesiumJS and deck.gl. Its
**engine** is not: SUMO, the hydrodynamics and the multi-agent logic run as Python
services and stream state to the browser.

Built with [OpenSpec](https://github.com/Fission-AI/OpenSpec). Each step starts as
experiments in `notebooks/`, then becomes one change proposal, reviewed and approved
before any pipeline code is written in `src/`. A step ships four things together:
the notebooks that informed it, working code, a validated spec, and the matching
section of the methods paper.

---

## Status

| # | Change | Capability | Status |
|---|---|---|---|
| 0 | baseline | Study area, CRS policy, KPIs, latency budget | ✅ |
| 1 | `add-geospatial-foundation` | Terrain, 3D buildings, road network, imagery | ⬜ |
| 2 | `add-flood-hazard-layer` | Inundation surfaces, depth-to-link mapping | ⬜ |
| 3 | `add-traffic-simulation` | SUMO network, demand calibration, baselines | ⬜ |
| 4 | `add-realtime-ingestion` | MQTT/Kafka paths, 5G measurement | ⬜ |
| 5 | `add-spatiotemporal-fusion` | H3 alignment, Zarr cube, age of information | ⬜ |
| 6 | `add-multi-agent-control` | Contract-net negotiation, rerouting, pre-emption | ⬜ |
| 7 | `add-api-gateway` | Ports and adapters, REST and WebSocket | ⬜ |
| 8 | `add-web-twin-viewer` | Cesium scene, live overlays, responsive tiers | ⬜ |
| 9 | `add-analytics-views` | deck.gl layers, KPI exploration | ⬜ |
| 10 | `add-evaluation-harness` | Scenarios, KPI computation, baseline comparison | ⬜ |

Step 0 was built before the project adopted OpenSpec; its notebook and spec are
being backfilled. See `openspec/constitution/roadmap.md` for dependencies, acceptance criteria and the risk register.

---

## Quick start

Everything runs in containers. Host tooling is optional.

```bash
git clone <repo> && cd nairobi-dt
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

docker compose exec pipeline uv run pytest     # Python: pipeline and domain tests
docker compose exec web npm test               # TypeScript: units + viewport matrix
```

| Service | URL |
|---|---|
| Web twin | http://localhost:5173 |
| API (OpenAPI docs) | http://localhost:8000/docs |
| JupyterLab | http://localhost:8888 |
| Grafana | http://localhost:3000 |
| MinIO console | http://localhost:9001 |

Running the pipeline outside containers, for iteration:

```bash
uv sync --extra dev
uv run pytest                          # offline tests
uv run pytest --nbmake notebooks/      # re-execute the lab notebooks
uv run ruff check src tests scripts && uv run mypy
```

The notebooks read OpenStreetMap through the osmnx cache in `cache/` when it is
present, and query OSM live when it is not.

---

## Step 0 — build the study area

```bash
docker compose exec pipeline uv run python scripts/00_build_study_area.py
# or, on the host:  uv run python scripts/00_build_study_area.py
```

Derives three nested spatial tiers from OpenStreetMap rather than hand-drawing them:
the **core** (CBD analysis polygon, where every KPI is computed), the **domain**
(core plus a 1 km buffer, simulated but never scored, to remove edge effects), and
the **county** (data-ingestion extent).

Outputs to `data/processed/`:

- `study_area.gpkg` — layers `tiers`, `corners`, `landmarks`
- `study_area_map.html` — open it and check the core boundary visually
- `study_area_report.json` — areas, corner diagnostics, validation results

The script exits non-zero if validation fails. Landmark coordinates are pinned by
`lonlat` in `config/study_area.yaml`, so the boundary does not depend on a live
geocoder. The core is derived from configuration only and is never edited by hand.

The lab record for this step is `notebooks/00-study-area-exploration.ipynb`, and the
contract is `openspec/specs/study-area/spec.md`.

---

## Layout

```
openspec/          constitution/project.md, constitution/stack.md, constitution/roadmap.md, specs/, changes/
config/            study_area.yaml, latency_budget.yaml, kpis.yaml
contracts/         JSON Schema — the single definition of anything crossing a boundary
src/nairobi_dt/    offline pipeline
services/          api (hexagonal), agents, sim, ingest
web/               TypeScript client: Cesium twin, deck.gl analytics
notebooks/         JupyterLab experimentation, numbered by pipeline step
docs/paper/        methods paper, one section per step
scripts/           step entry points
tests/             offline by default; integration tests marked
```

---

## Conventions

**Configuration, not constants.** Thresholds live in `config/*.yaml` — the
impassability depth, the latency classes, the KPI definitions. Examiners ask what
happens when they change, and the answer should be an edit, not a rebuild.

| File | Contents |
|---|---|
| `config/study_area.yaml` | CRS policy, boundary roads, landmarks, tier settings |
| `config/latency_budget.yaml` | Latency classes L1–L5, end-to-end and network share |
| `config/kpis.yaml` | KPI definitions, baselines, objective mapping |

**CRS policy.** EPSG:32737 (UTM 37S) for all measurement, EPSG:4326 for storage and
exchange, EPSG:3857 for web tiles only. One system, one purpose.

**Tests run offline.** Network access is confined to `fetch_*` functions, so the
suite runs in milliseconds with no containers. Adapter tests hitting real services
are marked `integration`.

**Notebooks are the lab record, not the pipeline.** Pipeline code never imports a
notebook; findings graduate into `src/` with a test. Install the output filter once
per clone:

```bash
uv run nbstripout --install
```

Notebooks cited in the paper are executed in CI via `pytest --nbmake` — one that
cannot be re-executed is not evidence.

---

## Principles

**P1 — Sparsity is a design condition.** The framework operates on the
instrumentation Nairobi actually has. Every state estimate carries its age; agents
act on the freshest available rather than waiting for completeness.

**P2 — Reproducible by a stranger.** Pinned image digests, lockfiles, versioned
artefacts, recorded seeds — including the study area boundary itself.

**P3 — Measure, do not assume.** Any claim about latency or control performance is
backed by an instrumented number at p50, p95 and p99.

**P4 — Never imply precision the data lacks.** Estimated building heights are
flagged; interpolated depths carry confidence; a guess and a measurement never render
identically.

---

## Open decisions

| Id | Decision | Blocks |
|---|---|---|
| D-1 | TimescaleDB versus native PostgreSQL partitioning | — |
| D-2 | SUMO base image and pinned version | Step 3 |
| D-3 | ISAC emulated, versus reframing as 5G-connected sensing | Step 4 |

D-3 is load-bearing: commercial 5G does not expose integrated sensing and
communication to third parties, so the spec must claim one or the other, plainly.
See `openspec/constitution/project.md`.

---

## Documentation

| Document | Purpose |
|---|---|
| `openspec/constitution/project.md` | Mission, principles, capabilities, conventions, glossary |
| `openspec/constitution/stack.md` | Technology decisions, with rejected alternatives |
| `openspec/constitution/roadmap.md` | Step sequence, dependencies, acceptance criteria, risks |
| `docs/paper/` | Methods paper, drafted as each step completes |