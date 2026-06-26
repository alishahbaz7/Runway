/* Runway - Teltonika packet parser: multiple independent device sessions in tabs.
 *
 * Each tab is an isolated parser instance (own input, grid, records table and map), built
 * by createParser() from the #tab-tpl template. A small tab manager handles add/close/
 * switch and persistence (open tabs + their data are restored on reload by re-decoding).
 * Decoding in a tab also POSTs to /api/ingest so the device shows on the Dashboard.
 */
(() => {
  "use strict";

  const SAMPLE = [
    "000f333536333037303432343431303133",
    "000000000000002a0801000001904f5fd940012e052890110e2278005000000a001e000403ef01f0011504014230d400000100002ac1",
    "000000000000002a0801000001904f6013d8012e055770110e41b80050001e0a0025000403ef01f0011504014230d400000100003fd4",
  ].join("\n");

  const LS_KEY = "runway.parser.v1";
  const TZONES = {
    ist: "Asia/Kolkata", utc: "UTC", dubai: "Asia/Dubai", singapore: "Asia/Singapore",
    london: "Europe/London", ny: "America/New_York", local: undefined,
  };

  // ---- shared pure helpers ----------------------------------------------
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  const spaced = (h) => (h && h.match(/.{1,2}/g) || []).join(" ");
  const valText = (f) => (f.unit ? `${f.value} ${f.unit}` : f.value);
  const cleanHex = (s) => {
    let h = (s || "").replace(/[^0-9a-fA-F]/g, "").toLowerCase();
    if (h.length % 2) h = h.slice(0, -1);
    return h;
  };
  const colLabel = (i) => {                       // 0->A, 1->B, … 26->AA
    let s = ""; i += 1;
    while (i > 0) { i -= 1; s = String.fromCharCode(65 + (i % 26)) + s; i = Math.floor(i / 26); }
    return s;
  };
  function isSignalRow(name) {
    if (name === "Timestamp") return false;
    if (name.startsWith("N of Total IO")) return false;
    if (/^N[1248] \(/.test(name)) return false;
    if (name.startsWith("NX (")) return false;
    return true;
  }

  // =======================================================================
  // One independent parser bound to a panel element.
  // =======================================================================
  function createParser(panel, id, onChange, peerName) {
    const q = (sel) => panel.querySelector(sel);
    const state = { records: [], packets: 0, selected: null, markers: [], trail: null,
                    lastPackets: [], feeds: [] };

    const map = L.map(q(".parser-map"), { zoomControl: true }).setView([20.59, 78.96], 4);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap",
    }).addTo(map);

    const viewMode = () => q(".view-sel").value || "grid";
    const transport = () => q(".transport-sel").value || "tcp";
    const sortMode = () => q(".sort-sel").value || "feed";

    function customOffsetMin() {
      const sign = q(".tz-sign").value === "-" ? -1 : 1;
      const h = parseInt(q(".tz-h").value, 10) || 0;
      const m = parseInt(q(".tz-m").value, 10) || 0;
      return sign * (h * 60 + m);
    }
    function fmtTs(iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const sel = q(".tz-sel").value;
      if (sel === "custom") {
        const s = new Date(d.getTime() + customOffsetMin() * 60000);
        const p = (n) => String(n).padStart(2, "0");
        return `${s.getUTCFullYear()}-${p(s.getUTCMonth() + 1)}-${p(s.getUTCDate())} `
             + `${p(s.getUTCHours())}:${p(s.getUTCMinutes())}:${p(s.getUTCSeconds())}`;
      }
      try {
        return new Intl.DateTimeFormat("en-GB", {
          timeZone: TZONES[sel] ?? undefined, year: "numeric", month: "2-digit", day: "2-digit",
          hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
        }).format(d).replace(",", "");
      } catch (e) { return iso; }
    }
    function ordered(items, getTs) {
      const arr = items.map((item, idx) => ({ item, idx }));
      const mode = sortMode();
      if (mode === "feed") return arr;
      arr.sort((a, b) => new Date(getTs(a.item)) - new Date(getTs(b.item)));
      if (mode === "desc") arr.reverse();
      return arr;
    }

    // ---- map ----
    function plotRecords() {
      state.markers.forEach((m) => map.removeLayer(m));
      state.markers = [];
      if (state.trail) { map.removeLayer(state.trail); state.trail = null; }
      const pts = [];
      ordered(state.records, (r) => r.timestamp).forEach(({ item: r, idx }, pos) => {
        if (r.latitude == null || r.longitude == null) return;
        if (r.latitude === 0 && r.longitude === 0) return;
        const ll = [r.latitude, r.longitude];
        pts.push(ll);
        const m = L.circleMarker(ll, { radius: 5, color: "#3fb6ff", fillColor: "#3fb6ff", fillOpacity: 0.9 })
          .addTo(map).bindTooltip(`${colLabel(pos)} · ${fmtTs(r.timestamp)}`);
        m.on("click", () => selectRecord(idx));
        state.markers.push(m);
      });
      if (pts.length > 1) state.trail = L.polyline(pts, { color: "#00d18f", weight: 2, opacity: 0.6 }).addTo(map);
      if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.3), { maxZoom: 14 });
    }

    // ---- click-to-highlight: select a field's bytes inside the single input ----
    function highlightField(base, f) {
      if (!f) return;
      const ta = q(".hexin");
      const raw = ta.value;
      const nibAt = [];                              // char index of each hex nibble
      for (let i = 0; i < raw.length; i++) {
        const c = raw.charCodeAt(i);
        if ((c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102)) nibAt.push(i);
      }
      const sN = (base + f.at) * 2, eN = sN + f.size * 2;     // nibble range of this field
      if (sN >= nibAt.length) return;
      const rs = nibAt[sN];
      const re = (eN - 1 < nibAt.length) ? nibAt[eN - 1] + 1 : nibAt[nibAt.length - 1] + 1;
      ta.focus();
      ta.setSelectionRange(rs, re);
    }

    // ---- field rendering ----
    function clearFields() { q(".fields").innerHTML = ""; }
    function fieldsMessage(txt) { clearFields(); q(".fields").appendChild(el("p", "empty", txt)); }
    function emptyRow(txt, span = 5) {
      const tr = el("tr"); const td = el("td", "empty", txt); td.colSpan = span;
      tr.appendChild(td); return tr;
    }
    function renderFields(packets) {
      if (!packets || !packets.length) { fieldsMessage("Nothing decoded"); return; }
      const view = viewMode();
      if (view === "grid") renderGrid(packets);
      else renderBreakdown(packets, view === "flat");
    }
    function renderBreakdown(packets, flat) {
      clearFields();
      const table = el("table");
      const htr = el("tr");
      ["Name", "Size", "Value", "Hex Value", "Bit Value"].forEach((h) => htr.appendChild(el("th", "", h)));
      const thead = el("thead"); thead.appendChild(htr); table.appendChild(thead);
      const tbody = el("tbody");
      packets.forEach((pkt, pi) => {
        const head = `Packet ${pi + 1} · ${pkt.codec_name}` +
          (pkt.crc_ok === false ? " · CRC MISMATCH" : pkt.crc_ok === true ? " · CRC OK" : "");
        addGroupRow(tbody, head, pkt.crc_ok === false, false);
        let lastGroup = null;
        pkt.fields.forEach((f) => {
          if (!flat && f.group && f.group !== lastGroup) { addGroupRow(tbody, f.group, false, true); lastGroup = f.group; }
          const tr = el("tr");
          tr.append(el("td", "", f.name), el("td", "num", f.size),
                    Object.assign(el("td", "val"), { textContent: valText(f) }),
                    el("td", "hex", spaced(f.hex)), el("td", "bit", f.bit || "—"));
          tr.style.cursor = "pointer";
          tr.onclick = () => highlightField(pkt.byte_offset || 0, f);
          tbody.appendChild(tr);
        });
      });
      table.appendChild(tbody);
      q(".fields").appendChild(table);
    }
    function addGroupRow(tbody, text, bad, sub) {
      const tr = el("tr", "group" + (bad ? " crc-bad" : ""));
      const td = el("td", "", (sub ? "▸ " : "") + text);
      td.colSpan = 5; td.style.paddingLeft = sub ? "20px" : "9px";
      tr.appendChild(td); tbody.appendChild(tr);
    }
    function buildGrid(packets) {
      const instances = [];
      packets.forEach((pkt, pi) => {
        const groups = new Map();
        pkt.fields.forEach((f) => {
          if (!f.group || !f.group.startsWith("AVL Record")) return;
          (groups.get(f.group) || groups.set(f.group, []).get(f.group)).push(f);
        });
        groups.forEach((fields) => {
          const byName = new Map();
          let ts = "";
          fields.forEach((f) => { byName.set(f.name, f); if (f.name === "Timestamp") ts = f.value; });
          instances.push({ packet: pi + 1, codec: pkt.codec_name, timestamp: ts, byName,
                           byteOffset: pkt.byte_offset || 0 });
        });
      });
      const cols = ordered(instances, (inst) => inst.timestamp).map((o) => o.item);
      const rowNames = [];
      const seen = new Set();
      cols.forEach((inst) => inst.byName.forEach((_f, name) => {
        if (isSignalRow(name) && !seen.has(name)) { seen.add(name); rowNames.push(name); }
      }));
      return { cols, rowNames };
    }
    function renderGrid(packets) {
      const { cols, rowNames } = buildGrid(packets);
      if (!cols.length) { fieldsMessage("Grid view shows AVL records — this input has none. Switch to “By record”."); return; }
      clearFields();
      const table = el("table", "grid");
      const htr = el("tr");
      htr.appendChild(el("th", "corner", "Signal"));
      cols.forEach((inst, i) => {
        const th = el("th");
        th.appendChild(el("div", "colhead", colLabel(i)));
        th.appendChild(el("small", "", fmtTs(inst.timestamp) || `packet ${inst.packet}`));
        th.title = `Packet ${inst.packet} · ${inst.codec}` + (inst.timestamp ? ` · ${fmtTs(inst.timestamp)}` : "");
        htr.appendChild(th);
      });
      const thead = el("thead"); thead.appendChild(htr); table.appendChild(thead);
      const tbody = el("tbody");
      rowNames.forEach((name) => {
        const tr = el("tr");
        tr.appendChild(el("td", "rowname", name));
        let prev;
        cols.forEach((inst) => {
          const f = inst.byName.get(name);
          const td = el("td", "val");
          if (f) {
            td.textContent = valText(f);
            td.title = `size ${f.size} · hex ${spaced(f.hex)}` + (f.bit ? ` · bit ${f.bit}` : "");
            if (prev !== undefined && prev !== f.value) td.classList.add("chg");
            prev = f.value;
            td.onclick = () => highlightField(inst.byteOffset, f);
          } else { td.textContent = "—"; td.classList.add("muted"); }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      q(".fields").appendChild(table);
    }

    // ---- records table ----
    function renderRecords() {
      const tbody = q(".rec-rows");
      tbody.innerHTML = "";
      if (!state.records.length) { tbody.appendChild(emptyRow("No AVL records yet", 4)); return; }
      ordered(state.records, (r) => r.timestamp).forEach(({ item: r, idx }) => {
        const tr = el("tr");
        if (idx === state.selected) tr.className = "sel";
        tr.append(el("td", "", fmtTs(r.timestamp)), el("td", "", r.longitude),
                  el("td", "", r.latitude), el("td", "", r.speed_kph));
        tr.onclick = () => selectRecord(idx);
        tbody.appendChild(tr);
      });
    }
    function selectRecord(i) {
      state.selected = i;
      renderRecords();
      const r = state.records[i];
      if (r && r.latitude != null) map.panTo([r.latitude, r.longitude]);
    }

    // ---- decode ----
    async function decodeHex(hex) {
      const msg = q(".parse-msg");
      try {
        const res = await fetch("/api/parse", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ hex, transport: transport() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        state.lastPackets = data.packets;
        renderFields(data.packets);
        state.packets += data.packets.length;
        data.records.forEach((r) => state.records.push(r));
        renderRecords();
        plotRecords();
        if (data.error) { msg.className = "parse-msg err"; msg.textContent = data.error; }
        else { msg.className = "parse-msg ok"; msg.textContent =
          `✓ ${data.packets.length} packet(s), ${data.records.length} record(s)`; }
        // Feed the Dashboard's live store; surface a hint if the endpoint is missing
        // (e.g. a server started before /api/ingest existed).
        fetch("/api/ingest", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ hex, codec: "teltonika",
                                 peer: (peerName ? peerName() : "parser-" + id) }),
        }).then((r) => { if (!r.ok) throw new Error(r.status); })
          .catch(() => {
            msg.className = "parse-msg err";
            msg.textContent += " · dashboard sync failed — restart the server";
          });
        return true;
      } catch (e) {
        msg.className = "parse-msg err"; msg.textContent = "parse failed: " + e.message;
        return false;
      }
    }
    async function decode() {
      const hex = q(".hexin").value.trim();
      const msg = q(".parse-msg");
      if (!hex) { msg.className = "parse-msg err"; msg.textContent = "paste some hex first"; return; }
      if (await decodeHex(hex)) { state.feeds.push(hex); onChange(); }
    }

    function downloadCsv() {
      const msg = q(".parse-msg");
      if (!state.lastPackets.length) { msg.className = "parse-msg err"; msg.textContent = "nothing to export"; return; }
      const { cols, rowNames } = buildGrid(state.lastPackets);
      if (!cols.length) { msg.className = "parse-msg err"; msg.textContent = "no AVL records to export"; return; }
      const esc = (s) => `"${String(s).replace(/"/g, '""')}"`;
      const lines = [];
      lines.push(["Signal", ...cols.map((c, i) => `${colLabel(i)} ${fmtTs(c.timestamp)}`)].map(esc).join(","));
      rowNames.forEach((name) => {
        const row = [name, ...cols.map((c) => { const f = c.byName.get(name); return f ? valText(f) : ""; })];
        lines.push(row.map(esc).join(","));
      });
      const blob = new Blob([lines.join("\r\n")], { type: "text/csv" });
      const a = el("a");
      a.href = URL.createObjectURL(blob); a.download = "runway_parsed.csv";
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
    }

    function rerender() {
      if (state.lastPackets.length) renderFields(state.lastPackets);
      renderRecords(); plotRecords();
    }
    function toggleTzCustom() {
      q(".tz-custom").style.display = q(".tz-sel").value === "custom" ? "inline-flex" : "none";
    }
    function clearAll() {
      state.records = []; state.packets = 0; state.selected = null;
      state.lastPackets = []; state.feeds = [];
      q(".hexin").value = "";
      fieldsMessage("Paste a packet and hit Decode");
      renderRecords(); plotRecords();
      q(".parse-msg").textContent = "";
      onChange();
    }

    // ---- wire the panel's controls ----
    q(".decode-btn").addEventListener("click", decode);
    q(".clear-btn").addEventListener("click", clearAll);
    q(".download-btn").addEventListener("click", downloadCsv);
    q(".sample-btn").addEventListener("click", () => { q(".hexin").value = SAMPLE; decode(); });
    q(".upload-btn").addEventListener("click", () => q(".file-input").click());
    q(".file-input").addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => { q(".hexin").value = String(reader.result || ""); decode(); };
      reader.onerror = () => { const m = q(".parse-msg"); m.className = "parse-msg err"; m.textContent = "could not read file"; };
      reader.readAsText(f);
      e.target.value = "";
    });
    q(".hexin").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") decode(); });
    q(".hexin").addEventListener("input", onChange);
    q(".view-sel").addEventListener("change", () => { rerender(); onChange(); });
    q(".transport-sel").addEventListener("change", onChange);
    q(".sort-sel").addEventListener("change", () => { rerender(); onChange(); });
    q(".tz-sel").addEventListener("change", () => { toggleTzCustom(); rerender(); onChange(); });
    [".tz-sign", ".tz-h", ".tz-m"].forEach((s) => q(s).addEventListener("change", () => { rerender(); onChange(); }));

    // ---- instance API ----
    function getState() {
      return {
        settings: {
          inputText: q(".hexin").value, view: viewMode(), transport: transport(),
          sort: sortMode(), tz: q(".tz-sel").value, tzSign: q(".tz-sign").value,
          tzH: q(".tz-h").value, tzM: q(".tz-m").value,
        },
        feeds: state.feeds.slice(),
      };
    }
    async function loadState(saved) {
      const s = saved.settings || {};
      const set = (sel, v) => { if (v != null) q(sel).value = v; };
      set(".hexin", s.inputText); set(".view-sel", s.view); set(".transport-sel", s.transport);
      set(".sort-sel", s.sort); set(".tz-sel", s.tz); set(".tz-sign", s.tzSign);
      set(".tz-h", s.tzH); set(".tz-m", s.tzM);
      toggleTzCustom();
      for (const f of (saved.feeds || [])) { await decodeHex(f); state.feeds.push(f); }
    }

    toggleTzCustom();
    return {
      getState, loadState,
      activate() { setTimeout(() => map.invalidateSize(), 0); },
      destroy() { map.remove(); },
    };
  }

  // =======================================================================
  // Tab manager
  // =======================================================================
  const tabbar = document.getElementById("tabbar");
  const panels = document.getElementById("panels");
  const tpl = document.getElementById("tab-tpl");
  const tabs = [];                 // { id, name, btn, panel, parser }
  let activeId = null;
  let seq = 0;

  function persist() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        activeId,
        tabs: tabs.map((t) => ({ id: t.id, name: t.name, ...t.parser.getState() })),
      }));
    } catch (e) { /* quota / disabled */ }
  }

  function updateStat() {
    const s = document.getElementById("stat-tabs");
    if (s) s.textContent = `${tabs.length} tab${tabs.length === 1 ? "" : "s"}`;
  }

  function activate(id) {
    activeId = id;
    tabs.forEach((t) => {
      const on = t.id === id;
      t.btn.classList.toggle("active", on);
      t.panel.classList.toggle("active", on);
      if (on) t.parser.activate();
    });
    persist();
  }

  function addTab(saved) {
    const id = ++seq;
    const name = (saved && saved.name) || `Device ${id}`;

    const panel = tpl.content.firstElementChild.cloneNode(true);
    panels.appendChild(panel);

    const btn = el("div", "tab");
    const label = el("span", "tab-label", name);
    const close = el("span", "tab-close", "✕");
    btn.append(label, close);
    tabbar.insertBefore(btn, document.getElementById("tab-add"));

    const tab = { id, name, btn, panel, parser: null };
    tab.parser = createParser(panel, id, persist, () => tab.name);
    tabs.push(tab);

    btn.addEventListener("click", (e) => { if (e.target !== close) activate(id); });
    label.addEventListener("dblclick", () => {
      const n = prompt("Tab name", tab.name);
      if (n) { tab.name = n; label.textContent = n; persist(); }
    });
    close.addEventListener("click", () => closeTab(id));

    activate(id);
    updateStat();
    if (saved) tab.parser.loadState(saved);   // async re-decode of remembered feeds
    return tab;
  }

  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    const [t] = tabs.splice(i, 1);
    t.parser.destroy();
    t.btn.remove();
    t.panel.remove();
    if (!tabs.length) { addTab(); return; }   // never leave zero tabs
    if (activeId === id) activate(tabs[Math.max(0, i - 1)].id);
    updateStat();
    persist();
  }

  // "+" button to open a new device tab.
  const addBtn = el("div", "", "+ New device");
  addBtn.id = "tab-add";
  tabbar.appendChild(addBtn);
  addBtn.addEventListener("click", () => addTab());

  // ---- boot: restore tabs, else open one ----
  (function restore() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { saved = null; }
    if (saved && Array.isArray(saved.tabs) && saved.tabs.length) {
      saved.tabs.forEach((t) => addTab(t));
      const want = saved.tabs.find((t) => t.id === saved.activeId);
      if (want) {
        // map saved id -> created tab order (ids are reassigned on load)
        const idx = saved.tabs.indexOf(want);
        if (tabs[idx]) activate(tabs[idx].id);
      }
    } else {
      addTab();
    }
  })();
})();
