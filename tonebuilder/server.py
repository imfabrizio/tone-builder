"""Server locale: API + interfaccia grafica nel browser.

Avvio:  python -m tonebuilder   (oppure il comando `tone-builder`)
Ascolta solo su 127.0.0.1: l'app non e' raggiungibile da altri computer.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
import webbrowser
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, dsp, sources
from .ai import AIError, get_provider
from .audio import analysis, metadata
from .backends import helix_stadium as hx
from .backends import quad_cortex as qcb
from .catalogs.helix import HelixCatalog
from .catalogs.quadcortex import QCCatalog
from .config import ProviderSettings, Settings, app_home, load_settings, public_view, save_settings
from . import gear as gearmod
from . import spotify
from . import planner
from .ai import get_provider
from .designer import DesignRequest, describe_audio, design, edit, suggest_guitars
from .importers import import_file
from .merge import Pick, merge
from .recipe import ToneRecipe

app = FastAPI(title="Tone Builder", version=__version__)
WEB = resources.files("tonebuilder").joinpath("web")


def _dir(name: str) -> Path:
    p = app_home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", Path(name).name)[:120] or "file"


def _err(e: Exception, status: int = 400):
    # l'errore compare anche nella finestra del server, utile se l'interfaccia lo nasconde
    print(f"[errore] {e}", flush=True)
    raise HTTPException(status_code=status, detail=str(e))


def _provider(name: str | None = None):
    try:
        return get_provider(load_settings(), name)
    except AIError as e:
        _err(e)


# ------------------------------------------------------------------ stato e impostazioni

def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


@app.get("/api/status")
def status() -> dict[str, Any]:
    s = load_settings()
    return {
        "version": __version__,
        "settings": public_view(s),
        "helix": hx.library_status(),
        "qc": {"catalog_from_device": QCCatalog.load().from_device, "models": len(QCCatalog.load().models),
               "library_installed": _has("pyquadcortex")},
        "optional": {"yt_dlp": _has("yt_dlp"), "demucs": analysis.demucs_available(),
                     "librosa": _has("librosa"), "ffmpeg": analysis.ffmpeg_available()},
    }


class SettingsIn(BaseModel):
    provider: str | None = None
    providers: dict[str, dict[str, str]] = {}
    web_research: bool | None = None
    ir_folders: list[str] | None = None
    preset_folders: list[str] | None = None
    helix_output_dir: str | None = None
    qc_target_setlist_slot: str | None = None
    demucs_command: str | None = None
    spotify_client_id: str | None = None


@app.put("/api/settings")
def update_settings(body: SettingsIn) -> dict[str, Any]:
    s = load_settings()
    if body.provider:
        s.provider = body.provider
    for name, vals in body.providers.items():
        cur = s.providers.get(name, ProviderSettings())
        for k in ("model", "base_url", "workspace_id"):
            if k in vals:
                setattr(cur, k, vals[k])
        if vals.get("api_key"):  # campo vuoto = lascia la chiave esistente
            cur.api_key = vals["api_key"].strip()
        s.providers[name] = cur
    for k in ("web_research", "ir_folders", "preset_folders", "helix_output_dir", "qc_target_setlist_slot", "demucs_command",
              "spotify_client_id"):
        v = getattr(body, k)
        if v is not None:
            setattr(s, k, v)
    save_settings(s)
    if body.demucs_command is not None:
        analysis.demucs_command(refresh=True)
    return public_view(s)


@app.get("/api/providers/{name}/models")
def provider_models(name: str) -> list[str]:
    try:
        return _provider(name).list_models()
    except AIError as e:
        _err(e)


# ------------------------------------------------------------------ libreria Helix / catalogo QC

class PathsIn(BaseModel):
    paths: list[str]


@app.post("/api/helix/ingest")
async def helix_ingest(files: list[UploadFile] = File(default=[]), path: str = Form(default="")) -> dict:
    try:
        if path:
            out = hx.ingest(Path(path).expanduser())
        else:
            if not files:
                raise ValueError("Nessun file")
            tmp = _dir("uploads") / f"ingest-{uuid.uuid4().hex[:8]}"
            tmp.mkdir()
            for f in files:
                (tmp / _safe_name(f.filename or "preset.hsp")).write_bytes(await f.read())
            out = hx.ingest(tmp)
        return {"message": out, **hx.library_status()}
    except Exception as e:
        _err(e)


_stadium_job: dict[str, Any] = {"running": False}


class StadiumImportIn(BaseModel):
    include_factory: bool = True
    ip: str = ""


@app.post("/api/helix/stadium-import")
def stadium_import_start(body: StadiumImportIn) -> dict:
    """Avvia in background la lettura dei preset dallo Stadium in rete."""
    if _stadium_job.get("running"):
        return _stadium_job
    _stadium_job.clear()
    _stadium_job.update({"running": True, "done": 0, "total": 0, "current": "Avvio…", "error": None, "result": None})

    def progress(n: int, tot: int, text: str) -> None:
        _stadium_job.update({"done": n, "total": tot, "current": text})

    def work() -> None:
        try:
            _stadium_job["result"] = hx.stadium_import(body.include_factory, body.ip, progress)
        except Exception as e:
            print(f"[errore] {e}", flush=True)
            _stadium_job["error"] = str(e)
        finally:
            _stadium_job["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return _stadium_job


@app.get("/api/helix/stadium-import")
def stadium_import_status() -> dict:
    return _stadium_job


@app.post("/api/helix/register-irs")
def helix_register_irs(body: PathsIn) -> dict:
    try:
        return {"message": hx.register_irs(body.paths)}
    except Exception as e:
        _err(e)


@app.post("/api/qc/read-catalog")
def qc_read_catalog() -> dict:
    try:
        return {"models": qcb.read_catalog()}
    except Exception as e:
        _err(e)


# ------------------------------------------------------------------ setlist (progetti)

def _setlist_path(sid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8,32}", sid):
        _err(ValueError("id non valido"))
    return _dir("setlists") / f"{sid}.json"


@app.get("/api/setlists")
def list_setlists() -> list[dict]:
    out = []
    for p in sorted(_dir("setlists").glob("*.json"), key=lambda p: -p.stat().st_mtime):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": p.stem, "name": d.get("name", p.stem), "songs": len(d.get("songs", []))})
    return out


@app.post("/api/setlists")
def create_setlist(body: dict) -> dict:
    sid = uuid.uuid4().hex[:12]
    data = {"id": sid, "name": body.get("name") or "Nuova scaletta", "songs": []}
    _setlist_path(sid).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def clean_links(data: dict) -> dict:
    """Toglie i collegamenti a patch che non esistono piu' (es. cancellate) e aggiorna i nomi."""
    patches = {x.get("id"): x for x in data.get("songs", []) if x.get("kind") == "patch" and x.get("recipe")}
    for x in data.get("songs", []):
        if x.get("kind") == "patch":
            continue
        if x.get("uses_patch"):
            p = patches.get(x["uses_patch"])
            if p is None:
                for k in ("uses_patch", "uses_patch_name", "patch_usage"):
                    x.pop(k, None)
            else:
                x["uses_patch_name"] = p.get("title", "")
        if x.get("suggested_patch"):
            p = patches.get(x["suggested_patch"])
            if p is None:
                for k in ("suggested_patch", "suggested_patch_name", "suggested_usage"):
                    x.pop(k, None)
            else:
                x["suggested_patch_name"] = p.get("title", "")
    for p in patches.values():
        p["covers"] = [f"{x.get('artist', '')} - {x.get('title', '')}" for x in data.get("songs", [])
                       if x.get("uses_patch") == p.get("id")]
    return data


