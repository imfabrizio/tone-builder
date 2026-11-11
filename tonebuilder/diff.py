"""Differenze tra due ricette: cosa ha cambiato l'AI, in forma strutturata per la UI."""
from __future__ import annotations

from typing import Any

from .recipe import ToneRecipe


def _name(b) -> str:
    return b.label or b.gear_ref or b.id


def _num(v: float) -> float:
    return round(float(v), 2)


def diff_recipes(old: ToneRecipe, new: ToneRecipe) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    blocks: set[str] = set()
    cells: set[tuple[str, str]] = set()
    ob = {b.id: b for b in old.chain}
    nb = {b.id: b for b in new.chain}

    for bid, b in nb.items():
        if bid not in ob:
            items.append({"type": "added", "block": bid, "name": _name(b), "role": b.role, "line": b.line})
            blocks.add(bid)
            continue
        o = ob[bid]
        if (o.gear_ref, o.helix.model, o.qc.model) != (b.gear_ref, b.helix.model, b.qc.model):
            items.append({"type": "model", "block": bid, "old": _name(o), "new": _name(b), "role": b.role})
            blocks.add(bid)
        if o.line != b.line:
            items.append({"type": "line", "block": bid, "name": _name(b), "old": o.line, "new": b.line})
            blocks.add(bid)
        if o.enabled != b.enabled:
            items.append({"type": "enabled", "block": bid, "name": _name(b), "new": b.enabled})
            blocks.add(bid)
        for k in sorted(set(o.settings) | set(b.settings)):
            ov, nv = o.settings.get(k), b.settings.get(k)
            if ov is None or nv is None or abs(ov - nv) > 0.05:
                items.append({"type": "knob", "block": bid, "name": _name(b), "knob": k,
                              "old": None if ov is None else _num(ov), "new": None if nv is None else _num(nv)})
                blocks.add(bid)
        if (o.ir or "") != (b.ir or ""):
            items.append({"type": "ir", "block": bid, "name": _name(b), "old": o.ir or "", "new": b.ir or ""})
            blocks.add(bid)
    for bid, o in ob.items():
        if bid not in nb:
            items.append({"type": "removed", "block": bid, "name": _name(o), "role": o.role})

    os_ = {s.name: s for s in old.snapshots}
    for s in new.snapshots:
        o = os_.get(s.name)
        if o is None:
            items.append({"type": "snap_added", "snap": s.name})
            continue
        for bid in nb:
            b = nb[bid]
            was = o.enabled.get(bid, ob[bid].enabled if bid in ob else b.enabled)
            now = s.enabled.get(bid, b.enabled)
            if bid in ob and was != now:
                items.append({"type": "snap_enabled", "snap": s.name, "block": bid, "name": _name(b), "new": now})
                cells.add((bid, s.name))
            oo, no = o.overrides.get(bid, {}), s.overrides.get(bid, {})
            for k in sorted(set(oo) | set(no)):
                a, c = oo.get(k), no.get(k)
                if a is None or c is None or abs(a - c) > 0.05:
                    items.append({"type": "snap_knob", "snap": s.name, "block": bid, "name": _name(b), "knob": k,
                                  "old": None if a is None else _num(a), "new": None if c is None else _num(c)})
                    cells.add((bid, s.name))
        if (o.level_db or 0) != (s.level_db or 0):
            items.append({"type": "snap_level", "snap": s.name, "old": o.level_db, "new": s.level_db})
    names = {s.name for s in new.snapshots}
    for s in old.snapshots:
        if s.name not in names:
            items.append({"type": "snap_removed", "snap": s.name})
    if old.name != new.name:
        items.append({"type": "rename", "old": old.name, "new": new.name})
    return {"items": items, "blocks": sorted(blocks), "cells": sorted([list(c) for c in cells])}
