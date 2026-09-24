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
# # 01 — Terrain tiling
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# `01-terrain-inspection` chose FABDEM as the terrain source and found that heights
# must move from the EGM2008 geoid to the WGS 84 ellipsoid (N ≈ −16 m). This
# notebook turns that raster into a Cesium quantized-mesh tileset and tests the
# roadmap's acceptance criterion: **the terrain tileset loads in a bare Cesium
# page**.
#
# Toolchain, all Python and pip-installable: `morecantile` for Cesium's geographic
# tiling scheme, `pydelatin` for the triangulated irregular network (TIN), and
# `quantized-mesh-encoder` for the tile format with vertex normals.
#
# Questions:
#
# 1. Which zoom levels, and what mesh error, does a 30 m source support?
# 2. What does the error bound cost in vertices and bytes?
# 3. Does the pyramid round-trip: do decoded tiles, and Cesium itself, return the
#    source heights?
# 4. What would the county tileset cost?
#
# **Input.** The pinned FABDEM domain clip (`data/raw/fabdem_domain.tif`). The
# tileset is written to `cache/terrain/domain/`; artefacts reach MinIO in the
# pipeline, not here.

# %% tags=["parameters"]
CONFIG = "config/study_area.yaml"
MAX_LEVEL = 12
MAX_ERROR_CAP_M = 1.0
TILE = 256  # cells per tile edge; grids carry TILE + 1 points so neighbours share edges
CESIUM_VERSION = "1.145.0"
BROWSER_CHECK = True

# %%
import http.server
import io
import json
import os
import shutil
import struct
import subprocess
import threading
from functools import partial
from pathlib import Path

import geopandas as gpd
import matplotlib.tri as mtri
import morecantile
import numpy as np
import pandas as pd
import pyproj
import quantized_mesh_encoder as qme
import rasterio
from pydelatin import Delatin
from pydelatin.util import rescale_positions
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

cfg = sa.load_config(CONFIG)
CRS_G = cfg["crs"]["geographic"]
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers_g = gpd.read_file(gpkg, layer="tiers").to_crs(CRS_G).set_index("tier").geometry

tms = morecantile.tms.get("WGS1984Quad")  # Cesium's geographic scheme: 2 x 1 tiles at level 0
OUT = ROOT / "cache" / "terrain" / "domain"

with rasterio.open(ROOT / "data" / "raw" / "fabdem_domain.tif") as src:
    dem = src.read(1).astype("float32")
    dem_tr, dem_crs, dem_bounds = src.transform, src.crs, src.bounds
dem[(dem < 0) | ~np.isfinite(dem)] = np.nan
print(f"FABDEM {dem.shape}, {np.isnan(dem).mean():.1%} nodata, bounds {tuple(dem_bounds)}")

# %% [markdown]
# ## 1. Levels and error budget
#
# At level z a tile spans 180° / 2^z of longitude over `TILE` cells. The first level
# finer than the 1″ (≈ 31 m) source is the deepest worth building; beyond it Cesium
# upsamples from the parent tile.
#
# Cesium decides when to refine a tile from its own per-level geometric error,
# `a · 2π · 0.25 / (65 · 2) / 2^z` for the WGS 84 semi-major axis `a`. A mesh whose
# error exceeds that would pop visibly as tiles refine, so the TIN error bound per
# level is the smaller of Cesium's value and `MAX_ERROR_CAP_M`.

# %%
A = qme.WGS84.a


def cesium_level_error(z: int) -> float:
    return A * 2 * np.pi * 0.25 / (65 * 2) / 2**z


def max_error(z: int) -> float:
    return min(cesium_level_error(z), MAX_ERROR_CAP_M)


levels = pd.DataFrame({"level": range(8, 14)})
levels["cell_m"] = levels["level"].map(lambda z: 180 / 2**z / TILE * 111_320)
levels["cesium_error_m"] = levels["level"].map(cesium_level_error)
levels["tin_error_m"] = levels["level"].map(max_error)
levels.round(2)

# %% [markdown]
# Level 12 is the first with cells (19 m) finer than the source, so `MAX_LEVEL = 12`.
# Cesium's budget (19 m at level 12) is far looser than the source's own accuracy,
# so the cap decides at every level built. The cap is therefore a research choice,
# not a rendering constraint; section 2 prices it.

# %% [markdown]
# ## Tile construction
#
# Each tile samples the DEM on a (TILE + 1)² grid whose points sit on the tile's
# edges, so adjacent tiles share edge heights. Cells outside the DEM are filled
# with its median: at low levels a tile spans a hemisphere and holds a few DEM
# cells at most.
#
# Heights are made ellipsoidal before meshing. N is computed by PROJ on a coarse
# grid over the DEM once and resampled onto every tile like the DEM. Querying PROJ
# per vertex fails at low levels, where vertices reach the poles and the remote
# EGM2008 grid read errors out.

