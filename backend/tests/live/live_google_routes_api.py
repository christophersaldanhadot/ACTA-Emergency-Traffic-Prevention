import os
import requests
import json
from dotenv import load_dotenv

# Load variables from .env file explicitly
load_dotenv()

def test_google_routes_api():
    # 1. Pull API key from environment
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("❌ Error: GOOGLE_MAPS_API_KEY environment variable not set in your .env file.")
        print("Please check that your .env file contains: GOOGLE_MAPS_API_KEY=AIzaSy...")
        return

    # 2. Fully qualified modern Routes API endpoint (escaped cleanly)
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    # 3. Construct headers (FieldMask is mandatory for the new Routes API)
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": str(api_key).strip(),
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
    }

    # 4. Construct payload using your specific FYP landmarks
    payload = {
        "origin": {
            "address": "Christ University Kengeri Campus, Bengaluru"
        },
        "destination": {
            "address": "Manipal Hospital Whitefield, Bengaluru"
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE", # Critical for ambulance research context
        "computeAlternativeRoutes": False
    }

    print(f"Targeting URL: {url}")
    print("Sending request to Routes API...")
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            print("\n✅ Success! Response received from Google Maps Routes API:")
            print(json.dumps(response.json(), indent=2))
        elif response.status_code == 400:
            print(f"\n❌ Error 400 (Bad Request): Check fieldmask or payload rules.")
            print(response.text)
        elif response.status_code == 403:
            print(f"\n❌ Error 403 (Forbidden): Check if your API key is correct and Routes API is enabled.")
            print(response.text)
        else:
            print(f"\n❌ Failed! Status Code: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"An exception occurred: {e}")

if __name__ == "__main__":
    test_google_routes_api()
