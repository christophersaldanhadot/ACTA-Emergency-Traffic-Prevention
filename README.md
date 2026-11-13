## ACTA — Adaptive Corridor Trigger Algorithm

Geometry-aware IoT framework for intelligent ambulance traffic-signal
preemption, with a SUMO/TraCI simulation of the resulting activation
plan and a React dashboard on top.

ACTA takes a Google Maps route, finds the OSM traffic signals that
genuinely belong to it (in the correct direction of travel), orders
them along the route, produces a signal-preemption activation plan
(merge / immediate trigger / waypoint trigger / multiple waypoints),
and can simulate that plan against a single-road SUMO corridor to see
the preemption fire in a real traffic microsimulation.

```
Google Maps route
      |
Route geometry A -> B
      |
OSM traffic signals (Overpass, bounding box)
      |
Identify ONLY signals belonging to the selected route, in the A->B direction
      |
Order signals along the route
      |
Inter-signal route distances
      |
ACTA classification (MERGE / IMMEDIATE / WAYPOINT / MULTIPLE_WAYPOINTS)
      |
Activation plan  ->  Backend API  ->  SUMO/TraCI simulation  ->  Dashboard
```

## Architecture

```
backend/
  app.py                        FastAPI endpoints
  services/
    route_service.py            Google Routes API -> distance/duration/polyline
    polyline_decoder.py          Google encoded-polyline -> [{lat,lng}, ...]
    signal_service.py            OSM signal + road-way acquisition (Overpass)
    route_match_service.py       Route/signal geometric + directional matching
    acta_engine.py                ACTA classification (Cases A-D) + plan building
    execution_planner.py         Decisions -> concrete preemption actions
    road_service.py              Google Roads API (snap-to-roads / nearest-roads)
    logging_utils.py             Console "ACTA ROUTE ANALYSIS" report formatting
  config/
    road_profiles.json           Per-road-type ACTA thresholds
  cache/                         Cached (non-empty) Overpass responses
  tests/                         Deterministic pytest suite (no live network)
    fixtures.py                  Synthetic route + synthetic OSM data
    live/                        Manual scripts that hit the REAL Google/Overpass APIs
simulation/
  network_builder.py             ACTA plan -> single-corridor SUMO network (netconvert)
  route_builder.py                Ambulance + light background traffic (.rou.xml)
  sumo_config_builder.py          .sumocfg assembly
  preemption_controller.py        TraCI loop: ACTA cases -> traffic-light preemption
  run_simulation.py               CLI runner (build + run, optional baseline comparison)
  sample_plans/                   Example plan JSON for offline testing
  scenarios/                      Generated network/route/config/report files (gitignored)
dashboard/
  src/App.jsx                     Route form, tables, corridor view, simulation panel
  src/CorridorView.jsx            SVG corridor visualization
  src/api.js                      FastAPI client
```

Each service has one job. `SignalService` only fetches raw OSM data; it
never decides whether a signal is "on route". `RouteMatchService` only
does geometry/direction; it knows nothing about Overpass or ACTA
thresholds. `ActaEngine` only classifies distances into cases; it
knows nothing about geometry. `simulation/` only turns an already-computed
ACTA plan into a runnable microsimulation; it knows nothing about how
that plan was derived (Google/OSM vs. a hand-written test fixture).

## Setup

```bash
cd ACTA_Project_P5
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

`requirements.txt` installs everything needed, including working
`sumo` / `sumo-gui` / `netconvert` binaries (via the `eclipse-sumo`
PyPI package) on this venv's PATH -- **no separate SUMO install or
`SUMO_HOME` is required**, on Linux, macOS, or Windows.

Create/verify `.env` in the project root:

```
GOOGLE_MAPS_API_KEY=your_key_here

# Optional: run the whole pipeline against a small fixed mock route
# instead of calling the real Google Routes API (useful for offline
# demos when a key isn't configured). Never enabled implicitly --
# only when this is explicitly set.
# ACTA_MOCK_ROUTES=1
```

Run the API:

```bash
cd backend
python run.py
# or: uvicorn app:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

Run the dashboard (separate terminal):

```bash
cd dashboard
npm install
cp .env.example .env      # points VITE_API_BASE_URL at the backend
npm run dev
```

Dashboard: http://127.0.0.1:5173 — enter an origin/destination,
generate the ACTA plan, then click "Build & run SUMO simulation" to
compile and run the corridor scenario and see the preemption event log.
The backend's CORS policy already allows `http://localhost:5173`.