# %%
pyproj.network.set_network_enabled(True)
to_ellipsoid = pyproj.Transformer.from_crs("EPSG:4326+3855", "EPSG:4979", always_xy=True)
pad = 0.01
gx = np.linspace(dem_bounds.left - pad, dem_bounds.right + pad, 20)
gy = np.linspace(dem_bounds.top + pad, dem_bounds.bottom - pad, 20)
lon_g, lat_g = np.meshgrid(gx, gy)
_, _, n_flat = to_ellipsoid.transform(lon_g.ravel(), lat_g.ravel(), np.zeros(lon_g.size))
geoid_n = n_flat.reshape(lon_g.shape).astype("float32")
geoid_tr = from_origin(gx[0] - (gx[1] - gx[0]) / 2, gy[0] + (gy[0] - gy[1]) / 2,
                       gx[1] - gx[0], gy[0] - gy[1])
FILL_H, FILL_N = float(np.nanmedian(dem)), float(np.median(geoid_n))
print(f"N over the DEM: {geoid_n.min():.2f} to {geoid_n.max():.2f} m; fill H = {FILL_H:.1f} m")


def resample(arr, tr, bounds, fill: float) -> np.ndarray:
    w, s, e, n = bounds
    dx, dy = (e - w) / TILE, (n - s) / TILE
    out = np.full((TILE + 1, TILE + 1), np.nan, "float32")
    reproject(arr, out, src_transform=tr, src_crs=dem_crs, src_nodata=np.nan,
              dst_transform=from_origin(w - dx / 2, n + dy / 2, dx, dy), dst_crs=CRS_G,
              dst_nodata=np.nan, resampling=Resampling.bilinear)
    return np.where(np.isfinite(out), out, fill)


def tile_heights(t: morecantile.Tile) -> np.ndarray:
    bounds = tuple(tms.xy_bounds(t))
    return (resample(dem, dem_tr, bounds, FILL_H)
            + resample(geoid_n, geoid_tr, bounds, FILL_N))


# %% [markdown]
# **Orientation.** `pydelatin` puts vertex y = 0 at the *last* row of the input
# array: its vertices already have a bottom-left origin. `rescale_positions`
# therefore needs `flip_y=False` for a north-up grid, although its docstring
# suggests flipping for image arrays. Flipping mirrors every tile north–south; the
# first build did exactly that and Cesium returned heights from the wrong place.
# The check below guards it.

# %%
ramp = np.repeat(np.arange(5, dtype="float32")[:, None] * 10, 5, axis=1)  # row index × 10
v = Delatin(ramp, max_error=0.01).vertices
assert v[v[:, 1] == 0, 2].max() == 40, "pydelatin y origin changed: revisit flip_y"


def build_tile(t: morecantile.Tile, err: float) -> tuple[bytes, dict]:
    grid = tile_heights(t)
    tin = Delatin(grid, max_error=err)
    bounds = tuple(tms.xy_bounds(t))
    pos = rescale_positions(tin.vertices, bounds, flip_y=False).astype("float64")
    buf = io.BytesIO()
    qme.encode(buf, pos, tin.triangles, bounds=bounds,
               extensions=[qme.VertexNormalsExtension(indices=tin.triangles, positions=pos)])
    return buf.getvalue(), {"vertices": len(pos), "triangles": len(tin.triangles),
                            "max_error_m": tin.error, "tin": tin, "grid": grid}


# %% [markdown]
# ## 2. What the error bound costs
#
# The densest level-12 tile, meshed at a range of error bounds. RMSE is measured
# against every grid point, by linear interpolation over the mesh.

# %%
def rmse(tin: Delatin, grid: np.ndarray) -> float:
    vx, vy, vz = tin.vertices.T
    interp = mtri.LinearTriInterpolator(mtri.Triangulation(vx, vy, tin.triangles), vz)
    yy, xx = np.mgrid[0:TILE + 1, 0:TILE + 1]
    est = interp(xx, TILE - yy)  # grid row r is vertex y = TILE - r
    return float(np.sqrt(np.nanmean((est - grid) ** 2)))


dense = max(tms.tiles(*dem_bounds, [MAX_LEVEL]),
            key=lambda t: np.isfinite(resample(dem, dem_tr, tuple(tms.xy_bounds(t)),
                                               np.nan)).sum())
trade = []
for err in (0.25, 0.5, 1.0, 2.0, 3.0):
    data, info = build_tile(dense, err)
    trade.append({"max_error_m": err, "vertices": info["vertices"], "kB": len(data) / 1e3,
                  "rmse_m": rmse(info["tin"], info["grid"])})
