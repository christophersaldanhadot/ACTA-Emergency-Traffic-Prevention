import asyncio

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from services.route_service import RouteService
from services.polyline_decoder import decode_polyline
from services.signal_service import SignalService
from services.route_match_service import RouteMatchService


async def main():

    route_service = RouteService()
    signal_service = SignalService()
    match_service = RouteMatchService()

    # -------------------------------------------------
    # 1. Generate Google route A -> B
    # -------------------------------------------------

    route = await route_service.get_route(
        "Christ University Kengeri Campus",
        "Rajarajeshwari Nagar Metro Station"
    )

    coordinates = decode_polyline(
        route["polyline"]
    )

    print(
        f"Route points: {len(coordinates)}"
    )

    # -------------------------------------------------
    # 2. Get OSM traffic signals
    # -------------------------------------------------

    signals = await signal_service.get_signals(
        coordinates
    )

    print(
        f"Total candidate signals fetched: "
        f"{len(signals)}"
    )

    # -------------------------------------------------
    # 3. Create directed A -> B route geometry
    # -------------------------------------------------

    route_geometry = (
        match_service.create_route_geometry(
            coordinates
        )
    )

    print(
        f"Route geometry length: "
        f"{route_geometry['length']:.2f} metres"
    )

    # -------------------------------------------------
    # 4. Determine route relationship
    # -------------------------------------------------

    print(
        "\n========== ROUTE SIGNAL CLASSIFICATION =========="
    )

    on_route_signals = []

    for signal in signals:

        result = (
            match_service.signal_position_on_route(
                signal,
                route_geometry
            )
        )

        print(
            f"\nSignal ID: {signal['id']}"
        )

        print(
            f"OSM coordinates: "
            f"{signal['latitude']}, "
            f"{signal['longitude']}"
        )

        print(
            f"Projected route position: "
            f"{result['position_on_route_meters']:.3f} m"
        )

        print(
            f"Nearest route point: "
            f"{result['nearest_route_point']}"
        )

        # -------------------------------------------------
        # IMPORTANT:
        # Do not use 30m as the ACTA rule.
        #
        # For now this is only a geometric candidate
        # relationship. We will establish the exact
        # route-membership test separately.
        # -------------------------------------------------

        on_route_signals.append({
            "id": signal["id"],
            "latitude": signal["latitude"],
            "longitude": signal["longitude"],
            "position_on_route_meters":
                result["position_on_route_meters"]
        })

    # -------------------------------------------------
    # 5. Sort according to A -> B direction
    # -------------------------------------------------

    on_route_signals.sort(
        key=lambda x:
            x["position_on_route_meters"]
    )

    # -------------------------------------------------
    # 6. Final ordered signals
    # -------------------------------------------------

    print(
        "\n========== SIGNALS ON A -> B ROUTE =========="
    )

    for index, signal in enumerate(
        on_route_signals,
        start=1
    ):

        print(
            f"{index}. "
            f"Signal {signal['id']} | "
            f"Route position: "
            f"{signal['position_on_route_meters']:.2f} m | "
            f"Coordinates: "
            f"{signal['latitude']}, "
            f"{signal['longitude']}"
        )


if __name__ == "__main__":

    asyncio.run(main())