const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function postJSON(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = data?.detail || response.statusText;
    throw new Error(detail);
  }

  return data;
}

export function generateFullPlan({ origin, destination, routingMode, originPlaceId, destinationPlaceId }) {
  return postJSON("/generate-full-plan", {
    origin,
    destination,
    routing_mode: routingMode,
    origin_place_id: originPlaceId ?? null,
    destination_place_id: destinationPlaceId ?? null,
  });
}

export function runSimulation({ origin, destination, compare, endTime, routingMode, originPlaceId, destinationPlaceId }) {
  return postJSON("/simulate", {
    origin,
    destination,
    compare,
    end_time: endTime,
    routing_mode: routingMode,
    origin_place_id: originPlaceId ?? null,
    destination_place_id: destinationPlaceId ?? null,
  });
}

export function runSimulationForPlan({ plan, compare, endTime }) {
  return postJSON("/simulate-plan", {
    plan,
    compare,
    end_time: endTime,
  });
}

export { API_BASE };