@app.get("/api/setlists/{sid}")
def get_setlist(sid: str) -> dict:
    p = _setlist_path(sid)
    if not p.exists():
        _err(KeyError("scaletta non trovata"), 404)
    return clean_links(json.loads(p.read_text(encoding="utf-8")))


@app.put("/api/setlists/{sid}")
def put_setlist(sid: str, body: dict) -> dict:
    for song in body.get("songs", []):
        if song.get("recipe"):
            ToneRecipe.model_validate(song["recipe"])  # niente ricette rotte su disco
    clean_links(body)
    body["id"] = sid
    _setlist_path(sid).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return body


@app.delete("/api/setlists/{sid}")
def delete_setlist(sid: str) -> dict:
    _setlist_path(sid).unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------------------ strumentazione

@app.get("/api/gear")
def get_gear() -> dict:
    g = gearmod.load_gear()
    return {**g.model_dump(), "configs": gearmod.PICKUP_CONFIGS}


@app.put("/api/gear")
def put_gear(body: gearmod.GearStore) -> dict:
    return gearmod.save_gear(body).model_dump()


@app.get("/api/gear/export")
def gear_export():
    p = app_home() / "gear.json"
    gearmod.save_gear(gearmod.load_gear())
    return FileResponse(p, filename="strumentazione-tone-builder.json", media_type="application/json")


@app.post("/api/gear/import")
async def gear_import(file: UploadFile = File(...), merge: bool = Form(default=True)) -> dict:
    try:
        new = gearmod.GearStore.model_validate(json.loads((await file.read()).decode("utf-8")))
    except Exception as e:
        _err(ValueError(f"File di strumentazione non valido: {e}"))
    if merge:
        cur = gearmod.load_gear()
        names = {g.name.lower() for g in cur.guitars}
        cur.guitars += [g for g in new.guitars if g.name.lower() not in names]
        for k in ("wah_external", "iridium", "iridium_midi_channel", "iridium_first_preset"):
            setattr(cur, k, getattr(new, k))
        if new.other:
            cur.other = new.other
        new = cur
    return gearmod.save_gear(new).model_dump()


@app.post("/api/iridium/guide")
def iridium_guide(body: dict) -> dict:
    from . import iridium

    try:
        r = ToneRecipe.model_validate(body.get("recipe") or {})
    except Exception as e:
        _err(e)
    return iridium.guide(r)


@app.get("/api/gear/search")
def gear_search(q: str = "", kind: str = "guitar") -> list[dict]:
    return gearmod.search_pickups(q) if kind == "pickup" else gearmod.search_guitars(q)


# ------------------------------------------------------------------ scaletta: import e automazioni

PORT = 8765


@app.get("/api/spotify/status")
def spotify_status() -> dict:
    s = load_settings()
    return {"client_id": bool(s.spotify_client_id), "connected": spotify.connected(),
            "redirect_uri": spotify.redirect_uri(PORT)}


@app.get("/api/spotify/login")
def spotify_login():
    try:
        return RedirectResponse(spotify.login_url(load_settings().spotify_client_id, PORT))
    except spotify.SpotifyError as e:
        _err(e)


@app.get("/api/spotify/callback")
def spotify_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/?spotify=errore&msg={error}")
    try:
        spotify.finish_login(load_settings().spotify_client_id, code, state, PORT)
    except spotify.SpotifyError as e:
        print(f"[errore] {e}", flush=True)
        return RedirectResponse("/?spotify=errore")
    return RedirectResponse("/?spotify=ok")


@app.get("/api/spotify/playlists")
def spotify_playlists() -> list[dict]:
    try:
        return spotify.my_playlists()
    except Exception as e:
        _err(e)


