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
# # 01 — Building height sources
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# `01-building-heights` found that 95% of OSM footprints in the domain carry no
# usable height, and that OSM land use cannot fill the gap. This notebook compares
# two free height rasters that do not depend on OSM tagging, as candidates for the
# rung between OSM tags and a flat default:
#
# | | GHSL GHS-BUILT-H ANBH | Google Open Buildings 2.5D Temporal |
# |---|---|---|
# | Measures | Average net building height per cell | Building height per pixel, relative to terrain |
# | Resolution | 100 m (Mollweide, EPSG:54009) | 4 m effective, 0.5 m grid (EPSG:32737 here) |
# | Epoch | 2018 | Yearly, 2016–2023 |
# | Range | Unbounded | Clipped to [0, 100] m |
# | Access | Direct download, no account | Earth Engine, account required |
# | Licence | CC BY 4.0 | CC BY 4.0 and ODbL 1.0 |
#
# Questions:
#
# 1. What share of footprints does each source give a height?
# 2. How well does each agree with OSM-tagged heights, in level and in rank?
# 3. Does a single calibration factor remove the bias, under cross-validation?
# 4. What does each source assign to the untagged majority?
#
# **Reference heights.** OSM `height` where present (n ≈ 48); otherwise
# `building:levels` × 4.8 m, the median storey height measured in
# `01-building-heights` (n ≈ 270). The level-derived set is itself an estimate, so
# the two are reported separately.
#
# **Inputs.** The GHSL tile is downloaded to `cache/ghsl/` on first run. The Open
# Buildings values are sampled in Earth Engine once and pinned as
# `data/raw/open_buildings_temporal_<year>_domain.parquet`, so the notebook
# re-executes without an Earth Engine account. Set `REFRESH_EE = True` to
# re-sample; that needs `earthengine authenticate` and `EE_PROJECT`.

# %% tags=["parameters"]
REFRESH_OSM = False
REFRESH_EE = False
EE_PROJECT = "parallelprocessing-433914"
OB_YEAR = 2023
OB_PRESENCE_MIN = 0.5  # uncalibrated model confidence; pixels below are not building
M_PER_LEVEL = 4.81  # median storey height, 01-building-heights
CONFIG = "config/study_area.yaml"
SEED = 1

# %%
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio

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
# The same footprints and tag parsing as `01-building-heights`.

# %%
raw = ox.features_from_polygon(gpd.GeoSeries([domain], crs=CRS_A).to_crs(CRS_G).iloc[0],
                               {"building": True})
b = raw[raw.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(CRS_A).reset_index()
b["area_m2"] = b.area
b["in_core"] = b.representative_point().within(core)


def parse_number(v: object, unit: str = "") -> float:
    if not isinstance(v, str):
        return np.nan
    m = re.fullmatch(rf"\s*(\d+(?:\.\d+)?)\s*{unit}\s*", v)
    return float(m.group(1)) if m else np.nan


b["height_m"] = b["height"].map(lambda v: parse_number(v, "m?"))
b["levels"] = b["building:levels"].map(parse_number)
b["ref_m"] = b["height_m"].fillna(b["levels"] * M_PER_LEVEL)
b["ref_kind"] = np.select([b["height_m"].notna(), b["levels"].notna()],
                          ["height", "levels"], default="none")
print(f"{len(b)} footprints; reference heights: {b['ref_kind'].value_counts().to_dict()}")

# %% [markdown]
# ## GHSL: sample at each footprint
#
# One 100 m cell per building, taken at the footprint's representative point. At
# 100 m a cell spans several buildings, so this is the neighbourhood average, not
# the building.

# %%
GHSL_TILE = "GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R10_C22"
GHSL_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_H_GLOBE_R2023A/"
            f"GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100/V1-0/tiles/{GHSL_TILE}.zip")
