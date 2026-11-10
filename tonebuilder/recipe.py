"""La "ricetta del suono": formato neutro, indipendente dal dispositivo.

L'AI scrive una ricetta; i backend la traducono in un preset Helix Stadium
(.hsp) o in una sequenza di scritture USB sul Quad Cortex.

Convenzione sui valori "neutri" (campo `settings` di ogni blocco):
- manopole classiche (gain, bass, mid, treble, presence, master, level, tone,
  drive, mix, feedback, decay, rate, depth, ...) sono espresse da 0 a 10,
  come sulle manopole di un ampli vero;
- `time_ms` e `predelay_ms` sono in millisecondi;
- `level_db` e' in dB.

Ogni blocco puo' anche portare valori nativi per un dispositivo (`helix`,
`qc`): se presenti, vincono sui valori neutri. Servono per importare preset
esistenti senza perdere precisione.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Role(str, Enum):
    gate = "gate"
    comp = "comp"
    wah = "wah"
    pitch = "pitch"
    drive = "drive"
    amp = "amp"
    preamp = "preamp"
    cab = "cab"
    ir = "ir"
    eq = "eq"
    mod = "mod"
    delay = "delay"
    reverb = "reverb"
    volume = "volume"
    filter = "filter"
    synth = "synth"
    looper = "looper"
    other = "other"


class DeviceModel(BaseModel):
    """Scelta concreta su un dispositivo: modello e parametri nativi."""

    model: str | None = None  # Helix: model_id (es. Agoura_AmpBritPlexi); QC: nome o id catalogo
    params: dict[str, Any] = Field(default_factory=dict)


class Block(BaseModel):
    id: str  # identificativo stabile nella ricetta, es. "amp1"
    role: Role
    gear_ref: str = ""  # ampli/pedale reale di riferimento, es. "Marshall JCM800"
    label: str = ""  # etichetta breve per footswitch/scribble strip
    line: int = Field(1, ge=1, le=2)  # linea 1 (pre/ampli) o 2 (effetti), in serie
    enabled: bool = True
    settings: dict[str, float] = Field(default_factory=dict)
    ir: str | None = None  # nome/percorso IR per cab o blocco IR
    helix: DeviceModel = Field(default_factory=DeviceModel)
    qc: DeviceModel = Field(default_factory=DeviceModel)
    notes: str = ""
    source: str = ""  # da quale preset/fonte arriva (utile nel merge)

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("id blocco vuoto")
        return v


class Snapshot(BaseModel):
    """Uno snapshot (Helix) / scena (Quad Cortex)."""

    name: str
    # blocchi accesi/spenti rispetto allo stato base (chiave = Block.id)
    enabled: dict[str, bool] = Field(default_factory=dict)
    # valori neutri diversi per questo snapshot: {block_id: {knob: value}}
    overrides: dict[str, dict[str, float]] = Field(default_factory=dict)
    level_db: float | None = None  # volume d'uscita dello snapshot
    notes: str = ""


class Footswitch(BaseModel):
    switch: str  # "FS1".."FS10" Helix / "A".."H" QC; il backend adatta
    block_id: str
    label: str = ""


class SongInfo(BaseModel):
    title: str = ""
    artist: str = ""
    year: int | None = None
    guitarist: str = ""
    reference_gear: str = ""  # strumentazione originale nota


class GuitarInfo(BaseModel):
    description: str = ""  # es. "Strat SSS", "Les Paul humbucker"
    pickups: str = ""


class ToneRecipe(BaseModel):
    name: str
    song: SongInfo = Field(default_factory=SongInfo)
    guitar: GuitarInfo = Field(default_factory=GuitarInfo)
    description: str = ""
    chain: list[Block] = Field(default_factory=list)
    snapshots: list[Snapshot] = Field(default_factory=list)
    footswitches: list[Footswitch] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # note per il chitarrista

    @model_validator(mode="after")
    def _refs_valid(self) -> "ToneRecipe":
        ids = [b.id for b in self.chain]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"id blocco duplicati: {sorted(dup)}")
        known = set(ids)
        for s in self.snapshots:
            bad = (set(s.enabled) | set(s.overrides)) - known
            if bad:
                raise ValueError(f"snapshot {s.name!r} cita blocchi inesistenti: {sorted(bad)}")
        for f in self.footswitches:
            if f.block_id not in known:
                raise ValueError(f"footswitch {f.switch} cita blocco inesistente: {f.block_id}")
        if len(self.snapshots) > 8:
            raise ValueError("massimo 8 snapshot/scene")
        return self

    def line_blocks(self, line: int) -> list[Block]:
        return [b for b in self.chain if b.line == line]

    def block(self, block_id: str) -> Block:
        for b in self.chain:
            if b.id == block_id:
                return b
        raise KeyError(block_id)

    def snapshot_controlled_params(self) -> dict[str, set[str]]:
        """Quali parametri cambiano tra snapshot (vanno messi in modalita' scena)."""
        out: dict[str, set[str]] = {}
        for s in self.snapshots:
            for bid, knobs in s.overrides.items():
                out.setdefault(bid, set()).update(knobs)
        return out

    def snapshot_controlled_bypass(self) -> set[str]:
        return {bid for s in self.snapshots for bid in s.enabled}


def json_schema_for_llm() -> dict[str, Any]:
    """Schema JSON da passare all'AI per l'output strutturato."""
    return ToneRecipe.model_json_schema()
