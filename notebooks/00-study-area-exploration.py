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
# # 00 — Study area exploration
#
# **Step 0 · capability `study-area` · paper §2.1**
#
# Backfilled lab record. Step 0 was built before the project adopted the
# notebook-first workflow, so this notebook re-derives the shipped result with
# `nairobi_dt.study_area` and records what the derivation depends on. It is the
# evidence cited by the `study-area` spec.
#
# Questions:
#
# 1. Which OSM ways does each configured road name actually match?
# 2. Where do consecutive boundary roads fail to meet, and by how much?
# 3. Do the landmarks close the gaps they are meant to close?
# 4. What does the interior-road check really measure?
# 5. What are the tier areas, and do they match the shipped report?
#
# Runs offline from the osmnx cache in `cache/`. Set `REFRESH_OSM = True` to
# re-query OpenStreetMap (the result may then differ from the shipped report).

# %% tags=["parameters"]
REFRESH_OSM = False
CONFIG = "config/study_area.yaml"

# %%
import json
import os
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

ox.settings.cache_folder = str(ROOT / "cache")
ox.settings.use_cache = not REFRESH_OSM

cfg = sa.load_config(CONFIG)
CRS_A, CRS_G = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
core_cfg = cfg["core"]

# %% [markdown]
# ## Inputs
#
# Roads come from a seed bounding box that only bounds the OSM query; it does
# not define the study area.

# %%
roads = sa.fetch_roads(cfg["seed_bbox"], CRS_A)
landmarks = sa.fetch_landmarks(core_cfg["landmarks"], CRS_A)
county = sa.fetch_county(cfg["county"]["query"], CRS_A)
print(f"{len(roads)} road features, CRS {roads.crs.to_epsg()}")
landmarks[["key", "source"]]

# %% [markdown]
# ## 1. What each configured name matches
#
# Matching is exact on the OSM `name` tag, case-insensitive. A name matches every
# way that carries it, whatever its `highway` class, so service roads and
# parallel carriageways are included.

# %%
def matches(names: list[str]) -> gpd.GeoDataFrame:
    wanted = {n.strip().lower() for n in names}
    return roads[roads["name"].fillna("").str.strip().str.lower().isin(wanted)]


rows = []
for group in ("boundary_ring", "interior_roads"):
    for r in core_cfg[group]:
        m = matches(r["names"])
        rows.append({"group": group, "key": r["key"], "ways": len(m),
                     "length_m": round(m.union_all().length),
                     "highway": ", ".join(sorted(m["highway"].astype(str).unique()))})
pd.DataFrame(rows)

# %% [markdown]
# **Which Ring Road.** Nairobi has several roads named Ring Road. Exact matching
# picks only the way tagged plainly `Ring Road`, not `Ring Road Ngara`:

# %%
similar = roads[roads["name"].fillna("").str.contains("ring road", case=False)]
print(sorted(similar["name"].unique()))
print("matched extent (lon/lat):",
      [round(v, 4) for v in matches(["Ring Road"]).to_crs(CRS_G).total_bounds])

# %% [markdown]
# ## 2. Corners: where the boundary roads meet
#
# The core's corners are the intersections of consecutive roads in the ring.
# Where two named ways do not touch, usually because the roundabout joining them
# has no name, the corner is the midpoint of the shortest gap and the gap is
# recorded. Gaps above the tolerance are flagged.

# %%
core, corners, checks = sa.build_core(roads, landmarks, core_cfg)
tol = core_cfg["corner_gap_tolerance_m"]
corner_df = pd.DataFrame([{"corner": f"{c.road_a}/{c.road_b}", "method": c.method,
                           "gap_m": round(c.gap_m, 1), "over_tolerance": c.gap_m > tol}
                          for c in corners])
corner_df

# %% [markdown]
# No ring pair intersects exactly: every corner is a `nearest_gap`. Three gaps are
# small (under 35 m) and consistent with unnamed roundabout rings. Kirinyaga Road /
# Ring Road is 131 m, inside the 150 m tolerance but close to it: a small OSM edit
# there could flip it to flagged. The University Way / Kirinyaga Road gap is wide,
# which is why the Globe Roundabout is a required landmark.

# %% [markdown]
# ## 3. Landmarks close the ring