ghsl_tif = ROOT / "cache" / "ghsl" / f"{GHSL_TILE}.tif"
if not ghsl_tif.exists():
    ghsl_tif.parent.mkdir(parents=True, exist_ok=True)
    zpath, _ = urllib.request.urlretrieve(GHSL_URL, ghsl_tif.with_suffix(".zip"))
    with zipfile.ZipFile(zpath) as z:
        z.extract(ghsl_tif.name, ghsl_tif.parent)

with rasterio.open(ghsl_tif) as src:
    pts = b.representative_point().to_crs(src.crs)
    b["ghsl_m"] = [v[0] for v in src.sample(zip(pts.x, pts.y, strict=True))]
    b.loc[b["ghsl_m"] == src.nodata, "ghsl_m"] = np.nan
    print(src.crs.to_string()[:40], src.res, "nodata", src.nodata)

# %% [markdown]
# ## Open Buildings: median height over each footprint
#
# Pixels with `building_presence` below `OB_PRESENCE_MIN` are masked, then the
# median `building_height` is taken over the footprint at 4 m, the dataset's
# effective resolution. A footprint with no pixel over the threshold gets no value.

# %%
ob_path = ROOT / "data" / "raw" / f"open_buildings_temporal_{OB_YEAR}_domain.parquet"


def fetch_open_buildings(footprints: gpd.GeoDataFrame) -> pd.DataFrame:
    import ee

    ee.Initialize(project=EE_PROJECT)
    img = (ee.ImageCollection("GOOGLE/Research/open-buildings-temporal/v1")
           .filterDate(f"{OB_YEAR}-01-01", f"{OB_YEAR + 1}-01-01")
           .filterBounds(ee.Geometry.Point(*footprints.union_all().centroid.coords[0]))
           .mosaic())
    height = (img.select("building_height")
              .updateMask(img.select("building_presence").gte(OB_PRESENCE_MIN))
              .rename("h"))
    reducer = ee.Reducer.median().combine(ee.Reducer.count(), sharedInputs=True)
    fp = footprints[["id", "geometry"]].to_crs(CRS_G)
    rows = []
    for i in range(0, len(fp), 1000):
        fc = ee.FeatureCollection(json.loads(fp.iloc[i:i + 1000].to_json(drop_id=True)))
        res = height.reduceRegions(fc, reducer, scale=4, crs=CRS_A)
        rows += [f["properties"] for f in res.map(lambda f: f.setGeometry(None))
                 .getInfo()["features"]]
    return (pd.DataFrame(rows)
            .rename(columns={"median": "ob_m", "count": "ob_pixels"})
            .assign(year=OB_YEAR, presence_min=OB_PRESENCE_MIN, scale_m=4))


if REFRESH_EE or not ob_path.exists():
    ob_path.parent.mkdir(parents=True, exist_ok=True)
    fetch_open_buildings(b).to_parquet(ob_path, index=False)
ob = pd.read_parquet(ob_path).set_index("id")
b["ob_m"] = b["id"].map(ob["ob_m"])
print(f"pinned extract: {len(ob)} rows; footprints not in it: {(~b['id'].isin(ob.index)).sum()}")

# %% [markdown]
# ## 1. Coverage

# %%
SOURCES = {"ghsl_m": "GHSL 2018", "ob_m": f"Open Buildings {OB_YEAR}"}


def coverage(g: pd.DataFrame) -> dict:
    return {f"{name} %": 100 * g[col].notna().mean() for col, name in SOURCES.items()}


pd.DataFrame({"domain": coverage(b), "core": coverage(b[b["in_core"]]),
              "untagged": coverage(b[b["ref_kind"] == "none"])}).T.round(1)

# %% [markdown]
# Open Buildings misses mainly small footprints, where no pixel clears the
# presence threshold:

# %%
b.groupby(b["ob_m"].isna().map({True: "no OB value", False: "OB value"}))["area_m2"] \
    .describe().round(0)

# %% [markdown]
# ## 2. Agreement with OSM reference heights
#
# Error is source minus reference, so negative bias means the source reads low.
# Rank correlation (Spearman) asks whether the source orders buildings correctly,
# whatever its scale.


