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
from fastapi.responses import FileResponse, HTMLResponse
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
from .designer import DesignRequest, describe_audio, design, edit
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
    for k in ("web_research", "ir_folders", "preset_folders", "helix_output_dir", "qc_target_setlist_slot", "demucs_command"):
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


@app.get("/api/setlists/{sid}")
def get_setlist(sid: str) -> dict:
    p = _setlist_path(sid)
    if not p.exists():
        _err(KeyError("scaletta non trovata"), 404)
    return json.loads(p.read_text(encoding="utf-8"))


@app.put("/api/setlists/{sid}")
def put_setlist(sid: str, body: dict) -> dict:
    for song in body.get("songs", []):
        if song.get("recipe"):
            ToneRecipe.model_validate(song["recipe"])  # niente ricette rotte su disco
    body["id"] = sid
    _setlist_path(sid).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return body


@app.delete("/api/setlists/{sid}")
def delete_setlist(sid: str) -> dict:
    _setlist_path(sid).unlink(missing_ok=True)
    return {"ok": True}


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

    result: dict[str, Any] = {"track": info.to_dict(), "analysis": "", "features": {}, "notes": notes,
                              "audio_id": work.name if audio_path else None}
    if audio_path is None:
        return result
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
        result["analysis"] = describe_audio(_provider(), str(target), ftxt) or ftxt
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
        res = design(DesignRequest(**body.model_dump(exclude={"audio_id"})), _provider(),
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
        res = edit(body.recipe, body.instruction, _provider())
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
        path, b = hx.generate_hsp(body.recipe, s.helix_output_dir)
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
    url = f"http://127.0.0.1:{a.port}"
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"Tone Builder su {url}  (Ctrl+C per chiudere)")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
