# Tone Builder

Dalla canzone al preset: descrivi il suono (o carica la traccia), e Tone Builder prepara la patch per **Line 6 Helix Stadium** (file `.hsp` da importare) e **Neural DSP Quad Cortex** (scrittura diretta via USB), con blocchi, snapshot e footswitch già impostati. Al chitarrista restano i ritocchi finali.

## Cosa fa

- **Progetta da canzone e descrizione**: titolo, artista, chitarra che userai, sezioni del brano (intro, strofa, assolo…). L'AI cerca sul web la strumentazione originale e le risorse **gratuite** (preset su CustomTone e simili, IR), legge cosa ne dicono gli utenti e scrive la ricetta del suono.
- **Analizza la traccia**: carica un MP3 o incolla un link. Titolo e autore vengono letti dai metadati; con Demucs la chitarra viene isolata; Gemini ascolta la traccia e descrive gain, EQ ed effetti per ogni sezione.
- **Modifiche a parole**: "più gain nell'assolo", "metti il delay sul FS4", "il mix del riverbero cambia per snapshot".
- **Editor visivo**: catena dei blocchi come una pedaliera, matrice blocchi × snapshot per accendere/spegnere e impostare valori per snapshot.
- **Merge intelligente**: prendi il pulito da un preset, il crunch da un altro, il lead da un terzo. Ampli della stessa famiglia diventano un solo ampli con impostazioni diverse per snapshot; se il DSP non basta, ti propone compromessi da confermare.
- **Scaletta**: tutti i brani di una serata in un progetto.
- **AI a scelta**: Gemini, Claude, oppure qualsiasi servizio compatibile OpenAI (anche modelli locali con Ollama / LM Studio).

## Windows: eseguibile pronto

Scarica **ToneBuilder.exe** dalla sezione [Releases](https://github.com/imfabrizio/tone-builder/releases/tag/latest) e avvialo con doppio clic: si apre una finestra nera (il motore dell'app, lasciala aperta) e il browser con l'interfaccia. Per chiudere l'app chiudi quella finestra.

- Non serve installare Python.
- Al primo avvio Windows SmartScreen può mostrare "Windows ha protetto il PC", perché l'eseguibile non è firmato: *Ulteriori informazioni > Esegui comunque*.
- L'eseguibile si aggiorna da solo a ogni modifica del codice: riscarica il file per avere l'ultima versione.
- Non include la separazione della chitarra (Demucs, troppo pesante) né le misure audio di librosa: l'analisi della traccia la fa l'AI ascoltando il mix completo (con Gemini).

## Installazione da sorgente (macOS, oppure Windows con Python)

Serve Python 3.11 o successivo.

```bash
git clone https://github.com/imfabrizio/tone-builder.git
cd tone-builder
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS:    source .venv/bin/activate
python -m pip install -e ".[all]"
```

Facoltativi, per l'analisi audio:

```bash
python -m pip install -e ".[audio]"   # yt-dlp, demucs, librosa (Demucs scarica il modello al primo uso)
```

Avvio:

```bash
python -m tonebuilder
```

Si apre il browser su `http://127.0.0.1:8765`. L'app ascolta solo sul tuo computer. Su Windows puoi anche fare doppio clic su `avvia.bat`, su Mac su `avvia.command`.

## Prima configurazione

Tutto da **Impostazioni** nell'app:

1. **AI**: incolla la chiave API (Gemini, Claude o compatibile OpenAI) e premi *Elenca modelli* per scegliere il modello. Le chiavi vengono salvate in `~/.tone-builder/settings.yaml`, fuori dal repository. In alternativa usa le variabili d'ambiente `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
2. **Helix Stadium** (una volta sola): esporta dall'app Helix Stadium alcuni tuoi preset `.hsp` (meglio un'intera setlist) e caricali. Il primo file diventa il "telaio" dei preset generati, gli altri insegnano al programma i blocchi disponibili.
3. **Quad Cortex** (una volta sola): collega il QC via USB, **chiudi Cortex Control** e premi *Leggi catalogo*. Serve per conoscere i range dei parametri e le tue Neural Capture.
4. **Cartelle** (facoltativo): dove tieni gli IR `.wav` e i preset scaricati (`.hlx`, `.hsp`).

## Limiti da conoscere

- Le librerie che parlano con i dispositivi sono progetti open source **non ufficiali**, ricavati dall'analisi dei formati e del protocollo: [helixgen](https://github.com/sheax0r/helixgen-core) per Helix Stadium e [pyquadcortex](https://pypi.org/project/pyquadcortex/) per il Quad Cortex. Un aggiornamento firmware può romperle. Fai un backup prima di scrivere sui dispositivi.
- **Quad Cortex**: scrivere sul QC sovrascrive lo slot scelto (l'app chiede conferma). L'on/off dei blocchi per scena si può scrivere solo dopo aver attivato a mano la "modalità scena" del blocco sul touchscreen: per delay, riverberi e modulazioni l'app usa il Mix a 0 nelle scene in cui sono spenti (come i preset di fabbrica); per gli altri blocchi ti indica il passaggio e poi applica gli stati con *Applica on/off per scena*. Gli IR vanno caricati con Cortex Control: l'app li seleziona per nome.
- **Helix Stadium**: gli IR devono essere registrati (vedi `helixgen register-irs`); se un IR manca il preset viene creato comunque, con il cab senza IR, e l'app lo segnala.
- **DSP**: i costi per blocco sono stime (modificabili in `~/.tone-builder/dsp_weights.json`). Il verdetto finale è del dispositivo.
- **CustomTone e Cortex Cloud**: l'app non accede ai siti con le tue credenziali. Prepara i link di ricerca; scarichi tu i preset gratuiti con il tuo account e li metti nella cartella preset, da dove l'app li importa.
- **YouTube**: leggere titolo e autore da un link non scarica nulla. Scaricare l'audio è contro i termini di YouTube: l'opzione esiste per uso personale ed è a tuo rischio.
- Solo risorse gratuite: l'app non cerca né converte preset o pacchetti a pagamento.

## Struttura

```
tonebuilder/
  recipe.py            formato neutro della "ricetta del suono"
  knobs.py             traduzione manopole neutre <-> parametri nativi
  designer.py          AI: ricerca, progettazione, modifiche, analisi audio
  merge.py             merge intelligente con budget DSP
  dsp.py               stima del carico DSP
  importers.py         import .hlx / .hsp / .json
  sources.py           IR e preset locali, link di ricerca
  ai/                  provider: Gemini, Claude, compatibile OpenAI
  catalogs/            cataloghi modelli Helix Stadium e Quad Cortex
  backends/            generazione .hsp e scrittura USB sul QC
  audio/               metadati, separazione chitarra, misure
  server.py, web/      server locale e interfaccia grafica
tests/                 test automatici (pytest)
```

## Sviluppo

```bash
python -m pip install -e ".[all,dev]"
pytest
```

## Licenza

MIT. Line 6, Helix, Neural DSP e Quad Cortex sono marchi dei rispettivi proprietari; questo progetto non è affiliato né approvato da loro.
