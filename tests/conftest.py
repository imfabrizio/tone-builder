import os
import tempfile

import pytest

# isolamento: impostazioni e cache in una cartella temporanea
os.environ.setdefault("TONEBUILDER_HOME", tempfile.mkdtemp(prefix="tb-test-"))

from tonebuilder.recipe import Block, DeviceModel, Footswitch, Role, Snapshot, ToneRecipe  # noqa: E402


@pytest.fixture
def recipe() -> ToneRecipe:
    return ToneRecipe(
        name="Test Song",
        chain=[
            Block(id="drive1", role=Role.drive, gear_ref="Ibanez TS808", label="TS",
                  enabled=False, settings={"drive": 3, "tone": 6, "level": 7},
                  helix=DeviceModel(model="HD2_DistScream808Mono")),
            Block(id="amp1", role=Role.amp, gear_ref="Marshall JCM800 2203", label="2203",
                  settings={"gain": 6, "bass": 4, "mid": 7, "treble": 6, "presence": 5},
                  helix=DeviceModel(model="Agoura_AmpBrit2203MV")),
            Block(id="cab1", role=Role.cab, gear_ref="Marshall 4x12 Greenback"),
            Block(id="dly1", role=Role.delay, gear_ref="Boss DM-2", label="DLY", enabled=False,
                  settings={"time_ms": 380, "feedback": 3, "mix": 3}),
            Block(id="rev1", role=Role.reverb, gear_ref="Plate", settings={"mix": 2, "decay": 4}),
        ],
        snapshots=[
            Snapshot(name="Strofa", overrides={"amp1": {"gain": 4}}),
            Snapshot(name="Ritornello"),
            Snapshot(name="Assolo", enabled={"drive1": True, "dly1": True}, level_db=2),
        ],
        footswitches=[Footswitch(switch="FS3", block_id="drive1")],
    )
