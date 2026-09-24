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
# # 01 — Imagery composite
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# The roadmap's imagery artefact is a Sentinel-2 cloud-free composite, stored as a
# Cloud-Optimised GeoTIFF (COG) in MinIO and served by TiTiler. Nairobi has two
# rainy seasons and an overcast cool season, so the composite is mostly a
# cloud-masking problem.
#
# Questions:
#
# 1. Which months give usable scenes?
# 2. How do two cloud masks compare over the domain: the scene classification (SCL)
#    shipped with Sentinel-2 L2A, from the Earth Search catalogue on AWS (no
#    account), and Google's Cloud Score+ in Earth Engine?
# 3. How many clear observations back each composite pixel? That count ships with
#    the composite, so the imagery carries its own confidence (P4).
# 4. What does the COG cost, and does it serve tiles the way TiTiler will?
#
# **Inputs.** Scene metadata is searched live (fast). The two domain composites are
# slow to build (Earth Search reads every scene's windows) and are pinned in
# `data/raw/`; set `REFRESH_STAC` or `REFRESH_EE` to rebuild them.

# %% tags=["parameters"]
REFRESH_STAC = False
REFRESH_EE = False
EE_PROJECT = "parallelprocessing-433914"
CONFIG = "config/study_area.yaml"
YEARS = (2024, 2025)
WINDOWS = [("2024-01-01", "2024-02-29"), ("2024-09-01", "2024-09-30"),
           ("2025-01-01", "2025-02-28"), ("2025-09-01", "2025-09-30")]
MAX_SCENE_CLOUD = 80  # % per scene, a pre-filter only; pixels are masked individually
SCL_CLEAR = [4, 5, 6]  # vegetation, not vegetated, water
CS_CLEAR = 0.6  # Cloud Score+ cs_cdf threshold, Google's recommended default
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"

# %%
import os
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pystac_client
import rasterio
import rasterio.features
from rasterio.transform import from_origin
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles
from rio_tiler.io import Reader

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

cfg = sa.load_config(CONFIG)
CRS_A, CRS_G = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers = gpd.read_file(gpkg, layer="tiers").set_index("tier").geometry
tiers_g, tiers_a = tiers.to_crs(CRS_G), tiers.to_crs(CRS_A)

# Both composites share one 10 m grid in the analysis CRS, snapped to 10 m.
w, s, e, n = tiers_a["domain"].bounds
w, s = np.floor(w / 10) * 10, np.floor(s / 10) * 10
e, n = np.ceil(e / 10) * 10, np.ceil(n / 10) * 10
GRID_TR = from_origin(w, n, 10, 10)
GRID_SHAPE = (int((n - s) / 10), int((e - w) / 10))
print(f"domain grid {GRID_SHAPE} at 10 m, {CRS_A}")

# %% [markdown]
# ## 1. Season
#
# Scene-level cloud cover over the county, per calendar month, for the years
# searched. A scene covers far more than the county, so this is a guide to season,
# not a pixel count.

# %%
catalog = pystac_client.Client.open(EARTH_SEARCH)
meta = pd.DataFrame([
    {"id": i.id, "date": i.datetime, "cloud": i.properties["eo:cloud_cover"],
     "tile": i.properties["grid:code"], "baseline": i.properties["s2:processing_baseline"]}
    for i in catalog.search(collections=["sentinel-2-l2a"], bbox=list(tiers_g["county"].bounds),
                            datetime=f"{YEARS[0]}-01-01/{YEARS[-1]}-12-31").items()])
print(f"{len(meta)} scenes; tiles {meta['tile'].value_counts().to_dict()}")
monthly = meta.groupby(meta["date"].dt.month)["cloud"].agg(
    scenes="size", median_cloud_pct="median",
    under_20_pct=lambda c: (c < 20).sum()).T
monthly.round(0)

# %% [markdown]
# The county straddles two MGRS tiles in different UTM zones (36MZD, 37MBU), so
# each acquisition appears twice and must be merged by solar day.
#
# February and September are clearest; June to August, the cool overcast season,
# are worst. The composite windows are January–February and September of both
# years.

