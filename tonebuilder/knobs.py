"""Traduzione tra manopole "neutre" della ricetta e parametri nativi.

Le manopole neutre sono quelle che un chitarrista legge su un ampli o un
pedale reale. Per ogni manopola elenchiamo i nomi con cui compare sui due
dispositivi (il confronto ignora maiuscole, spazi e trattini).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# manopola neutra -> nomi candidati del parametro nativo, in ordine di preferenza
KNOB_ALIASES: dict[str, list[str]] = {
    "gain": ["Drive", "Gain", "BrightDrv", "NormDrv", "Volume I", "Fuzz", "Distortion", "Overdrive"],
    "drive": ["Drive", "Gain", "Distortion", "Fuzz", "Overdrive", "Sustain"],
    "bass": ["Bass", "Low"],
    "mid": ["Mid", "Middle", "Mids"],
    "treble": ["Treble", "High", "Highs"],
    "presence": ["Presence"],
    "master": ["Master", "Master Volume"],
    "channel_volume": ["ChVol", "Channel Volume", "Volume"],
    "tone": ["Tone", "Filter"],
    "level_db": ["Level", "Output", "Output Level"],
    "level": ["Level", "Output", "Volume"],
    "mix": ["Mix", "Blend", "Dry/Wet"],
    "time_ms": ["Time", "Delay Time", "Time1"],
    "feedback": ["Feedback", "Repeats", "Regen"],
    "decay": ["Decay", "Time", "Decay Time"],
    "predelay_ms": ["Predelay", "PreDelay", "Pre-Delay", "Pre Delay"],
    "rate": ["Speed", "Rate", "Frequency"],
    "depth": ["Depth", "Intensity", "Width"],
    "threshold_db": ["Threshold", "Thresh"],
    "ratio": ["Ratio"],
    "sustain": ["Sustain", "Compression", "Sensitivity"],
    "attack_ms": ["Attack"],
    "low_cut_hz": ["LowCut", "Low Cut"],
    "high_cut_hz": ["HighCut", "High Cut"],
    "bright": ["Bright"],
    "sag": ["Sag"],
    "bias": ["Bias"],
}


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-/]+", "", name).lower()


@dataclass(frozen=True)
class ParamDef:
    name: str
    min: float
    max: float
    default: float | None = None
    is_int: bool = False


def find_param(knob: str, params: dict[str, ParamDef]) -> ParamDef | None:
    """Trova il parametro nativo corrispondente a una manopola neutra."""
    by_norm = {_norm(k): p for k, p in params.items()}
    # una manopola puo' anche essere gia' un nome nativo
    if _norm(knob) in by_norm:
        return by_norm[_norm(knob)]
    for alias in KNOB_ALIASES.get(knob, []):
        p = by_norm.get(_norm(alias))
        if p is not None:
            return p
    return None


def to_native(knob: str, value: float, p: ParamDef) -> float:
    """Converte il valore neutro nell'unita' del parametro nativo, con clamp."""
    lo, hi = p.min, p.max
    if knob.endswith("_ms"):
        # i parametri tempo nativi sono in secondi quando il range e' piccolo
        v = value / 1000.0 if hi <= 20 else value
    elif knob.endswith(("_db", "_hz")) or knob == "ratio":
        v = value
    else:  # manopola 0..10
        frac = max(0.0, min(10.0, value)) / 10.0
        v = lo + (hi - lo) * frac
    v = max(lo, min(hi, v))
    if p.is_int:
        v = float(round(v))
    return round(v, 4)


def to_neutral(knob: str, native: float, p: ParamDef) -> float:
    """Operazione inversa, usata quando si importa un preset esistente."""
    if knob.endswith("_ms"):
        return round(native * 1000.0 if p.max <= 20 else native, 1)
    if knob.endswith(("_db", "_hz")) or knob == "ratio":
        return round(native, 2)
    if p.max == p.min:
        return 0.0
    return round(10.0 * (native - p.min) / (p.max - p.min), 2)


def map_settings(settings: dict[str, float], params: dict[str, ParamDef]) -> tuple[dict[str, float], list[str]]:
    """Traduce tutte le manopole di un blocco. Ritorna (parametri nativi, manopole non mappate)."""
    out: dict[str, float] = {}
    missing: list[str] = []
    for knob, value in settings.items():
        p = find_param(knob, params)
        if p is None:
            missing.append(knob)
            continue
        out[p.name] = to_native(knob, float(value), p)
    return out, missing