As of v6, the dashboard no longer exposes a "Road profile" selector --
route distance, ETA, and (when comparing) the SUMO baseline arrival
time are all sourced live from Google's traffic-aware Routes API on
every plan generation (see "Live traffic baseline" below). The
`profile` request field still exists on the API (default `"Urban"`)
purely as an internal input to the ACTA matching-tolerance/waypoint-
spacing config in `road_profiles.json`; it is no longer surfaced as a
user choice in the UI.

## API

### `POST /generate-full-plan`  (the full ACTA pipeline)

```json
{
  "origin": "Christ University Kengeri Campus",
  "destination": "Rajarajeshwari Nagar Metro Station"
}
```

Runs the entire pipeline end-to-end: Google route -> OSM signals ->
route/direction matching -> ordered A->B signals -> inter-signal
distances -> ACTA plan. Returns:

```json
{
  "origin": "...",
  "destination": "...",
  "route_distance_meters": 15771.9,
  "route_duration_seconds": 1980,
  "route_duration_in_traffic_seconds": 1980,
  "route_static_duration_seconds": 1560,
  "route_source": "google_routes_api",
  "signals": [
    {
      "signal_id": 560642468,
      "on_route": true,
      "direction": "A_TO_B",
      "position_on_route_meters": 2345.4,
      "distance_from_route_meters": 4.1,
      "match_confidence": 0.94
    }
  ],
  "discarded_signals": [
    {
      "signal_id": 1145362952,
      "on_route": false,
      "direction": "UNKNOWN",
      "reason": "off_route"
    }
  ],
  "acta_plan": [
    {
      "from_signal": 560642468,
      "to_signal": 699704164,
      "distance_meters": 520.0,
      "case": "IMMEDIATE_TRIGGER"
    }
  ]
}
```

`route_duration_in_traffic_seconds` is Google's live traffic-aware ETA
(Routes API `routingPreference: TRAFFIC_AWARE_OPTIMAL`) -- this is what
the dashboard now uses as the SUMO "Arrival, baseline" figure instead
of running a second no-preemption simulation (see `/simulate` below).
`route_static_duration_seconds` is the traffic-free ETA for comparison
and isn't used elsewhere yet.

Degrades gracefully: if Overpass is completely unreachable the response
still returns 200 with `signals: []` and `acta_plan: []`, rather than
failing the whole request. If the Google route itself fails, the
endpoint returns `502`. An unknown `profile` returns `400`.

### `POST /simulate`  (build + run the SUMO scenario from origin/destination)

```json
{
  "origin": "Christ University Kengeri Campus",
  "destination": "Rajarajeshwari Nagar Metro Station",
  "compare": true,
  "end_time": 1200
}
```

Runs `/generate-full-plan` internally, then builds a single-corridor
SUMO network (one traffic light per matched signal) and simulates one
ambulance travelling A -> B with ACTA-driven preemption applied via
TraCI (see "SUMO simulation" below). If `compare: true`, the "no
preemption" baseline arrival time is Google's live
`route_duration_in_traffic_seconds` for this corridor when available
(falling back to an actual unpreempted SUMO run otherwise -- e.g. for
a hand-built `/simulate-plan` request with no Google data attached).
`without_preemption_arrival_s` is expressed in seconds either way so
`time_saved_s` stays comparable. The blocking netconvert/TraCI work
runs in a worker thread so it doesn't stall the API's event loop.
Returns:

```json
{
  "net_file": ".../scenario.net.xml",
  "route_file": ".../scenario.rou.xml",
  "sumocfg_file": ".../scenario.sumocfg",
  "with_preemption_arrival_s": 210.0,
  "without_preemption_arrival_s": 1980.0,
  "baseline_source": "google_live_traffic",
  "time_saved_s": 1770.0,
  "events": [
    {"sim_time": 5.0, "event": "PREEMPT_ON", "tls_id": "n_sig_101", "signal_id": 101, "case": "DISPATCH", "trigger_position_m": 0.0, "junction_position_m": 500.0},
    {"sim_time": 42.0, "event": "PREEMPT_OFF", "tls_id": "n_sig_101"}
  ],
  "plan": { "...": "the full /generate-full-plan response" }
}
```

`baseline_source` is `"google_live_traffic"` when the plan carried a
`route_duration_in_traffic_seconds` value, or `"sumo_simulation"` when
it fell back to an actual unpreempted TraCI run.

