"""Guida passo passo per usare lo Strymon Iridium nel loop effetti dello Stadium.

Fonti: Strymon, "How do I control Iridium with MIDI?" (canale 1 di default, 300
preset, Program Change sul jack EXP in modalita' DIGITAL con cavo MIDI EXP o
Conduit). Le impostazioni del loop vanno verificate a orecchio: dipendono dal
livello che vuoi in uscita.
"""
from __future__ import annotations

from .recipe import ToneRecipe

AMP_DESC = {"Round": "Fender Deluxe Reverb", "Chime": "Vox AC30", "Punch": "Marshall Plexi"}


def guide(recipe: ToneRecipe) -> dict:
    plan = recipe.iridium
    if not plan or not plan.enabled:
        return {"enabled": False}
    rows = []
    for i, s in enumerate(recipe.snapshots):
        ir = next((x for x in plan.snapshots if x.snapshot == s.name), None)
        if ir is None and i < len(plan.snapshots):
            ir = plan.snapshots[i]
        rows.append({
            "snapshot": s.name, "preset": plan.first_preset + i,
            "amp": f"{ir.amp} ({AMP_DESC.get(ir.amp, '')})" if ir else "-",
            "cab": ir.cab if ir else "-",
            "knobs": (f"Drive {ir.drive:g} · Bass {ir.bass:g} · Middle {ir.middle:g} · Treble {ir.treble:g} · "
                      f"Level {ir.level:g} · Room {ir.room:g}") if ir else "",
            "notes": ir.notes if ir else "",
        })
    ch = plan.midi_channel
    steps = [
        "Collegamenti audio: Send 1 dello Stadium all'ingresso dell'Iridium; uscite L e R dell'Iridium in Return 1 e "
        "Return 2 dello Stadium (con una sola uscita usa Return 1 e il loop mono).",
        "Livelli: nelle impostazioni globali dello Stadium imposta il Send a livello strumento e i Return a livello linea; "
        "sull'Iridium tieni Level intorno a ore 12 e pareggia poi a orecchio con un preset dello Stadium.",
        "Collegamento MIDI: dall'uscita MIDI dello Stadium al jack EXP dell'Iridium, con il cavo Strymon MIDI EXP "
        "(o con un Conduit). Sull'Iridium imposta il jack EXP in modalita' DIGITAL.",
        f"Canale MIDI: l'Iridium riceve sul canale 1 di fabbrica. Il preset usa il canale {ch}"
        + ("." if ch == 1 else ": per cambiarlo accendi l'Iridium tenendo premuti i due footswitch, gira DRIVE fino "
           "al canale giusto e conferma con ON."),
        f"Preset dell'Iridium: salva un preset per ogni snapshot ai numeri indicati nella tabella "
        f"({plan.first_preset}-{plan.first_preset + len(rows) - 1}), con le impostazioni scritte. Puoi farlo dal pedale "
        "o con il software Strymon Nixie.",
        "Sullo Stadium gli snapshot sono sui footswitch FS1-FS5 e FS7-FS9: ognuno cambia snapshot e richiama il preset "
        "dell'Iridium. I footswitch dei singoli blocchi sono su FS10 e FS11.",
        "Prova: passa da uno snapshot all'altro e controlla che il LED dell'Iridium cambi preset; se non succede, "
        "verifica canale MIDI e modalita' DIGITAL del jack EXP.",
    ]
    return {"enabled": True, "channel": ch, "rows": rows, "steps": steps}
