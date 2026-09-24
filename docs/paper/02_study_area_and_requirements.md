# 2. Study Area and System Requirements

## 2.1 Study area

Nairobi County, with a population of approximately 4.4 million at the 2019 census [1], experiences a bimodal rainfall regime, with long rains from March to May and short rains from October to December. Intense convective storms during both seasons overwhelm drainage capacity in the central business district (CBD), where impervious cover is near-total and the road network carries the city's highest traffic densities. Flash flooding and congestion therefore coincide in space and time, which makes the CBD the appropriate primary site for evaluating concurrent hazard response.

The study area is defined as three nested spatial tiers, summarised in Table 1 and shown in Fig. 1.

**Table 1.** Spatial tiers of the study area.

| Tier | Extent | Role | Area (km²) |
|---|---|---|---|
| Core | Nairobi CBD | All KPIs are computed here | 1.763 |
| Domain | Core + 1 km buffer | Simulated but not scored, to remove boundary effects | 10.232 |
| County | Nairobi County | Data-ingestion extent for sensing and Earth observation | 695.937 |

The core is bounded by Uhuru Highway to the west, University Way to the north, Kirinyaga Road to the north-east from the Globe Roundabout, Ring Road to the east, and Haile Selassie Avenue to the south, extending south-east to include the OTC public transport terminus. It contains the principal commercial spine of Moi Avenue and Kenyatta Avenue.

Rather than digitising this boundary by hand, the core polygon is derived from OpenStreetMap: its corners are computed as the intersections of consecutive boundary roads, and its outline is the convex hull of those corners together with the Globe Roundabout and OTC as required landmarks. The landmark coordinates are fixed in the configuration rather than geocoded at build time, so the boundary cannot shift if a geocoding service changes its answer. Where two named roads do not meet — typically because the connecting roundabout carries no road name in OpenStreetMap — the corner is placed at the midpoint of the shortest connecting segment and the gap is recorded. The build is then validated by confirming that both landmarks fall inside the core and that at least 300 m of both Moi Avenue and Kenyatta Avenue lies within it. The criterion is an absolute length rather than a share of each road's total length, because both avenues legitimately continue beyond the CBD and a share would penalise the core for their extent outside it. The length is summed over every OpenStreetMap way carrying the road's name, including parallel carriageways and service roads, so it confirms that each avenue passes through the core rather than measuring how much of its centreline does; in the reported build both exceed the minimum by roughly a factor of ten. This makes the study area reproducible from configuration alone, and makes any change to it auditable.

The simulation domain extends 1 km beyond the core. Traffic entering the CBD originates outside it, and vehicles near a simulation boundary behave unrealistically because the network ends abruptly; scoring only the interior core isolates KPIs from these artefacts.

The county tier defines the extent over which sensor streams and Earth observation data are ingested. Because flash flooding in the CBD is driven partly by runoff generated upstream, the hydrological analysis in Section 3.2 extends to the relevant catchment boundaries, which do not coincide with the administrative boundary.

All metric operations use WGS 84 / UTM zone 37S (EPSG:32737). Data are stored and exchanged in WGS 84 (EPSG:4326), and Web Mercator (EPSG:3857) is used solely for web tiles in the 3D visualisation layer. Restricting each coordinate reference system to one purpose avoids the silent distortion that arises when areas or distances are measured in a display projection.

*Fig. 1. The three study area tiers, derived boundary corners, and landmarks. [Insert from study_area_map.html]*

## 2.2 Functional requirements

The framework must satisfy the following functional requirements.

**Table 2.** Functional requirements.

| ID | Requirement | Objective |
|---|---|---|
| FR1 | Ingest traffic state from intersections and flood state from gauges, rainfall and Earth observation in near real time | SO2, SO3 |
| FR2 | Fuse traffic and flood observations onto a common spatiotemporal grid | SO3 |
| FR3 | Identify road links whose inundation depth exceeds the passability threshold | SO3 |
| FR4 | Remove impassable links from routing and reroute affected vehicles | SO1, SO5 |
| FR5 | Adapt signal timing through negotiation among intersection agents | SO1 |
| FR6 | Grant pre-emption priority to emergency vehicles | SO1 |
| FR7 | Render live traffic and flood state on a 3D city model for operators | SO4 |
| FR8 | Log every observation, decision and actuation with timestamps for evaluation | SO5 |

## 2.3 Non-functional requirements

Four non-functional requirements shape the architecture more strongly than any functional one.