class SetlistImportIn(BaseModel):
    playlist: str = ""  # link o id della playlist Spotify
    text: str = ""  # oppure elenco incollato
    name: str = ""
    setlist_id: str = ""  # se presente, aggiunge i brani a questa scaletta


@app.post("/api/setlists/import")
def import_setlist(body: SetlistImportIn) -> dict:
    try:
        if body.playlist.strip():
            pname, tracks = spotify.playlist_tracks(body.playlist)
        else:
            pname, tracks = body.name or "Scaletta importata", spotify.parse_text_list(body.text)
    except Exception as e:
        _err(e)
    if not tracks:
        _err(ValueError("Nessun brano trovato"))
    songs = [{"id": uuid.uuid4().hex[:8], "title": t["title"], "artist": t.get("artist", ""),
              "request": {}, "audio": None, "recipe": None, "research": "", "sources": [], "links": [],
              "warnings": [], "chat": []} for t in tracks]
    if body.setlist_id:
        data = get_setlist(body.setlist_id)
        data["songs"].extend(songs)
    else:
        data = {"id": uuid.uuid4().hex[:12], "name": body.name or pname, "songs": songs}
    _setlist_path(data["id"]).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


@app.post("/api/setlists/{sid}/suggest-guitars")
def api_suggest_guitars(sid: str) -> dict:
    data = get_setlist(sid)
    text = gearmod.describe_for_ai(gearmod.load_gear())
    try:
        rows = suggest_guitars(data["songs"], text, _provider())
    except AIError as e:
        _err(e)
    for r in rows:
        song = data["songs"][r["index"]]
        req = song.setdefault("request", {}) or {}
        parts = [r["guitar"], f"pickup: {r['pickups']}" if r["pickups"] else "", f"accordatura: {r['tuning']}" if r["tuning"] else ""]
        req["guitar"] = " — ".join(p for p in parts if p)
        song["request"] = req
        song["guitar_reason"] = (f"Nel disco: {r['original']}. " if r["original"] else "") + r["reason"]
    _setlist_path(sid).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


_jobs: dict[str, dict[str, Any]] = {}


def _ir_files() -> dict[str, str]:
    """IR dell'utente: nome file -> percorso (dalle cartelle IR delle impostazioni)."""
    out: dict[str, str] = {}
    for r in sources.scan(load_settings().ir_folders or [], "ir"):
        out.setdefault(Path(r.path).name, r.path)
    return out


def _ir_names() -> list[str]:
    return sorted(_ir_files())


