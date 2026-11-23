/* Runway — the track map: Log analysis's map as a component, for one device or many.
 *
 *   const tm = RunwayMap.track(host, { drawKey, onPoint, onGap, onFocus, fullRight });
 *   tm.set(tracks, { refit })   tracks: [{ id, name, colour, series, driving, settings }] — series and
 *                               driving as the analysis makes them (/api/analysis/{id}, /api/live/track)
 *   tm.mark(id, k, pan, ll)  ring a track's point (its row k);  tm.markGap(id, gap);  tm.clearMark()
 *   tm.focus(id | null);  tm.trip(n | null);  tm.setFull(on);  tm.fitFull();  tm.note(text)
 *   opts: title (the head's), onTrip (a trip clicked: the page decides — Log analysis rescopes its
 *   report, then sets the trip with tm.trip or set({trip})), onFull(on, floatBox), fullRight()
 *
 * Coloured by device (several), by trip or by speed; drawn as lines or dots. Live packets solid and
 * bright, history (sent later) the same colour dimmed and thin, no data dotted — red while the ignition
 * was on (data lost), grey while off (asleep). Stale positions and jumps dashed, never joined into the
 * track. Trips numbered at their start, ringed at their end; driving events and the last position
 * marked; arrows show the way it went. A device chosen in the bar is drawn on top, the rest faded; a
 * trip of it likewise. A click on a line or dot picks the packet nearest; on a gap or a hop, that. */
