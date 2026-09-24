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
# # 01 — Road network
#
# **Step 1 · capability `geospatial-foundation` · paper §3.1**
#
# The roadmap turns OSM roads into a SUMO `.net.xml` with `netconvert`, plus GeoJSON
# for rendering. Its acceptance criterion: **no disconnected core links**. This
# notebook runs `netconvert` on the domain and measures that criterion before any
# pipeline code fixes an option set.
#
# Questions:
#
# 1. What does OSM hold for the domain: road classes, one-way tagging, signals?
# 2. What does `netconvert` make of it with the standard OSM typemap?
# 3. Which core edges fall outside the network's main strongly connected
#    component, and why?
# 4. Do the network's traffic lights correspond to OSM's signals?
#
# **SUMO version (D-2).** Eclipse SUMO 1.27.1, the current release, run from the
# official image pinned by digest. `sumolib` is pinned to the same version.
#
# **Inputs.** Raw OSM XML (`highway=*` ways and their nodes) for the domain's
# bounding box from Overpass, pinned as `data/raw/osm_domain_highways.osm.gz` with
# its snapshot time. Set `REFRESH_OSM_XML = True` to re-download.

# %% tags=["parameters"]
REFRESH_OSM_XML = False
CONFIG = "config/study_area.yaml"
SUMO_IMAGE = ("ghcr.io/eclipse-sumo/sumo@sha256:"
              "87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2")  # 1.27.1
OVERPASS = "https://overpass-api.de/api/interpreter"

# %%
import gzip
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import sumolib
from shapely.geometry import LineString

from nairobi_dt import study_area as sa

ROOT = Path.cwd() if (Path.cwd() / "config").exists() else Path.cwd().parent
os.chdir(ROOT)

cfg = sa.load_config(CONFIG)
CRS_A, CRS_G = cfg["crs"]["analysis"], cfg["crs"]["geographic"]
gpkg = ROOT / cfg["outputs"]["dir"] / cfg["outputs"]["geopackage"]
tiers = gpd.read_file(gpkg, layer="tiers").to_crs(CRS_A).set_index("tier").geometry
core, domain = tiers["core"], tiers["domain"]
print("sumolib", sumolib.__version__ if hasattr(sumolib, "__version__") else "?")

# %% [markdown]
# ## Inputs

# %%
osm_path = ROOT / "data" / "raw" / "osm_domain_highways.osm.gz"


def fetch_osm_xml(bounds: tuple[float, float, float, float], path: Path) -> None:
    w, s, e, n = bounds
    query = f'[out:xml][timeout:180];way["highway"]({s},{w},{n},{e});(._;>;);out meta;'
    req = urllib.request.Request(
        OVERPASS, urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "nairobi-dt research (github.com/Odero54/nairobi-dt)"})
    data = urllib.request.urlopen(req, timeout=300).read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data, mtime=0))


if REFRESH_OSM_XML or not osm_path.exists():
    fetch_osm_xml(gpd.GeoSeries([domain], crs=CRS_A).to_crs(CRS_G).total_bounds, osm_path)
head = gzip.open(osm_path, "rt").read(2000)
print("snapshot:", re.search(r'osm_base="([^"]+)"', head).group(1),
      f"| {osm_path.stat().st_size / 1e6:.2f} MB compressed")

# %% [markdown]
# ## 1. What OSM holds
#
# Tags are read straight from the XML, so the counts are what `netconvert` sees.

# %%
xml = gzip.open(osm_path, "rt").read()
ways = re.findall(r"<way .*?</way>", xml, flags=re.S)
tags = [dict(re.findall(r'<tag k="([^"]+)" v="([^"]*)"', w)) for w in ways]
w_df = pd.DataFrame(tags)
print(f"{len(w_df)} highway ways")
by_class = w_df.groupby("highway").agg(
    ways=("highway", "size"),
    oneway_tagged=("oneway", lambda s: s.notna().mean() * 100),
    lanes_tagged=("lanes", lambda s: s.notna().mean() * 100),
    maxspeed_tagged=("maxspeed", lambda s: s.notna().mean() * 100),
).sort_values("ways", ascending=False).round(0)
by_class.head(15)

