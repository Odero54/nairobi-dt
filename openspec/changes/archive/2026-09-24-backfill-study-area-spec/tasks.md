## 1. Lab record

- [x] 1.1 Write `notebooks/00-study-area-exploration.ipynb` (paired `.py`), re-deriving the shipped study area offline
- [x] 1.2 Confirm the notebook reproduces the committed report's tier areas and executes under `pytest --nbmake`

## 2. Reproducibility and documentation fixes

- [x] 2.1 Pin `lonlat` for `globe_roundabout` and `otc` in `config/study_area.yaml` to the currently geocoded coordinates (D2)
- [x] 2.2 Rebuild the study area and confirm the tier areas are unchanged
- [x] 2.3 Remove the `config/core_override.geojson` instructions from `README.md` (D3)
- [x] 2.4 Correct the Step 0 test count in `openspec/constitution/roadmap.md`
- [x] 2.5 Record pyarrow (dev extra, for notebook maps and GeoParquet) in `openspec/constitution/stack.md`

## 3. Code quality gates

- [x] 3.1 Fix the ruff findings in `src/nairobi_dt/study_area.py` and `tests/test_study_area.py` (D5)
- [x] 3.2 Fix the mypy finding on the OSM bounding-box type (D5)
- [x] 3.3 Confirm `ruff check`, `mypy` and the existing tests pass unchanged

## 4. Tests for untested scenarios

- [x] 4.1 Test: interior road with less than the minimum inside the core fails validation
- [x] 4.2 Test: landmark outside the core fails validation
- [x] 4.3 Test: collinear corners and landmarks fail with a study-area error
- [x] 4.4 Test: a landmark with `lonlat` is placed without geocoding and has source `manual`
- [x] 4.5 Test: end-to-end build with `fetch_*` replaced by fixtures writes the three GeoPackage layers in EPSG:4326, the report, and records `passed` (D4)
- [x] 4.6 Test: the entry point exits non-zero when validation fails, and outputs are still written

## 5. Close out

- [x] 5.1 Record the notebook command (`uv run pytest --nbmake notebooks/`) in `README.md`; CI is a separate change
- [x] 5.2 Confirm paper §2.1 matches the spec (ring roads, 300 m rule, landmarks)
- [x] 5.3 `openspec validate backfill-study-area-spec --strict`
- [x] 5.4 Archive the change, creating `openspec/specs/study-area/spec.md`
