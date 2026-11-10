"""Backend Helix Stadium: ricetta -> file .hsp pronto da importare.

Usa helixgen (progetto open source non ufficiale) tramite la sua CLI, che e'
l'interfaccia stabile della libreria.

Prerequisito una tantum: helixgen deve conoscere il tuo Stadium. Esporta dalla
app Helix Stadium almeno un preset .hsp (meglio un'intera setlist) e importalo
dalla pagina Impostazioni (equivale a `helixgen ingest <cartella>`): il primo
file diventa il "telaio" dei preset generati.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..catalogs.helix import HelixCatalog, HelixModel
from ..knobs import map_settings
from ..recipe import Block, Role, ToneRecipe

MAX_BLOCKS_PER_PATH = 12
HELIX_SWITCHES = [f"FS{i}" for i in range(1, 11)]
_QC_LETTERS = "ABCDEFGH"


class HelixError(RuntimeError):
    pass


@dataclass
class HelixBuild:
    spec: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)  # block_id -> model_id


def _helixgen_cmd() -> list[str]:
    exe = shutil.which("helixgen")
    return [exe] if exe else [sys.executable, "-m", "helixgen.cli"]


def _choose_model(b: Block, cat: HelixCatalog, warnings: list[str]) -> HelixModel:
    if b.helix.model:
        m = cat.get(b.helix.model)
        if m:
            return m
        warnings.append(f"{b.id}: modello Helix '{b.helix.model}' non esiste, cerco un equivalente")
    candidates = cat.search(f"{b.gear_ref} {b.label} {b.helix.model or ''}", b.role)
    if not candidates:
        candidates = cat.by_role(b.role)
    if not candidates:
        raise HelixError(f"{b.id}: nessun modello Helix disponibile per il ruolo {b.role.value}")
    ready = [c for c in candidates if c.in_library] or candidates
    m = ready[0]
    warnings.append(f"{b.id}: scelto '{m.name}' ({m.model_id}) per '{b.gear_ref or b.role.value}'")
    return m


def _native_params(b: Block, model: HelixModel, overrides: dict[str, float] | None,
                   warnings: list[str], where: str) -> dict[str, float]:
    params = model.visible_params()
    settings = overrides if overrides is not None else b.settings
    out, missing = map_settings(settings, params)
    if missing:
        warnings.append(f"{where}: manopole senza equivalente su '{model.name}': {', '.join(missing)}")
    if overrides is None:  # i valori nativi espliciti valgono solo per lo stato base
        for k, v in b.helix.params.items():
            if k in model.params:
                out[k] = v
            else:
                warnings.append(f"{where}: parametro nativo '{k}' sconosciuto su '{model.name}', ignorato")
    return out


def _switch_name(s: str) -> str:
    s = s.strip().upper()
    if s in _QC_LETTERS and len(s) == 1:
        return f"FS{_QC_LETTERS.index(s) + 1}"
    if re.fullmatch(r"\d+", s):
        return f"FS{s}"
    return s


def build_spec(recipe: ToneRecipe, cat: HelixCatalog | None = None) -> HelixBuild:
    """Traduce la ricetta nel formato recipe di helixgen."""
    cat = cat or HelixCatalog.load()
    warnings: list[str] = []
    chain = [b for b in recipe.chain if b.role != Role.looper]
    if len(chain) > MAX_BLOCKS_PER_PATH:
        raise HelixError(
            f"La catena ha {len(chain)} blocchi: su un percorso Stadium ne stanno "
            f"{MAX_BLOCKS_PER_PATH}. Usa il merge per ridurla."
        )

    models = {b.id: _choose_model(b, cat, warnings) for b in chain}
    ids = [m.model_id for m in models.values()]
    has_dupes = len(ids) != len(set(ids))

    # Stato base: acceso se acceso in base o in almeno uno snapshot; gli
    # snapshot di helixgen sanno solo SPEGNERE rispetto alla base.
    base_on = {b.id: b.enabled or any(s.enabled.get(b.id) for s in recipe.snapshots) for b in chain}

    def ref(bid: str) -> dict[str, Any]:
        r: dict[str, Any] = {"block": models[bid].model_id}
        if has_dupes:
            r["pos"] = [b.id for b in chain].index(bid) + 1
        return r

    blocks = []
    for i, b in enumerate(chain):
        m = models[b.id]
        entry: dict[str, Any] = {"block": m.model_id}
        params = _native_params(b, m, None, warnings, b.id)
        if params:
            entry["params"] = params
        if not base_on[b.id]:
            entry["enabled"] = False
        if has_dupes:
            entry["pos"] = i + 1
        if b.ir and b.role in (Role.cab, Role.ir):
            entry["ir"] = Path(b.ir).name
        blocks.append(entry)

    spec: dict[str, Any] = {"name": recipe.name[:32], "paths": [{"blocks": blocks}]}
    if recipe.song.artist:
        spec["author"] = recipe.song.artist[:32]

    snaps = []
    for s in recipe.snapshots:
        sd: dict[str, Any] = {"name": s.name[:16]}
        disable = [ref(b.id) for b in chain if base_on[b.id] and not s.enabled.get(b.id, b.enabled)]
        if disable:
            sd["disable"] = disable
        plist = []
        for bid, knobs in s.overrides.items():
            if bid not in models:
                continue
            b = recipe.block(bid)
            native = _native_params(b, models[bid], knobs, warnings, f"{s.name}/{bid}")
            if native:
                plist.append({**ref(bid), "params": native})
        if plist:
            sd["params"] = plist
        if s.level_db is not None:
            sd["output"] = [{"path": 0, "level": s.level_db}]
        snaps.append(sd)
    if snaps:
        spec["snapshots"] = snaps

    fs = []
    for f in recipe.footswitches:
        if f.block_id not in models:
            continue
        name = _switch_name(f.switch)
        if name not in HELIX_SWITCHES:
            warnings.append(f"footswitch '{f.switch}' non valido su Stadium, saltato")
            continue
        item = {"switch": name, **ref(f.block_id)}
        label = f.label or recipe.block(f.block_id).label
        if label:
            item["label"] = label[:16]
        fs.append(item)
    if fs:
        spec["footswitches"] = fs

    return HelixBuild(spec, warnings, {bid: m.model_id for bid, m in models.items()})


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(_helixgen_cmd() + args, capture_output=True, text=True, encoding="utf-8")


def library_status() -> dict[str, Any]:
    cat = HelixCatalog.load()
    n = sum(1 for m in cat.models.values() if m.in_library)
    return {"ready": n > 0, "blocks_in_library": n}


def ingest(path: str | Path) -> str:
    """Importa nella libreria helixgen un .hsp o una cartella di export Stadium."""
    r = _run(["ingest", str(path)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip() or r.stdout.strip())
    HelixCatalog.reload()
    return r.stdout.strip()


def register_irs(wavs: list[str | Path]) -> str:
    r = _run(["register-irs", *map(str, wavs)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def generate_hsp(recipe: ToneRecipe, out_dir: str | Path) -> tuple[Path, HelixBuild]:
    """Genera il file .hsp. Se un IR non e' registrato, riprova senza e lo segnala."""
    if not library_status()["ready"]:
        raise HelixError(
            "La libreria helixgen e' vuota: importa prima almeno un tuo export .hsp "
            "dalle Impostazioni (serve come telaio dei preset)."
        )
    build = build_spec(recipe)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", recipe.name).strip().replace(" ", "_") or "preset"
    out = out_dir / f"{safe}.hsp"
    n = 2
    while out.exists():
        out = out_dir / f"{safe}_{n}.hsp"
        n += 1

    def attempt(spec: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "recipe.json"
            rp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            return _run(["generate", str(rp), "-o", str(out)])

    r = attempt(build.spec)
    if r.returncode != 0 and re.search(r"\bIR\b|irhash|impulse", r.stderr, re.I):
        for blk in build.spec["paths"][0]["blocks"]:
            if blk.pop("ir", None):
                build.warnings.append(
                    f"IR non registrato per '{blk['block']}': il cab resta senza IR, "
                    "registralo dalle Impostazioni e rigenera"
                )
        r = attempt(build.spec)
    if r.returncode != 0:
        raise HelixError((r.stderr or r.stdout).strip())
    return out, build


def read_hsp(path: str | Path) -> dict[str, Any]:
    """Legge un .hsp come recipe helixgen (per l'importazione)."""
    r = _run(["view", str(path)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip())
    return json.loads(r.stdout)
