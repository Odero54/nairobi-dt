# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 01 — Traffic signals from Mapillary
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# `01-road-network` found no OSM traffic signals in the core, and 18 signalised
# junctions there when netconvert guesses from geometry. The control layer (Step 6)
# acts on signals, so the inventory needs evidence. Mapillary runs object detection
# over street-level imagery and publishes the detections as map features, among
# them traffic lights.
#
# Questions:
#
# 1. How well has Mapillary photographed the core, and how recently?
# 2. What traffic lights has it detected, and when were they last seen?
# 3. Which junctions do the detections point to, and how do they compare with
#    netconvert's guesses?
#
# **Inputs.** Mapillary API v4, queried in 0.005° cells because large bounding boxes
# return HTTP 500. Detections and image points are pinned in `data/raw/`; set
# `REFRESH_MLY = True` to re-query, which needs `MAPILLARY_TOKEN` in the
# environment or `.env`. Mapillary data is CC BY-SA 4.0.
#
# The SUMO networks come from `01-road-network` (`cache/sumo/`); run it first.

# %% tags=["parameters"]
REFRESH_MLY = False
CONFIG = "config/study_area.yaml"
CELL_DEG = 0.005
MATCH_M = 40  # a detection within this distance of a junction is evidence for it
RECENT_YEAR = 2023

# %%
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import sumolib

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

cfg = sa.load_config(CONFIG)
CRS_A, CRS_G = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers = gpd.read_file(gpkg, layer="tiers").to_crs(CRS_A).set_index("tier").geometry
core, domain = tiers["core"], tiers["domain"]

# %% [markdown]
# ## Inputs

# %%
TL_VALUES = [f"object--traffic-light--{v}" for v in
             ("general-upright", "general-horizontal", "general-single",
              "pedestrians", "cyclists", "other")]
tl_path = ROOT / "data" / "raw" / "mapillary_domain_traffic_lights.geojson"
img_path = ROOT / "data" / "raw" / "mapillary_domain_images.parquet"


def mapillary_token() -> str:
    if tok := os.environ.get("MAPILLARY_TOKEN"):
        return tok
    env = (ROOT / ".env").read_text() if (ROOT / ".env").exists() else ""
    m = re.search(r"^MAPILLARY_TOKEN=(.+)$", env, flags=re.M)
    if not m:
        raise RuntimeError("MAPILLARY_TOKEN is not set in the environment or .env")
    return m.group(1).strip()


def fetch_mapillary(bounds: tuple[float, float, float, float]) -> tuple[list, list]:
    token = mapillary_token()

    def get(path: str, **q: object) -> list:
        url = f"https://graph.mapillary.com/{path}?" + urllib.parse.urlencode(
            {**q, "access_token": token})
        for attempt in range(3):
            try:
                return json.load(urllib.request.urlopen(url, timeout=120))["data"]
            except urllib.error.HTTPError:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Mapillary query failed: {path} {q.get('bbox')}")

    w, s, e, n = bounds
    feats, imgs = {}, {}
    for x in np.arange(w, e, CELL_DEG):
        for y in np.arange(s, n, CELL_DEG):
            bbox = f"{x:.5f},{y:.5f},{min(x + CELL_DEG, e):.5f},{min(y + CELL_DEG, n):.5f}"
            for f in get("map_features", bbox=bbox, limit=2000,
                         fields="id,object_value,geometry,first_seen_at,last_seen_at",
                         object_values=",".join(TL_VALUES)):
                feats[f["id"]] = f
            cell = get("images", bbox=bbox, limit=2000, fields="id,captured_at,geometry")
            if len(cell) == 2000:
                print(f"image cap reached in cell {bbox}")
            for i in cell:
                imgs[i["id"]] = i
    return list(feats.values()), list(imgs.values())


if REFRESH_MLY or not (tl_path.exists() and img_path.exists()):
    feats, imgs = fetch_mapillary(tuple(gpd.GeoSeries([domain], crs=CRS_A)
                                        .to_crs(CRS_G).total_bounds))
    gpd.GeoDataFrame.from_features(
        [{"type": "Feature", "geometry": f["geometry"],
          "properties": {k: f[k] for k in f if k != "geometry"}} for f in feats],
        crs=CRS_G).to_file(tl_path, driver="GeoJSON")
    gpd.GeoDataFrame(
        {"id": [i["id"] for i in imgs], "captured_at": [i["captured_at"] for i in imgs]},
        geometry=gpd.points_from_xy([i["geometry"]["coordinates"][0] for i in imgs],
                                    [i["geometry"]["coordinates"][1] for i in imgs]),
        crs=CRS_G).to_parquet(img_path, index=False)

