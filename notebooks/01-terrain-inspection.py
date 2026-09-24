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
# # 01 — Terrain inspection
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# The roadmap builds the Cesium terrain tileset from Copernicus GLO-30 (`stack.md`
# §1, §8). GLO-30 is a surface model: it includes buildings and trees. That matters
# twice: in the 3D scene, extruded buildings would stand on terrain that already
# contains them; and in Step 2, flood routing needs the ground, not the roofs.
#
# FABDEM is GLO-30 with buildings and forests removed by machine learning
# (Hawker et al. 2022). It is CC BY-NC-SA 4.0, which the research-only status of
# this project permits.
#
# Questions:
#
# 1. Does GLO-30 have voids or artefacts over the county, the domain or the core?
# 2. How far does GLO-30 sit above bare earth (FABDEM) in the CBD, and does that
#    track building height?
# 3. What vertical datum do both use, and what offset does Cesium need?
#
# **Inputs.** GLO-30 tiles stream from the public AWS bucket (no account). The
# FABDEM clip for the domain is taken once through Earth Engine and pinned as
# `data/raw/fabdem_domain.tif`; set `REFRESH_EE = True` to re-take it. Building
# heights come from the pinned Open Buildings extract of
# `01-building-height-sources`.

# %% tags=["parameters"]
REFRESH_OSM = False
REFRESH_EE = False
EE_PROJECT = "parallelprocessing-433914"
CONFIG = "config/study_area.yaml"
OB_K = 1.19  # Open Buildings calibration, 01-building-height-sources

# %%
import os
import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
import pyproj
import rasterio
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.merge import merge

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

ox.settings.cache_folder = str(ROOT / "cache")
ox.settings.use_cache = not REFRESH_OSM

cfg = sa.load_config(CONFIG)
CRS_A, CRS_G = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers_g = gpd.read_file(gpkg, layer="tiers").to_crs(CRS_G).set_index("tier").geometry

# %% [markdown]
# ## GLO-30 tiles
#
# 1° × 1° COGs, named by their south-west corner. The county spans two tiles.

# %%
GLO30 = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{t}_DEM/"
         "Copernicus_DSM_COG_10_{t}_DEM.tif")


def glo30_tiles(bounds: tuple[float, float, float, float]) -> list[str]:
    w, s, e, n = bounds
    names = []
    for lat in range(int(np.floor(s)), int(np.ceil(n))):
        for lon in range(int(np.floor(w)), int(np.ceil(e))):
            ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00"
            ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00"
            names.append(GLO30.format(t=f"{ns}_{ew}"))
    return names


tiles = glo30_tiles(tiers_g["county"].bounds)
print(*[t.rsplit("/", 1)[1] for t in tiles], sep="\n")

# %% [markdown]
# ## 1. Voids and range

# %%
srcs = [rasterio.open(t) for t in tiles]
print(srcs[0].crs, srcs[0].res, srcs[0].dtypes, "nodata:", srcs[0].nodata)


def snap(bounds: tuple[float, float, float, float], src) -> tuple[float, ...]:
    """Grow bounds outward to whole cells; unsnapped bounds make merge pad with 0."""
    (res, _), x0, y0 = src.res, src.bounds.left, src.bounds.top
    w, s, e, n = bounds
    return (x0 + np.floor((w - x0) / res) * res, y0 - np.ceil((y0 - s) / res) * res,
            x0 + np.ceil((e - x0) / res) * res, y0 - np.floor((y0 - n) / res) * res)


county_dem, county_tr = merge(srcs, bounds=snap(tiers_g["county"].bounds, srcs[0]),
                              nodata=np.nan)
for s in srcs:
    s.close()


def stats(arr: np.ndarray, tr, geom) -> dict:
    inside = ~geometry_mask([geom], arr.shape[-2:], tr)
    v = arr[0][inside] if arr.ndim == 3 else arr[inside]
    return {"cells": v.size, "non_finite": int((~np.isfinite(v)).sum()),
            "min_m": v.min(), "p50_m": np.median(v), "max_m": v.max()}


pd.DataFrame({t: stats(county_dem, county_tr, tiers_g[t])
              for t in ("county", "domain", "core")}).T.round(1)