@app.post("/api/setlists/{sid}/design-all")
def design_all(sid: str, body: dict | None = None) -> dict:
    """Crea in background i preset di tutti i brani che non ce l'hanno."""
    job = _jobs.get(f"{sid}:design-all")
    if job and job.get("running"):
        return job
    data = get_setlist(sid)
    redo = bool((body or {}).get("redo"))
    todo = [i for i, s in enumerate(data["songs"]) if (redo or not s.get("recipe")) and not s.get("uses_patch")
            and s.get("kind") != "patch"]
    default_desc = (body or {}).get("description") or ""
    job = {"running": True, "done": 0, "total": len(todo), "current": "", "errors": [], "result": None}
    _jobs[f"{sid}:design-all"] = job
    prov = _provider()
    settings = load_settings()
    gear_text = gearmod.describe_for_ai(gearmod.load_gear())
    ir_names = _ir_names()

    def work() -> None:
        try:
            for n, i in enumerate(todo, 1):
                cur = json.loads(_setlist_path(sid).read_text(encoding="utf-8"))
                if i >= len(cur["songs"]) or (cur["songs"][i].get("recipe") and not redo):
                    continue
                song = cur["songs"][i]
                job["current"] = f"{song.get('artist', '')} - {song.get('title', '')}"
                rq = song.get("request") or {}
                req = DesignRequest(
                    title=song.get("title", ""), artist=song.get("artist", ""),
                    description=rq.get("description") or default_desc or "il suono di chitarra originale del brano, pronto per il live",
                    guitar=rq.get("guitar", ""), snapshots=rq.get("snapshots", ""),
                    gear_preferences=rq.get("gear_preferences", ""), gear=gear_text,
                    audio_analysis=(song.get("audio") or {}).get("analysis", ""), irs=ir_names,
                )
                try:
                    res = design(req, prov, web_research=settings.web_research)
                    cur = json.loads(_setlist_path(sid).read_text(encoding="utf-8"))
                    tgt = next((x for x in cur["songs"] if x.get("id") == song.get("id")), None)
                    if tgt is not None:
                        if tgt.get("recipe"):
                            tgt["prev_recipe"] = tgt["recipe"]
                        tgt.update({"recipe": res.recipe.model_dump(), "research": res.research, "sources": res.sources,
                                    "warnings": res.warnings, "links": sources.search_links(req.artist, req.title)})
                        tgt.setdefault("chat", []).append({"who": "ai", "text": f"Preset creato in automatico: {len(res.recipe.chain)} blocchi, {len(res.recipe.snapshots)} snapshot."})
                        _setlist_path(sid).write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception as e:
                    job["errors"].append(f"{job['current']}: {e}")
                    print(f"[errore] {job['current']}: {e}", flush=True)
                job["done"] = n
        finally:
            job["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return job


@app.get("/api/setlists/{sid}/design-all")
def design_all_status(sid: str) -> dict:
    return _jobs.get(f"{sid}:design-all", {"running": False, "done": 0, "total": 0, "errors": []})


# ------------------------------------------------------------------ lavori lunghi in background

def _start_job(key: str, total: int, work) -> dict[str, Any]:
    job = _jobs.get(key)
    if job and job.get("running"):
        return job
    job = {"running": True, "done": 0, "total": total, "current": "", "errors": [], "result": None}
    _jobs[key] = job

    def run() -> None:
        try:
            job["result"] = work(job)
        except Exception as e:
            job["errors"].append(str(e))
            print(f"[errore] {e}", flush=True)
        finally:
            job["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return job


JOB_LABELS = {"youtube": "Ascolto dei brani da YouTube", "design-all": "Creazione dei preset",
              "generic": "Creazione delle patch generiche", "edit-many": "Modifica di tutti i preset"}


def jobs_summary(sid: str) -> list[dict]:
    out = []
    for kind, label in JOB_LABELS.items():
        j = _jobs.get(f"{sid}:{kind}")
        if j:
            out.append({"kind": kind, "label": label, "running": j.get("running", False), "done": j.get("done", 0),
                        "total": j.get("total", 0), "current": j.get("current", ""), "errors": len(j.get("errors", []))})
    return out


def jobs_text(sid: str) -> str:
    rows = []
    for j in jobs_summary(sid):
        state = "in corso" if j["running"] else "terminato"
        rows.append(f"- {j['label']}: {state}, {j['done']} di {j['total']}"
                    + (f", ora su {j['current']}" if j["running"] and j["current"] else "")
                    + (f", {j['errors']} problemi" if j["errors"] else ""))
    return "\n".join(rows)


@app.get("/api/setlists/{sid}/jobs")
def setlist_jobs(sid: str) -> list[dict]:
    return jobs_summary(sid)


@app.get("/api/jobs/{sid}/{kind}")
def job_status(sid: str, kind: str) -> dict:
    return _jobs.get(f"{sid}:{kind}", {"running": False, "done": 0, "total": 0, "errors": [], "result": None})


def _load_sl(sid: str) -> dict:
    return json.loads(_setlist_path(sid).read_text(encoding="utf-8"))


def _save_sl(data: dict) -> None:
    clean_links(data)
    _setlist_path(data["id"]).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ YouTube: analisi di tutta la scaletta

def start_youtube(sid: str) -> dict:
    data = get_setlist(sid)
    todo = [s["id"] for s in data["songs"] if s.get("kind") != "patch" and s.get("title")
            and not (s.get("audio") or {}).get("analysis")]
    prov = _provider()

    def work(job):
        for n, song_id in enumerate(todo, 1):
            cur = _load_sl(sid)
            song = next((x for x in cur["songs"] if x.get("id") == song_id), None)
            if song is None:
                continue
            job["current"] = f"{song.get('artist', '')} - {song.get('title', '')}"
            wdir = _dir("audio") / uuid.uuid4().hex[:10]
            wdir.mkdir()
            try:
                path, info = metadata.search_and_download(song.get("artist", ""), song.get("title", ""), wdir)
                res = _analyze_file(wdir, path, info, ["Audio cercato e scaricato da YouTube (uso personale)."],
                                    True, prov)
                cur = _load_sl(sid)
                tgt = next((x for x in cur["songs"] if x.get("id") == song_id), None)
                if tgt is not None:
                    tgt["audio"] = res
                    _save_sl(cur)
            except Exception as e:
                job["errors"].append(f"{job['current']}: {e}")
                print(f"[errore] {job['current']}: {e}", flush=True)
            job["done"] = n
        return {"analyzed": len(todo) - len(job["errors"])}

    return _start_job(f"{sid}:youtube", len(todo), work)


@app.post("/api/setlists/{sid}/youtube")
def api_youtube(sid: str) -> dict:
    try:
        return start_youtube(sid)
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ------------------------------------------------------------------ patch generiche

class GroupIn(BaseModel):
    max_patches: int = 4
    notes: str = ""
    use_iridium: bool = False


@app.post("/api/setlists/{sid}/group-proposal")
def api_group_proposal(sid: str, body: GroupIn) -> dict:
    data = get_setlist(sid)
    try:
        prop = planner.propose_groups(data["songs"], max(1, min(8, body.max_patches)), body.notes,
                                      gearmod.describe_for_ai(gearmod.load_gear()), _provider())
        prop["use_iridium"] = body.use_iridium
        return prop
    except AIError as e:
        _err(e)


class ReviseIn(BaseModel):
    proposal: dict
    history: list[dict]
    max_patches: int = 4


@app.post("/api/setlists/{sid}/group-revise")
def api_group_revise(sid: str, body: ReviseIn) -> dict:
    data = get_setlist(sid)
    try:
        return planner.revise_groups(data["songs"], body.proposal, body.history, max(1, min(8, body.max_patches)),
                                     gearmod.describe_for_ai(gearmod.load_gear()), _provider())
    except AIError as e:
        _err(e)


class GenericIn(BaseModel):
    proposal: dict


def start_generic(sid: str, proposal: dict) -> dict:
    groups = proposal.get("patches") or []
    if not groups:
        _err(ValueError("Nessuna patch da creare"))
    prov = _provider()
    gear_text = gearmod.describe_for_ai(gearmod.load_gear())
    snapshot_songs = get_setlist(sid)["songs"]
    ir_names = _ir_names()
    groups = [{**g, "song_ids": [snapshot_songs[r["index"]]["id"] for r in g["songs"] if 0 <= r["index"] < len(snapshot_songs)]}
              for g in groups]

    old_ids = {x.get("id") for x in snapshot_songs if x.get("kind") == "patch"}

    def work(job):
        made = []
        for n, g in enumerate(groups, 1):
            job["current"] = g["name"]
            try:
                req = planner.generic_request(g, snapshot_songs, gear_text, bool(proposal.get("use_iridium")))
                req.irs = ir_names
                res = design(req, prov, web_research=False)
                cur = _load_sl(sid)
                if old_ids:
                    # le nuove patch sostituiscono quelle vecchie, non si aggiungono
                    cur["songs"] = [x for x in cur["songs"] if x.get("id") not in old_ids]
                    for x in cur["songs"]:
                        if x.get("uses_patch") in old_ids:
                            for k in ("uses_patch", "uses_patch_name", "patch_usage"):
                                x.pop(k, None)
                        if x.get("suggested_patch") in old_ids:
                            for k in ("suggested_patch", "suggested_patch_name", "suggested_usage"):
                                x.pop(k, None)
                    old_ids.clear()
                pid = uuid.uuid4().hex[:8]
                by_id = {x.get("id"): x for x in cur["songs"]}
                covered = []
                for r, song_id in zip(g["songs"], g["song_ids"]):
                    song = by_id.get(song_id)
                    if song is None:
                        continue
                    # solo un suggerimento: l'assegnazione la decide il chitarrista
                    song.update({"suggested_patch": pid, "suggested_patch_name": g["name"],
                                 "suggested_usage": r.get("usage", "")})
                    covered.append(f"{song.get('artist', '')} - {song.get('title', '')}")
                entry = {"id": pid, "kind": "patch", "title": g["name"], "artist": "Patch generica",
                         "request": {"description": g.get("description", ""), "snapshots": g.get("snapshots", ""),
                                     "guitar": g.get("guitar", "")},
                         "covers": [], "suggested_for": covered, "summary": planner.summarize_patch(res.recipe, g, prov),
                         "audio": None, "recipe": res.recipe.model_dump(), "research": "",
                         "sources": [], "links": [], "warnings": res.warnings,
                         "chat": [{"who": "ai", "text": f"Patch generica pensata per {len(covered)} brani. Dimmi cosa ritoccare."}]}
                first_song = next((i for i, x in enumerate(cur["songs"]) if x.get("kind") != "patch"), len(cur["songs"]))
                cur["songs"].insert(first_song, entry)
                _save_sl(cur)
                made.append(g["name"])
            except Exception as e:
                job["errors"].append(f"{g['name']}: {e}")
                print(f"[errore] {g['name']}: {e}", flush=True)
            job["done"] = n
        return {"patches": made}

    return _start_job(f"{sid}:generic", len(groups), work)


def _targets(songs: list[dict], target: Any) -> list[int]:
    if isinstance(target, list):
        return [i for i in target if isinstance(i, int) and 0 <= i < len(songs) and songs[i].get("recipe")]
    if target == "patches":
        return [i for i, x in enumerate(songs) if x.get("kind") == "patch" and x.get("recipe")]
    if target == "songs":
        return [i for i, x in enumerate(songs) if x.get("kind") != "patch" and x.get("recipe")]
    return [i for i, x in enumerate(songs) if x.get("recipe")]


def start_edit_many(sid: str, instruction: str, target: Any = "all") -> dict:
    instruction = (instruction or "").strip()
    if not instruction:
        _err(ValueError("Scrivi la modifica da applicare"))
    songs = get_setlist(sid)["songs"]
    ids = [songs[i]["id"] for i in _targets(songs, target)]
    if not ids:
        _err(ValueError("Nessun preset da modificare: crea prima i preset"))
    prov, settings, ir_names = _provider(), load_settings(), _ir_names()

    def work(job):
        for n, song_id in enumerate(ids, 1):
            cur = _load_sl(sid)
            song = next((x for x in cur["songs"] if x.get("id") == song_id), None)
            if not song or not song.get("recipe"):
                job["done"] = n
                continue
            job["current"] = song.get("title") or song_id
            try:
                res = edit(ToneRecipe.model_validate(song["recipe"]), instruction, prov,
                           web_research=False, irs=ir_names)
                cur = _load_sl(sid)
                song = next((x for x in cur["songs"] if x.get("id") == song_id), None)
                if song is not None:
                    song["prev_recipe"] = song["recipe"]
                    song.update({"recipe": res.recipe.model_dump(), "warnings": res.warnings,
                                 "last_changes": res.changes})
                    song.setdefault("chat", []).append({"who": "ai", "text": f"Modifica su tutta la scaletta: {res.reply}",
                                                        "changes": res.changes})
                    _save_sl(cur)
            except Exception as e:
                job["errors"].append(f"{job['current']}: {e}")
                print(f"[errore] {job['current']}: {e}", flush=True)
            job["done"] = n
        return {}

    return _start_job(f"{sid}:edit-many", len(ids), work)


@app.post("/api/setlists/{sid}/edit-many")
def api_edit_many(sid: str, body: dict) -> dict:
    return start_edit_many(sid, str(body.get("instruction") or ""), body.get("target") or "all")


@app.get("/api/irs")
def list_irs() -> dict:
    names = _ir_names()
    return {"count": len(names), "names": names[:500], "folders": load_settings().ir_folders or []}


@app.get("/api/setlists/{sid}/overview")
def setlist_overview(sid: str) -> list[dict]:
    """Riepilogo di tutti i preset: ampli, cab/IR ed effetti, per controllare cosa usa ognuno."""
    out = []
    for i, x in enumerate(get_setlist(sid)["songs"]):
        r = x.get("recipe")
        row = {"index": i, "title": x.get("title", ""), "artist": x.get("artist", ""), "kind": x.get("kind", "song"),
               "uses_patch_name": x.get("uses_patch_name", ""), "suggested_patch_name": x.get("suggested_patch_name", ""),
               "has_recipe": bool(r), "blocks": []}
        if r:
            row["name"] = r.get("name", "")
            row["snapshots"] = [s.get("name", "") for s in r.get("snapshots", [])]
            for b in r.get("chain", []):
                row["blocks"].append({"role": b.get("role"), "name": b.get("gear_ref") or b.get("label") or b.get("id"),
                                      "ir": b.get("ir") or "", "line": b.get("line", 1),
                                      "helix": (b.get("helix") or {}).get("model") or "",
                                      "qc": (b.get("qc") or {}).get("model") or ""})
        out.append(row)
    return out


@app.post("/api/setlists/{sid}/helix-zip")
def setlist_helix_zip(sid: str):
    """Genera i file .hsp di tutti i preset della scaletta e li restituisce in un unico zip."""
    import io
    import zipfile
    data = get_setlist(sid)
    s = load_settings()
    irs = _ir_files()
    buf, done, problems = io.BytesIO(), 0, []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        n = 0
        for x in data["songs"]:
            if not x.get("recipe"):
                continue
            n += 1
            try:
                path, b = hx.generate_hsp(ToneRecipe.model_validate(x["recipe"]), s.helix_output_dir, irs)
                z.write(path, f"{n:02d} {path.name}")
                done += 1
                problems += [f"{x.get('title')}: {w}" for w in b.warnings]
            except Exception as e:
                problems.append(f"{x.get('title')}: {e}")
        if problems:
            z.writestr("LEGGIMI-avvisi.txt", "\n".join(problems))
    if not done:
        _err(ValueError("Nessun file creato. " + (problems[0] if problems else "Nessun preset nella scaletta")))
    name = re.sub(r"[^\w\- ]+", "", data.get("name", "scaletta")).strip() or "scaletta"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name} - Helix.zip"',
                             "X-Created": str(done), "X-Problems": str(len(problems))})


