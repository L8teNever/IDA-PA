// Überlappende Termine eines Tages nebeneinander setzen (Spaltenverfahren).
export function columns(items) {
  const sorted = [...items].sort((a, b) => a.s - b.s || b.e - a.e);
  const out = []; let group = [], gEnd = -1;
  const flush = () => {
    const cols = [];
    for (const it of group) {
      let c = cols.findIndex((last) => last <= it.s);
      if (c < 0) { c = cols.length; cols.push(0); }
      cols[c] = it.e; it.col = c;
    }
    group.forEach((it) => { it.cols = cols.length; out.push(it); });
    group = [];
  };
  for (const it of sorted) {
    if (group.length && it.s >= gEnd) { flush(); gEnd = -1; }
    group.push(it); gEnd = Math.max(gEnd, it.e);
  }
  if (group.length) flush();
  return out;
}
