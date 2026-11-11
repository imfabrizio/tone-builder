"""Import di una playlist Spotify come scaletta.

Spotify (regole da febbraio 2026) permette alle app personali di leggere i
brani solo delle playlist che crei o di cui sei collaboratore, e solo con il
tuo accesso. Serve quindi una piccola app gratuita sul portale sviluppatori:

1. https://developer.spotify.com/dashboard -> Create app
2. Redirect URI: http://127.0.0.1:8765/api/spotify/callback
3. API: Web API. Copia il "Client ID" nelle Impostazioni di Tone Builder.

Il proprietario dell'app deve avere Spotify Premium (requisito di Spotify per
le app in modalita' sviluppo). Usiamo il flusso PKCE: nessun segreto da salvare.
Il token resta in ~/.tone-builder/spotify_token.json.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx

from .config import app_home

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative"

_pending: dict[str, str] = {}  # state -> code_verifier


class SpotifyError(RuntimeError):
    pass


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/spotify/callback"


def _token_path():
    return app_home() / "spotify_token.json"


def login_url(client_id: str, port: int) -> str:
    if not client_id:
        raise SpotifyError("Manca il Client ID di Spotify: inseriscilo nelle Impostazioni")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri(port),
        "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state,
    })


def _save(tok: dict, client_id: str) -> None:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    tok["client_id"] = client_id
    _token_path().write_text(json.dumps(tok), encoding="utf-8")


def finish_login(client_id: str, code: str, state: str, port: int) -> None:
    verifier = _pending.pop(state, None)
    if not verifier:
        raise SpotifyError("Accesso a Spotify scaduto o non valido: riprova")
    r = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(port),
        "client_id": client_id, "code_verifier": verifier,
    }, timeout=20)
    if r.status_code != 200:
        raise SpotifyError(f"Spotify ha rifiutato l'accesso: {r.text[:200]}")
    _save(r.json(), client_id)


def connected() -> bool:
    return _token_path().exists()


def logout() -> None:
    _token_path().unlink(missing_ok=True)


def _access_token() -> str:
    p = _token_path()
    if not p.exists():
        raise SpotifyError("Collega prima Spotify (Importa scaletta > Spotify > Collega)")
    tok = json.loads(p.read_text(encoding="utf-8"))
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": tok["client_id"]}, timeout=20)
    if r.status_code != 200:
        logout()
        raise SpotifyError("Accesso a Spotify scaduto: ricollegalo")
    new = r.json()
    new.setdefault("refresh_token", tok["refresh_token"])
    _save(new, tok["client_id"])
    return new["access_token"]


def _get(url: str, params: dict | None = None) -> dict:
    r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=20)
    if r.status_code == 403:
        raise SpotifyError("Spotify nega l'accesso: con le app personali si leggono solo playlist create da te "
                           "o di cui sei collaboratore, e il proprietario dell'app deve avere Premium.")
    if r.status_code != 200:
        raise SpotifyError(f"Spotify: errore {r.status_code} {r.text[:200]}")
    return r.json()


def my_playlists() -> list[dict]:
    out, url, params = [], f"{API}/me/playlists", {"limit": 50}
    while url and len(out) < 500:
        d = _get(url, params)
        params = None
        for p in d.get("items", []):
            if p:
                out.append({"id": p["id"], "name": p.get("name", ""), "owner": (p.get("owner") or {}).get("display_name", ""),
                            "count": ((p.get("items") or p.get("tracks") or {}).get("total"))})
        url = d.get("next")
    return out


def _parse_id(playlist: str) -> str:
    s = playlist.strip()
    if "playlist/" in s:
        s = s.split("playlist/", 1)[1]
    return s.split("?")[0].split("/")[0]


def playlist_tracks(playlist: str) -> tuple[str, list[dict]]:
    """Ritorna (nome playlist, brani [{title, artist, year, duration_s, spotify_id}])."""
    pid = _parse_id(playlist)
    meta = _get(f"{API}/playlists/{pid}", {"fields": "name"})
    tracks: list[dict] = []
    url, params = f"{API}/playlists/{pid}/items", {"limit": 100}
    try:
        first = _get(url, params)
    except SpotifyError:
        url = f"{API}/playlists/{pid}/tracks"  # nome precedente dell'endpoint
        first = _get(url, params)
    d = first
    while True:
        for row in d.get("items", []):
            t = row.get("item") or row.get("track") or {}
            if not t or t.get("type", "track") != "track":
                continue
            date = (t.get("album") or {}).get("release_date") or ""
            tracks.append({
                "title": t.get("name", ""),
                "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
                "year": int(date[:4]) if date[:4].isdigit() else None,
                "duration_s": round((t.get("duration_ms") or 0) / 1000),
                "spotify_id": t.get("id", ""),
            })
        if not d.get("next"):
            break
        d = _get(d["next"])
    if not tracks and "items" not in first:
        raise SpotifyError("Spotify non restituisce i brani: la playlist deve essere tua o condivisa con te come collaboratore.")
    return meta.get("name", "Playlist"), tracks


def parse_text_list(text: str) -> list[dict]:
    """'Artista - Titolo' (o 'Titolo - Artista' con ' by '), una riga per brano; accetta anche CSV di Exportify."""
    out = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and "track name" in lines[0].lower() and "," in lines[0]:  # CSV Exportify
        import csv
        import io

        for row in csv.DictReader(io.StringIO("\n".join(lines))):
            title = row.get("Track Name") or row.get("track name") or ""
            artist = row.get("Artist Name(s)") or row.get("Artist Name") or ""
            if title:
                out.append({"title": title, "artist": artist.replace(";", ", ")})
        return out
    for ln in lines:
        ln = ln.lstrip("0123456789.)- \t")
        if " by " in ln.lower():
            i = ln.lower().index(" by ")
            out.append({"title": ln[:i].strip(), "artist": ln[i + 4:].strip()})
            continue
        for sep in (" - ", " – ", " — ", "\t", " | "):
            if sep in ln:
                a, b = ln.split(sep, 1)
                out.append({"artist": a.strip(), "title": b.strip()})
                break
        else:
            out.append({"title": ln, "artist": ""})
    return out
