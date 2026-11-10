"""Merge intelligente di piu' preset in un'unica patch a snapshot.

Esempio: dal preset A prendo il pulito, da B il crunch, da C il lead. Ogni
"scelta" (pick) diventa uno snapshot della patch finale.

Strategia, in ordine:
1. Sintesi automatica (non chiede nulla): blocchi identici vengono condivisi,
   e ampli della STESSA famiglia (es. due Marshall) diventano un unico ampli
   con gain/EQ diversi per snapshot. Lo stesso per i loro cab.
2. Se la stima DSP supera il budget, proponiamo compromessi in ordine di
   impatto crescente (riverberi/delay unificati, modulazioni unificate, ampli
   di famiglie affini unificati, drive sostituiti da piu' gain sull'ampli).
   Ogni compromesso va confermato: `merge()` li applica solo se compaiono in
   `accept` (oppure con accept="all"), altrimenti li restituisce come proposte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dsp import ROLE_ORDER, BUDGET, estimate_blocks
from .recipe import Block, Footswitch, Role, Snapshot, SongInfo, ToneRecipe

# famiglia di ampli -> parole chiave (gear_ref, nome modello, id)
AMP_FAMILIES: dict[str, list[str]] = {
    "marshall": ["marshall", "brit", "plexi", "jcm", "2203", "800", "jtm", "superlead", "super lead", "1959", "jvm", "silver jubilee", "jubilee"],
    "fender": ["fender", "twin", "deluxe", "tweed", "bassman", "princeton", "us double", "us deluxe", "us princess", "us lux", "us super", "us small", "blackface", "super reverb", "vibrolux", "champ"],
    "vox": ["vox", "ac30", "ac15", "essex", "class a", "a30"],
    "mesa": ["mesa", "boogie", "rectifier", "recto", "cali", "mark iic", "mark iv", "mark v", "triple crown", "lonestar"],
    "5150": ["5150", "evh", "panama", "peavey", "6505", "invective"],
    "soldano": ["soldano", "slo", "solo 100", "solo-100"],
    "bogner": ["bogner", "ecstasy", "uberschall", "shiva", "placater"],
    "german": ["diezel", "engl", "german", "vh4", "herbert", "fireball", "powerball"],
    "orange": ["orange", "mandarin", "rockerverb", "or120", "or 120"],
    "dumble": ["dumble", "odr", "ods", "two-rock", "two rock", "overdrive special"],
    "hiwatt": ["hiwatt", "who watt", "dr103", "dr 103"],
    "friedman": ["friedman", "be100", "be-100", "dirty shirley", "fm "],
    "matchless": ["matchless", "matchstick", "dc30", "dc-30"],
    "revv": ["revv"],
}

ROLE_IT = {"gate": "Gate", "comp": "Compressore", "wah": "Wah", "pitch": "Pitch", "drive": "Drive",
           "amp": "Ampli", "preamp": "Preampli", "eq": "EQ", "mod": "Modulazione", "delay": "Delay",
           "reverb": "Riverbero", "volume": "Volume", "filter": "Filtro", "synth": "Synth", "ir": "IR"}

# famiglie affini: usate solo come compromesso se il DSP non basta
AMP_GROUPS: dict[str, set[str]] = {
    "clean": {"fender", "vox", "matchless", "dumble"},
    "crunch": {"marshall", "orange", "hiwatt", "friedman"},
    "high_gain": {"mesa", "5150", "soldano", "bogner", "german", "revv"},
}
_GROUP_ORDER = ["clean", "crunch", "high_gain"]


def amp_family(b: Block) -> str:
    from .catalogs.helix import humanize

    model = humanize(b.helix.model) if b.helix.model else ""
    text = " ".join([b.gear_ref, b.label, model, str(b.qc.model or "")])
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).lower()
    for fam, keys in AMP_FAMILIES.items():
        if any(k in text for k in keys):
            return fam
    return "other:" + (b.helix.model or str(b.qc.model) or b.gear_ref or b.id).lower()


def amp_group(fam: str) -> str:
    for g, fams in AMP_GROUPS.items():
        if fam in fams:
            return g
    return "other"


@dataclass
class Pick:
    """Quale suono prendere da quale preset."""

    recipe: ToneRecipe
    snapshot: str | None = None  # nome snapshot sorgente; None = stato base
    name: str = ""  # nome dello snapshot nel preset finale


@dataclass
class Contribution:
    pick: int
    block: Block
    settings: dict[str, float]


@dataclass
class Item:
    """Un blocco della patch finale, alimentato da uno o piu' pick."""

    role: Role
    key: str
    contribs: list[Contribution] = field(default_factory=list)

    @property
    def picks(self) -> set[int]:
        return {c.pick for c in self.contribs}


@dataclass
class Compromise:
    id: str
    text: str
    kind: str