# %%
landmark_df = pd.DataFrame({"key": landmarks["key"],
                            "inside_core": [checks["landmarks_inside"][k] for k in landmarks["key"]],
                            "dist_to_nearest_corner_m": [
                                round(min(g.distance(gpd.points_from_xy([c.x], [c.y])[0])
                                          for c in corners))
                                for g in landmarks.geometry]})
landmark_df

# %% [markdown]
# Without landmarks the core would be the convex hull of the five corners alone.
# Comparing the two shows how much area the landmarks add:

# %%
hull_only, _, _ = sa.build_core(roads, landmarks.iloc[0:0], core_cfg)
print(f"corners only: {hull_only.area / 1e6:.3f} km²   "
      f"with landmarks: {core.area / 1e6:.3f} km²   "
      f"added: {(core.area - hull_only.area) / 1e6:.3f} km²")

# %% [markdown]
# ## 4. What the interior-road check measures
#
# Validation requires at least `min_interior_length_m` of each interior road to lie
# inside the core. The measured length sums **every** matched way, so dual
# carriageways and same-named service roads count more than once:

# %%
minx, miny, maxx, maxy = core.bounds
print(f"core extent: {maxx - minx:.0f} m E-W x {maxy - miny:.0f} m N-S")
interior = []
for r in core_cfg["interior_roads"]:
    m = matches(r["names"]).clip(core)
    by_class = m.assign(len_m=m.length).groupby(m["highway"].astype(str))["len_m"].sum()
    interior.append({"key": r["key"], "inside_m": round(m.length.sum()),
                     **{f"{k}_m": round(v) for k, v in by_class.items()}})
pd.DataFrame(interior).fillna(0)

# %% [markdown]
# **Finding.** Both avenues exceed the 300 m minimum by an order of magnitude, and
# their measured length exceeds the core's own width. The check passes robustly,
# but it is weaker than it reads: it confirms that the avenues are inside the core,
# not that a particular share of the avenue is. That is acceptable for its purpose
# (catching a core that has slid off the town centre), and the spec states it as
# a length of matched ways, not of a centreline. Restricting it to main
# carriageways is an option for a later change, not part of this backfill.

# %% [markdown]
# ## 5. Tiers, and agreement with the shipped report

# %%
domain = sa.build_domain(core, cfg["domain"]["buffer_m"])
areas = {"core": core.area / 1e6, "domain": domain.area / 1e6,
         "county": county.geometry.iloc[0].area / 1e6}
report_path = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["report"]
shipped = json.loads(report_path.read_text())["area_km2"] if report_path.exists() else {}
pd.DataFrame({"derived_km2": {k: round(v, 3) for k, v in areas.items()},
              "shipped_km2": shipped})

# %%
print("domain contains core:", domain.contains(core))
print("county contains domain:", county.geometry.iloc[0].contains(domain))
print("validation passed:", checks["passed"], "| wide gaps:", checks["wide_gaps"])

# %% [markdown]
# ## Map

# %%
from lonboard import Map, PathLayer, PolygonLayer, ScatterplotLayer

tiers = gpd.GeoDataFrame({"tier": ["core", "domain"]}, crs=CRS_A,
                         geometry=[core, domain]).to_crs(CRS_G)
ring = pd.concat([matches(r["names"]).assign(key=r["key"]) for r in core_cfg["boundary_ring"]])
Map(layers=[
    PolygonLayer.from_geopandas(tiers, get_fill_color=[215, 48, 31, 20],
                                get_line_color=[215, 48, 31], line_width_min_pixels=2),
    PathLayer.from_geopandas(ring[["key", "geometry"]].to_crs(CRS_G),
                             get_color=[40, 40, 40], width_min_pixels=3),
    ScatterplotLayer.from_geopandas(landmarks[["key", "geometry"]].to_crs(CRS_G),
                                    get_fill_color=[0, 90, 200], radius_min_pixels=6),
])

# %% [markdown]
# ## Conclusions carried into the spec
#
# | Finding | Spec requirement |
# |---|---|
# | Boundary roads rarely intersect in OSM; gaps are measured and flagged over 150 m | Corner derivation |
# | The Globe Roundabout closes the one wide gap; OTC extends the core south-east | Landmarks inside core |
# | Name matching is exact and case-insensitive; `Ring Road` ≠ `Ring Road Ngara` | Boundary derivation from configuration |
# | Interior check measures matched-way length, not share | Interior-road validation |
# | Core 1.763, domain 10.232, county 695.937 km², nested | Three nested tiers |
