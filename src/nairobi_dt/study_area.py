"""Step 0 — study area construction for the Nairobi digital twin.

Builds three nested spatial tiers:

    core    CBD analysis polygon; every KPI is measured inside it
    domain  core buffered outward; simulated but not scored, to remove edge effects
    county  Nairobi County; the data-ingestion extent

The core is derived rather than hand-drawn: its corners are the intersections of
consecutive boundary roads taken from OpenStreetMap, and its outline is the convex
hull of those corners plus any required landmarks. That makes the study area
reproducible from the configuration alone.

Pure geometry functions take GeoDataFrames and are tested offline. Network access
(OSM, geocoding) is confined to the ``fetch_*`` functions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import geopandas as gpd
import yaml
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

log = logging.getLogger(__name__)

ESRI_STREET_TILES = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                     "World_Street_Map/MapServer/tile/{z}/{y}/{x}")


@dataclass
class CornerDiagnostic:
    road_a: str
    road_b: str
    method: str  # "intersection" or "nearest_gap"
    gap_m: float
    x: float
    y: float


class StudyAreaError(RuntimeError):
    """Raised when the study area cannot be built to specification."""


# --------------------------------------------------------------------------- config

def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------ pure geometry

def select_road(roads: gpd.GeoDataFrame, names: list[str]) -> BaseGeometry:
    """Union of all line features whose OSM name matches any alias (case-insensitive)."""
    wanted = {n.strip().lower() for n in names}
    mask = roads["name"].fillna("").str.strip().str.lower().isin(wanted)
    lines = roads.loc[mask & roads.geom_type.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        raise StudyAreaError(f"No OSM road found for {names}")
    return unary_union(lines.geometry.values)


def corner_between(geom_a: BaseGeometry, geom_b: BaseGeometry,
                   key_a: str, key_b: str) -> CornerDiagnostic:
    """Corner where two boundary roads meet.

    Uses the true intersection when the geometries touch. Otherwise uses the midpoint
    of the shortest connecting segment and records the gap, since named OSM ways
    commonly stop at a roundabout whose ring carries no road name.
    """
    inter = geom_a.intersection(geom_b)
    if not inter.is_empty:
        c = inter.centroid  # dual carriageways yield several points
        return CornerDiagnostic(key_a, key_b, "intersection", 0.0, c.x, c.y)
    pa, pb = nearest_points(geom_a, geom_b)
    mid = Point((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
    return CornerDiagnostic(key_a, key_b, "nearest_gap", pa.distance(pb), mid.x, mid.y)


def build_core(roads: gpd.GeoDataFrame, landmarks: gpd.GeoDataFrame,
               core_cfg: dict) -> tuple[BaseGeometry, list[CornerDiagnostic], dict]:
    """Derive the CBD core polygon. Inputs must already be in a metric CRS."""
    if roads.crs is None or not roads.crs.is_projected:
        raise StudyAreaError("Roads must be in a projected (metric) CRS")

    ring = core_cfg["boundary_ring"]
    geoms = {r["key"]: select_road(roads, r["names"]) for r in ring}

    corners = []
    for i, road in enumerate(ring):
        nxt = ring[(i + 1) % len(ring)]
        corners.append(corner_between(geoms[road["key"]], geoms[nxt["key"]],
                                      road["key"], nxt["key"]))

    tolerance = core_cfg["corner_gap_tolerance_m"]
    wide = [c for c in corners if c.gap_m > tolerance]
    for c in wide:
        log.warning("Corner %s/%s has a %.0f m gap (tolerance %d m); "
                    "check that a landmark closes it", c.road_a, c.road_b, c.gap_m, tolerance)

    points = [Point(c.x, c.y) for c in corners] + list(landmarks.geometry.values)
    core = MultiPoint(points).convex_hull
    if core.geom_type != "Polygon":
        raise StudyAreaError("Corners and landmarks are collinear; the core is not a polygon")

    checks = validate_core(core, roads, landmarks, core_cfg)
    checks["wide_gaps"] = [f"{c.road_a}/{c.road_b}" for c in wide]
    return core, corners, checks


def validate_core(core: BaseGeometry, roads: gpd.GeoDataFrame,
                  landmarks: gpd.GeoDataFrame, core_cfg: dict) -> dict:
    """Confirm that landmarks and the town-centre roads fall inside the core."""
    checks: dict = {"landmarks_inside": {}, "interior_length_m": {}}
    for key, geom in zip(landmarks["key"], landmarks.geometry, strict=True):
        checks["landmarks_inside"][key] = bool(core.buffer(1.0).contains(geom))

    # Length inside the core, not share of total length: town-centre roads such as
    # Kenyatta Avenue legitimately continue beyond the CBD.
    minimum = core_cfg["min_interior_length_m"]
    for road in core_cfg.get("interior_roads", []):
        inside = select_road(roads, road["names"]).intersection(core).length
        checks["interior_length_m"][road["key"]] = round(inside, 1)
        if inside < minimum:
            log.warning("Only %.0f m of %s lies inside the core (minimum %.0f m)",
                        inside, road["key"], minimum)

    checks["passed"] = (all(checks["landmarks_inside"].values())
                        and all(v >= minimum for v in checks["interior_length_m"].values()))
    return checks


def build_domain(core: BaseGeometry, buffer_m: float) -> BaseGeometry:
    return core.buffer(buffer_m, join_style="round")


# ------------------------------------------------------------------ network access

def fetch_roads(bbox: list[float], crs_out: str) -> gpd.GeoDataFrame:
    import osmnx as ox

    west, south, east, north = bbox
    feats = ox.features_from_bbox(bbox=(west, south, east, north), tags={"highway": True})
    feats = feats.reset_index()
    if "name" not in feats.columns:
        feats["name"] = None
    lines = feats.loc[feats.geom_type.isin(["LineString", "MultiLineString"]),
                      ["name", "highway", "geometry"]]
    return lines.to_crs(crs_out)


def fetch_landmarks(landmark_cfg: list[dict], crs_out: str) -> gpd.GeoDataFrame:
    import osmnx as ox

    rows = []
    for lm in landmark_cfg:
        if lm.get("lonlat"):
            lon, lat = lm["lonlat"]
            source = "manual"
        else:
            lon = lat = None
            for query in lm["queries"]:
                try:
                    lat, lon = ox.geocode(query)
                    source = f"geocoded: {query}"
                    break
                except Exception:  # noqa: BLE001 — geocoder raises several types
                    continue
            if lat is None:
                raise StudyAreaError(
                    f"Could not geocode landmark '{lm['key']}'. Set its lonlat in "
                    "config/study_area.yaml after locating it on the map.")
        rows.append({"key": lm["key"], "source": source, "geometry": Point(lon, lat)})
    return gpd.GeoDataFrame(rows, crs="EPSG:4326").to_crs(crs_out)


def fetch_county(query: str, crs_out: str) -> gpd.GeoDataFrame:
    import osmnx as ox

    return ox.geocode_to_gdf(query)[["geometry"]].to_crs(crs_out)


# ------------------------------------------------------------------ orchestration

def run(config_path: str | Path, project_root: str | Path = ".") -> dict:
    cfg = load_config(config_path)
    crs_a, crs_g = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
    out_dir = Path(project_root) / cfg["outputs"]["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    roads = fetch_roads(cfg["seed_bbox"], crs_a)
    landmarks = fetch_landmarks(cfg["core"]["landmarks"], crs_a)
    county = fetch_county(cfg["county"]["query"], crs_a)

    core, corners, checks = build_core(roads, landmarks, cfg["core"])
    domain = build_domain(core, cfg["domain"]["buffer_m"])

    tiers = gpd.GeoDataFrame(
        {"tier": ["core", "domain", "county"],
         "area_km2": [core.area / 1e6, domain.area / 1e6, county.geometry.iloc[0].area / 1e6]},
        geometry=[core, domain, county.geometry.iloc[0]], crs=crs_a)

    corner_gdf = gpd.GeoDataFrame(
        [asdict(c) for c in corners],
        geometry=[Point(c.x, c.y) for c in corners], crs=crs_a)

    gpkg = out_dir / cfg["outputs"]["geopackage"]
    tiers.to_crs(crs_g).to_file(gpkg, layer="tiers", driver="GPKG")
    corner_gdf.to_crs(crs_g).to_file(gpkg, layer="corners", driver="GPKG")
    landmarks.to_crs(crs_g).to_file(gpkg, layer="landmarks", driver="GPKG")

    report = {
        "crs": cfg["crs"],
        "area_km2": {t: round(a, 3)
                     for t, a in zip(tiers["tier"], tiers["area_km2"], strict=True)},
        "corners": [asdict(c) | {"gap_m": round(c.gap_m, 1)} for c in corners],
        "checks": checks,
    }
    (out_dir / cfg["outputs"]["report"]).write_text(json.dumps(report, indent=2))
    write_map(tiers.to_crs(crs_g), corner_gdf.to_crs(crs_g), landmarks.to_crs(crs_g),
              out_dir / cfg["outputs"]["map"])

    if not checks["passed"]:
        log.error("Study area built but failed validation — inspect %s before continuing",
                  out_dir / cfg["outputs"]["map"])
    return report


def write_map(tiers: gpd.GeoDataFrame, corners: gpd.GeoDataFrame,
              landmarks: gpd.GeoDataFrame, path: Path) -> None:
    """Interactive map for the visual check. Skipped if folium is not installed."""
    try:
        import folium
    except ImportError:
        log.info("folium not installed; skipping map")
        return

    centre = tiers.loc[tiers["tier"] == "core"].geometry.iloc[0].centroid
    m = folium.Map(location=[centre.y, centre.x], zoom_start=15, tiles=ESRI_STREET_TILES,
                   attr="Tiles &copy; Esri")
    style = {"core": "#d7301f", "domain": "#fc8d59", "county": "#636363"}
    for tier in ["county", "domain", "core"]:
        row = tiers.loc[tiers["tier"] == tier]
        folium.GeoJson(row, name=tier, style_function=lambda _, c=style[tier]: {
            "color": c, "weight": 2, "fillOpacity": 0.08}).add_to(m)
    for _, r in corners.iterrows():
        tip = f"{r.road_a}/{r.road_b} ({r.method}, {r.gap_m:.0f} m)"
        folium.CircleMarker([r.geometry.y, r.geometry.x], radius=5, color="black",
                            tooltip=tip).add_to(m)
    for _, r in landmarks.iterrows():
        folium.Marker([r.geometry.y, r.geometry.x], tooltip=f"{r.key} ({r.source})").add_to(m)
    folium.LayerControl().add_to(m)
    m.save(str(path))
