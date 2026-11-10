/* Tone Builder — interfaccia (JavaScript semplice, nessuna compilazione). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Math.random().toString(16).slice(2, 10);

const ROLE_IT = {
  gate: "Gate", comp: "Compressore", wah: "Wah", pitch: "Pitch", drive: "Drive", amp: "Ampli", preamp: "Preampli",
  cab: "Cab", ir: "IR", eq: "EQ", mod: "Modulazione", delay: "Delay", reverb: "Riverbero", volume: "Volume",
  filter: "Filtro", synth: "Synth", looper: "Looper", other: "Altro",
};
const KNOBS = ["gain", "drive", "bass", "mid", "treble", "presence", "master", "channel_volume", "tone", "level",
  "mix", "feedback", "decay", "rate", "depth", "sustain", "bright", "sag", "bias",
  "time_ms", "predelay_ms", "attack_ms", "level_db", "threshold_db", "low_cut_hz", "high_cut_hz", "ratio"];
const KNOB_IT = {
  gain: "Gain", drive: "Drive", bass: "Bassi", mid: "Medi", treble: "Alti", presence: "Presence", master: "Master",
  channel_volume: "Volume canale", tone: "Tono", level: "Livello", mix: "Mix", feedback: "Ripetizioni", decay: "Decadimento",
  rate: "Velocità", depth: "Profondità", sustain: "Sustain", bright: "Bright", sag: "Sag", bias: "Bias",
  time_ms: "Tempo (ms)", predelay_ms: "Predelay (ms)", attack_ms: "Attacco (ms)", level_db: "Livello (dB)",
  threshold_db: "Soglia (dB)", low_cut_hz: "Taglio bassi (Hz)", high_cut_hz: "Taglio alti (Hz)", ratio: "Rapporto",
};
const isScale10 = (k) => !/_ms$|_db$|_hz$|^ratio$/.test(k);

const S = {
  status: null, setlists: [], setlist: null, songIdx: -1,
  selBlock: null, selSnap: null, mergePicks: [], mergeRecipe: null, qcPlan: null,
};

/* ---------------------------------------------------------------- utilità */

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opt);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : `Errore ${r.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ");
    throw new Error(msg);
  }
  return data;
}

/* Un <dialog> modale sta sopra a tutto il resto della pagina: messaggi e
   attesa vanno spostati dentro il dialogo aperto, altrimenti finiscono sotto. */
function topHost() {
  return $("dialog[open]") || document.body;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  const host = topHost();
  if (t.parentElement !== host) host.appendChild(t);
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), bad ? 6000 : 2800);
}

async function busy(text, fn) {
  $("#busyText").textContent = text;
  const host = topHost();
  if ($("#busy").parentElement !== host) host.appendChild($("#busy"));
  $("#busy").classList.remove("hidden");
  try { return await fn(); }
  catch (e) { toast(e.message, true); throw e; }
  finally { $("#busy").classList.add("hidden"); }
}

function download(name, content, type = "application/json") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

const song = () => (S.setlist && S.songIdx >= 0 ? S.setlist.songs[S.songIdx] : null);
const recipe = () => song()?.recipe || null;

/* ---------------------------------------------------------------- salvataggio */

let saveTimer = null;
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    if (!S.setlist) return;
    try { await api("PUT", `/api/setlists/${S.setlist.id}`, S.setlist); }
    catch (e) { toast(`Salvataggio non riuscito: ${e.message}`, true); }
  }, 500);
}

let checkTimer = null;
function recipeChanged() {
  renderPreset();
  saveSoon();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    const r = recipe();
    if (!r) return;
    try { const v = await api("POST", "/api/validate", r); renderMeters(v.dsp); }
    catch (e) { toast(`Ricetta non valida: ${e.message}`, true); }
  }, 400);
}

/* ---------------------------------------------------------------- stato */

async function loadStatus() {
  S.status = await api("GET", "/api/status");
  const st = S.status;
  const prov = st.settings.provider;
  const p = st.settings.providers[prov] || {};
  const items = [
    [`AI: ${prov} ${p.model ? "· " + p.model : ""}`, p.has_key || prov === "openai"],
    [st.helix.ready ? `Helix pronto (${st.helix.blocks_in_library} blocchi)` : "Helix: carica un export .hsp", st.helix.ready],
    [st.qc.catalog_from_device ? "QC: catalogo letto" : "QC: catalogo da leggere", st.qc.catalog_from_device],
  ];
  $("#topStatus").innerHTML = items.map(([t, ok]) => `<span class="${ok ? "ok" : "ko"}">${esc(t)}</span>`).join("");
}

/* ---------------------------------------------------------------- scalette */

async function loadSetlists(selectId) {
  S.setlists = await api("GET", "/api/setlists");
  if (!S.setlists.length) {
    const s = await api("POST", "/api/setlists", { name: "La mia scaletta" });
    S.setlists = [{ id: s.id, name: s.name, songs: 0 }];
  }
  const id = selectId || localStorage.getItem("tb.setlist") || S.setlists[0].id;
  const pick = S.setlists.find((s) => s.id === id) || S.setlists[0];
  $("#setlistSelect").innerHTML = S.setlists.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")
    + `<option value="__rename">Rinomina questa scaletta…</option><option value="__delete">Elimina questa scaletta…</option>`;
  await openSetlist(pick.id);
}

async function openSetlist(id) {
  S.setlist = await api("GET", `/api/setlists/${id}`);
  try { localStorage.setItem("tb.setlist", id); } catch {}
  $("#setlistSelect").value = id;
  S.songIdx = S.setlist.songs.length ? 0 : -1;
  renderSongs();
  openSong(S.songIdx);
}

function renderSongs() {
  const list = $("#songList");
  list.innerHTML = S.setlist.songs.map((s, i) => `
    <li class="${i === S.songIdx ? "active" : ""}" data-i="${i}">
      <span class="t">${esc(s.title || "Senza titolo")}<span class="a">${esc(s.artist || "")}</span></span>
      <span class="dot ${s.recipe ? "ready" : ""}" title="${s.recipe ? "Preset pronto" : "Da progettare"}"></span>
      <button class="del" data-del="${i}" aria-label="Elimina brano">✕</button>
    </li>`).join("");
}

function addSong() {
  S.setlist.songs.push({ id: uid(), title: "", artist: "", request: {}, audio: null, recipe: null,
    research: "", sources: [], links: [], warnings: [], chat: [] });
  S.songIdx = S.setlist.songs.length - 1;
  renderSongs();
  openSong(S.songIdx);
  saveSoon();
  $("#songTitle").focus();
}

function openSong(i) {
  S.songIdx = i;
  S.selBlock = null; S.selSnap = null;
  const s = song();
  $("#emptyState").classList.toggle("hidden", !!s);
  $("#songView").classList.toggle("hidden", !s);
  renderSongs();
  if (!s) { renderAssist(); return; }
  $("#songTitle").value = s.title || "";
  $("#songArtist").value = s.artist || "";
  const rq = s.request || {};
  $("#reqDescription").value = rq.description || "";
  $("#reqGuitar").value = rq.guitar || "";
  $("#reqPrefs").value = rq.gear_preferences || "";
  $("#reqSnapshots").value = rq.snapshots || "";
  renderAnalysis();
  switchTab(s.recipe ? "preset" : "design");
  renderPreset();
  renderAssist();
  if (s.recipe) recipeChanged();
}

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#tab-design").classList.toggle("hidden", name !== "design");
  $("#tab-preset").classList.toggle("hidden", name !== "preset");
}

/* ---------------------------------------------------------------- progettazione */

function readRequest() {
  const s = song();
  s.title = $("#songTitle").value.trim();
  s.artist = $("#songArtist").value.trim();
  s.request = {
    description: $("#reqDescription").value.trim(),
    guitar: $("#reqGuitar").value.trim(),
    gear_preferences: $("#reqPrefs").value.trim(),
    snapshots: $("#reqSnapshots").value.trim(),
  };
}

function renderAnalysis() {
  const s = song();
  const box = $("#analysisBox");
  const a = s?.audio;
  if (!a) { box.classList.add("hidden"); $("#dropZone span").innerHTML = "Trascina qui l'MP3 o <u>scegli un file</u>"; $("#dropZone").classList.remove("has"); return; }
  box.classList.remove("hidden");
  const tr = a.track || {};
  box.textContent = [
    tr.title || tr.artist ? `Brano riconosciuto: ${tr.artist ? tr.artist + " – " : ""}${tr.title}` : "",
    ...(a.notes || []),
    a.analysis ? `\n${a.analysis}` : "",
  ].filter(Boolean).join("\n");
}

async function analyze() {
  const s = song();
  const fd = new FormData();
  const f = $("#audioFile").files[0];
  const url = $("#audioUrl").value.trim();
  if (f) fd.append("file", f);
  else if (url) { fd.append("url", url); fd.append("download", $("#audioDownload").checked ? "true" : "false"); }
  else { toast("Carica un file audio o incolla un link", true); return; }
  const res = await busy("Analizzo la traccia (la separazione della chitarra può richiedere qualche minuto)…",
    () => api("POST", "/api/audio/analyze", fd));
  s.audio = res;
  if (res.track) {
    if (!$("#songTitle").value && res.track.title) $("#songTitle").value = res.track.title;
    if (!$("#songArtist").value && res.track.artist) $("#songArtist").value = res.track.artist;
  }
  readRequest();
  renderAnalysis();
  renderSongs();
  saveSoon();
}

async function designPreset() {
  readRequest();
  const s = song();
  if (!s.title && !s.request.description) { toast("Scrivi almeno il titolo o una descrizione del suono", true); return; }
  const body = {
    title: s.title, artist: s.artist, ...s.request,
    audio_analysis: s.audio?.analysis || "", audio_id: s.audio?.audio_id || null,
  };
  const res = await busy("Cerco strumentazione e risorse gratuite, poi progetto il preset…", () => api("POST", "/api/design", body));
  s.recipe = res.recipe;
  s.research = res.research;
  s.sources = res.sources || [];
  s.links = res.links || [];
  s.warnings = res.warnings || [];
  s.chat = [{ who: "ai", text: `Preset creato: ${res.recipe.chain.length} blocchi, ${res.recipe.snapshots.length} snapshot.` }];
  renderSongs();
  switchTab("preset");
  renderAssist();
  recipeChanged();
  renderMeters(res.dsp);
}

/* ---------------------------------------------------------------- preset */

function effOn(block, snap) {
  return snap.enabled && block.id in snap.enabled ? snap.enabled[block.id] : block.enabled;
}

function renderMeters(d) {
  if (!d) return;
  const m = (label, v, budget) => `
    <div class="meter ${v > budget ? "over" : ""}" title="Stima indicativa: il verdetto finale è del dispositivo">
      ${label}: ${Math.round(v)}% DSP stimato
      <div class="bar"><div class="fill" style="width:${Math.min(100, v)}%"></div></div>
    </div>`;
  $("#meters").innerHTML = m("Helix (1 percorso)", d.helix, d.budget.helix) + m("Quad Cortex", d.qc, d.budget.qc);
}

function renderPreset() {
  const r = recipe();
  $("#noPreset").classList.toggle("hidden", !!r);
  $("#presetBody").classList.toggle("hidden", !r);
  if (!r) return;
  $("#presetName").value = r.name;
  renderBoard();
  $("#notes").innerHTML = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("") || "<li>Nessuna nota.</li>";
  renderBlockEditor();
  renderSnapEditor();
}

/* pedaliera e matrice: ridisegnabili senza toccare gli editor aperti */
function renderBoard() {
  const r = recipe();
  if (!r) return;
  const fsOf = (id) => (r.footswitches || []).filter((f) => f.block_id === id).map((f) => f.switch).join(" ");
  const pedal = (b) => {
    const knobs = Object.entries(b.settings || {}).slice(0, 5)
      .map(([k, v]) => `<span class="knob">${esc(KNOB_IT[k] || k)} ${esc(v)}</span>`).join("");
    const fs = fsOf(b.id);
    return `<div class="pedal ${b.enabled ? "" : "off"} ${S.selBlock === b.id ? "sel" : ""}" data-id="${esc(b.id)}"
              style="--c: var(--r-${b.role})" tabindex="0" role="button" aria-label="${esc(ROLE_IT[b.role])} ${esc(b.label || b.gear_ref)}">
        <span class="led"></span>
        <span class="role">${esc(ROLE_IT[b.role] || b.role)}</span>
        <span class="name">${esc(b.label || b.gear_ref || b.id)}</span>
        <span class="gear">${esc(b.gear_ref)}</span>
        <span class="knobs">${knobs}</span>
        ${fs ? `<span class="fs">${esc(fs)}</span>` : ""}
      </div>`;
  };
  $("#board").innerHTML = [1, 2].map((ln) => {
    const blocks = r.chain.filter((b) => (b.line || 1) === ln);
    return `<div class="line-label">Linea ${ln}${ln === 1 ? " · ingresso" : " · uscita"} <span>${blocks.length} blocchi</span></div>
      <div class="line" data-line="${ln}">${blocks.map(pedal).join("")}
        <button class="btn ghost add-block" data-add-line="${ln}">Aggiungi blocco</button></div>`;
  }).join("");

  // matrice
  const snaps = r.snapshots || [];
  let html = `<thead><tr><th></th>${snaps.map((s, i) => `<th data-snap="${i}" title="Modifica snapshot">${esc(s.name)}</th>`).join("")}
    ${snaps.length < 8 ? `<th class="add" data-snap="new">Aggiungi</th>` : ""}</tr></thead><tbody>`;
  const ordered = [...r.chain].sort((a, b) => (a.line || 1) - (b.line || 1));
  for (const b of ordered) {
    html += `<tr style="--c: var(--r-${b.role})"><th><span class="swatch"></span>${esc(b.label || b.gear_ref || b.id)}${(b.line || 1) === 2 ? " <small>L2</small>" : ""}</th>`;
    snaps.forEach((s, i) => {
      const on = effOn(b, s);
      const ov = s.overrides?.[b.id];
      const ovTxt = ov ? Object.entries(ov).map(([k, v]) => `${KNOB_IT[k] || k} ${v}`).join(", ") : "";
      html += `<td class="${on ? "on" : ""}" data-b="${esc(b.id)}" data-s="${i}" title="${on ? "Acceso" : "Spento"}${ovTxt ? " · " + esc(ovTxt) : ""}">
        ${on ? "ON" : "off"}${ovTxt ? `<span class="ov">${esc(ovTxt.length > 22 ? ovTxt.slice(0, 21) + "…" : ovTxt)}</span>` : ""}</td>`;
    });
    html += "<td style='background:none;cursor:default'></td></tr>";
  }
  $("#matrix").innerHTML = html + "</tbody>";
}

function knobInputs(settings, prefix) {
  return Object.entries(settings || {}).map(([k, v]) => {
    const scale = isScale10(k);
    return `<div class="knob-row">
      <label for="${prefix}-${k}">${esc(KNOB_IT[k] || k)}</label>
      ${scale ? `<input type="range" min="0" max="10" step="0.1" value="${esc(v)}" data-k="${esc(k)}" data-kind="range">` : "<span></span>"}
      <input type="number" step="${scale ? 0.1 : 1}" value="${esc(v)}" id="${prefix}-${k}" data-k="${esc(k)}" data-kind="num">
      <button class="x" data-del-k="${esc(k)}" aria-label="Rimuovi ${esc(KNOB_IT[k] || k)}">✕</button>
    </div>`;
  }).join("");
}

function knobAdder(existing) {
  const opts = KNOBS.filter((k) => !(k in (existing || {}))).map((k) => `<option value="${k}">${esc(KNOB_IT[k])}</option>`).join("");
  return `<select data-add-knob><option value="">Aggiungi manopola…</option>${opts}</select>`;
}

function renderBlockEditor() {
  const ed = $("#blockEditor");
  const r = recipe();
  const b = r?.chain.find((x) => x.id === S.selBlock);
  if (!b) { ed.classList.add("hidden"); return; }
  ed.classList.remove("hidden");
  const fs = (r.footswitches || []).find((f) => f.block_id === b.id);
  const roles = Object.keys(ROLE_IT).map((k) => `<option value="${k}" ${k === b.role ? "selected" : ""}>${ROLE_IT[k]}</option>`).join("");
  const fsOpts = ["", ...Array.from({ length: 10 }, (_, i) => `FS${i + 1}`)]
    .map((f) => `<option value="${f}" ${fs?.switch === f ? "selected" : ""}>${f || "Nessuno"}</option>`).join("");
  ed.innerHTML = `
    <div class="editor-head">
      <h3>${esc(ROLE_IT[b.role])}: ${esc(b.label || b.gear_ref)}</h3>
      <div class="row">
        <button class="btn ghost" data-move="-1" aria-label="Sposta a sinistra">◀</button>
        <button class="btn ghost" data-move="1" aria-label="Sposta a destra">▶</button>
        <button class="btn ghost" id="delBlock">Rimuovi blocco</button>
      </div>
    </div>
    <div class="editor-grid">
      <div class="field"><label>Ruolo</label><select data-f="role">${roles}</select></div>
      <div class="field"><label>Riferimento reale</label><input data-f="gear_ref" value="${esc(b.gear_ref)}"></div>
      <div class="field"><label>Etichetta</label><input data-f="label" value="${esc(b.label)}" maxlength="16"></div>
      <div class="field"><label>Footswitch</label><select data-f="fs">${fsOpts}</select></div>
      <div class="field"><label>Linea</label><select data-f="line">
        <option value="1" ${(b.line || 1) === 1 ? "selected" : ""}>Linea 1 (ingresso, ampli)</option>
        <option value="2" ${b.line === 2 ? "selected" : ""}>Linea 2 (effetti in uscita)</option></select></div>
      <div class="field"><label>Modello Helix</label><input data-f="helix" value="${esc(b.helix?.model || "")}" list="helixModels"></div>
      <div class="field"><label>Modello Quad Cortex</label><input data-f="qc" value="${esc(b.qc?.model ?? "")}"></div>
      <div class="field"><label>IR (nome file)</label><input data-f="ir" value="${esc(b.ir || "")}"></div>
      <label class="check"><input type="checkbox" data-f="enabled" ${b.enabled ? "checked" : ""}> Acceso nello stato base</label>
    </div>
    <datalist id="helixModels"></datalist>
    <div class="editor-grid" data-knobs>${knobInputs(b.settings, "bk")}</div>
    <div>${knobAdder(b.settings)}</div>
    ${b.notes ? `<p class="hint">${esc(b.notes)}</p>` : ""}`;
  loadHelixOptions(b.role);
}

async function loadHelixOptions(role) {
  try {
    const list = await api("GET", `/api/catalog/helix?role=${encodeURIComponent(role)}`);
    const dl = $("#helixModels");
    if (dl) dl.innerHTML = list.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.ready ? " ✓" : ""}</option>`).join("");
  } catch {}
}

