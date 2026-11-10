import json

import pytest
from helixgen.spec import parse_spec

from tonebuilder.ai.base import parse_json
from tonebuilder.backends import helix_stadium as hx
from tonebuilder.backends import quad_cortex as qcb
from tonebuilder.catalogs.helix import HelixCatalog, humanize
from tonebuilder.catalogs.quadcortex import QCCatalog
from tonebuilder.dsp import estimate
from tonebuilder.importers import from_hlx
from tonebuilder.knobs import ParamDef, find_param, to_native, to_neutral
from tonebuilder.merge import Pick, amp_family, merge
from tonebuilder.recipe import Block, Role, Snapshot, ToneRecipe


def test_recipe_rejects_bad_refs(recipe):
    data = recipe.model_dump()
    data["snapshots"][0]["enabled"] = {"nope": True}
    with pytest.raises(Exception):
        ToneRecipe.model_validate(data)


def test_knob_mapping():
    params = {"Drive": ParamDef("Drive", 0, 1), "Time": ParamDef("Time", 0, 8), "Level": ParamDef("Level", -60, 6)}
    assert find_param("gain", params).name == "Drive"
    assert to_native("gain", 5, params["Drive"]) == 0.5
    assert to_native("time_ms", 380, params["Time"]) == 0.38
    assert to_native("level_db", 20, params["Level"]) == 6  # clamp
    assert to_neutral("gain", 0.7, params["Drive"]) == 7.0


def test_helix_catalog_loads():
    cat = HelixCatalog.load()
    assert cat.get("Agoura_AmpBritPlexi").role == Role.amp
    assert len(cat.by_role(Role.delay)) > 20
    assert humanize("Agoura_AmpBritPlexi") == "Brit Plexi"
    assert "Agoura_AmpBrit2203MV" in cat.llm_listing()


def test_helix_spec_is_valid_for_helixgen(recipe):
    b = hx.build_spec(recipe)
    spec = parse_spec(b.spec)  # la validazione strutturale di helixgen non deve fallire
    assert len(spec.paths[0].blocks) == 5
    assert [s.name for s in spec.snapshots] == ["Strofa", "Ritornello", "Assolo"]
    blocks = b.spec["paths"][0]["blocks"]
    amp = next(x for x in blocks if x["block"] == "Agoura_AmpBrit2203MV")
    assert amp["params"]["Drive"] == 0.6
    # drive e delay accesi solo nell'assolo: base accesa, spenti negli altri snapshot
    strofa = b.spec["snapshots"][0]
    disabled = {d["block"] for d in strofa["disable"]}
    assert "HD2_DistScream808Mono" in disabled
    assert b.spec["snapshots"][2].get("output") == [{"path": 0, "level": 2}]
    assert b.spec["footswitches"][0]["switch"] == "FS3"


def test_helix_spec_duplicate_models_get_positions(recipe):
    recipe.chain.append(Block(id="rev2", role=Role.reverb, helix={"model": recipe.chain[4].helix.model or ""}))
    b = hx.build_spec(recipe)
    models = [x["block"] for x in b.spec["paths"][0]["blocks"]]
    if len(models) != len(set(models)):
        assert all("pos" in x for x in b.spec["paths"][0]["blocks"])
    parse_spec(b.spec)


class FakeQC:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return [] if name == "list_irs" else None
        return rec


def test_qc_plan_and_execute(recipe):
    plan = qcb.build_plan(recipe, "30A", QCCatalog.load())
    actions = [s.action for s in plan.steps]
    assert actions[0] == "clear_grid" and actions[-1] == "save"
    assert actions.count("set_block") == 5
    assert actions.count("scene_label") == 3
    fake = FakeQC()
    problems = qcb.execute_plan(plan, client=fake)
    names = [c[0] for c in fake.calls]
    assert names.count("set_block") == 5
    assert names[-1] == "save_current_preset"
    assert not [p for p in problems if "budget" in p]


def test_dsp_estimate(recipe):
    e = estimate(recipe)
    assert 0 < e.helix < 100 and 0 < e.qc < 100


