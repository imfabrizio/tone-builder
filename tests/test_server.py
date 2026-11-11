import json

from fastapi.testclient import TestClient

import tonebuilder.server as srv
from tonebuilder.ai.base import LLMProvider, ResearchResult


class StubAI(LLMProvider):
    name = "stub"
    supports_web = True
    supports_audio = False

    def __init__(self, recipe_json):
        self.api_key, self.model, self.base_url = "x", "stub", ""
        self.recipe_json = recipe_json
        self.calls = 0

    def complete_text(self, system, user, *, audio_path=None):
        self.calls += 1
        if self.calls == 1:  # prima risposta volutamente rotta: deve scattare la correzione
            return '{"name": "x", "chain": [{"id": "a"}]}'
        return json.dumps(self.recipe_json)

    def research(self, question):
        return ResearchResult("Usava un JCM800.", [{"title": "Fonte", "url": "https://example.com"}])


def test_full_flow(recipe, monkeypatch):
    stub = StubAI(recipe.model_dump())
    monkeypatch.setattr(srv, "_provider", lambda name=None: stub)
    c = TestClient(srv.app)

    assert c.get("/").status_code == 200
    st = c.get("/api/status").json()
    assert "helix" in st and "qc" in st

    r = c.post("/api/design", json={"title": "Song", "artist": "Band", "description": "crunch"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["recipe"]["name"] == "Test Song"
    assert d["sources"][0]["url"] == "https://example.com"
    assert stub.calls == 2  # una correzione

    sl = c.post("/api/setlists", json={"name": "Capodanno"}).json()
    sl["songs"] = [{"id": "1", "title": "Song", "recipe": d["recipe"]}]
    assert c.put(f"/api/setlists/{sl['id']}", json=sl).status_code == 200
    assert c.get("/api/setlists").json()[0]["songs"] == 1

    p = c.post("/api/helix/preview", json={"recipe": d["recipe"]}).json()
    assert p["spec"]["paths"][0]["blocks"]

    plan = c.post("/api/qc/plan", json={"recipe": d["recipe"], "slot": "30A"}).json()
    assert plan["steps"][-1]["action"] == "save"
    assert c.post("/api/qc/execute", json={"recipe": d["recipe"], "slot": "30A"}).status_code == 400  # serve conferma

    m = c.post("/api/merge", json={"picks": [
        {"recipe": d["recipe"], "snapshot": "Strofa", "name": "Strofa"},
        {"recipe": d["recipe"], "snapshot": "Assolo", "name": "Lead"},
    ], "name": "Unito"}).json()
    assert len(m["recipe"]["snapshots"]) == 2

    g = c.post("/api/helix/generate", json={"recipe": d["recipe"]})
    assert g.status_code in (200, 400)  # 400 atteso se la libreria helixgen e' vuota
    if g.status_code == 400:
        assert "libreria" in g.json()["detail"].lower()


def test_settings_never_leak_keys(monkeypatch):
    c = TestClient(srv.app)
    c.put("/api/settings", json={"providers": {"claude": {"api_key": "sk-ant-secret-1234"}}})
    st = c.get("/api/status").json()
    p = st["settings"]["providers"]["claude"]
    assert p["api_key"] == "" and p["has_key"] and p["key_hint"] == "…1234"
    assert "secret" not in json.dumps(st)


def test_gear_import_and_setlist_automation(recipe, monkeypatch):
    import time

    class SetlistAI(StubAI):
        def complete_json(self, system, user, *, audio_path=None):
            if "tecnico di palco" in system:
                return {"songs": [{"index": 0, "guitar": "Fender Telecaster", "pickups": "ponte", "tuning": "standard",
                                   "original": "Gibson", "reason": "suono asciutto"}]}
            return recipe.model_dump()

    stub = SetlistAI(recipe.model_dump())
    monkeypatch.setattr(srv, "_provider", lambda name=None: stub)
    c = TestClient(srv.app)

    g = c.get("/api/gear").json()
    assert "configs" in g
    body = {"guitars": [{"name": "Fender Telecaster", "config": "SS", "pickups": {"ponte": "Fender Broadcaster (Tele)"}}],
            "devices": ["Helix Stadium"], "other": ""}
    assert c.put("/api/gear", json=body).status_code == 200
    assert c.get("/api/gear/search?kind=pickup&q=JB").json()[0]["name"].startswith("Seymour Duncan JB")

    sl = c.post("/api/setlists/import", json={"text": "The Cranberries - Zombie\nAlanis Morissette - Ironic", "name": "90s"}).json()
    assert [s["title"] for s in sl["songs"]] == ["Zombie", "Ironic"]

    sl = c.post(f"/api/setlists/{sl['id']}/suggest-guitars").json()
    assert sl["songs"][0]["request"]["guitar"].startswith("Fender Telecaster")
    assert "asciutto" in sl["songs"][0]["guitar_reason"]

    c.post(f"/api/setlists/{sl['id']}/design-all", json={})
    for _ in range(50):
        st = c.get(f"/api/setlists/{sl['id']}/design-all").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["done"] == 2 and not st["errors"]
    final = c.get(f"/api/setlists/{sl['id']}").json()
    assert all(s["recipe"] for s in final["songs"])


def test_generic_patches_dialog_and_youtube(recipe, monkeypatch, tmp_path):
    import time
    from tonebuilder.audio import metadata

    class PlanAI(StubAI):
        supports_audio = True

        def complete_json(self, system, user, *, audio_path=None):
            if "PATCH GENERICHE" in system:
                return {"patches": [{"name": "Crunch 90", "description": "rock", "snapshots": "Pulito, Crunch, Lead",
                                     "songs": [{"index": 0, "usage": "strofa Crunch"}, {"index": 1, "usage": "assolo Lead"}]}],
                        "notes": "simili"}
            if "tecnico del suono di un chitarrista e parli" in system:
                ok = "CHITARRISTA: ok procedi" in user.splitlines()[-2] if len(user.splitlines()) > 1 else False
                return {"reply": "Faccio due patch?", "action": {"type": "generic_patches", "max_patches": 2}}
            return recipe.model_dump()

        def complete_text(self, system, user, *, audio_path=None):
            return "suono crunch con chorus"

    stub = PlanAI(recipe.model_dump())
    monkeypatch.setattr(srv, "_provider", lambda name=None: stub)
    c = TestClient(srv.app)
    sl = c.post("/api/setlists/import", json={"text": "A - Uno\nB - Due\nC - Tre", "name": "t"}).json()
    sid = sl["id"]

    # il dialogo non esegue azioni senza un ok esplicito
    r = c.post("/api/dialog", json={"setlist_id": sid, "history": [{"who": "me", "text": "riduciamo le patch"}]}).json()
    assert r["action"] is None and r["reply"]
    r = c.post("/api/dialog", json={"setlist_id": sid, "history": [{"who": "me", "text": "ok procedi"}]}).json()
    assert r["action"]["type"] == "generic_patches"

    prop = c.post(f"/api/setlists/{sid}/group-proposal", json={"max_patches": 2}).json()
    assert sorted(x["index"] for x in prop["patches"][0]["songs"]) == [0, 1, 2]  # brano dimenticato riassegnato
    c.post(f"/api/setlists/{sid}/generic-patches", json={"proposal": prop})
    for _ in range(50):
        st = c.get(f"/api/jobs/{sid}/generic").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert not st["errors"], st
    data = c.get(f"/api/setlists/{sid}").json()
    assert data["songs"][0]["kind"] == "patch" and data["songs"][0]["recipe"]
    assert all(s.get("uses_patch") == data["songs"][0]["id"] for s in data["songs"][1:])
    assert data["songs"][1]["patch_usage"] == "strofa Crunch"

    # YouTube: download simulato
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(metadata, "search_and_download",
                        lambda a, t, d: (wav, metadata.TrackInfo(title=t, artist=a)))
    monkeypatch.setattr(srv.analysis, "demucs_available", lambda: False)
    monkeypatch.setattr(srv, "_has", lambda m: False)
    c.post(f"/api/setlists/{sid}/youtube")
    for _ in range(50):
        st = c.get(f"/api/jobs/{sid}/youtube").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["done"] == 3 and not st["errors"], st
    data = c.get(f"/api/setlists/{sid}").json()
    assert data["songs"][1]["audio"]["analysis"] == "suono crunch con chorus"


def test_group_revise_dialog(monkeypatch):
    class ReviseAI(StubAI):
        def complete_json(self, system, user, *, audio_path=None):
            return {"reply": "Spostata.", "go": True,
                    "proposal": {"patches": [{"name": "Pulita", "songs": [{"index": 0}]},
                                             {"name": "Distorta", "songs": [{"index": 1}]}]}}

    monkeypatch.setattr(srv, "_provider", lambda name=None: ReviseAI({}))
    c = TestClient(srv.app)
    sl = c.post("/api/setlists/import", json={"text": "A - Uno\nB - Due", "name": "r"}).json()
    prop = {"patches": [{"name": "Unica", "songs": [{"index": 0}, {"index": 1}]}]}
    r = c.post(f"/api/setlists/{sl['id']}/group-revise",
               json={"proposal": prop, "max_patches": 1, "history": [{"who": "me", "text": "dividile in due"}]}).json()
    assert len(r["proposal"]["patches"]) == 2  # ha chiesto di cambiare il numero
    assert r["go"] is False  # nessun ok esplicito
    r = c.post(f"/api/setlists/{sl['id']}/group-revise",
               json={"proposal": prop, "max_patches": 2, "history": [{"who": "me", "text": "ok creale"}]}).json()
    assert r["go"] is True


def test_gear_export_import_and_iridium_guide(recipe):
    import io
    c = TestClient(srv.app)
    data = {"guitars": [{"name": "Music Man JP6", "config": "HH + Piezo", "use": "acustici e pezzi duri"}],
            "wah_external": True, "iridium": True, "iridium_midi_channel": 3, "iridium_first_preset": 40}
    r = c.post("/api/gear/import", files={"file": ("g.json", io.BytesIO(json.dumps(data).encode()), "application/json")},
               data={"merge": "true"})
    assert r.status_code == 200 and r.json()["iridium_midi_channel"] == 3
    assert "acustici" in c.get("/api/gear/export").text
    rec = recipe.model_dump()
    rec["iridium"] = {"enabled": True, "midi_channel": 3, "first_preset": 40,
                      "snapshots": [{"snapshot": s["name"], "amp": "Chime"} for s in rec["snapshots"]]}
    g = c.post("/api/iridium/guide", json={"recipe": rec}).json()
    assert g["enabled"] and g["rows"][0]["preset"] == 40 and "Vox" in g["rows"][0]["amp"]
