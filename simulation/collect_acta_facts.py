"""
collect_acta_facts.py

Gathers the numbers that the TODO markers in paper.tex ask for, from the
saved /generate-full-plan JSON of each corridor.

Run from the project root (the folder that contains simulation/ and backend/):

    python collect_acta_facts.py plans/kengeri.json plans/hbr.json ... --end-time 1200
    python collect_acta_facts.py plans/*.json --end-time 3600 --out facts.json

Plan-only facts (route length, Google durations, speed factor, case counts,
segment lengths) need nothing but the JSON. Add --sumo to also run SUMO:
preempted ambulance, calibrated ego car, and the ambulance with preemption
switched off. That needs the eclipse-sumo and traci packages.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "simulation"))

from traffic_calibration import compute_ego_speed_factor  # noqa: E402


def sumo_version() -> str | None:
    exe = shutil.which("sumo")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    return out.splitlines()[0].strip() if out else None


def plan_facts(plan: dict) -> dict:
    segs = plan.get("acta_plan", [])
    return {
        "origin": plan.get("origin"),
        "destination": plan.get("destination"),
        "matched_signals": len(plan.get("signals", [])),
        "route_km": round(plan["route_distance_meters"] / 1000, 2) if plan.get("route_distance_meters") else None,
        "google_free_flow_s": plan.get("route_static_duration_seconds"),
        "google_live_s": plan.get("route_duration_in_traffic_seconds"),
        "ego_speed_factor": round(compute_ego_speed_factor(
            plan.get("route_static_duration_seconds"),
            plan.get("route_duration_in_traffic_seconds")), 3),
        "segments": len(segs),
        "cases": dict(Counter(s["case"] for s in segs)),
        "segment_lengths_m": [round(s["distance_meters"]) for s in segs],
    }


def sumo_facts(plan: dict, out_dir: str, end_time: int) -> dict:
    from run_simulation import build_scenario, run_no_preemption_baseline
    from preemption_controller import PreemptionController

    meta = build_scenario(plan, out_dir, end_time=end_time)
    res = PreemptionController(meta, plan).run(meta["cfg_file"])
    ego = run_no_preemption_baseline(meta["cfg_file"], vehicle_id="ego_car_0")
    amb = run_no_preemption_baseline(meta["cfg_file"], vehicle_id="ambulance_0")
    return {
        "end_time_s": end_time,
        "acta_ambulance_arrival_s": res["ambulance_arrival_time"],
        "ego_baseline_arrival_s": ego,
        "ambulance_no_preemption_arrival_s": amb,
        "events": res["events"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", nargs="+")
    ap.add_argument("--end-time", type=int, default=1200)
    ap.add_argument("--sumo", action="store_true", help="also run the simulations")
    ap.add_argument("--out", default="acta_facts.json")
    args = ap.parse_args()

    result = {"sumo_version": sumo_version(), "corridors": []}
    for path in args.plans:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
        row = {"plan_file": path, **plan_facts(plan)}
        if args.sumo:
            out_dir = os.path.join(ROOT, "simulation", "scenarios", os.path.splitext(os.path.basename(path))[0])
            row["sumo"] = sumo_facts(plan, out_dir, args.end_time)
        result["corridors"].append(row)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"SUMO version: {result['sumo_version']}")
    for r in result["corridors"]:
        print(f"\n{r['origin']} -> {r['destination']}")
        print(f"  route {r['route_km']} km, Google free-flow {r['google_free_flow_s']} s, "
              f"live {r['google_live_s']} s, ego speed factor {r['ego_speed_factor']}")
        print(f"  {r['segments']} segments {r['cases']}")
        print(f"  segment lengths (m): {r['segment_lengths_m']}")
        if "sumo" in r:
            s = r["sumo"]
            print(f"  ACTA {s['acta_ambulance_arrival_s']} s | ego {s['ego_baseline_arrival_s']} s | "
                  f"ambulance without preemption {s['ambulance_no_preemption_arrival_s']} s "
                  f"(None means not finished in {s['end_time_s']} s)")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
