/* Runway - Teltonika packet parser: multiple independent device sessions in tabs.
 *
 * Each tab is an isolated parser instance (own input, grid, records table and map), built
 * by createParser() from the #tab-tpl template. A small tab manager handles add/close/
 * switch. State is volatile (in-memory only) — a reload/restart starts fresh at Device 1.
 * Decoding in a tab also POSTs to /api/ingest so the device shows on the Dashboard.
 */
(() => {
  "use strict";

  const SAMPLE = [
    "000f333536333037303432343431303133",
    "000000000000002a0801000001904f5fd940012e052890110e2278005000000a001e000403ef01f0011504014230d400000100002ac1",
    "000000000000002a0801000001904f6013d8012e055770110e41b80050001e0a0025000403ef01f0011504014230d400000100003fd4",
  ].join("\n");

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
  // Decode one sub-field from a parameter's value bytes per a signature entry.
  // (Big-endian / MSB-first bit numbering for reverse=0; LSB-first for reverse=1.)
  function sigValue(hex, sub) {
    if (!hex) return "—";
    const bytes = (hex.match(/.{1,2}/g) || []).map((b) => parseInt(b, 16));
    const start = sub.startingBit | 0, size = sub.size | 0;
    if (size <= 0 || start + size > bytes.length * 8) return "—";
    const bitAt = (bp) => (bytes[bp >> 3] >> (7 - (bp & 7))) & 1;
    let raw = 0;
    if (!sub.reverse) for (let i = 0; i < size; i++) raw = raw * 2 + bitAt(start + i);
    else for (let i = size - 1; i >= 0; i--) raw = raw * 2 + bitAt(start + i);
    if (sub.signed && raw >= 2 ** (size - 1)) raw -= 2 ** size;
    const v = raw * (sub.multiplier == null ? 1 : sub.multiplier) + (sub.offset || 0);
    return Number.isInteger(v) ? v : Math.round(v * 1000) / 1000;
  }
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
                    lastPackets: [], feeds: [], signature: null, signatureName: null,
                    lastSkipped: 0, hl: null };

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
      if (state.hl) { map.removeLayer(state.hl); state.hl = null; }
      const pts = [];
      const MAX_MARKERS = 2000;                 // cap markers so huge dumps don't freeze the map
      ordered(state.records, (r) => r.timestamp).slice(0, MAX_MARKERS).forEach(({ item: r, idx }, pos) => {
        if (r.latitude == null || r.longitude == null) return;
        if (r.latitude === 0 && r.longitude === 0) return;
        const ll = [r.latitude, r.longitude];
        pts.push(ll);
        const m = L.circleMarker(ll, { radius: 5, color: "#3fb6ff", fillColor: "#3fb6ff", fillOpacity: 0.9 })
          .addTo(map).bindTooltip(`#${pos + 1} · ${fmtTs(r.timestamp)}`);
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

      // Applied signature: AVL id -> [sub-fields]. Matching IO params expand into sub-rows.
      const sig = {};
      if (state.signature) {
        for (const k in state.signature) {
          if (Array.isArray(state.signature[k])) sig[parseInt(k, 10)] = state.signature[k];
        }
      }
      const rows = [];
      const seen = new Set();
      cols.forEach((inst) => inst.byName.forEach((_f, name) => {
        if (!isSignalRow(name) || seen.has(name)) return;
        seen.add(name);
        rows.push({ kind: "field", name, label: name });
        const m = name.match(/\(AVL (\d+)\)/);
        if (m && sig[+m[1]]) {
          sig[+m[1]].forEach((sub) =>
            rows.push({ kind: "sig", parent: name, sub, label: "↳ " + (sub.fieldName || "field") }));
        }
      }));
      return { cols, rows };
    }
    // Transposed: one row per time instance (record), one column per signal (AVL params).
    function renderGrid(packets) {
      const { cols, rows } = buildGrid(packets);   // cols = records/time, rows = signal defs
      if (!cols.length) { fieldsMessage("Grid view shows AVL records — this input has none. Switch to “By record”."); return; }
      clearFields();
      const table = el("table", "grid");

      // header: "Time" corner + one column per signal
      const htr = el("tr");
      htr.appendChild(el("th", "corner", "Time"));
      rows.forEach((r) => {
        const th = el("th");
        th.appendChild(el("div", "colhead" + (r.kind === "sig" ? " sig" : ""), r.label));
        htr.appendChild(th);
      });
      const thead = el("thead"); thead.appendChild(htr); table.appendChild(thead);

      const tbody = el("tbody");
      const prev = new Array(rows.length).fill(undefined);   // track change per signal column
      const MAX_ROWS = 1000;                                  // cap DOM rows so huge dumps don't freeze
      const shown = cols.slice(0, MAX_ROWS);
      shown.forEach((inst, ci) => {
        const tr = el("tr");
        const tcell = el("td", "rowname time");
        tcell.append(el("div", "", fmtTs(inst.timestamp) || `packet ${inst.packet}`),
                     el("small", "muted", `#${ci + 1}`));
        tcell.title = "show this packet on the map";
        tcell.onclick = () => {
          tbody.querySelectorAll("tr.sel").forEach((x) => x.classList.remove("sel"));
          tr.classList.add("sel");
          const la = inst.byName.get("Latitude"), lo = inst.byName.get("Longitude");
          if (la && lo) {
            const lat = parseFloat(la.value), lon = parseFloat(lo.value);
            if (!isNaN(lat) && !isNaN(lon) && !(lat === 0 && lon === 0)) focusLatLon([lat, lon]);
          }
        };
        tr.appendChild(tcell);

        rows.forEach((r, ri) => {
          const td = el("td", "val");
          let v;
          if (r.kind === "sig") {
            const pf = inst.byName.get(r.parent);
            v = pf ? sigValue(pf.hex, r.sub) : "—";
            td.textContent = v;
          } else {
            const f = inst.byName.get(r.name);
            if (f) {
              td.textContent = valText(f);
              td.title = `size ${f.size} · hex ${spaced(f.hex)}` + (f.bit ? ` · bit ${f.bit}` : "");
              td.onclick = () => highlightField(inst.byteOffset, f);
              v = f.value;
            } else { td.textContent = "—"; td.classList.add("muted"); v = undefined; }
          }
          if (prev[ri] !== undefined && String(prev[ri]) !== String(v)) td.classList.add("chg");
          prev[ri] = v;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      if (cols.length > MAX_ROWS) {
        const tr = el("tr");
        const td = el("td", "empty", `… showing ${MAX_ROWS} of ${cols.length} records — use ⤓ CSV to export all`);
        td.colSpan = rows.length + 1;
        tr.appendChild(td); tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      q(".fields").appendChild(table);
    }

    // ---- map focus (pan + highlight a point; unfold the map if it's hidden) ----
    function highlightAt(ll) {
      if (state.hl) map.removeLayer(state.hl);
      state.hl = L.circleMarker(ll, { radius: 9, color: "#ffb454", weight: 3, fill: false }).addTo(map);
    }
    function focusLatLon(ll) {
      if (!ll) return;
      const w = q(".map-wrap");
      if (w.classList.contains("folded")) {
        w.classList.remove("folded");
        q(".map-fold").textContent = "Hide map ▾";
        setTimeout(() => { map.invalidateSize(); map.panTo(ll); highlightAt(ll); }, 0);
      } else { map.panTo(ll); highlightAt(ll); }
    }
    function selectRecord(i) {                 // map marker click
      const r = state.records[i];
      if (r && r.latitude != null) focusLatLon([r.latitude, r.longitude]);
    }

    // ---- summary / insights (replaces the redundant records table) ----
    function bars(rowsArr) {
      const max = Math.max(1, ...rowsArr.map((r) => r[1]));
      const box = el("div", "sum-bars");
      rowsArr.forEach(([label, count, color]) => {
        const row = el("div", "sum-bar");
        row.append(el("span", "sum-bar-l", label), el("span", "sum-bar-c", String(count)));
        const track = el("div", "sum-bar-t");
        const fill = el("div", "sum-bar-f");
        fill.style.width = (count / max * 100) + "%"; fill.style.background = color;
        track.appendChild(fill); row.appendChild(track);
        box.appendChild(row);
      });
      return box;
    }
    function renderSummary() {
      const wrap = q(".summary");
      wrap.innerHTML = "";
      const pkts = state.lastPackets || [];
      if (!pkts.length) { wrap.appendChild(el("p", "empty", "Decode to see a summary")); return; }

      let handshakes = 0, crcBad = 0, records = 0, gprs = 0, gpsValid = 0;
      const byCodec = {}, prio = { Low: 0, High: 0, Panic: 0 };
      let firstTs = null, lastTs = null, sMin = null, sMax = null, sSum = 0, sN = 0;
      pkts.forEach((p) => {
        byCodec[p.codec_name] = (byCodec[p.codec_name] || 0) + 1;
        if (p.kind === "imei") handshakes++;
        if (p.crc_ok === false) crcBad++;
        if ([12, 13, 14].includes(p.codec_id)) gprs++;
        (p.fields || []).forEach((f) => {
          if (f.name === "Priority") { const m = /\((Low|High|Panic)\)/.exec(f.value); if (m) prio[m[1]]++; }
        });
        (p.records || []).forEach((r) => {
          records++;
          if (r.latitude != null && !(r.latitude === 0 && r.longitude === 0)) gpsValid++;
          if (typeof r.speed_kph === "number") {
            sSum += r.speed_kph; sN++;
            sMin = sMin == null ? r.speed_kph : Math.min(sMin, r.speed_kph);
            sMax = sMax == null ? r.speed_kph : Math.max(sMax, r.speed_kph);
          }
          const t = Date.parse(r.timestamp);
          if (!isNaN(t)) { if (firstTs == null || t < firstTs) firstTs = t; if (lastTs == null || t > lastTs) lastTs = t; }
        });
      });

      const skipped = state.lastSkipped || 0;
      const stats = el("div", "sum-stats");
      const tile = (label, value, cls) => {
        const t = el("div", "stat" + (cls ? " " + cls : ""));
        t.append(el("div", "stat-v", String(value)), el("div", "stat-k", label));
        stats.appendChild(t);
      };
      tile("Packets", pkts.length);
      tile("AVL records", records);
      tile("Handshakes", handshakes);
      tile("GPRS msgs", gprs);
      tile("CRC bad", crcBad, crcBad ? "bad" : "");
      tile("Corrupt skipped", skipped, skipped ? "warn" : "");
      wrap.append(el("h3", "sum-h", "Overview"), stats);

      const kv = el("div", "sum-kv");
      const add = (k, v) => { kv.append(el("div", "k", k), el("div", "v", String(v))); };
      add("GPS-valid", `${gpsValid} / ${records}`);
      if (sN) add("Speed", `${Math.round(sMin)}–${Math.round(sMax)} km/h · avg ${Math.round(sSum / sN)}`);
      if (firstTs != null) add("Time span",
        `${fmtTs(new Date(firstTs).toISOString())} → ${fmtTs(new Date(lastTs).toISOString())}`);
      wrap.append(el("h3", "sum-h", "Details"), kv);

      wrap.append(el("h3", "sum-h", "Priority"));
      wrap.appendChild(bars([["Low", prio.Low, "#3fb6ff"], ["High", prio.High, "#ffb454"], ["Panic", prio.Panic, "#ff6b6b"]]));
      wrap.append(el("h3", "sum-h", "By codec"));
      wrap.appendChild(bars(Object.entries(byCodec).map(([n, c]) => [n, c, "#00d18f"])));
    }

    // ---- decode ----
    async function decodeHex(hex) {
      const msg = q(".parse-msg");
      msg.className = "parse-msg"; msg.textContent = "Decoding…";
      try {
        const res = await fetch("/api/parse-stream", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ hex, transport: transport() }),
        });
        if (!res.ok) {
          let d; try { d = await res.json(); } catch (e) { /* ignore */ }
          throw new Error((d && d.detail) || res.status);
        }
        // read NDJSON: progress lines update the status; the final line is the result
        const reader = res.body.getReader();
        const td = new TextDecoder();
        let sbuf = "", data = null;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          sbuf += td.decode(value, { stream: true });
          let nl;
          while ((nl = sbuf.indexOf("\n")) >= 0) {
            const line = sbuf.slice(0, nl).trim(); sbuf = sbuf.slice(nl + 1);
            if (!line) continue;
            const ev = JSON.parse(line);
            if (ev.type === "progress") {
              const pct = ev.total_bytes ? Math.round((ev.bytes / ev.total_bytes) * 100) : 0;
              msg.className = "parse-msg";
              msg.textContent = `Decoding… ${pct}% · ${ev.packets} packets, ${ev.records} records`
                + (ev.skipped ? ` · ${ev.skipped} skipped` : "");
            } else if (ev.type === "result") { data = ev; }
          }
        }
        if (!data) throw new Error("no result from server");
        state.lastPackets = data.packets;
        state.lastSkipped = data.skipped || 0;
        renderFields(data.packets);
        state.packets += data.packets.length;
        data.records.forEach((r) => state.records.push(r));
        renderSummary();
        plotRecords();
        const k = data.skipped || 0;
        // "N of total" parsed, plus how many corrupt frames were skipped
        const note = `${data.packets.length}/${data.total || data.packets.length} packet(s)`
          + `, ${data.records.length} record(s)` + (k ? ` · ${k} skipped (corrupt)` : "");
        if (data.error) { msg.className = "parse-msg err"; msg.textContent = `${note} · ${data.error}`; }
        else { msg.className = "parse-msg ok"; msg.textContent = "✓ " + note; }
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

    // Build the CSV from a chosen subset of signal rows (Time is always the first column).
    function buildCsv(rowsSel, cols) {
      const esc = (s) => `"${String(s).replace(/"/g, '""')}"`;
      const cell = (r, inst) => {
        if (r.kind === "sig") { const pf = inst.byName.get(r.parent); return pf ? sigValue(pf.hex, r.sub) : ""; }
        const f = inst.byName.get(r.name); return f ? valText(f) : "";
      };
      const lines = [["Time", ...rowsSel.map((r) => r.label)].map(esc).join(",")];
      cols.forEach((inst) => {
        lines.push([fmtTs(inst.timestamp), ...rowsSel.map((r) => cell(r, inst))].map(esc).join(","));
      });
      return lines.join("\r\n");
    }
    function downloadCsvFile(text) {
      const blob = new Blob([text], { type: "text/csv" });
      const a = el("a");
      a.href = URL.createObjectURL(blob); a.download = "runway_parsed.csv";
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
    }

    // CSV button → pick which signals to include, then download.
    function exportCsv() {
      const msg = q(".parse-msg");
      if (!state.lastPackets.length) { msg.className = "parse-msg err"; msg.textContent = "nothing to export"; return; }
      const { cols, rows } = buildGrid(state.lastPackets);
      if (!cols.length) { msg.className = "parse-msg err"; msg.textContent = "no AVL records to export"; return; }
      q(".more-panel").classList.remove("open");

      const overlay = el("div", "modal-overlay");
      const modal = el("div", "modal");
      const head = el("div", "modal-head");
      head.append(el("span", "", "Export columns"),
        Object.assign(el("button", "modal-x", "✕"), { onclick: () => overlay.remove() }));
      const tools = el("div", "export-tools");
      const allBtn = el("button", "ghost mini", "All");
      const noneBtn = el("button", "ghost mini", "None");
      const search = el("input", "export-search"); search.placeholder = "filter…";
      tools.append(allBtn, noneBtn, search);
      const list = el("div", "export-list");
      const tRow = el("label", "export-row");
      const tcb = el("input"); tcb.type = "checkbox"; tcb.checked = true; tcb.disabled = true;
      tRow.append(tcb, document.createTextNode(" Time (always included)"));
      list.appendChild(tRow);
      const prevSel = state.exportSel instanceof Set ? state.exportSel : null;
      const cbs = rows.map((r) => {
        const row = el("label", "export-row");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = prevSel ? prevSel.has(r.label) : true;
        row.append(cb, document.createTextNode(" " + r.label));
        list.appendChild(row);
        return { cb, r, label: r.label, row };
      });
      const foot = el("div", "modal-foot");
      const cancel = el("button", "ghost", "Cancel"); cancel.onclick = () => overlay.remove();
      const dl = el("button", "", "⤓ Download CSV");
      foot.append(cancel, dl);
      modal.append(head, tools, list, foot);

      allBtn.onclick = () => cbs.forEach((x) => { x.cb.checked = true; });
      noneBtn.onclick = () => cbs.forEach((x) => { x.cb.checked = false; });
      search.addEventListener("input", () => {
        const qv = search.value.toLowerCase();
        cbs.forEach((x) => { x.row.style.display = (!qv || x.label.toLowerCase().includes(qv)) ? "" : "none"; });
      });
      dl.onclick = () => {
        const chosen = cbs.filter((x) => x.cb.checked).map((x) => x.r);
        state.exportSel = new Set(chosen.map((r) => r.label));
        downloadCsvFile(buildCsv(chosen, cols));
        overlay.remove();
      };

      overlay.appendChild(modal);
      overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
      document.body.appendChild(overlay);
    }

    function rerender() {
      if (state.lastPackets.length) renderFields(state.lastPackets);
      renderSummary(); plotRecords();
    }
    function toggleTzCustom() {
      q(".tz-custom").style.display = q(".tz-sel").value === "custom" ? "inline-flex" : "none";
    }
    function clearAll() {
      state.records = []; state.packets = 0; state.selected = null;
      state.lastPackets = []; state.feeds = [];
      q(".hexin").value = "";
      fieldsMessage("Paste a packet and hit Decode");
      renderSummary(); plotRecords();
      q(".parse-msg").textContent = "";
      onChange();
    }

    // ---- wire the panel's controls ----
    const closeMore = () => q(".more-panel").classList.remove("open");
    q(".decode-btn").addEventListener("click", decode);
    q(".clear-btn").addEventListener("click", () => { clearAll(); closeMore(); });
    q(".download-btn").addEventListener("click", exportCsv);   // exportCsv already closes More
    q(".sample-btn").addEventListener("click", () => { q(".hexin").value = SAMPLE; closeMore(); decode(); });
    q(".upload-btn").addEventListener("click", () => q(".file-input").click());
    q(".file-input").addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => { q(".hexin").value = String(reader.result || ""); closeMore(); decode(); };
      reader.onerror = () => { const m = q(".parse-msg"); m.className = "parse-msg err"; m.textContent = "could not read file"; };
      reader.readAsText(f);
      e.target.value = "";
    });
    q(".hexin").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") decode(); });
    q(".hexin").addEventListener("input", onChange);
    q(".view-sel").addEventListener("change", () => { rerender(); onChange(); closeMore(); });
    q(".transport-sel").addEventListener("change", () => { onChange(); closeMore(); });
    q(".sort-sel").addEventListener("change", () => { rerender(); onChange(); });   // Order lives on the bar
    q(".tz-sel").addEventListener("change", () => {
      toggleTzCustom(); rerender(); onChange();
      if (q(".tz-sel").value !== "custom") closeMore();   // keep open to enter a custom HH:MM
    });
    [".tz-sign", ".tz-h", ".tz-m"].forEach((s) => q(s).addEventListener("change", () => { rerender(); onChange(); }));

    // ---- foldable map ----
    q(".map-fold").addEventListener("click", () => {
      const w = q(".map-wrap");
      const folded = w.classList.toggle("folded");
      q(".map-fold").textContent = folded ? "Show map ▸" : "Hide map ▾";
      if (!folded) setTimeout(() => map.invalidateSize(), 0);
    });

    // ---- "More" overflow dropdown ----
    q(".more-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      q(".more-panel").classList.toggle("open");
    });
    document.addEventListener("click", (e) => {
      const p = q(".more-panel");
      if (p.classList.contains("open") && !p.contains(e.target) && !q(".more-btn").contains(e.target)) {
        p.classList.remove("open");
      }
    });

    // ---- signature: pick from the repository, apply / remove ----
    (async function loadSigOptions() {
      try {
        const list = await fetch("/api/signatures").then((r) => r.json());
        const sel = q(".sig-sel");
        list.forEach((s) => sel.appendChild(Object.assign(document.createElement("option"),
          { value: s.name, textContent: s.name })));
      } catch (e) { /* repo unavailable */ }
    })();
    q(".sig-apply").addEventListener("click", async () => {
      const btn = q(".sig-apply"); const sel = q(".sig-sel"); const m = q(".parse-msg");
      if (state.signature) {                          // toggle off
        state.signature = null; state.signatureName = null;
        btn.textContent = "Apply"; btn.classList.remove("on"); sel.disabled = false;
        rerender(); return;
      }
      const name = sel.value;
      if (!name) { m.className = "parse-msg err"; m.textContent = "pick a signature first"; return; }
      try {
        const d = await fetch("/api/signatures/" + encodeURIComponent(name)).then((r) => r.json());
        state.signature = d.content; state.signatureName = name;
        btn.textContent = "Remove"; btn.classList.add("on"); sel.disabled = true;
        m.className = "parse-msg ok"; m.textContent = "signature applied: " + name;
        rerender();
      } catch (e) { m.className = "parse-msg err"; m.textContent = "apply failed: " + e.message; }
    });

    // ---- instance API (for session persistence across page navigation) ----
    function getState() {
      return {
        settings: {
          inputText: q(".hexin").value, view: viewMode(), transport: transport(),
          sort: sortMode(), tz: q(".tz-sel").value, tzSign: q(".tz-sign").value,
          tzH: q(".tz-h").value, tzM: q(".tz-m").value, signatureName: state.signatureName,
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
      if (s.signatureName) {
        try {
          const d = await fetch("/api/signatures/" + encodeURIComponent(s.signatureName)).then((r) => r.json());
          state.signature = d.content; state.signatureName = s.signatureName;
          q(".sig-sel").value = s.signatureName; q(".sig-sel").disabled = true;
          const b = q(".sig-apply"); b.textContent = "Remove"; b.classList.add("on");
        } catch (e) { /* signature gone from repo */ }
      }
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

  // Parser state lives in sessionStorage: it survives navigating between pages and a page
  // refresh, but a new browser session (or closing the tab) starts fresh at "Device 1".
  const SS_KEY = "runway.parser.session";

  function persist() {
    try {
      sessionStorage.setItem(SS_KEY, JSON.stringify({
        activeId,
        tabs: tabs.map((t) => ({ id: t.id, name: t.name, ...t.parser.getState() })),
      }));
    } catch (e) { /* quota / disabled — non-fatal */ }
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
    if (saved) tab.parser.loadState(saved);   // re-decode the retained data (auto, with progress)
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

  // ---- boot: restore the session's tabs, else open a fresh Device 1 ----
  (function restore() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(SS_KEY) || "null"); } catch (e) { saved = null; }
    if (saved && Array.isArray(saved.tabs) && saved.tabs.length) {
      saved.tabs.forEach((t) => addTab(t));
      const idx = saved.tabs.findIndex((t) => t.id === saved.activeId);
      if (idx >= 0 && tabs[idx]) activate(tabs[idx].id);
    } else {
      addTab();
    }
  })();
})();