# %%
def agreement(g: pd.DataFrame, col: str) -> dict:
    g = g.dropna(subset=[col, "ref_m"])
    err = g[col] - g["ref_m"]
    return {"n": len(g), "MAE_m": err.abs().mean(), "MedAE_m": err.abs().median(),
            "bias_m": err.median(), "spearman": g[col].rank().corr(g["ref_m"].rank())}


ref = b[b["ref_kind"] != "none"]
agree = pd.DataFrame([{"source": name, "reference": kind, **agreement(g, col)}
                      for col, name in SOURCES.items()
                      for kind, g in ref.groupby("ref_kind")])
agree.round(2)

# %% [markdown]
# Buildings above 100 m cannot be read correctly by Open Buildings, which clips
# there. How many references reach that high?

# %%
tall = ref[ref["ref_m"] > 100][["id", "name", "ref_m", "ref_kind", "ob_m", "ghsl_m"]]
tall.round(1)

# %%
fig, axes = plt.subplots(1, 2, figsize=(9, 4.3), sharex=True, sharey=True)
lim = [0, max(130, ref["ref_m"].max() * 1.05)]
marker = {"height": "o", "levels": "^"}
for ax, (col, name) in zip(axes, SOURCES.items(), strict=True):
    for kind, g in ref.groupby("ref_kind"):
        ax.scatter(g["ref_m"], g[col], s=18, marker=marker[kind], color="#2a78d6",
                   alpha=0.55, linewidths=0, label=f"OSM {kind}")
    ax.plot(lim, lim, color="#8c8c8c", lw=1, ls="--", label="1:1")
    ax.set(title=name, xlabel="OSM reference height (m)", xlim=lim, ylim=lim)
    ax.grid(color="#e6e6e6", lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)
axes[0].set_ylabel("Source height (m)")
axes[0].legend(frameon=False, loc="upper left")
fig.tight_layout()

# %% [markdown]
# ## 3. Does one calibration factor fix the bias?
#
# The simplest correction is `k × source`, with `k` the median ratio of reference
# to source. It is fitted and scored under 5-fold cross-validation over all
# reference buildings, so every score is out of sample.


# %%
def cross_validate(g: pd.DataFrame, col: str, folds: int = 5) -> dict:
    g = g.dropna(subset=[col, "ref_m"]).query(f"{col} > 0")
    fold = np.random.default_rng(SEED).permutation(len(g)) % folds
    pred = pd.Series(index=g.index, dtype=float)
    ks = []
    for f in range(folds):
        train, test = g[fold != f], g[fold == f]
        k = (train["ref_m"] / train[col]).median()
        pred[test.index] = k * test[col]
        ks.append(k)
    err = pred - g["ref_m"]
    return {"n": len(g), "k_mean": np.mean(ks), "k_range": np.ptp(ks),
            "MAE_m": err.abs().mean(), "MedAE_m": err.abs().median(), "bias_m": err.median()}


calib = pd.DataFrame([{"source": name, **cross_validate(ref, col)}
                      for col, name in SOURCES.items()])
calib.round(2)

# %% [markdown]
# ## 4. What the untagged majority would receive
#
# The rung matters most for the 95% of buildings with no OSM height. Compare the
# distributions each source assigns them, raw and calibrated:

# %%
untagged = b[b["ref_kind"] == "none"]
k = dict(zip(calib["source"], calib["k_mean"], strict=True))
pd.DataFrame({
    **{f"{name} raw": untagged[col].describe() for col, name in SOURCES.items()},
    **{f"{name} ×k": (untagged[col] * k[name]).describe() for col, name in SOURCES.items()},
    "reference (tagged)": ref["ref_m"].describe(),
}).round(1)

# %% [markdown]
# ## Findings
#
# Filled in from the outputs above; see the discussion that closes this notebook.

# %%
