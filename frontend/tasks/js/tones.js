// Stellas eigene Signaltöne für das Live-Gespräch, direkt im Browser erzeugt (keine Audiodateien):
// weiche, glockige Töne (Sinus + leiser Oberton, sanfter Anschlag, kurzer Nachhall).
//   connected()    – verbunden:  heller Dreiklang aufwärts („ding-ding-ding“)
//   hungUp()       – aufgelegt:  drei Töne abwärts mit langem Ausklingen
//   disconnected() – getrennt/Fehler: tiefer, abwärts gleitender Ton mit leichtem Moll
// Der AudioContext läuft nur, solange ein Ton klingt: ein laufender Context hält den Audio-Thread des Handys wach, auch ohne Ton.
let ctx = null;
let quiet = 0;

function audio() {
  clearTimeout(quiet);
  try {
    if (!ctx || ctx.state === "closed") ctx = new (window.AudioContext || window.webkitAudioContext)();
    ctx.resume?.();
  } catch { ctx = null; }
  return ctx;
}

function quietIn(ms) {
  clearTimeout(quiet);
  quiet = setTimeout(() => ctx?.suspend?.().catch(() => {}), ms);
}

// beim ersten Tippen aufrufen, damit der Browser die Töne später abspielen darf
export function unlockTones() { audio(); quietIn(300); }

function bell(a, out, freq, at, len, vol, glideTo) {
  for (const [mult, gain, type] of [[1, 1, "sine"], [2, 0.18, "sine"], [3, 0.05, "triangle"]]) {
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq * mult, at);
    if (glideTo) o.frequency.exponentialRampToValueAtTime(glideTo * mult, at + len * 0.8);
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(vol * gain, at + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, at + len);
    o.connect(g).connect(out);
    o.start(at);
    o.stop(at + len + 0.05);
  }
}

function play(notes) {
  const a = audio();
  if (!a) return;
  const out = a.createGain();
  out.gain.value = 0.9;
  // kleiner Nachhall: zwei leise, gedämpfte Echos
  const echo = a.createDelay(0.5);
  const fb = a.createGain();
  const damp = a.createBiquadFilter();
  echo.delayTime.value = 0.13;
  fb.gain.value = 0.22;
  damp.type = "lowpass";
  damp.frequency.value = 2600;
  out.connect(a.destination);
  out.connect(echo);
  echo.connect(damp).connect(fb).connect(echo);
  fb.connect(a.destination);
  const t = a.currentTime + 0.03;
  for (const [freq, start, len, vol, glide] of notes) bell(a, out, freq, t + start, len, vol, glide);
  quietIn((Math.max(...notes.map(([, start, len]) => start + len)) + 0.8) * 1000);   // + Nachhall
}

export const connected = () => play([[659.3, 0, 0.5, 0.16], [830.6, 0.09, 0.5, 0.15], [987.8, 0.18, 0.9, 0.17]]);   // E5 Gis5 H5
// Auflegen/Getrennt: länger und lauter als „verbunden“, damit das Ende deutlich auffällt
export const hungUp = () => play([[987.8, 0, 0.6, 0.24], [783.99, 0.22, 0.6, 0.24], [659.3, 0.44, 1.5, 0.26]]);           // H5 → G5 → E5
export const disconnected = () => play([[523.3, 0, 0.55, 0.24], [440, 0.3, 0.6, 0.24], [440, 0.62, 1.6, 0.26, 293.7]]);  // C5 → A4, gleitet nach D4
