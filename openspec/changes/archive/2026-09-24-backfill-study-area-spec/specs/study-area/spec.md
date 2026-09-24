## Purpose

Defines where the digital twin operates: three nested spatial tiers derived from
OpenStreetMap and configuration rather than hand-drawn, the coordinate reference
systems used for each purpose, and the validation that confirms the derived CBD core
covers the town centre. Every KPI is computed inside the core, so later capabilities
depend on this boundary being reproducible and auditable.

## ADDED Requirements

### Requirement: Three nested spatial tiers
The study area SHALL consist of three tiers: the **core** (the CBD analysis polygon,
where every KPI is computed), the **domain** (the core buffered outward by
`domain.buffer_m` in `config/study_area.yaml`, 1000 m, with round joins; simulated but
never scored), and the **county** (the polygon returned for `county.query`, Nairobi
County; the data-ingestion extent). The domain SHALL contain the core. The buffer
width removes boundary effects from vehicles entering an abruptly ending network
(paper §2.1).

#### Scenario: Domain is the buffered core
- **WHEN** the core is a 1000 m × 1000 m square and the buffer is 1000 m
- **THEN** the domain area equals 1000² + 4·1000·1000 + π·1000² m² within 0.1%
- **AND** the domain contains the core

#### Scenario: Shipped tiers are nested
- **WHEN** the study area is built from the committed configuration
- **THEN** the county contains the domain and the domain contains the core

### Requirement: One coordinate reference system per purpose
All metric operations (areas, buffers, distances, gap and length measurements) SHALL
use `crs.analysis`, EPSG:32737 (WGS 84 / UTM zone 37S). Stored and exchanged outputs
SHALL use `crs.geographic`, EPSG:4326. `crs.web`, EPSG:3857, SHALL be used for web
tiles only and never for measurement. Core derivation MUST refuse inputs that are not
in a projected CRS, because measuring in degrees silently corrupts every threshold.

#### Scenario: Geographic input is rejected
- **WHEN** core derivation is given roads in EPSG:4326
- **THEN** it fails with a study-area error and produces no core

#### Scenario: Outputs are stored in the geographic CRS
- **WHEN** the build writes its outputs
- **THEN** every layer of the GeoPackage is in EPSG:4326
- **AND** the report records all three configured CRS codes

### Requirement: Boundary roads are selected by configured name
The core boundary SHALL be defined in `core.boundary_ring` as an ordered, closed ring
of roads, each with one or more name aliases. A road SHALL match every OpenStreetMap
line feature whose `name` equals any alias, ignoring case and surrounding whitespace;
partial matches SHALL NOT count, so `Ring Road` does not match `Ring Road Ngara`. A
configured road that matches no feature MUST fail the build with an error naming
the aliases.

The committed ring, in order, is Uhuru Highway, University Way, Kirinyaga Road,
Ring Road and Haile Selassie Avenue.

#### Scenario: Name matching ignores case
- **WHEN** a road is configured as `west road` and OSM names it `West Road`
- **THEN** the feature is selected

#### Scenario: Unknown road fails the build
- **WHEN** a configured alias matches no OSM feature
- **THEN** the build fails with a study-area error naming the aliases

### Requirement: Corners are derived where consecutive boundary roads meet
For each consecutive pair in the ring, including last to first, the build SHALL
derive one corner. Where the two roads intersect, the corner SHALL be the centroid of
their intersection and its gap recorded as 0 m. Where they do not, the corner SHALL
be the midpoint of the shortest segment between them and the length of that segment
recorded as the gap, since named OSM ways commonly stop at roundabouts whose rings
carry no name. A gap exceeding `core.corner_gap_tolerance_m` (150 m) SHALL be flagged
in the report and logged as a warning, but SHALL NOT by itself fail the build. The
150 m value is empirical: in the committed build three gaps are under 35 m, one is
131 m, and one (University Way / Kirinyaga Road) is 295 m
(`notebooks/00-study-area-exploration.ipynb` §2).