pd.DataFrame(trade).round(2)

# %% [markdown]
# ## 3. Build the pyramid

# %%
if OUT.exists():
    shutil.rmtree(OUT)
rows, available = [], []
for z in range(MAX_LEVEL + 1):
    tiles = list(tms.tiles(*dem_bounds, [z]))
    ys = []
    for t in tiles:
        y_tms = 2**z - 1 - t.y  # Cesium's default "tms" scheme counts y from the south
        data, info = build_tile(t, max_error(z))
        path = OUT / str(z) / str(t.x) / f"{y_tms}.terrain"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append({"level": z, "x": t.x, "y": y_tms, "vertices": info["vertices"],
                     "bytes": len(data), "tin_error_m": info["max_error_m"]})
        ys.append(y_tms)
    xs = [t.x for t in tiles]
    available.append([{"startX": min(xs), "endX": max(xs), "startY": min(ys), "endY": max(ys)}])

layer = {"tilejson": "2.1.0", "name": "nairobi-dt FABDEM terrain",
         "attribution": "FABDEM V1-2 (University of Bristol, CC BY-NC-SA 4.0), "
                        "from Copernicus GLO-30",
         "format": "quantized-mesh-1.0", "version": "1.0.0", "scheme": "tms",
         "projection": "EPSG:4326", "tiles": ["{z}/{x}/{y}.terrain?v={version}"],
         "extensions": ["octvertexnormals"], "bounds": [-180, -90, 180, 90],
         "available": available}
(OUT / "layer.json").write_text(json.dumps(layer, indent=1))
built = pd.DataFrame(rows)
built.groupby("level").agg(tiles=("x", "size"), vertices=("vertices", "sum"),
                           kB=("bytes", lambda b: b.sum() / 1e3),
                           tin_error_m=("tin_error_m", "max")).round(2).T

# %% [markdown]
# ## 4. Round trip
#
# Decode the written tiles (header, then zig-zag delta-encoded u, v, height) and
# compare heights at check points with the source: FABDEM plus N, taken directly.

