"""Offline tests: a synthetic 1 km x 1 km 'CBD' in UTM 37S, no network access."""

import math

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

from nairobi_dt.study_area import (
    StudyAreaError,
    build_core,
    build_domain,
    corner_between,
    fetch_landmarks,
    select_road,
    validate_core,
)

CRS = "EPSG:32737"
X0, Y0 = 257_000, 9_857_000  # somewhere near central Nairobi in UTM 37S


def roads_fixture(gap_ne: float = 0.0) -> gpd.GeoDataFrame:
    """Square ring: west, north, east, south. The NE corner can be opened by gap_ne."""
    lines = {
        "West Road":  LineString([(X0, Y0 - 200), (X0, Y0 + 1200)]),        # overshoots both ends
        "North Road": LineString([(X0 - 100, Y0 + 1000), (X0 + 1000 - gap_ne, Y0 + 1000)]),
        "East Road":  LineString([(X0 + 1000, Y0 + 1000 - gap_ne), (X0 + 1000, Y0)]),
        "South Road": LineString([(X0 + 1100, Y0), (X0 - 100, Y0)]),
        "Middle Ave": LineString([(X0 + 500, Y0 + 50), (X0 + 500, Y0 + 950)]),
    }
    return gpd.GeoDataFrame({"name": list(lines)}, geometry=list(lines.values()), crs=CRS)


def core_cfg(landmarks: bool = False) -> dict:
    return {
        "boundary_ring": [
            {"key": "w", "names": ["West Road"]},
            {"key": "n", "names": ["North Road"]},
            {"key": "e", "names": ["East Road"]},
            {"key": "s", "names": ["South Road"]},
        ],
        "interior_roads": [{"key": "mid", "names": ["Middle Ave"]}],
        "corner_gap_tolerance_m": 150,
        "min_interior_length_m": 300,
    }


def landmarks_fixture(points: dict) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"key": list(points)}, geometry=list(points.values()), crs=CRS)


def test_closed_ring_gives_square_core():
    core, corners, checks = build_core(roads_fixture(), landmarks_fixture({}), core_cfg())
    assert core.area == pytest.approx(1_000_000, rel=1e-6)
    assert all(c.method == "intersection" for c in corners)
    assert checks["passed"]
    assert checks["interior_length_m"]["mid"] == pytest.approx(900.0)


def test_name_matching_is_case_insensitive():
    geom = select_road(roads_fixture(), ["west road"])
    assert geom.length == pytest.approx(1400)


def test_missing_road_raises():
    with pytest.raises(StudyAreaError):
        select_road(roads_fixture(), ["Nonexistent Street"])


def test_small_gap_uses_midpoint_and_records_distance():
    roads = roads_fixture(gap_ne=60)
    corner = corner_between(select_road(roads, ["North Road"]), select_road(roads, ["East Road"]),
                            "n", "e")
    assert corner.method == "nearest_gap"
    assert corner.gap_m == pytest.approx(84.85, abs=0.1)  # diagonal across a 60 m notch


def test_wide_gap_is_flagged_and_landmark_closes_it():
    roads = roads_fixture(gap_ne=300)
    globe = Point(X0 + 1000, Y0 + 1000)  # the landmark sits where the roads fail to meet
    core, _, checks = build_core(roads, landmarks_fixture({"globe": globe}), core_cfg())
    assert checks["wide_gaps"] == ["n/e"]
    assert checks["landmarks_inside"]["globe"]
    assert core.area == pytest.approx(1_000_000, rel=1e-6)


def test_landmark_outside_ring_expands_core():
    otc = Point(X0 + 1300, Y0 - 100)  # south-east, beyond the ring
    core, _, checks = build_core(roads_fixture(), landmarks_fixture({"otc": otc}), core_cfg())
    assert core.area > 1_000_000
    assert checks["landmarks_inside"]["otc"]


def test_geographic_crs_is_rejected():
    with pytest.raises(StudyAreaError):
        build_core(roads_fixture().to_crs("EPSG:4326"), landmarks_fixture({}), core_cfg())


def test_domain_buffer():
    core, _, _ = build_core(roads_fixture(), landmarks_fixture({}), core_cfg())
    domain = build_domain(core, 1000)
    # square + four side strips + four quarter-circle corners (round join)
    expected = 1000**2 + 4 * 1000 * 1000 + math.pi * 1000**2
    assert domain.area == pytest.approx(expected, rel=1e-3)
    assert domain.contains(core)


def test_interior_road_below_minimum_fails_validation(caplog):
    cfg = core_cfg() | {"min_interior_length_m": 1000}  # Middle Ave has 900 m inside
    with caplog.at_level("WARNING"):
        _, _, checks = build_core(roads_fixture(), landmarks_fixture({}), cfg)
    assert checks["interior_length_m"]["mid"] == pytest.approx(900.0)
    assert not checks["passed"]
    assert "mid" in caplog.text


def test_landmark_outside_core_fails_validation():
    core = box(X0, Y0, X0 + 1000, Y0 + 1000)
    far = landmarks_fixture({"far": Point(X0 + 2000, Y0 + 500)})
    checks = validate_core(core, roads_fixture(), far, core_cfg())
    assert checks["landmarks_inside"] == {"far": False}
    assert not checks["passed"]


def test_collinear_corners_and_landmarks_fail():
    # Two crossing roads give one corner twice; a landmark on the same line keeps it 1-D.
    cfg = core_cfg() | {"boundary_ring": [{"key": "w", "names": ["West Road"]},
                                          {"key": "n", "names": ["North Road"]}]}
    on_line = landmarks_fixture({"lm": Point(X0, Y0 + 500)})
    with pytest.raises(StudyAreaError):
        build_core(roads_fixture(), on_line, cfg)


def test_manual_lonlat_skips_geocoding(monkeypatch):
    import osmnx as ox

    def no_geocoding(query):
        raise AssertionError(f"geocoder called for {query}")

    monkeypatch.setattr(ox, "geocode", no_geocoding)
    cfg = [{"key": "globe", "queries": ["Globe Roundabout, Nairobi, Kenya"],
            "lonlat": [36.821248, -1.278654]}]
    lm = fetch_landmarks(cfg, CRS)
    assert lm["source"].tolist() == ["manual"]
    assert lm.crs.to_epsg() == 32737
    assert lm.to_crs(4326).geometry.iloc[0].x == pytest.approx(36.821248)
