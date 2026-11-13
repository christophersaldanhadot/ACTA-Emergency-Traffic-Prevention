class ExecutionPlanner:
    """
    Turns ActaEngine decisions into concrete signal-preemption actions.

    Note: /generate-plan (the simulation endpoint) only receives raw
    inter-signal distances, with no real signal identity attached, so
    actions here are keyed by segment index ("segment_1", "segment_2",
    ...) rather than invented letters. Callers that have real OSM
    signal ids should use ActaEngine.build_plan_from_signals() instead,
    which already returns real from_signal/to_signal ids.
    """

    @staticmethod
    def generate_actions(decisions: list) -> list:
        actions = []

        for index, item in enumerate(decisions, start=1):
            decision = item["decision"]
            segment_label = f"segment_{index}"

            if decision == "IMMEDIATE":
                actions.append({
                    "action": "TRIGGER_IMMEDIATE",
                    "segment": segment_label,
                    "distance": item["distance"],
                })

            elif decision == "WAYPOINT":
                actions.append({
                    "action": "TRIGGER_AT_WAYPOINT",
                    "segment": segment_label,
                    "distance": item["distance"],
                    "position": item["waypoint"],
                })

            elif decision == "MULTIPLE_WAYPOINTS":
                actions.append({
                    "action": "TRIGGER_AT_MULTIPLE_WAYPOINTS",
                    "segment": segment_label,
                    "distance": item["distance"],
                    "positions": item["waypoints"],
                })

        return actions