@app.post("/api/setlists/{sid}/assign")
def assign_patches(sid: str, body: dict) -> dict:
    """Assegna le patch ai brani. body: {"assignments": {song_id: patch_id | null}} oppure {"suggested": true}."""
    data = get_setlist(sid)
    patches = {x["id"]: x for x in data["songs"] if x.get("kind") == "patch" and x.get("recipe")}
    if body.get("suggested"):
        plan = {x["id"]: x.get("suggested_patch") for x in data["songs"]
                if x.get("kind") != "patch" and x.get("suggested_patch") in patches}
    else:
        plan = body.get("assignments") or {}
    for x in data["songs"]:
        if x.get("kind") == "patch" or x.get("id") not in plan:
            continue
        pid = plan[x["id"]]
        if pid and pid in patches:
            x.update({"uses_patch": pid, "uses_patch_name": patches[pid].get("title", ""),
                      "patch_usage": x.get("suggested_usage", "") if pid == x.get("suggested_patch") else ""})
        else:
            for k in ("uses_patch", "uses_patch_name", "patch_usage"):
                x.pop(k, None)
    _save_sl(data)
    return data


@app.post("/api/setlists/{sid}/generic-patches")
def api_generic(sid: str, body: GenericIn) -> dict:
    return start_generic(sid, body.proposal)


