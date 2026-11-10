"""Catalogo dei modelli Helix Stadium.

Fonte primaria: le definizioni del dispositivo incluse in helixgen
(_defs_data.json): ogni modello con categoria e range dei parametri, anche
senza Stadium collegato. Se la libreria helixgen e' stata popolata con i tuoi
export (.hsp), usiamo anche i nomi "da editor" e segnaliamo quali modelli
sono pronti per la generazione.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

from ..knobs import ParamDef
from ..recipe import Role

# categoria helixgen -> ruolo nella ricetta
CATEGORY_ROLE: dict[str, Role] = {
    "amp": Role.amp,
    "preamp": Role.preamp,
    "distortion": Role.drive,
    "dynamics": Role.comp,
    "modulation": Role.mod,
    "delay": Role.delay,
    "reverb": Role.reverb,
    "cab_ir_interp": Role.cab,
    "ir": Role.ir,
    "eq": Role.eq,
    "wah": Role.wah,
    "volume": Role.volume,
    "pitch": Role.pitch,
    "filter": Role.filter,
    "synth": Role.synth,
    "looper": Role.looper,
}

_ROLE_CATEGORIES: dict[Role, set[str]] = {}
for _c, _r in CATEGORY_ROLE.items():
    _ROLE_CATEGORIES.setdefault(_r, set()).add(_c)
_ROLE_CATEGORIES[Role.gate] = {"dynamics"}

# parametri interni che non hanno senso per l'AI
_HIDDEN = re.compile(r"^(AmpCab|CabIdx|IrData|SyncSelect|TempoSync|Hum$|Ripple$|BiasX$)")


@dataclass
class HelixModel:
    model_id: str
    category: str
    name: str
    params: dict[str, ParamDef] = field(default_factory=dict)
    in_library: bool = False

    @property
    def role(self) -> Role:
        return CATEGORY_ROLE.get(self.category, Role.other)

    def visible_params(self) -> dict[str, ParamDef]:
        return {k: v for k, v in self.params.items() if not _HIDDEN.match(k)}


def humanize(model_id: str) -> str:
    """'Agoura_AmpBritPlexi' -> 'Brit Plexi' (nome indicativo)."""
    core = model_id.split("_", 1)[1] if "_" in model_id else model_id
    core = re.sub(r"^(CabMicIr_|Amp|Preamp|Cab|Dist|Delay|Reverb|Mod|Compressor|Wah|EQ|Pitch|Filter|Volume|Gate)", "", core)
    core = re.sub(r"(WithPan|Mono|Stereo)$", "", core)
    core = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", core)
    return core.strip() or model_id


def _load_defs() -> dict:
    with resources.files("helixgen.device").joinpath("_defs_data.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _library_blocks() -> dict[str, str]:
    """model_id -> display_name per i blocchi presenti nella libreria helixgen."""
    try:
        from helixgen.home import library_dir
        from helixgen.library import Library

        lib = Library(library_dir())
        return {b.model_id: b.display_name for b in lib.list_blocks()}
    except Exception:
        return {}


class HelixCatalog:
    def __init__(self, models: dict[str, HelixModel]):
        self.models = models

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "HelixCatalog":
        d = _load_defs()
        lib = _library_blocks()
        models: dict[str, HelixModel] = {}
        for mid, cat in d["model_categories"].items():
            if cat not in CATEGORY_ROLE:
                continue
            num = str(d["models"].get(mid))
            raw = d["model_params"].get(num, {})
            params = {
                k: ParamDef(k, float(v.get("min", 0)), float(v.get("max", 1)),
                            v.get("def"), v.get("type") == "i")
                for k, v in raw.items()
                if isinstance(v, dict) and "min" in v and "max" in v
            }
            models[mid] = HelixModel(mid, cat, lib.get(mid) or humanize(mid), params, mid in lib)
        return cls(models)

    @staticmethod
    def reload() -> "HelixCatalog":
        HelixCatalog.load.cache_clear()
        return HelixCatalog.load()

    def get(self, model_id: str) -> HelixModel | None:
        return self.models.get(model_id)

    def by_role(self, role: Role) -> list[HelixModel]:
        cats = _ROLE_CATEGORIES.get(role, set())
        return [m for m in self.models.values() if m.category in cats]

    def library_ready(self) -> bool:
        return any(m.in_library for m in self.models.values())

    def search(self, text: str, role: Role | None = None, limit: int = 8) -> list[HelixModel]:
        """Ricerca per somiglianza di parole (usata per riparare scelte dell'AI)."""
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        pool = self.by_role(role) if role else list(self.models.values())
        scored = []
        for m in pool:
            hay = set(re.findall(r"[a-z0-9]+", (m.name + " " + humanize(m.model_id)).lower()))
            score = len(words & hay)
            if score:
                scored.append((score + (0.5 if m.in_library else 0), m))
        scored.sort(key=lambda t: -t[0])
        return [m for _, m in scored[:limit]]

    def llm_listing(self, roles: list[Role] | None = None) -> str:
        """Elenco compatto 'model_id | nome | categoria' da dare all'AI."""
        roles = roles or [Role.amp, Role.drive, Role.comp, Role.mod, Role.delay, Role.reverb,
                          Role.cab, Role.eq, Role.wah, Role.volume, Role.pitch, Role.filter]
        lines = []
        for r in roles:
            for m in sorted(self.by_role(r), key=lambda m: m.model_id):
                flag = " *" if m.in_library else ""
                lines.append(f"{m.model_id} | {m.name} | {m.category}{flag}")
        return "\n".join(dict.fromkeys(lines))