# %%
def decode(data: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    h_min, h_max = struct.unpack_from("<ff", data, 24)
    n = struct.unpack_from("<I", data, 88)[0]
    raw = np.frombuffer(data, dtype="<u2", count=3 * n, offset=92).astype(np.int64)
    u, v, h = (np.cumsum((a >> 1) ^ -(a & 1)) / 32767 for a in raw.reshape(3, n))
    tri_start = 92 + 6 * n  # 16-bit indices need no padding (vertex count < 65536)
    n_tri = struct.unpack_from("<I", data, tri_start)[0]
    idx = np.frombuffer(data, dtype="<u2", count=3 * n_tri, offset=tri_start + 4).astype(int)
    hw, tris = 0, []
    for c in idx:  # high-water-mark decoding
        tris.append((hw - c) % 65536)  # codes wrap in uint16, as in Cesium
        if c == 0:
            hw += 1
    return u, v, h_min + h * (h_max - h_min), np.array(tris).reshape(-1, 3)


CHECK = [(36.822, -1.285), (36.815, -1.295), (36.835, -1.275), (36.8267, -1.2833)]
with rasterio.open(ROOT / "data" / "raw" / "fabdem_domain.tif") as src:
    source_h = [float(next(src.sample([p]))[0]) for p in CHECK]
expected = [to_ellipsoid.transform(x, y, h)[2] for (x, y), h in zip(CHECK, source_h,
                                                                     strict=True)]
decoded = []
for (x, y) in CHECK:
    t = tms.tile(x, y, MAX_LEVEL)
    w, s, e, n = tms.xy_bounds(t)
    u, v, h, tri = decode((OUT / str(t.z) / str(t.x) / f"{2**t.z - 1 - t.y}.terrain")
                          .read_bytes())
    interp = mtri.LinearTriInterpolator(mtri.Triangulation(u, v, tri), h)
    decoded.append(float(interp((x - w) / (e - w), (y - s) / (n - s))))
check = pd.DataFrame({"lon": [p[0] for p in CHECK], "lat": [p[1] for p in CHECK],
                      "FABDEM_H_m": source_h, "expected_h_m": expected,
                      "decoded_h_m": decoded})
check["decoded_minus_expected_m"] = check["decoded_h_m"] - check["expected_h_m"]
check.round(2)

# %% [markdown]
# ### In a bare Cesium page
#
# The acceptance test: CesiumJS, pinned, loads `layer.json` through
# `CesiumTerrainProvider` and samples the most detailed tiles at the check points.
# Headless Chrome runs the page against a local server; sampling needs no WebGL.

# %%
page = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/cesium@CESIUM_VERSION/Build/Cesium/Cesium.js"></script>
</head><body><pre id="out">pending</pre><script>
(async () => {
  const out = document.getElementById("out");
  try {
    const p = await Cesium.CesiumTerrainProvider.fromUrl(".", {requestVertexNormals: true});
    const pts = CHECK_POINTS.map(([x, y]) => Cesium.Cartographic.fromDegrees(x, y));
    const r = await Cesium.sampleTerrainMostDetailed(p, pts);
    out.textContent = JSON.stringify({normals: p.hasVertexNormals,
                                      heights: r.map(c => c.height)});
  } catch (e) { out.textContent = JSON.stringify({error: String(e)}); }
})();
</script></body></html>"""
page = page.replace("CESIUM_VERSION", CESIUM_VERSION).replace("CHECK_POINTS", json.dumps(CHECK))
(OUT / "check.html").write_text(page)

chrome = shutil.which("google-chrome") or shutil.which("chromium")
if BROWSER_CHECK and chrome:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

    handler = partial(Quiet, directory=str(OUT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dom = subprocess.run([chrome, "--headless=new", "--disable-gpu",
                          "--virtual-time-budget=30000", "--dump-dom",
                          f"http://127.0.0.1:{server.server_port}/check.html"],
                         capture_output=True, text=True, timeout=120).stdout
    server.shutdown()
    result = json.loads(dom.split('<pre id="out">')[1].split("</pre>")[0])
    print(result.get("error") or f"vertex normals: {result['normals']}")
    check["cesium_h_m"] = result.get("heights")
    check["cesium_minus_expected_m"] = check["cesium_h_m"] - check["expected_h_m"]
else:
    print("browser check skipped")
check.round(2)

# %% [markdown]
# ## 5. The county tileset
#
# The domain is 10 km²; the ingestion extent is the county, 696 km². Tile counts
# over the county's bounding box, with bytes estimated from this build's mean tile
# size per level (an overestimate at low levels, where domain tiles are mostly
# fill):

# %%
mean_bytes = built.groupby("level")["bytes"].mean()
county = pd.DataFrame({"level": range(MAX_LEVEL + 1)})
county["tiles"] = county["level"].map(
    lambda z: len(list(tms.tiles(*tiers_g["county"].bounds, [z]))))
county["MB_est"] = county["tiles"] * county["level"].map(mean_bytes) / 1e6
print(f"county: {county['tiles'].sum()} tiles, about {county['MB_est'].sum():.0f} MB")
county.set_index("level").T.round(2)

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | A pip-only toolchain (`morecantile`, `pydelatin`, `quantized-mesh-encoder`) produces quantized-mesh tiles with vertex normals that CesiumJS 1.145.0 loads from a bare page, without Cesium ion | The acceptance criterion is met and testable in CI with headless Chrome. `stack.md` §8 ("GDAL → quantized-mesh") is amended to this toolchain; no terrain-builder container is needed |
# | Levels 0–12; level 12 is the first finer (19 m) than the 1″ source. Cesium's per-level error budget (19 m at level 12) never binds, so the TIN error bound is a choice | The bound is configuration, with its RMSE cited |
# | On the densest tile, a 1 m bound gives RMSE 0.20 m in 33 kB; 0.25 m gives RMSE 0.05 m in 126 kB | Against a 0.30 m impassability threshold, and flood depths draped on this surface in Step 8, 0.25 m is worth its cost |
# | Decoded tiles and Cesium return identical heights, within ±0.6 m of FABDEM + N at a 1 m bound | The round trip is exact apart from the TIN bound, and becomes a test |
# | The county needs 128 tiles, about 2 MB at 1 m (roughly four times that at 0.25 m) | Size is no constraint; the tileset is one small, content-addressed MinIO artefact |
# | Levels 0–6 are almost all fill: one tile spans a hemisphere, so the county sits on a plateau at the fill height | The viewer constrains the camera to the study area, or low levels come from a coarse global DEM. A viewer decision for Step 8, recorded here |
#
# **Pitfalls found, each now guarded in code:**
#
# - `pydelatin` vertices have a bottom-left origin: `flip_y=False` (asserted above).
#   The first build was mirrored north–south.
# - Geoid heights come from a coarse N grid resampled per tile; per-vertex PROJ
#   lookups fail near the poles at low levels.
# - `quantized-mesh-encoder` keeps the TIN's vertex order, so high-water-mark codes
#   wrap in uint16. Cesium decodes them correctly; any decoder of ours must wrap too.
#
# FABDEM is CC BY-NC-SA 4.0, so the tileset is too; `layer.json` carries the
# attribution.

# %%