Note each `PREEMPT_ON` event carries both `trigger_position_m` (where
the ambulance was when the trigger fired) and `junction_position_m`
(the real position of the junction being preempted) -- these are
often different points (e.g. IMMEDIATE_TRIGGER fires at the *upstream*
signal but preempts the *downstream* one), and the release logic keys
off `junction_position_m`, not the firing position.

### `POST /simulate-plan`  (build + run the SUMO scenario from an existing plan)

```json
{
  "plan": { "...": "an already-computed /generate-full-plan response" },
  "compare": true,
  "end_time": 1200
}
```

Same as `/simulate`, but skips the Google Routes + Overpass round trip
entirely by taking an already-computed plan directly. **This is what
the dashboard uses** for its "Build & run SUMO simulation" button: it
simulates exactly the plan currently on screen, with no risk of the
simulation silently drifting from a re-derived plan if the origin/
destination form fields were edited in between. Returns the same shape
as `/simulate`.

Open the generated scenario in the SUMO GUI to watch it visually:

```bash
sumo-gui -c <sumocfg_file from the response>
```

(`sumo-gui` needs a display; on a headless server, run
`simulation/run_simulation.py --gui` from a machine that has one, or
just inspect the returned `events` log / `report.json`.)

### `POST /generate-route`  (route + raw candidate signals, pre-matching)

Unchanged existing endpoint — returns the decoded route and every
*candidate* signal in the bounding box, without direction filtering.
Kept for backward compatibility with any existing frontend/dashboard
code that calls it.

### `POST /generate-plan`  (simulation endpoint, no SUMO)

Unchanged existing endpoint — classifies a raw list of inter-signal
distances (no real signal identity) into ACTA cases. Used for quick
what-if checks without a real route or SUMO. Now also supports Case D
(`MULTIPLE_WAYPOINTS`).

## SUMO simulation methodology

Scope, deliberately simplified per project brief ("ignore different
lanes and exceptional cases, focus on one road"):

- **One through road**, one lane per direction, modelled as a straight
  line. Node spacing preserves the REAL inter-signal distances from
  the matched route (not the real curved geometry — ACTA's timing only
  depends on distance, not curvature).
- Every ACTA on-route signal becomes a **traffic-light junction**.
- A short **minor cross-street stub** (dead-end, one lane each way) is
  added at every junction purely so `netconvert` generates a real
  multi-phase traffic light with something to hold red against — a
  junction with only one road has no conflicting movement, and
  therefore no meaningful signal to preempt. It is not a modelled real
  cross road; it exists only so preemption has a visible before/after
  effect. Light background traffic runs on both the main road and the
  cross streets so the signals have genuine demand.
- One **ambulance** (`vClass="emergency"`) is routed the full corridor
  A -> B.

**Preemption mechanics.** For each junction, a dedicated `"preempt"`
TLS program is created at runtime via
`traci.trafficlight.setProgramLogic()` — a single static phase holding
the main-road approaches green. The ACTA plan is flattened into an
ordered list of trigger events by corridor position:

| ACTA case | SUMO trigger |
|---|---|
| Signal 1 (first on route) | preempted at dispatch (ambulance position 0) |
| MERGE | both signals in the pair preempted together, at the position of the first |
| IMMEDIATE_TRIGGER | downstream signal preempted the instant the ambulance reaches the upstream signal |
| WAYPOINT_TRIGGER | downstream signal preempted at the plan's calculated waypoint position |
| MULTIPLE_WAYPOINTS | every waypoint but the last is logged as an `ADVISORY` event only (models an early warning to a real controller); the last waypoint preempts, same as WAYPOINT_TRIGGER |

The controller tracks the ambulance's cumulative distance travelled
(`traci.vehicle.getDistance`) every simulation step and fires each
event once that distance reaches the event's corridor position. Once
the ambulance has cleared a junction by a safety margin, that
junction's program is switched back to its original default, resuming
normal operation.

**Simplification, documented here rather than hidden:** a real
preemption controller would insert a brief all-red clearance interval
before switching to the emergency-green phase if the signal was
currently green for a conflicting approach. This demo switches
directly. For a lightly-trafficked single-corridor demo this is a
reasonable simplification; a production system would add the
clearance interval as a small state-machine step in
`preemption_controller.py`.

### Running a simulation directly (without the API)

```bash
python simulation/run_simulation.py --plan simulation/sample_plans/synthetic_plan_all_cases.json --compare
```

`--gui` runs with `sumo-gui` (needs a display); `--compare` also runs
an unpreempted baseline and reports time saved; output (network,
routes, config, `report.json`) goes to `simulation/scenarios/<name>/`.
`simulation/sample_plans/synthetic_plan_all_cases.json` is a
hand-built plan covering all four ACTA cases, useful for testing the
simulation independently of Google/Overpass access.

## Route-signal matching methodology

Proximity alone (`distance(signal, route) < 30m`) is **not** used as
the membership test — every point in space has a nearest point on any
route, and two carriageways of the same divided road can sit metres
apart while carrying opposite directions of travel. Membership is
decided from three pieces of evidence together:

1. **Corridor distance** — the signal must project within
   `corridor_threshold_m` (35m default) of the route geometry. This is
   a necessary but not sufficient condition.
2. **Road bearing at the signal** — for each OSM way passing through
   the signal node, the way's own digitized geometry (the points
   immediately before/after the signal in that way) gives a bearing.
   That bearing is compared against the route's local bearing at the
   same position.
