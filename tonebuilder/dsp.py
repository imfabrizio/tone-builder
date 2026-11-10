"""Stima del carico DSP.

Nessuno dei due produttori pubblica un costo per blocco, quindi questi sono
pesi indicativi (in % del budget del percorso/preset) ricavati dall'uso
pratico: servono a decidere quando un merge va semplificato, non a
garantire che il preset entri. Il verdetto finale e' del dispositivo: lo
Stadium rifiuta l'import, il QC rifiuta il blocco (e noi lo segnaliamo).
I valori si possono ritoccare in ~/.tone-builder/dsp_weights.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .config import app_home
from .recipe import Block, Role, ToneRecipe

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    # Helix Stadium: % di UN percorso (un preset puo' usarne due)
    "helix": {
        "amp": 28, "preamp": 14, "cab": 9, "ir": 6, "drive": 6, "comp": 4, "gate": 2,
        "wah": 4, "pitch": 22, "eq": 3, "mod": 8, "delay": 8, "reverb": 13,
        "volume": 1, "filter": 6, "synth": 18, "looper": 5, "other": 5,
    },
    # Quad Cortex: % del budget dell'intero preset
    "qc": {
        "amp": 20, "preamp": 12, "cab": 6, "ir": 5, "drive": 4, "comp": 3, "gate": 2,
        "wah": 3, "pitch": 16, "eq": 3, "mod": 6, "delay": 6, "reverb": 10,
        "volume": 1, "filter": 5, "synth": 14, "looper": 4, "other": 4,
    },
}

# margine di sicurezza: oltre questa soglia chiediamo conferma
BUDGET = {"helix": 92.0, "qc": 90.0}


def weights() -> dict[str, dict[str, float]]:
    p = app_home() / "dsp_weights.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            return {dev: {**DEFAULT_WEIGHTS[dev], **user.get(dev, {})} for dev in DEFAULT_WEIGHTS}
        except Exception:
            pass
    return DEFAULT_WEIGHTS


def block_cost(b: Block, device: str) -> float:
    w = weights()[device]
    role = b.role.value
    # un blocco "amp" con cab integrato (QC / amp+cab Helix) pesa di piu'
    return float(w.get(role, w["other"]))


@dataclass
class DspEstimate:
    helix: float
    qc: float

    @property
    def fits(self) -> bool:
        return self.helix <= BUDGET["helix"] and self.qc <= BUDGET["qc"]

    def to_dict(self) -> dict:
        return {"helix": round(self.helix, 1), "qc": round(self.qc, 1),
                "budget": BUDGET, "fits": self.fits}


def estimate(recipe: ToneRecipe) -> DspEstimate:
    return DspEstimate(
        helix=sum(block_cost(b, "helix") for b in recipe.chain),
        qc=sum(block_cost(b, "qc") for b in recipe.chain),
    )


def estimate_blocks(blocks: list[Block]) -> DspEstimate:
    return DspEstimate(sum(block_cost(b, "helix") for b in blocks),
                       sum(block_cost(b, "qc") for b in blocks))


ROLE_ORDER = [Role.gate, Role.comp, Role.wah, Role.pitch, Role.filter, Role.drive, Role.amp,
              Role.preamp, Role.cab, Role.ir, Role.eq, Role.mod, Role.delay, Role.reverb,
              Role.volume, Role.synth, Role.other, Role.looper]
