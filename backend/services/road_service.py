import os
import httpx
from dotenv import load_dotenv

load_dotenv()


class RoadService:

    def __init__(self):

        self.api_key = os.getenv(
            "GOOGLE_MAPS_API_KEY"
        )

        self.snap_url = (
            "https://roads.googleapis.com/v1/snapToRoads"
        )

        self.nearest_url = (
            "https://roads.googleapis.com/v1/nearestRoads"
        )

    # ---------------------------------------------------------
    # Snap sequential route points to roads
    # ---------------------------------------------------------
    async def snap_route_to_roads(self, coordinates):

        if not self.api_key:
            raise ValueError(
                "GOOGLE_MAPS_API_KEY not found."
            )

        if not coordinates:
            return []

        all_snapped_points = []

        # Google Roads API maximum = 100 points/request
        batch_size = 100

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:

            for start in range(
                0,
                len(coordinates),
                batch_size
            ):

                batch = coordinates[
                    start:start + batch_size
                ]

                path = "|".join(
                    f"{point['lat']},{point['lng']}"
                    for point in batch
                )

                params = {
                    "path": path,
                    "interpolate": "true",
                    "key": self.api_key
                }

                print(
                    f"\nSnapping route points "
                    f"{start + 1} - "
                    f"{start + len(batch)}"
                )

                response = await client.get(
                    self.snap_url,
                    params=params
                )

                if response.status_code != 200:

                    raise Exception(
                        f"Google Snap to Roads failed "
                        f"({response.status_code}): "
                        f"{response.text}"
                    )

                data = response.json()

                snapped = data.get(
                    "snappedPoints",
                    []
                )

                all_snapped_points.extend(
                    snapped
                )

        print(
            f"\nTotal snapped route points: "
            f"{len(all_snapped_points)}"
        )

        return all_snapped_points

    # ---------------------------------------------------------
    # Get unique road segments from route
    # ---------------------------------------------------------
    async def get_route_road_segments(
        self,
        coordinates
    ):

        snapped_points = (
            await self.snap_route_to_roads(
                coordinates
            )
        )

        route_segments = []

        seen = set()

        for point in snapped_points:

            place_id = point.get(
                "placeId"
            )

            if not place_id:
                continue

            if place_id in seen:
                continue

            seen.add(place_id)

            route_segments.append({
                "place_id": place_id,
                "latitude": point[
                    "location"
                ]["latitude"],
                "longitude": point[
                    "location"
                ]["longitude"]
            })

        print(
            f"Unique route road segments: "
            f"{len(route_segments)}"
        )

        return route_segments

    # ---------------------------------------------------------
    # Find nearest road for traffic signals
    # ---------------------------------------------------------
    async def get_nearest_roads(
        self,
        signals
    ):

        if not self.api_key:
            raise ValueError(
                "GOOGLE_MAPS_API_KEY not found."
            )

        if not signals:
            return []

        results = []

        batch_size = 100

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:

            for start in range(
                0,
                len(signals),
                batch_size
            ):

                batch = signals[
                    start:start + batch_size
                ]

                points = "|".join(
                    f"{signal['latitude']},"
                    f"{signal['longitude']}"
                    for signal in batch
                )

                params = {
                    "points": points,
                    "key": self.api_key
                }

                response = await client.get(
                    self.nearest_url,
                    params=params
                )

                if response.status_code != 200:

                    raise Exception(
                        f"Google Nearest Roads failed "
                        f"({response.status_code}): "
                        f"{response.text}"
                    )

                data = response.json()

                for point in data.get(
                    "snappedPoints",
                    []
                ):

                    original_index = point.get(
                        "originalIndex"
                    )

                    if original_index is None:
                        continue

                    if original_index >= len(batch):
                        continue

                    signal = batch[
                        original_index
                    ]

                    results.append({

                        "signal_id":
                            signal["id"],

                        "signal_latitude":
                            signal["latitude"],

                        "signal_longitude":
                            signal["longitude"],

                        "road_latitude":
                            point["location"][
                                "latitude"
                            ],

                        "road_longitude":
                            point["location"][
                                "longitude"
                            ],

                        "place_id":
                            point.get(
                                "placeId"
                            )
                    })

        return results