# ------------------------------------------------------------------ voce e dialogo

@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)) -> dict:
    s = load_settings()
    gem = s.providers.get("gemini")
    if not gem or not gem.api_key:
        _err(AIError("Per la voce su questo browser serve la chiave Gemini (Impostazioni > AI > Gemini). "
                     "In alternativa usa Chrome o Edge."))
    ext = Path(file.filename or "voce.webm").suffix or ".webm"
    wdir = _dir("voice")
    for old in sorted(wdir.glob("*"), key=lambda p: p.stat().st_mtime)[:-20]:
        old.unlink(missing_ok=True)
    path = wdir / f"{uuid.uuid4().hex[:8]}{ext}"
    path.write_bytes(await file.read())
    try:
        path = analysis.ensure_wav(path)
        text = get_provider(s, "gemini").complete_text("Sei un trascrittore accurato.", planner.transcribe_prompt(),
                                                        audio_path=str(path))
    except Exception as e:
        _err(e)
    return {"text": text.strip().strip('"')}


class DialogIn(BaseModel):
    setlist_id: str
    current: int | None = None
    history: list[dict]


@app.post("/api/dialog")
def api_dialog(body: DialogIn) -> dict:
    data = get_setlist(body.setlist_id)
    ctx = planner.setlist_context(data, body.current, gearmod.describe_for_ai(gearmod.load_gear()),
                                  jobs_text(body.setlist_id))
    try:
        return planner.converse(body.history, ctx, _provider(), data)
    except AIError as e:
        _err(e)


class ActionIn(BaseModel):
    setlist_id: str
    action: dict


