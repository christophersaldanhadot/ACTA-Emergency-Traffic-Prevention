# backend/services/polyline_decoder.py
from typing import List, Dict

def decode_polyline(polyline_str: str) -> List[Dict[str, float]]:
    """
    Decodes a Google encoded polyline string into an ordered sequence of GPS coordinates.
    Algorithm details: Pure Python bitwise operations matching standard Google format.
    """
    index, lat, lng = 0, 0, 0
    coordinates = []
    changes = {'latitude': 0, 'longitude': 0}

    while index < len(polyline_str):
        for unit in ['latitude', 'longitude']:
            shift, result = 0, 0
            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not (byte & 0x20):
                    break
            
            # Invert values if the terminal bit sign is flipped
            if result & 1:
                changes[unit] = ~(result >> 1)
            else:
                changes[unit] = (result >> 1)

        lat += changes['latitude']
        lng += changes['longitude']

        # Coordinates are scaled by a factor of 1e5 in the encoded string format
        coordinates.append({
            "lat": lat / 100000.0,
            "lng": lng / 100000.0
        })

    return coordinates
