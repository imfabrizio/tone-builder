"""Separazione della chitarra e misure di base sulla traccia.

- Separazione: Demucs, modello htdemucs_6s, che ha una traccia "guitar"
  dedicata. Gira come programma esterno, quindi funziona anche con
  l'eseguibile Windows purche' Demucs sia installato sul computer.
- Misure: librosa (brillantezza, dinamica, tempo, "ruvidita'" del segnale)
  da passare all'AI quando il modello non ascolta l'audio direttamente.
Entrambi sono opzionali: se mancano, l'app lo dice e si va avanti.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


_DEMUCS_CACHE: list[str] | None | bool = False  # False = non ancora cercato


def _probe(cmd: list[str]) -> bool:
    try:
        r = subprocess.run(cmd + ["--help"], capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def demucs_command(refresh: bool = False) -> list[str] | None:
    """Come lanciare Demucs su questo computer, oppure None se non c'e'.

    Ordine: percorso indicato nelle Impostazioni; Demucs nello stesso Python
    dell'app (non nell'eseguibile Windows); comando `demucs` nel PATH (es.
    installato con pipx); su Windows il launcher `py` con Demucs installato.
    """
    global _DEMUCS_CACHE
    if _DEMUCS_CACHE is not False and not refresh:
        return _DEMUCS_CACHE  # type: ignore[return-value]
    from ..config import load_settings

    found: list[str] | None = None
    custom = load_settings().demucs_command.strip().strip('"')
    if custom:
        p = Path(custom).expanduser()
        cand = [str(p)] if p.suffix.lower() not in (".py",) and "python" not in p.name.lower() else [str(p), "-m", "demucs"]
        found = cand if _probe(cand) else None
    if found is None and not getattr(sys, "frozen", False):
        try:
            import demucs  # noqa: F401
            found = [sys.executable, "-m", "demucs"]
        except ImportError:
            pass
    if found is None and shutil.which("demucs"):
        found = [shutil.which("demucs")]  # type: ignore[list-item]
    if found is None and sys.platform == "win32" and shutil.which("py"):
        cand = ["py", "-3", "-m", "demucs"]
        found = cand if _probe(cand) else None
    _DEMUCS_CACHE = found
    return found


def demucs_available() -> bool:
    return demucs_command() is not None


DEMUCS_HELP = (
    "Separazione della chitarra non disponibile: Demucs non e' installato su questo computer, "
    "analizzo il mix completo. Per attivarla vedi la sezione 'Separazione della chitarra' del README."
)


def separate_guitar(audio: str | Path, out_dir: str | Path) -> Path:
    """Isola la chitarra. Ritorna il percorso di guitar.wav."""
    cmd0 = demucs_command()
    if not cmd0:
        raise RuntimeError(DEMUCS_HELP)
    audio, out_dir = Path(audio), Path(out_dir)
    cmd = cmd0 + ["-n", "htdemucs_6s", "--two-stems", "guitar", "-o", str(out_dir), str(audio)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"Demucs: {r.stderr.strip()[-500:]}")
    hits = list(out_dir.rglob("guitar.wav"))
    if not hits:
        raise RuntimeError("Demucs non ha prodotto la traccia chitarra")
    return max(hits, key=lambda p: p.stat().st_mtime)


def features(audio: str | Path) -> dict:
    """Misure sintetiche della traccia (richiede librosa)."""
    import numpy as np
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True, duration=240)
    if y.size == 0:
        return {}
    rms = librosa.feature.rms(y=y)[0]
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    flat = librosa.feature.spectral_flatness(y=y)[0]
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    active = rms > (rms.max() * 0.1)
    loud = 20 * np.log10(rms[active] + 1e-9) if active.any() else np.array([0.0])
    # sezioni: segmenti da 15 s con livello e brillantezza
    hop = 512
    seg = int(15 * sr / hop)
    sections = []
    for i in range(0, len(rms), seg):
        r = rms[i:i + seg]
        c = cent[i:i + seg]
        if r.size:
            sections.append({
                "from_s": round(i * hop / sr), "rms_db": round(float(20 * np.log10(r.mean() + 1e-9)), 1),
                "brightness_hz": round(float(c.mean())),
            })
    return {
        "tempo_bpm": round(float(np.atleast_1d(tempo)[0]), 1),
        "brightness_hz": round(float(np.median(cent))),
        "dynamic_range_db": round(float(np.percentile(loud, 95) - np.percentile(loud, 10)), 1),
        "noisiness": round(float(np.median(flat)), 4),
        "sections": sections[:16],
    }


def features_text(f: dict) -> str:
    if not f:
        return ""
    lines = [
        f"Tempo stimato: {f['tempo_bpm']} BPM (utile per il delay)",
        f"Brillantezza mediana (centroide spettrale): {f['brightness_hz']} Hz",
        f"Escursione dinamica: {f['dynamic_range_db']} dB (bassa = molto compresso/saturato)",
        f"Indice di rumorosita' spettrale: {f['noisiness']} (piu' alto = piu' distorsione/fuzz)",
        "Sezioni (inizio s, livello dB, brillantezza Hz): "
        + "; ".join(f"{s['from_s']}s {s['rms_db']}dB {s['brightness_hz']}Hz" for s in f.get("sections", [])),
    ]
    return "\n".join(lines)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None