(() => {
  "use strict";
  const UI = window.RunwayUI;
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  const pad = (n) => String(n).padStart(2, "0");
  const num = (x, d) => (x == null || x === "" ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 1 : d }));
  const dt = (iso) => (iso ? new Date(iso) : null);
  const dayOf = (d) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
  const clock = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const dur = (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return s + " s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " min" + (s % 60 && m < 10 ? " " + (s % 60) + " s" : "");
    return Math.floor(m / 60) + " h " + (m % 60) + " min";
  };
  const pos = (p) => (p && p[0] != null ? `${Number(p[0]).toFixed(5)}, ${Number(p[1]).toFixed(5)}` : "—");
  const distText = (m) => (m == null ? "—" : m >= 1000 ? num(m / 1000, 2) + " km" : num(m, 0) + " m");
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  // the map's own tones, tuned per theme (trackmap.css)
  const mapNum = (n, fallback) => { const v = parseFloat(cssVar(n)); return Number.isFinite(v) ? v : fallback; };
  const TRIP_COLOURS = ["#3ecf8e", "#4f8ef7", "#f5a524", "#b07cf8", "#22c3d6", "#f472b6", "#9ad94a", "#fb7a3c"];
  const tripColour = (n) => TRIP_COLOURS[(n - 1) % TRIP_COLOURS.length];
  const ARROW_GAP = 70, ARROW_MAX = 700;
  const TIP = { sticky: true, className: "tm-tipbox" };
  const BREAK = { loss: ["--danger", "ignition on — data lost"], sleep: ["--map-asleep", "ignition off"], unknown: ["--warn", "no ignition state"] };

  // A line's points, each with how far the line would move without it (metres): the tolerance at which
  // Ramer-Douglas-Peucker would leave it out. At a zoom the map draws the points that matter by more than
  // half a pixel there — never further than that from the line of every packet; at street level, all.
  function importance(pts) {
    const n = pts.length;
    const sig = new Float64Array(n);
    if (!n) return sig;
    sig[0] = sig[n - 1] = Infinity;
    const kx = 111320 * Math.cos((pts[0][0] * Math.PI) / 180), ky = 110540;
    const xs = new Float64Array(n), ys = new Float64Array(n);
    for (let i = 0; i < n; i++) { xs[i] = pts[i][1] * kx; ys[i] = pts[i][0] * ky; }
    const stack = [[0, n - 1, Infinity]];
    while (stack.length) {
      const [a, b, cap] = stack.pop();
      if (b - a < 2) continue;
      const x1 = xs[a], y1 = ys[a], dx = xs[b] - x1, dy = ys[b] - y1, ll = dx * dx + dy * dy;
      let far = -1, at = a + 1;
      for (let i = a + 1; i < b; i++) {
        let d;
        if (!ll) d = Math.hypot(xs[i] - x1, ys[i] - y1);
        else {
          let t = ((xs[i] - x1) * dx + (ys[i] - y1) * dy) / ll;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          d = Math.hypot(xs[i] - (x1 + t * dx), ys[i] - (y1 + t * dy));
        }
        if (d > far) { far = d; at = i; }
      }
      const v = Math.min(far, cap);            // kept at a tolerance only when every split above it is too
      sig[at] = v;
      stack.push([a, at, v], [at, b, v]);
    }
    return sig;
  }
  // half a screen pixel, in metres, at a zoom and latitude
  const halfPixel = (z, lat) => (0.5 * 40075016.686 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, z + 8);

  function tripAt(k, trips) {
    for (const t of trips) if (k >= t.k && k <= t.k_end) return t.n;
    return null;
  }

  // a card in a tooltip: a title, a small table, a foot of figures
  function cardTip(title, rows, foot) {
    const e = UI.esc;
    const head = rows && rows.head ? `<thead><tr>${rows.head.map((h) => `<th>${e(h)}</th>`).join("")}</tr></thead>` : "";
    const body = (rows && rows.body ? rows.body : []).map((r) => `<tr><th>${e(r[0])}</th>${r.slice(1).map((c) => `<td>${e(c)}</td>`).join("")}</tr>`).join("");
    return `<div class="tm-gtip"><div class="tm-gtip-h">${e(title)}</div>` + (body ? `<table>${head}<tbody>${body}</tbody></table>` : "") +
      (foot && foot.length ? `<div class="tm-gtip-f">${foot.map((f) => `<span>${f[1] == null ? e(f[0]) : `${e(f[0])} <b>${e(f[1])}</b>`}</span>`).join("")}</div>` : "") + "</div>";
  }

  function track(host, opts) {
    opts = opts || {};
    const drawKey = opts.drawKey || "runway.map.draw";
    let tracks = [];
    let mode = null;                      // "device" | "trips" | "speed"; null: the default for the tracks
    let draw = (() => { try { return localStorage.getItem(drawKey) === "dots" ? "dots" : "lines"; } catch (e) { return "lines"; } })();
    let focusId = null;                   // a device chosen in the bar, or null for all
    let trip = null;                      // a trip of the focused (or only) device, or null
    let pick = null;                      // { id, k, ll } or { gap, id }
    let full = false;
    let day0 = "";

    // ---- the card: head (colour by, draw as, legend), the bar (devices, trips), the map ----
    const card = el("div", "tm-card");
    const head = el("div", "tm-head");
    const modeSeg = el("div", "tcp-seg tm-seg");
    modeSeg.setAttribute("role", "tablist");
    modeSeg.setAttribute("aria-label", "Colour the track by");
    const drawSeg = el("div", "tcp-seg tm-seg");
    drawSeg.setAttribute("role", "tablist");
    drawSeg.setAttribute("aria-label", "Draw the track as");
    const legend = el("span", "tm-legend");
    if (opts.title) head.appendChild(el("span", "tm-title", opts.title));
    head.append(modeSeg, drawSeg, legend);
    const bar = el("div", "tm-bar");
    const tripsBox = el("div", "tm-trips");             // the trip list, opened from the bar
    tripsBox.hidden = true;
    const listKey = drawKey + ".trips";
    let listOpen = (() => { try { return localStorage.getItem(listKey) === "1"; } catch (e) { return false; } })();
    const wrap = el("div", "tm-wrap");
    const box = el("div", "tm-map");
    const floatBox = el("div", "tm-float");
    const noteBox = el("div", "tm-note");
    noteBox.hidden = true;
    wrap.append(box, floatBox, noteBox);
    card.append(head, bar, tripsBox, wrap);
    host.appendChild(card);

    const seg = (host_, items, on, pickFn) => {
      host_.textContent = "";
      items.forEach(([id, label, tip]) => {
        const b = el("button", "seg" + (id === on ? " on" : ""), label);
        b.type = "button";
        if (tip) b.dataset.tip = tip;
        b.addEventListener("click", () => pickFn(id));
        host_.appendChild(b);
      });
    };

    if (!window.L) {
      box.textContent = "The map needs the internet";
      return { set() {}, mark() {}, markGap() {}, clearMark() {}, focus() {}, trip() {}, invalidate() {}, note() {},
               setFull() {}, fitFull() {}, el: card };
    }
    // no keyboard handler: it focuses the map on a press, which scrolls a half-hidden map into view
    // between press and release — and the click on a line or dot is lost
    const map = L.map(box, { zoomControl: true, attributionControl: true, keyboard: false }).setView([20.59, 78.96], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
    const layers = L.layerGroup().addTo(map);
    const arrowLayer = L.layerGroup().addTo(map);
    const pointLayer = L.layerGroup().addTo(map);
    // the dots on a canvas of their own, under the lines: the lines' layer lets a click through where it
    // has nothing drawn (trackmap.css), so a dot is clickable and so is every line
    map.createPane("tm-dots").style.zIndex = 390;
    const dotRenderer = L.canvas({ padding: 0.5, pane: "tm-dots" });
    let fullBtn = null;
    const Full = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const w = L.DomUtil.create("div", "leaflet-bar tm-full-ctl");
        fullBtn = L.DomUtil.create("a", "", w);
        fullBtn.href = "#";
        fullBtn.setAttribute("role", "button");
        fullBtn.innerHTML = UI.icon("maximize");
        fullBtn.title = "Full screen";
        fullBtn.setAttribute("aria-label", "Full screen");
        L.DomEvent.on(fullBtn, "click", (e) => { L.DomEvent.stop(e); setFull(!full); });
        L.DomEvent.disableClickPropagation(w);
        return w;
      },
    });
    new Full().addTo(map);
    let arrowPaths = [];
    let lodLines = [];                    // [{ run, sig, lines }] — each zoom draws the points that matter there
    let dotItems = [];                    // the dots, bottom to top — drawn in the view, one per pixel
    const dotLayer = L.layerGroup().addTo(map);
    map.on("zoomend", () => { applyLod(); drawArrows(); });
    map.on("moveend", drawDots);

    const multi = () => tracks.length > 1;
    const byId = (id) => tracks.find((t) => t.id === id) || null;
    // the device whose trips can be chosen: the only one, or the one chosen in the bar
    const tripTrack = () => (multi() ? byId(focusId) : tracks[0] || null);
    const modeNow = () => {
      const m = mode || (multi() ? "device" : "trips");
      return m === "device" && !multi() ? "trips" : m;
    };
    const when = (iso, withDate) => {
      const d = dt(iso);
      if (!d || isNaN(d)) return "—";
      return withDate || dayOf(d) !== day0 ? `${dayOf(d)} ${clock(d)}` : clock(d);
    };
    const range = (a, b) => {
      const da = dt(a), db = dt(b);
      if (!da || !db) return `${when(a)} → ${when(b)}`;
      if (dayOf(da) !== dayOf(db)) return `${when(a, true)} → ${when(b, true)}`;
      return (dayOf(da) !== day0 ? `${dayOf(da)} · ` : "") + `${clock(da)} → ${clock(db)}`;
    };
    function speedColour(v, tr) {
      const lim = (tr.settings && tr.settings.overspeed_kmh) || 80;
      return v > lim ? cssVar("--danger") : v > lim * 0.75 ? cssVar("--warn") : v > 3 ? cssVar("--accent") : cssVar("--map-quiet");
    }
    function pin(latlng, text, colour, cls) {
      const icon = L.divIcon({ className: "tm-pin-wrap", html: `<span class="tm-pin ${cls || ""}" style="--pc:${colour}">${UI.esc(text)}</span>`,
                               iconSize: [22, 22], iconAnchor: [11, 11] });
      return L.marker(latlng, { icon, keyboard: false });
    }
    const named = (tr, title) => (multi() ? `${tr.name} · ${title}` : title);
    function tripTip(tr, n, hist) {
      const t = ((tr.driving || {}).trips || []).find((x) => x.n === n);
      if (!t) return cardTip(named(tr, hist ? "History · not in a trip" : "Not in a trip"));
      return cardTip(named(tr, `Trip ${t.n}${hist ? " · history" : ""}`), { body: [["Start", when(t.start, true)], ["End", when(t.end, true)]] },
        [["Took", t.duration], ["Distance", t.km.toFixed(1) + " km"], ["Top", num(t.max_speed, 0) + " km/h"], ["Average", num(t.avg_speed, 0) + " km/h"]]);
    }
    function hopTip(tr, x) {
      const stale = x.kind === "stale";
      return cardTip(named(tr, `${stale ? "Stale position" : "Jump"} · ${when(x.time)}`),
        { head: ["", "Time", "Position", "Satellites"],
          body: [["From", when(x.from_time), pos(x.from), (x.sats || [])[0] == null ? "—" : String(x.sats[0])],
                 ["To", when(x.time), pos(x.to), (x.sats || [])[1] == null ? "—" : String(x.sats[1])]] },
        [["Apart", distText(x.metres)], ["In", x.seconds == null ? "—" : dur(x.seconds)], ["Would be", x.kmh == null ? "—" : num(x.kmh, 0) + " km/h"]]);
    }
    function gapTip(tr, g, words) {
      const da = dt(g.start), db = dt(g.end);
      const oneDay = da && db && dayOf(da) === dayOf(db);
      const t = (iso) => (oneDay ? when(iso, true).slice(-8) : when(iso, true));
      return cardTip(named(tr, `No data${oneDay ? ` · ${dayOf(da)}` : ""}`),
        { head: ["", "Time", "Position"], body: [["Start", t(g.start), pos(g.from)], ["End", t(g.end), pos(g.to)]] },
        [["Duration", g.duration || dur(g.seconds)], ["Apart", distText(g.metres)], [words[0].toUpperCase() + words.slice(1), null]]);
    }

    // ---- the bar: devices (several), then the trips of the one chosen ------------------------------
    function chip(label, on, colour, tip, fn) {
      const b = el("button", "tm-chip" + (on ? " on" : ""));
      b.type = "button";
      if (colour) { const sw = el("i"); sw.style.background = colour; b.appendChild(sw); }
      b.appendChild(document.createTextNode(label));
      if (tip) b.title = tip;
      b.addEventListener("click", fn);
      return b;
    }
    function renderBar() {
      bar.textContent = "";
      if (multi()) {
        bar.appendChild(chip("All devices", focusId === null, null, null, () => setFocus(null)));
        tracks.forEach((tr) => bar.appendChild(chip(tr.name, focusId === tr.id, tr.colour, `${tr.rows || 0} packets on the map`,
          () => setFocus(focusId === tr.id ? null : tr.id))));
      }
      const tt = tripTrack();
      const trips = tt ? (tt.driving || {}).trips || [] : [];
      if (trips.length) {
        if (multi()) bar.appendChild(el("span", "tm-bar-sep"));
        bar.appendChild(chip("All trips", trip === null, null, null, () => setTrip(null)));
        const at = (iso) => { const w = when(iso); return w.length > 8 ? `${w.slice(0, 5)} ${w.slice(-8, -3)}` : w.slice(0, 5); };
        trips.forEach((t) => bar.appendChild(chip(`${t.n} · ${at(t.start)} · ${kmText(t.km)}`, trip === t.n, tripSwatch(t, tt),
          `Trip ${t.n}: ${range(t.start, t.end)}, ${kmText(t.km)}`, () => setTrip(trip === t.n ? null : t.n))));
        const lb = chip("", listOpen, null, listOpen ? "Hide the trip list" : "Every trip with what happened on it", () => {
          listOpen = !listOpen;
          try { localStorage.setItem(listKey, listOpen ? "1" : "0"); } catch (e) { /* not remembered */ }
          renderBar();
          setTimeout(() => map.invalidateSize(), 30);
        });
        lb.classList.add("tm-list-btn");
        lb.innerHTML = UI.icon("list");
        lb.appendChild(document.createTextNode("List"));
        bar.appendChild(lb);
        const t = trips.find((x) => x.n === trip);
        if (t) {
          bar.appendChild(el("span", "tm-bar-info",
            `${range(t.start, t.end)} · ${t.duration} · ${kmText(t.km)} · top ${num(t.max_speed, 0)} km/h · average ${num(t.avg_speed, 0)} km/h`));
        }
      }
      bar.hidden = !bar.childNodes.length;
      renderTrips(listOpen ? trips : [], tt);
    }
    const kmText = (km) => (km == null ? "—" : (km < 10 ? km.toFixed(1) : num(km, 0)) + " km");
    const tripSwatch = (t, tt) => (modeNow() === "trips" ? tripColour(t.n) : modeNow() === "device" ? tt.colour : cssVar("--accent"));

    // ---- the trip list: one row per trip — when, how long, how far, how fast, and what happened on it
    // (alerts, driving events, overspeed, history packets, data lost while driving); a row picks its trip
    function renderTrips(trips, tt) {
      tripsBox.textContent = "";
      tripsBox.hidden = !trips.length;
      if (!trips.length) return;
      const COLS = [
        ["Trip", ""], ["Start", ""], ["End", ""], ["Took", ""], ["Distance", "num"], ["Top", "num"], ["Average", "num"],
        ["Alerts", "num", "Device alerts: packets other than location updates"], ["Events", "num", "Driving events the device reported"],
        ["Overspeed", "num", "Times over the overspeed limit"], ["History", "num", "Packets sent later, from the store of the device"],
        ["Data lost", "num", "Time without data while driving"],
      ];
      const table = el("table", "tm-trip-table");
      const htr = el("tr");
      COLS.forEach(([h, cls, tip]) => { const th = el("th", cls, h); if (tip) th.dataset.tip = tip; htr.appendChild(th); });
      table.appendChild(el("thead")).appendChild(htr);
      const tb = el("tbody");
      const count = (n) => el("td", "num" + (n ? "" : " zero"), n ? num(n, 0) : "0");
      const lost = (x) => el("td", "num" + (x ? " bad" : " zero"), x ? dur(x) : "0");
      const sum = { km: 0, top: 0, alerts: 0, events: 0, overspeed: 0, history: 0, lost: 0, s: 0 };
      trips.forEach((t) => {
        const tr = el("tr", trip === t.n ? "on" : "");
        const c0 = el("td", "tm-trip-n");
        const sw = el("i");
        sw.style.background = tripSwatch(t, tt);
        c0.append(sw, document.createTextNode(String(t.n)));
        tr.append(c0, el("td", "", when(t.start)), el("td", "", when(t.end)), el("td", "", t.duration || dur(t.seconds)),
                  el("td", "num", kmText(t.km)), el("td", "num", num(t.max_speed, 0) + " km/h"), el("td", "num", num(t.avg_speed, 0) + " km/h"),
                  count(t.alerts), count(t.events), count(t.overspeed), count(t.history), lost(t.lost_s));
        tr.addEventListener("click", () => setTrip(trip === t.n ? null : t.n));
        tb.appendChild(tr);
        sum.km += t.km || 0;
        sum.top = Math.max(sum.top, t.max_speed || 0);
        sum.s += t.seconds || 0;
        ["alerts", "events", "overspeed", "history"].forEach((k) => { sum[k] += t[k] || 0; });
        sum.lost += t.lost_s || 0;
      });
      table.appendChild(tb);
      const ft = el("tr", "tm-trip-sum");
      ft.append(el("td", "", `${trips.length} trip${trips.length === 1 ? "" : "s"}`), el("td"), el("td"), el("td", "", dur(sum.s)),
                el("td", "num", kmText(Math.round(sum.km * 100) / 100)), el("td", "num", num(sum.top, 0) + " km/h"), el("td"),
                count(sum.alerts), count(sum.events), count(sum.overspeed), count(sum.history), lost(sum.lost));
      table.appendChild(el("tfoot")).appendChild(ft);
      tripsBox.appendChild(table);
      const on = tripsBox.querySelector("tbody tr.on");
      if (on) on.scrollIntoView({ block: "nearest" });
    }
    function setFocus(id) {
      if (id === focusId) return;
      focusId = id;
      trip = null;
      render(true);
      if (opts.onFocus) opts.onFocus(id);
    }
    function setTrip(n) {
      const tt = tripTrack();
      if (opts.onTrip && tt) { opts.onTrip(tt.id, n); return; }   // the page decides, then sets it (tm.trip)
      trip = n;
      render(true);
    }

    // ---- the drawing ----------------------------------------------------------------------------
    function render(refit) {
      const m = modeNow();
      seg(modeSeg, [...(multi() ? [["device", "Device", "Each device its own colour"]] : []),
                    ["trips", "Trips", "Each trip its own colour, numbered at its start"],
                    ["speed", "Speed", "Moving, near the limit, over it"]], m, (id) => { mode = id; render(false); });
      seg(drawSeg, [["lines", "Lines"], ["dots", "Dots", "A dot per packet: history small and dim"]], draw, (id) => {
        draw = id;
        try { localStorage.setItem(drawKey, draw); } catch (e) { /* not remembered */ }
        render(false);
      });
      layers.clearLayers();
      const starts = tracks.map((t) => dt((t.series || {}).start)).filter((d) => d && !isNaN(d)).sort((a, b) => a - b);
      day0 = starts.length ? dayOf(starts[0]) : "";
      renderBar();
      const casing = getComputedStyle(box).getPropertyValue("--map-casing").trim() || "#10120f";
      const fade = mapNum("--map-fade", 0.22), hist = mapNum("--map-hist", 0.45);
      const tt = tripTrack();
      const all = [], chosen = [];
      const runs = [];
      const kinds = new Set();
      let anyHistory = false, anyStale = false, anyJump = false, anyEvent = false, anyDup = false;
      arrowPaths = [];
      tracks.forEach((tr) => {
        const s = tr.series || {};
        const trips = (tr.driving || {}).trips || [];
        const off = focusId !== null && tr.id !== focusId;           // another device than the one chosen
        const tripHere = trip !== null && tt && tr.id === tt.id;
        const colourOf = (p, n) => (m === "device" ? tr.colour : m === "trips" ? (n ? tripColour(n) : cssVar("--map-quiet")) : speedColour(p[2], tr));
        (s.track || []).forEach((sg) => {
          let run = null;
          sg.forEach((p) => {
            const n = tripAt(p[3], trips);
            const h = p[4] ? 1 : 0;
            if (h) anyHistory = true;
            const colour = colourOf(p, n);
            if (!run || run.colour !== colour || run.n !== n || run.h !== h) {
              const prev = run && run.pts[run.pts.length - 1];
              const prevK = run && run.ks[run.ks.length - 1];
              run = { tr, off, colour, n, h, pts: prev ? [prev] : [], ks: prev ? [prevK] : [], joined: !!prev,
                      faded: off || (tripHere && n !== trip) };
              runs.push(run);
            }
            run.pts.push([p[0], p[1]]);
            run.ks.push(p[3]);
            all.push([p[0], p[1]]);
            if (!off && ((tripHere && n === trip) || (!tripHere && focusId !== null))) chosen.push([p[0], p[1]]);
          });
        });
        // the stretches that get direction arrows: each trip (by trip), every stretch moving (otherwise)
        if (off) return;
        (s.track || []).forEach((sg) => {
          let cur = null, key = null;
          sg.forEach((p) => {
            const n = tripAt(p[3], trips);
            const moving = m === "trips" ? n !== null : p[2] > 3;
            if (!moving || (tripHere && n !== trip)) { cur = null; return; }
            const k = m === "trips" ? n : 1;
            if (!cur || key !== k) { cur = []; key = k; arrowPaths.push(cur); }
            cur.push([p[0], p[1], m === "trips" ? tripColour(n) : m === "device" ? tr.colour : speedColour(p[2], tr)]);
          });
        });
      });
      if (draw === "dots") arrowPaths = [];
      // drawn bottom to top: faded, not in a trip, history, live in a trip
      const rank = (r) => (r.faded ? 0 : r.n === null && m === "trips" ? 1 : r.h ? 2 : 3);
      // a click on a line picks the packet nearest the click
      const pickOn = (r) => (e) => {
        let best = 0, bd = Infinity;
        r.pts.forEach((q, i) => { const dd = map.distance(e.latlng, q); if (dd < bd) { bd = dd; best = i; } });
        selectPoint(r.tr, r.ks[best], r.pts[best]);
      };
      const order = runs.filter((r) => r.pts.length > 0).sort((a, b) => rank(a) - rank(b));
      lodLines = [];
      dotItems = [];
      if (draw === "dots") {
        // one dot per packet: live bright, history small and dim — each picks its packet; drawn in the view,
        // one per screen pixel (a dot under another one exactly), so a long log stays quick at any zoom
        order.forEach((r) => {
          const inTrip = r.n !== null;
          const style = { renderer: dotRenderer, radius: r.h ? 2.6 : inTrip || m !== "trips" ? 3.6 : 2.8, color: casing,
                          weight: r.h ? 0.5 : 1, fillColor: r.colour, fillOpacity: r.faded ? fade : r.h ? hist : 1,
                          opacity: r.faded ? fade * 0.7 : r.h ? hist * 0.8 : 0.8 };
          r.pts.forEach((q, i) => {
            if (i === 0 && r.joined) return;
            dotItems.push({ q, style, tr: r.tr, k: r.ks[i] });
          });
        });
      } else {
        order.forEach((r) => {
          const inTrip = r.n !== null;
          if (r.pts.length === 1) {                    // a lone packet (between two breaks): a dot, never left out
            L.circleMarker(r.pts[0], { renderer: dotRenderer, radius: r.h ? 2.8 : 3.6, color: casing, weight: 1, fillColor: r.colour,
                                       fillOpacity: r.faded ? fade : r.h ? hist : 1, opacity: r.faded ? fade * 0.7 : 0.8 })
              .on("click", () => selectPoint(r.tr, r.ks[0], r.pts[0])).addTo(layers);
            return;
          }
          // the line's points that matter at this zoom (within half a pixel of every packet; Leaflet's own
          // simplification off, smoothFactor 0, so nothing moves the line further); a click
          // still picks the nearest packet of the whole run
          const lod = { run: r, sig: importance(r.pts), lines: [] };
          const line = (opt) => { const l = L.polyline(lodPts(lod), { smoothFactor: 0, ...opt }); lod.lines.push(l); return l; };
          lodLines.push(lod);
          if (r.faded) {
            line({ color: r.colour, weight: inTrip && !r.h ? 3 : 2, opacity: fade }).on("click", pickOn(r)).addTo(layers);
            return;
          }
          if (r.h) {                                   // history: the same colour, dimmed and thin
            line({ color: r.colour, weight: inTrip || m !== "trips" ? 2.5 : 1.8, opacity: hist })
              .bindTooltip(tripTip(r.tr, r.n, true), TIP).on("click", pickOn(r)).addTo(layers);
            return;
          }
          const bold = inTrip || m !== "trips";
          if (bold) line({ color: casing, weight: inTrip ? 8 : 6, opacity: 0.6 }).addTo(layers);
          line({ color: r.colour, weight: bold ? (inTrip ? 4.5 : 3.5) : 2.5, opacity: bold ? 1 : 0.7 })
            .bindTooltip(tripTip(r.tr, r.n, false), TIP).on("click", pickOn(r)).addTo(layers);
        });
      }
      drawDots();
      tracks.forEach((tr) => {
        const s = tr.series || {};
        const d = tr.driving || {};
        const trips = d.trips || [];
        const off = focusId !== null && tr.id !== focusId;
        const tripHere = trip !== null && tt && tr.id === tt.id;
        const inChosen = (k) => !off && (!tripHere || tripAt(k, trips) === trip);
        // no data: dotted from the last position before the gap to the first after it
        (s.breaks || []).forEach((g) => {
          kinds.add(g.kind);
          const [tone, words] = BREAK[g.kind] || BREAK.unknown;
          const on = inChosen(g.k) || inChosen(g.k_from);
          L.polyline([g.from, g.to], { color: cssVar(tone), weight: 3.5, dashArray: "1 9", lineCap: "round", opacity: on ? 0.95 : Math.max(0.3, fade) })
            .bindTooltip(gapTip(tr, g, words), TIP).on("click", () => selectGap(tr, g)).addTo(layers);
        });
        (s.stale || []).forEach((x) => {
          if (!x.stale) return;
          anyStale = true;
          const real = x.stale[0] === x.from[0] && x.stale[1] === x.from[1] ? x.to : x.from;
          const on = inChosen(x.k);
          L.polyline([real, x.stale], { color: cssVar("--danger"), weight: 3, dashArray: "6 6", opacity: on ? 0.9 : Math.max(0.35, fade) })
            .bindTooltip(hopTip(tr, x), TIP).on("click", () => selectHop(tr, x)).addTo(layers);
          L.circleMarker(x.stale, { radius: 7, color: cssVar("--danger"), weight: 2, fillOpacity: 0.25, opacity: on ? 1 : fade })
            .bindTooltip(hopTip(tr, x), TIP).on("click", () => selectHop(tr, x)).addTo(layers);
        });
        (s.jumps || []).forEach((x) => {
          anyJump = true;
          L.polyline([x.from, x.to], { color: cssVar("--warn"), weight: 3, dashArray: "4 6", opacity: inChosen(x.k) ? 0.9 : Math.max(0.35, fade) })
            .bindTooltip(hopTip(tr, x), TIP).on("click", () => selectHop(tr, x)).addTo(layers);
        });
        // one moment in several packets: a ring with how many, where they stood — a pile of duplicates
        // otherwise reads as one point, and the frame numbers it used as packets missing
        (s.dups || []).forEach((x) => {
          if (!x.at) return;
          anyDup = true;
          const on = inChosen(x.k);
          const icon = L.divIcon({ className: "tm-pin-wrap", html: `<span class="tm-dup">${x.count > 999 ? "999+" : x.count}</span>`,
                                   iconSize: [26, 26], iconAnchor: [13, 13] });
          L.marker(x.at, { icon, keyboard: false, opacity: on ? 1 : Math.max(0.35, fade), zIndexOffset: 400 })
            .bindTooltip(cardTip(named(tr, `${x.count} packets at one time · ${when(x.time, true)}`),
              { body: [["Position", pos(x.at)], ["Frame numbers", x.frames ? `${x.frames[0]} to ${x.frames[1]}` : "—"],
                       ["History", `${x.history} of ${x.count}`]] },
              [[x.same ? "The same data each time — the same packet made again" : "Different data at one time — the clock did not move", null]]), TIP)
            .on("click", () => selectPoint(tr, x.k, x.at)).addTo(layers);
        });
        if (off) return;
        // each trip numbered at its start, its end ringed; the chosen one labelled with its times
        trips.forEach((t) => {
          if (tripHere && t.n !== trip) return;
          const colour = m === "trips" ? tripColour(t.n) : m === "device" ? tr.colour : cssVar("--accent");
          if (t.to && t.to[0] != null) {
            pin(t.to, "", colour, "end").bindTooltip(named(tr, `End ${when(t.end).slice(-8)}`),
              { permanent: tripHere && t.n === trip, direction: "auto", offset: [10, 0], className: "tm-tipbox tm-pintip" }).addTo(layers);
          }
          if (t.from && t.from[0] != null) {
            pin(t.from, String(t.n), colour).bindTooltip(named(tr, `Start ${when(t.start).slice(-8)}`),
              { permanent: tripHere && t.n === trip, direction: "auto", offset: [12, 0], className: "tm-tipbox tm-pintip" })
              .on("click", () => {
                if (multi() && focusId !== tr.id) { focusId = tr.id; if (opts.onFocus) opts.onFocus(tr.id); }
                setTrip(trip === t.n ? null : t.n);
              }).addTo(layers);
          }
        });
        (d.events || []).forEach((e) => {
          if (!e.at || !e.at[0]) return;
          if (tripHere && tripAt(e.k, trips) !== trip) return;
          anyEvent = true;
          L.circleMarker(e.at, { radius: 6, color: casing, weight: 1.5, fillColor: cssVar("--info"), fillOpacity: 1 })
            .bindTooltip(cardTip(named(tr, `Driving event · ${e.name || "code " + e.code}`),
              { body: [["Time", when(e.time, true)], ["Speed", num(e.speed, 0) + " km/h"], ["Position", pos(e.at)]] }), TIP)
            .on("click", () => selectPoint(tr, e.k, e.at)).addTo(layers);
        });
        // where it stood last (the only mark at all for a vehicle that never moved)
        const lastSeg = (s.track || []).length ? s.track[s.track.length - 1] : null;
        const last = lastSeg && lastSeg[lastSeg.length - 1];
        if (last && !tripHere) {
          pin([last[0], last[1]], "", m === "device" ? tr.colour : cssVar("--text"), "last")
            .bindTooltip(cardTip(named(tr, "Last position"), { body: [["Time", when(s.end, true)], ["Position", pos([last[0], last[1]])]] }), TIP)
            .on("click", () => selectPoint(tr, last[3], [last[0], last[1]])).addTo(layers);
          if (!(s.track || []).some((sg) => sg.length)) all.push([last[0], last[1]]);
        }
      });
      if (refit) {
        const fit = chosen.length ? chosen : all;
        setTimeout(() => {
          map.invalidateSize();
          if (fit.length) map.fitBounds(fit, { padding: [28, 28], maxZoom: 17, animate: false });
          drawArrows();
          markPoint();
        }, 50);
      } else {
        drawArrows();
        markPoint();
      }
      renderLegend(m, anyHistory, kinds, anyStale, anyJump, anyEvent, anyDup);
    }

    // a line's points at the zoom on screen: those that move it by more than half a pixel
    function lodPts(lod) {
      const pts = lod.run.pts;
      if (pts.length < 3) return pts;
      const tol = halfPixel(map.getZoom(), pts[0][0]);
      const out = [];
      for (let i = 0; i < pts.length; i++) if (lod.sig[i] > tol) out.push(pts[i]);
      return out;
    }
    function applyLod() {
      lodLines.forEach((lod) => { const pts = lodPts(lod); lod.lines.forEach((l) => l.setLatLngs(pts)); });
    }
    // the dots in the view (and a margin): one per screen pixel, the one drawn on top kept
    function drawDots() {
      dotLayer.clearLayers();
      if (!dotItems.length) return;
      const view = map.getBounds().pad(0.25);
      const seen = new Set();
      const keep = [];
      for (let i = dotItems.length - 1; i >= 0; i--) {
        const d = dotItems[i];
        if (!view.contains(d.q)) continue;
        const p = map.latLngToLayerPoint(d.q);
        const key = Math.round(p.x) + "," + Math.round(p.y);
        if (seen.has(key)) continue;
        seen.add(key);
        keep.push(d);
      }
      for (let i = keep.length - 1; i >= 0; i--) {
        const d = keep[i];
        L.circleMarker(d.q, d.style).on("click", () => selectPoint(d.tr, d.k, d.q)).addTo(dotLayer);
      }
    }

    function renderLegend(m, anyHistory, kinds, anyStale, anyJump, anyEvent, anyDup) {
      legend.textContent = "";
      const lim = Math.min(...tracks.map((t) => (t.settings && t.settings.overspeed_kmh) || 80), 80);
      const dots = draw === "dots";
      const items = [["Live", "--text", dots ? "dot" : "line"]]
        .concat(anyHistory ? [["History", "--text", dots ? "dot dim" : "line thin"]] : [])
        .concat(kinds.has("loss") ? [["Data lost", "--danger", "dots"]] : [], kinds.has("sleep") ? [["Asleep", "--map-asleep", "dots"]] : [])
        .concat(m === "trips" ? [["Not in a trip", "--map-quiet", "line"]] : [])
        .concat(m === "speed" ? [[`Over ${lim} km/h`, "--danger"], ["Moving", "--accent"], ["Standing", "--map-quiet"]] : [])
        .concat(anyStale ? [["Stale position", "--danger", "dot"]] : [], anyJump ? [["Jump", "--warn", "line"]] : [],
                anyEvent ? [["Driving event", "--info", "dot"]] : [], anyDup ? [["Same time", "--warn", "ring"]] : []);
      items.forEach(([t, c, k]) => {
        const sp = el("span");
        const i = el("i", k || "line");
        i.style.setProperty("--c", `var(${c})`);
        sp.append(i, document.createTextNode(t));
        legend.appendChild(sp);
      });
    }

    // direction: an arrow every ARROW_GAP pixels along each moving stretch, pointing the way it went —
    // placed again on every zoom so they stay evenly spaced on screen
    function drawArrows() {
      arrowLayer.clearLayers();
      const casing = getComputedStyle(box).getPropertyValue("--map-casing").trim() || "#10120f";
      let count = 0;
      arrowPaths.forEach((path) => {
        let carry = ARROW_GAP / 2;
        for (let i = 1; i < path.length && count < ARROW_MAX; i++) {
          const a = map.latLngToLayerPoint([path[i - 1][0], path[i - 1][1]]);
          const b = map.latLngToLayerPoint([path[i][0], path[i][1]]);
          const len = a.distanceTo(b);
          if (len < 0.5) continue;
          let at = carry;
          const deg = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
          while (at <= len && count < ARROW_MAX) {
            const f = at / len;
            const ll = map.layerPointToLatLng(L.point(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f));
            const icon = L.divIcon({
              className: "tm-arrow-wrap", iconSize: [18, 18], iconAnchor: [9, 9],
              html: `<span class="tm-arrow" style="color:${path[i][2]};--ac:${casing};transform:rotate(${deg.toFixed(0)}deg)">${UI.icons.direction}</span>`,
            });
            L.marker(ll, { icon, interactive: false, keyboard: false, zIndexOffset: -500 }).addTo(arrowLayer);
            count++;
            at += ARROW_GAP;
          }
          carry = at - len;
        }
      });
    }

    // ---- what is picked: a ring on the map, in the page's text colour ---------------------------------
    function selectPoint(tr, k, latlng) {
      if (k == null) return;
      pick = { id: tr.id, k, ll: latlng || null };
      markPoint();
      if (opts.onPoint) opts.onPoint(tr.id, k, latlng);
    }
    function selectGap(tr, g) {
      pick = { id: tr.id, gap: Object.assign({}, g, { _type: "gap" }) };
      markPoint();
      if (opts.onGap) opts.onGap(tr.id, pick.gap);
    }
    function selectHop(tr, x) {
      pick = { id: tr.id, gap: Object.assign({}, x, { _type: x.kind === "stale" ? "stale" : "jump" }) };
      markPoint();
      if (opts.onGap) opts.onGap(tr.id, pick.gap);
    }
    function markPoint() {
      pointLayer.clearLayers();
      if (!pick) return;
      const ink = cssVar("--text");
      if (pick.gap) {
        const g = pick.gap;
        const hop = g._type === "jump" || g._type === "stale";
        L.polyline([g.from, g.to], { color: ink, weight: 10, opacity: 0.22, lineCap: "round", interactive: false }).addTo(pointLayer);
        L.polyline([g.from, g.to], { color: cssVar(hop ? (g._type === "stale" ? "--danger" : "--warn") : g.kind === "loss" ? "--danger" : g.kind === "sleep" ? "--map-asleep" : "--warn"),
                                     weight: 4, dashArray: hop ? "6 6" : "1 9", lineCap: "round", interactive: false }).addTo(pointLayer);
        [g.from, g.to].forEach((q) => L.circleMarker(q, { radius: 7, color: ink, weight: 2.5, fill: false, interactive: false }).addTo(pointLayer));
        return;
      }
      if (!pick.ll) return;
      L.circleMarker(pick.ll, { radius: 10, color: ink, weight: 2.5, fill: false, interactive: false }).addTo(pointLayer);
      L.circleMarker(pick.ll, { radius: 4, color: ink, weight: 0, fillColor: ink, fillOpacity: 1, interactive: false }).addTo(pointLayer);
    }
    // a track's point by its row: where it is, when it has a position
    function pointOf(tr, k) {
      for (const sg of (tr.series || {}).track || []) for (const p of sg) if (p[3] === k) return [p[0], p[1]];
      return null;
    }

    // ---- full screen: the card fills the window (the page may keep a pane at its right edge) ----------
    function fitFull() {
      if (full) card.style.right = (opts.fullRight ? opts.fullRight() : 0) + "px";
    }
    function setFull(on) {
      if (on === full) return;
      full = on;
      card.classList.toggle("tm-is-full", on);
      document.body.classList.toggle("tm-full", on);
      if (!on) card.style.right = "";
      if (fullBtn) {
        fullBtn.innerHTML = UI.icon(on ? "minimize" : "maximize");
        fullBtn.title = on ? "Exit full screen" : "Full screen";
        fullBtn.setAttribute("aria-label", fullBtn.title);
      }
      try {
        if (on && document.documentElement.requestFullscreen && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
        if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
      } catch (e) { /* the page fills the window instead */ }
      if (opts.onFull) opts.onFull(on, floatBox);
      fitFull();
      setTimeout(() => { fitFull(); map.invalidateSize(); }, 120);
      setTimeout(() => map.invalidateSize(), 400);
    }
    document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && full) setFull(false); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && full && !document.querySelector("dialog[open]")) setFull(false);
    });
    window.addEventListener("resize", fitFull);

    return {
      el: card,
      map,
      // the tracks to draw; refit: zoom to them (a live update keeps the reader's view)
      set(list, o) {
        tracks = (list || []).filter((t) => t && t.series);
        if (focusId !== null && !byId(focusId)) { focusId = null; trip = null; }
        const tt = tripTrack();
        if (o && "trip" in o) trip = o.trip == null ? null : o.trip;
        if (trip !== null && !(tt && ((tt.driving || {}).trips || []).some((t) => t.n === trip))) trip = null;
        if (pick && !byId(pick.id)) pick = null;
        render(!!(o && o.refit));
      },
      // ring a track's point (its row k); pan: bring it into view (a row picked in a table)
      mark(id, k, pan, at) {
        const tr = byId(id);
        const ll = at || (tr && k != null ? pointOf(tr, k) : null);
        pick = tr && k != null ? { id, k, ll } : null;
        markPoint();
        if (ll && pan !== false) map.panTo(ll);
      },
      clearMark() { pick = null; markPoint(); },
      // the promise of the level of detail, measured: at this zoom, how far (screen pixels) the packet
      // furthest from its drawn line lies, over every line — and how many points are drawn of how many
      accuracy() {
        let worst = 0, all = 0, drawn = 0;
        const segDist = (p, a, b) => {
          const dx = b.x - a.x, dy = b.y - a.y, ll = dx * dx + dy * dy;
          let t = ll ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / ll : 0;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
        };
        lodLines.forEach((lod) => {
          const pts = lod.run.pts, keep = lodPts(lod);
          all += pts.length;
          drawn += keep.length;
          const z = map.getZoom();
          const kp = keep.map((q) => map.project(q, z));             // exact (unrounded) pixels
          let j = 0;
          const index = new Map(keep.map((q, i) => [q, i]));
          pts.forEach((q) => {
            if (index.has(q)) { j = index.get(q); return; }
            const p = map.project(q, z);
            if (j + 1 < kp.length) worst = Math.max(worst, segDist(p, kp[j], kp[j + 1]));
          });
        });
        return { points: all, drawn, worstPx: Math.round(worst * 100) / 100 };
      },
      // ring a stretch without data, a stale position or a jump (as onGap gave it: its _type kept)
      markGap(id, g) { pick = g ? { id, gap: Object.assign({ _type: "gap" }, g) } : null; markPoint(); },
      // the trip of the focused (or only) track, set by the page — no onTrip back
      trip(n) { trip = n == null ? null : n; render(true); },
      fitFull,
      // one line over the map: why there is nothing to draw (an empty or failed state); "" hides it
      note(text) { noteBox.textContent = text || ""; noteBox.hidden = !text; },
      focus(id) { setFocus(id && byId(id) ? id : null); },
      invalidate() { map.invalidateSize(); },
      setFull,
    };
  }

  const instances = [];                  // every map made on the page (tests time and drive them)
  window.RunwayMap = { track: (host, opts) => { const t = track(host, opts); instances.push(t); return t; }, tripColour, instances };
})();