# %% [markdown]
# ## 2. GLO-30 against bare earth
#
# FABDEM keeps GLO-30's grid: both put cell edges half an arc-second off whole
# degrees (FABDEM origin 35.99986°, GLO-30 cells at 36° + (k + ½)″). The export
# therefore picks cells without resampling, and the two difference cell by cell.

# %%
dom_box = tiers_g["domain"].buffer(0.002)
with rasterio.open(tiles[0]) as src:
    glo, glo_tr = mask(src, [dom_box], crop=True, filled=False)
glo = glo[0].astype("float64").filled(np.nan)

fab_path = ROOT / "data" / "raw" / "fabdem_domain.tif"


def fetch_fabdem(transform, shape: tuple[int, int], path: Path) -> None:
    import ee

    ee.Initialize(project=EE_PROJECT)
    img = (ee.ImageCollection("projects/sat-io/open-datasets/FABDEM")
           .filterBounds(ee.Geometry.Point(*dom_box.centroid.coords[0])).mosaic())
    w, n = transform.c, transform.f
    e, s = w + shape[1] * transform.a, n + shape[0] * transform.e
    # crs_transform with a planar region reproduces the grid exactly; `dimensions`
    # or the camelCase key make Earth Engine resample to fit instead.
    url = img.getDownloadURL({
        "region": ee.Geometry.Rectangle([w, s, e, n], None, False), "crs": CRS_G,
        "crs_transform": list(transform)[:6], "format": "GEO_TIFF"})
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)


if REFRESH_EE or not fab_path.exists():
    fetch_fabdem(glo_tr, glo.shape, fab_path)
with rasterio.open(fab_path) as f:
    assert f.transform.almost_equals(glo_tr) and f.shape == glo.shape, "grids differ"
    fab = f.read(1).astype("float64")
fab[(fab < 0) | ~np.isfinite(fab)] = np.nan  # edge cells outside the export
diff = glo - fab


def summary(mask_: np.ndarray) -> dict:
    v = diff[mask_ & np.isfinite(diff)]
    return {"cells": v.size, "mean_m": v.mean(), "p50_m": np.median(v),
            "p95_m": np.percentile(v, 95), "max_m": v.max(), "min_m": v.min()}


in_ = {t: ~geometry_mask([tiers_g[t]], glo.shape, glo_tr) for t in ("domain", "core")}
pd.DataFrame({t: summary(m) for t, m in in_.items()}).T.round(2)

# %% [markdown]
# Does the excess track what stands on the cell? Mean building height per 30 m cell,
# weighted by footprint area, from OSM tags where present and calibrated Open
# Buildings otherwise:

# %%
raw = ox.features_from_polygon(tiers_g["domain"], {"building": True})
b = raw[raw.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index()
ob = pd.read_parquet(ROOT / "data" / "raw" / "open_buildings_temporal_2023_domain.parquet")
b["h_m"] = (pd.to_numeric(b["height"], errors="coerce")
            .fillna(pd.to_numeric(b["building:levels"], errors="coerce") * 4.81)
            .fillna(b["id"].map(ob.set_index("id")["ob_m"]) * OB_K))
b = b.dropna(subset=["h_m"])

pts = b.to_crs(CRS_A).representative_point().to_crs(CRS_G)
rows, cols = rasterio.transform.rowcol(glo_tr, pts.x, pts.y)
b["cell"] = [f"{r}_{c}" for r, c in zip(rows, cols, strict=True)]
b["area"] = b.to_crs(CRS_A).area
cell_h = b.groupby("cell").apply(lambda g: np.average(g["h_m"], weights=g["area"]),
                                 include_groups=False)
cell_d = pd.Series({k: diff[tuple(map(int, k.split("_")))] for k in cell_h.index})
paired = pd.DataFrame({"building_m": cell_h, "excess_m": cell_d}).dropna()
bins = pd.cut(paired["building_m"], [0, 5, 10, 20, 40, 150])
paired.groupby(bins, observed=True)["excess_m"].describe()[["count", "50%", "75%", "max"]] \
    .round(1)

# %%
fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(10, 4.2), width_ratios=[1.1, 1])
lim = np.nanpercentile(np.abs(diff), 99)
w, s, e, n = rasterio.transform.array_bounds(*glo.shape, glo_tr)
im = ax0.imshow(diff, cmap="RdBu_r", vmin=-lim, vmax=lim, extent=(w, e, s, n))
for t, ls in (("core", "-"), ("domain", "--")):
    ax0.plot(*tiers_g[t].exterior.xy, color="#303030", lw=1, ls=ls)
