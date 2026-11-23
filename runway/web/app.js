/* Runway dashboard - live map, device list and packet inspector. */
(() => {
  "use strict";

  const MAX_ROWS = 500;
  const COLORS = ["#3fb6ff", "#00d18f", "#ffb454", "#ff6b6b", "#b78bff",
                  "#5ad1c8", "#f78fb3", "#9ccc65"];

  const state = {
    packets: [],            // newest-last
    devices: new Map(),     // id -> summary
    hidden: new Set(),      // device ids hidden from map+table (default: all shown)
    seenIo: new Set(),      // io_<id> keys actually received (drives the column picker)
    seenFields: new Set(),  // the other fields packets carried (AIS-140: packet status, frame number, …)
    selectedSeq: null,
    colorIdx: 0,
    colorOf: new Map(),
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
  // times are local and 24-hour: HH:MM:SS, and with the date DD-MM-YYYY HH:MM:SS
  const p2 = (n) => String(n).padStart(2, "0");
  const fmtTime = (iso) => {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d)) return "—";
    return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
  };
  const fmtDateTime = (iso) => {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d)) return "—";
    return `${p2(d.getDate())}-${p2(d.getMonth() + 1)}-${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
  };
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
  const onOff = (v) => (v == null ? "—" : v ? "On" : "Off");
  const posStr = (r) => (r.position && r.position.latitude != null
    ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "—");

  const STD_COLS = [
    { key: "seq",         label: "Sr No",             get: (r) => r.seq, cls: "muted" },
    { key: "time",        label: "Time",              get: (r) => fmtDateTime(r.event_time || r.received_at) },
    // as the packet says it: its type and alert ID ("NR,1", "MoveS,21") — else what the protocol calls it
    { key: "msgtype",     label: "Message type",      get: (r) => {
      const io = r.io || {};
      return io.packet_type ? io.packet_type + (io.alert_id != null && io.alert_id !== "" ? "," + io.alert_id : "") : (r.message_type || "—");
    } },
    { key: "device",      label: "Device",            get: (r) => r.device_id },
    { key: "proto",       label: "Proto",             get: (r) => r.protocol },
    { key: "type",        label: "Type",              get: (r) => r.message_type || "—" },
    { key: "latlon",      label: "Lat, Lon",          get: posStr },
    { key: "altitude",    label: "Alt (m)",           get: (r) => num(r.altitude_m) },
    { key: "heading",     label: "Heading",           get: (r) => num(r.heading_deg) },
    { key: "satellites",  label: "Satellites",        get: (r) => def(r.satellites) },
    { key: "speed",       label: "Speed (km/h)",      get: (r) => num(r.speed_kph) },
    { key: "external_v",  label: "Input voltage (V)", get: (r) => num(r.external_v) },
    // the device's own battery: its level comes in health packets, its voltage in every location packet
    { key: "battery_pct", label: "Internal battery level (%)", get: (r) => num(r.battery_pct) },
    { key: "battery_v",   label: "Internal battery (V)",       get: (r) => num(r.battery_v) },
    { key: "ignition",    label: "Ignition",          get: (r) => onOff(r.ignition) },
    { key: "gsm",         label: "GSM",               get: (r) => def(r.gsm_signal) },
    { key: "odometer",    label: "Odometer (m)",      get: (r) => num(r.odometer_m) },
    // Protocols that carry the vehicle bus inline (Zenithra Ax1 sCAN): how many CAN frames
    // this packet brought. Opt-in via the Columns picker — 0 is a normal packet, not an error.
    { key: "can",         label: "CAN frames",        get: (r) => def(r.io && r.io.can_frame_count) },
    { key: "can_signals", label: "CAN signals",       get: (r) => def(r.io && r.io.can_signal_count) },
  ];
  let paramMap = {};         // AVL id -> {name, unit} (just for labelling; from /api/params)
  const DEFAULT_COLS = ["seq", "time", "msgtype", "latlon", "satellites", "speed", "external_v", "battery_pct", "battery_v"];
  // columns added later, each put once into a list the person chose before (then the choice is theirs)
  const ADDED_COLS = [["battery_v", "battery_pct"], ["msgtype", "time"]];
  let visibleCols = loadCols();

  function loadCols() {
    try {
      const v = JSON.parse(localStorage.getItem(COLS_KEY) || "null");
      if (Array.isArray(v) && v.length) {
        for (const [col, after] of ADDED_COLS) {
          if (localStorage.getItem(COLS_KEY + "." + col)) continue;
          if (!v.includes(col)) {
            const at = v.indexOf(after);
            v.splice(at < 0 ? v.length : at + 1, 0, col);
          }
          localStorage.setItem(COLS_KEY + "." + col, "1");
        }
        localStorage.setItem(COLS_KEY, JSON.stringify(v));
        return v;
      }
    } catch (e) { /* ignore */ }
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
  // Every other field the packets carried becomes a column to choose — named from its key, in the order
  // first seen. Lists (an AIS-140 packet's neighbour cells) stay in the detail pane.
  const ACRONYMS = new Set(["mcc", "mnc", "lac", "pdop", "hdop", "ota", "id", "imei", "gsm", "can", "io", "nmr", "gps", "avl", "sos"]);
  const UNITS = { m: "m", pct: "%", s: "s", v: "V", kph: "km/h", min: "min" };   // a key's last word as its unit
  const fieldLabel = (k) => {
    const words = k.split("_");
    const unit = words.length > 1 && UNITS[words[words.length - 1]];
    if (unit) words.pop();
    const name = words.map((w, i) => (ACRONYMS.has(w) ? w.toUpperCase() : i ? w : w.charAt(0).toUpperCase() + w.slice(1))).join(" ");
    return unit ? `${name} (${unit})` : name;
  };
  const NOT_FIELDS = new Set(["layout"]);           // how Runway read the packet, not something it carried
  function seenFieldCols() {
    return [...state.seenFields].filter((k) => !NOT_FIELDS.has(k))
      .map((k) => ({ key: "f_" + k, label: fieldLabel(k), get: (r) => def(r.io ? r.io[k] : undefined) }));
  }
  const allCols = () => STD_COLS.concat(seenFieldCols(), seenAvlCols());
  const colByKey = (k) => allCols().find((c) => c.key === k);
  const noteIo = (rec) => {
    if (!rec || !rec.io) return;
    for (const k in rec.io) {
      const v = rec.io[k];
      if (/^io_\d+$/.test(k)) state.seenIo.add(k);
      else if (v == null || typeof v !== "object") state.seenFields.add(k);
    }
  };

  async function loadParams() {
    try {
      const data = await api("/api/params");
      (data.avl || []).forEach((p) => { paramMap[p.id] = { name: p.name, unit: p.unit }; });
    } catch (e) { console.error(e); }
  }

  // ---- Map: Log analysis's map on the live packets — one device or many ----------------------------
  // Each shown device's latest packets are analysed on the server as a log is (/api/live/track): the
  // same track, trips, data lost, asleep, stale positions, jumps and driving events. Asked again at most
  // every 2 s while packets come; a new device or a choice in the list zooms to them, a live update
  // keeps the reader's view.
  const tracks = new Map();                 // device → its track (series, driving, seqs: the record behind each row)
  const tmap = window.RunwayMap.track($("map"), {
    drawKey: "runway.dashboard.draw",
    onPoint: (id, k) => openPoint(id, k),
    onGap: (id, g) => showGap(id, g),
    fullRight: () => { const d = $("detail"); return d && d.offsetWidth && !d.closest(".is-folded") ? d.offsetWidth : 0; },
  });
  let trackTimer = null, trackBusy = false, trackAgain = false, trackRefit = false;
  const shownIds = () => [...state.devices.keys()].filter((id) => !state.hidden.has(id));
  function loadTracks(refit) {
    if (refit) trackRefit = true;
    if (trackTimer && !refit) return;            // one ask per 2 s while packets stream in
    clearTimeout(trackTimer);
    trackTimer = setTimeout(() => { trackTimer = null; fetchTracks(); }, refit ? 0 : 2000);
  }
  async function fetchTracks() {
    if (trackBusy) { trackAgain = true; return; }
    trackBusy = true;
    const refit = trackRefit;
    trackRefit = false;
    try {
      const ids = shownIds();
      const d = ids.length ? await api("/api/live/track?" + ids.map((id) => "device=" + encodeURIComponent(id)).join("&"))
                           : { devices: [] };
      tracks.clear();
      d.devices.forEach((t) => tracks.set(t.device, t));
      const drawn = d.devices.filter((t) => t.rows);
      tmap.set(drawn.map((t) => ({
        id: t.device, name: nameOf(t.device), colour: colorFor(t.device), rows: t.rows,
        series: t.series, driving: t.driving, settings: t.settings })), { refit });
      tmap.note(!ids.length ? "" : drawn.some((t) => ((t.series || {}).track || []).some((sg) => sg.length))
        ? "" : "No position from the devices shown yet");
      const sel = state.selectedSeq != null && state.packets.find((r) => r.seq === state.selectedSeq);
      if (sel) markOnMap(sel, false);
    } catch (e) {
      // the page is newer than the Runway serving it (started before the map was): say so, not a blank map
      tmap.note(/^404/.test(e.message) ? "This Runway started before the live map — run main.py again to restart it"
                                       : `The map could not be drawn: ${e.message}`);
    }
    trackBusy = false;
    if (trackAgain) { trackAgain = false; fetchTracks(); }
  }
  // a device's name on the map: its id, or where it came from while it has none yet
  function nameOf(id) {
    const d = state.devices.get(id);
    return /unknown$/i.test(id) && d ? srcOf(d) || id : id;
  }
  // a record's place on its track: its row, found by the record's seq
  function markOnMap(rec, pan) {
    const t = tracks.get(rec.device_id);
    const k = t ? t.seqs.indexOf(rec.seq) : -1;
    if (k >= 0) tmap.mark(rec.device_id, k, pan); else tmap.clearMark();
  }
  // a point picked on the map: its record in the detail pane (asked for when the table has let it go)
  async function openPoint(id, k) {
    const t = tracks.get(id);
    const seq = t ? t.seqs[k] : null;
    if (seq == null) return;
    let rec = state.packets.find((r) => r.seq === seq);
    if (!rec) {
      try { rec = (await api(`/api/devices/${encodeURIComponent(id)}/records?limit=1000`)).find((r) => r.seq === seq); } catch (e) { rec = null; }
    }
    if (rec) showDetail(rec, true);
  }
  // a stretch without data, a stale position or a jump: what it was, in the detail pane
  function showGap(id, g) {
    state.selectedSeq = null;
    renderRows();
    const body = $("detail-body");
    body.innerHTML = "";
    const hop = g._type === "jump" || g._type === "stale";
    body.appendChild(el("h2", "", hop ? (g._type === "stale" ? "Stale position" : "Jump") : "Missing data"));
    const kv = el("div", "kv");
    const add = (k, v) => { if (v == null || v === "") return; kv.append(el("div", "k", k), el("div", "v", String(v))); };
    const p = (q) => (q && q[0] != null ? `${Number(q[0]).toFixed(5)}, ${Number(q[1]).toFixed(5)}` : null);
    add("Device", id);
    add("From", hop ? g.from_time : g.start);
    add("To", hop ? g.time : g.end);
    add("From position", p(g.from));
    add("To position", p(g.to));
    add("Apart", g.metres == null ? null : g.metres >= 1000 ? (g.metres / 1000).toFixed(2) + " km" : g.metres + " m");
    if (hop) add("Would be", g.kmh == null ? null : Math.round(g.kmh) + " km/h");
    else add("Duration", g.duration);
    if (!hop) add("Ignition", g.kind === "loss" ? "On — data lost" : g.kind === "sleep" ? "Off — asleep" : "Not known");
    body.appendChild(kv);
  }

  // ---- Records -----------------------------------------------------------
  function ingest(rec) {
    state.packets.push(rec);
    if (state.packets.length > MAX_ROWS) state.packets.shift();
    noteIo(rec);
    upsertDevice(rec);
    if (!state.hidden.has(rec.device_id) && rec.event_time) loadTracks(!tracks.has(rec.device_id));
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
        loadTracks(true); renderRows(); renderDevices();
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
    // a device time a packet of the same kind already carried (NR live and history are one kind): tagged
    const kindOf = (r) => (r.io && r.io.packet_type) || r.message_type || "";
    const firstAt = new Map();
    for (const r of state.packets) {
      if (!r.event_time) continue;
      const key = `${r.device_id}|${r.event_time}|${kindOf(r)}`;
      if (!firstAt.has(key)) firstAt.set(key, r.seq);
    }
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
        cols.forEach((c) => {
          const td = el("td", c.cls || "", String(c.get(r)));
          const first = c.key === "time" && r.event_time ? firstAt.get(`${r.device_id}|${r.event_time}|${kindOf(r)}`) : null;
          if (first != null && first !== r.seq) {
            const tag = el("span", "ui-pill sm warn", "Same time");
            tag.dataset.tip = `The device time of #${first} again`;
            td.append(" ", tag);
          }
          tr.appendChild(td);
        });
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
      const fields = seenFieldCols();
      if (fields.length) {
        list.appendChild(el("div", "cols-group", "Packet fields (received)"));
        fields.forEach(add);
      }
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
  // fromMap: picked on the map — ringed there already, the view stays
  function showDetail(rec, fromMap) {
    state.selectedSeq = rec.seq;
    renderRows();
    if (!fromMap) markOnMap(rec, true);
    const body = $("detail-body");
    body.innerHTML = "";
    const kv = el("div", "kv");
    const add = (k, v) => { if (v == null || v === "") return;
      kv.append(el("div", "k", k), el("div", "v", String(v))); };
    add("Device", rec.device_id);
    add("Protocol", rec.protocol);
    add("Message", rec.message_type);
    add("Event time", rec.event_time ? fmtDateTime(rec.event_time) : null);
    add("Received", rec.received_at ? fmtDateTime(rec.received_at) : null);
    if (rec.position) add("Position", `${rec.position.latitude}, ${rec.position.longitude}`);
    add("Speed", rec.speed_kph != null ? rec.speed_kph + " km/h" : null);
    add("Heading", rec.heading_deg != null ? rec.heading_deg + "°" : null);
    add("Satellites", rec.satellites);
    add("Ignition", rec.ignition == null ? null : onOff(rec.ignition));
    add("GSM", rec.gsm_signal);
    add("Input voltage", rec.external_v != null ? rec.external_v + " V" : null);
    add("Internal battery", rec.battery_v != null ? rec.battery_v + " V" : null);
    add("Internal battery level", rec.battery_pct != null ? rec.battery_pct + " %" : null);
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
    state.packets = []; state.devices.clear(); state.hidden.clear(); state.seenIo.clear(); state.seenFields.clear();
    state.selectedSeq = null; tracks.clear(); tmap.set([]);
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
        (msg.data.recent || []).forEach((r) => { state.packets.push(r); noteIo(r); });
        renderDevices(); renderRows(); loadTracks(true);
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