@dataclass
class MergeResult:
    recipe: ToneRecipe
    dsp: dict
    syntheses: list[str]
    applied: list[Compromise]
    proposed: list[Compromise]
    over_budget: bool

    def to_dict(self) -> dict:
        return {
            "recipe": self.recipe.model_dump(),
            "dsp": self.dsp,
            "syntheses": self.syntheses,
            "applied": [c.__dict__ for c in self.applied],
            "proposed": [c.__dict__ for c in self.proposed],
            "over_budget": self.over_budget,
        }


def _state_for(pick: Pick) -> list[tuple[Block, dict[str, float]]]:
    """Blocchi accesi e loro impostazioni nello snapshot scelto."""
    r = pick.recipe
    snap = next((s for s in r.snapshots if s.name == pick.snapshot), None) if pick.snapshot else None
    if pick.snapshot and snap is None:
        raise ValueError(f"snapshot {pick.snapshot!r} non trovato in {r.name!r}")
    out = []
    for b in r.chain:
        on = snap.enabled.get(b.id, b.enabled) if snap else b.enabled
        if not on:
            continue
        settings = dict(b.settings)
        if snap:
            settings.update(snap.overrides.get(b.id, {}))
        out.append((b, settings))
    return out


def _model_key(b: Block) -> str:
    return (b.helix.model or str(b.qc.model or "") or b.gear_ref or b.label or b.id).lower()


def _cost(items: list[Item]) -> tuple[float, float]:
    blocks = [it.contribs[0].block for it in items]
    est = estimate_blocks(blocks)
    return est.helix, est.qc


def _fits(items: list[Item]) -> bool:
    h, q = _cost(items)
    return h <= BUDGET["helix"] and q <= BUDGET["qc"]


def _join(items: list[Item], role_filter, key: str) -> Item | None:
    """Unisce tutti gli item che soddisfano il filtro in uno solo."""
    group = [it for it in items if role_filter(it)]
    if len(group) < 2:
        return None
    merged = Item(group[0].role, key)
    for it in group:
        merged.contribs.extend(it.contribs)
        items.remove(it)
    items.append(merged)
    return merged


def merge(picks: list[Pick], name: str = "Merged", accept: list[str] | str | None = None) -> MergeResult:
    if not picks:
        raise ValueError("nessun suono selezionato")
    if len(picks) > 8:
        raise ValueError("massimo 8 suoni (8 snapshot)")
    accept_all = accept == "all"
    accept_set = set(accept or []) if not accept_all else set()

    syntheses: list[str] = []
    items: list[Item] = []
    # 1) raccolta + sintesi automatica
    for pi, pick in enumerate(picks):
        for b, settings in _state_for(pick):
            if b.role in (Role.amp, Role.preamp, Role.cab):
                key = f"{b.role.value}:{amp_family(b)}"
            else:
                key = f"{b.role.value}:{_model_key(b)}"
            # la stessa sorgente puo' avere due blocchi uguali: non vanno fusi tra loro
            target = next((it for it in items if it.key == key and pi not in it.picks), None)
            if target is None:
                target = Item(b.role, key)
                items.append(target)
            target.contribs.append(Contribution(pi, b, settings))

    for it in items:
        if len(it.picks) > 1:
            models = {_model_key(c.block) for c in it.contribs}
            who = ", ".join(picks[p].name or f"suono {p + 1}" for p in sorted(it.picks))
            if it.role in (Role.amp, Role.preamp) and len(models) > 1:
                syntheses.append(
                    f"Ampli della stessa famiglia ({it.key.split(':', 1)[1]}) unificati in uno solo, "
                    f"con impostazioni diverse per snapshot: {who}"
                )
            elif it.role != Role.cab:
                syntheses.append(f"{ROLE_IT.get(it.role.value, it.role.value)} condiviso tra {who}")

    # 2) compromessi, solo se il budget DSP non basta
    reductions = _reductions()
    sim = _copy_items(items)
    proposed_all: list[Compromise] = []
    for cid, text, kind, fn in reductions:
        if _fits(sim):
            break
        trial = _copy_items(sim)
        if fn(trial):
            sim = trial
            proposed_all.append(Compromise(cid, text, kind))

    applied: list[Compromise] = []
    proposed_ids = {c.id for c in proposed_all}
    for cid, text, kind, fn in reductions:
        if cid in proposed_ids and (accept_all or cid in accept_set):
            if fn(items):
                applied.append(Compromise(cid, text, kind))
    applied_ids = {c.id for c in applied}
    proposed = [c for c in proposed_all if c.id not in applied_ids]

    # 3) costruzione ricetta finale
    items.sort(key=lambda it: ROLE_ORDER.index(it.role) if it.role in ROLE_ORDER else 99)
    chain: list[Block] = []
    snapshots = [Snapshot(name=(p.name or p.snapshot or f"Suono {i + 1}")[:16]) for i, p in enumerate(picks)]
    used_ids: set[str] = set()
    footswitches: list[Footswitch] = []

    for it in items:
        first = it.contribs[0]
        base = first.block.model_copy(deep=True)
        bid = re.sub(r"\W+", "_", f"{it.role.value}") or "blk"
        n = 1
        while f"{bid}{n}" in used_ids:
            n += 1
        base.id = f"{bid}{n}"
        used_ids.add(base.id)
        base.settings = dict(first.settings)
        base.enabled = 0 in it.picks
        base.source = ", ".join(sorted({c.block.source or picks[c.pick].recipe.name for c in it.contribs}))
        # i valori nativi del primo contributo valgono solo se tutti usano lo stesso modello
        if len({_model_key(c.block) for c in it.contribs}) > 1:
            base.helix.params, base.qc.params = {}, {}
        chain.append(base)
        for pi in range(len(picks)):
            on = pi in it.picks
            if on != base.enabled:
                snapshots[pi].enabled[base.id] = on
            contrib = next((c for c in it.contribs if c.pick == pi), None)
            if contrib:
                diff = {k: v for k, v in contrib.settings.items() if base.settings.get(k) != v}
                if diff:
                    snapshots[pi].overrides[base.id] = diff

    for pi, p in enumerate(picks):
        src = next((s for s in p.recipe.snapshots if s.name == p.snapshot), None)
        if src and src.level_db is not None:
            snapshots[pi].level_db = src.level_db

    # blocchi che cambiano stato tra snapshot: utili anche su footswitch
    toggles = [b for b in chain if any(b.id in s.enabled for s in snapshots) and b.role in (Role.drive, Role.delay, Role.mod)]
    for i, b in enumerate(toggles[:4]):
        footswitches.append(Footswitch(switch=f"FS{i + 7}", block_id=b.id, label=b.label or b.role.value))

    first = picks[0].recipe
    recipe = ToneRecipe(
        name=name,
        song=first.song if len({p.recipe.song.title for p in picks}) == 1 else SongInfo(),
        guitar=first.guitar,
        description="Merge di: " + "; ".join(f"{p.name or p.snapshot or 'base'} da '{p.recipe.name}'" for p in picks),
        chain=chain,
        snapshots=snapshots,
        footswitches=footswitches,
        notes=[s for s in syntheses] + [f"Compromesso: {c.text}" for c in applied],
    )
    est = estimate_blocks(chain)
    return MergeResult(recipe, est.to_dict(), syntheses, applied, proposed, not est.fits)