# %%
signals = re.findall(r'<node id="(\d+)" [^>]*lat="([-\d.]+)" lon="([-\d.]+)"[^>]*>'
                     r'(?:(?!</node>).)*?k="highway" v="traffic_signals"', xml, flags=re.S)
osm_signals = gpd.GeoDataFrame(
    {"node": [s[0] for s in signals]},
    geometry=gpd.points_from_xy([float(s[2]) for s in signals],
                                [float(s[1]) for s in signals]),
    crs=CRS_G).to_crs(CRS_A)
osm_signals["in_core"] = osm_signals.within(core)
print(f"OSM traffic_signals nodes: {len(osm_signals)} "
      f"({osm_signals['in_core'].sum()} in the core)")
print("other signal tags:",
      {t: xml.count(t) for t in ('k="crossing" v="traffic_signals"',
                                 'k="traffic_signals"', 'k="crossing:signals" v="yes"')})

# %% [markdown]
# ## 2. `netconvert`
#
# The option set is the SUMO documentation's recommended import for OSM: guess
# ramps, roundabouts and signals, join clustered junctions, and simplify geometry.
# Nothing prunes disconnected parts: the point is to see them. The projection is
# given explicitly so the network shares the analysis CRS.

# %%
work = ROOT / "cache" / "sumo"
work.mkdir(parents=True, exist_ok=True)
(work / osm_path.name).write_bytes(osm_path.read_bytes())
NETCONVERT_OPTS = [
    "--type-files", "/usr/share/sumo/data/typemap/osmNetconvert.typ.xml",
    "--proj", "+proj=utm +zone=37 +south +datum=WGS84 +units=m +no_defs",
    "--geometry.remove", "--ramps.guess", "--roundabouts.guess",
    "--junctions.join", "--tls.guess-signals", "--tls.discard-simple", "--tls.join",
    "--output.street-names", "--output.original-names",
]


def netconvert(osm_file: str, out: str) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}",
           "-v", f"{work}:/work", "-w", "/work", SUMO_IMAGE,
           "netconvert", "--osm-files", osm_file, "-o", out, *NETCONVERT_OPTS]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


res = netconvert(osm_path.name, "domain.net.xml.gz")
log = res.stdout + res.stderr
warnings = pd.Series(re.findall(r"^Warning: (.*)$", log, flags=re.M))
print(log.strip().splitlines()[-1] if log.strip() else "(no output)")
print(f"{len(warnings)} warning lines")
# group by the message text before its first quoted id or number
warnings.str.replace(r"['(].*|\s\d.*", "", regex=True).str.strip().value_counts().head(10)

# %%
net = sumolib.net.readNet(str(work / "domain.net.xml.gz"), withInternal=False)
edges = [e for e in net.getEdges() if e.allows("passenger")]
print(f"{len(net.getEdges())} edges, {len(edges)} allow passenger cars; "
      f"{len(net.getNodes())} junctions; {len(net.getTrafficLights())} traffic lights")
print("location offset:", net.getLocationOffset())

# %% [markdown]
# Edge geometry is in network coordinates: the projected coordinates minus the
# location offset. Adding the offset back gives EPSG:32737.

# %%
dx, dy = net.getLocationOffset()
e_gdf = gpd.GeoDataFrame(
    {"edge": [e.getID() for e in edges], "type": [e.getType() for e in edges],
     "name": [e.getName() for e in edges], "lanes": [e.getLaneNumber() for e in edges],
     "speed_ms": [e.getSpeed() for e in edges], "length_m": [e.getLength() for e in edges]},
    geometry=[LineString([(x - dx, y - dy) for x, y in e.getShape()]) for e in edges],
    crs=CRS_A)
e_gdf["in_core"] = e_gdf.geometry.interpolate(0.5, normalized=True).within(core)
print(f"{e_gdf['in_core'].sum()} passenger edges in the core, "
      f"{e_gdf.loc[e_gdf['in_core'], 'length_m'].sum() / 1000:.1f} km")
