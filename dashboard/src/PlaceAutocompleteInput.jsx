import { useEffect, useRef, useState } from "react";
import { loadGoogleMapsPlaces } from "./googleMapsLoader";

// Bengaluru metro bias, matching the backend's RouteService bias
// (services/route_service.py: BLR_CENTER_LAT/LNG, BLR_BIAS_RADIUS_M).
// This is a *soft* bias for autocomplete predictions -- it makes
// Bengaluru-area results rank first without excluding everything else,
// which is what `componentRestrictions` (country) is for below.
const BLR_CENTER = { lat: 12.9716, lng: 77.5946 };
const BLR_BIAS_RADIUS_M = 50000;

/**
 * A text input backed by Google's PlaceAutocompleteElement
 * (google.maps.places, the current-generation widget --
 * google.maps.places.Autocomplete is deprecated for new customers as
 * of March 2025). Restricts results to India and biases them toward
 * the Bengaluru metro area, per the project's fuzzy-input requirement
 * ("sda hospital" should resolve locally, not cross-country).
 *
 * Reports BOTH the resolved place_id (preferred -- passed straight to
 * the backend's Routes API call as `placeId`, skipping re-geocoding)
 * and the formatted address (fallback display / for manually-typed
 * values that were never run through Autocomplete) via onPlaceChange.
 */
export default function PlaceAutocompleteInput({
  value,
  onPlaceChange,
  placeholder,
  required,
  id,
}) {
  const containerRef = useRef(null);
  const elementRef = useRef(null);
  const [loadError, setLoadError] = useState(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    loadGoogleMapsPlaces()
      .then((places) => {
        if (cancelled || !containerRef.current) return;

        const el = new places.PlaceAutocompleteElement({
          componentRestrictions: { country: "in" },
          locationBias: {
            center: BLR_CENTER,
            radius: BLR_BIAS_RADIUS_M,
          },
        });
        el.id = id;
        if (placeholder) el.setAttribute("placeholder", placeholder);
        if (required) el.setAttribute("required", "");
        el.classList.add("place-autocomplete-el");

        el.addEventListener("gmp-select", async (event) => {
          try {
            const place = event.placePrediction.toPlace();
            await place.fetchFields({ fields: ["id", "formattedAddress", "displayName"] });
            onPlaceChange({
              placeId: place.id,
              formattedAddress: place.formattedAddress || place.displayName || "",
            });
          } catch (err) {
            // Selection resolution failed (e.g. transient network
            // error) -- leave the previous value in place rather than
            // silently clearing what the user picked.
            console.error("Place selection failed:", err);
          }
        });

        containerRef.current.appendChild(el);
        elementRef.current = el;
        setReady(true);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message);
      });

    return () => {
      cancelled = true;
      if (elementRef.current) {
        elementRef.current.remove();
        elementRef.current = null;
      }
    };
    // Intentionally mount once: PlaceAutocompleteElement manages its
    // own internal input state after creation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loadError) {
    // Fall back to a plain text input so the form still works (with
    // manually-typed addresses, no place_id) if the Maps script can't
    // load -- e.g. missing/misconfigured VITE_GOOGLE_MAPS_API_KEY.
    return (
      <>
        <input
          id={id}
          required={required}
          value={value}
          onChange={(e) => onPlaceChange({ placeId: null, formattedAddress: e.target.value })}
          placeholder={placeholder}
        />
        <div className="field-hint field-hint--error">
          Autocomplete unavailable: {loadError}
        </div>
      </>
    );
  }

  return (
    <div className="place-autocomplete-container" ref={containerRef}>
      {!ready && (
        <input disabled placeholder="Loading address search…" />
      )}
    </div>
  );
}