# %% [markdown]
# ## 2. Two composites of the domain
#
# **Earth Search + SCL.** L2A COGs on AWS. A pixel is clear when SCL is vegetation,
# not-vegetated or water.
#
# **Earth Engine + Cloud Score+.** `COPERNICUS/S2_SR_HARMONIZED`, masked where Cloud
# Score+ `cs_cdf` is below 0.6.
#
# **Scale: trust the pixels, not the metadata.** Earth Search items carry
# `earthsearch:boa_offset_applied: false` and declare `scale 0.0001, offset −0.1`,
# which says reflectance is `DN × 0.0001 − 0.1`. The pixels disagree. For
# S2C_37MBU_20250226 (baseline 05.11) at 36.822° E, 1.285° S, red is 1108 on Earth
# Search, 1108 in Earth Engine's harmonised collection, and 2108 in its deprecated
# un-harmonised `COPERNICUS/S2_SR`. Earth Search pixels are therefore already
# harmonised: reflectance is `DN × 0.0001` in both sources. Applying the declared
# offset subtracts 0.1 a second time; the first build of this notebook did, and every
# band came out exactly 0.1 low.
#
# Each composite is the per-pixel median of clear observations, with the count of
# clear observations as a fifth band.

# %%
BANDS = ["red", "green", "blue", "nir"]
stac_path = ROOT / "data" / "raw" / "s2_domain_scl_median.tif"
ee_path = ROOT / "data" / "raw" / "s2_domain_csplus_median.tif"


def write_grid(path: Path, arr: np.ndarray, descriptions: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2],
                       count=arr.shape[0], dtype="int16", crs=CRS_A, transform=GRID_TR,
                       nodata=-32768, compress="deflate", predictor=2) as dst:
        dst.write(arr.astype("int16"))
        dst.descriptions = tuple(descriptions)


def build_stac_composite(path: Path) -> None:
    from odc.stac import load

    items = []
    for a, b in WINDOWS:
        items += catalog.search(collections=["sentinel-2-l2a"],
                                bbox=list(tiers_g["domain"].bounds), datetime=f"{a}/{b}",
                                query={"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}}).item_collection()
    ds = load(items, bands=[*BANDS, "scl"], crs=CRS_A, resolution=10, groupby="solar_day",
              x=(w, e), y=(n, s), pool=16)
    clear = ds["scl"].isin(SCL_CLEAR) & (ds["red"] > 0)
    # pixels are already harmonised (see above): DN == reflectance x 10000
    stack = [ds[b].where(clear).astype("float32") for b in BANDS]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
        med = np.stack([s_.median("time").values for s_ in stack])
    arr = np.concatenate([np.nan_to_num(med, nan=-32768), clear.sum("time").values[None]])
    write_grid(path, arr[:, :GRID_SHAPE[0], :GRID_SHAPE[1]], [*BANDS, "clear_count"])
    print(f"Earth Search: {len(items)} items, {ds.sizes['time']} solar days")


def build_ee_composite(path: Path) -> None:
    import urllib.request

    import ee

    ee.Initialize(project=EE_PROJECT)
    region = ee.Geometry.Rectangle([w, s, e, n], CRS_A, False)
    dates = ee.Filter.Or(*[ee.Filter.date(a, str(pd.Timestamp(b) + pd.Timedelta(days=1))[:10])
                           for a, b in WINDOWS])
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filter(dates)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_SCENE_CLOUD))
          .linkCollection(ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"),
                          ["cs_cdf"]))
    masked = s2.map(lambda i: i.updateMask(i.select("cs_cdf").gte(CS_CLEAR))
                    .select(["B4", "B3", "B2", "B8"], BANDS))
    comp = masked.median().addBands(masked.select("red").count().rename("clear_count"))
    url = comp.unmask(-32768).toInt16().getDownloadURL({
        "region": region, "crs": CRS_A, "crs_transform": list(GRID_TR)[:6],
        "format": "GEO_TIFF"})
    tmp = path.with_suffix(".download.tif")
    urllib.request.urlretrieve(url, tmp)
    with rasterio.open(tmp) as src:
        write_grid(path, src.read(), [*BANDS, "clear_count"])
    tmp.unlink()
    print(f"Earth Engine: {s2.size().getInfo()} scenes")