# sanity check: the network lies where the study area is
print("network inside domain bbox (buffered 2 km):",
      e_gdf.union_all().within(domain.envelope.buffer(2000)))

# %% [markdown]
# ## 3. Connectivity
#
# A vehicle can travel from any edge to any other only inside a strongly connected
# component (SCC) of the edge graph, where an edge links to the edges it has lane
# connections to. Edges outside the largest SCC are where routes dead-end or cannot
# be reached.

# %%
g = nx.DiGraph()
g.add_nodes_from(e_gdf["edge"])
for e in edges:
    for out in e.getOutgoing():
        if out.allows("passenger"):
            g.add_edge(e.getID(), out.getID())
sccs = sorted(nx.strongly_connected_components(g), key=len, reverse=True)
main = sccs[0]
e_gdf["in_main_scc"] = e_gdf["edge"].isin(main)
print(f"{len(sccs)} SCCs; the largest holds {len(main)} of {len(e_gdf)} edges")
conn = e_gdf.groupby("in_core").agg(
    edges=("edge", "size"), outside_main=("in_main_scc", lambda s: (~s).sum()),
    km_outside=("length_m", lambda s: s[~e_gdf.loc[s.index, "in_main_scc"]].sum() / 1000))
conn

# %% [markdown]
# Why are core edges outside? Split them into edges at the domain edge (the bbox
# cut them, so they are dead ends by construction), dead ends inside the network,
# and edges that are connected at both ends yet still stranded (turn restrictions
# or one-way errors).

# %%
bad = e_gdf[e_gdf["in_core"] & ~e_gdf["in_main_scc"]].copy()


def diagnose(edge_id: str) -> str:
    e = net.getEdge(edge_id)
    outs = [o for o in e.getOutgoing() if o.allows("passenger")]
    ins = [i for i in e.getIncoming() if i.allows("passenger")]
    if not outs and not ins:
        return "isolated"
    if not outs:
        return "no exit"
    if not ins:
        return "no entry"
    return "connected but stranded"


bad["reason"] = bad["edge"].map(diagnose)
bad.groupby(["reason", "type"]).agg(edges=("edge", "size"),
                                    km=("length_m", lambda s: s.sum() / 1000)).round(2)

# %%
bad.sort_values("length_m", ascending=False)[["edge", "name", "type", "length_m", "reason"]] \
    .head(15).round(1)

# %% [markdown]
# ## 4. Traffic lights against OSM signals
#
# With `--tls.guess-signals`, netconvert places a traffic light where OSM signal
# nodes cluster around a junction, and `--tls.join` merges nearby lights into one
# controller. How do the counts compare, and how many of the network's lights sit
# within 30 m of an OSM signal?

# %%
tls_pts = []
for tl in net.getTrafficLights():
    xs, ys = zip(*[n.getCoord() for n in {c[0].getEdge().getToNode()
                                          for c in tl.getConnections()}], strict=True)
    tls_pts.append((tl.getID(), np.mean(xs) - dx, np.mean(ys) - dy))
tls = gpd.GeoDataFrame({"tls": [t[0] for t in tls_pts]},
                       geometry=gpd.points_from_xy([t[1] for t in tls_pts],
                                                   [t[2] for t in tls_pts]), crs=CRS_A)
tls["in_core"] = tls.within(core)
tls["near_osm_signal"] = tls.geometry.apply(lambda p: osm_signals.distance(p).min() <= 30
                                            if len(osm_signals) else False)
pd.DataFrame({"OSM signal nodes": osm_signals.groupby("in_core").size(),
              "netconvert TLS": tls.groupby("in_core").size(),
              "TLS within 30 m of an OSM signal": tls.groupby("in_core")["near_osm_signal"]
              .sum()}).rename(index={True: "core", False: "outside core"})

