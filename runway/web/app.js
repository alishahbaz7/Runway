/* Runway dashboard - live map, device list and packet inspector. */
(() => {
  "use strict";

  const MAX_ROWS = 500;
  const MAX_TRAIL = 5000;   // points kept per device trail (the page may stay open for weeks)
  const COLORS = ["#3fb6ff", "#00d18f", "#ffb454", "#ff6b6b", "#b78bff",
                  "#5ad1c8", "#f78fb3", "#9ccc65"];

  const state = {
    packets: [],            // newest-last
    devices: new Map(),     // id -> summary
    hidden: new Set(),      // device ids hidden from map+table (default: all shown)
    seenIo: new Set(),      // io_<id> keys actually received (drives the column picker)
    selectedSeq: null,
    markers: new Map(),     // id -> L.marker
    trails: new Map(),      // id -> L.polyline
    paths: new Map(),       // id -> [{t, ll}] in device time: what the trail draws
    colorIdx: 0,
    colorOf: new Map(),
    hl: null,               // map highlight for the clicked packet
  };
  const LIVE_MS = 30000;    // a device "communicating" if seen within this window

  // ---- DOM helpers --------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "—");
  // Shared helpers from common.js — CAN frame rendering. Guarded so a stale cached index.html
  // that hasn't picked up the <script> tag yet degrades to the plain JSON view, not an error.
  const RW = window.Runway || {};
  const canOf = (r) => (RW.canOf ? RW.canOf(r) : []);
  const canSignalsOf = (r) => (RW.canSignalsOf ? RW.canSignalsOf(r) : []);
  const colorFor = (id) => {
    if (!state.colorOf.has(id)) {
      state.colorOf.set(id, COLORS[state.colorIdx++ % COLORS.length]);
    }
    return state.colorOf.get(id);
  };

  // ---- Packet table columns (customizable, mirrors the Parser params) ----
  const COLS_KEY = "runway.dash.cols";
  const num = (v) => (v == null ? "—" : Math.round(v * 100) / 100);
  const def = (v) => (v == null ? "—" : v);
  const bool = (v) => (v == null ? "—" : v ? "yes" : "no");
  const posStr = (r) => (r.position && r.position.latitude != null
    ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "—");

  const STD_COLS = [
    { key: "seq",         label: "Sr No",             get: (r) => r.seq, cls: "muted" },
    { key: "time",        label: "Time",              get: (r) => fmtTime(r.event_time || r.received_at) },
    { key: "device",      label: "Device",            get: (r) => r.device_id },
    { key: "proto",       label: "Proto",             get: (r) => r.protocol },
    { key: "type",        label: "Type",              get: (r) => r.message_type || "—" },
    { key: "latlon",      label: "Lat, Lon",          get: posStr },
    { key: "altitude",    label: "Alt (m)",           get: (r) => num(r.altitude_m) },
    { key: "heading",     label: "Heading",           get: (r) => num(r.heading_deg) },
    { key: "satellites",  label: "Satellites",        get: (r) => def(r.satellites) },
    { key: "speed",       label: "Speed (km/h)",      get: (r) => num(r.speed_kph) },
    { key: "external_v",  label: "Input voltage (V)", get: (r) => num(r.external_v) },
    { key: "battery_pct", label: "Battery level (%)", get: (r) => num(r.battery_pct) },
    { key: "battery_v",   label: "Battery (V)",       get: (r) => num(r.battery_v) },
    { key: "ignition",    label: "Ignition",          get: (r) => bool(r.ignition) },
    { key: "gsm",         label: "GSM",               get: (r) => def(r.gsm_signal) },
    { key: "odometer",    label: "Odometer (m)",      get: (r) => num(r.odometer_m) },
    // Protocols that carry the vehicle bus inline (Zenithra Ax1 sCAN): how many CAN frames
    // this packet brought. Opt-in via the Columns picker — 0 is a normal packet, not an error.
    { key: "can",         label: "CAN frames",        get: (r) => def(r.io && r.io.can_frame_count) },
    { key: "can_signals", label: "CAN signals",       get: (r) => def(r.io && r.io.can_signal_count) },
  ];
  let paramMap = {};         // AVL id -> {name, unit} (just for labelling; from /api/params)
  const DEFAULT_COLS = ["seq", "time", "latlon", "satellites", "speed", "external_v", "battery_pct"];
  let visibleCols = loadCols();

  function loadCols() {
    try { const v = JSON.parse(localStorage.getItem(COLS_KEY) || "null"); if (Array.isArray(v) && v.length) return v; } catch (e) { /* ignore */ }
    return DEFAULT_COLS.slice();
  }
  const saveCols = () => { try { localStorage.setItem(COLS_KEY, JSON.stringify(visibleCols)); } catch (e) { /* ignore */ } };

  // Only AVL params that have actually arrived become columns (labelled like the Parser).
  function seenAvlCols() {
    return [...state.seenIo]
      .map((key) => parseInt(key.slice(3), 10))
      .filter((id) => !isNaN(id))
      .sort((a, b) => a - b)
      .map((id) => {
        const p = paramMap[id];
        const name = p ? p.name + (p.unit ? ` (${p.unit})` : "") : `AVL ${id}`;
        return { key: "io_" + id, label: `${name} · AVL ${id}`,
                 get: (r) => def(r.io ? r.io["io_" + id] : undefined) };
      });
  }
  const allCols = () => STD_COLS.concat(seenAvlCols());
  const colByKey = (k) => allCols().find((c) => c.key === k);
  const noteIo = (rec) => { if (rec && rec.io) for (const k in rec.io) state.seenIo.add(k); };

  async function loadParams() {
    try {
      const data = await api("/api/params");
      (data.avl || []).forEach((p) => { paramMap[p.id] = { name: p.name, unit: p.unit }; });
    } catch (e) { console.error(e); }
  }

  // ---- Map ---------------------------------------------------------------
  const map = L.map("map", { zoomControl: true }).setView([20.59, 78.96], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);

  // The trail runs in device time, not arrival order. A device back from an outage sends its
  // stored (history) packets in between live ones; joined as they arrived, each drew a line
  // back to an old position and forward again — a fan. A late point slots into its place and
  // the marker stays on the newest position. A record without a device time (a login's last
  // known fix) only places the marker until timed points arrive: the time it was received is
  // another clock, and in a replay it is "now" — it sorted after every packet.
  function plot(rec) {
    const p = rec.position;
    if (!p || p.latitude == null || p.longitude == null) return;
    if (p.latitude === 0 && p.longitude === 0) return;
    if (state.hidden.has(rec.device_id)) return;
    const id = rec.device_id;
    const ll = [p.latitude, p.longitude];
    const color = colorFor(id);
    const t = Date.parse(rec.event_time);

    let path = state.paths.get(id);
    if (!path) { path = []; state.paths.set(id, path); }
    let marker = state.markers.get(id);
    if (!marker) {
      marker = L.circleMarker(ll, { radius: 6, color, fillColor: color, fillOpacity: 0.9 });
      marker.addTo(map).bindTooltip(id, { permanent: false });
      state.markers.set(id, marker);
    }
    if (isNaN(t)) {
      if (!path.length) marker.setLatLng(ll);
      return;
    }
    let i = path.length;
    while (i > 0 && path[i - 1].t > t) i--;          // live packets: no steps, appended
    path.splice(i, 0, { t, ll });
    if (path.length > MAX_TRAIL) path.shift();
    const newest = i >= path.length - 1;
    if (newest) marker.setLatLng(ll);
    let trail = state.trails.get(id);
    if (!trail) {
      trail = L.polyline([ll], { color, weight: 2, opacity: 0.7 }).addTo(map);
      state.trails.set(id, trail);
    } else if (newest && path.length < MAX_TRAIL) {
      trail.addLatLng(ll);
    } else {
      trail.setLatLngs(path.map((pt) => pt.ll));
    }
  }

  function refitMap() {
    const pts = [];
    state.markers.forEach((m) => pts.push(m.getLatLng()));
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 15 });
  }

  function clearMap() {
    state.markers.forEach((m) => map.removeLayer(m));
    state.trails.forEach((t) => map.removeLayer(t));
    state.markers.clear();
    state.trails.clear();
    state.paths.clear();
    if (state.hl) { map.removeLayer(state.hl); state.hl = null; }
  }

  // ---- Records -----------------------------------------------------------
  function ingest(rec) {
    state.packets.push(rec);
    if (state.packets.length > MAX_ROWS) state.packets.shift();
    noteIo(rec);
    upsertDevice(rec);
    plot(rec);
    renderRowsThrottled();
  }

  function upsertDevice(rec) {
    const d = state.devices.get(rec.device_id) || { device_id: rec.device_id, count: 0 };
    d.count += 1;
    d.protocol = rec.protocol;
    d.last_seen = rec.received_at;
    d.last_message_type = rec.message_type;
    d.transport = rec.transport;          // provenance, for the source badge
    d.peer = rec.peer;
    if (rec.position && rec.position.latitude != null) {
      d.last_position = { lat: rec.position.latitude, lon: rec.position.longitude };
    }
    state.devices.set(rec.device_id, d);
    renderDevices();
  }

  // ---- Rendering: devices -------------------------------------------------
  const liveOf = (d) => {
    const t = d.last_seen ? Date.parse(d.last_seen) : 0;
    return t && (Date.now() - t) < LIVE_MS;
  };
  function srcOf(d) {                      // where the data came from (parsed tab / TCP port)
    const t = d.transport || (d.last && d.last.transport) || "";
    const p = d.peer || (d.last && d.last.peer) || "";
    if (t === "parser") return p || "parsed";
    if (t === "tcp" || t === "udp") return ":" + (String(p).split(":").pop() || "?");
    if (t === "file") return "replay";
    return t || "stored";
  }
  function redrawMap() { clearMap(); state.packets.forEach(plot); refitMap(); }

  function renderDevices() {
    const ul = $("devices");
    const q = ($("dev-search").value || "").toLowerCase();
    const list = [...state.devices.values()].filter((d) =>
      d.device_id.toLowerCase().includes(q));
    ul.innerHTML = "";
    if (!list.length) { ul.appendChild(el("li", "empty", "No devices yet")); return; }
    for (const d of list) {
      const hidden = state.hidden.has(d.device_id);
      const live = liveOf(d);
      const li = el("li", hidden ? "off" : "");

      const cb = el("input", "dev-cb"); cb.type = "checkbox"; cb.checked = !hidden;
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        if (cb.checked) state.hidden.delete(d.device_id); else state.hidden.add(d.device_id);
        redrawMap(); renderRows(); renderDevices();
      };

      // When the device id is just a placeholder (no IMEI yet), show the source name
      // (e.g. the Parser tab name) as the title instead of "…-unknown".
      const src = srcOf(d);
      const unknown = /unknown$/i.test(d.device_id) || d.device_id === "unknown";
      const title = unknown && src ? src : d.device_id;

      const idLine = el("div", "id");
      idLine.append(Object.assign(el("span", "dot"), { style: `background:${colorFor(d.device_id)}` }),
                    document.createTextNode(title));
      const meta = el("div", "meta");
      meta.appendChild(el("span", "live-badge " + (live ? "on" : "idle"), live ? "Live" : "Idle"));
      if (src && src !== title) meta.appendChild(el("span", "src-badge", src));
      meta.appendChild(el("span", "", `${d.count} pkts`));
      meta.appendChild(el("span", "muted", fmtTime(d.last_seen)));
      const body = el("div", "dev-body");
      body.append(idLine, meta);
      li.append(cb, body);
      li.onclick = () => { cb.checked = !cb.checked; cb.onchange(); };  // row toggles too
      ul.appendChild(li);
    }
    const nd = state.devices.size;
    $("stat-devices").textContent = `${nd} device${nd === 1 ? "" : "s"}`;
  }

  // ---- Rendering: packet table -------------------------------------------
  let rowsPending = false;
  function renderRowsThrottled() {
    if (rowsPending) return;
    rowsPending = true;
    requestAnimationFrame(() => { rowsPending = false; renderRows(); });
  }

  function renderRows() {
    const cols = visibleCols.map(colByKey).filter(Boolean);
    const head = $("packet-head");
    head.innerHTML = "";
    const htr = el("tr");
    cols.forEach((c) => htr.appendChild(el("th", "", c.label)));
    head.appendChild(htr);

    const tbody = $("packet-rows");
    const rows = state.packets
      .filter((r) => !state.hidden.has(r.device_id))
      .slice(-MAX_ROWS).reverse();
    tbody.innerHTML = "";
    if (!rows.length) {
      const tr = el("tr"); const td = el("td", "empty", "Waiting for packets");
      td.colSpan = cols.length || 1; tr.appendChild(td); tbody.appendChild(tr);
    } else {
      for (const r of rows) {
        const tr = el("tr");
        if (r.seq === state.selectedSeq) tr.className = "sel";
        cols.forEach((c) => tr.appendChild(el("td", c.cls || "", String(c.get(r)))));
        tr.onclick = () => showDetail(r);
        tbody.appendChild(tr);
      }
    }
    // header pill = TOTAL packets across all devices (matches the device counts);
    // the table itself is only the recent rolling window.
    const total = [...state.devices.values()].reduce((s, d) => s + (d.count || 0), 0);
    $("stat-packets").textContent = `${total} packet${total === 1 ? "" : "s"}`;
    const lbl = $("packets-label");
    if (lbl) {
      lbl.textContent = "Recent packets ";
      lbl.appendChild(el("span", "ui-count", String(rows.length)));
    }
  }

  // Column picker popover: pick which signals show as columns (standard + AVL params).
  function toggleColsPicker() {
    const existing = document.getElementById("cols-pop");
    if (existing) { existing.remove(); return; }
    const pop = el("div", "cols-pop"); pop.id = "cols-pop";
    const search = el("input", "cols-search"); search.placeholder = "Search";
    const list = el("div", "cols-list");
    pop.append(search, list);

    function build(q) {
      list.innerHTML = "";
      const add = (c) => {
        if (q && !c.label.toLowerCase().includes(q)) return;
        const row = el("label", "cols-row");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = visibleCols.includes(c.key);
        cb.onchange = () => {
          if (cb.checked) { if (!visibleCols.includes(c.key)) visibleCols.push(c.key); }
          else visibleCols = visibleCols.filter((k) => k !== c.key);
          saveCols(); renderRows();
        };
        row.append(cb, document.createTextNode(" " + c.label));
        list.appendChild(row);
      };
      list.appendChild(el("div", "cols-group", "Standard"));
      STD_COLS.forEach(add);
      const seen = seenAvlCols();
      list.appendChild(el("div", "cols-group", "Device parameters (received)"));
      if (seen.length) seen.forEach(add);
      else list.appendChild(el("div", "hint", "None received yet"));
    }
    build("");
    search.addEventListener("input", () => build(search.value.toLowerCase()));

    document.body.appendChild(pop);
    const b = $("cols-btn").getBoundingClientRect();
    pop.style.top = `${b.bottom + 4}px`;
    pop.style.left = `${Math.max(8, b.right - pop.offsetWidth)}px`;
    setTimeout(() => document.addEventListener("click", onDoc), 0);
    function onDoc(e) {
      if (!pop.contains(e.target) && e.target !== $("cols-btn")) {
        pop.remove(); document.removeEventListener("click", onDoc);
      }
    }
  }

  // ---- Rendering: detail panel -------------------------------------------
  function highlightAt(ll) {                 // ring the clicked packet on the map
    if (state.hl) map.removeLayer(state.hl);
    state.hl = L.circleMarker(ll, { radius: 9, color: "#ffb454", weight: 3, fill: false }).addTo(map);
  }
  function showDetail(rec) {
    state.selectedSeq = rec.seq;
    renderRows();
    const p = rec.position;
    if (p && p.latitude != null && !(p.latitude === 0 && p.longitude === 0)) {
      map.panTo([p.latitude, p.longitude]);
      highlightAt([p.latitude, p.longitude]);
    }
    const body = $("detail-body");
    body.innerHTML = "";
    const kv = el("div", "kv");
    const add = (k, v) => { if (v == null || v === "") return;
      kv.append(el("div", "k", k), el("div", "v", String(v))); };
    add("Device", rec.device_id);
    add("Protocol", rec.protocol);
    add("Message", rec.message_type);
    add("Event time", rec.event_time);
    add("Received", rec.received_at);
    if (rec.position) add("Position", `${rec.position.latitude}, ${rec.position.longitude}`);
    add("Speed", rec.speed_kph != null ? rec.speed_kph + " km/h" : null);
    add("Heading", rec.heading_deg != null ? rec.heading_deg + "°" : null);
    add("Satellites", rec.satellites);
    add("Ignition", rec.ignition);
    add("GSM", rec.gsm_signal);
    add("Battery", rec.battery_v != null ? rec.battery_v + " V" : null);
    if (rec.alarms && rec.alarms.length) add("Alarms", rec.alarms.join(", "));
    add("Transport", rec.transport);
    add("Peer", rec.peer);
    body.appendChild(kv);

    // Protocols that carry the vehicle bus inline (Zenithra Ax1 sCAN): the frames get their
    // own table right under the field list — a single packet can bring dozens of them, which
    // buries every other IO field when left in the JSON dump.
    // Decoded signals first — the frames below them are the raw evidence.
    const signals = canSignalsOf(rec);
    if (signals.length) {
      const sigName = (rec.io && rec.io.can_signature) ? ` · ${rec.io.can_signature}` : "";
      body.appendChild(el("h2", "", `CAN signals — ${signals.length}${sigName}`));
      body.appendChild(RW.canSignalsTable(signals));
    }
    const can = canOf(rec);
    if (can.length) {
      body.appendChild(el("h2", "", `CAN frames — ${can.length}`));
      body.appendChild(RW.canTable(can));
    }
    if (rec.io && Object.keys(rec.io).length) {
      body.appendChild(el("h2", "", "IO elements"));
      body.appendChild(jsonBlock(RW.ioWithoutCan ? RW.ioWithoutCan(rec.io) : rec.io));
    }
    body.appendChild(el("h2", "", "Decoded (full)"));
    body.appendChild(jsonBlock(rec));
    if (rec.raw_hex) {
      body.appendChild(el("h2", "", "Raw bytes"));
      const pre = el("pre", "hex", spacedHex(rec.raw_hex));
      body.appendChild(pre);
    }
  }
  const jsonBlock = (obj) => el("pre", "json", JSON.stringify(obj, null, 2));
  const spacedHex = (h) => (h.match(/.{1,2}/g) || []).join(" ");

  // ---- API + forms -------------------------------------------------------
  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
  }

  // Ingest controls live elsewhere now; the Dashboard is view-only. Data arrives from the
  // Parser (/api/ingest) or live feeds. "Clear" just empties the live view.
  $("clear-btn").addEventListener("click", async () => {
    const ok = await window.RunwayUI.confirm({
      title: "Clear the dashboard?",
      text: "Every device and packet shown here is removed. New packets appear as they arrive.",
      action: "Clear", tone: "danger" });
    if (!ok) return;
    await api("/api/data", { method: "DELETE" });
    state.packets = []; state.devices.clear(); state.hidden.clear(); state.seenIo.clear();
    state.selectedSeq = null; clearMap();
    renderDevices(); renderRows(); $("detail-body").innerHTML =
      '<p class="empty">No packet selected</p>';
  });

  $("dev-search").addEventListener("input", renderDevices);
  $("cols-btn").addEventListener("click", toggleColsPicker);

  // ---- WebSocket ---------------------------------------------------------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    sock.onopen = () => setConn(true);
    sock.onclose = () => { setConn(false); setTimeout(connect, 2000); };
    sock.onerror = () => sock.close();
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") {
        (msg.data.devices || []).forEach((d) => state.devices.set(d.device_id, d));
        (msg.data.recent || []).forEach((r) => { state.packets.push(r); noteIo(r); plot(r); });
        renderDevices(); renderRows(); refitMap();
      } else if (msg.type === "record") {
        ingest(msg.data);
      } else if (msg.type === "job") {
        console.log("job", msg.data);
      }
    };
  }
  function setConn(live) {
    const p = $("stat-conn");
    // the live feed's link: said only when it is down — the mode chip beside it already says Live
    p.textContent = live ? "" : "Offline";
    p.className = "pill dead";
    p.hidden = live;
  }

  // ---- boot --------------------------------------------------------------
  renderRows();                              // draw the header immediately
  loadParams().then(renderRows);             // add AVL params to the picker, then redraw
  connect();
  setInterval(renderDevices, 5000);          // refresh live/idle status + last-seen
})();
