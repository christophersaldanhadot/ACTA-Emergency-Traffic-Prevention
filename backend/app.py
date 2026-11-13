from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from services.road_service import RoadService

from services.acta_engine import ActaEngine
from services.execution_planner import ExecutionPlanner
from services.route_service import RouteService
from services.polyline_decoder import decode_polyline
from services.signal_service import SignalService
from services.route_match_service import RouteMatchService
from services.logging_utils import log_route_analysis

import asyncio
import os
import re
import sys

SIMULATION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "simulation"))
if SIMULATION_DIR not in sys.path:
    sys.path.insert(0, SIMULATION_DIR)

from preemption_controller import PreemptionController  # noqa: E402
from run_simulation import build_scenario, run_no_preemption_baseline  # noqa: E402

road_service = RoadService()
app = FastAPI(title="ACTA Project API")

# Allow the local Vite dashboard (default port 5173) to call this API
# directly from the browser during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ActaEngine()
route_service = RouteService()
signal_service = SignalService()
match_service = RouteMatchService()

# ---------- Request Models ----------

class RouteRequest(BaseModel):
    origin: str
    destination: str
    routing_mode: str = "fastest"  # "fastest" | "shortest"
    # Optional Google Place IDs, set when the frontend's Places
    # Autocomplete resolved a specific place for that field. When
    # present, RouteService uses the place ID directly instead of
    # re-geocoding the text (see RouteService._waypoint_payload).
    origin_place_id: str | None = None
    destination_place_id: str | None = None


class PlanRequest(BaseModel):
    profile: str
    segments: list[int]


class FullPlanRequest(BaseModel):
    origin: str
    destination: str
    # No longer a user-facing concept (the v6 dashboard replaced manual
    # road-profile selection with live Google traffic data) -- kept as an
    # internal default so the ACTA matching-tolerance/waypoint-spacing
    # config (see road_profiles.json) still has a value to read. See the
    # note on generate_full_plan() below.
    profile: str = "Urban"
    # Routing preference passed through to Google Routes (routingPreference)
    # and, at simulation time, to duarouter (--weight-attribute length for
    # "shortest"). "fastest" = live-traffic-optimal time (default);
    # "shortest" = minimize distance.
    routing_mode: str = "fastest"
    # Exact-spec telemetry parameters, now computed server-side as part
    # of the standard acta_plan (see ActaEngine.build_plan_from_signals)
    # instead of being re-derived client-side with its own hardcoded
    # defaults.
    t_target_s: float = 60.0
    mt_m: float = 150.0
    tt_m: float = 1000.0
    # Optional Google Place IDs from the frontend's Places Autocomplete
    # (see RouteRequest above for the rationale).
    origin_place_id: str | None = None
    destination_place_id: str | None = None


class SimulateRequest(BaseModel):
    origin: str
    destination: str
    profile: str = "Urban"
    compare: bool = False
    end_time: int = 1200
    routing_mode: str = "fastest"
    origin_place_id: str | None = None
    destination_place_id: str | None = None


class SimulatePlanRequest(BaseModel):
    plan: dict
    compare: bool = False
    end_time: int = 1200


class TelemetryPlanRequest(BaseModel):
    """New 3-case mathematical spec (MT / TT / beta) request.

    `segments` is a raw list of inter-signal distances in metres (no
    signal identity attached) -- mirrors the existing /generate-plan
    endpoint's contract, but returns full telemetry per the new spec.
    """
    segments: list[float]
    t_target_s: float = 60.0   # desired traversal time per segment (s)
    mt_m: float = 150.0        # Merge Threshold (m)
    tt_m: float = 1000.0       # Trigger Threshold (m)
    beta_m: float = 200.0      # Queue Clearance Buffer (m)


# ---------- Home ----------

@app.get("/")
def home():
    return {
        "message": "ACTA Backend Running",
        "status": "OK"
    }


# ---------- Route Generation ----------

