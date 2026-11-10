"""Importazione di preset esistenti nel formato ricetta.

Supporta:
- .hlx  (Helix/HX/Native, es. scaricati da CustomTone): JSON in chiaro;
- .hsp  (Helix Stadium): letto tramite `helixgen view`;
- .json (una ricetta di questo programma).

Dei parametri teniamo due copie: i valori nativi (helix.params) per non
perdere precisione e le manopole neutre per poter tradurre verso il QC.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .catalogs.helix import HelixCatalog, HelixModel, humanize
from .knobs import KNOB_ALIASES, find_param, to_neutral
from .recipe import Block, DeviceModel, Footswitch, Role, Snapshot, ToneRecipe

_NEUTRAL_KNOBS = [k for k in KNOB_ALIASES if k not in ("drive", "level")]


def _role_from_model(model_id: str, cat: HelixCatalog) -> Role:
    m = cat.get(model_id)
    if m:
        return m.role
    s = model_id.lower()
    for key, role in [("cab", Role.cab), ("impulse", Role.ir), ("preamp", Role.preamp), ("amp", Role.amp),
                      ("dist", Role.drive), ("drive", Role.drive), ("fuzz", Role.drive), ("comp", Role.comp),
                      ("gate", Role.gate), ("delay", Role.delay), ("reverb", Role.reverb), ("chorus", Role.mod),
                      ("flang", Role.mod), ("phas", Role.mod), ("trem", Role.mod), ("wah", Role.wah),
                      ("pitch", Role.pitch), ("eq", Role.eq), ("vol", Role.volume)]:
        if key in s:
            return role
    return Role.other


def _neutral(params: dict[str, Any], model: HelixModel | None) -> dict[str, float]:
    if not model:
        return {}
    out: dict[str, float] = {}
    used: set[str] = set()
    for knob in _NEUTRAL_KNOBS:
        p = find_param(knob, model.params)
        if p and p.name in params and p.name not in used and isinstance(params[p.name], (int, float)):
            out[knob] = to_neutral(knob, float(params[p.name]), p)
            used.add(p.name)
    return out


def _scalar(v: Any) -> Any:
    if isinstance(v, dict):
        if "@value" in v:
            return v["@value"]
        if "value" in v:
            return v["value"]
    return v


# ------------------------------------------------------------------ .hlx

def from_hlx(data: dict[str, Any], name_hint: str = "") -> ToneRecipe:
    cat = HelixCatalog.load()
    tone = data.get("data", {}).get("tone", {})
    meta = data.get("data", {}).get("meta", {})
    blocks: list[Block] = []
    index: dict[tuple[str, str], str] = {}  # (dsp, blockKey) -> id
    counts: dict[str, int] = {}

    for dsp in ("dsp0", "dsp1"):
        d = tone.get(dsp) or {}
        keys = sorted((k for k in d if re.fullmatch(r"block\d+", k)),
                      key=lambda k: (d[k].get("@path", 0), d[k].get("@position", 0)))
        for k in keys:
            raw = d[k]
            mid = raw.get("@model", "")
            role = _role_from_model(mid, cat)
            counts[role.value] = counts.get(role.value, 0) + 1
            bid = f"{role.value}{counts[role.value]}"
            native = {pk: _scalar(pv) for pk, pv in raw.items() if not pk.startswith("@")}
            model = cat.get(mid)
            blocks.append(Block(
                id=bid, role=role, gear_ref=model.name if model else humanize(mid), label=humanize(mid)[:16],
                enabled=bool(raw.get("@enabled", True)), settings=_neutral(native, model),
                helix=DeviceModel(model=mid, params=native),
            ))
            index[(dsp, k)] = bid
            cab_key = raw.get("@cab")
            if cab_key and isinstance(d.get(cab_key), dict):
                craw = d[cab_key]
                cmid = craw.get("@model", "")
                counts["cab"] = counts.get("cab", 0) + 1
                cid = f"cab{counts['cab']}"
                cnative = {pk: _scalar(pv) for pk, pv in craw.items() if not pk.startswith("@")}
                blocks.append(Block(id=cid, role=Role.cab, gear_ref=humanize(cmid), label=humanize(cmid)[:16],
                                    enabled=bool(raw.get("@enabled", True)),
                                    helix=DeviceModel(model=cmid, params=cnative)))

    snapshots: list[Snapshot] = []
    for i in range(8):
        s = tone.get(f"snapshot{i}")
        if not isinstance(s, dict) or s.get("@valid") is False:
            continue
        snap = Snapshot(name=(s.get("@name") or f"Snapshot {i + 1}")[:16])
        for dsp, blks in (s.get("blocks") or {}).items():
            for k, on in (blks or {}).items():
                bid = index.get((dsp, k))
                if bid is not None:
                    snap.enabled[bid] = bool(on)
        for dsp, blks in (s.get("controllers") or {}).items():
            for k, params in (blks or {}).items():
                bid = index.get((dsp, k))
                if bid is None:
                    continue
                b = next(x for x in blocks if x.id == bid)
                native = {pk: _scalar(pv) for pk, pv in (params or {}).items()}
                neu = _neutral(native, cat.get(b.helix.model or ""))
                if neu:
                    snap.overrides[bid] = neu
        snapshots.append(snap)

    fsw: list[Footswitch] = []
    for dsp, blks in (tone.get("footswitch") or {}).items():
        for k, fs in (blks or {}).items():
            bid = index.get((dsp, k))
            if bid is None or not isinstance(fs, dict):
                continue
            idx = fs.get("@fs_index")
            if isinstance(idx, int) and 0 <= idx < 12:
                fsw.append(Footswitch(switch=f"FS{idx + 1}", block_id=bid, label=str(fs.get("@fs_label") or "")[:16]))

    return ToneRecipe(name=(meta.get("name") or name_hint or "Importato")[:32],
                      description=f"Importato da preset Helix ({meta.get('application', 'hlx')})",
                      chain=blocks, snapshots=snapshots[:8], footswitches=fsw,
                      notes=[f"Fonte: {name_hint}"] if name_hint else [])


# ------------------------------------------------------------------ .hsp (via helixgen view)

def from_helixgen_projection(proj: dict[str, Any], name_hint: str = "") -> ToneRecipe:
    cat = HelixCatalog.load()
    by_name = {m.name: m for m in cat.models.values()}
    blocks: list[Block] = []
    lookup: dict[str, str] = {}
    counts: dict[str, int] = {}
    for path in proj.get("paths", []):
        for entry in path.get("blocks", []):
            if not isinstance(entry, dict) or "block" not in entry:
                continue
            ref = entry["block"]
            model = cat.get(ref) or by_name.get(ref)
            mid = model.model_id if model else ref
            role = model.role if model else _role_from_model(ref, cat)
            counts[role.value] = counts.get(role.value, 0) + 1
            bid = f"{role.value}{counts[role.value]}"
            native = {k: _scalar(v) for k, v in (entry.get("params") or {}).items()}
            blocks.append(Block(id=bid, role=role, gear_ref=model.name if model else ref,
                                label=(model.name if model else ref)[:16],
                                enabled=entry.get("enabled", True) is not False,
                                settings=_neutral(native, model), ir=entry.get("ir"),
                                helix=DeviceModel(model=mid, params=native)))
            lookup.setdefault(ref, bid)
            lookup.setdefault(mid, bid)

    def ref_id(r: Any) -> str | None:
        key = r.get("block") if isinstance(r, dict) else r
        return lookup.get(key)

    snaps: list[Snapshot] = []
    for s in proj.get("snapshots", [])[:8]:
        snap = Snapshot(name=s.get("name", "Snapshot")[:16])
        for r in s.get("disable", []):
            bid = ref_id(r)
            if bid:
                snap.enabled[bid] = False
        params = s.get("params", {})
        items = params.items() if isinstance(params, dict) else [(p.get("block"), p.get("params", {})) for p in params]
        for key, vals in items:
            bid = lookup.get(key)
            if not bid:
                continue
            b = next(x for x in blocks if x.id == bid)
            neu = _neutral({k: _scalar(v) for k, v in vals.items()}, cat.get(b.helix.model or ""))
            if neu:
                snap.overrides[bid] = neu
        snaps.append(snap)

    fsw = []
    for f in proj.get("footswitches", []):
        bid = ref_id(f)
        if bid:
            fsw.append(Footswitch(switch=f.get("switch", "FS1"), block_id=bid, label=f.get("label", "")))
    return ToneRecipe(name=(proj.get("name") or name_hint or "Importato")[:32],
                      description="Importato da preset Helix Stadium", chain=blocks,
                      snapshots=snaps, footswitches=fsw)


def import_file(path: str | Path) -> ToneRecipe:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".hlx":
        return from_hlx(json.loads(path.read_text(encoding="utf-8")), path.stem)
    if ext == ".hsp":
        from .backends.helix_stadium import read_hsp

        return from_helixgen_projection(read_hsp(path), path.stem)
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if "chain" in data:
            return ToneRecipe.model_validate(data)
        if "paths" in data:
            return from_helixgen_projection(data, path.stem)
        if "data" in data and "tone" in data.get("data", {}):
            return from_hlx(data, path.stem)
    raise ValueError(f"Formato non supportato: {path.name} (usa .hlx, .hsp o .json)")