@app.post("/api/dialog/execute")
def api_dialog_execute(body: ActionIn) -> dict:
    sid, a = body.setlist_id, body.action
    kind = a.get("type")
    data = get_setlist(sid)
    gear_text = gearmod.describe_for_ai(gearmod.load_gear())
    if kind in ("design_song", "edit_song"):
        try:
            idx = int(a.get("song_index"))
            song = data["songs"][idx]
        except (TypeError, ValueError, IndexError):
            _err(ValueError("Brano non trovato"))
        if kind == "edit_song" and not song.get("recipe") and song.get("uses_patch"):
            # il brano usa una patch generica: la modifica va sulla patch
            for j, other in enumerate(data["songs"]):
                if other.get("id") == song["uses_patch"] and other.get("recipe"):
                    idx, song = j, other
                    break
        try:
            if kind == "edit_song" and song.get("recipe"):
                res = edit(ToneRecipe.model_validate(song["recipe"]), str(a.get("instruction") or ""), _provider(),
                           web_research=load_settings().web_research, irs=_ir_names())
            else:
                rq = song.get("request") or {}
                for k in ("description", "snapshots", "guitar", "gear_preferences"):
                    if a.get(k):
                        rq[k] = str(a[k])
                song["request"] = rq
                req = DesignRequest(title=song.get("title", ""), artist=song.get("artist", ""),
                                    description=rq.get("description") or str(a.get("instruction") or ""),
                                    guitar=rq.get("guitar", ""), snapshots=rq.get("snapshots", ""),
                                    gear_preferences=rq.get("gear_preferences", ""), gear=gear_text,
                                    audio_analysis=(song.get("audio") or {}).get("analysis", ""), irs=_ir_names())
                res = design(req, _provider(), web_research=load_settings().web_research)
                song.update({"research": res.research, "sources": res.sources,
                             "links": sources.search_links(req.artist, req.title)})
        except AIError as e:
            _err(e)
        if song.get("recipe"):
            song["prev_recipe"] = song["recipe"]  # per "Annulla modifica"
        song["recipe"] = res.recipe.model_dump()
        song["warnings"] = res.warnings
        song["last_changes"] = res.changes
        _save_sl(data)
        return {"done": True, "song_index": idx, "reply": getattr(res, "reply", ""), "changes": res.changes}
    if kind == "analyze_youtube":
        return {"job": "youtube", **start_youtube(sid)}
    if kind == "design_all":
        return {"job": "design-all", **design_all(sid, {"description": a.get("description", ""),
                                                        "redo": bool(a.get("redo"))})}
    if kind == "edit_many":
        return {"job": "edit-many", **start_edit_many(sid, str(a.get("instruction") or ""), a.get("target") or "all")}
    if kind == "generic_patches":
        try:
            prop = planner.propose_groups(data["songs"], max(1, min(8, int(a.get("max_patches") or 4))),
                                          str(a.get("notes") or ""), gear_text, _provider())
        except AIError as e:
            _err(e)
        return {"job": "generic", "proposal": prop, **start_generic(sid, prop)}
    _err(ValueError(f"Azione sconosciuta: {kind}"))


# ------------------------------------------------------------------ audio

@app.post("/api/audio/analyze")
async def audio_analyze(file: UploadFile | None = File(default=None), url: str = Form(default=""),
                        separate: bool = Form(default=True), download: bool = Form(default=False)) -> dict:
    work = _dir("audio") / uuid.uuid4().hex[:10]
    work.mkdir()
    notes: list[str] = []
    audio_path: Path | None = None
    try:
        if file is not None and file.filename:
            audio_path = work / _safe_name(file.filename)
            audio_path.write_bytes(await file.read())
            info = metadata.from_file(audio_path)
        elif url:
            info = metadata.from_url(url)
            if download:
                audio_path = metadata.download_audio(url, work)
                notes.append("Audio scaricato per uso personale (termini di YouTube a tuo rischio).")
        else:
            raise ValueError("Carica un file audio o incolla un link")
    except Exception as e:
        _err(e)
    if audio_path is None:
        return {"track": info.to_dict(), "analysis": "", "features": {}, "notes": notes, "audio_id": None}
    return _analyze_file(work, audio_path, info, notes, separate)


def _analyze_file(work: Path, audio_path: Path, info, notes: list[str], separate: bool = True,
                  provider=None) -> dict[str, Any]:
    """Converte, (se possibile) isola la chitarra, misura e fa descrivere il suono all'AI."""
    result: dict[str, Any] = {"track": info.to_dict(), "analysis": "", "features": {}, "notes": notes,
                              "audio_id": work.name}
    try:
        audio_path = analysis.ensure_wav(audio_path)
    except Exception as e:
        notes.append(str(e))
    target = audio_path
    if separate:
        if analysis.demucs_available():
            try:
                target = analysis.separate_guitar(audio_path, work / "stems")
                notes.append("Chitarra isolata con Demucs.")
            except Exception as e:
                notes.append(f"Separazione non riuscita, uso il mix completo: {e}")
        else:
            notes.append(analysis.DEMUCS_HELP)
    (work / "target.txt").write_text(str(target), encoding="utf-8")
    ftxt = ""
    if _has("librosa"):
        try:
            result["features"] = analysis.features(target)
            ftxt = analysis.features_text(result["features"])
        except Exception as e:
            notes.append(f"Misure audio non riuscite: {e}")
    try:
        result["analysis"] = describe_audio(provider or _provider(), str(target), ftxt) or ftxt
    except (AIError, HTTPException) as e:
        notes.append(f"Analisi AI non riuscita: {getattr(e, 'detail', e)}")
        result["analysis"] = ftxt
    return result


# ------------------------------------------------------------------ progettazione e modifiche

class DesignIn(DesignRequest):
    audio_id: str | None = None


@app.post("/api/design")
def api_design(body: DesignIn) -> dict:
    s = load_settings()
    audio_path = None
    if body.audio_id and re.fullmatch(r"[a-f0-9]{10}", body.audio_id):
        t = _dir("audio") / body.audio_id / "target.txt"
        if t.exists():
            audio_path = t.read_text(encoding="utf-8")
    try:
        req = DesignRequest(**body.model_dump(exclude={"audio_id"}))
        req.irs = _ir_names()
        if not req.gear:
            req.gear = gearmod.describe_for_ai(gearmod.load_gear())
        res = design(req, _provider(),
                     web_research=s.web_research, audio_path=audio_path)
    except AIError as e:
        _err(e)
    out = res.to_dict()
    out["dsp"] = dsp.estimate(res.recipe).to_dict()
    out["links"] = sources.search_links(body.artist, body.title)
    return out


class EditIn(BaseModel):
    recipe: ToneRecipe
    instruction: str