ax0.set(title="GLO-30 minus FABDEM (m)", xticks=[], yticks=[])
fig.colorbar(im, ax=ax0, shrink=0.8)
ax1.scatter(paired["building_m"], paired["excess_m"], s=10, color="#2a78d6", alpha=0.5,
            linewidths=0)
ax1.axhline(0, color="#8c8c8c", lw=1)
ax1.set(title="Excess against building height, per 30 m cell",
        xlabel="Mean building height in cell (m)", ylabel="GLO-30 − FABDEM (m)")
ax1.grid(color="#e6e6e6", lw=0.6)
ax1.spines[["top", "right"]].set_visible(False)
fig.tight_layout()

# %% [markdown]
# ## 3. Vertical datum
#
# GLO-30 and FABDEM heights are orthometric, above the EGM2008 geoid. Cesium's
# quantized-mesh terrain, 3D Tiles and every `Cartographic` height are ellipsoidal,
# above WGS 84. The difference is the geoid undulation N, with `h = H + N`. PROJ
# computes N from the EGM2008 grid, fetched from the PROJ CDN if not installed.

# %%
pyproj.network.set_network_enabled(True)
to_ellipsoid = pyproj.Transformer.from_crs("EPSG:4326+3855", "EPSG:4979", always_xy=True)
w, s, e, n = tiers_g["county"].bounds
lons, lats = np.meshgrid(np.linspace(w, e, 12), np.linspace(s, n, 12))
_, _, N = to_ellipsoid.transform(lons.ravel(), lats.ravel(), np.zeros(lons.size))
core_c = tiers_g["core"].centroid
print(f"N at the core centroid: {to_ellipsoid.transform(core_c.x, core_c.y, 0.0)[2]:.2f} m")
print(f"N across the county: {N.min():.2f} to {N.max():.2f} m (range {np.ptp(N):.2f} m)")

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | GLO-30 has no voids over the county. Elevation is 1,459–1,935 m over the county, 1,637–1,725 m over the domain, and 1,648–1,687 m over the core: about 39 m of relief across the CBD | No void filling is needed. The core's relief is what Step 2's flood routing runs on |
# | GLO-30 sits above bare earth by a median of 2.5 m in the core (95th percentile 7.6 m, max 15.6 m). Street and rail corridors are near zero; built blocks are raised | GLO-30 is a surface, not the ground. Against a 0.30 m impassability threshold, a 2–8 m raise on built blocks is not negligible |
# | The raise barely tracks building height: a median of 2.0 m for cells under 5 m and 2.7 m for cells with 40–150 m towers. At 30 m, GLO-30 has smoothed the towers away | Extruding buildings on GLO-30 does not double-count the towers. It does lift every base 2–8 m off the ground |
# | Both DEMs are orthometric (EGM2008). The geoid undulation N is −16.05 m at the core and −16.18 to −15.48 m across the county | Terrain must convert `h = H + N` per vertex before tiling, or the scene floats 16 m high. A single constant would be off by up to 0.35 m. The EGM2008 grid is fetched from the PROJ CDN, so it must be pinned (P2) |
#
# **Recommendation for the proposal.** Build terrain from **FABDEM**: bare earth for
# both the Cesium tileset and Step 2's hydraulics, so buildings and water stand on
# the same ground. Keep GLO-30 as the recorded upstream source. FABDEM is
# CC BY-NC-SA 4.0, which the research-only status permits; a tileset derived from
# it inherits the share-alike terms.
#
# **Still open.** Earth Engine lists GLO-30 as superseded by `GLO30_2024_1`; the AWS
# bucket may hold the older release. FABDEM (V1-2) was built from the older one, so
# they match as used here. Which release to pin is a proposal decision. The
# quantized-mesh tooling is not explored here.

# %%