3. **`oneway` tag** — if the way is one-directional (`oneway=yes` or
   `oneway=-1`), the direction of allowed travel is derived from the
   tag and compared to the route's bearing:
   - aligned within 45 degrees -> `A_TO_B`, kept
   - opposed (>135 degrees difference) -> `B_TO_A`, **discarded** as
     the opposite carriageway
   - Two-way/untagged ways can't be used to prove a direction from OSM
     tags alone; alignment is checked mod 180 (same corridor or not).
     If neither orientation aligns (e.g. a perpendicular cross street),
     the signal is discarded as `ambiguous_direction`, even if it sits
     directly on the route polyline.
4. If a signal has **no associated way geometry at all**, it is marked
   `direction_unknown` and excluded from the plan rather than guessed
   into (or out of) the route.

Signals are ordered by `position_on_route_meters`, which is the
signal's projected distance along the *directed* Google polyline (not
latitude, longitude, or OSM id).

**Deduplication:** only OSM nodes with *exact*-matching coordinates are
collapsed (these represent the same mapped entity duplicated in OSM).
Distinct signal heads/movements that merely sit close together at the
same junction are deliberately preserved, since a fixed distance
threshold cannot reliably tell them apart.

## Overpass querying

A single bounding-box query (with an automatic small-tile split for
unusually large routes, capped at 6 tiles) replaces the old
per-75m-segment approach that was generating dozens of requests and
causing HTTP 504s:

```
[out:json][timeout:60];
(
  node["highway"="traffic_signals"](south,west,north,east);
);
out body;
way(bn)["highway"];
out body geom;
```

`way(bn)` finds every way that contains one of the retrieved signal
nodes ("by node"), with full geometry — this is what makes direction
determination possible.

Three public mirrors are tried in order and never removed:

```
https://overpass-api.de/api/interpreter
https://overpass.kumi.systems/api/interpreter
https://maps.mail.ru/osm/tools/overpass/api/interpreter
```

Only non-empty, successful responses are cached to `backend/cache/`
(keyed by bounding box). Previously the cache directory held several
files that were all empty `elements: []` payloads — those were the
result of the corrupted `signal_service.py` silently caching failures;
that bug is fixed.

## ACTA classification (3 cases)

Given the ordered on-route signals, distance `d` between each
consecutive pair is classified using fixed, profile-independent
boundaries (per the project architecture spec):

| Case | Condition | Action |
|---|---|---|
| IMMEDIATE_TRIGGER | `d <= 100m` | trigger the next signal directly |
| WAYPOINT_TRIGGER | `100m < d < 1000m` | trigger the next signal from a single waypoint, `queueBuffer` metres before it |
| MULTIPLE_WAYPOINTS | `d >= 1000m` | trigger the next signal from a chain of waypoints spaced roughly `waypointSpacing` metres apart |

The 100m/1000m boundaries are fixed constants
(`IMMEDIATE_THRESHOLD_M` / `MULTI_WAYPOINT_THRESHOLD_M` in
`acta_engine.py`) and do **not** vary by road profile. The selected
profile (`Urban`/`CBD`/`Highway`/`Rural`) still matters for *how* a
waypoint is placed -- `queueBuffer` (how far before the target signal
a single waypoint sits) and `waypointSpacing` (gap between chained
waypoints in the MULTIPLE_WAYPOINTS case) are read from
`backend/config/road_profiles.json` and differ per profile.

Earlier revisions of this project used a 4-case system (adding a
`MERGE` case for very short gaps) with profile-dependent boundaries.
That was superseded by explicit project-spec direction to use exactly
3 cases with fixed 100m/1000m boundaries; `MERGE` was removed
accordingly (see "Hardening changelog" below).