tl = gpd.read_file(tl_path).to_crs(CRS_A)
img = gpd.read_parquet(img_path).to_crs(CRS_A)
img["year"] = pd.to_datetime(img["captured_at"], unit="ms").dt.year
print(f"{len(tl)} traffic-light detections, {len(img)} images")

# %% [markdown]
# ## 1. Image coverage
#
# Coverage is measured on the road network: the share of core passenger-edge length
# with an image within 20 m, for all years and for recent years only.

# %%
net = sumolib.net.readNet(str(ROOT / "cache" / "sumo" / "domain.net.xml.gz"))
dx, dy = net.getLocationOffset()
from shapely.geometry import LineString, Point  # noqa: E402

edges = gpd.GeoDataFrame(
    {"edge": [e.getID() for e in net.getEdges() if e.allows("passenger")]},
    geometry=[LineString([(x - dx, y - dy) for x, y in e.getShape()])
              for e in net.getEdges() if e.allows("passenger")], crs=CRS_A)
core_edges = edges[edges.geometry.interpolate(0.5, normalized=True).within(core)]
core_len = core_edges.length.sum()


def covered_share(images: gpd.GeoDataFrame) -> float:
    if images.empty:
        return 0.0
    buf = images.buffer(20).union_all()
    return core_edges.intersection(buf).length.sum() / core_len


img["in_core"] = img.within(core)
pd.DataFrame({
    "images in core": img[img["in_core"]].groupby("year").size(),
}).T

# %%
print(f"core edge length covered within 20 m: all years {100 * covered_share(img):.0f}%, "
      f"{RECENT_YEAR}+ {100 * covered_share(img[img['year'] >= RECENT_YEAR]):.0f}%")

# %% [markdown]
# ## 2. Detections

# %%
tl["kind"] = tl["object_value"].str.replace("object--traffic-light--", "")
tl["last_seen"] = pd.to_datetime(tl["last_seen_at"]).dt.year
tl["first_seen"] = pd.to_datetime(tl["first_seen_at"]).dt.year
tl["in_core"] = tl.within(core)
pd.crosstab([tl["in_core"], tl["kind"]], tl["last_seen"], margins=True)

# %% [markdown]
# Detections stop in 2020, although the core has thousands of images from
# `RECENT_YEAR` onward. Were recent images taken where the detections are? For each
# detection, the newest image within 20 m:

# %%
near = gpd.sjoin_nearest(tl[["id", "last_seen", "geometry"]],
                         img[["year", "geometry"]], max_distance=20, how="left")
newest = near.groupby("id")["year"].max()
tl["newest_image_20m"] = tl["id"].map(newest)
print("detections with a newer image within 20 m than their last sighting:",
      int((tl["newest_image_20m"] > tl["last_seen"]).sum()), "of", len(tl))

# %% [markdown]
# ## 3. Junctions
#
# A Mapillary feature is one signal head, not a junction. Vehicle signal heads
# (all kinds except `pedestrians`) are assigned to the nearest network junction
# within `MATCH_M`; a junction with at least one is **observed signalised**.
# Pedestrian heads are kept apart: they mark signalised crossings, which need not
# be junctions.


# %%
def junctions(network) -> gpd.GeoDataFrame:
    nodes = [n for n in network.getNodes() if not n.getType().startswith("dead_end")]
    ox_, oy_ = network.getLocationOffset()
    return gpd.GeoDataFrame(
        {"node": [n.getID() for n in nodes], "type": [n.getType() for n in nodes]},
        geometry=[Point(n.getCoord()[0] - ox_, n.getCoord()[1] - oy_) for n in nodes],
        crs=CRS_A)


jn = junctions(net)
veh = tl[tl["kind"] != "pedestrians"]
hit = gpd.sjoin_nearest(veh[["id", "geometry"]], jn[["node", "geometry"]],
                        max_distance=MATCH_M, distance_col="dist_m")
observed = jn[jn["node"].isin(hit["node"])].copy()
observed["in_core"] = observed.within(core)
print(f"{len(veh)} vehicle signal heads -> {hit['node'].nunique()} junctions "
      f"({observed['in_core'].sum()} in the core); "
      f"{veh['id'].isin(hit['id']).mean() * 100:.0f}% of heads matched within {MATCH_M} m")

