"""Generic graph solver for the topology engine.

Resolves node outputs by:
  1. a demand pass (sum of each node's grid-independent electrical load),
  2. a topological-order evaluation along the supply/transport links, so each
     node sees its upstream inputs before it runs.

The current Marin topology is a DAG, so one ordered pass is exact and
reproduces the previous hand-written cascade. The `passes` parameter allows a
future builder topology with feedback loops to iterate to a fixed point; for a
DAG the second pass changes nothing.
"""
from __future__ import annotations

from . import archetypes


def _toposort(nodes, links):
    ids = [n["id"] for n in nodes]
    incoming = {i: set() for i in ids}
    outgoing = {i: [] for i in ids}
    for ln in links:
        incoming[ln["dst"]].add(ln["src"])
        outgoing[ln["src"]].append(ln["dst"])
    ready = [i for i in ids if not incoming[i]]
    order, seen = [], set()
    while ready:
        n = ready.pop(0)
        if n in seen:
            continue
        seen.add(n); order.append(n)
        for m in outgoing[n]:
            incoming[m].discard(n)
            if not incoming[m] and m not in seen:
                ready.append(m)
    # append any nodes left (cycles) in declaration order so solving still runs
    for i in ids:
        if i not in seen:
            order.append(i)
    return order


def solve(ctx, topo, advance, passes=1):
    nodes = {n["id"]: n for n in topo["nodes"]}
    links = topo["links"]
    order = _toposort(topo["nodes"], links)

    total_demand = sum(archetypes.demand(n["arch"], ctx, n) for n in topo["nodes"])

    out = {}
    for p in range(max(1, passes)):
        adv = advance and p == 0  # integrate stateful effects once
        for nid in order:
            node = nodes[nid]
            inputs = {}
            for ln in links:
                if ln["dst"] == nid and ln["src"] in out:
                    inputs[ln["role"]] = out[ln["src"]]
            if node["arch"] == "grid_bus":
                inputs["_demand"] = total_demand
            out[nid] = archetypes.evaluate(node["arch"], ctx, node, inputs, adv)
    return out
