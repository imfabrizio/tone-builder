"""Separazione della chitarra e misure di base sulla traccia.

- Separazione: Demucs, modello htdemucs_6s, che ha una traccia "guitar"
  dedicata (pip install demucs; la prima volta scarica il modello).
- Misure: librosa (brillantezza, dinamica, tempo, "ruvidita'" del segnale)
  da passare all'AI quando il modello non ascolta l'audio direttamente.
Entrambi sono opzionali: se mancano, l'app lo dice e si va avanti.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def demucs_available() -> bool:
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def separate_guitar(audio: str | Path, out_dir: str | Path) -> Path:
    """Isola la chitarra. Ritorna il percorso di guitar.wav."""
    if not demucs_available():
        raise RuntimeError("Demucs non installato: pip install demucs")
    audio, out_dir = Path(audio), Path(out_dir)
    cmd = [sys.executable, "-m", "demucs", "-n", "htdemucs_6s", "--two-stems", "guitar", "-o", str(out_dir), str(audio)]
    r = subprocess.run(cmd, capture_output=True, text=True)
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
