"""Backend Helix Stadium: ricetta -> file .hsp pronto da importare.

Usa helixgen (progetto open source non ufficiale) tramite i comandi della sua
CLI, che sono l'interfaccia stabile della libreria. I comandi girano dentro
questo stesso processo: cosi' funziona anche nell'eseguibile per Windows.

Prerequisito una tantum: helixgen deve conoscere il tuo Stadium. Esporta dalla
app Helix Stadium almeno un preset .hsp (meglio un'intera setlist) e importalo
dalla pagina Impostazioni (equivale a `helixgen ingest <cartella>`): il primo
file diventa il "telaio" dei preset generati.
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..catalogs.helix import HelixCatalog, HelixModel
from ..knobs import map_settings
from ..recipe import Block, Role, ToneRecipe

MAX_BLOCKS_PER_PATH = 12
HELIX_SWITCHES = [f"FS{i}" for i in range(1, 11)]
_QC_LETTERS = "ABCDEFGH"


class HelixError(RuntimeError):
    pass


@dataclass
class HelixBuild:
    spec: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)  # block_id -> model_id


@dataclass
class _Result:
    returncode: int
    stdout: str
    stderr: str


def _choose_model(b: Block, cat: HelixCatalog, warnings: list[str]) -> HelixModel | None:
    """Sceglie il modello Helix. Con la libreria popolata, helixgen sa generare
    solo i blocchi che compaiono nei tuoi export: se il modello scelto non c'e',
    usiamo il piu' simile tra quelli disponibili, o togliamo il blocco."""
    need_lib = cat.library_ready()
    wanted = cat.get(b.helix.model) if b.helix.model else None
    if b.helix.model and not wanted:
        warnings.append(f"{b.id}: modello Helix '{b.helix.model}' non esiste, cerco un equivalente")
    if wanted and (wanted.in_library or not need_lib):
        return wanted
    query = f"{b.gear_ref} {b.label} {wanted.name if wanted else ''} {b.helix.model or ''}"
    pool = cat.search(query, b.role, limit=50)
    if need_lib:
        pool = [m for m in pool if m.in_library] or [m for m in cat.by_role(b.role) if m.in_library]
    elif not pool:
        pool = cat.by_role(b.role)
    if not pool:
        missing = wanted.name if wanted else (b.gear_ref or b.role.value)
        warnings.append(
            f"{b.id}: tolto dal preset Helix: nei tuoi export non c'e' nessun blocco "
            f"{b.role.value} ('{missing}'). Esporta dallo Stadium un preset che lo contiene e ricaricalo."
        )
        return None
    m = pool[0]
    why = f"'{wanted.name}' non e' nei tuoi export" if wanted else f"per '{b.gear_ref or b.role.value}'"
    warnings.append(f"{b.id}: uso '{m.name}' ({why})")
    return m


def _native_params(b: Block, model: HelixModel, overrides: dict[str, float] | None,
                   warnings: list[str], where: str) -> dict[str, float]:
    params = model.visible_params()
    if model.library_params:  # helixgen accetta solo i parametri visti negli export
        params = {k: v for k, v in params.items() if k in model.library_params}
    settings = overrides if overrides is not None else b.settings
    out, missing = map_settings(settings, params)
    if missing:
        warnings.append(f"{where}: manopole senza equivalente su '{model.name}': {', '.join(missing)}")
    if overrides is None and b.helix.model == model.model_id:  # valori nativi solo se il modello e' quello
        allowed = model.library_params or set(model.params)
        for k, v in b.helix.params.items():
            if k in allowed:
                out[k] = v
            else:
                warnings.append(f"{where}: parametro nativo '{k}' sconosciuto su '{model.name}', ignorato")
    return out


def _switch_name(s: str) -> str:
    s = s.strip().upper()
    if s in _QC_LETTERS and len(s) == 1:
        return f"FS{_QC_LETTERS.index(s) + 1}"
    if re.fullmatch(r"\d+", s):
        return f"FS{s}"
    return s