def _copy_items(items: list[Item]) -> list[Item]:
    return [Item(i.role, i.key, [Contribution(c.pick, c.block, dict(c.settings)) for c in i.contribs])
            for i in items]


def _share(role: Role):
    return lambda its: _join(its, lambda it: it.role == role, f"{role.value}:shared") is not None


def _amp_group(g: str):
    def fn(its: list[Item]) -> bool:
        a = _join(its, lambda it: it.role in (Role.amp, Role.preamp)
                  and amp_group(it.key.split(":", 1)[1]) == g, f"amp:{g}")
        _join(its, lambda it: it.role == Role.cab and amp_group(it.key.split(":", 1)[1]) == g, f"cab:{g}")
        return a is not None
    return fn


def _drives_to_gain(its: list[Item]) -> bool:
    drives = [it for it in its if it.role == Role.drive]
    amps = [it for it in its if it.role in (Role.amp, Role.preamp)]
    if not drives or not amps:
        return False
    for d in drives:
        for c in d.contribs:
            for a in amps:
                for ac in a.contribs:
                    if ac.pick == c.pick:
                        knob = "gain" if "gain" in ac.settings or "drive" not in ac.settings else "drive"
                        ac.settings[knob] = min(10.0, ac.settings.get(knob, 5.0) + 1.5)
        its.remove(d)
    return True


def _reductions():
    """Compromessi possibili, dal meno al piu' invasivo."""
    out = [
        ("share_reverb", "Uso un unico riverbero per tutti gli snapshot (cambiano solo le impostazioni)", "reverb", _share(Role.reverb)),
        ("share_delay", "Uso un unico delay per tutti gli snapshot (cambiano solo le impostazioni)", "delay", _share(Role.delay)),
        ("share_mod", "Uso un'unica modulazione per tutti gli snapshot", "mod", _share(Role.mod)),
    ]
    for g in _GROUP_ORDER:
        out.append((f"amp_group_{g}", f"Unifico gli ampli di famiglie affini ({g.replace('_', ' ')}) in un solo ampli",
                    "amp", _amp_group(g)))
    out.append(("drives_to_gain", "Tolgo i pedali drive e compenso alzando il gain dell'ampli negli snapshot che li usavano",
                "drive", _drives_to_gain))
    return out
