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
# # 01 — Building heights
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# The roadmap flags OSM building-height coverage in Nairobi as thin, and makes the
# estimation rule and its flag a spec requirement (P1, P4). `stack.md` §8 sketches
# the rule: OSM `height`, else level count, else a land-use prior. This notebook
# measures whether the data supports that chain.
#
# Questions:
#
# 1. How many buildings carry `height` or `building:levels`, in the core and the
#    domain, by count and by footprint area?
# 2. What metres-per-level factor do buildings tagged with both imply?
# 3. Can OSM `landuse` polygons supply a prior for the rest?
# 4. What share of buildings would each rung of the chain resolve?
#
# Runs offline from the osmnx cache in `cache/` and reads the study-area tiers from
# `data/processed/study_area.gpkg` (run `just study-area` first). Set
# `REFRESH_OSM = True` to re-query OpenStreetMap.

# %% tags=["parameters"]
REFRESH_OSM = False
CONFIG = "config/study_area.yaml"

# %%
import os
import re
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
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers = gpd.read_file(gpkg, layer="tiers").to_crs(CRS_A).set_index("tier").geometry
core, domain = tiers["core"], tiers["domain"]

# %% [markdown]
# ## Inputs
#
# Every OSM feature tagged `building` inside the domain. Points (buildings mapped
# as a node) have no footprint to extrude and are dropped.

# %%
raw = ox.features_from_polygon(gpd.GeoSeries([domain], crs=CRS_A).to_crs(CRS_G).iloc[0],
                               {"building": True})
