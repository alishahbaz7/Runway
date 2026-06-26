/* ProInfo - Teltonika packet parser: hex in -> field breakdown + records + map. */
(() => {
  "use strict";

  // A sample: IMEI handshake + two Codec-8 AVL packets (from samples/teltonika_demo.hex).
  const SAMPLE = [
    "000f333536333037303432343431303133",
    "000000000000002a0801000001904f5fd940012e052890110e2278005000000a001e000403ef01f0011504014230d400000100002ac1",
    "000000000000002a0801000001904f6013d8012e055770110e41b80050001e0a0025000403ef01f0011504014230d400000100003fd4",
  ].join("\n");

  const state = { records: [], packets: 0, selected: null, markers: [], trail: null };

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  const radio = (name) =>
    (document.querySelector(`input[name=${name}]:checked`) || {}).value;

  // ---- Map ---------------------------------------------------------------
  const map = L.map("parser-map", { zoomControl: true }).setView([20.59, 78.96], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);

  function plotRecords() {
    state.markers.forEach((m) => map.removeLayer(m));
    state.markers = [];
    if (state.trail) { map.removeLayer(state.trail); state.trail = null; }
    const pts = [];
    state.records.forEach((r, i) => {
      if (r.latitude == null || r.longitude == null) return;
      if (r.latitude === 0 && r.longitude === 0) return;
      const ll = [r.latitude, r.longitude];
      pts.push(ll);
      const m = L.circleMarker(ll, { radius: 5, color: "#3fb6ff", fillColor: "#3fb6ff", fillOpacity: 0.9 })
        .addTo(map).bindTooltip(`#${i + 1} · ${r.timestamp}`);
      m.on("click", () => selectRecord(i));
      state.markers.push(m);
    });
    if (pts.length > 1) state.trail = L.polyline(pts, { color: "#00d18f", weight: 2, opacity: 0.6 }).addTo(map);
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.3), { maxZoom: 14 });
  }

  // ---- Field breakdown ---------------------------------------------------
  function renderFields(packets) {
    const tbody = $("field-rows");
    tbody.innerHTML = "";
    const flat = radio("view") === "flat";
    const multi = packets.length > 1;
    let rows = 0;

    packets.forEach((pkt, pi) => {
      const head = `Packet ${pi + 1} · ${pkt.codec_name}` +
        (pkt.crc_ok === false ? " · CRC MISMATCH" : pkt.crc_ok === true ? " · CRC OK" : "");
      addGroupRow(tbody, head, pkt.crc_ok === false);
      let lastGroup = null;
      pkt.fields.forEach((f) => {
        if (!flat && f.group && f.group !== lastGroup) {
          addGroupRow(tbody, f.group, false, true);
          lastGroup = f.group;
        }
        const tr = el("tr");
        tr.append(
          el("td", "", f.name),
          el("td", "num", f.size),
          tdVal(f),
          el("td", "hex", spaced(f.hex)),
          el("td", "bit", f.bit || "—"),
        );
        tbody.appendChild(tr);
        rows++;
      });
    });
    if (!rows) tbody.appendChild(emptyRow("Nothing decoded"));
  }
  function addGroupRow(tbody, text, bad, sub) {
    const tr = el("tr", "group" + (bad ? " crc-bad" : ""));
    const td = el("td", "", (sub ? "▸ " : "") + text);
    td.colSpan = 5; td.style.paddingLeft = sub ? "20px" : "9px";
    tr.appendChild(td); tbody.appendChild(tr);
  }
  function tdVal(f) {
    const td = el("td", "val");
    td.textContent = f.unit ? `${f.value} ${f.unit}` : f.value;
    return td;
  }
  const spaced = (h) => (h && h.match(/.{1,2}/g) || []).join(" ");
  function emptyRow(txt, span = 5) {
    const tr = el("tr"); const td = el("td", "empty", txt); td.colSpan = span;
    tr.appendChild(td); return tr;
  }

  // ---- Records table -----------------------------------------------------
  function renderRecords() {
    const tbody = $("rec-rows");
    tbody.innerHTML = "";
    if (!state.records.length) {
      tbody.appendChild(emptyRow("No AVL records yet", 4));
      return;
    }
    state.records.forEach((r, i) => {
      const tr = el("tr");
      if (i === state.selected) tr.className = "sel";
      tr.append(
        el("td", "", r.timestamp),
        el("td", "", r.longitude),
        el("td", "", r.latitude),
        el("td", "", r.speed_kph),
      );
      tr.onclick = () => selectRecord(i);
      tbody.appendChild(tr);
    });
  }
  function selectRecord(i) {
    state.selected = i;
    renderRecords();
    const r = state.records[i];
    if (r && r.latitude != null) map.panTo([r.latitude, r.longitude]);
  }

  // ---- Decode ------------------------------------------------------------
  async function decode() {
    const hex = $("hexin").value.trim();
    const msg = $("parse-msg");
    if (!hex) { msg.className = "err"; msg.textContent = "paste some hex first"; return; }
    try {
      const res = await fetch("/api/parse", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ hex, transport: radio("transport") }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.status);

      renderFields(data.packets);
      state.packets += data.packets.length;
      // Accumulate records so feeding multiple packets builds the table.
      data.records.forEach((r) => state.records.push(r));
      renderRecords();
      plotRecords();
      $("stat-pkts").textContent = `${state.packets} packets`;
      $("stat-recs").textContent = `${state.records.length} records`;

      if (data.error) { msg.className = "err"; msg.textContent = data.error; }
      else { msg.className = "ok"; msg.textContent =
        `✓ ${data.packets.length} packet(s), ${data.records.length} record(s)`; }
    } catch (e) {
      msg.className = "err"; msg.textContent = "parse failed: " + e.message;
    }
  }

  function clearAll() {
    state.records = []; state.packets = 0; state.selected = null;
    $("field-rows").innerHTML = ""; $("field-rows").appendChild(emptyRow("Paste a packet and hit Decode"));
    renderRecords(); plotRecords();
    $("stat-pkts").textContent = "0 packets"; $("stat-recs").textContent = "0 records";
    $("parse-msg").textContent = "";
  }

  // ---- Wire up -----------------------------------------------------------
  $("decode-btn").addEventListener("click", decode);
  $("clear-btn").addEventListener("click", clearAll);
  $("sample-btn").addEventListener("click", () => { $("hexin").value = SAMPLE; decode(); });
  $("hexin").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") decode();
  });
  document.querySelectorAll("input[name=view]").forEach((r) =>
    r.addEventListener("change", () => { if (state.packets) decode(); }));
})();