function renderSnapEditor() {
  const ed = $("#snapEditor");
  const r = recipe();
  const s = r?.snapshots?.[S.selSnap];
  if (!s) { ed.classList.add("hidden"); return; }
  ed.classList.remove("hidden");
  const blocks = r.chain.map((b) => `<option value="${esc(b.id)}">${esc(b.label || b.gear_ref || b.id)}</option>`).join("");
  ed.innerHTML = `
    <div class="editor-head">
      <h3>Snapshot ${S.selSnap + 1}</h3>
      <button class="btn ghost" id="delSnap">Elimina snapshot</button>
    </div>
    <div class="editor-grid">
      <div class="field"><label>Nome</label><input data-sf="name" value="${esc(s.name)}" maxlength="16"></div>
      <div class="field"><label>Volume dello snapshot (dB)</label><input type="number" step="0.5" data-sf="level_db" value="${s.level_db ?? ""}"></div>
    </div>
    <p class="hint">Valori diversi in questo snapshot (i parametri elencati vengono gestiti per snapshot):</p>
    ${Object.entries(s.overrides || {}).map(([bid, knobs]) => {
      const b = r.chain.find((x) => x.id === bid);
      return `<div data-ov="${esc(bid)}"><strong>${esc(b ? b.label || b.gear_ref || bid : bid)}</strong>
        <div class="editor-grid">${knobInputs(knobs, "sv-" + bid)}</div>${knobAdder(knobs)}</div>`;
    }).join("")}
    <div class="row"><select id="ovBlock"><option value="">Aggiungi valori per il blocco…</option>${blocks}</select></div>`;
}