print(raw.geom_type.value_counts().to_dict())
b = raw[raw.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(CRS_A).copy()
b["area_m2"] = b.area
b["in_core"] = b.representative_point().within(core)
print(f"{len(b)} footprints, {b['in_core'].sum()} in the core")

# %% [markdown]
# ## 1. Tag coverage
#
# OSM `height` is free text. Values are parsed as metres when they are a bare number
# or carry an `m` suffix; anything else counts as missing.

# %%
def parse_metres(v: object) -> float | None:
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m?\s*", v)
    return float(m.group(1)) if m else None


def parse_levels(v: object) -> float | None:
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", v)
    return float(m.group(1)) if m else None


b["height_m"] = b.get("height", pd.Series(index=b.index, dtype=object)).map(parse_metres)
b["levels"] = b.get("building:levels", pd.Series(index=b.index, dtype=object)).map(parse_levels)
unparsed = b["height"].notna() & b["height_m"].isna()
print("height values that did not parse:", b.loc[unparsed, "height"].tolist())


def coverage(g: pd.DataFrame) -> pd.Series:
    known = g["height_m"].notna() | g["levels"].notna()
    return pd.Series({
        "footprints": len(g),
        "height_%": 100 * g["height_m"].notna().mean(),
        "levels_%": 100 * g["levels"].notna().mean(),
        "either_%": 100 * known.mean(),
        "either_by_area_%": 100 * g.loc[known, "area_m2"].sum() / g["area_m2"].sum(),
    })


cov = pd.DataFrame({"core": coverage(b[b["in_core"]]),
                    "domain outside core": coverage(b[~b["in_core"]]),
                    "domain": coverage(b)}).T.round(1)
cov

# %% [markdown]
# Coverage by count is poor everywhere. It is better by area, and far better in the
# core, because the tall CBD towers are the buildings mappers have tagged. The
# untagged majority are small footprints.

# %%
b.groupby(b["height_m"].notna() | b["levels"].notna())["area_m2"].describe().round(0)

# %% [markdown]
# ## 2. Metres per level
#
# Where a building carries both tags, `height / levels` is the observed storey
# height. It includes the taller ground floor and any roof structure, so it is
# expected to exceed a nominal 3 m.

# %%
both = b.dropna(subset=["height_m", "levels"]).query("levels > 0")
m_per_level = both["height_m"] / both["levels"]
print(f"n = {len(both)}")
m_per_level.describe().round(2)

# %% [markdown]
# How much does the choice of factor matter? Error of `levels × k` against tagged
# height, for the observed median and for the conventional 3 m:

# %%
k_median = m_per_level.median()
pd.DataFrame({
    f"k={k:.2f}": (both["levels"] * k - both["height_m"]).abs().describe()
    for k in (3.0, k_median)
}).round(1)

# %% [markdown]
# ## 3. Can OSM land use supply the prior?

# %%
lu_raw = ox.features_from_polygon(gpd.GeoSeries([domain], crs=CRS_A).to_crs(CRS_G).iloc[0],
                                  {"landuse": True})
lu = lu_raw[lu_raw.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(CRS_A)
lu_cover = lu.union_all().intersection(domain).area / domain.area
print(f"{len(lu)} landuse polygons covering {100 * lu_cover:.0f}% of the domain")
lu["landuse"].value_counts().head(10)

# %%
pts = gpd.GeoDataFrame(geometry=b.representative_point(), crs=CRS_A)
joined = gpd.sjoin(pts, lu[["landuse", "geometry"]], how="left", predicate="within")
b["landuse"] = joined[~joined.index.duplicated()]["landuse"]
print(f"buildings inside any landuse polygon: {100 * b['landuse'].notna().mean():.0f}%")
b["landuse"].value_counts(dropna=False).head(8)

# %% [markdown]
# Would the prior be informative where it exists? Median known height by land use,
# with levels converted at the median factor:

# %%
b["known_m"] = b["height_m"].fillna(b["levels"] * k_median)
(b.dropna(subset=["known_m"])
  .groupby(b["landuse"].fillna("(none)"))["known_m"]
  .agg(["count", "median", "min", "max"]).round(1)
  .sort_values("count", ascending=False))

# %% [markdown]
# ## 4. What each rung of the chain would resolve

# %%
rung = pd.Series("default", index=b.index)
rung[b["landuse"].notna()] = "landuse_prior"
rung[b["levels"].notna()] = "osm_levels"
rung[b["height_m"].notna()] = "osm_height"
b["source"] = rung
order = ["osm_height", "osm_levels", "landuse_prior", "default"]
share = pd.DataFrame({
    "footprints_%": 100 * b["source"].value_counts(normalize=True),
    "area_%": 100 * b.groupby("source")["area_m2"].sum() / b["area_m2"].sum(),
    "core_footprints_%": 100 * b.loc[b["in_core"], "source"].value_counts(normalize=True),
}).reindex(order).fillna(0).round(1)
share

# %% [markdown]
# ## Map
#
# Footprints coloured by the rung that resolves them: dark for OSM tags, amber for
# the land-use prior, grey for the default.

# %%
import numpy as np
from lonboard import Map, PolygonLayer

colours = {"osm_height": [8, 48, 107], "osm_levels": [66, 146, 198],
           "landuse_prior": [254, 153, 41], "default": [189, 189, 189]}
view = b[["source", "geometry"]].to_crs(CRS_G)
Map(layers=[
    PolygonLayer.from_geopandas(
        view, get_fill_color=np.array([colours[s] for s in view["source"]], dtype=np.uint8),
        get_line_color=[80, 80, 80], line_width_min_pixels=0.5),
    PolygonLayer.from_geopandas(
        gpd.GeoDataFrame(geometry=[core], crs=CRS_A).to_crs(CRS_G),
        filled=False, get_line_color=[215, 48, 31], line_width_min_pixels=2),
])

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | 5.0% of footprints carry a usable `height` or `building:levels`: 14% in the core by count, 32% by area. One `height` value (`10 floors`) does not parse | Estimation is the normal case, not the exception (P1). Every building carries a height source. Unparseable tags fall through to the next rung |
# | Buildings with both tags imply a median of 4.8 m per level (n = 39), not 3 m. It halves the median error (8.0 to 2.4 m) but not the mean (about 10 m), which a few outliers dominate | The level factor is configuration, set from this measurement and cited, with n stated |
# | OSM `landuse` covers 40% of the domain and contains 37% of buildings. In the core, one `commercial` polygon covers 84% of footprints, so the "prior" there is a single number | The land-use prior in `stack.md` §8 cannot be the last rung: 62% of footprints (46% of area) would reach a flat default |
# | Tagged buildings are large (median 698 m² vs 167 m² untagged) and tall. Class medians taken from them (commercial 28.8 m) would overstate the untagged majority | A prior learned from tagged OSM buildings is biased by selection. It must come from an independent source, or be stated as an upper bias |
#
# **Open question for the proposal.** The chain needs a last rung that does not
# depend on OSM tagging. Candidates, both free and P1-compatible:
#
# - **Google Open Buildings 2.5D Temporal**: per-pixel building height for Africa
#   (CC BY 4.0), available through Earth Engine, which the stack already uses for
#   Sentinel-1.
# - **GHSL GHS-BUILT-H**: 100 m average building height, global (CC BY 4.0), direct
#   download.
#
# Either adds a raster rung between OSM tags and the default. Both are estimates,
# so they are flagged as estimates (P4). Choosing between them needs a second
# notebook that samples each at the 317 tagged buildings here and compares error.

# %%
