# Project

Conventions and constraints for this repository. Read before proposing a change.
`stack.md` records technology decisions; `roadmap.md` records sequence. This file
records what the project is for and the rules every change obeys.

---

## Mission

Build an autonomous digital twin of Nairobi that **perceives concurrent urban traffic
congestion and flash flooding, and acts on both** — coordinating signal control and
rerouting through multi-agent negotiation over deployed 5G infrastructure, with a
browser-based 3D interface for municipal decision support.

The research gap this closes: flood-coupled multi-agent signal control has been
demonstrated without a network sensing layer or 3D representation; city-scale flood
twins assess cascading disruption without acting on it; ISAC-driven twins for the
Global South operate at national scale with perception but no control loop. No prior
work closes the loop at municipal scale under sparse instrumentation.

**Scope.** Nairobi County as the ingestion extent; the CBD as the analysis core,
bounded by Uhuru Highway, University Way, Kirinyaga Road from the Globe Roundabout,
Ring Road and Haile Selassie Avenue, extending to the OTC terminus.

**Non-goals.** Not a production municipal system; not a general-purpose twin
platform; not a traffic forecasting service. Generalisation beyond Nairobi is argued
in the discussion, not engineered.

---

## Principles

**P1 — Sparsity is a design condition, not a limitation.**
The framework operates on the instrumentation Nairobi actually has. Every reviewed
flood twin presumes sensor, LiDAR or telemetry densities unavailable here. Missing
and intermittent observations are the normal case: every state estimate carries its
age, and agents act on the freshest estimate available rather than waiting for
completeness. No design may assume a data source that does not exist locally.

**P2 — Reproducible by a stranger.**
Every published result regenerates from the repository: pinned image digests,
lockfiles, versioned input artefacts, and a recorded seed. This includes the study
area boundary itself, which is derived from configuration rather than hand-drawn.
A result that exists only in someone's notebook or someone's laptop is not a result.

**P3 — Measure, do not assume.**
Latency classes and KPIs are measurement apparatus, not aspirations. Any claim about
timing, throughput or control performance is backed by an instrumented number at
p50, p95 and p99. Observability is part of the method, not operations garnish.

**P4 — Never imply precision the data lacks.**
Estimated building heights are flagged as estimates. Interpolated flood depths carry
confidence. Visualisation must not render a guess and a measurement identically.
Where uncertainty exists, it is shown, not smoothed.

---

## Architecture in one paragraph

The twin's **interface** is fully web-based; its **engine** is not. SUMO, the
hydrodynamics and the agent logic cannot run in a browser, so a TypeScript and
CesiumJS client renders state streamed from Python services that run the simulation.
This split is standard in the reviewed literature and keeps the offline geospatial
pipeline intact. The API follows ports and adapters (`stack.md` §3); the UI follows
responsive, tiered design (§7); everything runs in containers (§10).

---

## Capabilities

Specs live in `openspec/specs/<capability>/spec.md`. One capability, one bounded
behaviour.

| Capability | Owns |
|---|---|
| `study-area` | Spatial tiers, CRS policy, boundary derivation and validation |
| `geospatial-foundation` | Terrain, 3D buildings, road network, imagery artefacts |
| `flood-hazard` | Inundation surfaces, depth-to-link mapping, impassability |
| `traffic-simulation` | SUMO network, demand calibration, control baselines |
| `realtime-ingestion` | MQTT and Kafka paths, 5G measurement, schema validation |
| `spatiotemporal-fusion` | H3 alignment, the Zarr cube, age of information |
| `multi-agent-control` | Contract-net negotiation, signal plans, rerouting, pre-emption |
| `api-gateway` | Ports, use cases, REST and WebSocket adapters |
| `web-twin` | Cesium scene, live overlays, responsive tiers |
| `analytics-views` | deck.gl layers, KPI exploration |
| `evaluation` | Scenario definitions, KPI computation, baseline comparison |

---

## Conventions

