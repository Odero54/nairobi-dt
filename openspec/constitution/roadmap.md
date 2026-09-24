# Roadmap

Each step begins with experiments in `notebooks/NN-*.ipynb` and then becomes one
OpenSpec change. A step ships its notebooks, working code, a validated spec, and the
matching section of the methods paper — they move together or the step is not done
(`project.md`, Change workflow and Definition of done).

Status: ✅ complete · 🔨 in progress · ⬜ not started

---

## Sequence

| # | Change id | Capability | Status |
|---|---|---|---|
| 0 | — (baseline) | `study-area` | ✅ |
| 1 | `add-geospatial-foundation` | `geospatial-foundation` | ⬜ |
| 2 | `add-flood-hazard-layer` | `flood-hazard` | ⬜ |
| 3 | `add-traffic-simulation` | `traffic-simulation` | ⬜ |
| 4 | `add-realtime-ingestion` | `realtime-ingestion` | ⬜ |
| 5 | `add-spatiotemporal-fusion` | `spatiotemporal-fusion` | ⬜ |
| 6 | `add-multi-agent-control` | `multi-agent-control` | ⬜ |
| 7 | `add-api-gateway` | `api-gateway` | ⬜ |
| 8 | `add-web-twin-viewer` | `web-twin` | ⬜ |
| 9 | `add-analytics-views` | `analytics-views` | ⬜ |
| 10 | `add-evaluation-harness` | `evaluation` | ⬜ |

## Dependencies

```
0 study-area
└── 1 geospatial-foundation
    ├── 2 flood-hazard ──────┐
    └── 3 traffic-simulation ┤
                             ├── 5 spatiotemporal-fusion ── 6 multi-agent-control
        4 realtime-ingestion ┘                                      │
                                                 7 api-gateway ─────┤
                                                    ├── 8 web-twin ─┤
                                                    └── 9 analytics ┤
                                                                    └── 10 evaluation
```

Steps 2, 3 and 4 are independent of one another and can proceed in parallel.
Step 7 needs only contracts and can start alongside 5 or 6.

---

## Step 0 — Study area ✅

**Capability:** `study-area` · **Paper:** §2

Three nested spatial tiers derived from OpenStreetMap rather than hand-drawn: the CBD
core, a 1 km simulation buffer, and Nairobi County. Plus the CRS policy, the latency
budget (L1–L5) and the KPI register.

Shipped: `config/study_area.yaml`, `config/latency_budget.yaml`, `config/kpis.yaml`,
`src/nairobi_dt/study_area.py`, 18 offline tests, GeoPackage and validation report.

Built before OpenSpec was adopted; notebook and spec backfilled by change
`backfill-study-area-spec`: `notebooks/00-study-area-exploration.ipynb`,
`openspec/specs/study-area/spec.md`.

---

## Step 1 — Geospatial foundation ⬜

**Change:** `add-geospatial-foundation` · **Paper:** §3.1 · **Depends on:** 0

Build the static artefacts everything else renders or simulates on.

- GLO-30 terrain → quantized-mesh tileset
- OSM buildings → 3D Tiles, **with estimated heights flagged per building** (P4)
- OSM roads → SUMO `.net.xml` via `netconvert`, plus GeoJSON for rendering
- Sentinel-2 cloud-free composite → COG in MinIO, served by TiTiler

**Done when:** every artefact is versioned and content-addressed in MinIO; the
terrain tileset loads in a bare Cesium page; `netconvert` produces a network with no
disconnected core links; building height provenance is queryable.

**Watch:** OSM building height coverage in Nairobi is thin. The estimation rule and
its flag are a spec requirement, not an implementation detail.

---

## Step 2 — Flood hazard layer ⬜

**Change:** `add-flood-hazard-layer` · **Paper:** §3.2 · **Depends on:** 1

Inundation surfaces and their mapping onto road links.

- Historical extents from Sentinel-1 SAR via Earth Engine
- Rainfall from CHIRPS and GPM IMERG
- Modelled depth grids per scenario and timestep → COG
- Depth-to-link mapping; impassability at the configured threshold

**Done when:** a scenario yields per-link depth time series; impassable links are
derivable for any timestep; every depth carries a source and confidence (P4).

**Design decision to record:** inundation is **precomputed per scenario**, not solved
in the control loop. This meets the L3 budget (≤ 60 s) without running hydrodynamics
online, and makes runs deterministic and replayable (P2).

---

## Step 3 — Traffic simulation ⬜

**Change:** `add-traffic-simulation` · **Paper:** §3.3 · **Depends on:** 1 · **Blocked by:** D-2

SUMO scenario for the core plus buffer, with demand calibrated against observed CBD
volumes, and the fixed-time and actuated baselines.

**Done when:** a scenario runs headless via libsumo; calibration error against
observed counts is reported; fixed-time and actuated baselines produce stable KPIs
across seeds; the SUMO version is recorded in every `scenario_run`.

**Watch:** matatu and boda-boda behaviour is not SUMO's default vehicle mix.
Parameterising it is part of this step, and a limitation to state plainly if the
calibration data will not support it.

---

## Step 4 — Real-time ingestion ⬜

