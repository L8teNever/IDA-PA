// Dauerhafter Speicher: bittet den Browser, die Offline-Daten nicht von selbst zu löschen, und zeigt den Verbrauch.
let asked = false;

/** Beim ersten Schreiben und beim Installieren aufrufen. Gibt true zurück, wenn der Speicher dauerhaft ist. */
export async function persist() {
  try {
    if (!navigator.storage?.persist) return false;
    if (await navigator.storage.persisted()) return true;
    if (asked) return false;
    asked = true;
    return await navigator.storage.persist();
  } catch { return false; }
}

export async function info() {
  const out = { used: 0, quota: 0, persistent: false, supported: !!navigator.storage?.estimate };
  try {
    const e = await navigator.storage?.estimate?.();
    out.used = e?.usage || 0; out.quota = e?.quota || 0;
    out.persistent = !!(await navigator.storage?.persisted?.());
  } catch { /* nicht unterstützt */ }
  return out;
}

export const fmtBytes = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`).replace(".", ",");
