import asyncio

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from services.route_service import RouteService
from services.polyline_decoder import decode_polyline
from services.road_service import RoadService


async def main():

    route_service = RouteService()
    road_service = RoadService()

    # ---------------------------------------------------------
    # Generate ACTA route
    # ---------------------------------------------------------

    route = await route_service.get_route(
        "Christ University Kengeri Campus",
        "Rajarajeshwari Nagar Metro Station"
    )

    coordinates = decode_polyline(
        route["polyline"]
    )

    print(
        f"\nRoute coordinates: "
        f"{len(coordinates)}"
    )

    # ---------------------------------------------------------
    # Snap route to Google road network
    # ---------------------------------------------------------

    route_segments = (
        await road_service.get_route_road_segments(
            coordinates
        )
    )

    print(
        "\n========== ROUTE ROAD SEGMENTS =========="
    )

    for i, segment in enumerate(
        route_segments,
        start=1
    ):

        print(
            f"{i}. "
            f"{segment['place_id']} | "
            f"{segment['latitude']}, "
            f"{segment['longitude']}"
        )

    print(
        "=========================================="
    )


if __name__ == "__main__":

    asyncio.run(main())