"""
run_simulation.py
==================

End-to-end SUMO/TraCI runner for one ACTA plan.

Usage:

    # Using a previously saved plan (e.g. from POST /generate-full-plan):
    python simulation/run_simulation.py --plan simulation/sample_plans/synthetic_plan_all_cases.json

    # With the SUMO GUI (requires a display):
    python simulation/run_simulation.py --plan <file> --gui

    # Compare against a no-preemption baseline (runs twice):
    python simulation/run_simulation.py --plan <file> --compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from network_builder import build_network  # noqa: E402
from route_builder import build_routes  # noqa: E402
from sumo_config_builder import build_sumocfg  # noqa: E402
from preemption_controller import PreemptionController  # noqa: E402
from traffic_calibration import compute_ego_speed_factor  # noqa: E402
from duarouter_ego import compute_ego_route_edges  # noqa: E402


def build_scenario(plan: dict, out_dir: str, end_time: int = 1200, routing_mode: str = "fastest") -> dict:
    """Builds the network, then computes the ego_car passenger-only
    route (duarouter) and its live-traffic-calibrated speedFactor
    before writing the .rou.xml -- both depend on the compiled network
    (edge permissions/ids) and the plan's Google Routes timing fields
    respectively, so they must happen in this order.

    routing_mode: "fastest" (duarouter --weight-attribute traveltime) or
    "shortest" (duarouter --weight-attribute length, minimizing total
    distance instead). See duarouter_ego.compute_ego_route_edges()."""
    network_meta = build_network(plan, out_dir)

    ego_speed_factor = compute_ego_speed_factor(
        plan.get("route_static_duration_seconds"),
        plan.get("route_duration_in_traffic_seconds"),
    )
    ego_route_edges = compute_ego_route_edges(network_meta, out_dir, ego_speed_factor, routing_mode=routing_mode)

    rou_file = build_routes(
        network_meta, out_dir, end_time=end_time,
        ego_route_edges=ego_route_edges, ego_speed_factor=ego_speed_factor,
    )
    cfg_file = build_sumocfg(network_meta["net_file"], rou_file, out_dir, end_time=end_time)
    network_meta["rou_file"] = rou_file
    network_meta["cfg_file"] = cfg_file
    network_meta["ego_speed_factor"] = ego_speed_factor
    network_meta["routing_mode"] = routing_mode
    return network_meta


def run_no_preemption_baseline(cfg_path: str, vehicle_id: str = "ego_car_0", max_steps: int = 3600) -> float | None:
    """Runs the scenario with no ACTA preemption at all, to measure a
    vehicle's arrival time against normal (unpreempted) signal timing.

    Defaults to tracking "ego_car_0" (vClass="passenger", speedFactor
    calibrated to live Google traffic -- see build_scenario() above),
    since that's the vehicle meant to be comparable to Google's live
    ETA. Pass vehicle_id="ambulance_0" to instead measure how the
    ambulance itself would have fared with no preemption at all."""
    import traci

    traci.start(["sumo", "-c", cfg_path])
    arrival_time = None
    departed = False
    step = 0
    try:
        while step < max_steps:
            traci.simulationStep()
            step += 1
            if vehicle_id in traci.vehicle.getIDList():
                departed = True
            elif departed and arrival_time is None:
                arrival_time = traci.simulation.getTime()
            if arrival_time is not None and traci.simulation.getMinExpectedNumber() == 0:
                break
    finally:
        traci.close()
    return arrival_time


def main():
    parser = argparse.ArgumentParser(description="Run an ACTA plan through a SUMO/TraCI simulation.")
    parser.add_argument("--plan", required=True, help="Path to a plan JSON (shape of /generate-full-plan response)")
    parser.add_argument("--out", default=None, help="Output scenario directory (default: simulation/scenarios/<origin>_<destination>)")
    parser.add_argument("--gui", action="store_true", help="Run with sumo-gui instead of headless sumo")
    parser.add_argument("--compare", action="store_true", help="Also run a no-preemption baseline for comparison")
    parser.add_argument("--end-time", type=int, default=1200)
    parser.add_argument(
        "--routing-mode", choices=["fastest", "shortest"], default="fastest",
        help="duarouter ego_car objective: 'fastest' (--weight-attribute traveltime, default) or 'shortest' (--weight-attribute length)",
    )
    args = parser.parse_args()

    with open(args.plan, "r", encoding="utf-8") as f:
        plan = json.load(f)

    out_dir = args.out or os.path.join(
        os.path.dirname(__file__), "scenarios",
        f"{plan.get('origin','origin')}_{plan.get('destination','destination')}".replace(" ", "_")[:60],
    )

    print(f"[BUILD] Compiling SUMO network for {len(plan.get('signals', []))} on-route signals -> {out_dir}")
    network_meta = build_scenario(plan, out_dir, end_time=args.end_time, routing_mode=args.routing_mode)
    print(f"[BUILD] Network: {network_meta['net_file']}")
    print(f"[BUILD] Routes:  {network_meta['rou_file']}")
    print(f"[BUILD] Config:  {network_meta['cfg_file']}")
    print(f"[BUILD] ego_car speedFactor (live-traffic calibrated): {network_meta['ego_speed_factor']:.3f}")

    print("\n[RUN] Simulating WITH ACTA preemption...")
    controller = PreemptionController(network_meta, plan)
    result = controller.run(network_meta["cfg_file"], gui=args.gui)

    print(f"\nAmbulance arrival time (with preemption): {result['ambulance_arrival_time']}s")
    print(f"Preemption events: {len(result['events'])}")
    for event in result["events"]:
        print(f"  t={event['sim_time']:>6.0f}s  {event}")

    report = {
        "plan_origin": plan.get("origin"),
        "plan_destination": plan.get("destination"),
        "scenario_dir": out_dir,
        "with_preemption_arrival_s": result["ambulance_arrival_time"],
        "events": result["events"],
    }

    if args.compare:
        print("\n[RUN] Simulating calibrated ego_car baseline WITHOUT preemption (for comparison)...")
        baseline_arrival = run_no_preemption_baseline(network_meta["cfg_file"], vehicle_id="ego_car_0")
        report["without_preemption_arrival_s"] = baseline_arrival
        report["baseline_source"] = "sumo_calibrated_ego_car"
        if baseline_arrival is not None and result["ambulance_arrival_time"] is not None:
            report["time_saved_s"] = round(baseline_arrival - result["ambulance_arrival_time"], 1)
        print(f"ego_car arrival time (no preemption, calibrated): {baseline_arrival}s")

        google_eta = plan.get("route_duration_in_traffic_seconds")
        if google_eta is not None and baseline_arrival is not None:
            report["google_live_traffic_eta_s"] = google_eta
            report["calibration_delta_s"] = round(baseline_arrival - google_eta, 1)
            print(f"Google live-traffic ETA for this corridor: {google_eta}s "
                  f"(SUMO calibration delta: {report['calibration_delta_s']:+.1f}s)")
        if "time_saved_s" in report:
            print(f"Time saved by ACTA preemption (ambulance vs. calibrated ego_car): {report['time_saved_s']}s")

    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n[DONE] Report written to {report_path}")


if __name__ == "__main__":
    main()
