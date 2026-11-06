// Misst Stellas Stimme genau in dem Moment, in dem sie aus dem Lautsprecher kommt (nicht beim Empfang der Audio-Pakete,
// die kommen schneller als Echtzeit), damit sich das Maskottchen Silbe für Silbe mitbewegen kann.
export function outputMeter(ctx) {
  const node = ctx.createAnalyser();
  node.fftSize = 1024;   // ~20 ms Fenster: kurz genug für einzelne Silben
  node.smoothingTimeConstant = 0;
  node.connect(ctx.destination);
  const buf = new Float32Array(node.fftSize);
  return {
    node,
    level() {
      node.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const v = Math.min(1, Math.sqrt(sum / buf.length) / 0.14);
      return v < 0.04 ? 0 : v ** 0.75;   // Rauschen weg, leise Silben sichtbar anheben
    },
  };
}

// ein Schritt für die Anzeige: beim Sprechen schnell hoch und schnell wieder runter, sonst weich
export const follow = (shown, lvl, speak) => shown + (lvl - shown) * (speak ? (lvl > shown ? 0.6 : 0.3) : 0.12);