## Testing

```bash
cd backend
source ../.venv/bin/activate
python -m pytest -q
```

46 deterministic tests, no network required (`backend/tests/`):

| # | Covers |
|---|---|
| 1 | Google polyline decoding against a known reference |
| 2 | Route geometry length |
| 3 | Overpass signal retrieval (mocked transport) |
| 4 | Overpass mirror fallback on HTTP 504 / connection refused |
| 5–6 | Signal <-> route matching, A_TO_B vs B_TO_A |
| 7 | Signals genuinely outside the route corridor |
| 8 | Signals at/near intersections (cross-street rejection) |
| 9 | Duplicate OSM node handling |
| 10–11 | Signal ordering + inter-signal route distances |
| 12–15 | ACTA Cases A, B, C, D |
| 16 | Full `/generate-full-plan` integration test |
| — | `test_route_service.py`: Google Routes API response parsing against realistic response shapes, including the fractional-seconds duration format, 403/429/timeout/connection-refused/malformed-JSON handling, and the exact request contract (headers + body) sent |
| — | `test_preemption_controller.py`: pins the exact release-position formula (regression coverage for a real bug where release was computed from the trigger's firing position instead of the preempted junction's own position) |
| — | `/simulate` and `/simulate-plan` integration tests: build and run a REAL SUMO/TraCI scenario (not mocked) covering DISPATCH, MERGE, IMMEDIATE_TRIGGER preemption and release events |

Plus: all-mirrors-down degradation, cache write/read behaviour,
unknown-profile error handling, and signal-service failure not
crashing the API.

**Live tests** (require a real `GOOGLE_MAPS_API_KEY` and network
access to Overpass) live separately in `backend/tests/live/` and are
not run by `pytest` by default:

```bash
cd backend
python tests/live/live_route_match_service.py
python tests/live/live_road_service.py
python tests/live/live_route_roads.py
python tests/live/live_google_routes_api.py
```

## Error handling

- Google route failures -> `502` from `/generate-full-plan` with a
  clear message (never a raw traceback).
- Overpass fully unavailable -> pipeline continues with zero signals
  (logged as a warning) instead of failing the request.
- Fewer than two on-route signals -> empty `acta_plan` rather than an
  index error; `/simulate` returns `422` if there are zero on-route
  signals (nothing to build a scenario around).
- Unknown road profile -> `400` with the invalid profile name.
- Malformed Overpass JSON from a mirror -> that mirror is treated as
  failed and the next one is tried.
- `netconvert` failure while building a SUMO scenario -> `500` from
  `/simulate` with the captured `netconvert` stderr, not a bare
  stack trace.

## Hardening changelog (latest pass)

**Round 3** (UI clarity, explicit 3-case ACTA spec, presentation polish):

- **ACTA case system replaced**: switched from the earlier 4-case,
  profile-dependent design (`MERGE`/`IMMEDIATE_TRIGGER`/
  `WAYPOINT_TRIGGER`/`MULTIPLE_WAYPOINTS`) to the project architecture
  spec's exact 3-case, fixed-threshold design (`d<=100m`,
  `100m<d<1000m`, `d>=1000m`). `MERGE` is gone; `mergeThreshold` /
  `triggerThreshold` / `multiWaypointThreshold` were removed from
  `road_profiles.json` (now only `queueBuffer` / `waypointSpacing`,
  which still vary by profile). Updated every consumer of case names
  (`execution_planner.py`, `preemption_controller.py`, the dashboard's
  tag colours/legend) and rewrote the affected tests and sample plan
  to match. Re-ran the real SUMO simulation afterward to confirm all 3
  cases still fire with correct timing end-to-end.
- **Corridor view was cramped on real long routes** (confirmed against
  a real 17-signal / 38km run): fixed-width SVG with fixed label
  positions overlapped badly. Corridor width now scales with signal
  count (with horizontal scroll beyond a cap), signal-ID labels
  alternate between two rows whenever consecutive signals are close
  enough to collide, and segment-case labels only render inline when
  the segment is wide enough -- a compact legend covers the rest.
  `MULTIPLE_WAYPOINTS` now gets a dedicated purple badge colour.
  Verified via server-side React rendering with realistic 17-signal
  data (not just "it builds") -- see below.