#### Scenario: Roads that intersect
- **WHEN** two consecutive boundary roads cross
- **THEN** the corner method is `intersection` and the gap is 0 m

#### Scenario: Roads separated by a small gap
- **WHEN** two consecutive roads stop 60 m short of each other in both axes
- **THEN** the corner method is `nearest_gap`, the recorded gap is about 84.9 m, and the corner is not flagged

#### Scenario: Wide gap is flagged, not fatal
- **WHEN** two consecutive roads are 300 m apart
- **THEN** the pair is listed under wide gaps in the report
- **AND** the build still produces a core

### Requirement: The core is the convex hull of corners and landmarks
The core polygon SHALL be the convex hull of all derived corners together with every
landmark in `core.landmarks`. Landmarks close wide gaps and extend the core to
features the boundary roads miss: the Globe Roundabout closes the University Way /
Kirinyaga Road gap, and the OTC terminus extends the core south-east. If the hull is
not a polygon, the build MUST fail.

#### Scenario: Closed ring gives the enclosed area
- **WHEN** four boundary roads form a closed 1 km square and there are no landmarks
- **THEN** the core area is 1 km² within 10⁻⁶ relative tolerance

#### Scenario: A landmark beyond the ring expands the core
- **WHEN** a landmark lies outside the square formed by the corners
- **THEN** the core area exceeds that of the square and contains the landmark

#### Scenario: Degenerate hull fails
- **WHEN** all corners and landmarks are collinear
- **THEN** the build fails with a study-area error

### Requirement: Landmark locations are recorded with their source
Each landmark SHALL be located from its configured `lonlat` when present, with source
`manual`; otherwise by geocoding its `queries` in order, taking the first that
succeeds, with source `geocoded: <query>`. A landmark that cannot be located MUST fail
the build with an error that names it and directs the user to set its `lonlat`.

#### Scenario: Manual coordinates take precedence
- **WHEN** a landmark has `lonlat` set
- **THEN** it is placed there without geocoding and its source is `manual`

#### Scenario: Unlocatable landmark fails the build
- **WHEN** no query for a landmark geocodes and it has no `lonlat`
- **THEN** the build fails with an error naming the landmark

### Requirement: The core is validated against the town centre
The build SHALL validate the core and report the result. Validation passes only when:
every landmark lies within the core (with 1 m tolerance); and, for every road in
`core.interior_roads` (Moi Avenue and Kenyatta Avenue), at least
`core.min_interior_length_m` (300 m) of the matched ways lies inside the core. The
criterion is a length, not a share of each road, because both avenues continue
beyond the CBD. The length is summed over every way matching the name, including
parallel carriageways and same-named service roads, so it can exceed the core's
width (paper §2.1; notebook §4).

#### Scenario: Interior road well inside the core
- **WHEN** 900 m of an interior road lies inside the core and the minimum is 300 m
- **THEN** the interior length is reported as 900 m and validation passes

#### Scenario: Interior road too short inside the core
- **WHEN** less than 300 m of an interior road lies inside the core
- **THEN** validation fails and a warning names the road

#### Scenario: Landmark outside the core fails validation
- **WHEN** a landmark does not lie within the core
- **THEN** that landmark is reported as outside and validation fails

### Requirement: Build outputs
The build SHALL write to `outputs.dir` (`data/processed/`): a GeoPackage with layers
`tiers` (tier name, area in km², geometry), `corners` (road pair, method, gap in
metres) and `landmarks` (key, source); a JSON report with the CRS policy, the area of
each tier in km² to three decimals, every corner with its method and gap, and the
validation checks; and an interactive map of tiers, corners and landmarks for visual
inspection. Outputs SHALL be written even when validation fails, so the failure can
be inspected, and the build entry point MUST then exit non-zero.

#### Scenario: Successful build
- **WHEN** the build runs and validation passes
- **THEN** the GeoPackage, report and map exist and the entry point exits 0

#### Scenario: Failed validation still writes outputs
- **WHEN** the build runs and validation fails
- **THEN** the GeoPackage, report and map are written, the report shows `passed: false`, and the entry point exits non-zero