# %% [markdown]
# With almost no OSM signals to import, how many junctions would netconvert
# signalise from geometry alone? `--tls.guess` signalises a junction when the
# summed speed of its incoming roads crosses a threshold (default 250 km/h). This
# run is for comparison only.

# %%
NETCONVERT_OPTS.append("--tls.guess")
netconvert(osm_path.name, "domain_tlsguess.net.xml.gz")
NETCONVERT_OPTS.pop()
guessed = sumolib.net.readNet(str(work / "domain_tlsguess.net.xml.gz"), withInternal=False)
g_pts = gpd.GeoDataFrame(
    geometry=[gpd.points_from_xy([n.getCoord()[0] - dx], [n.getCoord()[1] - dy])[0]
              for n in guessed.getNodes() if n.getType().startswith("traffic_light")],
    crs=CRS_A)
print(f"guessed network: {len(guessed.getTrafficLights())} traffic lights, "
      f"{g_pts.within(core).sum()} signalised junction nodes in the core")

# %% [markdown]
# ## Map
#
# Passenger edges in grey; core edges outside the main SCC in red; traffic lights
# in blue; OSM signal nodes in black.

# %%
from lonboard import Map, PathLayer, PolygonLayer, ScatterplotLayer

Map(layers=[
    PathLayer.from_geopandas(e_gdf[["edge", "geometry"]].to_crs(CRS_G),
                             get_color=[170, 170, 170], width_min_pixels=1),
    PathLayer.from_geopandas(bad[["edge", "reason", "geometry"]].to_crs(CRS_G),
                             get_color=[215, 48, 31], width_min_pixels=3),
    ScatterplotLayer.from_geopandas(tls[["tls", "geometry"]].to_crs(CRS_G),
                                    get_fill_color=[42, 120, 214], radius_min_pixels=5),
    ScatterplotLayer.from_geopandas(osm_signals[["node", "geometry"]].to_crs(CRS_G),
                                    get_fill_color=[20, 20, 20], radius_min_pixels=3),
    PolygonLayer.from_geopandas(gpd.GeoDataFrame(geometry=[core], crs=CRS_A).to_crs(CRS_G),
                                filled=False, get_line_color=[215, 48, 31],
                                line_width_min_pixels=2),
])

# %% [markdown]
# ## Findings
#
# | Finding | Consequence for the spec |
# |---|---|
# | SUMO 1.27.1 from the pinned image converts the domain (3,412 highway ways) into 4,135 passenger edges and 4,394 junctions without errors. netconvert caps repeated warnings at five per type; none block the build | D-2 resolved: Eclipse SUMO 1.27.1, image pinned by digest, `sumolib` at the same version. The image is 1.5 GB, a cost for CI |
# | Of 949 core passenger edges (44.8 km), one is outside the main strongly connected component: a 6.7 m `tertiary_link` with no entry. Outside the core, 161 edges (31.7 km) are stranded, mostly where the bounding box cuts roads | The acceptance criterion is testable and nearly met as is. The spec requires every core passenger edge to lie in the main SCC, with stranded edges removed and logged, not silently pruned |
# | OSM holds 13 `traffic_signals` nodes in the domain and **none in the core**. netconvert imports 4 traffic lights, all outside the core | OSM cannot supply the signal inventory the control layer (Step 6) acts on (P1) |
# | Guessing from geometry (`--tls.guess`) gives 30 traffic lights, 18 signalised junctions in the core | Guessed signals are estimates: each traffic light carries its source, observed (OSM) or guessed (netconvert), per P4 |
# | One-way tagging is good on major roads (secondary 74%, trunk 88%) and poor on residential (20%); `lanes` is sparse below trunk | Lane counts and speeds mostly come from the typemap defaults. That is a calibration concern for Step 3, stated here |
#
# **For the proposal.** The signal inventory is the biggest gap this notebook found.
# Options beyond `--tls.guess`: Mapillary's machine-detected traffic-light map
# features (free API), or an inventory from Nairobi City County / KURA if one can be
# obtained. Either becomes a rung above the geometric guess, as with building
# heights.

# %%
