// Einstellungen → Stella: Gemini Live (eine Stimme von 21) oder die eigene Kette (Cloudflare oder Google erkennt, Claude antwortet, Piper spricht)
import { api } from "../../api.js";
import { state } from "../../store.js";
import { $, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

let currentAudio = null;
let currentPlayingId = null;
const SPEEDS = [0.9, 1.0, 1.1, 1.2];
const QUALITY = { x_low: "sehr einfach", low: "einfach", medium: "mittel", high: "hoch" };

export async function render(el) {
  el.innerHTML = subHead("Stella", "/einstellungen") + `
    <div class="srow" id="liveApp" hidden style="margin-bottom:12px;">
      <span class="smain"><b>Live-App</b><small>Eigene App nur fürs Sprechen mit Stella. Link öffnen, dann im Browser „App installieren“ wählen.</small></span>
      <a class="es-btn primary" id="liveAppLink" target="_blank" rel="noopener"><span class="ms">install_mobile</span>Öffnen</a>
    </div>
    <div id="stellaLoading" class="sub-intro">Lade …</div>
    <div id="stellaEngine"></div>
    <div id="stellaContent" style="display:none"></div>`;

  api.get("/voice/app-link").then((l) => { $("liveAppLink").href = l.url; $("liveApp").hidden = false; }).catch(() => {});
  let data;
  try {
    data = await api.get("/voice/settings");
  } catch (err) {
    if ($("stellaLoading")) $("stellaLoading").textContent = "Fehler beim Laden: " + err.message;
    return;
  }
  $("stellaLoading").style.display = "none";
  let eng = data.engine;
  let piperVoices = null;

  const save = async (patch) => {
    try {
      const r = await api.patch("/voice/settings", patch);
      eng = r.engine || eng;
      state.boot.voice_engine = eng.engine;
      draw();
    } catch (err) { toast(`Nicht gespeichert: ${err.message}`); }
  };

  const loadPiper = async () => {
    if (piperVoices || !eng.status.piper) return;
    try { piperVoices = (await api.get("/voice/piper/voices?lang=de")).voices || []; }
    catch (err) { piperVoices = []; toast(err.message); }
    draw();
  };

  const draw = () => {
    const box = $("stellaEngine");
    if (!box) return;
    const pipe = eng.engine === "pipeline";
    const sttProv = eng.stt_models.find((m) => m.id === eng.stt)?.provider || "cloudflare";
    const missing = [sttProv === "google" && !eng.status.google && "Google-Zugang: <code>GOOGLE_STT_API_KEY</code> (API-Schlüssel eines Google-Cloud-Projekts mit aktivierter „Cloud Speech-to-Text API“) in der .env",
      sttProv === "cloudflare" && !eng.status.cloudflare && "Cloudflare-Zugang: <code>CLOUDFLARE_AI_ACCOUNT_ID</code> und <code>CLOUDFLARE_AI_TOKEN</code> (API-Token mit dem Recht „Workers AI“) in der .env",
      !eng.status.piper && "Piper-Dienst: <code>PIPER_URL</code> (im Stack <code>http://piper:8000</code>)"].filter(Boolean);
    box.innerHTML = `
      <p class="sub-intro">Wie Stella zuhört, denkt und spricht – im Live-Gespräch, am Lautsprecher und am Telefon.</p>
      <div class="seg" id="sEng">${eng.engines.map((e) => `<button type="button" data-e="${e.id}" class="${e.id === eng.engine ? "on" : ""}">${esc(e.name)}</button>`).join("")}</div>
      <div class="rf-sum"><span class="ms">info</span><span>${esc(eng.engines.find((e) => e.id === eng.engine)?.description || "")}</span></div>
      ${pipe && missing.length ? `<div class="rf-sum" style="margin-top:8px"><span class="ms">warning</span><span>Für die eigene Kette fehlt noch: ${missing.join("; ")}. Bis dahin kann Stella so nicht sprechen.</span></div>` : ""}
      ${pipe ? `
      <div class="sgrp" style="margin-top:18px">1 · Zuhören (Cloudflare Workers AI oder Google)</div>
      <div class="voice-grid">${eng.stt_models.map((m) => `
        <div class="voice-card ${m.id === eng.stt ? "active" : ""}" data-stt="${esc(m.id)}" style="cursor:pointer">
          <div class="voice-info"><div class="voice-head"><span class="voice-name">${esc(m.name)}</span><span class="voice-gender">${esc(m.cost || `${m.neurons} Neurons/Min.`)}</span></div>
          <span class="voice-desc">${esc(m.description)}</span></div>
          <span class="ms">${m.id === eng.stt ? "check_circle" : "radio_button_unchecked"}</span>
        </div>`).join("")}</div>
      <div class="sgrp" style="margin-top:18px">2 · Antworten (Claude über deine Accounts)</div>
      <label class="fld"><span>Modell (Standard: Haiku – schnell und sparsam)</span><select id="sLlm">${eng.llm_models.map((m) => `<option value="${esc(m.id)}" ${m.id === eng.llm ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label>
      <div class="sgrp" style="margin-top:18px">3 · Sprechen</div>
      <div class="seg" id="sTts">${eng.tts_engines.map((t) => `<button type="button" data-t="${t.id}" class="${t.id === eng.tts ? "on" : ""}">${esc(t.name)}</button>`).join("")}</div>
      <div class="srow"><span class="smain"><b>Tempo</b><small>1,0 = normal</small></span><div class="seg" id="sSpeed" style="margin:0">${SPEEDS.map((v) => `<button type="button" data-v="${v}" class="${v === eng.piper_speed ? "on" : ""}">${String(v.toFixed(1)).replace(".", ",")}</button>`).join("")}</div></div>
      <div id="piperList" class="voice-grid">${!eng.status.piper ? "" : piperVoices === null ? `<div class="sub-intro">Lade Piper-Stimmen …</div>` : piperVoices.map((v) => {
        const on = v.key === eng.piper_voice, playing = currentPlayingId === v.key;
        return `
        <div class="voice-card ${on ? "active" : ""}">
          <div class="voice-info"><div class="voice-head"><span class="voice-name">${esc(v.name[0].toUpperCase() + v.name.slice(1))}</span>
            <span class="voice-gender">${esc(QUALITY[v.quality] || v.quality)}</span>${on ? `<span class="badge" style="background:var(--pink-accent);color:#000;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;">Aktiv</span>` : ""}</div>
            <span class="voice-desc">${esc(v.key)} · ${v.mb} MB${v.downloaded ? "" : " · wird beim ersten Mal geladen"}${v.speakers > 1 ? ` · ${v.speakers} Sprecher` : ""}</span></div>
          <div class="voice-actions">
            <button class="voice-play-btn ${playing ? "playing" : ""}" data-play="${esc(v.key)}" data-url="/api/v1/voice/piper/sample/${encodeURIComponent(v.key)}?speed=${eng.piper_speed}" aria-label="Anhören"><span class="ms">${playing ? "stop" : "play_arrow"}</span></button>
            <button class="voice-select-btn" data-pick="${esc(v.key)}"><span class="ms">${on ? "check" : "radio_button_unchecked"}</span><span>${on ? "Gewählt" : "Wählen"}</span></button>
          </div>
        </div>`;
      }).join("")}</div>` : ""}`;
    $("sEng").onclick = (e) => { const b = e.target.closest("[data-e]"); if (b && b.dataset.e !== eng.engine) save({ engine: b.dataset.e }); };
    $("stellaContent").style.display = pipe ? "none" : "block";
    if (!pipe) return;
    box.querySelectorAll("[data-stt]").forEach((c) => (c.onclick = () => c.dataset.stt !== eng.stt && save({ stt: c.dataset.stt })));
    $("sLlm").onchange = (e) => save({ llm: e.target.value });
    $("sTts").onclick = (e) => { const b = e.target.closest("[data-t]"); if (b && b.dataset.t !== eng.tts) save({ tts: b.dataset.t }); };
    $("sSpeed").onclick = (e) => { const b = e.target.closest("[data-v]"); if (b && +b.dataset.v !== eng.piper_speed) save({ piper_speed: +b.dataset.v }); };
    box.querySelectorAll("[data-play]").forEach((b) => (b.onclick = () => togglePlay(b.dataset.play, b.dataset.url, draw)));
    box.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => b.dataset.pick !== eng.piper_voice && save({ piper_voice: b.dataset.pick })));
    loadPiper();
  };
  draw();
  drawGemini(data);
}

function drawGemini(data) {
  const el = $("stellaContent");
  const activeVoice = data.voice || "Aoede";
  const voices = data.voices || [];
  el.innerHTML = `
      <div class="sgrp" style="margin-top:18px">Stimme in Gemini Live</div>
      <p class="sub-intro" style="margin-top:0">Jede der 21 Stimmen lässt sich mit einem Klick probehören.</p>
      <div class="chipset" id="stellaFilter" style="margin-bottom:12px;">
        <button class="on" data-filter="all">Alle (21)</button>
        <button data-filter="weiblich">Weiblich (10)</button>
        <button data-filter="männlich">Männlich (11)</button>
      </div>
      <div id="stellaVoiceList" class="voice-grid"></div>`;
  {
    let filter = "all";
    const drawList = () => {
      const filtered = voices.filter((v) => filter === "all" || v.gender.toLowerCase() === filter);
      const listEl = $("stellaVoiceList");
      if (!listEl) return;

      listEl.innerHTML = filtered.map((v) => {
        const isActive = v.id.toLowerCase() === (state.boot.voice_name || activeVoice).toLowerCase();
        const isPlaying = currentPlayingId === v.id;
        return `
          <div class="voice-card ${isActive ? "active" : ""}" data-voice-id="${esc(v.id)}">
            <div class="voice-info">
              <div class="voice-head">
                <span class="voice-name">${esc(v.name)}</span>
                <span class="voice-gender">${esc(v.gender === "weiblich" ? "Weiblich" : "Männlich")}</span>
                ${isActive ? `<span class="badge" style="background:var(--pink-accent);color:#000;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;">Aktiv</span>` : ""}
              </div>
              <span class="voice-desc">${esc(v.description)}</span>
            </div>
            <div class="voice-actions">
              <button class="voice-play-btn ${isPlaying ? "playing" : ""}" data-play="${esc(v.id)}" data-url="${esc(v.sample_url)}" aria-label="${isPlaying ? "Stopp" : "Anhören"}">
                <span class="ms">${isPlaying ? "stop" : "play_arrow"}</span>
              </button>
              <button class="voice-select-btn" data-select="${esc(v.id)}">
                <span class="ms">${isActive ? "check" : "radio_button_unchecked"}</span>
                <span>${isActive ? "Gewählt" : "Wählen"}</span>
              </button>
            </div>
          </div>`;
      }).join("");

      // Bind play buttons
      listEl.querySelectorAll("[data-play]").forEach((btn) => {
        btn.onclick = (e) => {
          e.stopPropagation();
          const vid = btn.dataset.play;
          const url = btn.dataset.url;
          togglePlay(vid, url, drawList);
        };
      });

      // Bind select buttons
      listEl.querySelectorAll("[data-select]").forEach((btn) => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          const vid = btn.dataset.select;
          await selectVoice(vid, drawList);
        };
      });
    };

    $("stellaFilter").onclick = (e) => {
      const b = e.target.closest("[data-filter]");
      if (!b) return;
      filter = b.dataset.filter;
      $("stellaFilter").querySelectorAll("button").forEach((btn) => btn.classList.toggle("on", btn === b));
      drawList();
    };

    drawList();
  }
}

function stopCurrentAudio() {
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
    } catch {}
    currentAudio = null;
  }
  currentPlayingId = null;
}

function togglePlay(voiceId, url, onStateChange) {
  if (currentPlayingId === voiceId) {
    stopCurrentAudio();
    onStateChange();
    return;
  }

  stopCurrentAudio();
  currentPlayingId = voiceId;
  onStateChange();

  const audio = new Audio(url);
  currentAudio = audio;

  audio.onended = () => {
    if (currentAudio === audio) {
      stopCurrentAudio();
      onStateChange();
    }
  };

  audio.onerror = () => {
    toast("Audio konnte nicht abgespielt werden");
    if (currentAudio === audio) {
      stopCurrentAudio();
      onStateChange();
    }
  };

  audio.play().catch((err) => {
    console.error("Audio playback error:", err);
    toast("Wiedergabefehler im Browser");
    stopCurrentAudio();
    onStateChange();
  });
}

async function selectVoice(voiceId, onStateChange) {
  try {
    await api.patch("/voice/settings", { voice: voiceId });
    state.boot.voice_name = voiceId;
    toast(`Stimme auf ${voiceId} geändert`);
    onStateChange();
  } catch (err) {
    toast("Fehler beim Speichern: " + err.message);
  }
}
