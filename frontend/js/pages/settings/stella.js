// Einstellungen → Stella-Stimme (Gemini Live)
import { api } from "../../api.js";
import { state } from "../../store.js";
import { $, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

let currentAudio = null;
let currentPlayingId = null;

export async function render(el) {
  el.innerHTML = subHead("Stella-Stimme", "/einstellungen") + `
    <div class="srow" style="background:transparent;padding:0 0 12px 0;">
      <p class="sub-intro" style="margin:0;">
        Wähle hier die Stimme für Stella in Live-Gesprächen und am Telefon (Gemini Live).
        Du kannst jede der 21 Stimmen mit einem Klick probehören.
      </p>
    </div>
    <div class="srow" id="liveApp" hidden style="margin-bottom:12px;">
      <span class="smain"><b>Live-App</b><small>Eigene App nur fürs Sprechen mit Stella. Link öffnen, dann im Browser „App installieren“ wählen.</small></span>
      <a class="es-btn primary" id="liveAppLink" target="_blank" rel="noopener"><span class="ms">install_mobile</span>Öffnen</a>
    </div>
    <div id="stellaLoading" class="sub-intro">Lade Stimmen …</div>
    <div id="stellaContent" style="display:none">
      <div class="chipset" id="stellaFilter" style="margin-bottom:12px;">
        <button class="on" data-filter="all">Alle (21)</button>
        <button data-filter="weiblich">Weiblich (10)</button>
        <button data-filter="männlich">Männlich (11)</button>
      </div>
      <div id="stellaVoiceList" class="voice-grid"></div>
    </div>`;

  api.get("/voice/app-link").then((l) => { $("liveAppLink").href = l.url; $("liveApp").hidden = false; }).catch(() => {});
  try {
    const data = await api.get("/voice/settings");
    const activeVoice = data.voice || "Aoede";
    const voices = data.voices || [];

    $("stellaLoading").style.display = "none";
    $("stellaContent").style.display = "block";

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
  } catch (err) {
    if ($("stellaLoading")) $("stellaLoading").textContent = "Fehler beim Laden: " + err.message;
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
