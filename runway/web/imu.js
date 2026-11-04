/* Runway - IMU page: a device's stored 6-axis samples over time.
 *
 * Seven stacked lanes on one canvas — ax, ay, az, |a| (acceleration, m/s², ±8 g sensor) and
 * the gyro's pitch, roll, yaw rates (°/s, ±250 dps) — each with its own y-scale, because az
 * carries gravity (~9.8) while ax/ay move around 0 and would flatten on a shared axis. One time
 * axis, one crosshair, one zoom for all of them, a thin packet lane on top (NR packets, history
 * packets, missing frames shaded across every lane), and a scrollbar under the plot spanning the
 * device's whole stored history.
 *
 * Sample times follow the device (the server works them out): a packet's 20 samples are spread
 * over the packet interval for its ignition state, so packets join back to back.
 *
 * The server thins a long range to about one point per pixel (min / max / mean per bucket,
 * drawn as a band and a line); zoomed in far enough, every sample comes back as-is.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let d; try { d = await r.json(); } catch (e) { /* ignore */ }
      throw new Error((d && d.detail) || r.status);
    }
    return r.json();
  }

  const LANES = [
    { key: "ax", label: "ax", unit: "m/s²", group: "acc" },
    { key: "ay", label: "ay", unit: "m/s²", group: "acc" },
    { key: "az", label: "az", unit: "m/s²", group: "acc" },
    { key: "amag", label: "|a|", unit: "m/s²", group: "acc" },
    { key: "pitch", label: "pitch", unit: "°/s", group: "ori" },
    { key: "roll", label: "roll", unit: "°/s", group: "ori" },
    { key: "yaw", label: "yaw", unit: "°/s", group: "ori" },
  ];
  // Two layouts of the same seven series. Lanes: one chart each, on its own scale. Combined:
  // acceleration's four on one chart and the three rotation rates on another — one shared axis
  // per chart (one unit), so the lines compare directly.
  const PANELS = {
    lanes: LANES.map((l) => ({ label: l.label, unit: l.unit, group: l.group, keys: [l.key] })),
    combined: [
      { label: "Acceleration", unit: "m/s²", group: "acc", keys: ["ax", "ay", "az", "amag"] },
      { label: "Rotation rate", unit: "°/s", group: "ori", keys: ["pitch", "roll", "yaw"] },
    ],
  };
  const labelOf = Object.fromEntries(LANES.map((l) => [l.key, l.label]));
  const PAD_L = 108, PAD_R = 14, EVENT_H = 20, AXIS_H = 26, GROUP_GAP = 12;
  const STORE = "runway.imu";

  const state = {
    devices: [], device: null, span: 900, live: false, layout: "lanes",
    view: null,            // {start, end} currently shown
    zoom: [],              // previous views, for double-click / Reset zoom
    data: null, loading: false, drag: null, hover: null, timer: null,
  };
  try { Object.assign(state, JSON.parse(localStorage.getItem(STORE) || "{}"), { zoom: [], data: null }); }
  catch (e) { /* private mode — defaults */ }
  if (!PANELS[state.layout]) state.layout = "lanes";
  const save = () => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ device: state.device, span: state.span, live: state.live,
                                                   layout: state.layout }));
    } catch (e) { /* ignore */ }
  };

  // ---- formatting ---------------------------------------------------------
  const p2 = (n) => String(n).padStart(2, "0");
  function fmtTime(t, withMs) {
    const d = new Date(t * 1000);
    const s = `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
    return withMs ? `${s}.${String(d.getMilliseconds()).padStart(3, "0").slice(0, 1)}` : s;
  }
  function fmtDate(t) {
    const d = new Date(t * 1000);
    return `${p2(d.getDate())}-${p2(d.getMonth() + 1)}-${d.getFullYear()}`;
  }
  const fmtNum = (v, dp = 2) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(dp));
  const compact = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? (n / 1e3).toFixed(1) + "K"
    : n.toLocaleString());
  function fmtSpan(s) {
    if (s < 90) return `${Math.round(s)} s`;
    if (s < 5400) return `${Math.round(s / 60)} min`;
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return m ? `${h} h ${m} min` : `${h} h`;
  }

  // ---- theme tokens (re-read on every draw, so the theme toggle just redraws) ----
  function tokens() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    return { surface: v("--panel"), text: v("--text"), muted: v("--muted"), grid: v("--border-subtle"),
             axis: v("--border"), warn: v("--warn"), accent: v("--accent"),
             acc: v("--imu-acc"), ori: v("--imu-ori"),
             series: [v("--imu-s1"), v("--imu-s2"), v("--imu-s3"), v("--imu-s4")] };
  }
  // Lanes: a lane's colour is its group's (one series per chart). Combined: each line on a chart
  // takes the next categorical slot, in the chart's fixed order — never cycled, never by rank.
  function colorOf(key, k) {
    if (state.layout !== "combined") return LANES.find((l) => l.key === key).group === "acc" ? k.acc : k.ori;
    const p = PANELS.combined.find((pp) => pp.keys.includes(key));
    return k.series[p.keys.indexOf(key)];
  }
  const alpha = (hex, a) => {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  };

  // ---- layout ---------------------------------------------------------------
  const canvas = $("imu-chart"), wrap = $("imu-chart-wrap"), tip = $("imu-tip");
  const ctx = canvas.getContext("2d");
  let L = null;                       // current layout: {w, h, x0, x1, panels:[{keys,y0,y1}], eventY}

  function layout() {
    const w = wrap.clientWidth;
    const top = wrap.getBoundingClientRect().top;
    const avail = Math.max(420, window.innerHeight - top - 18 - 24);   // 24: the timeline scrollbar
    const defs = PANELS[state.layout];
    const per = (avail - EVENT_H - AXIS_H - GROUP_GAP - 8) / defs.length;
    const panelH = state.layout === "combined" ? Math.max(160, Math.min(420, per))
                                               : Math.max(52, Math.min(120, per));
    const h = Math.round(EVENT_H + 8 + panelH * defs.length + GROUP_GAP + AXIS_H);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    canvas.style.width = w + "px"; canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let y = EVENT_H + 8;
    const panels = defs.map((p, i) => {
      if (i > 0 && defs[i - 1].group !== p.group) y += GROUP_GAP;
      const panel = { ...p, y0: y, y1: y + panelH };
      y += panelH;
      return panel;
    });
    L = { w, h, x0: PAD_L, x1: w - PAD_R, panels, eventY: 2, axisY: y };
  }
  const xOf = (t) => L.x0 + (t - state.view.start) / (state.view.end - state.view.start) * (L.x1 - L.x0);
  const tOf = (x) => state.view.start + (x - L.x0) / (L.x1 - L.x0) * (state.view.end - state.view.start);

  // "Nice" time ticks: ~1 per 110px
  const STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
  function timeTicks() {
    const span = state.view.end - state.view.start;
    const want = Math.max(2, (L.x1 - L.x0) / 110);
    const step = STEPS.find((s) => span / s <= want) || 86400;
    const tz = -new Date(state.view.start * 1000).getTimezoneOffset() * 60;   // align to local clock
    const first = Math.ceil((state.view.start + tz) / step) * step - tz;
    const out = [];
    for (let t = first; t <= state.view.end; t += step) out.push(t);
    return { ticks: out, step };
  }

  // ---- draw ------------------------------------------------------------------
  function rangeOf(keys) {                // one y-range covering every series on the chart
    let mn = Infinity, mx = -Infinity;
    keys.forEach((key) => {
      const s = state.data.series[key] || {};
      const lo = s.min || s.mean || [], hi = s.max || s.mean || [];
      for (let i = 0; i < lo.length; i++) { if (lo[i] != null && lo[i] < mn) mn = lo[i]; if (hi[i] != null && hi[i] > mx) mx = hi[i]; }
    });
    if (!isFinite(mn)) return [0, 1];
    const pad = Math.max((mx - mn) * 0.12, Math.abs(mx) * 0.002, 0.02);
    return [mn - pad, mx + pad];
  }
  // A hole in the data breaks the line instead of bridging it with a straight segment. Each
  // point carries its samples' own spacing (g: 500 ms with ignition ON, 100 ms OFF by default,
  // longer when an ignition change cut a window short), so the bar is set by the samples on
  // either side — not by the slowest state anywhere in view, which let a 1.1 s hole in 100 ms
  // data pass as a line.
  // Thinned to time buckets: by the samples ("buckets") or, for long ranges, from the stored
  // per-minute summaries ("minutes"). Both carry min / max / mean per point.
  const bucketed = (d) => d.mode === "buckets" || d.mode === "minutes";
  const bucketWidth = (d) => d.w || (d.end - d.start) / Math.max(1, d.t.length);
  function breaks(d) {
    const t = d.t, g = d.g || [], tm = d.timing || {};
    // a server older than per-point spacing: fall back to the slowest state's gap
    const dflt = Math.max((tm.on || {}).gap || 0.1, (tm.off || {}).gap || 0.1);
    const floor = bucketed(d) ? bucketWidth(d) : 0;
    return (i) => t[i] - t[i - 1] > 2.5 * Math.max(g[i] || dflt, g[i - 1] || dflt, floor);
  }

  function draw() {
    if (!L) layout();
    const k = tokens();
    ctx.clearRect(0, 0, L.w, L.h);
    if (!state.data || !state.view) return;
    const d = state.data, t = d.t;
    const { ticks, step } = timeTicks();

    // vertical time gridlines + axis labels
    ctx.font = "11.5px system-ui, 'Segoe UI', sans-serif";
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    ticks.forEach((tt) => {
      const x = Math.round(xOf(tt)) + 0.5;
      ctx.strokeStyle = k.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, EVENT_H + 4); ctx.lineTo(x, L.axisY); ctx.stroke();
      ctx.fillStyle = k.muted;
      ctx.fillText(step < 60 ? fmtTime(tt) : fmtTime(tt).slice(0, 5), x, L.axisY + 7);
    });
    if (state.view.end - state.view.start > 86400 || new Date(state.view.start * 1000).getDate()
        !== new Date(state.view.end * 1000).getDate()) {
      ctx.textAlign = "left"; ctx.fillText(fmtDate(state.view.start), 4, L.axisY + 7);
    }

    // missing frames: shaded across every lane, marked in the packet lane
    ctx.fillStyle = alpha(k.warn, 0.12);
    (d.gaps || []).forEach((g) => {
      const xa = Math.max(L.x0, xOf(g.from)), xb = Math.min(L.x1, xOf(g.to));
      if (xb > xa) {
        ctx.fillRect(xa, EVENT_H + 4, Math.max(1, xb - xa), L.axisY - EVENT_H - 4);
        ctx.fillStyle = k.warn; ctx.fillRect(xa, L.eventY + 4, Math.max(2, xb - xa), 8);
        ctx.fillStyle = alpha(k.warn, 0.12);
      }
    });
    // packet lane: NR packets as muted ticks, history packets in amber
    (d.events || []).forEach((e) => {
      const x = Math.round(xOf(e.t)) + 0.5;
      if (x < L.x0 || x > L.x1) return;
      ctx.strokeStyle = e.history ? k.warn : k.muted; ctx.lineWidth = e.history ? 2 : 1;
      ctx.beginPath(); ctx.moveTo(x, L.eventY + (e.history ? 2 : 5)); ctx.lineTo(x, L.eventY + 14); ctx.stroke();
    });
    ctx.fillStyle = k.muted; ctx.textAlign = "right"; ctx.textBaseline = "top";
    ctx.fillText("packets", L.x0 - 10, L.eventY + 2);

    const isBuckets = bucketed(d);
    const brk = breaks(d);
    const multi = state.layout === "combined";
    L.panels.forEach((p) => {
      const keys = p.keys.filter((key) => d.series[key]);
      if (!keys.length) return;
      const [mn, mx] = rangeOf(keys);
      const yOf = (v) => p.y1 - 6 - (v - mn) / (mx - mn) * (p.y1 - p.y0 - 12);
      p.yOf = yOf;
      // frame: baseline hairline + value ticks (3 on a lane, 5 on a combined chart)
      ctx.strokeStyle = k.axis; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(L.x0, Math.round(p.y1) + 0.5); ctx.lineTo(L.x1, Math.round(p.y1) + 0.5); ctx.stroke();
      ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = k.muted;
      const span = mx - mn, dp = span < 0.5 ? 3 : span < 5 ? 2 : 1;
      const fr = multi ? [0.08, 0.29, 0.5, 0.71, 0.92] : [0.15, 0.5, 0.85];
      fr.forEach((f) => {
        const v = mn + (mx - mn) * f, y = Math.round(yOf(v)) + 0.5;
        ctx.strokeStyle = k.grid; ctx.beginPath(); ctx.moveTo(L.x0, y); ctx.lineTo(L.x1, y); ctx.stroke();
        ctx.fillText(v.toFixed(dp), L.x0 - 6, y);
      });
      // chart name (primary ink) over its unit (muted), in their own column left of the ticks
      const mid = (p.y0 + p.y1) / 2;
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillStyle = k.text; ctx.font = "600 12.5px system-ui, 'Segoe UI', sans-serif";
      const words = multi ? p.label.split(" ") : [p.label];       // "Rotation rate" on two lines
      words.forEach((wd, wi) => ctx.fillText(wd, 8, mid - 7 - (words.length - 1 - wi) * 15));
      ctx.font = "11.5px system-ui, 'Segoe UI', sans-serif";
      if (p.unit) { ctx.fillStyle = k.muted; ctx.fillText(p.unit, 8, mid + 8); }

      ctx.save();
      ctx.beginPath(); ctx.rect(L.x0, p.y0, L.x1 - L.x0, p.y1 - p.y0); ctx.clip();
      keys.forEach((key) => {
        const s = d.series[key], color = colorOf(key, k);
        if (isBuckets && s.min) {            // min–max band per bucket, a wash of the series hue
          ctx.fillStyle = alpha(color, multi ? 0.10 : 0.18);
          let i = 0;
          while (i < t.length) {
            let j = i + 1;
            while (j < t.length && !brk(j)) j++;
            ctx.beginPath();
            for (let a = i; a < j; a++) ctx.lineTo(xOf(t[a]), yOf(s.max[a]));
            for (let a = j - 1; a >= i; a--) ctx.lineTo(xOf(t[a]), yOf(s.min[a]));
            ctx.closePath(); ctx.fill();
            i = j;
          }
        }
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
        ctx.beginPath();
        for (let i = 0; i < t.length; i++) {
          const x = xOf(t[i]), y = yOf(s.mean[i]);
          if (i === 0 || brk(i)) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      });
      ctx.restore();
      if (multi) panelLegend(p, keys, k);
    });

    // drag-to-zoom selection
    if (state.drag && Math.abs(state.drag.x1 - state.drag.x0) > 2) {
      const xa = Math.min(state.drag.x0, state.drag.x1), xb = Math.max(state.drag.x0, state.drag.x1);
      ctx.fillStyle = alpha(k.accent, 0.14); ctx.fillRect(xa, EVENT_H + 4, xb - xa, L.axisY - EVENT_H - 4);
      ctx.strokeStyle = k.accent; ctx.lineWidth = 1; ctx.strokeRect(xa + 0.5, EVENT_H + 4.5, xb - xa - 1, L.axisY - EVENT_H - 5);
    }
    // crosshair: snaps to the nearest point, one dot per lane (2px surface ring)
    if (state.hover != null && !state.drag) {
      const i = state.hover, x = Math.round(xOf(t[i])) + 0.5;
      ctx.strokeStyle = k.muted; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, EVENT_H + 4); ctx.lineTo(x, L.axisY); ctx.stroke();
      L.panels.forEach((p) => p.keys.forEach((key) => {
        const v = (d.series[key] || {}).mean; if (!v || v[i] == null || !p.yOf) return;
        ctx.beginPath(); ctx.arc(x, p.yOf(v[i]), 4, 0, Math.PI * 2);
        ctx.fillStyle = colorOf(key, k); ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = k.surface; ctx.stroke();
      }));
    }
  }

  // A combined chart's own legend, top-left inside the plot: a short line key in the series
  // colour beside its name in text ink — identity never rests on colour alone.
  function panelLegend(p, keys, k) {
    ctx.font = "600 12px system-ui, 'Segoe UI', sans-serif";
    const items = keys.map((key) => ({ key, label: labelOf[key], w: 18 + ctx.measureText(labelOf[key]).width }));
    const width = items.reduce((s, it) => s + it.w + 14, 0) + 6;
    const x0 = L.x0 + 6, y0 = p.y0 + 4;
    ctx.fillStyle = alpha(k.surface, 0.88);
    ctx.fillRect(x0, y0, width, 20);
    let x = x0 + 8;
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    items.forEach((it) => {
      ctx.strokeStyle = colorOf(it.key, k); ctx.lineWidth = 2; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(x, y0 + 10); ctx.lineTo(x + 12, y0 + 10); ctx.stroke();
      ctx.fillStyle = k.text; ctx.fillText(it.label, x + 17, y0 + 10.5);
      x += it.w + 14;
    });
    ctx.font = "11.5px system-ui, 'Segoe UI', sans-serif";
  }

  // ---- tooltip -------------------------------------------------------------------
  function nearestIndex(tt) {
    const t = state.data.t; let lo = 0, hi = t.length - 1;
    if (hi < 0) return null;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (t[m] < tt) lo = m; else hi = m; }
    return Math.abs(t[lo] - tt) <= Math.abs(t[hi] - tt) ? lo : hi;
  }
  function showTip(i, px, py) {
    const d = state.data, buckets = bucketed(d), half = bucketWidth(d) / 2;
    tip.innerHTML = "";
    const head = el("div", "imu-tip-h", buckets
      ? `${fmtTime(d.t[i] - half)} – ${fmtTime(d.t[i] + half)}`
      : fmtTime(d.t[i], true));
    tip.appendChild(head);
    const k = tokens();
    LANES.forEach((ln) => {
      const s = d.series[ln.key]; if (!s) return;
      const row = el("div", "imu-tip-r");
      const key = el("span", "imu-key"); key.style.background = colorOf(ln.key, k);
      row.append(key, el("b", "", fmtNum(s.mean[i], 2)));
      if (buckets && s.min) row.append(el("span", "muted", ` ${fmtNum(s.min[i], 2)} … ${fmtNum(s.max[i], 2)}`));
      row.append(el("span", "imu-tip-l", ln.label));
      tip.appendChild(row);
    });
    const tt = d.t[i], near = (d.end - d.start) / (L.x1 - L.x0) * 6;
    (d.events || []).filter((e) => Math.abs(e.t - tt) <= near).slice(0, 3).forEach((e) => {
      tip.appendChild(el("div", "imu-tip-e", (e.history ? "History packet · " : "NR packet · ")
        + `${fmtTime(e.t)}` + (e.speed != null ? ` · ${fmtNum(e.speed, 1)} km/h` : "")
        + (e.ignition != null ? ` · ignition ${e.ignition ? "on" : "off"}` : "")));
    });
    (d.gaps || []).filter((g) => tt >= g.from && tt <= g.to).forEach((g) => {
      tip.appendChild(el("div", "imu-tip-e warn", `${g.frames} frame${g.frames === 1 ? "" : "s"} missing`));
    });
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = (px + 16 + tw > L.w ? px - tw - 16 : px + 16) + "px";
    tip.style.top = Math.max(0, Math.min(py - th / 2, L.h - th)) + "px";
  }

  // ---- pointer: crosshair, drag to zoom, double-click to step back ----------------
  const plotX = (ev) => ev.clientX - canvas.getBoundingClientRect().left;
  canvas.addEventListener("pointermove", (ev) => {
    if (!state.data || !state.data.t.length || !L) return;
    const x = plotX(ev), y = ev.clientY - canvas.getBoundingClientRect().top;
    if (state.drag) { state.drag.x1 = Math.max(L.x0, Math.min(L.x1, x)); draw(); return; }
    if (x < L.x0 || x > L.x1) { state.hover = null; tip.hidden = true; draw(); return; }
    state.hover = nearestIndex(tOf(x));
    draw(); showTip(state.hover, x, y);
  });
  canvas.addEventListener("pointerleave", () => { state.hover = null; tip.hidden = true; if (!state.drag) draw(); });
  canvas.addEventListener("pointerdown", (ev) => {
    if (!L || !state.data) return;
    const x = plotX(ev);
    if (x < L.x0 || x > L.x1) return;
    state.drag = { x0: x, x1: x };
    canvas.setPointerCapture(ev.pointerId);
    tip.hidden = true;
  });
  canvas.addEventListener("pointerup", () => {
    const dr = state.drag; state.drag = null;
    if (!dr || Math.abs(dr.x1 - dr.x0) < 6) { draw(); return; }
    const a = tOf(Math.min(dr.x0, dr.x1)), b = tOf(Math.max(dr.x0, dr.x1));
    if (b - a < 1) { draw(); return; }                 // closer than 1 s shows nothing new
    state.zoom.push(state.view);
    setLive(false);
    state.view = { start: a, end: b };
    load();
  });
  canvas.addEventListener("dblclick", () => { if (state.zoom.length) { state.view = state.zoom.pop(); load(); } });
  $("imu-unzoom").addEventListener("click", () => { state.zoom = []; state.view = null; load(); });

  // ---- data ---------------------------------------------------------------------
  function deviceInfo() { return state.devices.find((d) => d.device_id === state.device); }
  function defaultView() {
    const dev = deviceInfo(); if (!dev) return null;
    const end = state.live ? Math.max(Date.now() / 1000, dev.last + 1) : dev.last + 1;
    const start = state.span ? end - state.span : dev.first - 1;
    return { start, end };
  }

  let reqSeq = 0;
  async function load() {
    if (!state.device) return;
    if (!state.view) state.view = defaultView();
    if (!state.view) return;
    const my = ++reqSeq;                  // scrolling fires loads quickly: only the newest counts
    state.loading = true; wrap.classList.add("loading");     // refetch keeps the frame
    try {
      const qs = new URLSearchParams({ device: state.device, start: state.view.start,
                                       end: state.view.end, points: Math.max(200, Math.round((L ? L.x1 - L.x0 : 1000))) });
      const data = await api(`/api/imu/series?${qs}`);
      if (my !== reqSeq) return;
      state.data = data;
      state.hover = null; tip.hidden = true;
      render();
    } catch (e) {
      if (my === reqSeq) showEmpty(`Could not load samples: ${e.message}`);
    } finally {
      if (my === reqSeq) { state.loading = false; wrap.classList.remove("loading"); }
    }
  }

  // ---- timeline scrollbar: the device's whole stored history, the view as its thumb ----------
  const scroller = $("imu-scroll"), scrollInner = $("imu-scroll-inner");
  let scrollExt = null, scrollQuiet = false, scrollTimer = null;
  function syncScroll() {
    const dev = deviceInfo(), v = state.view;
    if (!dev || !v) { scroller.hidden = true; return; }
    const ex = { start: Math.min(dev.first - 1, v.start),
                 end: Math.max(dev.last + 1, v.end, state.live ? Date.now() / 1000 : 0) };
    const span = v.end - v.start;
    if (ex.end - ex.start <= span * 1.001) { scroller.hidden = true; return; }   // nothing to scroll to
    scroller.hidden = false;
    scroller.style.marginLeft = PAD_L + "px"; scroller.style.marginRight = PAD_R + "px";
    const inner = Math.min(3e7, scroller.clientWidth * (ex.end - ex.start) / span);
    scrollInner.style.width = inner + "px";
    scrollExt = ex; scrollQuiet = true;
    scroller.scrollLeft = (v.start - ex.start) / (ex.end - ex.start) * inner;
    requestAnimationFrame(() => { scrollQuiet = false; });
  }
  scroller.addEventListener("scroll", () => {
    if (scrollQuiet || !scrollExt || !state.view) return;
    const span = state.view.end - state.view.start;
    const start = scrollExt.start + scroller.scrollLeft / scrollInner.offsetWidth * (scrollExt.end - scrollExt.start);
    if (state.live) setLive(false);
    state.view = { start, end: start + span };
    $("imu-range-text").textContent = rangeText(null);
    if (state.data) draw();                // pan what is on screen now; the new window loads next
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(load, 120);
  });
  // trackpad sideways swipe, or Shift + wheel, scrolls the timeline
  canvas.addEventListener("wheel", (ev) => {
    const dx = ev.shiftKey ? ev.deltaY || ev.deltaX : ev.deltaX;
    if (!dx || scroller.hidden || Math.abs(ev.deltaX) < Math.abs(ev.deltaY) && !ev.shiftKey) return;
    ev.preventDefault();
    scroller.scrollLeft += dx;
  }, { passive: false });

  function rangeText(d) {
    if (!state.view) return "";
    return `${fmtTime(state.view.start)} → ${fmtTime(state.view.end)} · ${fmtSpan(state.view.end - state.view.start)}`
      + (d ? (bucketed(d) ? ` · ${d.t.length.toLocaleString()} points${d.mode === "minutes" ? " · per minute" : ""}` : " · every sample") : "");
  }

  function showEmpty(text) {
    $("imu-empty").textContent = text; $("imu-empty").hidden = !text;
    canvas.style.visibility = text ? "hidden" : "visible";
  }

  function render() {
    const d = state.data;
    $("imu-unzoom").hidden = !state.zoom.length;
    $("imu-range-text").textContent = rangeText(d);
    renderTiles(d);
    if (!d || !d.samples) { showEmpty("No samples in this range."); syncScroll(); return; }
    showEmpty("");
    layout(); draw(); syncScroll();
  }

  function renderTiles(d) {
    const box = $("imu-tiles"); box.innerHTML = "";
    const tile = (label, value, sub, tone) => {
      const t = el("div", "stat" + (tone ? " " + tone : ""));
      const v = el("div", "stat-v", value);
      if (sub) v.appendChild(el("small", "stat-sub", " " + sub));
      t.append(v, el("div", "stat-k", label));
      box.appendChild(t);
    };
    if (!d) return;
    tile("Samples", compact(d.samples || 0));
    tile("IMU packets", compact(d.imu_packets || 0), d.packets ? `of ${compact(d.packets)}` : "");
    tile("Peak |a|", d.peak ? `${fmtNum(d.peak.amag, 2)}` : "—", d.peak ? `m/s² · ${fmtTime(d.peak.t)}` : "");
    const ex = d.extremes;
    const rot = ex ? [["pitch", ex.pitch_abs], ["roll", ex.roll_abs], ["yaw", ex.yaw_abs]]
      .reduce((a, b) => ((b[1] || 0) > (a[1] || 0) ? b : a)) : null;
    tile("Peak rotation", rot ? fmtNum(rot[1], 2) : "—", rot ? `°/s · ${rot[0]}` : "");
    tile("Missing frames", d.missing_frames == null ? "—" : String(d.missing_frames),
         d.gaps && d.gaps.length ? `in ${d.gaps.length} gap${d.gaps.length === 1 ? "" : "s"}` : "",
         d.missing_frames ? "warn" : "");
    tile("History packets", String(d.history_packets || 0));
  }

  function renderLegend() {
    const box = $("imu-legend"); box.innerHTML = "";
    const k = tokens();
    const item = (cls, color, text) => {
      const i = el("span", "imu-leg");
      const sw = el("span", cls); sw.style.background = color;
      i.append(sw, document.createTextNode(text)); box.appendChild(i);
    };
    if (state.layout !== "combined") {      // combined charts carry their own legends
      item("imu-key", k.acc, "Acceleration (m/s²)");
      item("imu-key", k.ori, "Rotation rate (°/s, gyro)");
    }
    item("imu-tick", k.muted, "NR packet");
    item("imu-tick", k.warn, "History packet");
    item("imu-gapkey", k.warn, "Missing frames");
  }

  // ---- controls -----------------------------------------------------------------------
  function setLive(on) {
    state.live = on; $("imu-live").checked = on; save();
    clearInterval(state.timer);
    if (on) state.timer = setInterval(async () => {
      if (state.loading || state.drag) return;
      await refreshDevices();
      state.zoom = []; state.view = defaultView(); load();
    }, 2000);
  }
  $("imu-live").addEventListener("change", (e) => {
    setLive(e.target.checked);
    if (e.target.checked) { state.zoom = []; state.view = null; load(); }
  });
  const rangeSel = $("imu-range");
  if (![...rangeSel.options].some((o) => Number(o.value) === state.span)) state.span = 900;
  rangeSel.value = String(state.span);
  rangeSel.addEventListener("change", () => {
    state.span = Number(rangeSel.value); save();
    state.zoom = []; state.view = null; load();
  });
  document.querySelectorAll("#imu-layout .seg").forEach((b) => {
    b.classList.toggle("on", b.dataset.layout === state.layout);
    b.addEventListener("click", () => {
      state.layout = b.dataset.layout; save();
      document.querySelectorAll("#imu-layout .seg").forEach((x) => x.classList.toggle("on", x === b));
      state.hover = null; tip.hidden = true;           // its colours belonged to the other view
      renderLegend();
      if (state.data && state.data.samples) { layout(); draw(); }
    });
  });
  $("imu-device").addEventListener("change", (e) => {
    state.device = e.target.value; save();
    state.zoom = []; state.view = null; load();
  });

  // sample timing (the server owns it: one setting re-times every stored sample)
  // What the chart is using right now, per ignition state: "every 500 ms (packets every 10 s,
  // measured)" — so a config change on the device shows up here, not as a squeezed chart.
  function timingNow() {
    const tm = state.data && state.data.timing;
    if (!tm) return "";
    const one = (name, x) => x ? `Ignition ${name}: every ${Math.round(x.gap * 1000)} ms `
      + `(packets every ${+x.interval.toFixed(1)} s, ${x.source})` : "";
    return [one("ON", tm.on), one("OFF", tm.off)].filter(Boolean).join("\n");
  }
  $("imu-timing-btn").addEventListener("click", async () => {
    const pop = $("imu-timing");
    if (!pop.hidden) { pop.hidden = true; return; }
    try {
      const tm = await api("/api/imu/timing");
      $("imu-on").value = tm.ign_on_s; $("imu-off").value = tm.ign_off_s;
      $("imu-measure").checked = !!tm.measure; $("imu-anchor").value = tm.anchor;
    } catch (e) { /* keep fields */ }
    $("imu-timing-now").textContent = timingNow();
    pop.hidden = false;
  });
  $("imu-timing-save").addEventListener("click", async () => {
    try {
      await api("/api/imu/timing", { method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ign_on_s: Number($("imu-on").value), ign_off_s: Number($("imu-off").value),
                               measure: $("imu-measure").checked, anchor: $("imu-anchor").value }) });
      $("imu-timing").hidden = true; $("imu-timing-err").textContent = ""; load();
    } catch (e) { $("imu-timing-err").textContent = e.message; }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".imu-timing-wrap")) $("imu-timing").hidden = true;
  });

  // CSV of what is on screen — the table view of the chart
  $("imu-csv").addEventListener("click", () => {
    const d = state.data; if (!d || !d.t.length) return;
    const buckets = bucketed(d);
    const cols = ["time"];
    LANES.forEach((ln) => { cols.push(buckets ? `${ln.key}_mean` : ln.key); if (buckets) cols.push(`${ln.key}_min`, `${ln.key}_max`); });
    const lines = [cols.join(",")];
    d.t.forEach((t, i) => {
      const row = [new Date(t * 1000).toISOString()];
      LANES.forEach((ln) => {
        const s = d.series[ln.key] || {};
        row.push(s.mean ? s.mean[i] : "");
        if (buckets) row.push(s.min ? s.min[i] : "", s.max ? s.max[i] : "");
      });
      lines.push(row.join(","));
    });
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv" }));
    a.download = `imu_${state.device}_${fmtDate(state.view.start)}_${fmtTime(state.view.start).replace(/:/g, "")}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  });

  // A running session that receives but does not store is the usual reason this page is empty:
  // say which one, and switch Store on from here (applies to the running session, no restart).
  async function checkStoring() {
    const box = $("imu-notice");
    let off = [];
    try { off = (await api("/api/jobs")).filter((j) => j.kind === "listen" && j.status === "running" && !j.store); }
    catch (e) { box.hidden = true; return; }
    box.innerHTML = "";
    off.forEach((j) => {
      const row = el("div", "imu-notice-row");
      row.append(el("span", "", `Port ${j.port} is receiving but not storing.`));
      const btn = el("button", "ghost mini", "Turn on Store");
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await api(`/api/listen/${encodeURIComponent(j.id)}/store`, { method: "PUT",
            headers: { "content-type": "application/json" }, body: JSON.stringify({ store: true }) });
          row.replaceChildren(el("span", "", `Port ${j.port} is storing — samples appear with the next IMU packet.`));
          setTimeout(async () => { await refreshDevices(); if (!state.data) { state.view = null; load(); } }, 4000);
        } catch (e) { btn.disabled = false; btn.textContent = `Turn on Store (${e.message})`; }
      };
      row.append(btn);
      box.append(row);
    });
    box.hidden = !off.length;
  }

  async function refreshDevices() {
    state.devices = (await api("/api/imu/devices")).filter((d) => d.imu_samples > 0);
    const sel = $("imu-device"), cur = sel.value;
    sel.innerHTML = "";
    state.devices.forEach((d) => sel.appendChild(new Option(d.device_id, d.device_id)));
    if (!state.devices.some((d) => d.device_id === state.device)) state.device = (state.devices[0] || {}).device_id || null;
    sel.value = state.device || cur;
  }

  window.addEventListener("resize", () => { if (state.data) { layout(); draw(); syncScroll(); } });
  new MutationObserver(() => { renderLegend(); if (state.data) draw(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  (async function init() {
    renderLegend();
    checkStoring();
    try { await refreshDevices(); } catch (e) { showEmpty(`Could not list devices: ${e.message}`); return; }
    if (!state.devices.length) { showEmpty("No stored IMU data yet."); renderTiles(null); return; }
    layout();
    if (state.live) setLive(true);
    load();
  })();
})();