def build_spec(recipe: ToneRecipe, cat: HelixCatalog | None = None) -> HelixBuild:
    """Traduce la ricetta nel formato recipe di helixgen.

    Linea 1 -> percorso 1 (path 0), linea 2 -> percorso 2 (path 1). Se la linea
    2 e' usata, l'uscita del percorso 1 entra nel percorso 2 (in serie).
    """
    cat = cat or HelixCatalog.load()
    warnings: list[str] = []
    chain = [b for b in recipe.chain if b.role != Role.looper]
    for ln in (1, 2):
        n = sum(1 for b in chain if b.line == ln)
        if n > MAX_BLOCKS_PER_PATH:
            raise HelixError(
                f"La linea {ln} ha {n} blocchi: su un percorso Stadium ne stanno "
                f"{MAX_BLOCKS_PER_PATH}. Sposta qualche blocco sull'altra linea."
            )

    chosen = {b.id: _choose_model(b, cat, warnings) for b in chain}
    models = {bid: m for bid, m in chosen.items() if m is not None}
    chain = [b for b in chain if b.id in models]
    if not any(m.role == Role.amp or m.role == Role.preamp for m in models.values()):
        warnings.append("Attenzione: nel preset Helix non c'e' nessun ampli. Importa dallo Stadium almeno un preset con l'ampli che ti serve.")
    ids = [m.model_id for m in models.values()]
    has_dupes = len(ids) != len(set(ids))

    lines = {ln: [b for b in chain if b.line == ln] for ln in (1, 2)}
    if not lines[1] and lines[2]:  # tutto sulla linea 2: lo riportiamo sulla 1
        lines = {1: lines[2], 2: []}
    use_two = bool(lines[2])
    where = {}  # block_id -> (path, pos)
    for ln, blist in lines.items():
        for i, b in enumerate(blist):
            where[b.id] = (ln - 1, i + 1)

    # Stato base: acceso se acceso in base o in almeno uno snapshot; gli
    # snapshot di helixgen sanno solo SPEGNERE rispetto alla base.
    base_on = {b.id: b.enabled or any(s.enabled.get(b.id) for s in recipe.snapshots) for b in chain}

    def ref(bid: str) -> dict[str, Any]:
        r: dict[str, Any] = {"block": models[bid].model_id}
        if has_dupes:
            path, pos = where[bid]
            r["path"], r["pos"] = path, pos
        return r

    paths = []
    for ln in (1, 2):
        if not lines[ln]:
            continue
        blocks = []
        for b in lines[ln]:
            m = models[b.id]
            entry: dict[str, Any] = {"block": m.model_id}
            params = _native_params(b, m, None, warnings, b.id)
            if params:
                entry["params"] = params
            if not base_on[b.id]:
                entry["enabled"] = False
            if has_dupes:
                entry["pos"] = where[b.id][1]
            if b.ir and b.role in (Role.cab, Role.ir):
                entry["ir"] = Path(b.ir).name
            blocks.append(entry)
        paths.append({"blocks": blocks})
    if use_two:
        paths[0]["output"] = {"to": "path2a"}  # linea 1 -> linea 2, in serie
        paths[1]["input"] = "none"  # la linea 2 riceve solo dalla linea 1
    last_path = len(paths) - 1

    spec: dict[str, Any] = {"name": recipe.name[:32], "paths": paths}
    if recipe.song.artist:
        spec["author"] = recipe.song.artist[:32]

    snaps = []
    for s in recipe.snapshots:
        sd: dict[str, Any] = {"name": s.name[:16]}
        disable = [ref(b.id) for b in chain if base_on[b.id] and not s.enabled.get(b.id, b.enabled)]
        if disable:
            sd["disable"] = disable
        plist = []
        for bid, knobs in s.overrides.items():
            if bid not in models:
                continue
            b = recipe.block(bid)
            native = _native_params(b, models[bid], knobs, warnings, f"{s.name}/{bid}")
            if native:
                plist.append({**ref(bid), "params": native})
        if plist:
            sd["params"] = plist
        if s.level_db is not None:
            sd["output"] = [{"path": last_path, "level": s.level_db}]
        snaps.append(sd)
    if snaps:
        spec["snapshots"] = snaps

    fs = []
    for f in recipe.footswitches:
        if f.block_id not in models:
            continue
        name = _switch_name(f.switch)
        if name not in HELIX_SWITCHES:
            warnings.append(f"footswitch '{f.switch}' non valido su Stadium, saltato")
            continue
        item = {"switch": name, **ref(f.block_id)}
        label = f.label or recipe.block(f.block_id).label
        if label:
            item["label"] = label[:16]
        fs.append(item)
    if fs:
        spec["footswitches"] = fs

    return HelixBuild(spec, warnings, {bid: m.model_id for bid, m in models.items()})


_RUN_LOCK = threading.Lock()  # CliRunner sostituisce stdout/stderr del processo: un comando alla volta


def _run(args: list[str]) -> _Result:
    """Esegue un comando helixgen nel processo corrente, catturandone l'output."""
    from click.testing import CliRunner
    from helixgen.cli import cli

    with _RUN_LOCK:
        res = CliRunner().invoke(cli, args, catch_exceptions=True)
    out = res.stdout if hasattr(res, "stdout") else res.output
    try:
        err = res.stderr or ""
    except ValueError:  # vecchie versioni di click: stderr mescolato a stdout
        err = ""
    if res.exception and not isinstance(res.exception, SystemExit):
        err = (err + "\n" + f"{type(res.exception).__name__}: {res.exception}").strip()
    return _Result(res.exit_code, out or "", err or (out if res.exit_code else ""))


def library_status() -> dict[str, Any]:
    cat = HelixCatalog.load()
    n = sum(1 for m in cat.models.values() if m.in_library)
    return {"ready": n > 0, "blocks_in_library": n}


