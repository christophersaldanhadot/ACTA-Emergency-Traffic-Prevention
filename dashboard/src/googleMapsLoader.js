// Loads the Google Maps JavaScript API (places library) exactly once,
// no matter how many components request it, and returns the same
// promise to every caller. This key is client-side and visible in the
// built bundle -- that's expected for Google's browser-side Places
// widgets (Google's own guidance is to restrict it by HTTP referrer in
// Cloud Console, not to keep it secret). It is intentionally a
// SEPARATE key from the backend's GOOGLE_MAPS_API_KEY (used
// server-side for the Routes API), which must never be exposed to the
// browser.
let loadPromise = null;

export function loadGoogleMapsPlaces() {
  if (loadPromise) return loadPromise;

  const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;

  loadPromise = new Promise((resolve, reject) => {
    if (window.google?.maps?.places) {
      resolve(window.google.maps.places);
      return;
    }
    if (!apiKey) {
      reject(
        new Error(
          "VITE_GOOGLE_MAPS_API_KEY is not set (see dashboard/.env.example). " +
            "Address autocomplete needs a browser-side Google Maps API key with " +
            "the Places API enabled and HTTP-referrer restrictions set in Cloud Console."
        )
      );
      return;
    }

    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&v=weekly&loading=async&callback=__acta_gmaps_loaded`;
    script.async = true;
    window.__acta_gmaps_loaded = async () => {
      try {
        const places = await window.google.maps.importLibrary("places");
        resolve(places);
      } catch (err) {
        reject(err);
      }
    };
    script.onerror = () => reject(new Error("Failed to load the Google Maps JavaScript API script."));
    document.head.appendChild(script);
  });

  return loadPromise;
}