def _patch(name, amp_ref, amp_model, gain, extra=()):
    return ToneRecipe(name=name, chain=[
        Block(id="amp1", role=Role.amp, gear_ref=amp_ref, settings={"gain": gain}, helix={"model": amp_model}),
        Block(id="cab1", role=Role.cab, gear_ref=amp_ref + " cab"),
        *extra,
    ])


def test_merge_same_family_amps_are_synthesised():
    a = _patch("A", "Marshall Plexi", "Agoura_AmpBritPlexi", 3)
    b = _patch("B", "Marshall JCM800", "Agoura_AmpBrit2203MV", 7)
    r = merge([Pick(a, None, "Pulito"), Pick(b, None, "Crunch")], "Unito")
    amps = [x for x in r.recipe.chain if x.role == Role.amp]
    assert len(amps) == 1
    assert r.recipe.snapshots[1].overrides[amps[0].id]["gain"] == 7
    assert r.syntheses


def test_merge_different_families_switch_by_snapshot():
    a = _patch("A", "Fender Twin", "Agoura_AmpUSDoubleBlack", 3)
    b = _patch("B", "Mesa Rectifier", "Agoura_AmpCali2CPlus", 8)
    r = merge([Pick(a, None, "Clean"), Pick(b, None, "Lead")])
    amps = [x for x in r.recipe.chain if x.role == Role.amp]
    assert len(amps) == 2
    assert r.recipe.snapshots[1].enabled[amps[0].id] is False
    assert r.recipe.snapshots[1].enabled[amps[1].id] is True


def test_merge_proposes_compromises_when_over_budget():
    fx = lambda i: [Block(id=f"p{i}", role=Role.pitch, gear_ref=f"Whammy {i}"),
                    Block(id=f"r{i}", role=Role.reverb, gear_ref=f"Hall {i}")]
    picks = [Pick(_patch("A", "Fender Twin", "Agoura_AmpUSDoubleBlack", 3, fx(1)), None, "A"),
             Pick(_patch("B", "Marshall Plexi", "Agoura_AmpBritPlexi", 6, fx(2)), None, "B"),
             Pick(_patch("C", "Mesa Rectifier", "Agoura_AmpCali2CPlus", 8, fx(3)), None, "C")]
    r = merge(picks)
    assert r.over_budget and r.proposed
    assert r.proposed[0].id == "share_reverb"
    r2 = merge(picks, accept="all")
    assert r2.applied and len(r2.recipe.chain) < len(r.recipe.chain)


def test_amp_family():
    assert amp_family(Block(id="a", role=Role.amp, gear_ref="Marshall JCM800")) == "marshall"
    assert amp_family(Block(id="a", role=Role.amp, helix={"model": "Agoura_AmpUSDoubleBlack"})) == "fender"


def test_import_hlx():
    hlx = {"data": {"meta": {"name": "Sweet Child"}, "tone": {
        "dsp0": {
            "block0": {"@model": "HD2_DistScream808Mono", "@enabled": False, "@path": 0, "@position": 1, "Drive": 0.4},
            "block1": {"@model": "Agoura_AmpBritPlexi", "@enabled": True, "@path": 0, "@position": 2,
                       "@cab": "cab0", "Bass": 0.5},
            "cab0": {"@model": "HD2_CabMicIr_4x12Greenback25WithPan"},
        },
        "snapshot0": {"@name": "Intro", "@valid": True, "blocks": {"dsp0": {"block0": False}}},
        "snapshot1": {"@name": "Solo", "@valid": True, "blocks": {"dsp0": {"block0": True}},
                      "controllers": {"dsp0": {"block1": {"Bass": {"@value": 0.8}}}}},
        "footswitch": {"dsp0": {"block0": {"@fs_index": 2, "@fs_label": "TS"}}},
    }}}
    r = from_hlx(hlx)
    assert r.name == "Sweet Child"
    assert [b.role for b in r.chain] == [Role.drive, Role.amp, Role.cab]
    assert r.snapshots[1].enabled["drive1"] is True
    assert r.snapshots[1].overrides["amp1"]["bass"] == 8.0
    assert r.footswitches[0].switch == "FS3"
    hx.build_spec(r)  # deve tornare indietro verso Helix senza errori


def test_parse_json_tolerates_fences():
    assert parse_json('Ecco:\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
