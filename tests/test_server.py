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
