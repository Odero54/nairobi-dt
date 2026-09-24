"""Offline end-to-end build: the fetch_* stage is replaced by synthetic inputs."""

import json
import runpy
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
import yaml
from shapely.geometry import Point, box
from test_study_area import CRS, X0, Y0, core_cfg, roads_fixture

from nairobi_dt import study_area as sa

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "00_build_study_area.py"


def write_config(tmp_path: Path, min_interior_m: float = 300) -> Path:
    cfg = {
        "crs": {"geographic": "EPSG:4326", "analysis": CRS, "web": "EPSG:3857"},
        "seed_bbox": [36.8, -1.3, 36.84, -1.27],
        "core": core_cfg() | {
            "min_interior_length_m": min_interior_m,
            "landmarks": [{"key": "centre", "queries": [], "lonlat": None}],
        },
        "domain": {"buffer_m": 1000},
        "county": {"query": "Synthetic County"},
        "outputs": {"dir": "out", "geopackage": "sa.gpkg", "map": "map.html",
                    "report": "report.json"},
    }
    path = tmp_path / "study_area.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.fixture
def offline(monkeypatch):
    """Replace every network call with synthetic data in the analysis CRS."""
    def landmarks(cfg, crs_out):
        return gpd.GeoDataFrame({"key": ["centre"], "source": ["manual"]},
                                geometry=[Point(X0 + 500, Y0 + 500)], crs=CRS).to_crs(crs_out)

    def county(query, crs_out):
        return gpd.GeoDataFrame(geometry=[box(X0 - 5000, Y0 - 5000, X0 + 6000, Y0 + 6000)],
                                crs=CRS).to_crs(crs_out)

    monkeypatch.setattr(sa, "fetch_roads", lambda bbox, crs_out: roads_fixture().to_crs(crs_out))
    monkeypatch.setattr(sa, "fetch_landmarks", landmarks)
    monkeypatch.setattr(sa, "fetch_county", county)


def test_build_writes_outputs_in_geographic_crs(tmp_path, offline):
    report = sa.run(write_config(tmp_path), tmp_path)

    out = tmp_path / "out"
    gpkg = out / "sa.gpkg"
    assert set(pyogrio.list_layers(gpkg)[:, 0]) == {"tiers", "corners", "landmarks"}
    for layer in ("tiers", "corners", "landmarks"):
        assert gpd.read_file(gpkg, layer=layer).crs.to_epsg() == 4326, layer
    assert (out / "map.html").exists()

    saved = json.loads((out / "report.json").read_text())
    assert saved == report
    assert saved["crs"] == {"geographic": "EPSG:4326", "analysis": CRS, "web": "EPSG:3857"}
    assert saved["area_km2"]["core"] == pytest.approx(1.0, abs=1e-3)
    assert {c["method"] for c in saved["corners"]} == {"intersection"}
    assert saved["checks"]["passed"] is True


def test_entry_point_exits_nonzero_and_still_writes_outputs(tmp_path, offline, monkeypatch):
    config = write_config(tmp_path, min_interior_m=1000)  # Middle Ave has only 900 m inside
    real_run = sa.run
    monkeypatch.setattr(sa, "run", lambda *_: real_run(config, tmp_path))

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exit_info.value.code == 1
    out = tmp_path / "out"
    assert (out / "sa.gpkg").exists() and (out / "map.html").exists()
    assert json.loads((out / "report.json").read_text())["checks"]["passed"] is False
