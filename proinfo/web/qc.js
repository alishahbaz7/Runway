/* Runway - Device QC (Target 1: intake + identify + record).
 * Start a QC session on a static port (protocol Auto-detect or manual), watch the
 * device connect + get identified (protocol + device id), see its live packets, and
 * stop to finalize the session (its capture file stays on disk, downloadable).
 * Backend: /api/qc/start, /api/qc/{id}/stop, /api/qc, /api/qc/{id}, /api/qc/{id}/capture. */
(() => {
  "use strict";

  const { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull } = window.Runway;
  const msg = (cls, txt) => { const m = $("qc-msg"); m.className = cls; m.textContent = txt; };

  const RAWFMT_KEY = "runway.qc.rawfmt";    // retained view mode (hard rule: sticks until changed)
  let rawFmt = "hex";
  try { rawFmt = localStorage.getItem(RAWFMT_KEY) || "hex"; } catch (e) { /* ignore */ }

  const rawView = (hex) => (!hex ? "" : rawFmt === "ascii" ? hexToAscii(hex) : hexSpaced(hex));

  const sessions = new Map();       // qc_id -> session object (from /api/qc)
  const feedBySession = new Map();  // qc_id -> array of records (newest first)
  const MAX_FEED = 300;
  let focusId = null;               // qc_id whose feed is shown on the right
  let LOCAL_IP = "";

  // ---- start form ---------------------------------------------------------
  async function loadProtocols() {
    const sel = $("qc-protocol");
    sel.innerHTML = "";
    try {
      (await api("/api/codecs")).forEach((c) => {
        if (c.name === "generic_hex" || c.name === "custom_template") return;
        sel.appendChild(new Option(c.display_name || c.name, c.name));
      });
    } catch (e) { /* ignore */ }
  }
  async function loadDefaults() {
    try {
      const d = await api("/api/listen/defaults");
      LOCAL_IP = d.local_ip || "";
      $("qc-host").value = d.local_ip || d.host || "0.0.0.0";
      $("qc-localip").textContent = LOCAL_IP
        ? `This machine: ${LOCAL_IP}${d.hostname ? " (" + d.hostname + ")" : ""} — devices dial in here`
        : "";
    } catch (e) { /* ignore */ }
  }
  $("qc-mode").addEventListener("change", () => {
    $("qc-protocol-fld").style.display = $("qc-mode").value === "manual" ? "" : "none";
  });

  $("qc-start").addEventListener("click", async () => {
    const port = Number($("qc-port").value);
    if (!port || port < 1 || port > 65535) { msg("err", "enter a valid port"); return; }
    const mode = $("qc-mode").value;
    const body = {
      port, host: $("qc-host").value.trim() || "0.0.0.0",
      protocol_mode: mode, protocol: mode === "manual" ? $("qc-protocol").value : "auto",
      operator: $("qc-operator").value.trim(),
    };
    try {
      const qc = await api("/api/qc/start", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      sessions.set(qc.id, qc);
      focusId = qc.id;
      feedBySession.set(qc.id, []);
      renderTabs(); renderFeed();
      msg("ok", `QC session #${qc.id} listening on ${body.host}:${port}`);
    } catch (e) { msg("err", "start failed: " + e.message); }
  });

  // ---- tabs (top): one per open port/session -------------------------------
  function protoLabel(s) {
    if (s.identified_as) return s.identified_as;
    if (s.protocol_mode === "manual") return s.protocol;
    return s.status === "running" ? "identifying…" : "unidentified";
  }
  function renderTabs() {
    const host = $("qc-tabs");
    host.innerHTML = "";
    const list = [...sessions.values()].sort((a, b) => b.id - a.id);
    if (!list.length) { host.appendChild(el("p", "empty", "No QC sessions yet. Start one above.")); return; }
    list.forEach((s) => {
      const running = s.status === "running";
      const tab = el("div", "qc-tab" + (running ? " on" : " off") + (s.id === focusId ? " active" : ""));
      tab.onclick = () => { focusId = s.id; renderTabs(); renderFeed(); };
      tab.append(el("span", "tcp-dot " + (s.connected ? "live" : "dead")));
      tab.append(el("span", "qc-tab-port", `:${s.port}`));
      tab.append(el("span", "qc-tab-sub muted", s.device_id || protoLabel(s)));
      if (s.verdict) tab.append(el("span", "qc-verdict qc-verdict-" + s.verdict, s.verdict.toUpperCase()));
      host.appendChild(tab);
    });
  }
  async function stopQc(id) {
    try { const s = await api(`/api/qc/${id}/stop`, { method: "POST" }); sessions.set(id, s); renderTabs(); renderFeed(); msg("ok", `session #${id} stopped`); }
    catch (e) { msg("err", "stop failed: " + e.message); }
  }
  async function resumeQc(id) {
    try {
      const s = await api(`/api/qc/${id}/resume`, { method: "POST" });
      sessions.set(id, s); focusId = id; renderTabs(); renderFeed();
      msg("ok", `session #${id} resumed on :${s.port}`);
    } catch (e) { msg("err", "resume failed: " + e.message); }
  }
  async function clearQc(id) {
    const s = sessions.get(id);
    const label = s ? `#${id} (${s.device_id || "port " + s.port})` : `#${id}`;
    if (!window.confirm(`Clear QC session ${label} from the list?\n\nThis only removes it from `
        + `this list — the capture file on disk is kept.`)) return;
    try { await api(`/api/qc/${id}`, { method: "DELETE" }); } catch (e) { msg("err", "clear failed: " + e.message); return; }
    sessions.delete(id); feedBySession.delete(id);
    if (focusId === id) focusId = null;
    renderTabs(); renderFeed();
  }

  // tick uptime once a second without re-fetching
  setInterval(() => {
    if ([...sessions.values()].some((s) => s.status === "running")) renderTabs();
  }, 1000);

  // ---- feed header: actions + checks + verdict, for the focused session ----
  function renderFeedActions(s) {
    const host = $("qc-feed-actions");
    host.innerHTML = "";
    if (!s) return;
    const running = s.status === "running";
    if (s.checks && s.checks.length) {
      const rep = el("button", "ghost mini", "📄 Report");
      rep.onclick = () => openReport(s.id);
      host.append(rep);
    }
    if (running) {
      const stop = el("button", "ghost mini danger", "■ Stop");
      stop.onclick = () => stopQc(s.id);
      host.append(stop);
    } else {
      // Resume brings back the SAME session (same id, cumulative packet count, capture
      // file appended-to) — Start on the form always creates a distinct new session.
      const res = el("button", "ghost mini", "▶ Resume");
      res.title = "resume this session on the same port (does not create a new one)";
      res.onclick = () => resumeQc(s.id);
      host.append(res);
      if (s.capture_path) {
        const dl = el("button", "ghost mini", "⤓ Capture");
        dl.onclick = () => window.open(`/api/qc/${s.id}/capture`, "_blank");
        host.append(dl);
      }
      const clr = el("button", "ghost mini tcp-x", "🗑 Clear");
      clr.title = "remove this session from the list (capture file on disk is kept)";
      clr.onclick = () => clearQc(s.id);
      host.append(clr);
    }
  }
  function renderChecksRow(s) {
    const host = $("qc-checks-row");
    host.innerHTML = "";
    (s && s.checks ? s.checks : []).forEach((chk) => {
      const chip = el("span", "qc-chip qc-chip-" + chk.result, chk.label);
      chip.title = chk.detail || "";
      host.append(chip);
    });
  }
  function renderVerdict(s) {
    const v = $("qc-feed-verdict");
    if (s && s.verdict) { v.textContent = s.verdict.toUpperCase(); v.className = "qc-verdict qc-verdict-" + s.verdict; }
    else { v.textContent = ""; v.className = ""; }
  }

  // ---- device info panel (right): firmware/vendor + alarm coverage for the
  // focused session's device_id. Cross-session: GET /api/qc/device/{id} reflects the
  // device's whole history (every session it has ever run), not just this one.
  let devicePanelSeq = 0;
  async function renderDevicePanel(deviceId) {
    const host = $("qc-device-panel");
    if (!deviceId) {
      host.innerHTML = '<p class="empty">Select a port above once a device connects.</p>';
      return;
    }
    const seq = ++devicePanelSeq;
    try {
      const d = await api(`/api/qc/device/${encodeURIComponent(deviceId)}`);
      if (seq !== devicePanelSeq) return;   // a newer request has since superseded this one
      host.innerHTML = "";
      host.append(el("div", "qc-imei", deviceId));
      const info = d.device_info || {};
      const bits = [d.protocol || "—", `${d.records_seen} records seen`];
      if (info.firmware_version) bits.push("firmware " + info.firmware_version);
      if (info.vendor_id) bits.push("vendor " + info.vendor_id);
      host.append(el("div", "muted", bits.join(" · ")));
      host.append(el("h4", "tcp-pkt-h", "Alarm coverage"));
      const alarms = el("div", "qc-checks");
      (d.alarms || []).forEach((a) => {
        const chip = el("span", "qc-chip qc-chip-" + (a.seen ? "pass" : "na"), a.label);
        chip.title = a.seen
          ? `seen ${a.count}×` + (a.last_seen ? " · last " + new Date(a.last_seen).toLocaleString() : "")
          : "never seen";
        alarms.append(chip);
      });
      host.append(alarms);
      if (!(d.alarms || []).length) alarms.append(el("p", "empty", "No known alarm catalog for this protocol."));
    } catch (e) {
      if (seq !== devicePanelSeq) return;
      host.innerHTML = "";
      host.append(el("p", "empty", `${deviceId} — failed to load (${e.message})`));
    }
  }

  // ---- live feed (center) ---------------------------------------------------
  function renderFeed() {
    const s = sessions.get(focusId);
    $("qc-feed-for").textContent = s ? `— #${s.id} :${s.port}` : "";
    renderFeedActions(s);
    renderChecksRow(s);
    renderVerdict(s);
    renderDevicePanel(s && s.device_id);
    const rows = feedBySession.get(focusId) || [];
    $("qc-feed-count").textContent = rows.length ? `${rows.length} shown` : "";
    const host = $("qc-feed");
    if (!rows.length) {
      host.innerHTML = focusId
        ? '<p class="empty">No packets yet for this session.</p>'
        : '<p class="empty">Start a QC session and connect a device to see packets here.</p>';
      return;
    }
    host.innerHTML = "";
    const t = el("table", "tcp-feed-table");
    const thead = el("thead");
    const htr = el("tr");
    htr.append(el("th", "", "Timestamp"));
    const ph = el("th", "", "Payload"); ph.colSpan = 2; htr.append(ph);
    thead.append(htr); t.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr", "tcp-feed-row");
      tr.title = "click to see the full packet";
      tr.onclick = () => openPacket(r);
      const tm = r.event_time || r.received_at;
      tr.append(el("td", "muted tcp-time", tm ? new Date(tm).toLocaleTimeString() : ""));
      const pkt = el("td", "mono tcp-raw", rawView(r.raw_hex)); pkt.colSpan = 2;
      tr.append(pkt);
      tb.append(tr);
    });
    t.append(tb); host.append(t);
  }
  $("qc-feed-clear").addEventListener("click", () => {
    if (focusId) feedBySession.set(focusId, []);
    renderFeed();
  });
  function setRawFmt(fmt) {
    rawFmt = fmt;
    try { localStorage.setItem(RAWFMT_KEY, fmt); } catch (e) { /* ignore */ }
    $("qc-fmt-hex").classList.toggle("on", fmt === "hex");
    $("qc-fmt-ascii").classList.toggle("on", fmt === "ascii");
    renderFeed();
  }
  $("qc-fmt-hex").addEventListener("click", () => setRawFmt("hex"));
  $("qc-fmt-ascii").addEventListener("click", () => setRawFmt("ascii"));
  setRawFmt(rawFmt);

  // full-packet modal
  function openPacket(r) {
    const hex = r.raw_hex || "";
    const when = r.event_time || r.received_at;
    $("qc-pkt-meta").textContent =
      `${r.device_id || "—"} · ${r.message_type || ""} · ${hex.length / 2} bytes`
      + (when ? " · " + new Date(when).toLocaleString() : "");
    $("qc-pkt-ascii").textContent = hex ? asciiFull(hex) : "(no raw bytes on this record)";
    $("qc-pkt-hex").textContent = hex ? hexSpaced(hex) : "";
    $("qc-pkt-modal").style.display = "flex";
  }
  $("qc-pkt-close").addEventListener("click", () => { $("qc-pkt-modal").style.display = "none"; });
  $("qc-pkt-modal").addEventListener("click", (e) => {
    if (e.target.id === "qc-pkt-modal") $("qc-pkt-modal").style.display = "none";
  });

  // ---- QC report modal (verdict + checks) ----------------------------------
  let reportQcId = null;
  async function openReport(qcId) {
    reportQcId = qcId;
    $("qc-report-verdict").textContent = "";
    $("qc-report-meta").textContent = "loading…";
    $("qc-report-checks").innerHTML = "";
    $("qc-report-modal").style.display = "flex";
    try {
      const r = await api(`/api/qc/${qcId}/report`);
      const v = $("qc-report-verdict");
      v.textContent = r.verdict ? r.verdict.toUpperCase() : "";
      v.className = "qc-verdict" + (r.verdict ? " qc-verdict-" + r.verdict : "");
      const started = r.started_at ? new Date(r.started_at * 1000).toLocaleString() : "—";
      const ended = r.ended_at ? new Date(r.ended_at * 1000).toLocaleString() : "—";
      $("qc-report-meta").textContent =
        `#${r.qc_id} · ${r.device_id || "(unidentified)"} · ${r.identified_as || r.protocol_mode}`
        + ` · port ${r.port} · ${r.packet_count} packets · ${started} → ${ended}`
        + (r.operator ? ` · operator: ${r.operator}` : "")
        + (r.prior_session_count ? ` · seen before ×${r.prior_session_count}` : "");
      const host = $("qc-report-checks");
      (r.checks || []).forEach((c) => {
        const chip = el("span", "qc-chip qc-chip-" + c.result, c.label);
        chip.title = c.detail || "";
        host.append(chip);
      });
      if (!(r.checks || []).length) host.append(el("p", "empty", "No checks evaluated yet."));
    } catch (e) {
      $("qc-report-meta").textContent = "failed to load: " + e.message;
    }
  }
  $("qc-report-close").addEventListener("click", () => { $("qc-report-modal").style.display = "none"; });
  $("qc-report-modal").addEventListener("click", (e) => {
    if (e.target.id === "qc-report-modal") $("qc-report-modal").style.display = "none";
  });
  $("qc-report-json").addEventListener("click", () => {
    if (reportQcId != null) window.open(`/api/qc/${reportQcId}/report`, "_blank");
  });
  $("qc-report-txt").addEventListener("click", () => {
    if (reportQcId != null) window.open(`/api/qc/${reportQcId}/report?fmt=txt`, "_blank");
  });

  // ---- record routing: which QC session does an incoming record belong to? ----
  function routeRecord(r) {
    if (!r.peer) return;
    const owner = [...sessions.values()].find((s) => s.peer === r.peer);
    if (!owner) return;
    const arr = feedBySession.get(owner.id) || [];
    arr.unshift(r);
    if (arr.length > MAX_FEED) arr.length = MAX_FEED;
    feedBySession.set(owner.id, arr);
    if (owner.id === focusId) renderFeed();
  }

  // ---- refresh + WebSocket -------------------------------------------------
  async function refreshSessions() {
    try {
      const list = await api("/api/qc");
      list.forEach((s) => { if (!feedBySession.has(s.id)) feedBySession.set(s.id, []); sessions.set(s.id, s); });
      if (focusId == null && list.length) focusId = list[0].id;
      renderTabs();
      if (focusId != null) renderFeed();
    } catch (e) { /* ignore */ }
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    sock.onclose = () => setTimeout(connect, 2000);
    sock.onerror = () => sock.close();
    sock.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "snapshot") {
        // Re-seed each known session's feed from the server's recent buffer (peer match)
        // so returning to this page doesn't come back empty (retention hard rule).
        (m.data.recent || []).slice().reverse().forEach(routeRecord);
      } else if (m.type === "record") {
        routeRecord(m.data);
      } else if (m.type === "qc") {
        const s = m.data;
        if (!feedBySession.has(s.id)) feedBySession.set(s.id, []);
        sessions.set(s.id, s);
        if (focusId == null) focusId = s.id;
        renderTabs();
        if (s.id === focusId) renderFeed();
      }
    };
  }

  loadProtocols();
  loadDefaults();
  // Populate `sessions` BEFORE opening the WebSocket: otherwise its first "snapshot"
  // message can race ahead of this fetch and routeRecord() would find no owning session
  // for any of it (silently dropping the re-seed the retention rule depends on).
  refreshSessions().finally(() => {
    setInterval(refreshSessions, 2500);
    connect();
  });
})();