if REFRESH_STAC or not stac_path.exists():
    build_stac_composite(stac_path)
if REFRESH_EE or not ee_path.exists():
    build_ee_composite(ee_path)


def read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(path) as src:
        assert src.transform.almost_equals(GRID_TR) and src.shape == GRID_SHAPE
        a = src.read().astype("float32")
    refl = np.where(a[:4] == -32768, np.nan, a[:4] / 10000)
    return refl, a[4]


comps = {"SCL (Earth Search)": read(stac_path), "Cloud Score+ (Earth Engine)": read(ee_path)}

# %% [markdown]
# ## 3. Clear observations per pixel

# %%
in_core = ~rasterio.features.geometry_mask([tiers_a["core"]], GRID_SHAPE, GRID_TR)
rows = []
for name, (_refl, count) in comps.items():
    for area, m in (("domain", np.ones(GRID_SHAPE, bool)), ("core", in_core)):
        c = count[m]
        rows.append({"mask": name, "area": area, "min": c.min(), "p5": np.percentile(c, 5),
                     "median": np.median(c), "pixels < 5 clear %": 100 * (c < 5).mean(),
                     "no clear %": 100 * (c == 0).mean()})
pd.DataFrame(rows).round(1)

# %% [markdown]
# Where does SCL find fewer clear observations than Cloud Score+? Bright roofs and
# paving are a known SCL failure: classified as cloud, they are masked in every
# scene.

# %%
(scl_refl, scl_n), (cs_refl, cs_n) = comps.values()
lost = cs_n - scl_n
print(f"median clear observations lost to SCL: domain {np.median(lost):.0f}, "
      f"core {np.median(lost[in_core]):.0f}")
bright = np.nanmean(cs_refl[:3], axis=0)
print("mean visible reflectance vs clear observations lost (Spearman):",
      round(pd.Series(bright.ravel()).rank().corr(pd.Series(lost.ravel()).rank()), 2))

# %% [markdown]
# Agreement between the two composites where both have a value, and a residual-
# cloud check: haze and cloud edges raise blue reflectance.

# %%
both = np.isfinite(scl_refl[0]) & np.isfinite(cs_refl[0])
pd.DataFrame({
    b: {"median SCL − CS+": np.median((scl_refl[i] - cs_refl[i])[both]),
        "RMSD": np.sqrt(np.mean((scl_refl[i] - cs_refl[i])[both] ** 2)),
        "SCL blue > 0.15 %": 100 * np.mean(scl_refl[2][both] > 0.15) if b == "blue" else np.nan,
        "CS+ blue > 0.15 %": 100 * np.mean(cs_refl[2][both] > 0.15) if b == "blue" else np.nan}
    for i, b in enumerate(BANDS)}).round(4)


# %%
def rgb(refl: np.ndarray) -> np.ndarray:
    img = np.moveaxis(refl[:3], 0, -1)
    return np.clip(img / 0.25, 0, 1) ** (1 / 1.6)  # display stretch only


fig, axes = plt.subplots(1, 4, figsize=(15, 4.2), width_ratios=[1, 1, 1, 1])
extent = (w, e, s, n)
for ax, (name, (refl, _)) in zip(axes[:2], comps.items(), strict=True):
    ax.imshow(np.nan_to_num(rgb(refl)), extent=extent)
    ax.set_title(name)
vmax = max(scl_n.max(), cs_n.max())
for ax, (name, (_, count)) in zip(axes[2:], comps.items(), strict=True):
    im = ax.imshow(count, extent=extent, cmap="Blues", vmin=0, vmax=vmax)
    ax.set_title(f"Clear obs.: {name.split(' (')[0]}")
fig.colorbar(im, ax=axes[2:], shrink=0.8)
for ax in axes:
    ax.plot(*tiers_a["core"].exterior.xy, color="#d7301f", lw=1)
    ax.set(xticks=[], yticks=[])

