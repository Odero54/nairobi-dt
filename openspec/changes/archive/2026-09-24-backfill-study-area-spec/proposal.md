## Why

Step 0 (`study-area`) shipped before the project adopted OpenSpec, so the capability
every later step builds on has no spec: its thresholds, failure modes and outputs are
defined only by the code. Step 1 (`add-geospatial-foundation`) clips to these tiers,
so their contract must be written down before that change is proposed.

This is a backfill. It records the behaviour that shipped; it does not change it.

**Informed by:** `notebooks/00-study-area-exploration.ipynb`, which re-derives the
shipped result offline from the osmnx cache and reproduces the report exactly
(core 1.763 km², domain 10.232 km², county 695.937 km²).

## What Changes

- Add the `study-area` spec, describing the shipped behaviour: three nested tiers,
  the CRS policy, boundary derivation from configured roads and landmarks, corner-gap
  handling, validation, and the build outputs.
- Remove the README's claim that `config/core_override.geojson` overrides the core.
  No code reads it; the spec cannot promise it.
- Pin the two landmark coordinates in `config/study_area.yaml` to the values
  currently geocoded, so the boundary no longer depends on a live geocoder (P2).
  The derived core is unchanged.
- Close the gaps the backfill exposed: ruff and mypy findings in
  `study_area.py` and its tests, the roadmap's test count (it says 13; 12 exist), and
  tests for spec scenarios that have none yet.

## Capabilities

### New Capabilities

- `study-area`: spatial tiers, CRS policy, boundary derivation and validation, as
  defined in `openspec/constitution/project.md`.

### Modified Capabilities

None.

## Non-goals

- **Latency budget and KPI register.** Step 0 also shipped `config/latency_budget.yaml`
  and `config/kpis.yaml`, guarded by `tests/test_requirements.py`. They are not
  study-area behaviour and are left for the capabilities that consume them
  (`realtime-ingestion`, `evaluation`).
- **Tightening the interior-road check.** The notebook shows it measures the length of
  every way sharing a name, so it passes by an order of magnitude. That is a
  behaviour change for a later proposal, not a backfill.

## Impact

- `openspec/specs/study-area/spec.md` created on archive.
- `config/study_area.yaml`: landmark `lonlat` values filled in.
- `src/nairobi_dt/study_area.py`: lint and typing fixes only; no behaviour change.
- `tests/`: new tests for untested scenarios.
- `README.md`, `openspec/constitution/roadmap.md`: corrections.
- No new dependencies. (pyarrow was added to the dev extras for the notebook before
  this change; `stack.md` should record it.)