def ingest(path: str | Path) -> str:
    """Importa nella libreria helixgen un .hsp o una cartella di export Stadium."""
    r = _run(["ingest", str(path)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip() or r.stdout.strip())
    HelixCatalog.reload()
    return r.stdout.strip()


def register_irs(wavs: list[str | Path]) -> str:
    r = _run(["register-irs", *map(str, wavs)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def generate_hsp(recipe: ToneRecipe, out_dir: str | Path) -> tuple[Path, HelixBuild]:
    """Genera il file .hsp. Se un IR non e' registrato, riprova senza e lo segnala."""
    if not library_status()["ready"]:
        raise HelixError(
            "La libreria helixgen e' vuota: importa prima almeno un tuo export .hsp "
            "dalle Impostazioni (serve come telaio dei preset)."
        )
    build = build_spec(recipe)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", recipe.name).strip().replace(" ", "_") or "preset"
    out = out_dir / f"{safe}.hsp"
    n = 2
    while out.exists():
        out = out_dir / f"{safe}_{n}.hsp"
        n += 1

    def attempt(spec: dict) -> _Result:
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "recipe.json"
            rp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            return _run(["generate", str(rp), "-o", str(out)])

    r = attempt(build.spec)
    if r.returncode != 0 and re.search(r"\bIR\b|irhash|impulse", r.stderr, re.I):
        for blk in build.spec["paths"][0]["blocks"]:
            if blk.pop("ir", None):
                build.warnings.append(
                    f"IR non registrato per '{blk['block']}': il cab resta senza IR, "
                    "registralo dalle Impostazioni e rigenera"
                )
        r = attempt(build.spec)
    if r.returncode != 0:
        raise HelixError((r.stderr or r.stdout).strip())
    return out, build


def read_hsp(path: str | Path) -> dict[str, Any]:
    """Legge un .hsp come recipe helixgen (per l'importazione)."""
    r = _run(["view", str(path)])
    if r.returncode != 0:
        raise HelixError(r.stderr.strip())
    return json.loads(r.stdout)


# ---------------------------------------------------------------- import dallo Stadium in rete

def _records(stdout: str) -> list[dict[str, Any]]:
    data = json.loads(stdout or "[]")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [r for r in data if isinstance(r, dict)]


def stadium_import(include_factory: bool = True, ip: str = "",
                   progress=None, out_root: str | Path | None = None) -> dict[str, Any]:
    """Legge i preset dallo Stadium in rete (sola lettura, non cambia il preset attivo),
    li converte in .hsp e li importa nella libreria helixgen.

    Richiede helixgen[device]. Lo Stadium deve essere acceso e sulla stessa rete.
    """
    from datetime import datetime

    from ..config import app_home

    def say(n: int, tot: int, text: str) -> None:
        if progress:
            progress(n, tot, text)

    ipargs = ["--ip", ip.strip()] if ip.strip() else []
    say(0, 0, "Cerco lo Stadium in rete…")
    if not ipargs:
        r = _run(["device", "info"])
        if r.returncode != 0:
            d = _run(["device", "discover"])
            if d.returncode != 0:
                raise HelixError(
                    "Helix Stadium non trovato in rete. Controlla che sia acceso e collegato alla stessa rete "
                    "del computer (Wi-Fi o cavo); in alternativa indica il suo indirizzo IP. "
                    + (d.stderr or d.stdout).strip()[-300:]
                )
    else:
        r = _run(["device", "info", *ipargs])
        if r.returncode != 0:
            raise HelixError(f"Lo Stadium all'indirizzo {ip} non risponde: {(r.stderr or r.stdout).strip()[-300:]}")

    sources = ["user"] + (["factory"] if include_factory else [])
    todo: list[tuple[str, dict]] = []
    for src in sources:
        say(0, 0, f"Leggo l'elenco dei preset ({'tuoi' if src == 'user' else 'di fabbrica'})…")
        r = _run(["device", "list", "--setlist", src, "--json", *ipargs])
        if r.returncode != 0:
            if src == "user":
                raise HelixError(f"Elenco preset non riuscito: {(r.stderr or r.stdout).strip()[-300:]}")
            continue
        todo += [(src, rec) for rec in _records(r.stdout) if rec.get("cid_") is not None]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(out_root) if out_root else app_home() / "stadium_import" / stamp
    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    for n, (src, rec) in enumerate(todo, 1):
        name = str(rec.get("name") or rec["cid_"])
        say(n, len(todo), f"{name}")
        safe = re.sub(r"[^\w\- ]+", "_", name).strip() or str(rec["cid_"])
        dest = out / src / f"{n:03d}_{safe}.hsp"
        dest.parent.mkdir(exist_ok=True)
        r = _run(["device", "to-hsp", str(rec["cid_"]), "-o", str(dest), "--no-verify", *ipargs])
        if r.returncode == 0 and dest.exists():
            ok += 1
        else:
            failed.append(f"{name}: {(r.stderr or r.stdout).strip().splitlines()[-1:] or ['errore']}")
    if not ok:
        raise HelixError("Nessun preset letto dallo Stadium. " + "; ".join(failed[:3]))

    say(len(todo), len(todo), "Importo nella libreria…")
    # i tuoi preset per primi: il primo file importato fa da telaio
    msg = ingest(out / "user") if (out / "user").exists() else ""
    if (out / "factory").exists():
        msg += "\n" + ingest(out / "factory")
    status = library_status()
    return {"converted": ok, "failed": failed, "folder": str(out), "message": msg.strip(), **status}
