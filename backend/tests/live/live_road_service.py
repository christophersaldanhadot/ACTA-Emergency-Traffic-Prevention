import asyncio

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from services.road_service import RoadService


async def main():

    service = RoadService()

    signals = [
        {
            "id": "test_1",
            "latitude": 12.9364154,
            "longitude": 77.518236
        },
        {
            "id": "test_2",
            "latitude": 12.9127956,
            "longitude": 77.485376
        }
    ]

    results = await service.get_nearest_roads(
        signals
    )

    print("\n========== ROAD ASSOCIATION ==========")

    for result in results:

        print(
            f"Signal: "
            f"{result['signal_id']}"
        )

        print(
            f"Signal coordinates: "
            f"{result['signal_latitude']}, "
            f"{result['signal_longitude']}"
        )

        print(
            f"Road coordinates: "
            f"{result['road_latitude']}, "
            f"{result['road_longitude']}"
        )

        print(
            f"Road Place ID: "
            f"{result['place_id']}"
        )

        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(main())