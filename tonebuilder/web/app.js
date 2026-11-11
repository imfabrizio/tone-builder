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
const MAX_AMPS = 2;
const ampsInLine = (r, ln) => r.chain.filter((x) => (x.line || 1) === ln && (x.role === "amp" || x.role === "preamp")).length;
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

async function saveNow() {
  clearTimeout(saveTimer);
  if (S.setlist) await api("PUT", `/api/setlists/${S.setlist.id}`, S.setlist);
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

async function openSetlist(id, keepIdx) {
  S.setlist = await api("GET", `/api/setlists/${id}`);
  try { localStorage.setItem("tb.setlist", id); } catch {}
  $("#setlistSelect").value = id;
  S.songIdx = S.setlist.songs.length ? 0 : -1;
  if (keepIdx != null && keepIdx >= 0 && keepIdx < S.setlist.songs.length) S.songIdx = keepIdx;
  renderSongs();
  openSong(S.songIdx);
}

function renderSongs() {
  const list = $("#songList");
  list.innerHTML = S.setlist.songs.map((s, i) => `
    <li class="${i === S.songIdx ? "active" : ""} ${s.kind === "patch" ? "patch" : ""}" data-i="${i}">
      <span class="t">${esc(s.title || "Senza titolo")}<span class="a">${esc(s.artist || "")}</span>${s.uses_patch ? `<span class="uses">Patch: ${esc(s.uses_patch_name || "")}</span>` : ""}</span>
      <span class="dot ${s.recipe || s.uses_patch ? "ready" : ""}" title="${s.recipe ? "Preset pronto" : s.uses_patch ? "Usa una patch generica" : "Da progettare"}"></span>
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
  $("#reqIridium").checked = !!rq.use_iridium;
  $("#reqSnapshots").value = rq.snapshots || "";
  $("#guitarReason").textContent = s.guitar_reason || "";
  renderAnalysis();
  switchTab(s.recipe || s.uses_patch ? "preset" : "design");
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
    use_iridium: S.hasIridium && $("#reqIridium").checked,
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

function renderPatchInfo() {
  const s = song();
  const box = $("#patchInfo");
  if (!s) { box.classList.add("hidden"); return; }
  if (s.kind === "patch") {
    box.innerHTML = `<strong>Patch generica</strong> per ${(s.covers || []).length} brani:<ul>${(s.covers || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
    box.classList.remove("hidden");
  } else if (s.uses_patch) {
    box.innerHTML = `<strong>Questo brano usa la patch generica "${esc(s.uses_patch_name || "")}".</strong>
      ${s.patch_usage ? `<p class="hint">${esc(s.patch_usage)}</p>` : ""}
      <div class="row"><button class="btn" data-open-patch="${esc(s.uses_patch)}">Apri la patch</button>
      ${s.recipe ? "" : `<span class="hint">Se questo brano ha bisogno di una patch sua, usa Progetta.</span>`}</div>`;
    box.classList.remove("hidden");
  } else box.classList.add("hidden");
}

async function renderIridium() {
  const r = recipe();
  const box = $("#iridiumBox");
  if (!r?.iridium) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  $("#iridiumOn").checked = !!r.iridium.enabled;
  if (!r.iridium.enabled) { $("#iridiumGuide").innerHTML = ""; return; }
  try {
    const g = await api("POST", "/api/iridium/guide", { recipe: r });
    if (!g.enabled) { $("#iridiumGuide").innerHTML = ""; return; }
    $("#iridiumGuide").innerHTML = `
      <table class="iridium-table"><tr><th>Snapshot</th><th>Preset Iridium</th><th>Ampli</th><th>Cab</th><th>Manopole</th></tr>
      ${g.rows.map((x) => `<tr><td>${esc(x.snapshot)}</td><td>${x.preset}</td><td>${esc(x.amp)}</td><td>${esc(x.cab)}</td>
        <td>${esc(x.knobs)}${x.notes ? `<div class="hint">${esc(x.notes)}</div>` : ""}</td></tr>`).join("")}</table>
      <details><summary>Guida passo passo: collegamenti, livelli e MIDI</summary>
      <ol class="iridium-steps">${g.steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></details>`;
  } catch (e) { $("#iridiumGuide").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

function renderPreset() {
  const r = recipe();
  renderPatchInfo();
  renderIridium();
  $("#noPreset").classList.toggle("hidden", !!r || !!song()?.uses_patch);
  $("#presetBody").classList.toggle("hidden", !r);
  if (!r) return;
  $("#presetName").value = r.name;
  renderBoard();
  $("#notes").innerHTML = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("") || "<li>Nessuna nota.</li>";
  renderBlockEditor();
  renderSnapEditor();
}

/* pedaliera e matrice: ridisegnabili senza toccare gli editor aperti */
/* cosa ha cambiato l'AI: testo leggibile per ogni differenza */
function changeText(c) {
  const k = (x) => KNOB_IT[x] || x;
  const v = (x) => (x === null || x === undefined ? "—" : x);
  const kv = (name, kn, o, n) => (o === null || o === undefined ? `${name} ${k(kn)} impostato a ${n}`
    : n === null || n === undefined ? `${name} ${k(kn)} torna al valore base (${o} prima)` : `${name} ${k(kn)} ${o} → ${n}`);
  switch (c.type) {
    case "added": return `Aggiunto ${ROLE_IT[c.role] || c.role} "${c.name}" sulla linea ${c.line}`;
    case "removed": return `Tolto ${ROLE_IT[c.role] || c.role} "${c.name}"`;
    case "model": return `${ROLE_IT[c.role] || c.role}: "${c.old}" → "${c.new}"`;
    case "line": return `${c.name}: spostato dalla linea ${c.old} alla linea ${c.new}`;
    case "enabled": return `${c.name}: ${c.new ? "acceso" : "spento"} di base`;
    case "knob": return kv(`${c.name}:`, c.knob, c.old, c.new);
    case "ir": return `${c.name}: IR "${c.old || "nessuno"}" → "${c.new || "nessuno"}"`;
    case "snap_added": return `Nuovo snapshot "${c.snap}"`;
    case "snap_removed": return `Tolto lo snapshot "${c.snap}"`;
    case "snap_enabled": return `Snapshot ${c.snap}: ${c.name} ${c.new ? "acceso" : "spento"}`;
    case "snap_knob": return kv(`Snapshot ${c.snap}: ${c.name}`, c.knob, c.old, c.new);
    case "snap_level": return `Snapshot ${c.snap}: volume ${v(c.old)} → ${v(c.new)} dB`;
    case "rename": return `Nome del preset: "${c.old}" → "${c.new}"`;
    default: return "";
  }
}

function changesHtml(ch, open = false) {
  const items = ch?.items || [];
  if (!items.length) return "";
  return `<details class="changes" ${open ? "open" : ""}><summary>Cosa è cambiato (${items.length})</summary><ul>${
    items.map((c) => `<li>${esc(changeText(c))}</li>`).join("")}</ul></details>`;
}

function renderChangeBar() {
  const s = song();
  const bar = $("#changeBar");
  const n = s?.last_changes?.items?.length || 0;
  bar.classList.toggle("hidden", !n && !s?.prev_recipe);
  if (!n && !s?.prev_recipe) return;
  bar.innerHTML = `<div class="cb-row"><span>${n ? `Ultima modifica dell'AI: ${n} ${n === 1 ? "cambiamento" : "cambiamenti"}, evidenziati qui sotto.` : "Ultima modifica dell'AI."}</span>
    ${s.prev_recipe ? `<button class="btn ghost small" id="undoAi">Annulla la modifica</button>` : ""}
    ${n ? `<button class="btn ghost small" id="clearChg">Togli evidenziazione</button>` : ""}</div>${changesHtml(s.last_changes)}`;
}

function renderBoard() {
  const r = recipe();
  renderChangeBar();
  if (!r) return;
  const chg = song()?.last_changes || {};
  const chgBlocks = new Set(chg.blocks || []);
  const chgCells = new Set((chg.cells || []).map(([b, sn]) => `${b}|${sn}`));
  const fsOf = (id) => (r.footswitches || []).filter((f) => f.block_id === id).map((f) => f.switch).join(" ");
  const pedal = (b) => {
    const knobs = Object.entries(b.settings || {}).slice(0, 5)
      .map(([k, v]) => `<span class="knob">${esc(KNOB_IT[k] || k)} ${esc(v)}</span>`).join("");
    const fs = fsOf(b.id);
    return `<div class="pedal ${b.enabled ? "" : "off"} ${S.selBlock === b.id ? "sel" : ""} ${chgBlocks.has(b.id) ? "changed" : ""}" data-id="${esc(b.id)}"
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
      html += `<td class="${on ? "on" : ""} ${chgCells.has(`${b.id}|${s.name}`) ? "chg" : ""}" data-b="${esc(b.id)}" data-s="${i}" title="${on ? "Acceso" : "Spento"}${ovTxt ? " · " + esc(ovTxt) : ""}">
        ${on ? "ON" : "off"}${ovTxt ? `<span class="ov">${esc(ovTxt.length > 22 ? ovTxt.slice(0, 21) + "…" : ovTxt)}</span>` : ""}</td>`;
    });
    html += "<td style='background:none;cursor:default'></td></tr>";
  }
  $("#matrix").innerHTML = html + "</tbody>";
  renderUses();
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
  const fsOpts = ["", "FS1", "FS2", "FS3", "FS4", "FS5", "FS7", "FS8", "FS9", "FS10", "FS11"]
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
    if ((f === "role" || f === "line") && ampsInLine(r, b.line || 1) > MAX_AMPS) {
      toast(`Al massimo ${MAX_AMPS} ampli per linea: spostalo sull'altra linea o togli un ampli`, true);
      if (f === "role") b.role = "other"; else b.line = b.line === 1 ? 2 : 1;
    }
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

S.mode = (() => { try { return localStorage.getItem("tb.mode") || "dialog"; } catch { return "dialog"; } })();

function setMode(m) {
  S.mode = m;
  try { localStorage.setItem("tb.mode", m); } catch {}
  $$(".mode-seg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  $("#modeHint").textContent = m === "dialog"
    ? "Parla con il tecnico del suono di tutta la scaletta: fa domande, riassume il piano e crea o modifica solo quando gli dai l'ok."
    : "Ogni messaggio modifica subito il preset del brano aperto.";
  $("#chatSend").textContent = m === "dialog" ? "Invia" : "Applica";
  renderAssist();
}

function chatLog() {
  if (S.mode === "dialog") { S.setlist.dialog = S.setlist.dialog || []; return S.setlist.dialog; }
  const s = song();
  if (s) s.chat = s.chat || [];
  return s ? s.chat : [];
}

function renderAssist() {
  const s = song();
  const log = S.setlist ? chatLog() : [];
  $("#chat").innerHTML = log.map((m, i) => `<div class="msg ${m.who} ${m.err ? "err" : ""}">${esc(m.text)}${changesHtml(m.changes, true)}${
    m.pending ? `<div class="confirm-row"><button class="btn primary small" data-confirm="${i}">Sì, esegui: ${esc(m.pendingLabel || "procedi")}</button><button class="btn ghost small" data-dismiss="${i}">No</button></div>`
    : m.confirmed ? `<div class="confirm-note">Confermato: ${esc(m.pendingLabel || "")}</div>` : ""}</div>`).join("")
    || `<p class="hint">${S.mode === "dialog" ? "Esempio: \"ho questa scaletta, voglio al massimo quattro patch: una pulita, una crunch anni '90, una distorta, una per i lenti\"." : "Quando il preset è pronto puoi chiedere modifiche, come faresti con un tecnico del suono."}</p>`;
  const chat = $("#chat");
  chat.scrollTop = chat.scrollHeight;
  $("#researchText").textContent = s?.research || "Ancora nessuna ricerca.";
  const links = [...(s?.sources || []), ...(s?.links || [])];
  $("#sourceLinks").innerHTML = links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a></li>`).join("");
  const w = s?.warnings || [];
  $("#warnings").innerHTML = w.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>Nessun avviso.</li>";
  $("#warnBox").querySelector("summary").textContent = w.length ? `Avvisi (${w.length})` : "Avvisi";
}

/* Indicatore "sta lavorando" dentro una chat: puntini animati e secondi trascorsi.
   Ritorna una funzione che lo toglie. Disattiva i pulsanti passati finche' lavora. */
function working(chatEl, label, buttons = []) {
  const el = document.createElement("div");
  el.className = "msg ai pending";
  el.setAttribute("role", "status");
  el.innerHTML = `<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span> <span class="lbl"></span>`;
  chatEl.appendChild(el);
  chatEl.scrollTop = chatEl.scrollHeight;
  const t0 = Date.now();
  const tick = () => {
    const sec = Math.round((Date.now() - t0) / 1000);
    el.querySelector(".lbl").textContent = `${label}${sec >= 3 ? ` (${sec} s)` : ""}`;
  };
  tick();
  const timer = setInterval(tick, 1000);
  buttons.forEach((b) => b && (b.disabled = true));
  return () => {
    clearInterval(timer);
    el.remove();
    buttons.forEach((b) => b && (b.disabled = false));
  };
}

/* dialogo: risposta dell'AI ed esecuzione dopo l'ok */
const OK_RE = /^\s*(ok|okay|va bene|vai|procedi|fallo|falla|falle|s[iì]|confermo|perfetto|d'accordo|esatto|avanti)\b/i;

async function dialogTurn(text) {
  const log = chatLog();
  // un "ok" breve dopo una proposta esegue direttamente l'azione proposta, senza richiedere all'AI
  const lastAi = [...log].reverse().find((m) => m.who === "ai" && !m.err);
  if (lastAi?.pending && OK_RE.test(text) && text.split(/\s+/).length <= 8) {
    const action = lastAi.pending;
    lastAi.pending = null;
    lastAi.confirmed = true;
    log.push({ who: "me", text });
    renderAssist();
    await executeAction(action);
    return "";
  }
  log.push({ who: "me", text });
  renderAssist();
  saveSoon();
  let res;
  const stop = working($("#chat"), "Ci penso…", [$("#chatSend")]);
  try {
    res = await api("POST", "/api/dialog", { setlist_id: S.setlist.id, current: S.songIdx >= 0 ? S.songIdx : null,
      history: log.filter((m) => !m.err).map(({ who, text }) => ({ who, text })) });
  } catch (err) {
    stop();
    log.push({ who: "ai", text: err.message, err: true });
    renderAssist();
    return "";
  }
  stop();
  const msg = { who: "ai", text: res.reply };
  if (res.pending_action) Object.assign(msg, { pending: res.pending_action, pendingLabel: res.pending_label });
  log.push(msg);
  renderAssist();
  saveSoon();
  if (res.action) await executeAction(res.action);
  return res.reply;
}

/* annulla l'ultima modifica dell'AI / togli l'evidenziazione */
$("#changeBar").addEventListener("click", (e) => {
  const s = song();
  if (!s) return;
  if (e.target.id === "undoAi" && s.prev_recipe) {
    s.recipe = s.prev_recipe;
    s.prev_recipe = null;
    s.last_changes = null;
    (s.chat || (s.chat = [])).push({ who: "ai", text: "Ho annullato l'ultima modifica: il preset è tornato com'era prima." });
    recipeChanged();
    renderAssist();
  } else if (e.target.id === "clearChg") {
    s.last_changes = null;
    renderBoard();
    saveSoon();
  }
});

/* conferma con un clic di un'azione proposta dall'AI */
$("#chat").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-confirm],[data-dismiss]");
  if (!b) return;
  const log = chatLog();
  const m = log[Number(b.dataset.confirm ?? b.dataset.dismiss)];
  if (!m?.pending) return;
  const action = m.pending;
  m.pending = null;
  if (b.dataset.dismiss !== undefined) {
    log.push({ who: "me", text: "No, lascia stare." });
    renderAssist();
    saveSoon();
    return;
  }
  m.confirmed = true;
  log.push({ who: "me", text: `Ok, procedi: ${m.pendingLabel || ""}` });
  renderAssist();
  await executeAction(action);
});

async function executeAction(action) {
  const log = chatLog();
  let res;
  try {
    await saveNow();
    const label = { design_song: "Creo il preset… (circa un minuto)", edit_song: "Modifico il preset…",
      generic_patches: "Preparo le patch generiche…", analyze_youtube: "Avvio l'ascolto dei brani…",
      design_all: "Avvio la creazione dei preset…", edit_many: "Avvio la modifica di tutti i preset…" }[action.type] || "Lo faccio…";
    res = await busy(label, () => api("POST", "/api/dialog/execute", { setlist_id: S.setlist.id, action }));
  } catch (err) {
    log.push({ who: "ai", text: err.message, err: true });
    renderAssist();
    return;
  }
  if (res.job) {
    await pollJob(res.job, { youtube: "Ascolto i brani", "design-all": "Creo i preset", generic: "Creo le patch generiche", "edit-many": "Modifico i preset" }[res.job]);
    log.push({ who: "ai", text: "Fatto. Trovi tutto nella scaletta: dimmi cosa vuoi ritoccare." });
  } else {
    await openSetlist(S.setlist.id, res.song_index);
    switchTab("preset");
    log.push({ who: "ai", text: res.reply || "Fatto: il preset è aggiornato.", changes: res.changes });
  }
  renderAssist();
  saveSoon();
}

/* barra dei lavori in background: resta visibile anche dopo un ricaricamento */
const jobSeen = {};
async function refreshJobBar() {
  const bar = $("#jobBar");
  if (!S.setlist) { bar.classList.add("hidden"); return; }
  let jobs = [];
  try { jobs = await api("GET", `/api/setlists/${S.setlist.id}/jobs`); } catch { return; }
  const rows = [];
  let finished = false;
  for (const j of jobs) {
    const key = `${S.setlist.id}:${j.kind}`;
    if (j.running) {
      jobSeen[key] = "running";
      const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
      rows.push(`<div class="job run"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><div><strong>${esc(j.label)}</strong>: ${j.done} di ${j.total}${j.current ? ` — ${esc(j.current)}` : ""}<div class="jobmeter"><span style="width:${pct}%"></span></div></div></div>`);
    } else if (jobSeen[key] === "running" || jobSeen[key] === "done") {
      if (jobSeen[key] === "running") finished = true;
      jobSeen[key] = "done";
      rows.push(`<div class="job ok"><strong>${esc(j.label)}</strong>: completato, ${j.done} di ${j.total}${j.errors ? `, ${j.errors} problemi` : ""}</div>`);
    }
  }
  bar.innerHTML = rows.join("");
  bar.classList.toggle("hidden", !rows.length);
  if (finished && $("#busy").classList.contains("hidden")) await openSetlist(S.setlist.id, S.songIdx);
}
setInterval(refreshJobBar, 4000);

/* lavori lunghi: avanzamento a schermo */
async function pollJob(kind, label) {
  const url = kind === "design-all" ? `/api/setlists/${S.setlist.id}/design-all` : `/api/jobs/${S.setlist.id}/${kind}`;
  $("#busy").classList.remove("hidden");
  $("#busyText").textContent = `${label}…`;
  refreshJobBar();
  let j;
  for (;;) {
    try { j = await api("GET", url); } catch (e) { j = { running: false, errors: [e.message] }; }
    if (!j.running) break;
    $("#busyText").textContent = `${label}: ${j.done} di ${j.total}${j.current ? " — " + j.current : ""}`;
    await new Promise((r) => setTimeout(r, 2000));
  }
  $("#busy").classList.add("hidden");
  await openSetlist(S.setlist.id, S.songIdx);
  if (j.errors?.length) toast(`Completato con ${j.errors.length} problemi: ${j.errors[0]}`, true);
  else toast(`${label}: fatto`);
  return j;
}

async function sendChat(e) {
  e.preventDefault();
  const s = song();
  const text = $("#chatInput").value.trim();
  if (!text) return;
  if (S.mode === "dialog") {
    $("#chatInput").value = "";
    const reply = await dialogTurn(text);
    if (reply && $("#speakReplies").checked) speak(reply);
    return;
  }
  if (!s?.recipe) { toast("Crea o importa prima un preset", true); return; }
  s.chat.push({ who: "me", text });
  $("#chatInput").value = "";
  renderAssist();
  try {
    const stop = working($("#chat"), "Applico la modifica…", [$("#chatSend")]);
    let res;
    try { res = await api("POST", "/api/edit", { recipe: s.recipe, instruction: text }); } finally { stop(); }
    s.prev_recipe = s.recipe;
    s.recipe = res.recipe;
    s.last_changes = res.changes;
    s.warnings = res.warnings || [];
    s.chat.push({ who: "ai", text: res.reply || "Modifica applicata.", changes: res.changes });
    if (res.research) { s.research = res.research; s.sources = res.sources || []; }
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
const MIC_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11Z"/></svg>';

/* Registrazione con il microfono (funziona su tutti i browser, Firefox compreso).
   stopOnSilence: si ferma da sola dopo una pausa, per la conversazione a voce. */
async function recordAudio({ stopOnSilence = false, maxMs = 60000, onStart, controller } = {}) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mime = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm"].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const done = new Promise((resolve) => { rec.onstop = resolve; });
  let ctx, timer;
  const stop = () => { if (rec.state !== "inactive") rec.stop(); };
  if (controller) controller.stop = stop;
  if (stopOnSilence) {
    ctx = new AudioContext();
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Float32Array(an.fftSize);
    let spoke = false, lastLoud = performance.now();
    const t0 = performance.now();
    timer = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
      const now = performance.now();
      if (rms > 0.02) { spoke = true; lastLoud = now; }
      if ((spoke && now - lastLoud > 1500) || (!spoke && now - t0 > 8000)) stop();
    }, 100);
  }
  rec.start();
  onStart?.();
  const hard = setTimeout(stop, maxMs);
  await done;
  clearTimeout(hard);
  clearInterval(timer);
  ctx?.close();
  stream.getTracks().forEach((t) => t.stop());
  return new Blob(chunks, { type: rec.mimeType || "audio/webm" });
}

async function transcribe(blob) {
  if (blob.size < 2000) return "";
  const fd = new FormData();
  fd.append("file", blob, blob.type.includes("ogg") ? "voce.ogg" : "voce.webm");
  const res = await api("POST", "/api/voice/transcribe", fd);
  return res.text || "";
}

/* Un'unica frase: Web Speech su Chrome/Edge, altrimenti registrazione + Gemini */
function listenOnce(controller) {
  if (SpeechRec) {
    return new Promise((resolve, reject) => {
      const rec = new SpeechRec();
      rec.lang = "it-IT";
      rec.interimResults = false;
      rec.continuous = false;
      let text = "";
      controller.stop = () => rec.abort();
      rec.onresult = (ev) => { text = [...ev.results].map((r) => r[0].transcript).join(" "); };
      rec.onerror = (ev) => (ev.error === "no-speech" || ev.error === "aborted" ? resolve("") : reject(new Error(ev.error === "not-allowed" ? "Consenti l'uso del microfono al browser" : ev.error)));
      rec.onend = () => resolve(text);
      rec.start();
    });
  }
  return recordAudio({ stopOnSilence: true, controller }).then(transcribe);
}

let voices = [];
function loadVoices() { voices = window.speechSynthesis?.getVoices() || []; }
loadVoices();
if (window.speechSynthesis) speechSynthesis.onvoiceschanged = loadVoices;

function speak(text) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis || !text) { resolve(); return; }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "it-IT";
    u.voice = voices.find((v) => v.lang?.startsWith("it") && /natural|online|google/i.test(v.name)) || voices.find((v) => v.lang?.startsWith("it")) || null;
    u.rate = 1.05;
    u.onend = resolve;
    u.onerror = resolve;
    speechSynthesis.speak(u);
  });
}

/* Conversazione a voce: ascolta, risponde a voce, riascolta. Si ferma con il pulsante. */
const talk = { on: false, ctl: {} };
function setTalkState(st) {
  const b = $("#talkBtn");
  b.classList.remove("listening", "thinking", "speaking");
  if (st) b.classList.add(st);
  b.textContent = { listening: "Ti ascolto…", thinking: "Ci penso…", speaking: "Parlo…" }[st] || "Parla";
}

async function talkLoop() {
  if (talk.on) {
    talk.on = false;
    talk.ctl.stop?.();
    window.speechSynthesis?.cancel();
    setTalkState(null);
    return;
  }
  if (S.mode !== "dialog") setMode("dialog");
  talk.on = true;
  let empty = 0;
  while (talk.on) {
    setTalkState("listening");
    let text = "";
    try { text = (await listenOnce(talk.ctl)).trim(); }
    catch (e) { toast(e.message, true); break; }
    if (!talk.on) break;
    if (!text) { if (++empty >= 2) break; continue; }
    empty = 0;
    setTalkState("thinking");
    const reply = await dialogTurn(text);
    if (!talk.on) break;
    setTalkState("speaking");
    await speak(reply);
  }
  talk.on = false;
  setTalkState(null);
}

/* Dettatura nei campi di testo */
function attachMic(field) {
  if (!field || !(SpeechRec || navigator.mediaDevices?.getUserMedia)) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn icon mic";
  btn.title = "Detta a voce";
  btn.setAttribute("aria-label", "Detta a voce");
  btn.innerHTML = MIC_SVG;
  const wrap = document.createElement("div");
  wrap.className = "with-mic";
  field.parentNode.insertBefore(wrap, field);
  wrap.append(field, btn);
  const ctl = {};
  let active = false;
  btn.addEventListener("click", async () => {
    if (active) { ctl.stop?.(); return; }
    active = true;
    btn.classList.add("rec");
    const base = field.value ? field.value.replace(/\s*$/, " ") : "";
    try {
      if (SpeechRec) {
        await new Promise((resolve) => {
          const rec = new SpeechRec();
          rec.lang = "it-IT"; rec.interimResults = true; rec.continuous = true;
          let finalText = "";
          ctl.stop = () => rec.stop();
          rec.onresult = (ev) => {
            let interim = "";
            for (let i = ev.resultIndex; i < ev.results.length; i++) {
              const t = ev.results[i][0].transcript;
              if (ev.results[i].isFinal) finalText += t; else interim += t;
            }
            field.value = base + finalText + interim;
          };
          rec.onerror = (ev) => { if (ev.error === "not-allowed") toast("Consenti l'uso del microfono al browser", true); };
          rec.onend = resolve;
          rec.start();
        });
      } else {
        toast("Parla, poi premi di nuovo il microfono per fermare");
        const blob = await recordAudio({ controller: ctl, maxMs: 120000 });
        btn.classList.remove("rec");
        const text = await busy("Trascrivo…", () => transcribe(blob));
        field.value = base + text;
      }
    } catch (e) { toast(e.message, true); }
    active = false;
    btn.classList.remove("rec");
    field.dispatchEvent(new Event("change"));
  });
}

/* ---------------------------------------------------------------- YouTube e patch generiche */

async function ytAll() {
  const n = S.setlist.songs.filter((x) => x.kind !== "patch" && x.title && !x.audio?.analysis).length;
  if (!n) { toast("Tutti i brani sono già stati ascoltati"); return; }
  if (!confirm(`Cerco su YouTube e ascolto ${n} brani (circa 1-3 minuti a brano). L'audio è scaricato per uso personale. Procedo?`)) return;
  try {
    await saveNow();
    await api("POST", `/api/setlists/${S.setlist.id}/youtube`);
    await pollJob("youtube", "Ascolto i brani da YouTube");
  } catch (e) { toast(e.message, true); }
}

S.reduceProposal = null;
function openReduce() {
  S.reduceProposal = null;
  S.reduceChat = [];
  $("#reduceResult").innerHTML = "";
  S.reduceChat = [{ who: "ai", text: "Dimmi come vuoi raggruppare i brani, a voce o scrivendo, oppure premi Proponi i gruppi e decido io." }];
  renderReduceChat();
  $("#reduceCreate").classList.add("hidden");
  $("#reduceDlg").showModal();
}

async function makeProposal(notes) {
  await saveNow();
  S.reduceProposal = await api("POST", `/api/setlists/${S.setlist.id}/group-proposal`,
    { max_patches: +$("#reduceMax").value || 4, notes, use_iridium: S.hasIridium && $("#reduceIridium").checked });
  renderProposal();
}

function renderReduceChat() {
  const c = $("#reduceChat");
  c.innerHTML = S.reduceChat.map((m) => `<div class="msg ${m.who} ${m.err ? "err" : ""}">${esc(m.text)}</div>`).join("");
  c.scrollTop = c.scrollHeight;
}

/* un turno di dialogo sulla proposta: ritorna la risposta; se l'utente da' l'ok crea le patch */
async function reduceTurn(text) {
  S.reduceChat.push({ who: "me", text });
  renderReduceChat();
  if (!S.reduceProposal) {  // primo messaggio: diventa l'indicazione per la proposta
    const notes = [$("#reduceNotes").value.trim(), text].filter(Boolean).join(". ");
    const stopP = working($("#reduceChat"), "Raggruppo i brani…", [$("#reduceSend")]);
    try {
      await makeProposal(notes);
      stopP();
    } catch (e) {
      stopP();
      S.reduceChat.push({ who: "ai", text: e.message, err: true });
      renderReduceChat();
      return "";
    }
    const n = S.reduceProposal.patches.length;
    const reply = `Ecco la proposta: ${n} patch, ${S.reduceProposal.patches.map((p) => p.name).join(", ")}. Dimmi cosa cambiare, oppure di' ok e le creo.`;
    S.reduceChat.push({ who: "ai", text: reply });
    renderReduceChat();
    return reply;
  }
  let res;
  const stopR = working($("#reduceChat"), "Aggiorno la proposta…", [$("#reduceSend")]);
  try {
    res = await api("POST", `/api/setlists/${S.setlist.id}/group-revise`, {
      proposal: S.reduceProposal, max_patches: +$("#reduceMax").value || 4,
      history: S.reduceChat.filter((m) => !m.err).map(({ who, text }) => ({ who, text })) });
  } catch (e) {
    stopR();
    S.reduceChat.push({ who: "ai", text: e.message, err: true });
    renderReduceChat();
    return "";
  }
  stopR();
  S.reduceProposal = res.proposal;
  S.reduceChat.push({ who: "ai", text: res.reply });
  renderProposal();
  renderReduceChat();
  if (res.go) {
    if ($("#speakReplies").checked) await speak(res.reply);
    reduceTalk.on = false;
    await createGeneric();
    return "";
  }
  return res.reply;
}

async function createGeneric() {
  if (!S.reduceProposal) return;
  $("#reduceDlg").close();
  try {
    await saveNow();
    S.reduceProposal.use_iridium = S.hasIridium && $("#reduceIridium").checked;
    await api("POST", `/api/setlists/${S.setlist.id}/generic-patches`, { proposal: S.reduceProposal });
    await pollJob("generic", "Creo le patch generiche");
  } catch (e) { toast(e.message, true); }
}

const reduceTalk = { on: false, ctl: {} };
async function reduceTalkLoop() {
  const b = $("#reduceTalkBtn");
  const state = (st) => { b.classList.remove("listening", "speaking"); if (st) b.classList.add(st);
    b.textContent = { listening: "Ti ascolto…", speaking: "Parlo…", thinking: "Ci penso…" }[st] || "Parla"; };
  if (reduceTalk.on) { reduceTalk.on = false; reduceTalk.ctl.stop?.(); window.speechSynthesis?.cancel(); state(null); return; }
  reduceTalk.on = true;
  let empty = 0;
  while (reduceTalk.on && $("#reduceDlg").open) {
    state("listening");
    let text = "";
    try { text = (await listenOnce(reduceTalk.ctl)).trim(); } catch (e) { toast(e.message, true); break; }
    if (!reduceTalk.on) break;
    if (!text) { if (++empty >= 2) break; continue; }
    empty = 0;
    state("thinking");
    const reply = await reduceTurn(text);
    if (!reduceTalk.on || !reply) break;
    state("speaking");
    await speak(reply);
  }
  reduceTalk.on = false;
  state(null);
}

function renderProposal() {
  const p = S.reduceProposal;
  const songs = S.setlist.songs;
  $("#reduceResult").innerHTML = (p.notes ? `<p class="hint">${esc(p.notes)}</p>` : "") + p.patches.map((g) => `
    <div class="group-card"><h3>${esc(g.name)}</h3><div class="hint">${esc(g.description)}</div>
      <div class="hint">Snapshot: ${esc(g.snapshots)}${g.guitar ? ` · Chitarra: ${esc(g.guitar)}` : ""}</div>
      <ul>${g.songs.map((r) => `<li>${esc(songs[r.index]?.title || "?")}${r.usage ? ` — ${esc(r.usage)}` : ""}</li>`).join("")}</ul></div>`).join("");
  $("#reduceCreate").classList.remove("hidden");
}

function bindReduce() {
  $("#ytAll").addEventListener("click", ytAll);
  $("#openReduce").addEventListener("click", openReduce);
  $("#reduceClose").addEventListener("click", () => $("#reduceDlg").close());
  $("#reducePropose").addEventListener("click", async () => {
    try {
      await saveNow();
      await busy("Raggruppo i brani…", () => makeProposal($("#reduceNotes").value.trim()));
      S.reduceChat.push({ who: "ai", text: "Ecco la mia proposta. Dimmi cosa cambiare, oppure di' ok e le creo." });
      renderReduceChat();
    } catch {}
  });
  $("#reduceCreate").addEventListener("click", createGeneric);
  $("#reduceSend").addEventListener("click", async () => {
    const t = $("#reduceInput").value.trim();
    if (!t) return;
    $("#reduceInput").value = "";
    const reply = await reduceTurn(t);
    if (reply && $("#speakReplies").checked) speak(reply);
  });
  $("#reduceInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#reduceSend").click(); } });
  $("#reduceTalkBtn").addEventListener("click", reduceTalkLoop);
  $("#reduceDlg").addEventListener("close", () => { reduceTalk.on = false; reduceTalk.ctl.stop?.(); window.speechSynthesis?.cancel(); });
  attachMic($("#reduceInput"));
  attachMic($("#reduceNotes"));
  $("#patchInfo").addEventListener("click", (e) => {
    const id = e.target.dataset.openPatch;
    if (!id) return;
    const i = S.setlist.songs.findIndex((x) => x.id === id);
    if (i >= 0) openSong(i);
  });
  $$(".mode-seg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("#talkBtn").addEventListener("click", talkLoop);
  setMode(S.mode);
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
  $("#spClientId").value = st.spotify_client_id || "";
  $("#spRedirect").textContent = `${location.origin}/api/spotify/callback`;
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
    spotify_client_id: $("#spClientId").value.trim(),
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


/* ---------------------------------------------------------------- strumentazione */

const POSITIONS = { "HH + Piezo": ["manico", "ponte", "piezo"], SSS: ["manico", "centrale", "ponte"], HSS: ["manico", "centrale", "ponte"], HSH: ["manico", "centrale", "ponte"],
  HH: ["manico", "ponte"], SS: ["manico", "ponte"], H: ["ponte"], P90: ["ponte"], "P90 P90": ["manico", "ponte"],
  "Mini-HH": ["manico", "ponte"], Piezo: ["piezo"], Altro: ["manico", "ponte"] };
S.gear = null;

async function openGear() {
  S.gear = await api("GET", "/api/gear");
  renderGear();
  $("#gearOther").value = S.gear.other || "";
  $("#gearWah").checked = !!S.gear.wah_external;
  $("#gearIridium").checked = !!S.gear.iridium;
  $("#gearIrCh").value = S.gear.iridium_midi_channel || 1;
  $("#gearIrFirst").value = S.gear.iridium_first_preset ?? 10;
  $("#gearDlg").showModal();
}

function renderGear() {
  const g = S.gear;
  $("#gearList").innerHTML = g.guitars.length ? g.guitars.map((gt, i) => {
    const pos = POSITIONS[gt.config] || ["manico", "ponte"];
    return `<div class="guitar-card ${gt.active === false ? "reserve" : ""}" data-i="${i}">
      <div class="editor-head"><h3>${esc(gt.name || "Nuova chitarra")}</h3><button class="btn ghost" data-rm-guitar="${i}">Rimuovi</button></div>
      <div class="editor-grid">
        <div class="field"><label>Modello</label><input data-g="name" list="guitarOptions" value="${esc(gt.name)}" placeholder="Es. Fender Stratocaster"></div>
        <div class="field"><label>Configurazione pickup</label><select data-g="config"><option value="">—</option>${g.configs.map((c) => `<option ${c === gt.config ? "selected" : ""}>${c}</option>`).join("")}</select></div>
        <div class="field"><label>Accordatura</label><input data-g="tuning" value="${esc(gt.tuning)}"></div>
        ${pos.map((p) => `<div class="field"><label>Pickup ${p}</label><input data-pk="${p}" list="pickupOptions" value="${esc(gt.pickups?.[p] || "")}" placeholder="Modello o descrizione"></div>`).join("")}
        <div class="field"><label>Note</label><input data-g="notes" value="${esc(gt.notes)}" placeholder="Es. corde 10-46, push-pull coil split"></div>
        <div class="field" style="grid-column: span 2"><label>Da usare per</label><input data-g="use" value="${esc(gt.use || "")}" placeholder="Es. pezzi pop e puliti / acustici col piezo e pezzi duri"></div>
        <label class="check"><input type="checkbox" data-g-active ${gt.active === false ? "" : "checked"}> In uso (togli la spunta se è di riserva)</label>
      </div></div>`;
  }).join("") : `<p class="hint">Nessuna chitarra ancora: aggiungi quelle che porti sul palco.</p>`;
}

let gearSearchT;
function applyGearFlags(g) {
  S.hasIridium = !!g?.iridium;
  $$(".iridium-only").forEach((el) => el.classList.toggle("hidden", !S.hasIridium));
}

function bindGear() {
  $("#gearImport").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("merge", "true");
    try {
      await busy("Importo la strumentazione…", () => api("POST", "/api/gear/import", fd));
      await openGear();
      applyGearFlags(S.gear);
      toast("Strumentazione importata");
    } catch {}
  });
  $("#openGear").addEventListener("click", () => openGear().catch((e) => toast(e.message, true)));
  $("#gearClose").addEventListener("click", () => $("#gearDlg").close());
  $("#addGuitar").addEventListener("click", () => {
    S.gear.guitars.push({ name: "", config: "", pickups: {}, tuning: "standard (E)", notes: "" });
    renderGear();
    $$("#gearList [data-g=name]").slice(-1)[0]?.focus();
  });
  $("#gearList").addEventListener("click", (e) => {
    if (e.target.dataset.rmGuitar !== undefined) { S.gear.guitars.splice(+e.target.dataset.rmGuitar, 1); renderGear(); }
  });
  $("#gearList").addEventListener("input", (e) => {
    const card = e.target.closest(".guitar-card");
    if (!card) return;
    const gt = S.gear.guitars[+card.dataset.i];
    if (e.target.dataset.g) gt[e.target.dataset.g] = e.target.value;
    if (e.target.dataset.pk) { gt.pickups = gt.pickups || {}; gt.pickups[e.target.dataset.pk] = e.target.value; }
    const q = e.target.value.trim();
    const kind = e.target.dataset.g === "name" ? "guitar" : e.target.dataset.pk ? "pickup" : "";
    if (!kind || q.length < 2) return;
    clearTimeout(gearSearchT);
    gearSearchT = setTimeout(async () => {
      try {
        const res = await api("GET", `/api/gear/search?kind=${kind}&q=${encodeURIComponent(q)}`);
        $(kind === "guitar" ? "#guitarOptions" : "#pickupOptions").innerHTML =
          res.map((r) => `<option value="${esc(r.name)}">${esc(r.description)}</option>`).join("");
        if (kind === "guitar") {
          const hit = res.find((r) => r.name === q);
          if (hit) gt.wikidata_id = hit.wikidata_id;
        }
      } catch {}
    }, 300);
  });
  $("#gearList").addEventListener("change", (e) => {
    const card = e.target.closest(".guitar-card");
    if (card && e.target.dataset.g === "config") { S.gear.guitars[+card.dataset.i].config = e.target.value; renderGear(); }
    if (card && e.target.matches("[data-g-active]")) { S.gear.guitars[+card.dataset.i].active = e.target.checked; renderGear(); }
  });
  $("#gearSave").addEventListener("click", async () => {
    S.gear.other = $("#gearOther").value;
    S.gear.wah_external = $("#gearWah").checked;
    S.gear.iridium = $("#gearIridium").checked;
    S.gear.iridium_midi_channel = Math.min(16, Math.max(1, +$("#gearIrCh").value || 1));
    S.gear.iridium_first_preset = Math.min(119, Math.max(0, +$("#gearIrFirst").value || 0));
    S.gear.guitars = S.gear.guitars.filter((g) => g.name.trim());
    try {
      const { configs, ...body } = S.gear;
      await busy("Salvo…", () => api("PUT", "/api/gear", body));
      applyGearFlags(S.gear);
      $("#gearDlg").close();
      toast("Strumentazione salvata");
    } catch {}
  });
}

/* ---------------------------------------------------------------- import scaletta e automazioni */

async function openImport() {
  $("#importText").value = "";
  $("#spotifyLink").value = "";
  $("#importName").value = "";
  $("#importAppend").checked = false;
  const st = await api("GET", "/api/spotify/status");
  $("#spotifyConnect").textContent = st.connected ? "Ricollega" : "Collega Spotify";
  $("#spotifyConnect").disabled = !st.client_id;
  $("#spotifyStatus").textContent = !st.client_id
    ? "Per usare Spotify inserisci prima il Client ID in Impostazioni > Spotify. Intanto puoi incollare un elenco qui sotto."
    : st.connected ? "Spotify collegato: scegli una playlist." : "Premi Collega Spotify e autorizza l'accesso alle tue playlist.";
  const sel = $("#spotifyPlaylists");
  sel.innerHTML = `<option value="">Scegli una tua playlist…</option>`;
  if (st.connected) {
    try {
      const pls = await api("GET", "/api/spotify/playlists");
      sel.innerHTML += pls.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}${p.count ? ` (${p.count})` : ""}</option>`).join("");
    } catch (e) { $("#spotifyStatus").textContent = e.message; }
  }
  $("#importDlg").showModal();
}

async function runImport() {
  const playlist = $("#spotifyLink").value.trim() || $("#spotifyPlaylists").value;
  const text = $("#importText").value.trim();
  if (!playlist && !text) { toast("Scegli una playlist o incolla un elenco", true); return; }
  const body = { playlist, text, name: $("#importName").value.trim() || ($("#spotifyPlaylists").selectedOptions[0]?.textContent.replace(/ \(\d+\)$/, "") || ""),
    setlist_id: $("#importAppend").checked ? S.setlist.id : "" };
  if (!playlist) body.name = body.name.startsWith("Scegli") ? "" : body.name;
  try {
    const data = await busy("Importo i brani…", () => api("POST", "/api/setlists/import", body));
    $("#importDlg").close();
    await loadSetlists(data.id);
    toast(`${data.songs.length} brani nella scaletta "${data.name}"`);
  } catch {}
}

async function suggestGuitars() {
  try {
    S.setlist = await busy("Scelgo la chitarra per ogni brano…", () => api("POST", `/api/setlists/${S.setlist.id}/suggest-guitars`));
    renderSongs();
    openSong(Math.max(0, S.songIdx));
    toast("Chitarre proposte: le trovi in Progetta, campo Chitarra");
  } catch {}
}

/* ---------------------------------------------------------------- tutta la scaletta */

const USE_ORDER = ["amp", "preamp", "cab", "ir", "drive", "comp", "gate", "eq", "mod", "delay", "reverb", "pitch", "filter", "synth", "volume", "fxloop", "other"];

function overviewCell(blocks, roles) {
  const xs = blocks.filter((b) => roles.includes(b.role));
  return xs.map((b) => b.role === "ir" ? `IR ${esc(b.ir || b.name)}`
    : `${esc(b.name)}${b.ir ? ` <small>IR ${esc(b.ir)}</small>` : ""}`).join("<br>") || "—";
}

async function renderWhole() {
  const rows = await api("GET", `/api/setlists/${S.setlist.id}/overview`);
  const head = `<thead><tr><th>Brano / patch</th><th>Ampli</th><th>Cab / IR</th><th>Drive e dinamica</th><th>Effetti</th><th>Snapshot</th></tr></thead>`;
  $("#wholeOverview").innerHTML = head + "<tbody>" + rows.map((r) => {
    const title = `${r.kind === "patch" ? "<strong>Patch</strong> " : ""}${esc(r.title || "senza titolo")}${r.artist && r.kind !== "patch" ? ` <small>${esc(r.artist)}</small>` : ""}`;
    if (!r.has_recipe) {
      return `<tr><td><button class="link" data-goto="${r.index}">${title}</button></td><td colspan="5" class="muted">${r.uses_patch_name ? `usa la patch ${esc(r.uses_patch_name)}` : "nessun preset"}</td></tr>`;
    }
    const b = r.blocks;
    return `<tr><td><button class="link" data-goto="${r.index}">${title}</button></td>
      <td>${overviewCell(b, ["amp", "preamp", "fxloop"])}</td><td>${overviewCell(b, ["cab", "ir"])}</td>
      <td>${overviewCell(b, ["drive", "comp", "gate", "eq"])}</td><td>${overviewCell(b, ["mod", "delay", "reverb", "pitch", "filter", "synth"])}</td>
      <td>${esc((r.snapshots || []).join(", "))}</td></tr>`;
  }).join("") + "</tbody>";
  try {
    const ir = await api("GET", "/api/irs");
    $("#wholeIrs").textContent = ir.count
      ? `Ho trovato ${ir.count} tuoi IR nelle cartelle impostate: se chiedi di usarli, scelgo tra questi.`
      : "Non ho trovato tuoi IR: per farli usare indica la cartella con i file .wav in Impostazioni, Cartelle con IR.";
  } catch {}
}

async function openWhole() {
  if (!S.setlist) { toast("Apri prima una scaletta", true); return; }
  await saveNow();
  $("#wholeDlg").showModal();
  await renderWhole();
}

async function wholeCreate() {
  const redo = $("#wholeRedo").checked;
  const n = S.setlist.songs.filter((s) => (redo || !s.recipe) && !s.uses_patch && s.kind !== "patch").length;
  if (!n) { toast("Tutti i brani hanno già un preset o una patch generica: per rifarli spunta Rifai da zero", true); return; }
  if (!confirm(redo ? `Rifaccio da zero i preset di ${n} brani (i preset attuali restano annullabili). Procedo?`
    : `Creo i preset per ${n} brani, uno dopo l'altro (circa 1 minuto a brano). Procedo?`)) return;
  await saveNow();
  await api("POST", `/api/setlists/${S.setlist.id}/design-all`, { description: $("#wholeDesc").value.trim(), redo });
  $("#wholeDlg").close();
  await pollJob("design-all", "Creo i preset");
}

async function wholeEdit() {
  const instruction = $("#wholeEdit").value.trim();
  if (!instruction) { toast("Scrivi cosa cambiare", true); return; }
  await saveNow();
  await api("POST", `/api/setlists/${S.setlist.id}/edit-many`, { instruction, target: $("#wholeTarget").value });
  $("#wholeDlg").close();
  await pollJob("edit-many", "Modifico i preset");
}

async function wholeZip() {
  await saveNow();
  await busy("Creo i file Helix di tutta la scaletta…", async () => {
    const r = await fetch(`/api/setlists/${S.setlist.id}/helix-zip`, { method: "POST" });
    if (!r.ok) { let m = `Errore ${r.status}`; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${S.setlist.name || "scaletta"} - Helix.zip`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    const probs = Number(r.headers.get("X-Problems") || 0);
    toast(`Creati ${r.headers.get("X-Created")} file${probs ? `, ${probs} avvisi: li trovi in LEGGIMI-avvisi.txt dentro lo zip` : ""}`, probs > 0);
  });
}

/* tabella "cosa usa questo preset" */
function renderUses() {
  const r = recipe();
  const t = $("#usesTable");
  if (!r) { t.innerHTML = ""; return; }
  const chg = new Set(song()?.last_changes?.blocks || []);
  const rows = [...r.chain].sort((a, b) => (a.line || 1) - (b.line || 1));
  t.innerHTML = `<thead><tr><th>Linea</th><th>Tipo</th><th>Modello</th><th>Helix</th><th>Quad Cortex</th><th>IR</th><th>Di base</th></tr></thead><tbody>${
    rows.map((b) => `<tr class="${chg.has(b.id) ? "chg" : ""}"><td>${b.line || 1}</td><td>${esc(ROLE_IT[b.role] || b.role)}</td>
      <td>${esc(b.gear_ref || b.label || b.id)}</td><td>${esc(b.helix?.model || "—")}</td><td>${esc(b.qc?.model || "—")}</td>
      <td>${esc(b.ir || "—")}</td><td>${b.enabled ? "acceso" : "spento"}</td></tr>`).join("")}</tbody>`;
}

async function designAll() {
  const missing = S.setlist.songs.filter((s) => !s.recipe && !s.uses_patch && s.kind !== "patch").length;
  if (!missing) { toast("Tutti i brani hanno già un preset o una patch generica"); return; }
  if (!confirm(`Creo i preset per ${missing} brani, uno dopo l'altro (circa 1 minuto a brano). Procedo?`)) return;
  await saveNow();
  await api("POST", `/api/setlists/${S.setlist.id}/design-all`, {});
  await pollJob("design-all", "Creo i preset");
}

function bindSetlistTools() {
  $("#openImport").addEventListener("click", () => openImport().catch((e) => toast(e.message, true)));
  $("#importClose").addEventListener("click", () => $("#importDlg").close());
  $("#importRun").addEventListener("click", runImport);
  $("#spotifyConnect").addEventListener("click", () => { location.href = "/api/spotify/login"; });
  $("#suggestGuitars").addEventListener("click", suggestGuitars);
  $("#openWhole").addEventListener("click", () => openWhole().catch((e) => toast(e.message, true)));
  $("#wholeClose").addEventListener("click", () => $("#wholeDlg").close());
  $("#wholeCreate").addEventListener("click", () => wholeCreate().catch((e) => toast(e.message, true)));
  $("#wholeEditRun").addEventListener("click", () => wholeEdit().catch((e) => toast(e.message, true)));
  $("#wholeZip").addEventListener("click", () => wholeZip().catch((e) => toast(e.message, true)));
  $("#wholeOverview").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-goto]");
    if (!b) return;
    $("#wholeDlg").close();
    await openSetlist(S.setlist.id, Number(b.dataset.goto));
    switchTab("preset");
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

  ["#songTitle", "#songArtist", "#reqDescription", "#reqGuitar", "#reqPrefs", "#reqSnapshots", "#reqIridium"].forEach((s) =>
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
  bindGear();
  bindSetlistTools();
  bindReduce();
  $("#iridiumOn").addEventListener("change", (e) => {
    const r = recipe();
    if (r?.iridium) { r.iridium.enabled = e.target.checked; recipeChanged(); }
  });
}

(async function init() {
  bindAll();
  try {
    await loadStatus();
    try { applyGearFlags(await api("GET", "/api/gear")); } catch {}
    await loadSetlists();
    const sp = new URLSearchParams(location.search).get("spotify");
    if (sp) {
      history.replaceState(null, "", "/");
      if (sp === "ok") { toast("Spotify collegato"); openImport(); }
      else toast("Collegamento a Spotify non riuscito: controlla Client ID e Redirect URI", true);
    }
  } catch (e) {
    toast(`Avvio non riuscito: ${e.message}`, true);
  }
})();
