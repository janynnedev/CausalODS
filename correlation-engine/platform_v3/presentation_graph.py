"""Headless progressive views of persisted operational evidence only."""
from __future__ import annotations

import json
from pathlib import Path
import textwrap

STAGES = (
    "01-observed-events", "02-structural-relations", "03-semantic-relations",
    "04-temporal-statistical-evidence", "05-causal-candidates", "06-pruning", "07-rca-result",
)
COLORS = {"trace": "#3b82f6", "log": "#f59e0b", "metric": "#10b981", "business_event": "#a855f7"}
EDGE_COLORS = {"structural": "#64748b", "semantic": "#a855f7", "temporal": "#d97706",
               "statistical": "#0891b2", "causal_candidate": "#dc2626"}


def _key(reference: dict) -> tuple[str, str]:
    return reference["event_id"], reference.get("source_reference") or ""


def export_graphs(incident: dict, rca: dict, output: Path) -> list[Path]:
    from platform_v3.presentation_safety import checked_json
    checked_json([incident, rca])
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    import networkx as nx

    nodes = json.loads(incident["graph_nodes_json"])
    edges = json.loads(incident["graph_edges_json"])
    pruning = json.loads(incident["pruning_json"])
    if not nodes or not edges or not pruning:
        raise ValueError("Persisted graph and pruning required")
    if incident["incident_id"] != rca["incident_id"] or pruning["incident_id"] != incident["incident_id"]:
        raise ValueError("Unlinked graph/RCA/pruning")
    by_key = {_key(node["reference"]): node for node in nodes}
    if len(by_key) != len(nodes):
        raise ValueError("Duplicate graph references")
    graph = nx.DiGraph()
    ordered = sorted(by_key)
    graph.add_nodes_from(ordered)
    for edge in edges:
        source, target = _key(edge["source"]), _key(edge["target"])
        if source not in by_key or target not in by_key or edge["classification"] not in EDGE_COLORS:
            raise ValueError("Invalid persisted graph edge")
        graph.add_edge(source, target)
    positions = nx.spring_layout(graph, seed=42)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    labels = {key: f"{by_key[key].get('service_name') or 'unknown'}\n"
                   f"{by_key[key]['signal_type']} #{index + 1}" for index, key in enumerate(ordered)}
    kept = set(pruning["candidates"])
    excluded = {entry["service_name"] for entry in pruning["exclusions"]}
    ranking = json.loads(rca["ranking_json"])
    shap = json.loads(rca.get("shap_json", "{}"))
    paths = []
    for stage, name in enumerate(STAGES):
        allowed = set()
        if stage >= 1:
            allowed.add("structural")
        if stage >= 2:
            allowed.add("semantic")
        if stage >= 3:
            allowed.update(("temporal", "statistical"))
        if stage >= 4:
            allowed.add("causal_candidate")
        fig, ax = plt.subplots(figsize=(16, 9), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        fig.subplots_adjust(left=.03, right=.70, top=.86, bottom=.20)
        selected_edges = [edge for edge in edges if edge["classification"] in allowed]
        for classification, color in EDGE_COLORS.items():
            for direction in ("directed", "undirected"):
                endpoints = [(_key(edge["source"]), _key(edge["target"])) for edge in selected_edges
                             if edge["classification"] == classification and edge["direction"] == direction]
                if endpoints:
                    nx.draw_networkx_edges(graph, positions, edgelist=endpoints, ax=ax,
                                           edge_color=color, alpha=.35, arrows=direction == "directed",
                                           arrowsize=13, node_size=650,
                                           connectionstyle="arc3,rad=0.07")
        borders = []
        for key in ordered:
            service = by_key[key].get("service_name")
            border = "#ffffff"
            if stage >= 5:
                border = "#15803d" if service in kept else "#9ca3af" if service in excluded else "#ffffff"
            if stage == 6 and service == rca["root_cause_service"]:
                border = "#dc2626"
            borders.append(border)
        nx.draw_networkx_nodes(graph, positions, nodelist=ordered, ax=ax, node_size=650,
                               node_color=[COLORS.get(by_key[key]["signal_type"], "#94a3b8") for key in ordered],
                               edgecolors=borders, linewidths=3)
        nx.draw_networkx_labels(graph, positions, labels, ax=ax, font_size=7)
        ax.set_axis_off()
        fig.suptitle(name.replace("-", " "), fontsize=22, fontweight="bold", x=.04, ha="left")
        fig.text(.04, .91, f"Incident: {incident['incident_id']} | observed nodes: {len(nodes)} | shown edges: {len(selected_edges)}",
                 fontsize=10)
        actual_signals = sorted({node["signal_type"] for node in nodes})
        legends = [Line2D([0], [0], marker="o", color="w", markerfacecolor=COLORS.get(signal, "#94a3b8"),
                          markersize=10, label=signal) for signal in actual_signals]
        for relation in sorted({edge["relation_type"] for edge in selected_edges}):
            example = next(edge for edge in selected_edges if edge["relation_type"] == relation)
            label = relation + (" (causal_candidate)" if relation == "TOPOLOGY_DEPENDENCY" else "")
            legends.append(Line2D([0], [0], color=EDGE_COLORS[example["classification"]], label=label))
        fig.legend(handles=legends, loc="lower left", bbox_to_anchor=(.03, .04), ncol=3, fontsize=9)
        detail = "Observed events only.\nNo absent modality or relation is inferred for this image."
        if stage >= 3:
            detail += "\n\nAssociação ≠ causalidade comprovada."
        if stage >= 4:
            detail += "\nTOPOLOGY_DEPENDENCY = causal_candidate."
        if stage >= 5:
            detail += (f"\n\nPruning: {pruning['before_count']} → {pruning['after_count']}"
                       f"\nPolicy: {pruning['policy_version']}\nMantidos: {', '.join(sorted(kept)) or 'none'}"
                       f"\nExcluídos: {', '.join(sorted(excluded)) or 'none'}")
            for entry in pruning["exclusions"]:
                detail += f"\n{entry['service_name']}: {entry['rule']}"
        if stage == 6:
            detail = (f"RCA: {rca['root_cause_service']}\nConfidence: {rca['confidence']:.3f}"
                      f"\nReliable: {rca['reliable']}\n\nRanking (Top 5):")
            for index, entry in enumerate(ranking[:5], 1):
                scores = entry["scores"]
                detail += (f"\n{index}. {entry['service_name']}: {scores['final_score']:.3f}"
                           f"\n   model probability: {scores['model_probability']}")
            detail += "\n\nSHAP = contribuição do modelo\n(not proof of causality)"
            if isinstance(shap, dict):
                contributions = shap.get(rca["root_cause_service"], {}).get("contributions", [])
                for entry in contributions[:5]:
                    detail += f"\n{entry['feature_name']}: {entry['shap_value']:+.3f}"
        wrapped = "\n".join(textwrap.fill(line, 43) for line in detail.splitlines())
        fig.text(.72, .84, wrapped, va="top", fontsize=10)
        path = output / f"{name}.png"
        try:
            fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        paths.append(path)
    # Audit exact node identity and shared positions without dumping raw telemetry.
    from platform_v3.presentation_demo import write_json
    write_json(output / "layout.json", {
        "seed": 42, "scientific_result": False,
        "nodes": [{"reference": by_key[key]["reference"], "label": labels[key],
                   "position": [float(value) for value in positions[key]]} for key in ordered],
        "stages": [{"file": path.name} for path in paths],
    })
    return paths
