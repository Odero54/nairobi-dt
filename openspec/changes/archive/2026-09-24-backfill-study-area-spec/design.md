## Context

See proposal.md, Why. The code is `src/nairobi_dt/study_area.py`, driven by
`scripts/00_build_study_area.py` and `config/study_area.yaml`. The notebook
`notebooks/00-study-area-exploration.ipynb` re-derives the shipped result offline
from the osmnx cache in `cache/` and matches the committed report exactly.

## Goals / Non-Goals

**Goals:** a spec that the existing code already satisfies, with every scenario
backed by an offline test; the constitution's ruff and mypy gates passing.

**Non-goals:** any change to the derived core, to thresholds, or to the build's
outputs.

## Decisions

**D1 — Spec describes shipped behaviour, including its weak points.** The
interior-road check sums every same-named way, which makes it weak (notebook §4).
The spec states that plainly rather than describing a stronger check that does not
exist. *Alternative:* tighten it now to main carriageways. Rejected because it is a
behaviour change and belongs in its own proposal. Serves P4: the spec does not imply
precision the check lacks.

**D2 — Pin landmark coordinates.** Both landmarks are currently geocoded
(`OTC, Nairobi, Kenya` and `Globe Roundabout, Nairobi, Kenya`). A geocoder can return a
different point tomorrow, which would silently move the core. Copy the currently
geocoded coordinates into `lonlat`, keeping `queries` as documentation of how they
were found. *Alternative:* rely on the osmnx cache. Rejected: `cache/` is not
committed and a stranger has no cache. Serves P2.

**D3 — Remove the `core_override.geojson` claim from the README.** Nothing reads the
file, and the boundary will not be hand-edited in QGIS: the derived core is final
(confirmed by the project owner, 2026-09-24). *Alternative:* implement the override. Rejected for this change because it is
new behaviour; if a hand-refined boundary is ever needed, it gets its own proposal and
requirement.

**D4 — Test the build end to end offline by replacing the `fetch_*` stage.** Output
CRS, GeoPackage layers and the non-zero exit on failed validation are currently
untested. Substituting synthetic roads, landmarks and county for the network calls
exercises the whole build in milliseconds, consistent with the offline-tests rule.

**D5 — Lint fixes are mechanical.** `zip(..., strict=True)` where lengths must match
(landmark keys and geometries; tier names and areas), wrapped long lines, sorted
imports, and a 4-tuple type for the OSM bounding box. No behaviour change; the
existing tests and the notebook's agreement with the report confirm it.

## Risks / Trade-offs

- [Pinned landmarks drift from reality if OSM is corrected] → The notebook
  re-geocodes when `REFRESH_OSM = True`; a discrepancy is reason for a change.
- [The Kirinyaga Road / Ring Road gap is 131 m against a 150 m tolerance] → An OSM
  edit could flag it. Flagging is a warning, not a failure, and the report records it.
- [The spec cites a notebook as evidence] → The notebook is executed under
  `pytest --nbmake`, so the evidence cannot silently rot. It depends on `cache/` or
  network access; that dependency is stated in its header.
