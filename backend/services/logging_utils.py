"""Console logging helpers. Kept out of app.py / route_match_service.py so
that business logic stays free of print-formatting concerns."""

from typing import Dict, List


def log_route_analysis(
    origin: str,
    destination: str,
    route_distance_m: float,
    candidate_count: int,
    on_route_signals: List[Dict],
    discarded_signals: List[Dict],
    acta_plan: List[Dict],
) -> None:
    print("=" * 50)
    print("ACTA ROUTE ANALYSIS")
    print("=" * 50)
    print(f"\nRoute:\n{origin}\n\u2192\n{destination}")
    print(f"\nRoute distance:\n{route_distance_m / 1000:.2f} km")
    print(f"\nOSM signals fetched:\n{candidate_count}")
    print(f"\nRoute signals:\n{len(on_route_signals)}")
    print(f"\nDiscarded:\n{len(discarded_signals)}")

    print("\nSignal classification:\n")
    for i, s in enumerate(on_route_signals, start=1):
        print(f"{i}.")
        print(f"ID: {s['signal_id']}")
        print(f"Direction: {s['direction']}")
        print(f"Position: {s['position_on_route_meters'] / 1000:.2f} km")
        print("Status: ON_ROUTE\n")

    for s in discarded_signals:
        print(f"ID: {s['signal_id']}")
        print(f"Direction: {s.get('direction', 'UNKNOWN')}")
        print(f"Status: DISCARDED ({s.get('reason', 'unknown')})\n")

    print("=" * 50)
    print("ACTA SEGMENTS:\n")
    for seg in acta_plan:
        print(f"Signal {seg['from_signal']} \u2192 Signal {seg['to_signal']}")
        dist = seg["distance_meters"]
        dist_str = f"{dist / 1000:.2f}km" if dist >= 1000 else f"{dist:.0f}m"
        print(f"Distance: {dist_str}")
        print(f"Case: {seg['case']}\n")
    print("=" * 50)