@app.post("/generate-route")
async def generate_route(request: RouteRequest):

    try:

        # Step 1: Get the route from Google Routes API
        route = await route_service.get_route(
            request.origin,
            request.destination,
            routing_mode=request.routing_mode,
            origin_place_id=request.origin_place_id,
            destination_place_id=request.destination_place_id,
        )

        # Step 2: Decode the route polyline into GPS coordinates
        coordinates = decode_polyline(
            route["polyline"]
        )

        # Step 3: Get all traffic signals inside the route's bounding box
        signals = await signal_service.get_signals(
            coordinates
        )

        return {
            "distance_meters": route["distance_meters"],
            "duration_seconds": route["duration_seconds"],
            "number_of_points": len(coordinates),
            "number_of_signals": len(signals),
            "signals": signals,
            "coordinates": coordinates
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Full Pipeline: route -> signals -> matching -> ACTA plan ----------

@app.post("/generate-full-plan")
async def generate_full_plan(request: FullPlanRequest):
    """
    End-to-end ACTA pipeline:
      Google route -> OSM signals -> route/direction matching ->
      ordered A->B signals -> inter-signal distances -> ACTA plan.
    """

    try:
        route = await route_service.get_route(
            request.origin,
            request.destination,
            routing_mode=request.routing_mode,
            origin_place_id=request.origin_place_id,
            destination_place_id=request.destination_place_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Route generation failed: {e}")

    coordinates = decode_polyline(route["polyline"])
    if len(coordinates) < 2:
        raise HTTPException(status_code=422, detail="Route polyline decoded to fewer than 2 points.")

    route_geometry = match_service.create_route_geometry(coordinates)

    try:
        candidate_signals = await signal_service.get_signals(coordinates)
    except Exception as e:
        # OSM being unavailable should degrade gracefully, not crash the request.
        candidate_signals = []
        print(f"[WARN] Signal retrieval failed, continuing with 0 signals: {e}")

    match_result = match_service.match_route_signals(candidate_signals, route_geometry)
    on_route_signals = match_result["on_route_signals"]

    try:
        profile = engine.get_profile(request.profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(on_route_signals) < 2:
        acta_plan = []
    else:
        acta_plan = engine.build_plan_from_signals(
            request.profile,
            on_route_signals,
            t_target_s=request.t_target_s,
            mt_m=request.mt_m,
            tt_m=request.tt_m,
        )

    log_route_analysis(
        origin=request.origin,
        destination=request.destination,
        route_distance_m=route["distance_meters"],
        candidate_count=len(candidate_signals),
        on_route_signals=on_route_signals,
        discarded_signals=match_result["discarded_signals"],
        acta_plan=acta_plan,
    )

    return {
        "origin": request.origin,
        "destination": request.destination,
        "profile": request.profile,
        "routing_mode": request.routing_mode,
        "route_distance_meters": route["distance_meters"],
        "route_duration_seconds": route["duration_seconds"],
        # Google's live-traffic-adjusted ETA (Routes API's
        # TRAFFIC_AWARE_OPTIMAL "duration", equivalent to the legacy
        # Directions API's `duration_in_traffic`). This is what the
        # dashboard now uses as the SUMO baseline arrival time instead
        # of a second simulated run -- see _simulate_from_plan().
        "route_duration_in_traffic_seconds": route.get("duration_in_traffic_seconds"),
        "route_static_duration_seconds": route.get("static_duration_seconds"),
        "route_source": route.get("source", "google_routes_api"),
        "number_of_route_points": len(coordinates),
        "number_of_candidate_signals": len(candidate_signals),
        "signals": on_route_signals,
        "discarded_signals": match_result["discarded_signals"],
        "acta_plan": acta_plan,
    }


# ---------- SUMO simulation ----------

def _scenario_dir_for(origin: str, destination: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", f"{origin}_{destination}").strip("_")[:60]
    return os.path.join(SIMULATION_DIR, "scenarios", safe or "scenario")


def _run_simulation_sync(
    plan: dict, out_dir: str, compare: bool, end_time: int, google_baseline_arrival_s: float | None = None
) -> dict:
    """Blocking work (netconvert + duarouter + TraCI) -- always called
    via a worker thread from the async endpoint, never on the event
    loop directly."""
    routing_mode = plan.get("routing_mode", "fastest")
    network_meta = build_scenario(plan, out_dir, end_time=end_time, routing_mode=routing_mode)
    cfg_file = network_meta["cfg_file"]

    controller = PreemptionController(network_meta, plan)
    result = controller.run(cfg_file, gui=False)

    report = {
        "scenario_dir": out_dir,
        "net_file": network_meta["net_file"],
        "route_file": network_meta["rou_file"],
        "sumocfg_file": cfg_file,
        "ego_speed_factor": network_meta["ego_speed_factor"],
        "routing_mode": routing_mode,
        "with_preemption_arrival_s": result["ambulance_arrival_time"],
        "events": result["events"],
    }

    if compare:
        # A REAL SUMO run of "ego_car" (vClass="passenger", speedFactor
        # calibrated in build_scenario() from Google's live-traffic vs.
        # free-flow durations for this corridor) with preemption never
        # applied -- physically reproducing the live-traffic conditions
        # instead of just echoing Google's own number back as the
        # baseline.
        baseline_arrival = run_no_preemption_baseline(cfg_file, vehicle_id="ego_car_0")
        report["without_preemption_arrival_s"] = baseline_arrival
        report["baseline_source"] = "sumo_calibrated_ego_car"
        if baseline_arrival is not None and result["ambulance_arrival_time"] is not None:
            report["time_saved_s"] = round(baseline_arrival - result["ambulance_arrival_time"], 1)
        if google_baseline_arrival_s is not None and baseline_arrival is not None:
            # Kept for validation/display only: how closely the physical
            # calibrated SUMO run tracked Google's live ETA it was
            # calibrated against.
            report["google_live_traffic_eta_s"] = google_baseline_arrival_s
            report["calibration_delta_s"] = round(baseline_arrival - google_baseline_arrival_s, 1)

    return report


@app.post("/simulate")
async def simulate(request: SimulateRequest):
    """
    Computes the ACTA plan for origin -> destination (same pipeline as
    /generate-full-plan), then builds and runs a SUMO/TraCI simulation
    of a single ambulance travelling the corridor with ACTA-driven
    signal preemption applied. Returns the plan plus a simulation
    report (preemption event log, arrival time, and -- if
    `compare: true` -- an unpreempted baseline arrival time for
    comparison).

    Scope note: models one road (the matched corridor) with one lane
    per direction; intersecting cross streets are represented as
    minimal synthetic stubs purely so each junction has a real,
    preemptable traffic light. This mirrors the ACTA matching pipeline
    itself, which is scoped to a single selected route/corridor.

    Prefer POST /simulate-plan when you already have a plan (e.g. from
    a prior /generate-full-plan call) -- it skips the redundant
    Google/Overpass round trip and, more importantly, guarantees the
    simulation matches exactly the plan being displayed rather than
    silently re-deriving a (possibly different, if form fields changed)
    plan.
    """
    plan = await generate_full_plan(
        FullPlanRequest(
            origin=request.origin, destination=request.destination,
            profile=request.profile, routing_mode=request.routing_mode,
        )
    )
    return await _simulate_from_plan(plan, request.compare, request.end_time)


@app.post("/simulate-plan")
async def simulate_plan(request: SimulatePlanRequest):
    """
    Same as /simulate, but takes an already-computed plan (the exact
    JSON shape returned by /generate-full-plan) instead of
    origin/destination. This is what the dashboard uses: it simulates
    precisely the plan currently on screen, with no risk of it having
    silently drifted from edited form fields, and without paying for a
    second Google Routes + Overpass round trip.
    """
    return await _simulate_from_plan(request.plan, request.compare, request.end_time)


async def _simulate_from_plan(plan: dict, compare: bool, end_time: int) -> dict:
    if not plan.get("signals"):
        raise HTTPException(
            status_code=422,
            detail="No on-route signals in this plan; nothing to simulate.",
        )

    out_dir = _scenario_dir_for(
        plan.get("origin", "origin"), plan.get("destination", "destination")
    )

    google_baseline_arrival_s = plan.get("route_duration_in_traffic_seconds")

    try:
        report = await asyncio.to_thread(
            _run_simulation_sync, plan, out_dir, compare, end_time, google_baseline_arrival_s
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"SUMO scenario build failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    report["plan"] = plan
    return report


# ---------- ACTA ----------

@app.post("/generate-plan")
def generate_plan(request: PlanRequest):

    try:

        decisions = engine.evaluate(
            request.profile,
            request.segments
        )

        actions = ExecutionPlanner.generate_actions(
            decisions
        )

        return actions

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- ACTA: new 3-case mathematical spec (MT / TT / beta) ----------

@app.post("/generate-plan-telemetry")
def generate_plan_telemetry(request: TelemetryPlanRequest):
    """
    Evaluate raw inter-signal distances against the exact 3-case ACTA
    mathematical specification and return full per-segment telemetry:

        Case 1: MERGE_TRIGGER      (L < MT)
        Case 2: IMMEDIATE_TRIGGER  (MT <= L <= TT), AP = 0
        Case 3: WAYPOINT_TRIGGER   (L > TT), AP = L - beta, W1 = L/2, W2 = AP

    Every segment also includes target corridor speed (km/h and m/s)
    and the lead preemption window T_window = beta / v_target.
    """
    try:
        if request.mt_m <= 0 or request.tt_m <= 0 or request.beta_m < 0:
            raise ValueError("mt_m and tt_m must be positive; beta_m must be non-negative.")
        if request.mt_m > request.tt_m:
            raise ValueError("mt_m (Merge Threshold) must be <= tt_m (Trigger Threshold).")

        segments = engine.evaluate_telemetry(
            request.segments,
            t_target_s=request.t_target_s,
            mt_m=request.mt_m,
            tt_m=request.tt_m,
            beta_m=request.beta_m,
        )

        return {
            "parameters": {
                "MT": request.mt_m,
                "TT": request.tt_m,
                "beta": request.beta_m,
                "t_target_s": request.t_target_s,
            },
            "segments": segments,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-full-plan-telemetry")
async def generate_full_plan_telemetry(
    request: FullPlanRequest,
    t_target_s: float = 60.0,
    mt_m: float = 150.0,
    tt_m: float = 1000.0,
    beta_m: float = 200.0,
):
    """
    End-to-end pipeline (Google route -> OSM signals -> route matching)
    identical to /generate-full-plan, but the ACTA plan is built using
    the new 3-case mathematical spec (MERGE_TRIGGER / IMMEDIATE_TRIGGER /
    WAYPOINT_TRIGGER) with full target-speed / clearance-window telemetry
    per segment instead of the legacy profile-based classification.
    """
    try:
        route = await route_service.get_route(
            request.origin,
            request.destination,
            routing_mode=request.routing_mode,
            origin_place_id=request.origin_place_id,
            destination_place_id=request.destination_place_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Route generation failed: {e}")

    coordinates = decode_polyline(route["polyline"])
    if len(coordinates) < 2:
        raise HTTPException(status_code=422, detail="Route polyline decoded to fewer than 2 points.")

    route_geometry = match_service.create_route_geometry(coordinates)

    try:
        candidate_signals = await signal_service.get_signals(coordinates)
    except Exception as e:
        candidate_signals = []
        print(f"[WARN] Signal retrieval failed, continuing with 0 signals: {e}")

    match_result = match_service.match_route_signals(candidate_signals, route_geometry)
    on_route_signals = match_result["on_route_signals"]

    if len(on_route_signals) < 2:
        acta_plan = []
    else:
        acta_plan = engine.build_telemetry_plan_from_signals(
            on_route_signals,
            t_target_s=t_target_s,
            mt_m=mt_m,
            tt_m=tt_m,
            beta_m=beta_m,
        )

    return {
        "origin": request.origin,
        "destination": request.destination,
        "routing_mode": request.routing_mode,
        "route_distance_meters": route["distance_meters"],
        "route_duration_seconds": route["duration_seconds"],
        "route_duration_in_traffic_seconds": route.get("duration_in_traffic_seconds"),
        "route_source": route.get("source", "google_routes_api"),
        "number_of_route_points": len(coordinates),
        "number_of_candidate_signals": len(candidate_signals),
        "signals": on_route_signals,
        "discarded_signals": match_result["discarded_signals"],
        "parameters": {
            "MT": mt_m,
            "TT": tt_m,
            "beta": beta_m,
            "t_target_s": t_target_s,
        },
        "acta_plan": acta_plan,
    }