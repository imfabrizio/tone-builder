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
