// Attack rating from exported regulation tables, for pages that let a player type their own stats.
//
// A line-for-line port of `scripts/er-mechanics-ar.py` (`calc_correct`, `stat_multiplier`,
// `element_multiplier`, the status block of `attack_rating`). The data comes from
// `scripts/er-mechanics-ar-export.py`, whose `--selftest` runs this file under node against the
// Python results, so the two cannot drift silently.
(function (root) {
  const STATS = ['str', 'dex', 'int', 'fth', 'arc'];

  function calcCorrect(g, x) {
    if (!g) return x;
    const v = g.v, gr = g.g, a = g.a;
    if (v[4] <= x) x = v[4];
    if (!(x > 0)) return gr[0];
    let i = 0;
    while (i < 3 && !(x <= v[i + 1])) i++;
    const dv = v[i + 1] - v[i], dg = gr[i + 1] - gr[i];
    if (dv === 0) return gr[i + 1];
    let out;
    if (a[i] >= 0) out = gr[i] + Math.pow((x - v[i]) / dv, a[i]) * dg;
    else out = gr[i] + (1 - Math.pow((dv - (x - v[i])) / dv, -a[i])) * dg;
    return Math.min(Math.max(out, Math.min(gr[i], gr[i + 1])), Math.max(gr[i], gr[i + 1]));
  }

  function statMultiplier(c, req, stat, rate, graph, statMult) {
    const eff = Math.trunc(stat * statMult);
    if (req - eff > 0 && req > 0) {
      const shortPct = Math.min((1 - eff / req) * 100, c.penaltyCapPct);
      const k = (c.penaltyFullPct - 100) / (1 - c.penaltyCapPct * c.penaltyCapPct);
      const floor = k > 0 ? c.penaltyCapPct : 0;
      const out = ((shortPct - floor) * k * (shortPct - floor) + (100 - floor * k * floor)) / 100;
      return Math.max(out - c.lowStatusAtkPowDown, 0);
    }
    if (rate > 0) return (rate / 100) * (calcCorrect(graph, eff) / 100) + 1;
    return 1;
  }

  function elementMultiplier(c, el, stats, strMult) {
    const ms = [];
    for (const s of STATS) {
      const p = el.stats[s];
      if (!p) { ms.push(1); continue; }
      const m = statMultiplier(c, p.req, stats[s] || 0, p.rate, c.graphs[el.graph], s === 'str' ? strMult : 1);
      ms.push(p.infl * m);
    }
    if (ms.some((m) => m < 1)) return Math.min(1, ...ms);
    return 1 + ms.reduce((t, m) => t + (m - 1), 0);
  }

  // `row` is one affinity at one upgrade level from the export; returns {damage, status, total}.
  function attackRating(c, row, stats, twoHanded) {
    const strMult = twoHanded ? c.twoHandStrMult : 1;
    const damage = {};
    let total = 0;
    for (const [name, el] of Object.entries(row.elements)) {
      const t = el.base * elementMultiplier(c, el, stats, strMult);
      damage[name] = t;
      total += t;
    }
    const status = {};
    for (const [name, st] of Object.entries(row.status || {})) {
      const m = st.graph == null ? 1 : statMultiplier(c, st.req, stats.arc || 0, st.rate, c.graphs[st.graph], 1);
      status[name] = st.base * m;
    }
    return { damage, status, total };
  }

  root.ErAr = { calcCorrect, statMultiplier, elementMultiplier, attackRating, STATS };
  if (typeof module !== 'undefined') module.exports = root.ErAr;
})(typeof window !== 'undefined' ? window : globalThis);
