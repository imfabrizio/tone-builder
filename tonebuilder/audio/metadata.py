"""Titolo e autore da un file audio o da un link.

- File (MP3, FLAC, M4A, WAV...): tag letti con mutagen.
- Link YouTube: metadati con yt-dlp (opzionale). Scaricare l'audio da YouTube
  e' contro i termini di servizio di YouTube: la funzione esiste per uso
  personale ed e' a tuo rischio; i soli metadati non scaricano nulla.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_s: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|lyrics|remaster|remastered|hd|hq|4k|live|visualizer)[^\)\]]*[\)\]]",
    re.I,
)


def split_title(raw: str) -> tuple[str, str]:
    """'Artista - Titolo (Official Video)' -> ('Artista', 'Titolo')."""
    t = _NOISE.sub("", raw).strip()
    for sep in (" - ", " – ", " — ", " | "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip()
    return "", t


def from_file(path: str | Path) -> TrackInfo:
    path = Path(path)
    info = TrackInfo(source=path.name)
    try:
        import mutagen

        f = mutagen.File(path, easy=True)
        if f is not None:
            tags = f.tags or {}

            def first(key: str) -> str:
                v = tags.get(key)
                return (v[0] if isinstance(v, list) and v else v or "") if v else ""

            info.title, info.artist, info.album = first("title"), first("artist"), first("album")
            if getattr(f, "info", None) is not None and getattr(f.info, "length", None):
                info.duration_s = round(float(f.info.length), 1)
    except Exception:
        pass
    if not info.title:
        info.artist, info.title = split_title(path.stem.replace("_", " "))
    return info


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip()))


def from_url(url: str) -> TrackInfo:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("Per i link serve yt-dlp: pip install yt-dlp") from e
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
        d = ydl.extract_info(url, download=False)
    info = TrackInfo(
        title=d.get("track") or "",
        artist=d.get("artist") or d.get("creator") or "",
        album=d.get("album") or "",
        duration_s=d.get("duration"),
        source=url,
    )
    if not info.title:
        a, t = split_title(d.get("title") or "")
        info.title = t
        info.artist = info.artist or a or (d.get("uploader") or "").removesuffix(" - Topic")
    return info


def download_audio(url: str, dest_dir: str | Path) -> Path:
    """Scarica la traccia audio (uso personale, a tuo rischio: vedi nota sopra)."""
    import yt_dlp

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True, "noplaylist": True, "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        d = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(d))
