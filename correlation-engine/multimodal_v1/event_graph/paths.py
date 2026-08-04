from __future__ import annotations

from collections import deque
from typing import Iterable

from multimodal_v1.evidence.contracts import EventReference
from multimodal_v1.event_graph.contracts import (
    EdgeClassification, EdgeDirection, EventEvidenceGraph, reference_key,
)


class GraphPathExtractor:
    def __init__(self, graph: EventEvidenceGraph):
        if not isinstance(graph, EventEvidenceGraph):
            raise TypeError("GraphPathExtractor requer EventEvidenceGraph.")
        self.graph = graph
        self._nodes = {node.reference for node in graph.nodes}

    def adjacency(self, *, classifications: Iterable[EdgeClassification | str] | None = None,
                  reverse: bool = False) -> dict[EventReference, tuple[EventReference, ...]]:
        allowed = None if classifications is None else {EdgeClassification(item) for item in classifications}
        adjacent = {node.reference: set() for node in self.graph.nodes}
        for edge in self.graph.edges:
            if allowed is not None and edge.classification not in allowed:
                continue
            source, target = (edge.target, edge.source) if reverse else (edge.source, edge.target)
            adjacent[source].add(target)
            if edge.direction is EdgeDirection.UNDIRECTED:
                adjacent[target].add(source)
        return {
            node: tuple(sorted(targets, key=reference_key))
            for node, targets in sorted(adjacent.items(), key=lambda item: reference_key(item[0]))
        }

    def shortest_path(self, source: EventReference, target: EventReference, *,
                      classifications: Iterable[EdgeClassification | str] | None = None,
                      reverse: bool = False) -> tuple[EventReference, ...]:
        self._validate_endpoint(source, "source")
        self._validate_endpoint(target, "target")
        if source == target:
            return (source,)
        adjacency = self.adjacency(classifications=classifications, reverse=reverse)
        queue = deque([(source, (source,))])
        visited = {source}
        while queue:
            node, path = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor == target:
                    return (*path, neighbor)
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, (*path, neighbor)))
        return ()

    def paths(self, source: EventReference, target: EventReference, *,
              max_depth: int = 12,
              classifications: Iterable[EdgeClassification | str] | None = None,
              ) -> tuple[tuple[EventReference, ...], ...]:
        self._validate_endpoint(source, "source")
        self._validate_endpoint(target, "target")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
            raise ValueError("max_depth deve ser inteiro >= 0.")
        adjacency = self.adjacency(classifications=classifications)
        found = []

        def visit(node, path):
            if len(path) - 1 > max_depth:
                return
            if node == target:
                found.append(tuple(path))
                return
            for neighbor in adjacency[node]:
                if neighbor not in path:
                    visit(neighbor, (*path, neighbor))

        visit(source, (source,))
        return tuple(sorted(found, key=lambda path: (len(path), tuple(reference_key(ref) for ref in path))))

    def _validate_endpoint(self, reference, name):
        if not isinstance(reference, EventReference):
            raise TypeError(f"{name} deve ser EventReference.")
        if reference not in self._nodes:
            raise KeyError(reference)