**Change:** `add-realtime-ingestion` · **Paper:** §3.4 · **Depends on:** contracts · **Blocked by:** D-3

The sensing and transport layer: MQTT from devices, bridged to Kafka, schema-validated,
with measured 5G latency and throughput at p50, p95 and p99 against the budget.

**Done when:** the L1, L2 and L3 classes have measured end-to-end figures; message
loss and age of information are instrumented; replay from the event log reproduces a
run exactly (P2, P3).

**D-3 must be settled first.** Whether the spec asserts emulated ISAC or 5G-connected
sensing changes what this change claims. Do not start until it is decided.

---

## Step 5 — Spatiotemporal fusion ⬜

**Change:** `add-spatiotemporal-fusion` · **Paper:** §3.5 · **Depends on:** 2, 3, 4

Align flood and traffic state onto a shared H3 grid and a Zarr space-time cube, with
age of information carried through every estimate.

**Done when:** the cube is queryable by time and cell; per-link derived state lands
in Postgres for agents; staleness is explicit everywhere; gaps degrade gracefully
rather than failing (P1).

---

## Step 6 — Multi-agent control ⬜

**Change:** `add-multi-agent-control` · **Paper:** §3.6 · **Depends on:** 5

Contract-net agents negotiating signal plans, rerouting around impassable links, and
emergency vehicle pre-emption.

**Done when:** agents act within the L1 budget (≤ 250 ms end to end); rerouting
avoids impassable links measurably; pre-emption is granted without collapsing general
throughput; behaviour is deterministic under a fixed seed.

**Watch:** this is where the thesis contribution lives. Negotiation rules and their
rationale belong in `domain/rules.py` and in the paper, not buried in agent code.

---

## Step 7 — API gateway ⬜

**Change:** `add-api-gateway` · **Paper:** §3.7 · **Depends on:** contracts

Ports and adapters (`stack.md` §3): domain, use cases, REST and WebSocket adapters,
Redis and Postgres repositories, the libsumo runner, and the composition root.

**Done when:** domain tests run with no containers; adapter tests pass against real
services; OpenAPI generates the TypeScript client; the L1 path touches Redis only.

---

## Step 8 — Web twin viewer ⬜

**Change:** `add-web-twin-viewer` · **Paper:** §3.7 · **Depends on:** 1, 7

CesiumJS scene with terrain, buildings, live link state and the time-dynamic flood
overlay, across the three responsive tiers (`stack.md` §7).

**Done when:** live state updates within the L4 budget (≤ 2 s); the field tier loads
without Cesium; flood depth is encoded by ramp **and** pattern with text values (P4,
accessibility); Playwright passes the viewport matrix.

---

## Step 9 — Analytics views ⬜

**Change:** `add-analytics-views` · **Paper:** §4 · **Depends on:** 7, 10

deck.gl over MapLibre: H3 fusion cells, animated trajectories, OD flow shifts, queue
hotspots — the figures the results chapter needs.

**Done when:** each layer renders from scenario artefacts rather than live state;
`prefers-reduced-motion` replaces animation with a time slider; figures export at
publication resolution.

---

## Step 10 — Evaluation harness ⬜

**Change:** `add-evaluation-harness` · **Paper:** §4 · **Depends on:** 6

Scenario definitions, KPI computation over immutable GeoParquet with DuckDB, and
baseline comparison across fixed-time, actuated and multi-agent control.

Scenarios: normal peak; flood with no response; flood with fixed-time; flood with
multi-agent; each with and without an emergency vehicle overlay.

**Done when:** every KPI in `config/kpis.yaml` computes for every scenario and
baseline; results carry seeds and artefact versions; the full table regenerates from
artefacts with one command (P2).

---

## Milestones

| Milestone | Steps | Output |
|---|---|---|
| **M1 — Data foundation** | 1, 2, 3 | Artefacts and a calibrated simulation; paper §3.1–3.3 |
| **M2 — Closed loop** | 4, 5, 6 | Sensing through actuation; paper §3.4–3.6; the contribution is demonstrable |
| **M3 — Twin interface** | 7, 8, 9 | Browser twin and analytics; paper §3.7 |
| **M4 — Evidence** | 10 | KPI tables, baseline comparison; paper §4 |

The **literature and gap paper** is submittable now, before M1 — it depends on the
review, not the implementation, and establishes the claim to the gap before anyone
else fills it. The **methods paper** follows M2. The **results paper** follows M4.

---

## Risk register

| Risk | Step | Mitigation |
|---|---|---|
| D-3 unresolved stalls the sensing layer | 4 | Decide before starting; both options are defensible if stated plainly |
| Calibration data too thin for matatu behaviour | 3 | Parameterise what data supports; state the rest as a limitation |
| Sentinel-1 revisit misses flood peaks | 2 | Precomputed scenarios do not depend on capturing a specific event |
| L1 budget missed under load | 6 | Redis-only path; measure early, at Step 4, not at Step 6 |
| Cesium bundle blows the field-tier budget | 8 | Code-split from the start; budget enforced in CI |
| Scope creep into a production system | all | Non-goals in `project.md`; a thesis, not a platform |