### Change workflow

0. **Explore** in `notebooks/NN-topic.ipynb`, numbered by the step. Inspect the
   data, try the method, and record what worked and what did not. No `src/` code
   yet.
1. `/opsx:propose <change-id>` — proposal, spec deltas, design, tasks. The proposal
   cites the notebooks that informed it; thresholds and rules found there become
   requirements.
2. Review and approve **before** implementation. This gate is the point of the tool.
3. `/opsx:apply` — implement, ticking tasks as they complete. Findings graduate from
   the notebooks into `src/nairobi_dt/` with tests (`stack.md` §1).
4. `/opsx:archive` — merge deltas into `specs/`, archive the change.

Change ids are `verb-noun`, kebab-case: `add-flood-hazard-layer`,
`update-latency-budget`. One change, one capability, wherever possible.

The `study-area` capability (Step 0) was built before this workflow was adopted.
Its notebook and spec are backfilled from the shipped code rather than the reverse.

### Requirements

Requirements use SHALL or MUST and carry at least one scenario in WHEN/THEN form.
A requirement without a scenario is a wish. Requirements that encode research
decisions — thresholds, formulas, staleness limits — state the value and cite its
source, because the methodology chapter quotes them.

### Code

- Python: `src/nairobi_dt/`, typed, `ruff` and `mypy` clean. Network access confined
  to `fetch_*` functions so tests run offline.
- TypeScript: strict mode, no `any`. Types generated from `contracts/`.
- Domain logic imports no framework. Adapters map contract models at the boundary.
- Configuration lives in `config/*.yaml`, never in code. Thresholds are configuration
  because examiners ask what happens when they change.

### Tests

- Domain and pipeline tests: no network, no containers, milliseconds.
- Adapter tests: real services via testcontainers, marked `integration`.
- Web: Vitest units, Playwright across the viewport matrix.
- Notebooks cited in the paper: executed in CI via `pytest --nbmake`.
- A change is not done until its tests exist and pass.

### Definition of done

A change is complete when: its exploratory notebooks exist and re-execute; tasks are
ticked; tests pass; the spec delta validates
under `openspec validate --strict`; the matching section of `docs/paper/` is drafted;
any new dependency is recorded in `stack.md`; and its container starts healthy in
`docker compose up`.

---

## Open decisions

Tracked here until resolved by a change that amends the relevant file.

| Id | Decision | Status |
|---|---|---|
| **D-1** | TimescaleDB (source-available) versus native PostgreSQL partitioning | Open — Timescale retained; schema designed so the swap is a migration |
| **D-2** | SUMO base image and pinned version | Open — must resolve before `add-traffic-simulation` |
| **D-3** | ISAC emulated, versus reframing the layer as 5G-connected sensing | **Open and load-bearing** — see below |

**On D-3.** Commercial 5G does not expose integrated sensing and communication to
third parties: the radio-sensing function lives inside the operator's base stations
and is not standardised for external use. The framework can measure real 5G transport
latency and throughput while *emulating* the ISAC sensing function, or it can reframe
the layer as 5G-connected sensing with ISAC as the stated upgrade path. Both are
defensible. Claiming operational ISAC over a commercial network is not. This must be
settled before `add-realtime-ingestion`, because it determines what the spec asserts.

---

## Glossary

| Term | Meaning here |
|---|---|
| **Core** | The CBD analysis polygon. All KPIs are computed inside it |
| **Domain** (spatial) | Core plus 1 km buffer; simulated but never scored |
| **Link** | A directed road segment in the SUMO network |
| **Impassable** | Inundation depth above the configured threshold (0.30 m) |
| **Age of information** | Elapsed time between observation and the decision using it |
| **L1–L5** | Latency classes, from actuation to batch (`config/latency_budget.yaml`) |
| **Scenario run** | One simulation execution: scenario id, seed, control strategy, artefact versions |
| **Domain** (code) | The framework-free core of the API service. Disambiguate by context |