- **Table clutter**: coordinates are now shown truncated to 4 decimal
  places (full precision available via hover tooltip), waypoint/case
  metadata moved out of raw inline strings into small badge pills,
  and every table now uses `table-layout: fixed` with explicit
  per-column widths plus text-overflow ellipsis + tooltips, so long
  values can no longer crowd into neighbouring cells.
- **Presentation polish**: added an "ACTA Efficiency Summary" KPI
  panel (total route distance, active corridor signals, rejected
  off-route signals, calculated time-saved estimate) and replaced the
  plain event list with an expandable, terminal-styled "System
  Telemetry Log" drawer with colour-coded `INFO`/`WARN`/`ERROR`/`OK`
  levels, built client-side from the plan + simulation response
  (route computed, Overpass fetch count, matching summary, discard
  reasons grouped by cause, every preemption/advisory/release event,
  and a final completion line).
- Verification note: this sandbox cannot install a real headless
  browser (`chromium-browser` here is only available as a snap, which
  doesn't work in this container), so visual changes were verified by
  server-side rendering `CorridorView`/`TelemetryLog` with realistic
  data at the same scale as a real reported run (17 on-route signals,
  42 candidates) and inspecting the actual generated markup/SVG
  dimensions, rather than a literal pixel screenshot.

**Round 2** (connectivity hardening + Case D calibration):

- **Overpass 406 risk**: several public Overpass mirrors reject
  requests carrying the default HTTP-client User-Agent string. Every
  Overpass request now sends a descriptive, non-default
  `User-Agent: ACTA-Traffic-Engine/1.0 (...)`.
- **Slow failover**: the old flat 60s timeout applied per mirror meant
  a single unresponsive mirror could stall the whole request for up to
  a minute before the next mirror was even tried. Timeouts are now
  split into a short connect timeout (5s default) and a shorter read
  timeout (15s default, down from 60s) so failover to a working mirror
  happens quickly. The Overpass query's own server-side `[timeout:N]`
  directive already tracked this value dynamically.
- **Overpass response caching** (bounding-box-keyed, on disk, survives
  across requests/instances) was already implemented in an earlier
  pass -- verified it actually works across separate `SignalService`
  instances (i.e. separate backend requests), not just within one
  instance's lifetime, with a dedicated regression test.
- Case D (then `MULTIPLE_WAYPOINTS`) threshold recalibration -- since
  superseded by Round 3's switch to the fixed 100m/1000m spec above.

**Round 1** (core correctness):

A full line-by-line audit found and fixed these real bugs (not just
re-tested the happy path):

- Google Routes API duration parsing crash (fractional-second
  durations like `"212.500s"` legally occur and broke `int(...)`
  parsing), a real SUMO preemption-release-timing bug (release was
  keyed to the trigger's firing position instead of the actual
  junction's position, so preemption switched back off long before
  the ambulance arrived), an unsafe simultaneous-green conflict in the
  SUMO signal logic, a dashboard/backend plan-consistency bug (the
  simulate button could silently simulate a different route than the
  one on screen), and several smaller fixes -- see the codebase's
  service-level docstrings/tests for detail on each.

## Known limitations

- Direction determination relies on OSM `oneway`/way-geometry quality.
  Junctions where OSM only maps a single undivided way for a two-way
  road cannot be split into "which direction does this specific signal
  serve" from tags alone; ACTA currently accepts such a signal if its
  alignment matches the route corridor (mod 180), since in practice a
  single signal usually serves the whole junction in that case.
- The bounding-box tiling cap (6 tiles) is a pragmatic ceiling for a
  final-year-project route length; extremely long inter-city routes
  would need a larger cap or a proper corridor-buffer polygon query
  instead of one rectangular bbox.
- The SUMO scenario is a deliberate single-road abstraction (see "SUMO
  simulation methodology" above): real lane counts, turning movements,
  pedestrian phases, and multi-approach junction geometry are not
  modelled. This matches the stated project scope ("ignore different
  lanes and exceptional cases, focus on one road").
- Preemption switches directly to emergency-green without an
  all-red clearance interval (documented above as a simplification,
  not an oversight).
- This sandboxed development environment has no network access to
  `overpass-api.de` or `googleapis.com`, so the live scripts in
  `backend/tests/live/` have been reviewed and fixed for correct
  imports but not executed against the real APIs here — run them
  locally with your `.env` key to confirm against live data. The SUMO
  simulation itself, by contrast, HAS been run and verified end-to-end
  in this environment (network access to Google/OSM isn't needed for
  it — it only needs an already-computed ACTA plan), including through
  the live `/simulate` HTTP endpoint with the dashboard's CORS origin.