**Sparsity tolerance.** The framework must operate on the sensing density actually available in Nairobi, not on the dense instrumentation assumed by most reviewed systems. Missing and intermittent observations are treated as the normal case: every state estimate carries its age, and agents act on the freshest estimate available rather than waiting for complete data.

**Bounded latency.** Each data flow must meet the end-to-end latency class assigned to it in Section 2.5.

**Bandwidth economy.** Cameras perform vehicle detection at the edge and publish counts rather than video. This keeps continuous uplink demand per intersection in the tens of kilobits per second, and reserves video transmission for incident verification.

**Reproducibility.** Every result reported must be regenerable from the published code and configuration, including the study area boundary itself.

## 2.4 Key performance indicators

KPIs are computed inside the core only, and each is compared across three control strategies: fixed-time control, representing current practice; SUMO actuated control; and the proposed multi-agent control.

**Table 3.** Key performance indicators.

| Group | KPI | Unit | Better |
|---|---|---|---|
| Traffic | Mean delay | s/veh | Lower |
| | Mean and maximum queue length | veh | Lower |
| | Throughput | veh/h | Higher |
| | Travel time index | ratio | Lower |
| Emergency | EMV travel time | s | Lower |
| | EMV delay ratio | ratio | Lower |
| Flood | Flooded-link entries | veh | Lower |
| | Routing avoidance rate | ratio | Higher |
| | Detection-to-closure time | s | Lower |
| System | End-to-end latency, per class | ms | Lower |
| | 5G round-trip time | ms | Lower |
| | Uplink throughput | Mbit/s | Higher |
| | Message loss rate | ratio | Lower |
| | Age of information | s | Lower |
| Fusion | Flood extent critical success index | ratio | Higher |

A link is treated as impassable when its inundation depth exceeds 0.30 m, following the depth–disruption relationship reported by Pregnolato et al. [2]. The routing avoidance rate is defined as

$$
R = 1 - \frac{N_{\text{entered}}}{N_{\text{exposed}}}
$$

where $N_{\text{exposed}}$ is the number of vehicles whose original route crossed an impassable link and $N_{\text{entered}}$ is the number that entered one regardless.

## 2.5 Latency requirements

Latency is specified end to end, from the physical event to actuation or display, rather than as air-interface latency. The 5G link is one segment of that path, so each class also states the share of the budget the network may consume. This distinction matters: the IMT-2020 minimum requirement for user-plane latency is 1 ms for ultra-reliable low-latency communication and 4 ms for enhanced mobile broadband [3], but these figures describe the radio interface alone and say nothing about whether a complete sensing-to-actuation loop is fast enough.

**Table 4.** Latency classes (95th percentile).

| Class | Data flow | End-to-end | Network share | Basis |
|---|---|---|---|---|
| L1 | Actuation: EMV pre-emption, link closure | ≤ 250 ms | ≤ 50 ms | Completes within one 1 s control step with margin for negotiation |
| L2 | Traffic state to agents | ≤ 1 s | ≤ 100 ms | Matches the control step |
| L3 | Flood state updates | ≤ 60 s | ≤ 500 ms | Well inside flash flood onset time |
| L4 | 3D visualisation refresh | ≤ 2 s | ≤ 200 ms | Operator decision support |
| L5 | Earth observation and archives | ≤ 24 h | — | Bounded by satellite revisit |

The network share for L1 lies within the range specified for vehicle-to-everything services in 3GPP TS 22.186 [4]. Most of the framework therefore does not require ultra-reliable low-latency communication: only L1 is latency-critical, and even L1 is bounded by the one-second control step rather than by millisecond radio performance. These thresholds are design hypotheses; Section 3.4 reports whether the deployed 5G network meets them, at the 50th, 95th and 99th percentiles.

## References

[1] Kenya National Bureau of Statistics, *2019 Kenya Population and Housing Census, Volume I: Population by County and Sub-County*. Nairobi, Kenya: KNBS, 2019.

[2] M. Pregnolato, A. Ford, S. M. Wilkinson, and R. J. Dawson, "The impact of flooding on road transport: A depth-disruption function," *Transp. Res. Part D: Transp. Environ.*, vol. 55, pp. 67–81, 2017.

[3] ITU-R, "Minimum requirements related to technical performance for IMT-2020 radio interface(s)," Rep. ITU-R M.2410-0, Nov. 2017.

[4] 3GPP, "Enhancement of 3GPP support for V2X scenarios; Stage 1," TS 22.186.