# %% [markdown]
# Against netconvert's geometric guess (`--tls.guess`, from `01-road-network`):
# junction ids differ between the two builds, so they are matched by distance.

# %%
guessed_net = sumolib.net.readNet(str(ROOT / "cache" / "sumo" / "domain_tlsguess.net.xml.gz"))
gj = junctions(guessed_net)
guessed = gj[gj["type"].str.startswith("traffic_light")].copy()
guessed["in_core"] = guessed.within(core)


def within(a: gpd.GeoDataFrame, b: gpd.GeoDataFrame, d: float) -> pd.Series:
    return a.geometry.apply(lambda p: bool(len(b)) and b.distance(p).min() <= d)


guessed["mapillary_evidence"] = within(guessed, observed, MATCH_M)
observed["guessed"] = within(observed, guessed, MATCH_M)
summary = pd.DataFrame({
    "guessed (netconvert)": guessed.groupby("in_core").size(),
    "guessed with Mapillary evidence": guessed.groupby("in_core")["mapillary_evidence"].sum(),
    "observed (Mapillary)": observed.groupby("in_core").size(),
    "observed but not guessed": observed.groupby("in_core")["guessed"]
    .apply(lambda s: (~s).sum()),
}).rename(index={True: "core", False: "outside core"})
summary

# %% [markdown]
# ## Map
#
# Vehicle signal heads in blue, pedestrian heads in light blue, junctions guessed
# by netconvert as hollow orange rings, recent images (from `RECENT_YEAR`) as
# grey dots.

# %%
from lonboard import Map, PolygonLayer, ScatterplotLayer  # noqa: E402

Map(layers=[
    ScatterplotLayer.from_geopandas(img[img["year"] >= RECENT_YEAR][["year", "geometry"]]
                                    .to_crs(CRS_G), get_fill_color=[190, 190, 190],
                                    radius_min_pixels=1),
    ScatterplotLayer.from_geopandas(guessed[["node", "geometry"]].to_crs(CRS_G),
                                    filled=False, stroked=True, get_line_color=[235, 104, 52],
                                    line_width_min_pixels=2, radius_min_pixels=9),
    ScatterplotLayer.from_geopandas(veh[["id", "last_seen", "geometry"]].to_crs(CRS_G),
                                    get_fill_color=[42, 120, 214], radius_min_pixels=4),
    ScatterplotLayer.from_geopandas(
        tl[tl["kind"] == "pedestrians"][["id", "last_seen", "geometry"]].to_crs(CRS_G),
        get_fill_color=[134, 182, 239], radius_min_pixels=4),
    PolygonLayer.from_geopandas(gpd.GeoDataFrame(geometry=[core], crs=CRS_A).to_crs(CRS_G),
                                filled=False, get_line_color=[215, 48, 31],
                                line_width_min_pixels=2),
])

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | Mapillary imagery covers 20% of core passenger-edge length within 20 m in any year, and 2% from 2023. Most core imagery is from 2017 | Absence of a detection is not absence of a signal. Mapillary gives a lower bound |
# | 67 traffic-light detections in the domain, 9 in the core; all last seen 2015–2020. 26 have a newer image nearby than their last sighting, so detection has not been re-run on recent imagery, or the signal is gone; the data cannot tell which | Mapillary evidence is dated. Each junction records the date of its last sighting |
# | 57 vehicle signal heads map to 27 junctions (98% match within 40 m), 7 of them in the core | Detections resolve cleanly to network junctions |
# | Of netconvert's 18 guessed core junctions, 3 have Mapillary evidence; 3 of the 7 observed core junctions were not guessed | The geometric guess and the observations barely agree. Neither can stand alone as the inventory |
#
# **Recommendation for the proposal.** The core is small enough to verify by hand:
# about 22 candidate junctions (the guessed ones plus those observed but not
# guessed). A signal inventory in configuration (`config/signals.yaml`) lists each
# junction with its evidence: source (`osm`, `mapillary`, `manual`, `guessed`),
# the image or document it rests on, and its date. netconvert then takes the
# listed junctions as signalised (`--tls.set`) instead of guessing, and a guessed
# junction that no evidence supports stays flagged as guessed (P4). Outside the
# core, which is simulated but not scored, the guess is acceptable if flagged.
#
# Mapillary data is CC BY-SA 4.0: the pinned extracts and anything derived from
# them carry attribution and share-alike.

# %%