@app.post("/api/edit")
def api_edit(body: EditIn) -> dict:
    try:
        res = edit(body.recipe, body.instruction, _provider(), web_research=load_settings().web_research,
                   irs=_ir_names())
    except AIError as e:
        _err(e)
    out = res.to_dict()
    out["dsp"] = dsp.estimate(res.recipe).to_dict()
    return out


@app.post("/api/validate")
def api_validate(recipe: dict) -> dict:
    try:
        r = ToneRecipe.model_validate(recipe)
    except Exception as e:
        _err(e)
    return {"recipe": r.model_dump(), "dsp": dsp.estimate(r).to_dict()}


@app.post("/api/import")
async def api_import(file: UploadFile = File(...)) -> dict:
    tmp = _dir("uploads") / f"{uuid.uuid4().hex[:8]}-{_safe_name(file.filename or 'preset')}"
    tmp.write_bytes(await file.read())
    try:
        r = import_file(tmp)
    except Exception as e:
        _err(e)
    return {"recipe": r.model_dump(), "dsp": dsp.estimate(r).to_dict()}


class PickIn(BaseModel):
    recipe: ToneRecipe
    snapshot: str | None = None
    name: str = ""


class MergeIn(BaseModel):
    picks: list[PickIn]
    name: str = "Merged"
    accept: list[str] | str | None = None


@app.post("/api/merge")
def api_merge(body: MergeIn) -> dict:
    try:
        res = merge([Pick(p.recipe, p.snapshot, p.name) for p in body.picks], body.name, body.accept)
    except Exception as e:
        _err(e)
    return res.to_dict()


# ------------------------------------------------------------------ dispositivi

class RecipeIn(BaseModel):
    recipe: ToneRecipe


@app.post("/api/helix/preview")
def helix_preview(body: RecipeIn) -> dict:
    try:
        b = hx.build_spec(body.recipe)
    except Exception as e:
        _err(e)
    return {"spec": b.spec, "warnings": b.warnings, "resolved": b.resolved}


@app.post("/api/helix/generate")
def helix_generate(body: RecipeIn) -> dict:
    s = load_settings()
    try:
        path, b = hx.generate_hsp(body.recipe, s.helix_output_dir, _ir_files())
    except Exception as e:
        _err(e)
    return {"file": path.name, "path": str(path), "download": f"/api/files/helix/{path.name}", "warnings": b.warnings}


@app.get("/api/files/helix/{name}")
def download_helix(name: str):
    p = Path(load_settings().helix_output_dir) / _safe_name(name)
    if not p.exists():
        _err(KeyError("file non trovato"), 404)
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


class QCIn(BaseModel):
    recipe: ToneRecipe
    slot: str
    confirm: bool = False


_qc_lock = threading.Lock()


@app.post("/api/qc/plan")
def qc_plan(body: QCIn) -> dict:
    try:
        return qcb.build_plan(body.recipe, body.slot).to_dict()
    except Exception as e:
        _err(e)


@app.post("/api/qc/execute")
def qc_execute(body: QCIn) -> dict:
    if not body.confirm:
        _err(ValueError("Conferma richiesta: lo slot indicato verra' sovrascritto"))
    if not _qc_lock.acquire(blocking=False):
        _err(RuntimeError("Un'altra scrittura sul QC e' in corso"), 409)
    try:
        plan = qcb.build_plan(body.recipe, body.slot)
        problems = qcb.execute_plan(plan)
        return {"problems": problems, "manual": plan.manual, "steps": len(plan.steps)}
    except Exception as e:
        _err(e)
    finally:
        _qc_lock.release()


@app.post("/api/qc/scene-bypass")
def qc_scene_bypass(body: QCIn) -> dict:
    if not body.confirm:
        _err(ValueError("Conferma richiesta"))
    try:
        plan = qcb.build_plan(body.recipe, body.slot)
        return {"problems": qcb.apply_scene_bypass(body.recipe, plan)}
    except Exception as e:
        _err(e)


# ------------------------------------------------------------------ risorse

@app.get("/api/sources/local")
def local_sources(q: str = "") -> dict:
    s = load_settings()
    irs = sources.scan(s.ir_folders, "ir")
    presets = sources.scan(s.preset_folders, "preset")
    if q:
        irs, presets = sources.rank(irs, q), sources.rank(presets, q)
    return {"irs": [r.to_dict() for r in irs[:50]], "presets": [r.to_dict() for r in presets[:50]]}


@app.post("/api/sources/import-local")
def import_local(body: dict) -> dict:
    s = load_settings()
    p = Path(body.get("path", "")).expanduser().resolve()
    allowed = [Path(f).expanduser().resolve() for f in s.preset_folders]
    if not any(p.is_relative_to(a) for a in allowed):
        _err(ValueError("Il file deve stare in una delle cartelle preset configurate"))
    try:
        r = import_file(p)
    except Exception as e:
        _err(e)
    return {"recipe": r.model_dump(), "dsp": dsp.estimate(r).to_dict()}


@app.get("/api/sources/links")
def links(artist: str = "", title: str = "") -> list[dict]:
    return sources.search_links(artist, title)


@app.get("/api/catalog/helix")
def helix_catalog(role: str | None = None) -> list[dict]:
    cat = HelixCatalog.load()
    from .recipe import Role

    ms = cat.by_role(Role(role)) if role else list(cat.models.values())
    return [{"id": m.model_id, "name": m.name, "category": m.category, "ready": m.in_library} for m in ms]


# ------------------------------------------------------------------ interfaccia

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return WEB.joinpath("index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="Tone Builder")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    global PORT
    PORT = a.port
    url = f"http://127.0.0.1:{a.port}"
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"Tone Builder su {url}  (Ctrl+C per chiudere)")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
