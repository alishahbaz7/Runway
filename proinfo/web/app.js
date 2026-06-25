/* ProInfo dashboard - live map, device list and packet inspector. */
(() => {
  "use strict";

  const MAX_ROWS = 500;
  const COLORS = ["#3fb6ff", "#00d18f", "#ffb454", "#ff6b6b", "#b78bff",
                  "#5ad1c8", "#f78fb3", "#9ccc65"];

  const state = {
    packets: [],            // newest-last
    devices: new Map(),     // id -> summary
    filter: null,           // selected device id (null = all)
    selectedSeq: null,
    markers: new Map(),     // id -> L.marker
    trails: new Map(),      // id -> L.polyline
    colorIdx: 0,
    colorOf: new Map(),
  };

  // ---- DOM helpers --------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "—");
  const colorFor = (id) => {
    if (!state.colorOf.has(id)) {
      state.colorOf.set(id, COLORS[state.colorIdx++ % COLORS.length]);
    }
    return state.colorOf.get(id);
  };

  // ---- Map ---------------------------------------------------------------
  const map = L.map("map", { zoomControl: true }).setView([20.59, 78.96], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);

  function plot(rec) {
    const p = rec.position;
    if (!p || p.latitude == null || p.longitude == null) return;
    if (p.latitude === 0 && p.longitude === 0) return;
    if (state.filter && rec.device_id !== state.filter) return;
    const id = rec.device_id;
    const ll = [p.latitude, p.longitude];
    const color = colorFor(id);

    let marker = state.markers.get(id);
    if (!marker) {
      marker = L.circleMarker(ll, { radius: 6, color, fillColor: color, fillOpacity: 0.9 });
      marker.addTo(map).bindTooltip(id, { permanent: false });
      state.markers.set(id, marker);
    } else {
      marker.setLatLng(ll);
    }
    let trail = state.trails.get(id);
    if (!trail) {
      trail = L.polyline([ll], { color, weight: 2, opacity: 0.7 }).addTo(map);
      state.trails.set(id, trail);
    } else {
      trail.addLatLng(ll);
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
  }

  // ---- Records -----------------------------------------------------------
  function ingest(rec) {
    state.packets.push(rec);
    if (state.packets.length > MAX_ROWS) state.packets.shift();
    upsertDevice(rec);
    plot(rec);
    renderRowsThrottled();
  }

  function upsertDevice(rec) {
    const d = state.devices.get(rec.device_id) || {
      device_id: rec.device_id, count: 0, protocol: rec.protocol,
    };
    d.count += 1;
    d.protocol = rec.protocol;
    d.last_seen = rec.received_at;
    d.last_message_type = rec.message_type;
    if (rec.position && rec.position.latitude != null) {
      d.last_position = { lat: rec.position.latitude, lon: rec.position.longitude };
    }
    state.devices.set(rec.device_id, d);
    renderDevices();
  }

  // ---- Rendering: devices -------------------------------------------------
  function renderDevices() {
    const ul = $("devices");
    const q = ($("dev-search").value || "").toLowerCase();
    const list = [...state.devices.values()].filter((d) =>
      d.device_id.toLowerCase().includes(q));
    ul.innerHTML = "";
    if (!list.length) { ul.appendChild(el("li", "empty", "No devices yet")); return; }
    for (const d of list) {
      const li = el("li", state.filter === d.device_id ? "active" : "");
      const id = el("div", "id");
      id.append(Object.assign(el("span", "dot"), { style: `background:${colorFor(d.device_id)}` }),
                document.createTextNode(d.device_id));
      const meta = el("div", "meta");
      meta.append(el("span", "", `${d.protocol} · ${d.count} pkts`),
                  el("span", "", fmtTime(d.last_seen)));
      li.append(id, meta);
      li.onclick = () => {
        state.filter = state.filter === d.device_id ? null : d.device_id;
        clearMap();
        state.packets.forEach(plot);
        refitMap();
        renderDevices();
        renderRows();
      };
      ul.appendChild(li);
    }
    $("stat-devices").textContent = `${state.devices.size} devices`;
  }

  // ---- Rendering: packet table -------------------------------------------
  let rowsPending = false;
  function renderRowsThrottled() {
    if (rowsPending) return;
    rowsPending = true;
    requestAnimationFrame(() => { rowsPending = false; renderRows(); });
  }

  function renderRows() {
    const tbody = $("packet-rows");
    const rows = state.packets
      .filter((r) => !state.filter || r.device_id === state.filter)
      .slice(-MAX_ROWS).reverse();
    tbody.innerHTML = "";
    if (!rows.length) {
      const tr = el("tr"); const td = el("td", "empty", "Waiting for packets…");
      td.colSpan = 7; tr.appendChild(td); tbody.appendChild(tr);
    } else {
      for (const r of rows) {
        const tr = el("tr");
        if (r.seq === state.selectedSeq) tr.className = "sel";
        const pos = r.position && r.position.latitude != null
          ? `${r.position.latitude.toFixed(5)},${r.position.longitude.toFixed(5)}` : "—";
        const typeCls = (r.alarms && r.alarms.length) ? "badge alarm" : "badge";
        const typeTxt = (r.alarms && r.alarms.length) ? r.alarms.join(",") : (r.message_type || "—");
        tr.append(
          el("td", "muted", r.seq),
          el("td", "", fmtTime(r.received_at)),
          el("td", "", r.device_id),
          el("td", "", r.protocol),
          tdBadge(typeTxt, typeCls),
          el("td", "", pos),
          el("td", "", r.speed_kph != null ? Math.round(r.speed_kph) : "—"),
        );
        tr.onclick = () => showDetail(r);
        tbody.appendChild(tr);
      }
    }
    $("stat-packets").textContent = `${state.packets.length} packets`;
  }
  function tdBadge(txt, cls) {
    const td = el("td"); td.appendChild(el("span", cls, txt)); return td;
  }

  // ---- Rendering: detail panel -------------------------------------------
  function showDetail(rec) {
    state.selectedSeq = rec.seq;
    renderRows();
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

    if (rec.io && Object.keys(rec.io).length) {
      body.appendChild(el("h2", "", "IO elements"));
      body.appendChild(jsonBlock(rec.io));
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

  async function loadCodecs() {
    try {
      const codecs = await api("/api/codecs");
      for (const sel of [$("up-codec"), $("ls-codec")]) {
        sel.innerHTML = "";
        sel.appendChild(new Option("auto-detect", "auto"));
        codecs.forEach((c) => sel.appendChild(new Option(c.display_name, c.name)));
      }
    } catch (e) { console.error(e); }
  }

  $("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = $("up-file").files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("codec", $("up-codec").value);
    fd.append("fmt", $("up-fmt").value);
    fd.append("delay", $("up-delay").value || "0");
    try { await api("/api/replay", { method: "POST", body: fd }); }
    catch (err) { alert("Replay failed: " + err.message); }
  });

  $("listen-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const job = await api("/api/listen", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ codec: $("ls-codec").value, port: +$("ls-port").value }),
      });
      alert(`Listener started on port ${$("ls-port").value} (${job.job.id})`);
    } catch (err) { alert("Listen failed: " + err.message); }
  });

  $("clear-btn").addEventListener("click", async () => {
    if (!confirm("Clear all captured data?")) return;
    await api("/api/data", { method: "DELETE" });
    state.packets = []; state.devices.clear(); state.filter = null;
    state.selectedSeq = null; clearMap();
    renderDevices(); renderRows(); $("detail-body").innerHTML =
      '<p class="empty">Select a packet to inspect it</p>';
  });

  $("dev-search").addEventListener("input", renderDevices);

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
        (msg.data.recent || []).forEach((r) => { state.packets.push(r); plot(r); });
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
    p.textContent = live ? "● live" : "offline";
    p.className = "pill " + (live ? "live" : "dead");
  }

  // ---- boot --------------------------------------------------------------
  loadCodecs();
  connect();
})();