function updateKnob(target, settings) {
  const k = target.dataset.k;
  const v = parseFloat(target.value);
  if (Number.isNaN(v)) return false;
  settings[k] = v;
  const row = target.closest(".knob-row");
  $$("input", row).forEach((i) => { if (i !== target) i.value = v; });
  return true;
}

/* ---------------------------------------------------------------- eventi preset */

function bindPreset() {
  $("#presetName").addEventListener("change", (e) => { recipe().name = e.target.value.slice(0, 32); recipeChanged(); });

  $("#board").addEventListener("click", (e) => {
    const addLine = e.target.dataset.addLine;
    if (addLine) {
      const r = recipe();
      let n = 1; while (r.chain.some((b) => b.id === `fx${n}`)) n++;
      r.chain.push({ id: `fx${n}`, role: addLine === "2" ? "delay" : "drive", gear_ref: "", label: "", line: +addLine,
        enabled: true, settings: {}, ir: null, helix: { model: null, params: {} }, qc: { model: null, params: {} }, notes: "", source: "" });
      S.selBlock = `fx${n}`;
      recipeChanged();
      return;
    }
    const p = e.target.closest(".pedal");
    if (!p) return;
    S.selBlock = S.selBlock === p.dataset.id ? null : p.dataset.id;
    renderPreset();
  });
  $("#board").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("pedal")) { e.preventDefault(); e.target.click(); }
  });

  const be = $("#blockEditor");
  be.addEventListener("input", (e) => {
    const b = recipe().chain.find((x) => x.id === S.selBlock);
    if (e.target.dataset.k && updateKnob(e.target, b.settings)) { saveSoon(); clearTimeout(be._t); be._t = setTimeout(renderBoard, 200); }
  });
  be.addEventListener("change", (e) => {
    const r = recipe();
    const b = r.chain.find((x) => x.id === S.selBlock);
    const f = e.target.dataset.f;
    if (e.target.matches("[data-add-knob]") && e.target.value) {
      b.settings[e.target.value] = isScale10(e.target.value) ? 5 : 0;
    } else if (f === "enabled") b.enabled = e.target.checked;
    else if (f === "helix") b.helix = { ...(b.helix || {}), model: e.target.value.trim() || null, params: {} };
    else if (f === "qc") b.qc = { ...(b.qc || {}), model: e.target.value.trim() || null, params: {} };
    else if (f === "ir") b.ir = e.target.value.trim() || null;
    else if (f === "line") b.line = +e.target.value;
    else if (f === "fs") {
      r.footswitches = (r.footswitches || []).filter((x) => x.block_id !== b.id && x.switch !== e.target.value);
      if (e.target.value) r.footswitches.push({ switch: e.target.value, block_id: b.id, label: b.label || "" });
    } else if (f) b[f] = e.target.value;
    else return;
    recipeChanged();
  });
  be.addEventListener("click", (e) => {
    const r = recipe();
    const i = r.chain.findIndex((x) => x.id === S.selBlock);
    if (e.target.dataset.delK) { delete r.chain[i].settings[e.target.dataset.delK]; recipeChanged(); }
    else if (e.target.dataset.move) {
      const dir = parseInt(e.target.dataset.move, 10);
      let j = i + dir;
      while (j >= 0 && j < r.chain.length && (r.chain[j].line || 1) !== (r.chain[i].line || 1)) j += dir;
      if (j < 0 || j >= r.chain.length) return;
      [r.chain[i], r.chain[j]] = [r.chain[j], r.chain[i]];
      recipeChanged();
    } else if (e.target.id === "delBlock") {
      const id = r.chain[i].id;
      r.chain.splice(i, 1);
      r.footswitches = (r.footswitches || []).filter((f) => f.block_id !== id);
      for (const s of r.snapshots) { delete s.enabled[id]; delete s.overrides[id]; }
      S.selBlock = null;
      recipeChanged();
    }
  });

  $("#matrix").addEventListener("click", (e) => {
    const r = recipe();
    const th = e.target.closest("th[data-snap]");
    if (th) {
      if (th.dataset.snap === "new") {
        r.snapshots.push({ name: `Snapshot ${r.snapshots.length + 1}`, enabled: {}, overrides: {}, level_db: null, notes: "" });
        S.selSnap = r.snapshots.length - 1;
      } else S.selSnap = S.selSnap === +th.dataset.snap ? null : +th.dataset.snap;
      recipeChanged();
      return;
    }
    const td = e.target.closest("td[data-b]");
    if (!td) return;
    const b = r.chain.find((x) => x.id === td.dataset.b);
    const s = r.snapshots[+td.dataset.s];
    const next = !effOn(b, s);
    if (next === b.enabled) delete s.enabled[b.id]; else s.enabled[b.id] = next;
    recipeChanged();
  });

  const se = $("#snapEditor");
  se.addEventListener("input", (e) => {
    const s = recipe().snapshots[S.selSnap];
    const ov = e.target.closest("[data-ov]");
    if (ov && e.target.dataset.k && updateKnob(e.target, s.overrides[ov.dataset.ov])) { saveSoon(); clearTimeout(se._t); se._t = setTimeout(renderBoard, 200); }
  });
  se.addEventListener("change", (e) => {
    const r = recipe();
    const s = r.snapshots[S.selSnap];
    const ov = e.target.closest("[data-ov]");
    if (e.target.dataset.sf === "name") s.name = e.target.value.slice(0, 16);
    else if (e.target.dataset.sf === "level_db") s.level_db = e.target.value === "" ? null : parseFloat(e.target.value);
    else if (e.target.id === "ovBlock" && e.target.value) { s.overrides[e.target.value] = s.overrides[e.target.value] || {}; }
    else if (ov && e.target.matches("[data-add-knob]") && e.target.value) {
      const b = r.chain.find((x) => x.id === ov.dataset.ov);
      s.overrides[ov.dataset.ov][e.target.value] = b?.settings?.[e.target.value] ?? (isScale10(e.target.value) ? 5 : 0);
    } else if (ov && e.target.dataset.k) { /* già gestito in input */ }
    else return;
    recipeChanged();
  });
  se.addEventListener("click", (e) => {
    const r = recipe();
    const s = r.snapshots[S.selSnap];
    const ov = e.target.closest("[data-ov]");
    if (e.target.id === "delSnap") { r.snapshots.splice(S.selSnap, 1); S.selSnap = null; recipeChanged(); }
    else if (ov && e.target.dataset.delK) {
      delete s.overrides[ov.dataset.ov][e.target.dataset.delK];
      if (!Object.keys(s.overrides[ov.dataset.ov]).length) delete s.overrides[ov.dataset.ov];
      recipeChanged();
    }
  });
}

