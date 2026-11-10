"""Catalogo dei modelli Quad Cortex.

Senza dispositivo collegato conosciamo i blocchi di fabbrica (costanti di
pyquadcortex.models), ma non i range dei parametri. La prima volta che il QC
e' collegato via USB salviamo il suo catalogo completo (parametri, modello
reale di riferimento "based on", Neural Capture dell'utente) in
~/.tone-builder/qc_catalog.json e da li' in poi lavoriamo anche offline.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from ..config import app_home
from ..knobs import ParamDef
from ..recipe import Role

# classe di pyquadcortex.models / categoria del device -> ruolo
_CATEGORY_ROLE = [
    (r"amplifier", Role.amp),
    (r"overdrive", Role.drive),
    (r"cab", Role.cab),
    (r"ir", Role.ir),
    (r"compressor", Role.comp),
    (r"delay", Role.delay),
    (r"reverb", Role.reverb),
    (r"modulation", Role.mod),
    (r"equalizer|eq", Role.eq),
    (r"wah", Role.wah),
    (r"pitch", Role.pitch),
    (r"filter", Role.filter),
    (r"synth", Role.synth),
    (r"looper", Role.looper),
    (r"capture", Role.amp),
    (r"gate|utility", Role.other),
]


def role_for_category(category: str) -> Role:
    c = category.lower()
    for pat, role in _CATEGORY_ROLE:
        if re.search(pat, c):
            return role
    return Role.other


@dataclass
class QCModel:
    id: int
    name: str
    category: str
    based_on: str = ""
    params: dict[str, ParamDef] = field(default_factory=dict)
    factory: bool = True

    @property
    def role(self) -> Role:
        return role_for_category(self.category)


def cache_path():
    return app_home() / "qc_catalog.json"


def _from_factory_constants() -> dict[int, QCModel]:
    from pyquadcortex import models as m

    out: dict[int, QCModel] = {}
    for cls_name in dir(m):
        cls = getattr(m, cls_name)
        if not isinstance(cls, type):
            continue
        category = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cls_name)
        for const, val in vars(cls).items():
            if const.startswith("_") or not isinstance(val, int):
                continue
            name = const.lstrip("N") if re.match(r"^N\d", const) else const
            out[val] = QCModel(val, name.replace("_", " ").title(), category)
    return out


def _from_cache() -> dict[int, QCModel] | None:
    p = cache_path()
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for d in data["models"]:
        params = {k: ParamDef(**v) for k, v in d.pop("params", {}).items()}
        out[int(d["id"])] = QCModel(params=params, **d)
    return out


def save_device_catalog(device_catalog) -> int:
    """Salva il catalogo letto dal QC (pyquadcortex ModelCatalog)."""
    rows = []
    for mdl in device_catalog:
        if mdl.hidden or mdl.internal:
            continue
        params = {
            p.name: asdict(ParamDef(p.name, float(p.minimum), float(p.maximum), float(p.default),
                                    p.type.lower().startswith("int") or p.steps is not None))
            for p in mdl.parameters
        }
        rows.append({"id": mdl.id, "name": mdl.name, "category": mdl.category,
                     "based_on": mdl.based_on or "", "factory": mdl.is_factory, "params": params})
    cache_path().write_text(json.dumps({"models": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    QCCatalog.load.cache_clear()
    return len(rows)


class QCCatalog:
    def __init__(self, models: dict[int, QCModel], from_device: bool):
        self.models = models
        self.from_device = from_device

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "QCCatalog":
        cached = _from_cache()
        if cached is not None:
            return cls(cached, True)
        try:
            return cls(_from_factory_constants(), False)
        except ImportError:
            return cls({}, False)

    def get(self, key) -> QCModel | None:
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            return self.models.get(int(key))
        k = str(key).strip().lower()
        for m in self.models.values():
            if m.name.lower() == k:
                return m
        return None

    def by_role(self, role: Role) -> list[QCModel]:
        ms = [m for m in self.models.values() if m.role == role]
        # a parita' di ruolo prima i blocchi per chitarra (poi quelli per basso)
        return sorted(ms, key=lambda m: "bass" in m.category.lower())

    def search(self, text: str, role: Role | None = None, limit: int = 8) -> list[QCModel]:
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        pool = self.by_role(role) if role else list(self.models.values())
        scored = []
        for m in pool:
            hay = set(re.findall(r"[a-z0-9]+", f"{m.name} {m.based_on}".lower()))
            s = len(words & hay)
            if s:
                scored.append((s + (0.5 if "guitar" in m.category.lower() else 0), m))
        scored.sort(key=lambda t: -t[0])
        return [m for _, m in scored[:limit]]

    def llm_listing(self, roles: list[Role] | None = None) -> str:
        roles = roles or [Role.amp, Role.drive, Role.comp, Role.mod, Role.delay, Role.reverb,
                          Role.cab, Role.eq, Role.wah, Role.pitch, Role.filter]
        lines = []
        for r in roles:
            for m in sorted(self.by_role(r), key=lambda m: m.name):
                based = f" (based on {m.based_on})" if m.based_on else ""
                lines.append(f"{m.id} | {m.name}{based} | {m.category}")
        return "\n".join(lines)
