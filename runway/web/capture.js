/* Runway - Capture: ONE page, browser-style tabs, per-tab mode.
 *   • Testing tab — raw packet inspection on any protocol (backend: /api/listen jobs).
 *   • QC tab      — validated qualification per the config profile (backend: /api/qc/*).
 * Both share the same core: tab strip, device list, live feed (RAW/Parsed + colour code),
 * packet/send modals. QC tabs add live checks + verdict + report + capture + device info;
 * Testing tabs add log download / continuous logging. Live updates over /ws. */
(() => {
  "use strict";

  const { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull,
          fmtDate, TZ_LABEL, packetKind, fmtIdle,
          canOf, canTable, ioWithoutCan, canSignalsOf, canSignalsTable } = window.Runway;

  // Status messages: write to a visible modal span if given one, else a bottom-right toast.
  const toast = el("div", "toast");
  document.body.appendChild(toast);
  let toastTimer = null;
  const msg = (id, cls, txt) => {
    if (txt) txt = txt.charAt(0).toUpperCase() + txt.slice(1);   // every message in sentence case
    const m = id && $(id);
    if (m && m.offsetParent !== null) { m.className = cls; m.textContent = txt; return; }
    toast.className = "toast" + (cls ? " " + cls : "") + (txt ? " show" : "");
    toast.textContent = txt;
    clearTimeout(toastTimer);
    if (txt) toastTimer = setTimeout(() => toast.classList.remove("show"), 4000);
  };
  const UI = window.RunwayUI;                       // icons, labels, the house confirm (shell.js)
  const iconBtn = (cls, icon, label) => { const b = UI.setLabel(el("button", cls), icon, label); b.type = "button"; return b; };
  const withIcon = (cls, icon, text) => UI.setLabel(el("span", cls), icon, text);
  const postJSON = (path, body) => api(path, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtBytes = (n) => (!n ? "0 B" : n < 1024 ? n + " B" : n < 1048576
    ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");

  // Placeholder ids codecs emit before a real identity (IMEI) is learned (kept in sync with
  // qc/session.py's set) — never shown as a device name.
  const UNKNOWN_IDS = new Set(["", "unknown", "teltonika-unknown", "gt06-unknown", "nmea-device", "device-1", "ais140-unknown", "zenithra-unknown"]);

  const jobs = new Map();          // jid -> job (both modes: QC sessions also have a backing job)
  const sessions = new Map();      // qc_id -> QC session view
  const closedJids = new Set();    // listen jobs the user closed (block WS resurrection)
  const closedQc = new Set();      // QC sessions the user closed
  const feed = [];                 // recent records across all sessions (display window)
  const MAX_FEED = 500;
  const logBuffer = [];            // received records kept for log export (Testing)
  const LOG_MAX = 20000;
  const peerJob = new Map();       // peer (ip:port) -> jid, for routing records to a tab
  const peerInfo = new Map();      // peer -> {device_id, protocol} learned from records
  let sendCtx = null;              // { job, peer } for the send modal
  let LOCAL_IP = "";

  const RAWFMT_KEY = "runway.cap.rawfmt";   // "hex" | "ascii"      (localStorage)
  const VIEW_KEY = "runway.cap.view";       // "raw" | "parsed"     (localStorage)
  const MODE_KEY = "runway.cap.mode";       // draft default mode   (localStorage)
  const DEVCOLL_KEY = "runway.cap.devcoll"; // devices collapsed?   (localStorage)
  const FOCUS_KEY = "runway.cap.focus";     // focused tab key      (sessionStorage)
  const HIDDEN_KEY = "runway.cap.hidden";   // unchecked peers      (sessionStorage)
  const SELDEV_KEY = "runway.cap.seldev";   // {qcId: device_id}    (sessionStorage)
  const QCVIEW_KEY = "runway.cap.qcview";   // "raw" | "summary"    (localStorage)
  const HIDDENQC_KEY = "runway.cap.qchidden"; // {qcId: [device_id]} hidden from Raw (sessionStorage)
  const NEW = "__new__";           // sentinel focus key for the unstarted "New session" draft
  let draftOpen = false;
  let draftMode = "test";          // "test" | "qc" — what a new session will be
  let rawFmt = "hex";
  let viewMode = "raw";
  let qcView = "summary";          // QC feed pane: "summary" (per-device, default) | "raw" (packet stream)
  let devicesCollapsed = false;
  let focus = null;                // "t:<jid>" | "q:<qcId>" | NEW | null
  const hiddenPeers = new Set();
  try {
    rawFmt = localStorage.getItem(RAWFMT_KEY) || "hex";
    viewMode = localStorage.getItem(VIEW_KEY) || "raw";
    qcView = localStorage.getItem(QCVIEW_KEY) || "summary";
    draftMode = localStorage.getItem(MODE_KEY) || "test";
    devicesCollapsed = localStorage.getItem(DEVCOLL_KEY) === "1";
    focus = sessionStorage.getItem(FOCUS_KEY) || null;
    JSON.parse(sessionStorage.getItem(HIDDEN_KEY) || "[]").forEach((p) => hiddenPeers.add(p));
  } catch (e) { /* ignore */ }
  if (focus === NEW) draftOpen = true;
  const saveHidden = () => {
    try { sessionStorage.setItem(HIDDEN_KEY, JSON.stringify([...hiddenPeers])); } catch (e) { /* ignore */ }
  };
  // QC per-device selection: {qcId: device_id} — which device's coverage/feed/details show.
  let selDev = {};
  try { selDev = JSON.parse(sessionStorage.getItem(SELDEV_KEY) || "{}") || {}; } catch (e) { selDev = {}; }
  const saveSelDev = () => {
    try { sessionStorage.setItem(SELDEV_KEY, JSON.stringify(selDev)); } catch (e) { /* ignore */ }
  };
  const qcDevices = (sess) => (sess && sess.devices) || [];
  function qcSelectedDevice(sess) {
    const devs = qcDevices(sess);
    if (!devs.length) return null;
    return devs.find((d) => d.device_id === selDev[sess.id]) || devs[0];
  }
  function selectDevice(sess, did) { selDev[sess.id] = did; saveSelDev(); renderAll(); }

  // QC per-device Raw-feed visibility: {qcId: [device_id,...]} HIDDEN from the packet stream
  // — lets the operator show all devices, a partial set, or a single device on this port,
  // same "checkbox per row" pattern as the Testing tab's per-peer hide/show.
  let hiddenQcRaw = {};
  try { hiddenQcRaw = JSON.parse(sessionStorage.getItem(HIDDENQC_KEY) || "{}") || {}; } catch (e) { hiddenQcRaw = {}; }
  const saveHiddenQc = () => {
    try { sessionStorage.setItem(HIDDENQC_KEY, JSON.stringify(hiddenQcRaw)); } catch (e) { /* ignore */ }
  };
  const hiddenQcSet = (qcId) => new Set(hiddenQcRaw[qcId] || []);
  function setHiddenQc(qcId, set) { hiddenQcRaw[qcId] = [...set]; saveHiddenQc(); }

  const _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtLogTs(t) {
    const d = new Date(t);
    if (isNaN(d.getTime())) return "";
    const p2 = (n) => String(n).padStart(2, "0");
    const p3 = (n) => String(n).padStart(3, "0");
    return `${p2(d.getDate())}-${_MON[d.getMonth()]}-${d.getFullYear()} `
      + `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}:${p3(d.getMilliseconds())}`;
  }
  const rawView = (hex, fmt) => (!hex ? "" : fmt === "ascii" ? hexToAscii(hex) : hexSpaced(hex));
  // QC renders per the protocol (no user toggle): text protocols show ASCII, binary show
  // HEX. The QC profile's raw_view (sess.raw_view) is authoritative; the protocol-name set
  // covers profiles without one; and when the identity is unknown (e.g. a session that
  // ended without a clean stop), sniffing a sample packet keeps $-ASCII sentences readable
  // instead of collapsing to hex.
  const ASCII_PROTOCOLS = new Set(["ais140", "zenithra", "nmea"]);
  const looksAscii = (hex) => {
    if (!hex) return false;
    const n = Math.min(hex.length, 128);           // sample the first 64 bytes
    let printable = 0;
    for (let i = 0; i + 1 < n; i += 2) {
      const b = parseInt(hex.slice(i, i + 2), 16);
      if ((b >= 32 && b < 127) || b === 10 || b === 13 || b === 9) printable++;
    }
    return printable / (n / 2) >= 0.9;
  };
  const qcRawFmt = (sess, sampleHex) => (sess && sess.raw_view) ? sess.raw_view
    : (ASCII_PROTOCOLS.has((sess && sess.identified_as) || "") || looksAscii(sampleHex))
      ? "ascii" : "hex";

  function mapJobPeers(j) {
    (j.peers || []).forEach((p) => peerJob.set(p, j.id));
    (j.clients || []).forEach((c) => {
      peerJob.set(c.peer, j.id);
      // The server names each connection's device: the page's own records of an old
      // connection are long gone, which listed one re-dialling device as dozens.
      if (c.device_id || c.protocol) learnDevice(c);
    });
  }
  function learnDevice(r) {
    if (!r.peer) return;
    const info = peerInfo.get(r.peer) || { device_id: "", protocol: "" };
    if (r.device_id && !UNKNOWN_IDS.has(r.device_id)) info.device_id = r.device_id;
    if (r.protocol && (r.protocol !== "generic_hex" || !info.protocol)) info.protocol = r.protocol;
    peerInfo.set(r.peer, info);
  }
  const deviceLabel = (peer) => (peerInfo.get(peer) || {}).device_id || peer || "";
  // Testing hides by DEVICE (its IMEI), not by connection: a device re-dials with a new source
  // port each time, and a device the operator hid must stay hidden on its next connection.
  // A peer whose device is not known yet is hidden by its own address.
  const deviceKey = (peer) => (peerInfo.get(peer) || {}).device_id || peer;
  const isHiddenTesting = (r) => hiddenPeers.has(r.peer) || hiddenPeers.has(deviceKey(r.peer))
    || (!!r.device_id && hiddenPeers.has(r.device_id));
  const protoLabel = (s) => s.identified_as || (s.protocol_mode === "manual" ? s.protocol
    : (s.status === "running" ? "Identifying" : "Unidentified"));
  // Human label for a QC verdict/state (used on the tab, summary pill, feed badge).
  const VERDICT_LABELS = { in_progress: "In progress", timeout: "Timeout", pass: "Pass",
    conditional_pass: "Conditional pass", fail: "Fail" };
  const verdictLabel = (v) => VERDICT_LABELS[v] || (v ? v.charAt(0).toUpperCase() + v.slice(1).replace(/_/g, " ") : "");
  const fmtIdleLong = (s) => {   // "3m 20s" / "45s"
    s = Math.max(0, Math.floor(s));
    const m = Math.floor(s / 60);
    return m ? `${m}m ${s % 60}s` : `${s}s`;
  };

  // ---- unified tab model ----------------------------------------------------
  const listenJobs = () => [...jobs.values()].filter((j) => j.kind === "listen");
  function tabList() {
    const out = [];
    listenJobs().forEach((j) => out.push({ mode: "test", key: "t:" + j.id, id: j.id, job: j, t: j.started || 0 }));
    [...sessions.values()].forEach((s) => out.push({ mode: "qc", key: "q:" + s.id, id: s.id, sess: s, t: s.started_at || 0 }));
    return out.sort((a, b) => (a.t - b.t) || a.key.localeCompare(b.key));   // creation order
  }
  function focusedTab() {
    if (focus === NEW) return null;
    const list = tabList();
    let t = list.find((x) => x.key === focus);
    if (!t && list.length) { t = list[list.length - 1]; focus = t.key; }
    return t || null;
  }
  const tabRunning = (t) => t.mode === "qc" ? t.sess.status === "running"
    : (t.job.status === "running" || t.job.status === "pending");
  const tabJobId = (t) => t.mode === "qc" ? (t.sess.job_id || null) : t.id;
  const focusJobId = () => { const t = focusedTab(); return t ? tabJobId(t) : null; };
  function setFocus(key) {
    focus = key;
    try { sessionStorage.setItem(FOCUS_KEY, key || ""); } catch (e) { /* ignore */ }
    renderAll();
  }

  // ---- draft (unstarted "New session") tab ----------------------------------
  function setDraftMode(m) {
    draftMode = m;
    try { localStorage.setItem(MODE_KEY, m); } catch (e) { /* ignore */ }
    $("cap-mode-qc").classList.toggle("on", m === "qc");
    $("cap-mode-test").classList.toggle("on", m === "test");
    $("cap-test-proto").style.display = m === "test" ? "" : "none";
    $("cap-relay-fld").style.display = m === "test" ? "" : "none";
    $("cap-store-fld").style.display = m === "test" ? "" : "none";
    $("cap-qc-mode-fld").style.display = m === "qc" ? "" : "none";
    $("cap-qc-operator-fld").style.display = m === "qc" ? "" : "none";
    $("cap-qc-proto-fld").style.display = (m === "qc" && $("cap-qc-mode").value === "manual") ? "" : "none";
  }
  function openDraft() {
    if (draftOpen) { setFocus(NEW); return; }
    draftOpen = true;
    msg(null, "", "");
    loadDefaults();
    setFocus(NEW);
  }
  function closeDraft() {
    draftOpen = false;
    if (focus === NEW) focus = null;
    if (!tabList().length) { openDraft(); return; }   // never leave the page with nothing
    renderAll();
  }

  // ---- start form -----------------------------------------------------------
  async function loadCodecs(selected) {
    const sel = $("cap-codec");
    sel.innerHTML = "";
    sel.appendChild(new Option("Auto (detect)", "auto"));
    sel.appendChild(new Option("Generic (raw — no protocol)", "generic_hex"));
    try {
      (await api("/api/codecs")).forEach((c) => {
        if (c.name === "generic_hex" || c.name === "custom_template") return;
        sel.appendChild(new Option(c.display_name || c.name, c.name));
      });
    } catch (e) { /* ignore */ }
    if (selected) sel.value = selected;
  }
  async function loadProtocols() {
    const sel = $("cap-qc-protocol");
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
      $("cap-port").value = d.port;
      $("cap-host").value = d.local_ip || d.host || "0.0.0.0";
      $("cap-relay").value = d.relay || "";
      $("cap-store").checked = !!d.store;
      $("cap-localip").textContent = LOCAL_IP
        ? `This machine: ${LOCAL_IP}${d.hostname ? " (" + d.hostname + ")" : ""} — devices dial in here`
        : "";
      await loadCodecs("auto");
    } catch (e) { await loadCodecs("auto"); }
  }
  $("cap-mode-qc").addEventListener("click", () => setDraftMode("qc"));
  $("cap-mode-test").addEventListener("click", () => setDraftMode("test"));
  $("cap-qc-mode").addEventListener("change", () => {
    $("cap-qc-proto-fld").style.display = $("cap-qc-mode").value === "manual" ? "" : "none";
  });

  $("cap-start").addEventListener("click", async () => {
    const port = Number($("cap-port").value);
    if (!port || port < 1 || port > 65535) { msg(null, "err", "enter a valid port"); return; }
    const host = $("cap-host").value.trim() || "0.0.0.0";
    try {
      if (draftMode === "test") {
        const r = await postJSON("/api/listen", {
          codec: $("cap-codec").value, host, port,
          relay: $("cap-relay").value.trim(), store: $("cap-store").checked,
        });
        jobs.set(r.job.id, r.job); mapJobPeers(r.job);
        draftOpen = false;
        msg(null, "ok", `listening on ${host}:${port}`);
        setFocus("t:" + r.job.id);
      } else {
        const mode = $("cap-qc-mode").value;
        const qc = await postJSON("/api/qc/start", {
          port, host, protocol_mode: mode,
          protocol: mode === "manual" ? $("cap-qc-protocol").value : "auto",
          operator: $("cap-operator").value.trim(),
        });
        sessions.set(qc.id, qc);
        draftOpen = false;
        msg(null, "ok", `QC #${qc.id} listening on ${host}:${port}`);
        setFocus("q:" + qc.id);
        refreshAll();
      }
    } catch (e) { msg(null, "err", "start failed: " + e.message); }
  });

  // ---- stop / resume / close (branch on mode) --------------------------------
  async function stopTab(t) {
    try {
      if (t.mode === "qc") {
        const s = await api(`/api/qc/${t.id}/stop`, { method: "POST" }); sessions.set(t.id, s);
      } else {
        await api("/api/stop/" + t.id, { method: "POST" });
        const j = jobs.get(t.id); if (j) { j.status = "stopped"; j.ended = j.ended || Date.now() / 1000; }
      }
      renderAll(); msg(null, "ok", "stopped");
    } catch (e) { msg(null, "err", "stop failed: " + e.message); }
  }
  async function resumeTab(t) {
    try {
      if (t.mode === "qc") {
        const s = await api(`/api/qc/${t.id}/resume`, { method: "POST" }); sessions.set(t.id, s);
      } else {
        const r = await api("/api/resume/" + t.id, { method: "POST" }); jobs.set(t.id, { ...jobs.get(t.id), ...r.job });
      }
      renderAll(); msg(null, "ok", "Resumed");
    } catch (e) { msg(null, "err", "Could not resume: " + e.message); }
  }
  async function closeTab(t) {
    const isQc = t.mode === "qc";
    const label = isQc ? `#${t.id} (${t.sess.device_id || "port " + t.sess.port})`
                       : `:${t.job.port} (${t.job.codec})`;
    const running = tabRunning(t);
    const ok = await UI.confirm({
      title: running ? "Stop this session?" : "Close this tab?",
      text: (running ? `${label} stops listening and its tab closes.` : `${label} closes.`)
        + (isQc ? " The capture file on disk is kept." : ""),
      action: running ? "Stop and close" : "Close", tone: running ? "danger" : "" });
    if (!ok) return;
    try {
      if (isQc) {
        if (tabRunning(t)) await api(`/api/qc/${t.id}/stop`, { method: "POST" });
        for (let i = 0; ; i++) {
          try { await api(`/api/qc/${t.id}`, { method: "DELETE" }); break; }
          catch (e) { if (i >= 20) throw e; await sleep(100); }
        }
      } else {
        if (tabRunning(t)) await api("/api/stop/" + t.id, { method: "POST" });
        for (let i = 0; ; i++) {
          try { await api("/api/jobs/" + t.id, { method: "DELETE" }); break; }
          catch (e) { if (i >= 20) throw e; await sleep(100); }
        }
      }
    } catch (e) { msg(null, "err", "Could not close: " + e.message); return; }
    if (isQc) { closedQc.add(t.id); sessions.delete(t.id); }
    else { closedJids.add(t.id); jobs.delete(t.id); }
    if (focus === t.key) focus = null;
    msg(null, "ok", `Closed ${label}`);
    if (!tabList().length && !draftOpen) { openDraft(); return; }
    renderAll();
  }

  // ---- tab strip ------------------------------------------------------------
  function renderTabs() {
    const host = $("cap-tabs");
    host.innerHTML = "";
    tabList().forEach((t) => {
      const isQc = t.mode === "qc";
      const running = tabRunning(t);
      // devices online, not sockets: a device that re-dials holds a new connection each time
      const conn = new Set(((jobs.get(tabJobId(t)) || {}).clients || [])
        .filter((c) => c.connected).map((c) => deviceKey(c.peer))).size;
      const live = isQc ? !!(t.sess.connected || conn) : running;
      const tab = el("div", "tcp-tab" + (running ? " on" : " off") + (t.key === focus ? " active" : ""));
      tab.onclick = () => setFocus(t.key);
      tab.append(el("span", "tcp-dot " + (live ? "live" : "dead")));
      tab.append(el("span", "cap-tab-mode " + (isQc ? "is-qc" : "is-test"), isQc ? "QC" : "Test"));
      tab.append(el("span", "tcp-tab-port", String(isQc ? t.sess.port : t.job.port)));
      // Tab subtitle = the protocol only. The IMEI does NOT belong here: a QC port can
      // host many devices (which one would the tab name?), and the per-device identity
      // already lives in the device list + Summary panel.
      tab.append(el("span", "tcp-tab-sub muted", isQc ? protoLabel(t.sess) : t.job.codec));
      if (isQc && t.sess.verdict) tab.append(el("span", "qc-verdict qc-verdict-" + t.sess.verdict, verdictLabel(t.sess.verdict)));
      else if (conn) tab.append(el("span", "tcp-tab-count", String(conn)));
      const x = iconBtn("tcp-tab-x", "x");
      x.title = running ? "Stop this session and close the tab" : "Close this tab";
      x.setAttribute("aria-label", x.title);
      x.onclick = (e) => { e.stopPropagation(); closeTab(t); };
      tab.append(x);
      host.appendChild(tab);
    });
    if (draftOpen) {
      const tab = el("div", "tcp-tab tcp-tab-draft" + (focus === NEW ? " active" : ""));
      tab.onclick = () => setFocus(NEW);
      tab.append(el("span", "tcp-dot"));
      tab.append(el("span", "tcp-tab-port", "New session"));
      const x = iconBtn("tcp-tab-x", "x");
      x.title = "Discard this new session";
      x.setAttribute("aria-label", x.title);
      x.onclick = (e) => { e.stopPropagation(); closeDraft(); };
      tab.append(x);
      host.appendChild(tab);
    }
    const add = iconBtn("tcp-tab-add", "plus");
    add.title = "New session";
    add.setAttribute("aria-label", add.title);
    add.onclick = () => openDraft();
    host.appendChild(add);
  }

  // ---- focused session strip + device list (left) ---------------------------
  function renderSession() {
    const host = $("cap-session");
    host.innerHTML = "";
    const t = focusedTab();
    if (!t) return;
    const isQc = t.mode === "qc";
    const running = tabRunning(t);
    const port = isQc ? t.sess.port : t.job.port;
    const host_ = isQc ? t.sess.host : t.job.host;

    const head = el("div", "tcp-card-head");
    const conn = ((jobs.get(tabJobId(t)) || {}).clients || []).filter((c) => c.connected).length;
    head.append(el("span", "tcp-dot " + ((isQc ? (t.sess.connected || conn) : running) ? "live" : "dead")));
    const addr = (LOCAL_IP || host_) ? `${LOCAL_IP || host_}:${port}` : `:${port}`;
    const portEl = el("span", "tcp-port", addr);
    portEl.title = "Point devices to this address";
    head.append(portEl);
    head.append(el("span", "tcp-codec-tag", isQc ? protoLabel(t.sess) : t.job.codec));
    const status = isQc ? t.sess.status : t.job.status;
    head.append(el("span", "tcp-status " + status, status ? status.charAt(0).toUpperCase() + status.slice(1) : ""));
    head.append(el("span", "spacer"));
    if (running) {
      const stop = iconBtn("ghost mini danger", "stop", "Stop");
      stop.onclick = () => stopTab(t);
      head.append(stop);
    } else {
      const res = iconBtn("ghost mini", "play", "Resume");
      res.title = "Resume this session on the same port";
      res.onclick = () => resumeTab(t);
      head.append(res);
    }
    host.append(head);

    const meta = el("div", "tcp-card-meta");
    const started = isQc ? t.sess.started_at : t.job.started;
    const ended = isQc ? t.sess.ended_at : t.job.ended;
    const upSecs = running && started ? (Date.now() / 1000 - started)
      : (started && ended ? (ended - started) : null);
    meta.dataset.started = running && started ? started : "";
    const up = withIcon("tcp-up", "clock", upSecs == null ? "—" : fmtDur(upSecs));
    up.title = "Running for";
    meta.append(up);
    if (isQc) {
      meta.append(el("span", "", `${t.sess.packet_count || 0} packets`));
      if (running) {   // live idle timer — a QC session times out after ~10 min of silence
        const idleSpan = el("span", "cap-idle", "Idle —");
        idleSpan.dataset.last = lastRecvMsForJob(tabJobId(t)) || "";
        idleSpan.title = "Since the last packet (Timeout after 10 min idle)";
        meta.append(idleSpan);
      }
      if (t.sess.operator) meta.append(el("span", "", `Operator: ${t.sess.operator}`));
      if ((t.sess.prior_sessions || []).length) {
        const seen = withIcon("", "repeat", `Seen before ×${t.sess.prior_sessions.length}`);
        seen.title = "This device id has other QC sessions in the history";
        meta.append(seen);
      }
    } else {
      meta.append(el("span", "", `${t.job.records || 0} records`));
      meta.append(el("span", "", fmtBytes(t.job.bytes)));
      const routes = routesOf(t.job), nRules = Object.keys(routes.devices).length;
      if (routes.all || nRules) {
        const r = withIcon("cap-fork", "relay", [routes.all ? `All → ${routes.all}` : "",
          nRules ? `${nRules} device rule${nRules === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · "));
        r.title = "Raw bytes are forwarded by these rules; each server's replies come back down";
        meta.append(r);
      }
      if (t.job.store) {
        const s = withIcon("cap-fork", "store", "Stored");
        s.title = "Packets and IMU samples are kept in data/telemetry.db (IMU page)";
        meta.append(s);
      }
      const edit = iconBtn("ghost mini", "relay", "Relay and store");
      edit.title = "Where each device's data is relayed, and whether it is stored — changeable while the session runs";
      edit.onclick = () => openRelayDialog(t);
      meta.append(edit);
    }
    const err = isQc ? t.sess.error : t.job.error;
    if (err) meta.append(el("span", "err", err));
    host.append(meta);
  }

  // ---- Relay & store, changed on a running session ------------------------------
  // One rule for every device ("All devices → host:port") plus per-device overrides: follow the
  // all-devices rule, no relay, or its own server. Saved rules apply at once — a connected
  // device moved to another server gets a new link there, its login replayed first.
  const routesOf = (job) => job.routes || { all: job.relay || "", devices: {} };
  function openRelayDialog(t) {
    const job = jobs.get(tabJobId(t)) || t.job;
    const routes = routesOf(job);
    const known = deviceGroups((job && job.clients) || []).filter((g) => g.known).map((g) => g.key);
    const ids = [...new Set([...known, ...Object.keys(routes.devices)])];

    const overlay = el("div", "modal-overlay");
    const modal = el("div", "modal relay-modal");
    const head = el("div", "modal-head");
    const x = iconBtn("modal-x", "x"); x.setAttribute("aria-label", "Close"); x.onclick = () => overlay.remove();
    head.append(el("span", "", `Relay and store · :${job.port}`), x);
    const body = el("div", "relay-body");

    const storeLbl = el("label", "relay-check");
    const storeCb = el("input"); storeCb.type = "checkbox"; storeCb.checked = !!job.store;
    storeLbl.append(storeCb, document.createTextNode(" Store packets and IMU samples"));
    storeLbl.title = "Kept in data/telemetry.db — the IMU page reads it";

    const allIn = el("input", "relay-target"); allIn.value = routes.all || ""; allIn.placeholder = "host:port";
    allIn.spellcheck = false;
    const allRow = el("div", "relay-row");
    allRow.append(el("span", "relay-dev", "All devices"), el("span"), allIn);

    const list = el("div", "relay-list");
    const rows = [];
    const addRow = (id) => {
      const cur = routes.devices[id];
      const row = el("div", "relay-row");
      const sel = el("select");
      [["follow", "Same as all devices"], ["off", "No relay"], ["own", "Its own server"]]
        .forEach(([v, label]) => sel.appendChild(new Option(label, v)));
      sel.value = !cur ? "follow" : cur === "off" ? "off" : "own";
      const target = el("input", "relay-target"); target.placeholder = "host:port"; target.spellcheck = false;
      target.value = cur && cur !== "off" ? cur : "";
      const sync = () => { target.disabled = sel.value !== "own"; if (target.disabled) target.value = ""; };
      sel.onchange = () => { sync(); if (sel.value === "own") target.focus(); };
      sync();
      row.append(el("span", "relay-dev mono", id), sel, target);
      list.append(row);
      rows.push({ id, sel, target });
    };
    ids.forEach(addRow);
    if (!ids.length) list.append(el("p", "empty", "No devices identified yet."));

    const addIn = el("input", "relay-add"); addIn.placeholder = "IMEI"; addIn.spellcheck = false;
    const addBtn = iconBtn("ghost mini", "plus", "Add device");
    addBtn.onclick = () => {
      const id = addIn.value.trim();
      if (!id || rows.some((r) => r.id === id)) return;
      const empty = list.querySelector(".empty"); if (empty) empty.remove();
      addRow(id); addIn.value = "";
    };
    const addRowEl = el("div", "relay-addrow"); addRowEl.append(addIn, addBtn);

    const err = el("div", "relay-err");
    body.append(storeLbl, el("div", "relay-h", "Relay"), allRow, list, addRowEl, err);

    const foot = el("div", "modal-foot");
    const cancel = el("button", "ghost", "Cancel"); cancel.onclick = () => overlay.remove();
    const save = el("button", "", "Save");
    save.onclick = async () => {
      const devices = {};
      for (const r of rows) {
        if (r.sel.value === "off") devices[r.id] = "off";
        else if (r.sel.value === "own") {
          if (!r.target.value.trim()) { err.textContent = `${r.id}: enter host:port`; r.target.focus(); return; }
          devices[r.id] = r.target.value.trim();
        }
      }
      try {
        const put = (path, b) => api(path, { method: "PUT", headers: { "content-type": "application/json" },
                                             body: JSON.stringify(b) });
        let res = await put(`/api/listen/${job.id}/routes`, { all: allIn.value.trim(), devices });
        if (storeCb.checked !== !!job.store) res = await put(`/api/listen/${job.id}/store`, { store: storeCb.checked });
        jobs.set(res.job.id, { ...(jobs.get(res.job.id) || {}), ...res.job });
        overlay.remove();
        msg(null, "ok", "Relay and store saved — applied to connected devices");
        renderAll();
      } catch (e) { err.textContent = e.message; }
    };
    foot.append(cancel, save);
    modal.append(head, body, foot);
    overlay.append(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.append(overlay);
  }

  // This device's own link to the next server: state, and bytes each way on hover.
  function relayTag(r) {
    const label = { up: "Relay", connecting: "Connecting", down: "Relay down",
                    idle: "Relay idle" }[r.state] || "Relay";
    const waiting = r.queued > 0 ? ` · ${r.queued.toLocaleString()} queued` : "";
    const tag = withIcon("cap-relay " + (r.state || "") + (r.queued > 0 ? " queued" : ""), "relay", label + waiting);
    const age = r.oldest ? Math.max(0, Math.round(Date.now() / 1000 - r.oldest)) : 0;
    tag.title = `${r.target} · ${r.state}` + (r.error ? ` (${r.error})` : "")
      + (r.state === "idle" ? " — the server closed the quiet link; it reconnects when the device sends" : "")
      + ` · up ${fmtBytes(r.bytes_up)} · down ${fmtBytes(r.bytes_down)}`
      + (r.queued > 0 ? ` · waiting: ${fmtBytes(r.queued_bytes)}, oldest ${fmtDur(age)} ago` : "")
      + (r.dropped ? ` · ${fmtBytes(r.dropped)} dropped past the outbox limit` : "");
    return tag;
  }

  function renderDevices() {
    const host = $("cap-devices");
    host.innerHTML = "";
    const t = focusedTab();
    if (!t) { host.append(el("p", "empty", "No session running")); return; }
    const isQc = t.mode === "qc";
    const running = tabRunning(t);
    const j = jobs.get(tabJobId(t));
    const clients = (j && j.clients) || [];
    const groups = isQc ? [] : deviceGroups(clients);
    const count = isQc ? (qcDevices(t.sess).length || clients.length) : groups.length;

    const headEl = el("div", "tcp-peers-head");
    const caret = iconBtn("tcp-collapse", devicesCollapsed ? "chevron-right" : "chevron-down");
    caret.title = devicesCollapsed ? "Show the devices" : "Hide the devices";
    caret.setAttribute("aria-label", caret.title);
    const toggle = () => {
      devicesCollapsed = !devicesCollapsed;
      try { localStorage.setItem(DEVCOLL_KEY, devicesCollapsed ? "1" : "0"); } catch (e) { /* ignore */ }
      renderDevices();
    };
    caret.onclick = toggle;
    headEl.append(caret);
    const title = el("span", "tcp-peers-title", "Devices on this port");
    title.style.cursor = "pointer"; title.title = caret.title; title.onclick = toggle;
    headEl.append(title, el("span", "ui-count", String(count)
      + (!isQc && clients.length > count ? ` · ${clients.length} connections` : "")));
    headEl.append(el("span", "spacer"));
    if (count && !devicesCollapsed) {   // bulk show/hide in the Raw feed — all/partial/none
      const all = el("button", "ghost mini", "All");
      all.title = "Tick every device (shown in Raw, included in the QC report)";
      all.onclick = () => {
        if (isQc) { setHiddenQc(t.sess.id, new Set()); }
        else { groups.forEach((g) => [g.key, ...g.conns.map((c) => c.peer)].forEach((k) => hiddenPeers.delete(k))); saveHidden(); }
        renderDevices(); renderFeed();
      };
      const none = el("button", "ghost mini", "None");
      none.title = "Untick every device (hidden from Raw, left out of the QC report)";
      none.onclick = () => {
        if (isQc) { setHiddenQc(t.sess.id, new Set(qcDevices(t.sess).map((d) => d.device_id))); }
        else { groups.forEach((g) => hiddenPeers.add(g.key)); saveHidden(); }
        renderDevices(); renderFeed();
      };
      headEl.append(all, none);
    }
    host.append(headEl);
    if (devicesCollapsed) return;

    if (isQc) { renderQcDeviceRows(t, host, clients, running); return; }

    if (!groups.length) {
      host.append(el("p", "empty", running ? "Waiting for devices to dial in" : "No devices connected"));
      return;
    }
    groups.forEach((g) => host.append(testingDeviceRow(t, g, running)));
  }

  // Testing: one row per DEVICE, its connections folded under it. A device that re-dials gets
  // a new source port each time; listing each connection as its own row made one device look
  // like several. How often it re-dials (and how long each connection lasts) is the fact worth
  // seeing, so the row says it and the fold lists every connection.
  const openDevices = new Set();
  function deviceGroups(clients) {
    const groups = new Map();
    clients.forEach((c) => {
      const key = deviceKey(c.peer);
      if (!groups.has(key)) groups.set(key, { key, known: key !== c.peer, conns: [] });
      groups.get(key).conns.push(c);
    });
    const begun = (c) => c.first || c.last || 0;
    return [...groups.values()].map((g) => {
      g.conns.sort((a, b) => begun(b) - begun(a));                    // newest first
      g.live = g.conns.find((c) => c.connected) || null;
      g.records = g.conns.reduce((s, c) => s + (c.records || 0), 0);
      g.last = Math.max(0, ...g.conns.map((c) => c.last || 0));
      return g;
    });
  }
  const clockOf = (s) => new Date(s * 1000).toTimeString().slice(0, 8);
  function testingDeviceRow(t, g, running) {
    const wrap = el("div", "cap-device");
    const head = g.live || g.conns[0];
    const info = peerInfo.get(head.peer) || {};
    const row = el("div", "tcp-peer tcp-client-row");
    row.dataset.peer = head.peer; row.dataset.last = g.last || 0;
    const keys = [g.key, ...g.conns.map((c) => c.peer)];
    const cb = el("input"); cb.type = "checkbox";
    cb.checked = !keys.some((k) => hiddenPeers.has(k));
    cb.title = "Show this device's packets in the feed";
    cb.onchange = () => {
      keys.forEach((k) => (cb.checked ? hiddenPeers.delete(k) : hiddenPeers.add(k)));
      saveHidden(); renderFeed();
    };
    row.append(cb, el("span", "tcp-dot " + (g.live ? "live" : "dead")));
    const name = el("div", "tcp-device-id");
    name.append(el("span", "mono tcp-client-addr" + (g.live ? "" : " muted"), g.known ? g.key : head.peer));
    if (g.known) name.append(el("span", "muted tcp-device-peer", g.live ? g.live.peer : `Last ${head.peer}`));
    row.append(name);
    const meta = el("div", "tcp-peer-meta");
    if (info.protocol) meta.append(el("span", "tcp-codec-tag", info.protocol));
    if (g.live && g.live.relay) meta.append(relayTag(g.live.relay));
    meta.append(el("span", "tcp-client-count", String(g.records)));
    meta.append(el("span", "tcp-client-idle muted", fmtIdle(g.last || 0)));
    const jid = tabJobId(t);
    if (running && g.live && jid) {
      meta.append(sendButton(() => openSend(jid, g.live.peer, info.protocol || "", t.job && t.job.port)));
    }
    row.append(meta);
    wrap.append(row);

    if (g.conns.length > 1) {
      const ended = g.conns.filter((c) => !c.connected && c.first && c.last > c.first);
      const avg = ended.length ? ended.reduce((s, c) => s + (c.last - c.first), 0) / ended.length : null;
      const open = openDevices.has(g.key);
      const btn = iconBtn("cap-reconn", open ? "chevron-down" : "chevron-right", `${g.conns.length} connections`
        + (avg != null ? ` · ~${fmtDur(avg)} each` : ""));
      btn.title = "The device dialled in again on a new connection — list each one";
      btn.onclick = () => { if (open) openDevices.delete(g.key); else openDevices.add(g.key); renderDevices(); };
      wrap.append(btn);
      if (open) {
        const list = el("div", "cap-conns");
        g.conns.forEach((c) => {
          const until = c.connected ? Date.now() / 1000 : (c.last || c.first);
          const r = el("div", "cap-conn");
          r.append(el("span", "tcp-dot " + (c.connected ? "live" : "dead")),
                   el("span", "mono", c.peer),
                   el("span", "muted", c.first ? `${clockOf(c.first)} → ${c.connected ? "Now" : clockOf(until)}` : "—"),
                   el("span", "muted", c.first ? fmtDur(Math.max(0, until - c.first)) : ""),
                   el("span", "tcp-client-count", String(c.records || 0)));
          list.append(r);
        });
        wrap.append(list);
      }
    }
    return wrap;
  }

  // QC: one row PER DEVICE (by IMEI) — reconnects merge. Row click selects it (drives the
  // Summary panel + status pill); the checkbox is independent — it controls whether this
  // device's packets show in the Raw feed, so the operator can view all devices, a partial
  // set, or a single device on this port (same pattern as the Testing tab's per-peer hide).
  function renderQcDeviceRows(t, host, clients, running) {
    const sess = t.sess;
    const jid = tabJobId(t);
    const connSet = new Set(clients.filter((c) => c.connected).map((c) => c.peer));
    const devices = qcDevices(sess);
    const selected = qcSelectedDevice(sess);
    const known = new Set(devices.flatMap((d) => d.peers || []));
    const hidden = hiddenQcSet(sess.id);

    devices.forEach((d) => {
      const connected = (d.peers || []).some((p) => connSet.has(p));
      const isSel = selected && selected.device_id === d.device_id;
      const row = el("div", "tcp-peer qc-dev-row" + (isSel ? " sel" : ""));
      row.onclick = () => selectDevice(sess, d.device_id);
      const cb = el("input"); cb.type = "checkbox"; cb.checked = !hidden.has(d.device_id);
      cb.title = "show this device's packets in the Raw feed + include it in the QC report";
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        const next = hiddenQcSet(sess.id);
        cb.checked ? next.delete(d.device_id) : next.add(d.device_id);
        setHiddenQc(sess.id, next);
        renderFeed();
      };
      row.append(cb);
      row.append(el("span", "tcp-dot " + (connected ? "live" : "dead")));
      const name = el("div", "tcp-device-id");
      const idLine = el("span", "mono tcp-client-addr");
      // (#6) stable per-IMEI serial — the same physical device always shows the same
      // number, a genuinely new IMEI gets the next one.
      if (d.serial != null) {
        const badge = el("span", "qc-serial-badge", "#" + d.serial);
        badge.title = "device serial " + d.serial + " — stable for this IMEI across reconnects and sessions";
        idLine.append(badge);
      }
      idLine.append(document.createTextNode(d.device_id));
      name.append(idLine);
      if ((d.peers || []).length) name.append(el("span", "muted tcp-device-peer", d.peers.join(", ")));
      row.append(name);
      const meta = el("div", "tcp-peer-meta");
      if (d.verdict) meta.append(el("span", "qc-verdict qc-verdict-" + d.verdict, verdictLabel(d.verdict)));
      const skipped = new Set(d.skipped || []);
      const req = (d.coverage || []).filter((c) => c.required && !skipped.has(c.key));
      if (req.length) meta.append(el("span", "tcp-client-count", `${req.filter((c) => c.seen).length}/${req.length}`));
      meta.append(el("span", "tcp-client-idle muted", fmtIdle(d.last_seen || 0)));
      const cp = (d.peers || []).find((p) => connSet.has(p));
      if (running && cp && jid) {
        // every currently-connected device on this port — the send modal offers them as
        // extra recipients so one command (e.g. SET:EIP) can go to the whole bench at once
        const recips = devices
          .map((dd) => ({ peer: (dd.peers || []).find((p) => connSet.has(p)),
                          label: (dd.serial != null ? `#${dd.serial} ` : "") + dd.device_id }))
          .filter((r) => r.peer);
        const send = sendButton((e) => { e.stopPropagation(); openSend(jid, cp, sess.identified_as || "", sess.port, recips); });
        meta.append(send);
      }
      row.append(meta);
      host.append(row);
    });

    // connected peers that haven't identified a device yet (always shown in Raw — nothing
    // to key a hide checkbox on until they identify)
    clients.filter((c) => c.connected && !known.has(c.peer)).forEach((c) => {
      const row = el("div", "tcp-peer");
      row.append(el("span"));    // empty checkbox-column placeholder, keeps the grid aligned
      row.append(el("span", "tcp-dot live"));
      const name = el("div", "tcp-device-id");
      name.append(el("span", "mono tcp-client-addr muted", c.peer));
      name.append(el("span", "muted tcp-device-peer", "identifying…"));
      row.append(name);
      const meta = el("div", "tcp-peer-meta");
      meta.append(el("span", "tcp-client-count", String(c.records || 0)));
      row.append(meta);
      host.append(row);
    });

    if (!devices.length && !clients.length) {
      host.append(el("p", "empty", running ? "Waiting for the device to dial in…" : "No devices connected."));
    }
  }

  // newest RECEIVE time (ms) across a session's records, for the live idle timer
  function lastRecvMsForJob(jid) {
    let best = 0;
    feed.forEach((r) => {
      if (r.peer && peerJob.get(r.peer) === jid && r.received_at) {
        const ms = Date.parse(r.received_at);
        if (ms > best) best = ms;
      }
    });
    return best;
  }

  // tick uptime + session idle + per-device idle once a second without re-fetching
  setInterval(() => {
    const m = $("cap-session").querySelector(".tcp-card-meta");
    if (m) {
      const st = Number(m.dataset.started);
      const up = m.querySelector(".tcp-up");
      if (st && up) UI.setLabel(up, "clock", fmtDur(Date.now() / 1000 - st));
      const idleEl = m.querySelector(".cap-idle");
      if (idleEl) {
        const last = Number(idleEl.dataset.last);
        idleEl.textContent = last ? "Idle " + fmtIdleLong(Date.now() / 1000 - last / 1000) : "Idle —";
        idleEl.classList.toggle("stale", last && (Date.now() - last) >= 600000);   // >10 min
      }
    }
    document.querySelectorAll("#cap-devices .tcp-client-row").forEach((row) => {
      const i = row.querySelector(".tcp-client-idle");
      if (i) i.textContent = fmtIdle(Number(row.dataset.last) || 0);
    });
  }, 1000);

  // ---- QC extras: report/checks/capture actions (verdict lives in the pill/modal) ----
  function renderQcActions(t) {
    const host = $("cap-qc-actions");
    host.innerHTML = "";
    if (!t || t.mode !== "qc") return;
    if (qcDevices(t.sess).length) {
      const exp = iconBtn("ghost mini", "download", "Report");
      exp.title = "The QC report (Excel or CSV): a summary sheet and one sheet per ticked device";
      exp.onclick = () => openExport(t.sess);
      host.append(exp);
    }
    if (qcDevices(t.sess).length) {
      const lot = iconBtn("ghost mini", "clipboard", "Lot details");
      lot.title = "The bench-only report fields (mobile no, power, COTA/FOTA, sign-off) — they print on the report";
      lot.onclick = () => openLot(t.sess);
      host.append(lot);
    }
    if (t.sess.capture_path) {
      const dl = iconBtn("ghost mini", "download", "Capture");
      dl.title = "This session's capture file";
      dl.onclick = () => window.open(`/api/qc/${t.id}/capture`, "_blank");
      host.append(dl);
    }
  }

  // ---- QC report export (XLS/CSV): Summary sheet + one sheet per selected device ------
  // Scope = the devices TICKED in "Devices on this port" (the same tick that filters the
  // Raw feed) — all ticked by default; untick a row to leave that device out.
  const REPORTFMT_KEY = "runway.cap.reportfmt";   // "xlsx" | "csv"  (localStorage)
  let exportQcId = null;
  const exportSelection = (sess) => {
    const hidden = hiddenQcSet(sess.id);
    return qcDevices(sess).filter((d) => !hidden.has(d.device_id));
  };
  function openExport(sess) {
    exportQcId = sess.id;
    try { $("cap-export-format").value = localStorage.getItem(REPORTFMT_KEY) || "xlsx"; } catch (e) { /* ignore */ }
    msg("cap-export-msg", "", "");
    renderExportDevices();
    $("cap-export-modal").style.display = "flex";
  }
  // A device is printable only once its QC has SETTLED (pass/conditional/fail/timeout);
  // in-progress blocks the report — matches the backend gate on /api/qc/{id}/export.
  const exportIncomplete = (d) => !d.verdict || d.verdict === "in_progress";
  const exportMissing = (d) => {
    const skipped = new Set(d.skipped || []);
    return (d.coverage || []).filter((c) => c.required && !c.seen && !skipped.has(c.key));
  };
  // Included-device list inside the modal — re-rendered on every renderAll() while open,
  // so ticking rows in the device list / skipping items updates it (and the gate) live.
  function renderExportDevices() {
    if (exportQcId == null || $("cap-export-modal").style.display === "none") return;
    const sess = sessions.get(exportQcId);
    const host = $("cap-export-devs");
    host.innerHTML = "";
    if (!sess) return;
    const all = qcDevices(sess);
    const sel = exportSelection(sess);
    host.append(el("div", "muted", `${sel.length} of ${all.length} device${all.length === 1 ? "" : "s"} included:`));
    let blocked = 0;
    sel.forEach((d) => {
      const bad = exportIncomplete(d);
      if (bad) blocked++;
      const row = el("div", "cap-export-dev mono" + (bad ? " cap-export-bad" : ""));
      if (d.serial != null) row.append(el("span", "qc-serial-badge", "#" + d.serial));
      row.append(document.createTextNode(d.device_id));
      if (d.verdict) row.append(el("span", "qc-verdict qc-verdict-" + d.verdict, verdictLabel(d.verdict)));
      if (bad) {
        const miss = exportMissing(d);
        if (miss.length) {
          const info = el("span", "cap-export-miss muted",
            `${miss.length} required item${miss.length === 1 ? "" : "s"} missing`);
          info.title = "missing: " + miss.map((c) => c.label).join(", ");
          row.append(info);
          // only the profile's skippable items can be waived — mandatory ones must be
          // exercised (or the device settles as TIMEOUT once idle past the threshold)
          const waivable = miss.filter((c) => (c.skippable !== undefined ? c.skippable : true));
          if (waivable.length) {
            const skip = el("button", "ghost mini",
              waivable.length === miss.length ? "Skip missing" : `Skip ${waivable.length} skippable`);
            skip.title = "Mark the missing skippable items as Skip:\n"
              + waivable.map((c) => c.label).join(", ")
              + (waivable.length === miss.length
                ? "\nThe device closes as Conditional pass and can be printed."
                : "\nThe rest are mandatory and cannot be skipped — the device settles "
                  + "once they arrive (or as Timeout after going idle).");
            skip.onclick = async () => {
              // the reason lands on the report's Deviations section + the audit trail —
              // a Conditional Pass on a QC certificate must be accountable
              const reason = await UI.prompt({
                title: "Skip the missing items?", label: "Reason (printed under the report's Deviations)",
                value: "", action: "Skip" });
              if (reason === null) return;               // cancelled
              setSkipped(sess.id, d.device_id,
                [...(d.skipped || []), ...waivable.map((c) => c.key)], reason);
            };
            row.append(skip);
          }
        } else {
          row.append(el("span", "cap-export-miss muted", "Still receiving, not settled yet"));
        }
      }
      host.append(row);
    });
    if (!sel.length) host.append(el("p", "cap-export-warn", "No devices ticked"));
    // print history: every download is archived server-side; show it so a re-print of a
    // since-edited session is a conscious act, not an accident
    const prints = sess.reports || [];
    if (prints.length) {
      const lastAt = prints[prints.length - 1].at;
      host.append(el("div", "muted cap-export-prior",
        `Printed ×${prints.length} — last ${lastAt ? new Date(lastAt * 1000).toLocaleString() : ""}`
        + " (archived in data/reports)"));
    }
    $("cap-export-warn").style.display = blocked ? "" : "none";
    $("cap-export-go").disabled = !sel.length || blocked > 0;
  }
  const closeExport = () => { $("cap-export-modal").style.display = "none"; exportQcId = null; };
  $("cap-export-close").addEventListener("click", closeExport);
  $("cap-export-cancel").addEventListener("click", closeExport);
  $("cap-export-modal").addEventListener("click", (e) => { if (e.target.id === "cap-export-modal") closeExport(); });
  $("cap-export-go").addEventListener("click", () => {
    const sess = exportQcId != null ? sessions.get(exportQcId) : null;
    if (!sess) return;
    const sel = exportSelection(sess);
    if (!sel.length) { msg("cap-export-msg", "err", "tick at least one device in the device list"); return; }
    const blocked = sel.filter(exportIncomplete);
    if (blocked.length) {
      msg("cap-export-msg", "err", "QC not completed — skip the missing items or untick: "
        + blocked.map((d) => d.device_id).join(", "));
      return;
    }
    const fmt = $("cap-export-format").value;
    try { localStorage.setItem(REPORTFMT_KEY, fmt); } catch (e) { /* ignore */ }
    // send the explicit id list only when it's a subset — the default (all) needs no param
    const subset = sel.length !== qcDevices(sess).length;
    const q = subset ? "&devices=" + encodeURIComponent(sel.map((d) => d.device_id).join(",")) : "";
    const a = el("a");
    a.href = `/api/qc/${sess.id}/export?fmt=${fmt}${q}`;
    document.body.appendChild(a); a.click(); a.remove();
    msg("cap-export-msg", "ok", `report requested — ${sel.length} device${sel.length === 1 ? "" : "s"}, ${fmt === "xlsx" ? "Excel" : "CSV zip"}`);
  });

  // ---- Lot details: bench-only report fields typed in-app ---------------------
  // Session-level lot/sign-off fields + per-device manual columns. Saved server-side
  // (POST /api/qc/{id}/lot) so the exported report is the complete deliverable — no
  // editing the Excel by hand afterwards. Field keys mirror the API whitelists.
  const LOT_FIELDS = [
    ["lot_id", "Lot / Batch id"], ["lot_size", "Lot size"], ["pcb_number", "PCB number"],
    ["observation", "Observation"], ["corrective_action", "Corrective action"],
    ["approved_by", "Quality approved by"],
  ];
  const MANUAL_FIELDS = [
    ["mobile_no", "Mobile No"], ["data_on_intouch", "Data on Intouch"],
    ["all_io_static", "All Input and Output (On Static)"], ["harness", "Harness"],
    ["battery_charging", "Battery Charging"], ["ignition_on_intouch", "Ignition On (Intouch)"],
    ["ignition_off_intouch", "Ignition Off (Intouch)"],
    ["plugged_alarm_intouch", "Plugged/Unplugged Alarm on Intouch"],
    ["cota", "COTA"], ["fota", "FOTA"], ["sms_command", "SMS Command"],
    ["gprs_command", "GPRS Command"], ["serial_command", "Serial Command"],
    ["power_ma_without_ign", "Power without Ignition (mA)"],
    ["power_ma_with_ign", "Power with Ignition ON (mA)"],
    ["field_testing", "Field Testing"], ["remarks", "Remarks"],
  ];
  let lotQcId = null;
  let lotDraft = { lot: {}, manual: {} };    // unsaved edits, staged per device until Save
  let lotDev = "";                           // device whose manual fields are displayed

  function lotInputs(host, defs, values, onInput) {
    host.innerHTML = "";
    defs.forEach(([key, label]) => {
      const w = el("label", "tcp-fld cap-lot-fld");
      w.append(document.createTextNode(label));
      const inp = el("input");
      inp.type = "text";
      inp.value = values[key] || "";
      inp.placeholder = "—";
      inp.oninput = () => onInput(key, inp.value);
      w.append(inp);
      host.append(w);
    });
  }
  function renderLotDevice(sess) {
    const saved = (sess.manual || {})[lotDev] || {};
    const vals = { ...saved, ...(lotDraft.manual[lotDev] || {}) };
    lotInputs($("cap-lot-fields"), MANUAL_FIELDS, vals,
      (k, v) => { (lotDraft.manual[lotDev] = lotDraft.manual[lotDev] || {})[k] = v; });
  }
  function openLot(sess) {
    lotQcId = sess.id;
    lotDraft = { lot: {}, manual: {} };
    const devs = qcDevices(sess);
    lotDev = (qcSelectedDevice(sess) || devs[0] || {}).device_id || "";
    const sel = $("cap-lot-dev");
    sel.innerHTML = "";
    devs.forEach((d) => sel.appendChild(
      new Option(`${d.serial != null ? "#" + d.serial + " " : ""}${d.device_id}`, d.device_id)));
    sel.value = lotDev;
    // edits are staged per device (oninput), so switching devices loses nothing
    sel.onchange = () => { lotDev = sel.value; renderLotDevice(sess); };
    lotInputs($("cap-lot-sess"), LOT_FIELDS, { ...(sess.lot || {}), ...lotDraft.lot },
      (k, v) => { lotDraft.lot[k] = v; });
    renderLotDevice(sess);
    msg("cap-lot-msg", "", "");
    $("cap-lot-modal").style.display = "flex";
  }
  const closeLot = () => { $("cap-lot-modal").style.display = "none"; lotQcId = null; };
  $("cap-lot-close").addEventListener("click", closeLot);
  $("cap-lot-cancel").addEventListener("click", closeLot);
  $("cap-lot-modal").addEventListener("click", (e) => { if (e.target.id === "cap-lot-modal") closeLot(); });
  $("cap-lot-save").addEventListener("click", async () => {
    if (lotQcId == null) return;
    try {
      let s = null;
      if (Object.keys(lotDraft.lot).length) {
        s = await postJSON(`/api/qc/${lotQcId}/lot`, { lot: lotDraft.lot });
      }
      for (const [did, fields] of Object.entries(lotDraft.manual)) {
        if (Object.keys(fields).length) {
          s = await postJSON(`/api/qc/${lotQcId}/lot`, { device_id: did, fields });
        }
      }
      if (s) { sessions.set(lotQcId, s); renderAll(); }
      lotDraft = { lot: {}, manual: {} };
      msg("cap-lot-msg", "ok", "saved — these values print on the report");
    } catch (e) { msg("cap-lot-msg", "err", "save failed: " + e.message); }
  });

  // ---- live feed (right) -----------------------------------------------------
  function ingest(rec) {
    learnDevice(rec);
    logBuffer.push(rec);
    if (logBuffer.length > LOG_MAX) logBuffer.shift();
    feed.unshift(rec);
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    if (rec.peer && peerJob.get(rec.peer) === focusJobId()) renderFeed();
  }
  function focusRows() {
    const t = focusedTab();
    const jid = focusJobId();
    if (!jid) return [];
    let rows = feed.filter((r) => r.peer && peerJob.get(r.peer) === jid);
    if (t && t.mode === "qc") {
      // Raw view: every device's packets EXCEPT those the operator hid via the device-list
      // checkboxes — so the operator can see all devices, a partial set, or a single device.
      // Unidentified peers (no device yet) always show — there's no checkbox for them yet.
      const hidden = hiddenQcSet(t.sess.id);
      if (hidden.size) {
        const hiddenPeersSet = new Set();
        qcDevices(t.sess).forEach((d) => { if (hidden.has(d.device_id)) (d.peers || []).forEach((p) => hiddenPeersSet.add(p)); });
        rows = rows.filter((r) => !hiddenPeersSet.has(r.peer));
      }
      return rows;
    }
    return rows.filter((r) => !isHiddenTesting(r));           // Testing: per-device show/hide
  }
  function renderFeed() {
    const host = $("cap-feed");
    const t = focusedTab();
    const port = t ? (t.mode === "qc" ? t.sess.port : t.job.port) : null;
    // QC: session id + port already live on the tab and the session strip, so the feed
    // header stays clean; Testing keeps the port hint since it has no session strip.
    $("cap-feed-for").textContent = t && t.mode !== "qc" ? `— :${port}` : "";
    const rows = focusRows();
    $("cap-feed-count").textContent = rows.length ? `${rows.length} shown` : "";
    if (!rows.length) {
      host.innerHTML = `<p class="empty">${t
        ? "No packets yet"
        : "No session running"}</p>`;
      return;
    }
    // QC forces raw view in the protocol's format; Testing honours the user's toggles.
    const isQc = t && t.mode === "qc";
    const view = isQc ? "raw" : viewMode;
    const sample = rows.length ? (rows.find((r) => r.raw_hex) || {}).raw_hex : "";
    const fmt = isQc ? qcRawFmt(t.sess, sample) : rawFmt;
    host.innerHTML = "";
    const tbl = el("table", "tcp-feed-table");
    const thead = el("thead");
    const htr = el("tr");
    htr.append(el("th", "", TZ_LABEL ? `Timestamp (${TZ_LABEL})` : "Timestamp"));
    htr.append(el("th", "", "Device"));
    if (view === "parsed") {
      htr.append(el("th", "", "Type")); htr.append(el("th", "", "Position")); htr.append(el("th", "", "Speed"));
    } else {
      htr.append(el("th", "", "Raw packet"));
    }
    thead.append(htr); tbl.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr", "tcp-feed-row pk-" + packetKind(r));
      tr.title = "click to see the full packet";
      tr.onclick = () => openPacket(r);
      const tm = r.event_time || r.received_at;
      const tcell = el("td", "tcp-time");
      if (tm) {
        const d = new Date(tm);
        tcell.append(el("div", "tcp-time-hms", d.toLocaleTimeString()));
        tcell.append(el("div", "tcp-time-date", fmtDate(tm)));
      }
      tr.append(tcell);
      tr.append(el("td", "mono", deviceLabel(r.peer)));
      if (view === "parsed") {
        const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
        const pos = (!isRaw && r.position && r.position.latitude != null)
          ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "";
        tr.append(el("td", "", r.message_type || ""));
        tr.append(el("td", "mono", pos));
        tr.append(el("td", "num", !isRaw && r.speed_kph != null ? String(r.speed_kph) : ""));
      } else {
        tr.append(el("td", "mono tcp-raw", rawView(r.raw_hex, fmt)));
      }
      tb.append(tr);
    });
    tbl.append(tb); host.append(tbl);
  }
  $("cap-feed-clear").addEventListener("click", () => {
    const jid = focusJobId();
    if (jid) {
      const mine = (r) => r.peer && peerJob.get(r.peer) === jid;
      for (let i = feed.length - 1; i >= 0; i--) if (mine(feed[i])) feed.splice(i, 1);
      for (let i = logBuffer.length - 1; i >= 0; i--) if (mine(logBuffer[i])) logBuffer.splice(i, 1);
    } else { feed.length = 0; logBuffer.length = 0; }
    renderFeed();
  });

  function setViewMode(v) {
    viewMode = v;
    try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* ignore */ }
    $("cap-view-raw").classList.toggle("on", v === "raw");
    $("cap-view-parsed").classList.toggle("on", v === "parsed");
    $("cap-rawfmt-seg").style.display = v === "raw" ? "" : "none";
    renderFeed();
  }
  $("cap-view-raw").addEventListener("click", () => setViewMode("raw"));
  $("cap-view-parsed").addEventListener("click", () => setViewMode("parsed"));
  function setRawFmt(fmt) {
    rawFmt = fmt;
    try { localStorage.setItem(RAWFMT_KEY, fmt); } catch (e) { /* ignore */ }
    $("cap-fmt-hex").classList.toggle("on", fmt === "hex");
    $("cap-fmt-ascii").classList.toggle("on", fmt === "ascii");
    renderFeed();
  }
  $("cap-fmt-hex").addEventListener("click", () => setRawFmt("hex"));
  $("cap-fmt-ascii").addEventListener("click", () => setRawFmt("ascii"));

  // colour-legend popover (ⓘ)
  const legendBtn = $("cap-legend-btn");
  const legendPop = $("cap-legend");
  legendBtn.addEventListener("click", (e) => { e.stopPropagation(); legendPop.hidden = !legendPop.hidden; });
  document.addEventListener("click", (e) => {
    if (!legendPop.hidden && !legendPop.contains(e.target) && e.target !== legendBtn) legendPop.hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") legendPop.hidden = true; });

  // full-packet modal
  function openPacket(r) {
    const hex = r.raw_hex || "";
    const bytes = hex.length / 2;
    const when = r.event_time ? new Date(r.event_time).toLocaleString() : "";
    $("cap-pkt-meta").textContent =
      `${deviceLabel(r.peer) || r.device_id || "—"} · ${r.message_type || ""} · ${bytes} bytes${when ? " · " + when : ""}`;
    // CAN frames (Zenithra Ax1 sCAN) as a table, and folded out of the JSON below — dozens of
    // frames in the dump push every other decoded field off the screen.
    const signals = canSignalsOf(r);
    const sigSec = $("cap-pkt-sig-sec");
    sigSec.hidden = !signals.length;
    if (signals.length) {
      $("cap-pkt-sig-h").textContent = `CAN signals — ${signals.length}`
        + ((r.io && r.io.can_signature) ? ` · ${r.io.can_signature}` : "");
      const host = $("cap-pkt-sig");
      host.innerHTML = "";
      host.append(canSignalsTable(signals));
    }
    const can = canOf(r);
    const canSec = $("cap-pkt-can-sec");
    canSec.hidden = !can.length;
    if (can.length) {
      $("cap-pkt-can-h").textContent = `CAN frames — ${can.length}`;
      const host = $("cap-pkt-can");
      host.innerHTML = "";
      host.append(canTable(can));
    }
    const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
    if (isRaw) {
      $("cap-pkt-parsed").textContent = "Raw packet: no protocol matched, nothing parsed";
    } else {
      const parsed = { ...r, io: ioWithoutCan(r.io) }; delete parsed.raw_hex; delete parsed.seq;
      $("cap-pkt-parsed").textContent = JSON.stringify(parsed, null, 2);
    }
    $("cap-pkt-ascii").textContent = hex ? asciiFull(hex) : "No raw bytes on this record";
    $("cap-pkt-hex").textContent = hex ? hexSpaced(hex) : "";
    $("cap-pkt-modal").style.display = "flex";
  }
  $("cap-pkt-close").addEventListener("click", () => { $("cap-pkt-modal").style.display = "none"; });
  $("cap-pkt-modal").addEventListener("click", (e) => {
    if (e.target.id === "cap-pkt-modal") $("cap-pkt-modal").style.display = "none";
  });

  // ---- QC Summary panel: inline (not a popup), lives right on the Live Packets page ------
  // Toggled by the Raw|Summary segmented control; shows the SELECTED device (from the left
  // device list) and re-renders on every poll/WS update, so it's always live.
  const qcRefCache = {};                       // protocol -> {packet_types, message_alert_ids}
  const qcRefLoading = new Set();              // protocols currently being fetched (dedupe)

  // Fetch the reference catalogue (descriptions + example packets) once per protocol; when
  // it lands, re-render so hover tooltips pick up the description + reference packet.
  function ensureQcRef(proto) {
    if (!proto || proto in qcRefCache || qcRefLoading.has(proto)) return;
    qcRefLoading.add(proto);
    api(`/api/protocols/${encodeURIComponent(proto)}.json`)
      .then((def) => { qcRefCache[proto] = def.content || {}; })
      .catch(() => { qcRefCache[proto] = {}; })
      .finally(() => { qcRefLoading.delete(proto); renderAll(); });
  }

  async function setSkipped(qcId, deviceId, keys, reason) {
    try {
      const s = await postJSON(`/api/qc/${qcId}/skip`,
        { device_id: deviceId, keys, reason: reason || "" });
      sessions.set(qcId, s); renderAll();
    } catch (e) { msg(null, "err", "skip failed: " + e.message); }
  }

  // Reference description + example for a coverage item, tailored to THIS row. Every
  // alert-id item (Live, History, and all 14 alerts) rides on the SAME generic NMP/PVT
  // sentence in the protocol JSON — the doc only ships one static example (fixed
  // packet_type + alert_id), so showing it verbatim made every alert row look identical.
  // Here we substitute this row's own packet_type code + alert_id into that example and
  // flag which tokens were substituted, so the caller can highlight exactly what THIS row
  // expects (e.g. "EA,10" for Emergency state ON) instead of whatever the doc happened to
  // capture. Returns {desc, parts}: parts is the example pre-split into {text, hl} tokens.
  function qcRefRow(proto, item) {
    const content = qcRefCache[proto] || {};
    const pt = content.packet_types || {};
    const alerts = content.message_alert_ids || {};
    const fam = item.group === "emergency" ? pt.EPB   // emergency-server traffic is the EPB sentence
      : item.group === "packet"
        ? (item.code === "LGN" ? pt.LGN : item.code === "HEL" ? (pt.HLP || pt.HLM)
          : item.code === "EPB" ? pt.EPB : (pt.PVT || pt.NMP))
        : (pt.PVT || pt.NMP);                       // alerts ride on the NMP/PVT sentence
    const desc = (item.group === "alert" && item.alert_id != null && alerts[item.alert_id])
      ? alerts[item.alert_id] : ((fam && fam.description) || item.label);
    if (!fam || !fam.example) return { desc, parts: [] };

    const fields = fam.fields || [];
    const ptypeField = fields.find((f) => f.name === "packet_type");
    const alertField = fields.find((f) => f.name === "alert_id");
    // Nothing to substitute (e.g. LGN/HEL — no packet_type/alert_id in this row's terms):
    // show the example plain.
    if (item.alert_id == null && !(item.code && ptypeField)) {
      return { desc, parts: [{ text: fam.example, hl: false }] };
    }
    // tokens[0] is the "$NMP"/"$EPB" header itself (the split includes it), so the JSON's
    // 1-based field index N lands at tokens[N], not tokens[N-1] — e.g. NMP field 3
    // (packet_type) is tokens[3]; EPB field 1 (packet_type EMR/SEM) is tokens[1].
    const tokens = fam.example.split(",");              // safe: packet_type/alert_id sit
    const hl = new Set();                               // well before any embedded-comma field
    if (ptypeField && item.code) { tokens[ptypeField.index] = item.code; hl.add(ptypeField.index); }
    if (alertField && item.alert_id != null) { tokens[alertField.index] = String(item.alert_id); hl.add(alertField.index); }
    const parts = [];
    tokens.forEach((t, i) => { if (i > 0) parts.push({ text: ",", hl: false }); parts.push({ text: t, hl: hl.has(i) }); });
    return { desc, parts };
  }

  // Custom hover tooltip for a coverage row's Item cell — the native title= attribute can't
  // render partial bold/highlight, which is the whole point here (#1). Shown only after the
  // pointer rests for ~2s (like a native tooltip), not on every brief hover.
  const COV_TIP_DELAY_MS = 2000;
  const covTip = el("div", "cov-tip");
  covTip.hidden = true;
  document.body.appendChild(covTip);
  let covTipTimer = null;
  function scheduleCovTip(anchorEl, proto, item) {
    clearTimeout(covTipTimer);
    covTipTimer = setTimeout(() => showCovTip(anchorEl, proto, item), COV_TIP_DELAY_MS);
  }
  function placeCovTip(anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    const tipRect = covTip.getBoundingClientRect();
    let left = Math.min(r.left, window.innerWidth - tipRect.width - 8);
    let top = r.bottom + 6;
    if (top + tipRect.height > window.innerHeight - 8) top = r.top - tipRect.height - 6;
    covTip.style.left = Math.max(8, left) + "px";
    covTip.style.top = Math.max(8, top) + "px";
  }
  function showCovTip(anchorEl, proto, item) {
    const ref = qcRefRow(proto, item);
    covTip.innerHTML = "";
    covTip.append(el("div", "cov-tip-desc", ref.desc));
    if (ref.parts.length) {
      covTip.append(el("div", "cov-tip-h", "Reference packet — expected for this row"));
      const ex = el("div", "cov-tip-example");
      ref.parts.forEach((p) => ex.append(p.hl ? el("mark", "", p.text) : p.text));
      covTip.append(ex);
    }
    covTip.hidden = false;
    placeCovTip(anchorEl);
  }
  function hideCovTip() { clearTimeout(covTipTimer); covTip.hidden = true; }

  // Generic hold-hover tooltip for icon buttons: rest the pointer ~2s to see the name.
  function holdTip(btn, text) {
    btn.addEventListener("mouseenter", () => {
      clearTimeout(covTipTimer);
      covTipTimer = setTimeout(() => {
        covTip.innerHTML = "";
        covTip.append(el("div", "cov-tip-desc", text));
        covTip.hidden = false;
        placeCovTip(btn);
      }, COV_TIP_DELAY_MS);
    });
    btn.addEventListener("mouseleave", hideCovTip);
  }

  // Downlink send button: the paper-plane icon from the shared set; its name shows on hold-hover.
  function sendButton(onclick) {
    const b = iconBtn("ghost mini cap-send-btn", "send");
    b.setAttribute("aria-label", "Send command to the device");
    holdTip(b, "Send command to the device");
    b.onclick = onclick;
    return b;
  }

  function renderQcSummaryPanel(sess) {
    const proto = sess.identified_as || "";
    ensureQcRef(proto);
    const dev = qcSelectedDevice(sess);
    const cov = (dev && dev.coverage) || [];
    const fmt = qcRawFmt(sess, ((cov.find((c) => c.last_raw_hex)) || {}).last_raw_hex);
    const skipped = new Set((dev && dev.skipped) || []);
    const did = dev ? dev.device_id : "";
    // No panel-level device-nav / verdict / session-meta header: the tab + session strip +
    // status pill already carry session, port, protocol and verdict, and the shown device
    // is bound by its Serial badge (device list highlight + the Serial in Device details).

    const body = $("cap-qcsum-body");
    body.innerHTML = "";
    if (!dev) {
      body.append(el("p", "empty", "No device identified yet — packets are still being classified."));
      return;
    }

    // --- (#8) Device details ---
    body.append(el("h4", "tcp-pkt-h", "Device details"));
    const dl = el("div", "qcd-devinfo");
    const kv = (k, v) => { const w = el("div", "qcd-kv"); w.append(el("span", "qcd-k", k), el("span", "qcd-v mono", v || "—")); return w; };
    dl.append(kv("Serial", dev.serial != null ? "#" + dev.serial : null),
      kv("IMEI", did), kv("Firmware", dev.firmware_version), kv("Vendor ID", dev.vendor_id));

    // live vitals from the device's most recent traffic (position/battery fall back to the
    // newest record that actually carries them — health/login packets don't have both)
    const lp = dev.last_packet || {};
    let lpType = null;                                  // A. Location / Alarm (id N) / <type>
    if (lp.message_type) {
      if ((lp.alarms || []).length) lpType = `Alarm${lp.alert_id != null ? " (id " + lp.alert_id + ")" : ""}`;
      else if (lp.packet_status === "H") lpType = "Location (history)";
      else if (lp.packet_status === "L" || lp.message_type === "normal") lpType = "Location (live)";
      else lpType = lp.message_type;
    }
    dl.append(kv("Last packet", lpType));
    // B. when the packet LANDED on the utility, in local time (IST here)
    dl.append(kv(`Last received${TZ_LABEL ? " (" + TZ_LABEL + ")" : ""}`,
      lp.received_at ? `${new Date(lp.received_at).toLocaleTimeString()} · ${fmtDate(lp.received_at)}` : null));
    // C. latest position + satellites + fix status
    const pos = lp.position;
    dl.append(kv("Lat, Long", pos ? `${pos.lat.toFixed(6)}, ${pos.lon.toFixed(6)}` : null));
    dl.append(kv("GPS", pos
      ? `${pos.satellites != null ? pos.satellites + " sats" : "? sats"} · ${pos.gps_valid ? "fixed" : "no fix"}` : null));
    // D. battery voltages from the latest packet carrying them
    const fv = (v) => (v == null ? "—" : v.toFixed(1) + "V");
    dl.append(kv("Battery (ext · int)", lp.battery ? `${fv(lp.battery.external_v)} · ${fv(lp.battery.internal_v)}` : null));
    // (#7) recall automatically: if this IMEI has a FINAL result from an EARLIER session,
    // surface it immediately — the operator shouldn't have to go searching for it.
    if (dev.prior_verdict) {
      const prior = el("div", "qcd-prior");
      prior.append(el("span", "muted", "Previously tested: "));
      prior.append(el("span", "qc-verdict qc-verdict-" + dev.prior_verdict, verdictLabel(dev.prior_verdict)));
      prior.append(el("span", "muted", ` (session #${dev.prior_qc_id})`));
      dl.append(prior);
    }
    body.append(dl);

    // one coverage table per group (#3): Packets, then Alerts
    const covTable = (items, heading) => {
      if (!items.length) return;
      const reqSeen = items.filter((c) => c.required && !skipped.has(c.key) && c.seen).length;
      const reqTot = items.filter((c) => c.required && !skipped.has(c.key)).length;
      body.append(el("h4", "tcp-pkt-h", `${heading}${reqTot ? ` — ${reqSeen}/${reqTot} required` : ""}`));
      const tbl = el("table", "cov-table");
      const thead = el("thead"); const htr = el("tr");
      ["", "Item", "Req", "Count", "Received", "Skip"].forEach((h) => htr.append(el("th", "", h)));
      thead.append(htr); tbl.append(thead);
      const tb = el("tbody");
      items.forEach((c) => {
        const isSkip = skipped.has(c.key);
        const state = isSkip ? "skip" : (c.valid === false ? "bad" : c.seen ? "seen" : "pending");
        const tr = el("tr", "cov-row cov-" + state);
        // (#3) alert rows show the id + on-wire code: "Connect back to main battery (6 - BR)";
        // emergency rows show their EPB code: "Emergency ON (EMR)" / "Emergency OFF (SEM)"
        const label = (c.group === "alert" && c.alert_id != null)
          ? `${c.label} (${c.alert_id}${c.code ? " - " + c.code : ""})`
          : (c.group === "emergency" && c.code) ? `${c.label} (${c.code})` : c.label;
        const dot = el("td"); dot.append(el("span", "cov-dot cov-" + state)); tr.append(dot);
        const labelCell = el("td", "cov-label", label);
        // (#1)(#2) hover the ITEM NAME specifically (not anywhere in the row) and wait ~2s
        // (like a native tooltip) before showing the description + reference packet with
        // THIS row's own tokens highlighted. No "Received" section — that's already visible
        // in the Received column itself, so repeating it here was redundant.
        labelCell.addEventListener("mouseenter", () => scheduleCovTip(labelCell, proto, c));
        labelCell.addEventListener("mouseleave", hideCovTip);
        tr.append(labelCell);
        tr.append(el("td", "cov-req", c.required ? "Yes" : "—"));
        tr.append(el("td", "num", c.count ? String(c.count) : ""));
        // (#2) received packet — an inline preview of the actual captured sample (not just
        // a "view" button), so the operator sees at a glance what came in; click for the
        // full packet (parsed/ASCII/HEX). (#4) coverage() always hands back the MOST RECENT
        // matching record, so this preview is always the latest one seen for this item.
        const rc = el("td", "cov-recv");
        if (c.last_raw_hex) {
          const preview = el("span", "cov-recv-preview mono",
            rawView(c.last_raw_hex, fmt).replace(/\s+/g, " ").trim());
          preview.title = "click to see the full packet";
          preview.onclick = (e) => {
            e.stopPropagation();
            openPacket({ raw_hex: c.last_raw_hex, message_type: c.label, device_id: did, protocol: proto });
          };
          rc.append(preview);
        } else { rc.append(el("span", "muted", "—")); }
        tr.append(rc);
        // (#5) Skip only matters for an item that's still missing AND that the profile
        // marks skippable — the mandatory items can't be waived, and a received item has
        // nothing to mark optional. Otherwise the checkbox is just clutter; hide it.
        // (skippable === undefined means an old backend / pre-update snapshot that doesn't
        // carry the flag yet — fall back to showing the control; the server still enforces.)
        const skc = el("td");
        if (!c.seen && (c.skippable !== undefined ? c.skippable : true)) {
          const cb = el("input"); cb.type = "checkbox"; cb.checked = isSkip;
          cb.title = "mark optional for this device (missing -> Conditional Pass, not fail)";
          cb.onclick = (e) => e.stopPropagation();
          cb.onchange = () => {
            const next = new Set(skipped);
            cb.checked ? next.add(c.key) : next.delete(c.key);
            setSkipped(sess.id, did, [...next]);
          };
          skc.append(cb);
        }
        tr.append(skc);
        tb.append(tr);
      });
      tbl.append(tb); body.append(tbl);
    };
    covTable(cov.filter((c) => c.group !== "alert" && c.group !== "emergency"), "Packets");
    // EPB traffic goes to the EMERGENCY server (separate from the primary/alert stream),
    // so it gets its own section: Emergency ON = EMR, Emergency OFF = SEM.
    covTable(cov.filter((c) => c.group === "emergency"), "Emergency server");
    covTable(cov.filter((c) => c.group === "alert"), "Alerts");

    // --- Deviations (skipped now; invalid packets join here in Step 2) ---
    const devs = cov.filter((c) => skipped.has(c.key) || c.valid === false);
    if (devs.length) {
      body.append(el("h4", "tcp-pkt-h", `Deviations — ${devs.length} (conditional)`));
      const drow = el("div", "qc-checks");
      devs.forEach((c) => {
        const bad = c.valid === false;
        drow.append(el("span", "qc-chip " + (bad ? "qc-chip-fail" : "qc-chip-na"),
          c.label + (bad ? " — invalid" : " — skipped")));
      });
      body.append(drow);
    }
    // ("Test cases" chips removed — the field checks still run backend-side and appear in
    // the report; the Summary page is coverage-focused.)
  }

  // ---- Testing: log export + continuous logging ------------------------------
  function openLog() {
    const jid = focusJobId();
    const total = logBuffer.length;
    const sel = logBuffer.filter((r) => r.peer && peerJob.get(r.peer) === jid && !isHiddenTesting(r)).length;
    $("cap-log-msg").textContent = `${total} records buffered · ${sel} from selected devices on this session`;
    $("cap-log-modal").style.display = "flex";
  }
  const closeLog = () => { $("cap-log-modal").style.display = "none"; };
  function downloadLog() {
    const jid = focusJobId();
    const fmt = $("cap-log-format").value, scope = $("cap-log-scope").value, rawAs = $("cap-log-raw").value;
    const rows = logBuffer.filter((r) => scope === "all"
      || (r.peer && peerJob.get(r.peer) === jid && !isHiddenTesting(r)));
    const oneLineAscii = (h) => hexToAscii(h).replace(/\n/g, " ");
    const rawOf = (r) => (rawAs === "hex" ? hexSpaced(r.raw_hex || "") : oneLineAscii(r.raw_hex || ""));
    const tsOf = (r) => fmtLogTs(r.event_time || r.received_at);
    const clientOf = (r) => r.peer || r.device_id || "";
    let content, mime, ext;
    if (fmt === "csv") {
      const esc = (s) => `"${String(s).replace(/"/g, '""')}"`;
      content = "timestamp,client,raw\n"
        + rows.map((r) => [esc(tsOf(r)), esc(clientOf(r)), esc(rawOf(r))].join(",")).join("\n") + "\n";
      mime = "text/csv"; ext = "csv";
    } else {
      content = rows.map((r) => `${tsOf(r)} | ${clientOf(r)} | ${rawOf(r)}`).join("\n") + "\n";
      mime = "text/plain"; ext = "txt";
    }
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([content], { type: mime }));
    a.download = `runway-capture-log-${stamp}.${ext}`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
    $("cap-log-msg").textContent = `Saved ${rows.length} records`;
  }
  $("cap-log").addEventListener("click", openLog);
  $("cap-log-cancel").addEventListener("click", closeLog);
  $("cap-log-close").addEventListener("click", closeLog);
  $("cap-log-go").addEventListener("click", downloadLog);
  $("cap-log-modal").addEventListener("click", (e) => { if (e.target.id === "cap-log-modal") closeLog(); });

  let logRecActive = false, logDefaultDir = "";
  async function refreshLogStatus() {
    try {
      const s = await api("/api/log/status");
      logRecActive = s.active; logDefaultDir = s.default_dir || "";
      const b = $("cap-rec");
      UI.setLabel(b, s.active ? "stop" : "record", s.active ? `Stop log (${s.count})` : "Start log");
      b.classList.toggle("danger", s.active);
      $("cap-recfile").style.display = s.path ? "" : "none";
      $("cap-recfile").title = s.path ? "Download " + s.path : "";
    } catch (e) { /* ignore */ }
  }
  $("cap-rec").addEventListener("click", () => {
    if (logRecActive) { stopRec(); return; }
    $("cap-rec-msg").textContent = "";
    $("cap-rec-dir").value = logDefaultDir;
    $("cap-rec-name").value = "";
    $("cap-rec-modal").style.display = "flex";
  });
  const closeRec = () => { $("cap-rec-modal").style.display = "none"; };
  $("cap-rec-close").addEventListener("click", closeRec);
  $("cap-rec-cancel").addEventListener("click", closeRec);
  $("cap-rec-modal").addEventListener("click", (e) => { if (e.target.id === "cap-rec-modal") closeRec(); });
  $("cap-rec-go").addEventListener("click", async () => {
    const body = {
      fmt: $("cap-rec-format").value, raw: $("cap-rec-raw").value,
      name: $("cap-rec-name").value.trim(), directory: $("cap-rec-dir").value.trim(),
    };
    try {
      const s = await postJSON("/api/log/start", body);
      closeRec(); msg(null, "ok", "logging to " + (s.path || "file")); refreshLogStatus();
    } catch (e) { $("cap-rec-msg").textContent = "start failed: " + e.message; }
  });
  async function stopRec() {
    try { const s = await api("/api/log/stop", { method: "POST" }); msg(null, "ok", `logging stopped (${s.count} packets)`); }
    catch (e) { msg(null, "err", "stop failed: " + e.message); }
    refreshLogStatus();
  }
  $("cap-recfile").addEventListener("click", () => {
    const a = el("a"); a.href = "/api/log/file"; document.body.appendChild(a); a.click(); a.remove();
  });

  // ---- send-command modal (downlink: utility -> device) -----------------------
  let sendCmds = [];                 // the picked protocol's ota_commands (current modal)
  // port: this session's listening port — substituted into IP,port SET drafts (see apply()).
  // recipients: every connected device on this port [{label, peer}] — when there's more
  // than one, the modal shows a tick-list so one command can go to several devices.
  function openSend(job, peer, proto, port, recipients) {
    sendCtx = { job, peer, port: port || null, sel: new Set([peer]),
                multi: !!(recipients && recipients.length > 1) };
    // headline is the DEVICE (IMEI) once identified — the transient ip:port stays as a
    // hover tooltip only (a reconnect changes the port, never the IMEI)
    const label = deviceLabel(peer);
    $("cap-send-peer").textContent = label;
    $("cap-send-peer").title = peer;
    const rec = $("cap-send-recips");
    rec.innerHTML = "";
    rec.style.display = sendCtx.multi ? "" : "none";
    if (sendCtx.multi) {
      recipients.forEach((r) => {
        const lab = el("label", "cap-send-recip");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = r.peer === peer;
        cb.onchange = () => {
          cb.checked ? sendCtx.sel.add(r.peer) : sendCtx.sel.delete(r.peer);
          $("cap-send-peer").textContent = sendCtx.sel.size === 1
            ? deviceLabel([...sendCtx.sel][0]) : `${sendCtx.sel.size} devices`;
        };
        lab.append(cb, el("span", "mono", r.label), el("span", "muted tcp-device-peer", r.peer));
        rec.append(lab);
      });
    }
    $("cap-send-value").value = "";
    msg("cap-send-msg", "", "");
    populateSendCommands(proto || "");
    $("cap-send-modal").style.display = "flex";
    $("cap-send-value").focus();
  }
  // Command catalogue from the protocol definition (data/protocols/<proto>.json →
  // ota_commands): pick the command TYPE first (SET/GET/CLR), then a command from the
  // list FILTERED to those supporting that type (some support all three, some only a
  // subset — e.g. STOP is SET-only). The chosen string prefills the editable value; the
  // SET strings carry example values the operator edits before sending. With a catalogue
  // command chosen, Mode hides — the format is already defined for this device type.
  const SEND_ACTIONS = ["set", "get", "clr"];
  async function populateSendCommands(proto) {
    const fld = $("cap-send-cmd-fld"), actFld = $("cap-send-act-fld"), modeFld = $("cap-send-mode-fld");
    const hint = $("cap-send-cmd-hint");
    const sel = $("cap-send-cmd"), act = $("cap-send-act");
    sendCmds = [];
    fld.style.display = actFld.style.display = hint.style.display = "none";
    modeFld.style.display = "";                         // no catalogue -> plain text/hex modal
    sel.innerHTML = ""; act.innerHTML = "";
    if (!proto) return;
    if (!(proto in qcRefCache)) {
      try {
        const def = await api(`/api/protocols/${encodeURIComponent(proto)}.json`);
        qcRefCache[proto] = def.content || {};
      } catch (e) { qcRefCache[proto] = {}; }
    }
    sendCmds = ((qcRefCache[proto].ota_commands || {}).commands) || [];
    // normalize the older {command, function, example} shape into a SET-only entry so one
    // rendering path handles both catalogue formats
    sendCmds = sendCmds.map((c) => {
      if (c.set || c.get || c.clr) return c;
      const m = (c.command || "").match(/:([A-Z0-9]+)#/);
      return { name: c.name || (m && m[1]) || `cmd ${c.id}`,
               description: c.description || c.function || "", set: c.example || c.command || "" };
    });
    if (!sendCmds.length) return;

    // command types actually present in the catalogue (usually SET/GET/CLR)
    const types = SEND_ACTIONS.filter((a) => sendCmds.some((c) => c[a]));
    act.innerHTML = "";
    types.forEach((a) => act.appendChild(new Option(a.toUpperCase(), a)));
    actFld.style.display = "";
    fld.style.display = "";

    const rebuildCommands = () => {
      const type = act.value;
      const prev = sel.value;
      sel.innerHTML = "";
      sel.appendChild(new Option("— custom (type below) —", ""));
      sendCmds.forEach((c, i) => {
        if (c[type]) sel.appendChild(new Option(`${c.name} — ${c.description}`, String(i)));
      });
      // keep the same command selected across a type switch when it supports the new type
      if (prev && sendCmds[Number(prev)] && sendCmds[Number(prev)][type]) sel.value = prev;
    };
    const apply = () => {
      // NB: the custom option's value is "" and Number("") === 0 — an index-0 command
      // would swallow it, so resolve the empty value explicitly.
      const c = sel.value === "" ? null : sendCmds[Number(sel.value)];
      if (!c) {                                          // custom: free text / raw hex
        hint.style.display = "none";
        modeFld.style.display = "";
        return;
      }
      let v = c[act.value] || "";
      // IP,port setters (PIP/EIP/GIP): the host in the catalogue is the bench's constant
      // server address; the PORT becomes THIS session's listening port — the one the
      // device must dial back to (e.g. "+S*R:SET:EIP#182.71.127.231,4082;" on port 4082).
      if (act.value === "set" && sendCtx && sendCtx.port) {
        v = v.replace(/#([^,;#]+),(\d{1,5});/, `#$1,${sendCtx.port};`);
      }
      $("cap-send-value").value = v;
      $("cap-send-mode").value = "text";                 // catalogue commands are ASCII text
      modeFld.style.display = "none";                    // format is device-defined — no Mode
      hint.textContent = c.description || "";
      hint.style.display = "";
      $("cap-send-value").focus();
    };
    act.onchange = () => { rebuildCommands(); apply(); };
    sel.onchange = apply;
    rebuildCommands();
  }
  const closeSend = () => { $("cap-send-modal").style.display = "none"; sendCtx = null; };
  $("cap-send-cancel").addEventListener("click", closeSend);
  $("cap-send-go").addEventListener("click", async () => {
    if (!sendCtx) return;
    const value = $("cap-send-value").value;
    if (!value.trim()) { msg("cap-send-msg", "err", "enter a value"); return; }
    const peers = sendCtx.multi ? [...sendCtx.sel] : [sendCtx.peer];
    if (!peers.length) { msg("cap-send-msg", "err", "tick at least one device"); return; }
    const mode = $("cap-send-mode").value;
    let ok = 0, last = null;
    const errs = [];
    for (const p of peers) {
      try { last = await postJSON("/api/send/" + sendCtx.job, { peer: p, mode, value }); ok += 1; }
      catch (e) { errs.push(`${deviceLabel(p)}: ${e.message}`); }
    }
    if (errs.length) msg("cap-send-msg", "err", `sent to ${ok}/${peers.length} — failed: ${errs.join("; ")}`);
    else if (peers.length === 1) msg("cap-send-msg", "ok", `sent ${last.sent} bytes (${last.hex.slice(0, 24)}${last.hex.length > 24 ? "…" : ""})`);
    else msg("cap-send-msg", "ok", `sent to all ${peers.length} devices`);
  });

  // ---- render + refresh + WebSocket ------------------------------------------
  function renderPane() {
    const draft = focus === NEW;
    $("cap-config").style.display = draft ? "" : "none";
    $("cap-session").style.display = draft ? "none" : "";
    $("cap-devices").style.display = draft ? "none" : "";
    if (!draft) { renderSession(); renderDevices(); }
  }
  // Raw | Summary — QC-only, persisted globally like the other view toggles.
  function setQcView(v) {
    qcView = v;
    try { localStorage.setItem(QCVIEW_KEY, v); } catch (e) { /* ignore */ }
    $("cap-qc-view-raw").classList.toggle("on", v === "raw");
    $("cap-qc-view-summary").classList.toggle("on", v === "summary");
    renderAll();
  }
  $("cap-qc-view-raw").addEventListener("click", () => setQcView("raw"));
  $("cap-qc-view-summary").addEventListener("click", () => setQcView("summary"));

  function applyModeChrome() {
    const t = focus === NEW ? null : focusedTab();
    const isQc = !!(t && t.mode === "qc");
    const showSummary = isQc && qcView === "summary";
    // QC renders per the protocol config — hide the RAW/Parsed + HEX/ASCII toggles.
    $("cap-view-seg").style.display = isQc ? "none" : "";
    $("cap-rawfmt-seg").style.display = (!isQc && viewMode === "raw") ? "" : "none";
    $("cap-qc-viewseg").style.display = isQc ? "" : "none";
    $("cap-qc-actions").style.display = isQc ? "" : "none";
    $("cap-test-actions").style.display = (t && !isQc) ? "" : "none";
    $("cap-feed-count").style.display = showSummary ? "none" : "";
    $("cap-feed").style.display = showSummary ? "none" : "";
    $("cap-qc-summary-panel").style.display = showSummary ? "" : "none";
    renderQcActions(t);
    renderQcStatusPill(isQc ? t.sess : null);
    if (showSummary) renderQcSummaryPanel(t.sess);   // re-renders live on every poll/WS update
  }
  // QC-only status pill in the feed header: verdict + required-coverage fraction for the
  // SELECTED device. Always visible in both Raw and Summary views; clicking it is a
  // shortcut into the Summary view.
  function renderQcStatusPill(sess) {
    const btn = $("cap-qc-status");
    if (!sess) { btn.style.display = "none"; return; }
    btn.style.display = "";
    const dev = qcSelectedDevice(sess);          // the focused device (or first), else session
    const cov = (dev && dev.coverage) || sess.coverage || [];
    const verdict = dev ? dev.verdict : sess.verdict;
    const skipped = new Set((dev && dev.skipped) || []);
    let label;
    if (cov.length) {
      const req = cov.filter((c) => c.required && !skipped.has(c.key));
      label = `${verdictLabel(verdict) || "—"} · ${req.filter((c) => c.seen).length}/${req.length}`;
    } else {
      const checks = (dev && dev.checks) || sess.checks || [];
      label = `Tests ${checks.filter((c) => c.result === "pass").length}/${checks.length}`;
    }
    btn.textContent = label;
    btn.className = "qc-summary" + (verdict ? " qc-summary-" + verdict : "");
    btn.onclick = () => setQcView("summary");
  }
  function renderAll() { renderTabs(); renderPane(); renderFeed(); applyModeChrome(); renderExportDevices(); }

  async function refreshAll() {
    try {
      const qlist = await api("/api/qc");
      qlist.forEach((s) => { if (!closedQc.has(s.id)) sessions.set(s.id, s); });
      const qknown = new Set(qlist.map((s) => s.id));
      [...sessions.keys()].forEach((id) => { if (!qknown.has(id)) sessions.delete(id); });

      const jlist = await api("/api/jobs");
      jlist.forEach((j) => { if (!closedJids.has(j.id)) jobs.set(j.id, j); mapJobPeers(j); });
      const jknown = new Set(jlist.map((j) => j.id));
      [...jobs.keys()].forEach((id) => { if (!jknown.has(id)) jobs.delete(id); });
      renderAll();
    } catch (e) { /* ignore */ }
    refreshLogStatus();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    sock.onclose = () => setTimeout(connect, 2000);
    sock.onerror = () => sock.close();
    sock.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "snapshot") {
        feed.length = 0; logBuffer.length = 0;
        const recent = m.data.recent || [];
        recent.forEach((r) => { learnDevice(r); logBuffer.push(r); });
        recent.slice(-MAX_FEED).reverse().forEach((r) => feed.push(r));
        renderFeed();
      } else if (m.type === "record") {
        ingest(m.data);
      } else if (m.type === "qc") {
        if (closedQc.has(m.data.id)) return;
        sessions.set(m.data.id, m.data);
        renderAll();
      } else if (m.type === "job") {
        if (closedJids.has(m.data.id)) return;
        const j = { ...jobs.get(m.data.id), ...m.data };
        jobs.set(j.id, j); mapJobPeers(j);
        renderAll();
      } else if (m.type === "conn") {
        const j = jobs.get(m.data.job);
        if (j) {
          j.peers = m.data.peers;
          const open = new Set(j.peers);
          (j.clients || []).forEach((c) => { c.connected = open.has(c.peer); });
          mapJobPeers(j); renderAll();
        }
      }
    };
  }

  setViewMode(viewMode);
  setRawFmt(rawFmt);
  setQcView(qcView);
  setDraftMode(draftMode);
  loadProtocols();
  loadDefaults();
  // Populate sessions + jobs BEFORE opening the WebSocket so the first snapshot's records
  // have a peer->session mapping to route against.
  refreshAll().finally(() => {
    if (!tabList().length && !draftOpen) { draftOpen = true; focus = NEW; }
    renderAll();
    setInterval(refreshAll, 2500);
    connect();
  });
})();
