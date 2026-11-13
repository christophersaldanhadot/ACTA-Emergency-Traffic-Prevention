"""
Tests for duarouter_ego.compute_ego_route_edges.

`duarouter` itself is not invoked (no SUMO binary assumed available in
CI) -- subprocess.run is monkeypatched to write a canned .rou.xml
output, matching what real duarouter would produce, and we assert on
(a) the CLI args duarouter_ego builds and (b) the resulting return
value / telemetry log derived from that canned output.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import duarouter_ego  # noqa: E402
from duarouter_ego import compute_ego_route_edges  # noqa: E402


def _write_net_file(path: str, edge_lengths: dict) -> None:
    """Writes a minimal .net.xml with one <edge><lane length=".."/></edge>
    per entry in edge_lengths, enough for _sum_edge_lengths to parse."""
    lines = ["<net>"]
    for edge_id, length in edge_lengths.items():
        lines.append(f'  <edge id="{edge_id}">')
        lines.append(f'    <lane id="{edge_id}_0" length="{length}"/>')
        lines.append("  </edge>")
    lines.append("</net>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _fake_duarouter_run(duarouted_path: str, edges: list[str]):
    """Returns a subprocess.run replacement that ignores the real
    duarouter binary and instead writes a canned route output file,
    as if duarouter had chosen `edges`."""

    def _run(args, capture_output=True, text=True):
        edges_str = " ".join(edges)
        route_xml = (
            "<routes>\n"
            f'  <vehicle id="{duarouter_ego.EGO_VTYPE_ID}">\n'
            f'    <route edges="{edges_str}"/>\n'
            "  </vehicle>\n"
            "</routes>\n"
        )
        with open(duarouted_path, "w", encoding="utf-8") as f:
            f.write(route_xml)

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    return _run


@pytest.fixture
def network_meta(tmp_path):
    net_file = str(tmp_path / "scenario.net.xml")
    main_fwd_edges = ["e1", "e2", "e3"]
    _write_net_file(net_file, {"e1": 2000.0, "e2": 1620.0, "e3": 2000.0})  # sums to 5620m = 5.62km
    return {"net_file": net_file, "main_fwd_edges": main_fwd_edges}


def test_fastest_mode_passes_weight_attribute_traveltime(network_meta, tmp_path, monkeypatch, capsys):
    """REGRESSION: 'fastest' must explicitly pass
    --weight-attribute traveltime, not rely on duarouter's implicit
    default."""
    captured_args = {}

    def run_and_capture(args, capture_output=True, text=True):
        captured_args["args"] = args
        duarouted_path = str(tmp_path / "ego_route.rou.xml")
        # duarouter_ego always uses out_dir/ego_route.rou.xml
        for i, a in enumerate(args):
            if a == "--output-file":
                duarouted_path = args[i + 1]
        return _fake_duarouter_run(duarouted_path, network_meta["main_fwd_edges"])(args)

    monkeypatch.setattr(duarouter_ego.subprocess, "run", run_and_capture)

    out_dir = str(tmp_path / "out")
    edges = compute_ego_route_edges(network_meta, out_dir, routing_mode="fastest")

    args = captured_args["args"]
    assert "--weight-attribute" in args
    idx = args.index("--weight-attribute")
    assert args[idx + 1] == "traveltime"
    assert edges == network_meta["main_fwd_edges"]


def test_shortest_mode_passes_weight_attribute_length(network_meta, tmp_path, monkeypatch):
    """'shortest' must explicitly pass --weight-attribute length."""
    captured_args = {}

    def run_and_capture(args, capture_output=True, text=True):
        captured_args["args"] = args
        duarouted_path = str(tmp_path / "ego_route.rou.xml")
        for i, a in enumerate(args):
            if a == "--output-file":
                duarouted_path = args[i + 1]
        return _fake_duarouter_run(duarouted_path, network_meta["main_fwd_edges"])(args)

    monkeypatch.setattr(duarouter_ego.subprocess, "run", run_and_capture)

    out_dir = str(tmp_path / "out")
    edges = compute_ego_route_edges(network_meta, out_dir, routing_mode="shortest")

    args = captured_args["args"]
    assert "--weight-attribute" in args
    idx = args.index("--weight-attribute")
    assert args[idx + 1] == "length"
    assert edges == network_meta["main_fwd_edges"]


def test_telemetry_log_reflects_shortest_distance_mode(network_meta, tmp_path, monkeypatch, capsys):
    """REGRESSION: system telemetry log must reflect the selected mode
    and the actual computed distance, e.g.
    '[INFO] Route computed (mode=ShortestDistance, 5.62km)'."""

    def run_and_write(args, capture_output=True, text=True):
        duarouted_path = args[args.index("--output-file") + 1]
        return _fake_duarouter_run(duarouted_path, network_meta["main_fwd_edges"])(args)

    monkeypatch.setattr(duarouter_ego.subprocess, "run", run_and_write)

    out_dir = str(tmp_path / "out")
    compute_ego_route_edges(network_meta, out_dir, routing_mode="shortest")

    captured = capsys.readouterr()
    assert "[INFO] Route computed (mode=ShortestDistance, 5.62km)" in captured.out


def test_telemetry_log_reflects_fastest_time_mode(network_meta, tmp_path, monkeypatch, capsys):
    def run_and_write(args, capture_output=True, text=True):
        duarouted_path = args[args.index("--output-file") + 1]
        return _fake_duarouter_run(duarouted_path, network_meta["main_fwd_edges"])(args)

    monkeypatch.setattr(duarouter_ego.subprocess, "run", run_and_write)

    out_dir = str(tmp_path / "out")
    compute_ego_route_edges(network_meta, out_dir, routing_mode="fastest")

    captured = capsys.readouterr()
    assert "[INFO] Route computed (mode=FastestTime, 5.62km)" in captured.out


def test_rejects_unknown_routing_mode(network_meta, tmp_path):
    with pytest.raises(ValueError, match="Unknown routing_mode"):
        compute_ego_route_edges(network_meta, str(tmp_path / "out"), routing_mode="scenic")