/* ---------------------------------------------------------------- assistente */

function renderAssist() {
  const s = song();
  $("#chat").innerHTML = (s?.chat || []).map((m) => `<div class="msg ${m.who} ${m.err ? "err" : ""}">${esc(m.text)}</div>`).join("")
    || `<p class="hint">Quando il preset è pronto puoi chiedere modifiche, come faresti con un tecnico del suono.</p>`;
  const chat = $("#chat");
  chat.scrollTop = chat.scrollHeight;
  $("#researchText").textContent = s?.research || "Ancora nessuna ricerca.";
  const links = [...(s?.sources || []), ...(s?.links || [])];
  $("#sourceLinks").innerHTML = links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a></li>`).join("");
  const w = s?.warnings || [];
  $("#warnings").innerHTML = w.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>Nessun avviso.</li>";
  $("#warnBox").querySelector("summary").textContent = w.length ? `Avvisi (${w.length})` : "Avvisi";
}

async function sendChat(e) {
  e.preventDefault();
  const s = song();
  const text = $("#chatInput").value.trim();
  if (!text) return;
  if (!s?.recipe) { toast("Crea o importa prima un preset", true); return; }
  s.chat.push({ who: "me", text });
  $("#chatInput").value = "";
  renderAssist();
  try {
    const res = await busy("Applico la modifica…", () => api("POST", "/api/edit", { recipe: s.recipe, instruction: text }));
    s.recipe = res.recipe;
    s.warnings = res.warnings || [];
    const last = (res.recipe.notes || []).slice(-1)[0];
    s.chat.push({ who: "ai", text: last || "Modifica applicata." });
    recipeChanged();
    renderMeters(res.dsp);
  } catch (err) {
    s.chat.push({ who: "ai", text: err.message, err: true });
  }
  renderAssist();
  saveSoon();
}

function duplicateSong() {
  const s = song();
  if (!s?.recipe) return;
  const name = prompt("Nome del nuovo preset (lo trovi come nuovo brano nella scaletta)", `${s.recipe.name} 2`);
  if (!name) return;
  const title = prompt("Titolo del brano per cui lo userai (puoi lasciarlo uguale)", name) || name;
  const copy = JSON.parse(JSON.stringify(s));
  copy.id = uid();
  copy.title = title;
  copy.recipe.name = name.slice(0, 32);
  copy.recipe.song = { ...(copy.recipe.song || {}), title };
  copy.chat = [{ who: "ai", text: `Creato partendo da "${s.recipe.name}". Chiedimi le modifiche per questo brano.` }];
  S.setlist.songs.splice(S.songIdx + 1, 0, copy);
  saveSoon();
  openSong(S.songIdx + 1);
  toast(`Creato "${name}"`);
}

/* ---------------------------------------------------------------- voce */

const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
function attachMic(field) {
  if (!SpeechRec || !field) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn icon mic";
  btn.title = "Detta a voce";
  btn.setAttribute("aria-label", "Detta a voce");
  btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11Z"/></svg>';
  const wrap = document.createElement("div");
  wrap.className = "with-mic";
  field.parentNode.insertBefore(wrap, field);
  wrap.append(field, btn);
  let rec = null;
  btn.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new SpeechRec();
    rec.lang = "it-IT";
    rec.interimResults = true;
    rec.continuous = true;
    const base = field.value ? field.value.replace(/\s*$/, " ") : "";
    let finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t; else interim += t;
      }
      field.value = base + finalText + interim;
    };
    rec.onerror = (ev) => toast(ev.error === "not-allowed" ? "Consenti l'uso del microfono al browser" : `Dettatura: ${ev.error}`, true);
    rec.onend = () => { rec = null; btn.classList.remove("rec"); field.dispatchEvent(new Event("change")); };
    btn.classList.add("rec");
    rec.start();
  });
}

/* ---------------------------------------------------------------- dispositivi */

async function makeHelix() {
  const res = await busy("Creo il file .hsp…", () => api("POST", "/api/helix/generate", { recipe: recipe() }));
  $("#deviceResult").innerHTML = `<div class="result">
    <strong>File pronto:</strong> <a href="${esc(res.download)}" download>${esc(res.file)}</a>
    <p class="hint">Salvato anche in ${esc(res.path)}. Importalo dall'app Helix Stadium (Librarian &gt; Import).</p>
    ${res.warnings?.length ? `<ul>${res.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</div>`;
}

function openQC() {
  $("#qcSlotRun").value = S.status?.settings?.qc_target_setlist_slot || "";
  $("#qcPlan").innerHTML = "";
  $("#qcConfirm").checked = false;
  $("#qcRunBtn").disabled = true;
  $("#qcBypassBtn").classList.add("hidden");
  $("#qcDlg").showModal();
}

async function qcPlan() {
  const slot = $("#qcSlotRun").value.trim();
  if (!slot) { toast("Indica lo slot, ad esempio 30A", true); return; }
  const plan = await busy("Preparo il piano…", () => api("POST", "/api/qc/plan", { recipe: recipe(), slot }));
  S.qcPlan = plan;
  $("#qcPlan").innerHTML = `
    <ol class="plan">${plan.steps.map((s) => `<li>${esc(s.text)}</li>`).join("")}</ol>
    ${plan.warnings.length ? `<p class="hint">Avvisi:</p><ul class="plan">${plan.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    ${plan.manual.length ? `<p class="hint">Da fare a mano sul QC dopo la scrittura:</p><ul class="plan">${plan.manual.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  $("#qcRunBtn").disabled = !$("#qcConfirm").checked;
}

async function qcRun() {
  const slot = $("#qcSlotRun").value.trim();
  const res = await busy("Scrivo sul Quad Cortex…", () => api("POST", "/api/qc/execute", { recipe: recipe(), slot, confirm: true }));
  $("#qcPlan").innerHTML = `<div class="result"><strong>Fatto: ${res.steps} operazioni.</strong>
    ${res.problems.length ? `<ul>${res.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : "<p class='hint'>Nessun problema segnalato.</p>"}
    ${res.manual.length ? `<p class="hint">Ora attiva la modalità scena sui blocchi indicati, poi premi “Applica on/off per scena”.</p><ul>${res.manual.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}</div>`;
  $("#qcBypassBtn").classList.toggle("hidden", !res.manual.length);
  toast("Preset scritto sul Quad Cortex");
}

async function qcBypass() {
  const slot = $("#qcSlotRun").value.trim();
  const res = await busy("Scrivo on/off per scena…", () => api("POST", "/api/qc/scene-bypass", { recipe: recipe(), slot, confirm: true }));
  toast(res.problems.length ? `Completato con ${res.problems.length} problemi` : "On/off per scena applicato", !!res.problems.length);
}

async function importPreset(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await busy("Importo il preset…", () => api("POST", "/api/import", fd));
  const s = song();
  if (s.recipe && !confirm("Sostituire il preset attuale di questo brano con quello importato?")) return;
  s.recipe = res.recipe;
  s.chat.push({ who: "ai", text: `Importato "${res.recipe.name}" (${res.recipe.chain.length} blocchi).` });
  renderSongs(); renderAssist(); recipeChanged(); renderMeters(res.dsp);
}

/* ---------------------------------------------------------------- impostazioni */

function openSettings() {
  const st = S.status.settings;
  S.editProvider = st.provider;
  renderProviderFields();
  $("#webResearch").checked = st.web_research;
  $("#helixOut").value = st.helix_output_dir || "";
  $("#qcSlot").value = st.qc_target_setlist_slot || "";
  $("#irFolders").value = (st.ir_folders || []).join("\n");
  $("#presetFolders").value = (st.preset_folders || []).join("\n");
  $("#demucsCmd").value = st.demucs_command || "";
  $("#demucsStatus").textContent = S.status.optional.demucs
    ? "Demucs trovato: la chitarra viene isolata prima dell'analisi."
    : "Demucs non trovato: l'analisi usa il mix completo. Le istruzioni per installarlo sono nel README, sezione Separazione della chitarra.";
  $("#helixLibStatus").textContent = S.status.helix.ready
    ? `Libreria pronta: ${S.status.helix.blocks_in_library} blocchi dai tuoi export.` : "Libreria vuota: carica almeno un export .hsp.";
  $("#qcStatus").textContent = S.status.qc.catalog_from_device
    ? `Catalogo letto dal dispositivo (${S.status.qc.models} modelli).`
    : `Catalogo di fabbrica senza parametri (${S.status.qc.models} modelli).`;
  $("#settingsDlg").showModal();
}

const PROVIDER_LABEL = { gemini: "Gemini", claude: "Claude", openai: "OpenAI / locale" };
function renderProviderFields() {
  const st = S.status.settings;
  $("#providerSeg").innerHTML = Object.keys(st.providers).map((p) =>
    `<button type="button" data-p="${p}" class="${p === S.editProvider ? "active" : ""}">${PROVIDER_LABEL[p] || p}</button>`).join("");
  const p = st.providers[S.editProvider];
  $("#providerFields").innerHTML = `
    <div class="field"><label for="pfKey">Chiave API ${p.has_key ? `(salvata ${esc(p.key_hint)})` : ""}</label>
      <input id="pfKey" type="password" autocomplete="off" placeholder="${p.has_key ? "Lascia vuoto per non cambiarla" : "Incolla la chiave"}"></div>
    <div class="row">
      <div class="field" style="flex:1"><label for="pfModel">Modello</label><input id="pfModel" value="${esc(p.model)}" list="pfModels"></div>
      <button type="button" class="btn" id="pfList">Elenca modelli</button>
    </div>
    <datalist id="pfModels"></datalist>
    ${S.editProvider === "claude" ? `<div class="field"><label for="pfWs">ID workspace (solo se la chiave non appartiene a un workspace)</label><input id="pfWs" value="${esc(p.workspace_id || "")}" placeholder="wrkspc_…"></div>` : ""}
    ${S.editProvider === "openai" ? `<div class="field"><label for="pfBase">Indirizzo API</label><input id="pfBase" value="${esc(p.base_url)}" placeholder="https://api.openai.com/v1 oppure http://localhost:11434/v1"></div>` : ""}`;
}

async function saveSettings() {
  const body = {
    provider: S.editProvider,
    providers: { [S.editProvider]: { model: $("#pfModel").value.trim(), api_key: $("#pfKey").value.trim(),
      ...($("#pfBase") ? { base_url: $("#pfBase").value.trim() } : {}),
      ...($("#pfWs") ? { workspace_id: $("#pfWs").value.trim() } : {}) } },
    web_research: $("#webResearch").checked,
    helix_output_dir: $("#helixOut").value.trim(),
    qc_target_setlist_slot: $("#qcSlot").value.trim(),
    ir_folders: $("#irFolders").value.split("\n").map((x) => x.trim()).filter(Boolean),
    preset_folders: $("#presetFolders").value.split("\n").map((x) => x.trim()).filter(Boolean),
    demucs_command: $("#demucsCmd").value.trim(),
  };
  await busy("Salvo…", () => api("PUT", "/api/settings", body));
  await loadStatus();
  $("#settingsDlg").close();
  toast("Impostazioni salvate");
}

/* ---------------------------------------------------------------- merge */

function openMerge() {
  S.mergePicks = [];
  S.mergeRecipe = null;
  $("#mergeResult").innerHTML = "";
  $("#mergeUse").classList.add("hidden");
  $("#mergeFromSong").innerHTML = `<option value="">Aggiungi da un brano della scaletta…</option>` +
    S.setlist.songs.map((s, i) => s.recipe ? `<option value="${i}">${esc(s.title || s.recipe.name)}</option>` : "").join("");
  renderPicks();
  $("#mergeDlg").showModal();
}

function addPick(r) {
  if (S.mergePicks.length >= 8) { toast("Massimo 8 suoni", true); return; }
  S.mergePicks.push({ recipe: r, snapshot: r.snapshots?.[0]?.name || null, name: r.snapshots?.[0]?.name || r.name });
  renderPicks();
}

function renderPicks() {
  $("#picks").innerHTML = S.mergePicks.length ? `<tr><th>Preset</th><th>Suono da prendere</th><th>Nome nello snapshot</th><th></th></tr>` +
    S.mergePicks.map((p, i) => `<tr data-i="${i}">
      <td>${esc(p.recipe.name)}</td>
      <td><select data-pf="snapshot"><option value="">Stato base</option>${(p.recipe.snapshots || []).map((s) =>
        `<option ${s.name === p.snapshot ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></td>
      <td><input data-pf="name" value="${esc(p.name)}" maxlength="16"></td>
      <td><button class="btn ghost" data-rm="${i}" aria-label="Rimuovi">✕</button></td></tr>`).join("")
    : `<tr><td class="hint">Aggiungi almeno due suoni.</td></tr>`;
}

async function runMerge(accept) {
  if (S.mergePicks.length < 2) { toast("Servono almeno due suoni", true); return; }
  const res = await busy("Unisco i suoni…", () => api("POST", "/api/merge", {
    picks: S.mergePicks.map((p) => ({ recipe: p.recipe, snapshot: p.snapshot || null, name: p.name })),
    name: $("#mergeName").value.trim() || "Preset unito", accept,
  }));
  S.mergeRecipe = res.recipe;
  const d = res.dsp;
  $("#mergeResult").innerHTML = `<div class="result">
    <p><strong>${res.recipe.chain.length} blocchi, ${res.recipe.snapshots.length} snapshot.</strong>
      DSP stimato: Helix ${Math.round(d.helix)}%, Quad Cortex ${Math.round(d.qc)}%.</p>
    ${res.syntheses.length ? `<p class="hint">Sintesi fatte:</p><ul>${res.syntheses.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
    ${res.applied.length ? `<p class="hint">Compromessi applicati:</p><ul>${res.applied.map((c) => `<li>${esc(c.text)}</li>`).join("")}</ul>` : ""}
    ${res.proposed.length ? `<p><strong>Il DSP non basta.</strong> Scegli quali compromessi accettare:</p>
      ${res.proposed.map((c) => `<label class="compromise"><input type="checkbox" value="${esc(c.id)}" checked> ${esc(c.text)}</label>`).join("")}
      <button class="btn" id="mergeAccept">Riprova con i compromessi scelti</button>` : ""}
  </div>`;
  $("#mergeUse").classList.toggle("hidden", !song());
}

async function localSearch(q) {
  if (!q) { $("#localResults").innerHTML = ""; return; }
  try {
    const res = await api("GET", `/api/sources/local?q=${encodeURIComponent(q)}`);
    $("#localResults").innerHTML = res.presets.map((p) => `<li><button class="btn ghost" data-local="${esc(p.path)}">${esc(p.name)}</button></li>`).join("")
      || `<li class="hint">Nessun preset trovato nelle cartelle configurate.</li>`;
  } catch (e) { toast(e.message, true); }
}

function bindMerge() {
  $("#openMerge").addEventListener("click", openMerge);
  $("#mergeClose").addEventListener("click", () => $("#mergeDlg").close());
  $("#mergeImport").addEventListener("change", async (e) => {
    for (const f of e.target.files) {
      const fd = new FormData(); fd.append("file", f);
      try { const r = await busy(`Importo ${f.name}…`, () => api("POST", "/api/import", fd)); addPick(r.recipe); } catch {}
    }
    e.target.value = "";
  });
  $("#mergeFromSong").addEventListener("change", (e) => {
    if (e.target.value !== "") addPick(JSON.parse(JSON.stringify(S.setlist.songs[+e.target.value].recipe)));
    e.target.value = "";
  });
  let t;
  $("#localSearch").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => localSearch(e.target.value.trim()), 300); });
  $("#localResults").addEventListener("click", async (e) => {
    const path = e.target.dataset.local;
    if (!path) return;
    const r = await busy("Importo…", () => api("POST", "/api/sources/import-local", { path }));
    addPick(r.recipe);
  });
  $("#picks").addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    const p = S.mergePicks[+tr.dataset.i];
    if (e.target.dataset.pf === "snapshot") { p.snapshot = e.target.value || null; if (!p.nameTouched) { p.name = p.snapshot || p.recipe.name; renderPicks(); } }
    if (e.target.dataset.pf === "name") { p.name = e.target.value; p.nameTouched = true; }
  });
  $("#picks").addEventListener("click", (e) => {
    if (e.target.dataset.rm !== undefined) { S.mergePicks.splice(+e.target.dataset.rm, 1); renderPicks(); }
  });
  $("#mergeRun").addEventListener("click", () => runMerge(null).catch(() => {}));
  $("#mergeResult").addEventListener("click", (e) => {
    if (e.target.id !== "mergeAccept") return;
    const accept = $$("#mergeResult .compromise input:checked").map((i) => i.value);
    runMerge(accept).catch(() => {});
  });
  $("#mergeUse").addEventListener("click", () => {
    const s = song();
    if (!s || !S.mergeRecipe) return;
    if (s.recipe && !confirm("Sostituire il preset del brano selezionato con quello unito?")) return;
    s.recipe = S.mergeRecipe;
    s.chat.push({ who: "ai", text: `Preset unito "${S.mergeRecipe.name}" assegnato al brano.` });
    $("#mergeDlg").close();
    renderSongs(); renderAssist(); switchTab("preset"); recipeChanged();
  });
}

/* ---------------------------------------------------------------- avvio */

function bindAll() {
  $("#openSettings").addEventListener("click", openSettings);
  $("#saveSettings").addEventListener("click", () => saveSettings().catch(() => {}));
  $("#providerSeg").addEventListener("click", (e) => { if (e.target.dataset.p) { S.editProvider = e.target.dataset.p; renderProviderFields(); } });
  $("#providerFields").addEventListener("click", async (e) => {
    if (e.target.id !== "pfList") return;
    try {
      const pv = { api_key: $("#pfKey").value.trim() };
      if ($("#pfWs")) pv.workspace_id = $("#pfWs").value.trim();
      await api("PUT", "/api/settings", { providers: { [S.editProvider]: pv } });
      const list = await busy("Chiedo l'elenco dei modelli…", () => api("GET", `/api/providers/${S.editProvider}/models`));
      $("#pfModels").innerHTML = list.map((m) => `<option value="${esc(m)}">`).join("");
      toast(`${list.length} modelli disponibili: scegli dal campo Modello`);
    } catch {}
  });
  $("#ingestFiles").addEventListener("change", async (e) => {
    const fd = new FormData();
    for (const f of e.target.files) fd.append("files", f);
    try {
      const r = await busy("Importo gli export nella libreria Helix…", () => api("POST", "/api/helix/ingest", fd));
      toast(`Libreria Helix: ${r.blocks_in_library} blocchi`);
      await loadStatus(); openSettings();
    } catch {}
    e.target.value = "";
  });
  $("#stadiumImportBtn").addEventListener("click", async () => {
    const btn = $("#stadiumImportBtn");
    const out = $("#stadiumProgress");
    try {
      await api("POST", "/api/helix/stadium-import", { include_factory: $("#stadiumFactory").checked, ip: $("#stadiumIp").value.trim() });
    } catch (e) { toast(e.message, true); return; }
    btn.disabled = true;
    const poll = async () => {
      let j;
      try { j = await api("GET", "/api/helix/stadium-import"); } catch (e) { out.textContent = e.message; btn.disabled = false; return; }
      if (j.running) {
        out.textContent = j.total ? `Leggo i preset: ${j.done} di ${j.total} — ${j.current}` : j.current;
        setTimeout(poll, 1200);
        return;
      }
      btn.disabled = false;
      if (j.error) { out.textContent = ""; toast(j.error, true); return; }
      const r = j.result;
      out.textContent = `Fatto: ${r.converted} preset letti, ${r.blocks_in_library} blocchi in libreria.` +
        (r.failed.length ? ` ${r.failed.length} non letti.` : "");
      toast("Libreria Helix pronta");
      await loadStatus();
      $("#helixLibStatus").textContent = `Libreria pronta: ${S.status.helix.blocks_in_library} blocchi dai tuoi export.`;
    };
    poll();
  });
  $("#qcCatalogBtn").addEventListener("click", async () => {
    try {
      const r = await busy("Leggo il catalogo dal Quad Cortex…", () => api("POST", "/api/qc/read-catalog"));
      toast(`Catalogo QC salvato: ${r.models} modelli`);
      await loadStatus(); openSettings();
    } catch {}
  });

  $("#setlistSelect").addEventListener("change", async (e) => {
    const v = e.target.value;
    if (v === "__rename") {
      const n = prompt("Nuovo nome della scaletta", S.setlist.name);
      if (n) { S.setlist.name = n; saveSoon(); setTimeout(() => loadSetlists(S.setlist.id), 700); }
      else e.target.value = S.setlist.id;
    } else if (v === "__delete") {
      if (confirm(`Eliminare la scaletta "${S.setlist.name}" con tutti i suoi brani?`)) {
        await api("DELETE", `/api/setlists/${S.setlist.id}`);
        try { localStorage.removeItem("tb.setlist"); } catch {}
        await loadSetlists();
      } else e.target.value = S.setlist.id;
    } else openSetlist(v);
  });
  $("#newSetlist").addEventListener("click", async () => {
    const n = prompt("Nome della nuova scaletta", "Nuova scaletta");
    if (!n) return;
    const s = await api("POST", "/api/setlists", { name: n });
    await loadSetlists(s.id);
  });
  $("#addSong").addEventListener("click", addSong);
  $("#emptyAdd").addEventListener("click", addSong);
  $("#songList").addEventListener("click", (e) => {
    if (e.target.dataset.del !== undefined) {
      const i = +e.target.dataset.del;
      if (!confirm(`Eliminare "${S.setlist.songs[i].title || "Senza titolo"}"?`)) return;
      S.setlist.songs.splice(i, 1);
      saveSoon();
      openSong(Math.min(i, S.setlist.songs.length - 1));
      return;
    }
    const li = e.target.closest("li[data-i]");
    if (li) openSong(+li.dataset.i);
  });

  ["#songTitle", "#songArtist", "#reqDescription", "#reqGuitar", "#reqPrefs", "#reqSnapshots"].forEach((s) =>
    $(s).addEventListener("change", () => { readRequest(); renderSongs(); saveSoon(); }));
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  const dz = $("#dropZone");
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("over");
    if (e.dataTransfer.files[0]) { $("#audioFile").files = e.dataTransfer.files; $("#audioFile").dispatchEvent(new Event("change")); }
  });
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#audioFile").click(); } });
  $("#audioFile").addEventListener("change", () => {
    const f = $("#audioFile").files[0];
    if (f) { dz.classList.add("has"); $("#dropZone span").textContent = f.name; }
  });
  $("#analyzeBtn").addEventListener("click", () => analyze().catch(() => {}));
  $("#designBtn").addEventListener("click", () => designPreset().catch(() => {}));

  $("#chatForm").addEventListener("submit", sendChat);
  $("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chatForm").requestSubmit(); } });

  $("#helixBtn").addEventListener("click", () => makeHelix().catch(() => {}));
  $("#dupBtn").addEventListener("click", duplicateSong);
  ["#chatInput", "#reqDescription", "#reqSnapshots", "#reqPrefs", "#reqGuitar"].forEach((sel) => attachMic($(sel)));
  $("#qcBtn").addEventListener("click", openQC);
  $("#qcPlanBtn").addEventListener("click", () => qcPlan().catch(() => {}));
  $("#qcConfirm").addEventListener("change", (e) => { $("#qcRunBtn").disabled = !(e.target.checked && S.qcPlan); });
  $("#qcRunBtn").addEventListener("click", () => qcRun().catch(() => {}));
  $("#qcBypassBtn").addEventListener("click", () => qcBypass().catch(() => {}));
  $("#qcClose").addEventListener("click", () => $("#qcDlg").close());
  $("#exportJson").addEventListener("click", () => {
    const r = recipe();
    download(`${(r.name || "preset").replace(/[^\w\- ]+/g, "")}.json`, JSON.stringify(r, null, 2));
  });
  $("#importFile").addEventListener("change", (e) => { if (e.target.files[0]) importPreset(e.target.files[0]).catch(() => {}); e.target.value = ""; });

  bindPreset();
  bindMerge();
}

(async function init() {
  bindAll();
  try {
    await loadStatus();
    await loadSetlists();
  } catch (e) {
    toast(`Avvio non riuscito: ${e.message}`, true);
  }
})();