# %% [markdown]
# ## 4. COG and tile serving
#
# The chosen composite as a COG: 512-pixel internal tiles, DEFLATE, overviews, the
# clear-observation count kept as a band. `rio-tiler`, the library TiTiler is built
# on, then reads a Web Mercator tile over the CBD, the request TiTiler will serve.

# %%
cog_path = ROOT / "cache" / "imagery" / "s2_domain_cog.tif"
cog_path.parent.mkdir(parents=True, exist_ok=True)
profile = cog_profiles.get("deflate") | {"blockxsize": 512, "blockysize": 512}
cog_translate(ee_path, cog_path, profile, overview_level=3, quiet=True)
valid, errors, warns = cog_validate(cog_path)
print(f"valid COG: {valid}; errors {errors}; warnings {warns}; "
      f"{cog_path.stat().st_size / 1e6:.2f} MB")

with Reader(cog_path) as cog:
    tile_x, tile_y, zoom = cog.tms.tile(36.822, -1.285, 16)
    img = cog.tile(tile_x, tile_y, zoom, indexes=(1, 2, 3))
print(f"tile {zoom}/{tile_x}/{tile_y}: {img.data.shape}, "
      f"{(img.mask > 0).mean():.0%} valid, red median {np.median(img.data[0]) / 10000:.3f}")

# %% [markdown]
# County size, scaled from the domain by area (5 bands, int16, DEFLATE):

# %%
county_scale = tiers_a["county"].area / (GRID_SHAPE[0] * GRID_SHAPE[1] * 100)
print(f"county ≈ {county_scale:.0f} × domain grid ≈ "
      f"{county_scale * cog_path.stat().st_size / 1e6:.0f} MB")

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | February (median scene cloud 6%) and September (18%) are clearest; June–August are worst (52–70%). Two dry-season windows over two years give 35 solar days | The composite window is configuration: January–February and September, stated with this table as its source |
# | Both masks leave a median of 22–23 clear observations per pixel. Cloud Score+ never drops below 18; SCL leaves pinholes in the core (minimum 2, a few pixels under 5), where it masks the same bright surfaces in most scenes | Every composite pixel carries its clear-observation count as a band, so a thin pixel is visible, not hidden (P4) |
# | The composites agree to within RMSD 0.003–0.006 reflectance in every band, with a median difference under 0.001. About 3% of pixels have blue reflectance above 0.15 in both, so that is bright roofing, not residual cloud | Over this window the mask choice barely changes the image. The choice turns on access and cost, not quality |
# | Earth Search metadata declares a −0.1 offset that its pixels have already had removed. Following the metadata puts every band 0.1 low | Scale handling is verified against a reference scene in a test, not read from item metadata |
# | The composite as a COG (512 px tiles, DEFLATE, overviews, 5 bands int16) validates; `rio-tiler` serves a level-16 Web Mercator tile over the CBD. Domain 1.4 MB; county ≈ 70 MB | One county COG is a modest MinIO artefact. TiTiler itself is exercised when the compose stack lands |
#
# **Source, for the proposal.** Earth Search with SCL needs no account and is fully
# open (P2), but reads every scene: about 5 minutes for the domain, so hours for the
# county. Earth Engine with Cloud Score+ took seconds for the domain, has no
# pinholes, but needs an account, is free only for non-commercial use, and a county
# composite exceeds its direct-download limit (it needs an export). Recommendation:
# **Earth Search + SCL as the pipeline source**, run once as a batch job whose output
# is pinned, with the Cloud Score+ composite kept here as its cross-check.
#
# **CRS.** The COG keeps the native 10 m UTM grid (EPSG:32737). The CRS policy says
# EPSG:4326 for storage; resampling imagery to degrees would blur it for no gain,
# since TiTiler reprojects to Web Mercator on request. The proposal should state
# rasters as an exception: stored in the analysis CRS, served in EPSG:3857.
#
# Sentinel-2 data: Copernicus, free and open. Cloud Score+: Google, CC BY 4.0.

# %%
