"""Backend Quad Cortex: ricetta -> scritture dirette via USB.

Usa pyquadcortex (progetto open source non ufficiale). Il lavoro e' diviso in
due fasi, cosi' l'interfaccia puo' mostrarti tutto PRIMA di toccare il QC:

1. `build_plan()` produce l'elenco delle operazioni (piazza blocco, imposta
   parametro, valore per scena, footswitch, salvataggio nello slot scelto).
2. `execute_plan()` le esegue sul dispositivo.

Limiti noti del protocollo (documentati da pyquadcortex):
- Cortex Control deve essere CHIUSO: occupa la USB in esclusiva.
- On/off di un blocco PER SCENA si puo' scrivere solo se la "modalita' scena"
  del blocco e' gia' attiva, e quel flag lo imposta solo il touchscreen. Per
  ritardi, riverberi e modulazioni usiamo quindi il Mix a 0 nelle scene in cui
  sono "spenti" (e' quello che fanno anche i preset di fabbrica); per gli altri
  blocchi il piano ti indica il passaggio manuale da fare sul QC.
- Un blocco che non entra nel budget DSP viene rifiutato: lo segnaliamo.
- Gli IR non si caricano via USB: si selezionano tra quelli gia' presenti nel
  QC (caricali prima con Cortex Control).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..catalogs.quadcortex import QCCatalog, QCModel, save_device_catalog
from ..knobs import map_settings
from ..recipe import Block, Role, ToneRecipe

COLUMNS = 8
SCENES = "ABCDEFGH"
_MIX_BYPASS_ROLES = {Role.delay, Role.reverb, Role.mod, Role.pitch}


class QCError(RuntimeError):
    pass


@dataclass
class Step:
    action: str  # clear_grid | set_block | set_param | set_ir | scene_label | stomp | save | manual
    args: dict[str, Any]
    text: str  # descrizione leggibile


@dataclass
class QCPlan:
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    placement: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # block_id -> (row, col, model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [{"action": s.action, "args": s.args, "text": s.text} for s in self.steps],
            "warnings": self.warnings,
            "manual": self.manual,
            "placement": {k: list(v) for k, v in self.placement.items()},
        }


def _choose(b: Block, cat: QCCatalog, warnings: list[str]) -> QCModel | None:
    if b.qc.model:
        m = cat.get(b.qc.model)
        if m:
            return m
        warnings.append(f"{b.id}: modello QC '{b.qc.model}' non trovato, cerco un equivalente")
    cands = cat.search(f"{b.gear_ref} {b.label} {b.qc.model or ''}", b.role) or cat.by_role(b.role)
    if not cands:
        warnings.append(f"{b.id}: nessun blocco QC per il ruolo {b.role.value}, saltato")
        return None
    warnings.append(f"{b.id}: scelto '{cands[0].name}' per '{b.gear_ref or b.role.value}'")
    return cands[0]


def _params(b: Block, m: QCModel, knobs: dict[str, float], base: bool, warnings: list[str], where: str) -> dict[str, float]:
    if not m.params:
        return {}
    out, missing = map_settings(knobs, m.params)
    if missing:
        warnings.append(f"{where}: manopole senza equivalente su '{m.name}': {', '.join(missing)}")
    if base:
        for k, v in b.qc.params.items():
            if k in m.params:
                out[k] = v
    return out


def _mix_param(m: QCModel) -> str | None:
    for name in m.params:
        if name.strip().lower() in ("mix", "dry/wet", "blend"):
            return name
    return None


def build_plan(recipe: ToneRecipe, slot: str, cat: QCCatalog | None = None,
               clear_grid: bool = True) -> QCPlan:
    cat = cat or QCCatalog.load()
    plan = QCPlan()
    if not cat.from_device:
        plan.warnings.append(
            "Catalogo QC non ancora letto dal dispositivo: i parametri verranno "
            "impostati solo dopo il primo collegamento (Impostazioni > Leggi catalogo QC)."
        )
    chain = [b for b in recipe.chain if b.role != Role.looper]
    if len(chain) > COLUMNS:
        raise QCError(f"La catena ha {len(chain)} blocchi: su una riga del QC ne stanno {COLUMNS}. Usa il merge per ridurla.")

    if clear_grid:
        plan.steps.append(Step("clear_grid", {}, "Svuota la griglia corrente"))

    models: dict[str, QCModel] = {}
    for col, b in enumerate(chain):
        m = _choose(b, cat, plan.warnings)
        if not m:
            continue
        models[b.id] = m
        plan.placement[b.id] = (0, col, m.id)
        plan.steps.append(Step("set_block", {"row": 0, "column": col, "model": m.id},
                               f"Riga 1, colonna {col + 1}: {m.name} ({b.label or b.role.value})"))
        for pname, val in _params(b, m, b.settings, True, plan.warnings, b.id).items():
            plan.steps.append(Step("set_param", {"row": 0, "column": col, "model": m.id, "param": pname, "real": val},
                                   f"{m.name}: {pname} = {val}"))
        if b.ir and b.role in (Role.ir, Role.cab):
            plan.steps.append(Step("set_ir", {"row": 0, "column": col, "ir_name": b.ir},
                                   f"{m.name}: IR '{b.ir}' (se presente nel QC)"))

    bypass_blocks = recipe.snapshot_controlled_bypass()
    for i, s in enumerate(recipe.snapshots):
        plan.steps.append(Step("scene_label", {"scene": i, "label": s.name[:20]}, f"Scena {SCENES[i]}: {s.name}"))
        for bid, knobs in s.overrides.items():
            if bid not in models:
                continue
            row, col, mid = plan.placement[bid]
            for pname, val in _params(recipe.block(bid), models[bid], knobs, False, plan.warnings, f"{s.name}/{bid}").items():
                plan.steps.append(Step("set_param", {"row": row, "column": col, "model": mid, "param": pname,
                                                     "real": val, "scene": i},
                                       f"Scena {SCENES[i]} - {models[bid].name}: {pname} = {val}"))

    for bid in sorted(bypass_blocks):
        if bid not in models:
            continue
        b, m = recipe.block(bid), models[bid]
        row, col, mid = plan.placement[bid]
        mix = _mix_param(m) if b.role in _MIX_BYPASS_ROLES else None
        if mix:
            base_mix = _params(b, m, b.settings, True, [], "").get(mix, m.params[mix].default or m.params[mix].max * 0.3)
            for i, s in enumerate(recipe.snapshots):
                on = s.enabled.get(bid, b.enabled)
                val = base_mix if on else m.params[mix].min
                plan.steps.append(Step("set_param", {"row": row, "column": col, "model": mid, "param": mix,
                                                     "real": val, "scene": i},
                                       f"Scena {SCENES[i]} - {m.name}: {'acceso' if on else 'spento'} (Mix {val})"))
        else:
            states = ", ".join(f"{SCENES[i]}={'ON' if s.enabled.get(bid, b.enabled) else 'off'}"
                               for i, s in enumerate(recipe.snapshots))
            plan.manual.append(
                f"Sul QC tieni premuto '{m.name}' (riga 1, col. {col + 1}) e attiva la modalita' scena; "
                f"poi premi 'Applica on/off per scena'. Stati: {states}"
            )

    for f in recipe.footswitches:
        if f.block_id not in plan.placement:
            continue
        letter = f.switch.strip().upper()
        if letter.startswith("FS") and letter[2:].isdigit():
            n = int(letter[2:])
            letter = SCENES[n - 1] if 1 <= n <= 8 else ""
        if letter not in SCENES or not letter:
            plan.warnings.append(f"footswitch '{f.switch}' non valido sul QC (A-H), saltato")
            continue
        row, col, _ = plan.placement[f.block_id]
        label = (f.label or recipe.block(f.block_id).label)[:12]
        plan.steps.append(Step("stomp", {"row": row, "column": col, "footswitch": SCENES.index(letter), "label": label},
                               f"Footswitch {letter} -> {label or f.block_id}"))

    plan.steps.append(Step("save", {"slot": slot, "name": recipe.name[:32]},
                           f"Salva come '{recipe.name}' nello slot {slot} (SOVRASCRIVE lo slot)"))
    return plan


# ---------------------------------------------------------------- esecuzione

def connect():
    try:
        import pyquadcortex
    except ImportError as e:  # pragma: no cover
        raise QCError("pyquadcortex non installato: pip install pyquadcortex") from e
    try:
        return pyquadcortex.connect()
    except Exception as e:
        raise QCError(
            f"Quad Cortex non raggiungibile via USB ({e}). Controlla il cavo e che Cortex Control sia chiuso."
        ) from e


def read_catalog() -> int:
    """Legge e salva il catalogo completo dal QC collegato."""
    with connect() as qc:
        return save_device_catalog(qc.catalog)


def execute_plan(plan: QCPlan, progress: Callable[[int, int, str], None] | None = None,
                 client=None) -> list[str]:
    """Esegue il piano. `client` permette di passare un QC finto nei test."""
    from contextlib import nullcontext

    ctx = nullcontext(client) if client is not None else connect()
    problems: list[str] = []
    with ctx as qc:
        Scene = _enum("Scene")
        Setlist = _enum("Setlist")
        irs = None
        total = len(plan.steps)
        for n, st in enumerate(plan.steps, 1):
            if progress:
                progress(n, total, st.text)
            a = st.args
            try:
                if st.action == "clear_grid":
                    for row in range(4):
                        for col in range(COLUMNS):
                            qc.remove_block(row, col)
                elif st.action == "set_block":
                    qc.set_block(a["row"], a["column"], a["model"])
                elif st.action == "set_param":
                    kw = dict(param=a["param"], model=a["model"], real=a["real"])
                    if "scene" in a:
                        kw["scene"] = Scene(a["scene"]) if Scene else a["scene"]
                    qc.set_param(a["row"], a["column"], **kw)
                elif st.action == "set_ir":
                    irs = irs if irs is not None else qc.list_irs()
                    hit = next((i for i in irs if a["ir_name"].lower() in i.name.lower()), None)
                    if hit:
                        qc.set_ir(a["row"], a["column"], hit)
                    else:
                        problems.append(f"IR '{a['ir_name']}' non presente nel QC: caricalo con Cortex Control")
                elif st.action == "scene_label":
                    qc.set_scene_label(a["scene"], a["label"])
                elif st.action == "stomp":
                    qc.set_stomp_assignment(a["row"], a["column"], a["footswitch"])
                    if a.get("label"):
                        qc.set_stomp_label(a["footswitch"], a["label"])
                elif st.action == "save":
                    target = Setlist.USER if Setlist else "/media/p4/Presets/My Presets"
                    qc.save_current_preset(target, a["slot"], a["name"],
                                           default_scene=Scene(0) if Scene else None)
            except Exception as e:  # un errore su un passo non blocca il resto
                if type(e).__name__ == "BlockRefused":
                    problems.append(f"{st.text}: rifiutato dal QC (budget DSP esaurito)")
                else:
                    problems.append(f"{st.text}: {e}")
    return problems


def apply_scene_bypass(recipe: ToneRecipe, plan: QCPlan, client=None) -> list[str]:
    """Seconda fase: scrive on/off per scena DOPO che hai attivato la modalita' scena sul QC."""
    from contextlib import nullcontext

    ctx = nullcontext(client) if client is not None else connect()
    problems: list[str] = []
    Scene = _enum("Scene")
    with ctx as qc:
        for bid in recipe.snapshot_controlled_bypass():
            if bid not in plan.placement:
                continue
            row, col, _ = plan.placement[bid]
            b = recipe.block(bid)
            for i, s in enumerate(recipe.snapshots):
                on = s.enabled.get(bid, b.enabled)
                try:
                    qc.set_bypass(row, col, not on, scene=Scene(i) if Scene else i)
                except Exception as e:
                    problems.append(f"{bid} scena {SCENES[i]}: {e}")
        save = next((s for s in plan.steps if s.action == "save"), None)
        if save:
            Setlist = _enum("Setlist")
            qc.save_current_preset(Setlist.USER if Setlist else "/media/p4/Presets/My Presets",
                                   save.args["slot"], save.args["name"])
    return problems


def _enum(name: str):
    try:
        import pyquadcortex

        return getattr(pyquadcortex, name)
    except Exception:
        return None
