/* Runway - Capture: ONE page, browser-style tabs, per-tab mode.
 *   • Testing tab — raw packet inspection on any protocol (backend: /api/listen jobs).
 *   • QC tab      — validated qualification per the config profile (backend: /api/qc/*).
 * Both share the same core: tab strip, device list, live feed (RAW/Parsed + colour code),
 * packet/send modals. QC tabs add live checks + verdict + report + capture + device info;
 * Testing tabs add log download / continuous logging. Live updates over /ws. */
(() => {
  "use strict";

  const { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull,
          fmtDate, TZ_LABEL, packetKind, fmtIdle } = window.Runway;

  // Status messages: write to a visible modal span if given one, else a bottom-right toast.
  const toast = el("div", "toast");
  document.body.appendChild(toast);
  let toastTimer = null;
  const msg = (id, cls, txt) => {
    const m = id && $(id);
    if (m && m.offsetParent !== null) { m.className = cls; m.textContent = txt; return; }
    toast.className = "toast" + (cls ? " " + cls : "") + (txt ? " show" : "");
    toast.textContent = txt;
    clearTimeout(toastTimer);
    if (txt) toastTimer = setTimeout(() => toast.classList.remove("show"), 4000);
  };
  const postJSON = (path, body) => api(path, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtBytes = (n) => (!n ? "0 B" : n < 1024 ? n + " B" : n < 1048576
    ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");

  // Placeholder ids codecs emit before a real identity (IMEI) is learned (kept in sync with
  // qc/session.py's set) — never shown as a device name.
  const UNKNOWN_IDS = new Set(["", "unknown", "teltonika-unknown", "gt06-unknown", "nmea-device", "device-1", "ais140-unknown"]);

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
  let qcView = "raw";              // QC feed pane: "raw" (packet stream) | "summary" (per-device)
  let devicesCollapsed = false;
  let focus = null;                // "t:<jid>" | "q:<qcId>" | NEW | null
  const hiddenPeers = new Set();
  try {
    rawFmt = localStorage.getItem(RAWFMT_KEY) || "hex";
    viewMode = localStorage.getItem(VIEW_KEY) || "raw";
    qcView = localStorage.getItem(QCVIEW_KEY) || "raw";
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
  // QC renders per the protocol (no user toggle): text protocols show ASCII, binary show HEX.
  // (Interim heuristic by protocol name; moves to the QC config profile in a later phase.)
  const ASCII_PROTOCOLS = new Set(["ais140", "nmea"]);
  const qcRawFmt = (proto) => (ASCII_PROTOCOLS.has(proto) ? "ascii" : "hex");

  function mapJobPeers(j) {
    (j.peers || []).forEach((p) => peerJob.set(p, j.id));
    (j.clients || []).forEach((c) => peerJob.set(c.peer, j.id));
  }
  function learnDevice(r) {
    if (!r.peer) return;
    const info = peerInfo.get(r.peer) || { device_id: "", protocol: "" };
    if (r.device_id && !UNKNOWN_IDS.has(r.device_id)) info.device_id = r.device_id;
    if (r.protocol && (r.protocol !== "generic_hex" || !info.protocol)) info.protocol = r.protocol;
    peerInfo.set(r.peer, info);
  }
  const deviceLabel = (peer) => (peerInfo.get(peer) || {}).device_id || peer || "";
  const protoLabel = (s) => s.identified_as || (s.protocol_mode === "manual" ? s.protocol
    : (s.status === "running" ? "identifying…" : "unidentified"));
  // Human label for a QC verdict/state (used on the tab, summary pill, feed badge).
  const VERDICT_LABELS = { in_progress: "IN PROGRESS", timeout: "TIMEOUT", pass: "PASS",
    conditional_pass: "CONDITIONAL", fail: "FAIL" };
  const verdictLabel = (v) => VERDICT_LABELS[v] || (v ? v.toUpperCase() : "");
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
        const r = await postJSON("/api/listen", { codec: $("cap-codec").value, host, port });
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
      renderAll(); msg(null, "ok", "resumed");
    } catch (e) { msg(null, "err", "resume failed: " + e.message); }
  }
  async function closeTab(t) {
    const isQc = t.mode === "qc";
    const label = isQc ? `#${t.id} (${t.sess.device_id || "port " + t.sess.port})`
                       : `:${t.job.port} (${t.job.codec})`;
    const note = isQc ? "\n\nThis only removes it from the list — the capture file on disk is kept." : "";
    if (!window.confirm((tabRunning(t) ? `Stop ${label} and close its tab?` : `Close tab ${label}?`) + note)) return;
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
    } catch (e) { msg(null, "err", "close failed: " + e.message); return; }
    if (isQc) { closedQc.add(t.id); sessions.delete(t.id); }
    else { closedJids.add(t.id); jobs.delete(t.id); }
    if (focus === t.key) focus = null;
    msg(null, "ok", `closed ${label}`);
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
      const conn = ((jobs.get(tabJobId(t)) || {}).clients || []).filter((c) => c.connected).length;
      const live = isQc ? !!(t.sess.connected || conn) : running;
      const tab = el("div", "tcp-tab" + (running ? " on" : " off") + (t.key === focus ? " active" : ""));
      tab.onclick = () => setFocus(t.key);
      tab.append(el("span", "tcp-dot " + (live ? "live" : "dead")));
      tab.append(el("span", "cap-tab-mode " + (isQc ? "is-qc" : "is-test"), isQc ? "QC" : "TEST"));
      tab.append(el("span", "tcp-tab-port", String(isQc ? t.sess.port : t.job.port)));
      // Tab subtitle = the protocol only. The IMEI does NOT belong here: a QC port can
      // host many devices (which one would the tab name?), and the per-device identity
      // already lives in the device list + Summary panel.
      tab.append(el("span", "tcp-tab-sub muted", isQc ? protoLabel(t.sess) : t.job.codec));
      if (isQc && t.sess.verdict) tab.append(el("span", "qc-verdict qc-verdict-" + t.sess.verdict, verdictLabel(t.sess.verdict)));
      else if (conn) tab.append(el("span", "tcp-tab-count", String(conn)));
      const x = el("button", "tcp-tab-x", "✕");
      x.title = running ? "stop this session and close the tab" : "close this tab";
      x.onclick = (e) => { e.stopPropagation(); closeTab(t); };
      tab.append(x);
      host.appendChild(tab);
    });
    if (draftOpen) {
      const tab = el("div", "tcp-tab tcp-tab-draft" + (focus === NEW ? " active" : ""));
      tab.onclick = () => setFocus(NEW);
      tab.append(el("span", "tcp-dot"));
      tab.append(el("span", "tcp-tab-port", "New"));
      const x = el("button", "tcp-tab-x", "✕");
      x.title = "discard this new session";
      x.onclick = (e) => { e.stopPropagation(); closeDraft(); };
      tab.append(x);
      host.appendChild(tab);
    }
    const add = el("button", "tcp-tab-add", "+");
    add.title = "start a new session";
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
    portEl.title = "point devices to this address";
    head.append(portEl);
    head.append(el("span", "tcp-codec-tag", isQc ? protoLabel(t.sess) : t.job.codec));
    const status = isQc ? t.sess.status : t.job.status;
    head.append(el("span", "tcp-status " + status, status));
    head.append(el("span", "spacer"));
    if (running) {
      const stop = el("button", "ghost mini danger", "■ Stop");
      stop.onclick = () => stopTab(t);
      head.append(stop);
    } else {
      const res = el("button", "ghost mini", "▶ Resume");
      res.title = "resume this session on the same port (does not create a new one)";
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
    meta.append(el("span", "tcp-up", `▲ ${upSecs == null ? "—" : fmtDur(upSecs)}`));
    if (isQc) {
      meta.append(el("span", "", `${t.sess.packet_count || 0} packets`));
      if (running) {   // live idle timer — a QC session times out after ~10 min of silence
        const idleSpan = el("span", "cap-idle", "idle —");
        idleSpan.dataset.last = lastRecvMsForJob(tabJobId(t)) || "";
        idleSpan.title = "time since the last packet (TIMEOUT after 10 min idle)";
        meta.append(idleSpan);
      }
      if (t.sess.operator) meta.append(el("span", "", `operator: ${t.sess.operator}`));
      if ((t.sess.prior_sessions || []).length) {
        const seen = el("span", "", `🔁 seen before ×${t.sess.prior_sessions.length}`);
        seen.title = "this device id has other QC sessions in the history";
        meta.append(seen);
      }
    } else {
      meta.append(el("span", "", `${t.job.records || 0} records`));
      meta.append(el("span", "", fmtBytes(t.job.bytes)));
    }
    const err = isQc ? t.sess.error : t.job.error;
    if (err) meta.append(el("span", "err", err));
    host.append(meta);
  }

  function renderDevices() {
    const host = $("cap-devices");
    host.innerHTML = "";
    const t = focusedTab();
    if (!t) { host.append(el("p", "empty", "Start a session to see devices here.")); return; }
    const isQc = t.mode === "qc";
    const running = tabRunning(t);
    const j = jobs.get(tabJobId(t));
    const clients = (j && j.clients) || [];
    const count = isQc ? (qcDevices(t.sess).length || clients.length) : clients.length;

    const headEl = el("div", "tcp-peers-head");
    const caret = el("button", "tcp-collapse", devicesCollapsed ? "▸" : "▾");
    caret.title = devicesCollapsed ? "expand the device list" : "collapse the device list";
    const toggle = () => {
      devicesCollapsed = !devicesCollapsed;
      try { localStorage.setItem(DEVCOLL_KEY, devicesCollapsed ? "1" : "0"); } catch (e) { /* ignore */ }
      renderDevices();
    };
    caret.onclick = toggle;
    headEl.append(caret);
    const title = el("span", "tcp-peers-title", `Devices on this port (${count})`);
    title.style.cursor = "pointer"; title.title = caret.title; title.onclick = toggle;
    headEl.append(title);
    headEl.append(el("span", "spacer"));
    if (count && !devicesCollapsed) {   // bulk show/hide in the Raw feed — all/partial/none
      const all = el("button", "ghost mini", "All");
      all.title = "show all devices' packets in Raw";
      all.onclick = () => {
        if (isQc) { setHiddenQc(t.sess.id, new Set()); }
        else { clients.forEach((c) => hiddenPeers.delete(c.peer)); saveHidden(); }
        renderDevices(); renderFeed();
      };
      const none = el("button", "ghost mini", "None");
      none.title = "hide all devices' packets in Raw";
      none.onclick = () => {
        if (isQc) { setHiddenQc(t.sess.id, new Set(qcDevices(t.sess).map((d) => d.device_id))); }
        else { clients.forEach((c) => hiddenPeers.add(c.peer)); saveHidden(); }
        renderDevices(); renderFeed();
      };
      headEl.append(all, none);
    }
    host.append(headEl);
    if (devicesCollapsed) return;

    if (isQc) { renderQcDeviceRows(t, host, clients, running); return; }

    if (!clients.length) {
      host.append(el("p", "empty", running ? "Waiting for devices to dial in…" : "No devices connected."));
      return;
    }
    clients.forEach((c) => {
      const p = c.peer;
      const info = peerInfo.get(p) || {};
      const row = el("div", "tcp-peer tcp-client-row");
      row.dataset.peer = p; row.dataset.last = c.last || 0;
      const cb = el("input"); cb.type = "checkbox"; cb.checked = !hiddenPeers.has(p);
      cb.title = "show this device's packets in the feed";
      cb.onchange = () => { cb.checked ? hiddenPeers.delete(p) : hiddenPeers.add(p); saveHidden(); renderFeed(); };
      row.append(cb);
      row.append(el("span", "tcp-dot " + (c.connected ? "live" : "dead")));
      const name = el("div", "tcp-device-id");
      name.append(el("span", "mono tcp-client-addr" + (c.connected ? "" : " muted"), info.device_id || p));
      if (info.device_id) name.append(el("span", "muted tcp-device-peer", p));
      row.append(name);
      const meta = el("div", "tcp-peer-meta");
      if (info.protocol) meta.append(el("span", "tcp-codec-tag", info.protocol));
      meta.append(el("span", "tcp-client-count", String(c.records || 0)));
      meta.append(el("span", "tcp-client-idle muted", fmtIdle(c.last || 0)));
      const jid = tabJobId(t);
      if (running && c.connected && jid) {
        const send = el("button", "ghost mini", "✉ Send");
        send.onclick = () => openSend(jid, p);
        meta.append(send);
      }
      row.append(meta);
      host.append(row);
    });
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
      cb.title = "show this device's packets in the Raw feed";
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
        const send = el("button", "ghost mini", "✉ Send");
        send.onclick = (e) => { e.stopPropagation(); openSend(jid, cp); };
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
      if (st && up) up.textContent = "▲ " + fmtDur(Date.now() / 1000 - st);
      const idleEl = m.querySelector(".cap-idle");
      if (idleEl) {
        const last = Number(idleEl.dataset.last);
        idleEl.textContent = last ? "idle " + fmtIdleLong(Date.now() / 1000 - last / 1000) : "idle —";
        idleEl.classList.toggle("stale", last && (Date.now() - last) >= 600000);   // >10 min
      }
    }
    document.querySelectorAll("#cap-devices .tcp-client-row").forEach((row) => {
      const i = row.querySelector(".tcp-client-idle");
      if (i) i.textContent = fmtIdle(Number(row.dataset.last) || 0);
    });
  }, 1000);

  // ---- QC extras: report/capture actions (checks + verdict now live in the pill/modal) ----
  function renderQcActions(t) {
    const host = $("cap-qc-actions");
    host.innerHTML = "";
    if (!t || t.mode !== "qc") return;
    if (t.sess.checks && t.sess.checks.length) {
      const rep = el("button", "ghost mini", "📄 Report");
      rep.onclick = () => openReport(t.id);
      host.append(rep);
    }
    if (t.sess.capture_path) {
      const dl = el("button", "ghost mini", "⤓ Capture");
      dl.title = "download this session's capture file";
      dl.onclick = () => window.open(`/api/qc/${t.id}/capture`, "_blank");
      host.append(dl);
    }
  }

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
    return rows.filter((r) => !hiddenPeers.has(r.peer));   // Testing: per-peer show/hide
  }
  function renderFeed() {
    const host = $("cap-feed");
    const t = focusedTab();
    const port = t ? (t.mode === "qc" ? t.sess.port : t.job.port) : null;
    $("cap-feed-for").textContent = t ? (t.mode === "qc" ? `— #${t.id} :${port}` : `— :${port}`) : "";
    const rows = focusRows();
    $("cap-feed-count").textContent = rows.length ? `${rows.length} shown` : "";
    if (!rows.length) {
      host.innerHTML = `<p class="empty">${t
        ? "No packets yet — waiting for the device to send data."
        : "Start a session to see packets here."}</p>`;
      return;
    }
    // QC forces raw view in the protocol's format; Testing honours the user's toggles.
    const isQc = t && t.mode === "qc";
    const view = isQc ? "raw" : viewMode;
    const fmt = isQc ? qcRawFmt(t.sess.identified_as || "") : rawFmt;
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
    const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
    if (isRaw) {
      $("cap-pkt-parsed").textContent = "(raw packet — no protocol matched, nothing parsed)";
    } else {
      const parsed = { ...r }; delete parsed.raw_hex; delete parsed.seq;
      $("cap-pkt-parsed").textContent = JSON.stringify(parsed, null, 2);
    }
    $("cap-pkt-ascii").textContent = hex ? asciiFull(hex) : "(no raw bytes on this record)";
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

  async function setSkipped(qcId, deviceId, keys) {
    try {
      const s = await postJSON(`/api/qc/${qcId}/skip`, { device_id: deviceId, keys });
      sessions.set(qcId, s); renderAll();
    } catch (e) { msg(null, "err", "skip failed: " + e.message); }
  }
  async function finalizeVerdict(qcId, deviceId, verdict) {
    try {
      const s = await postJSON(`/api/qc/${qcId}/finalize`, { device_id: deviceId, verdict });
      sessions.set(qcId, s); renderAll();
      msg(null, "ok", verdict ? `marked ${verdictLabel(verdict)}` : "verdict cleared");
    } catch (e) { msg(null, "err", "finalize failed: " + e.message); }
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
    const fam = item.group === "packet"
      ? (item.code === "LGN" ? pt.LGN : item.code === "HEL" ? (pt.HLP || pt.HLM)
        : item.code === "EPB" ? pt.EPB : (pt.PVT || pt.NMP))
      : (pt.PVT || pt.NMP);                       // alerts ride on the NMP/PVT sentence
    const desc = (item.group === "alert" && item.alert_id != null && alerts[item.alert_id])
      ? alerts[item.alert_id] : ((fam && fam.description) || item.label);
    if (!fam || !fam.example) return { desc, parts: [] };
    if (item.alert_id == null) return { desc, parts: [{ text: fam.example, hl: false }] };

    const fields = fam.fields || [];
    const ptypeField = fields.find((f) => f.name === "packet_type");
    const alertField = fields.find((f) => f.name === "alert_id");
    // tokens[0] is the "$NMP" header itself (the split includes it), so the JSON's 1-based
    // field index N lands at tokens[N], not tokens[N-1] — e.g. field 3 (packet_type) is
    // tokens[3]: $NMP(0), vendor_id(1), firmware_version(2), packet_type(3), alert_id(4)…
    const tokens = fam.example.split(",");              // safe: packet_type/alert_id sit
    const hl = new Set();                               // well before any embedded-comma field
    if (ptypeField && item.code) { tokens[ptypeField.index] = item.code; hl.add(ptypeField.index); }
    if (alertField) { tokens[alertField.index] = String(item.alert_id); hl.add(alertField.index); }
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
    const r = anchorEl.getBoundingClientRect();
    const tipRect = covTip.getBoundingClientRect();
    let left = Math.min(r.left, window.innerWidth - tipRect.width - 8);
    let top = r.bottom + 6;
    if (top + tipRect.height > window.innerHeight - 8) top = r.top - tipRect.height - 6;
    covTip.style.left = Math.max(8, left) + "px";
    covTip.style.top = Math.max(8, top) + "px";
  }
  function hideCovTip() { clearTimeout(covTipTimer); covTip.hidden = true; }

  function renderQcSummaryPanel(sess) {
    const proto = sess.identified_as || "";
    ensureQcRef(proto);
    const fmt = qcRawFmt(proto);
    const dev = qcSelectedDevice(sess);
    const cov = (dev && dev.coverage) || [];
    const skipped = new Set((dev && dev.skipped) || []);
    const verdict = dev ? dev.verdict : sess.verdict;
    const did = dev ? dev.device_id : "";

    const vb = $("cap-qcsum-verdict");
    vb.textContent = verdictLabel(verdict);
    vb.className = "qc-verdict" + (verdict ? " qc-verdict-" + verdict : "");
    $("cap-qcsum-nav").textContent =
      qcDevices(sess).length > 1 ? `device ${qcDevices(sess).findIndex((d) => d.device_id === did) + 1}/${qcDevices(sess).length}` : "";
    $("cap-qcsum-meta").textContent =
      `#${sess.id} · port ${sess.port} · ${proto || sess.protocol_mode}`
      + (sess.operator ? ` · operator: ${sess.operator}` : "");

    // --- (#4) Pass/Fail actions, top-right of the panel's own mini-header ---
    const acts = $("cap-qcsum-actions");
    acts.innerHTML = "";
    if (dev) {
      const passc = el("button", "ghost mini", "✓ Pass w/ conditions");
      passc.title = "accept the skipped/deviated items and pass this device";
      passc.onclick = () => finalizeVerdict(sess.id, did, "conditional_pass");
      const failb = el("button", "ghost mini danger", "✕ Fail");
      failb.onclick = () => finalizeVerdict(sess.id, did, "fail");
      acts.append(passc, failb);
      if (dev.finalized_verdict) {
        const clr = el("button", "ghost mini", "↺ Clear");
        clr.title = "hand back to the automatic verdict";
        clr.onclick = () => finalizeVerdict(sess.id, did, "");
        acts.append(clr);
      }
    }

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
        // (#3) alert rows show the id + on-wire code: "Connect back to main battery (6 - BR)"
        const label = (c.group === "alert" && c.alert_id != null)
          ? `${c.label} (${c.alert_id}${c.code ? " - " + c.code : ""})` : c.label;
        const dot = el("td"); dot.append(el("span", "cov-dot cov-" + state)); tr.append(dot);
        const labelCell = el("td", "cov-label", label);
        // (#1)(#2) hover the ITEM NAME specifically (not anywhere in the row) and wait ~2s
        // (like a native tooltip) before showing the description + reference packet with
        // THIS row's own tokens highlighted. No "Received" section — that's already visible
        // in the Received column itself, so repeating it here was redundant.
        labelCell.addEventListener("mouseenter", () => scheduleCovTip(labelCell, proto, c));
        labelCell.addEventListener("mouseleave", hideCovTip);
        tr.append(labelCell);
        tr.append(el("td", "cov-req", c.required ? "yes" : "—"));
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
        // (#5) Skip only matters for an item that's still missing — once it's been received
        // there's nothing to mark optional, so the checkbox just adds clutter; hide it.
        const skc = el("td");
        if (!c.seen) {
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
    covTable(cov.filter((c) => c.group !== "alert"), "Packets");
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

    // --- Test cases (the field checks for this device) ---
    const checks = dev.checks || [];
    body.append(el("h4", "tcp-pkt-h", "Test cases"));
    const crow = el("div", "qc-checks");
    checks.forEach((c) => { const chip = el("span", "qc-chip qc-chip-" + c.result, c.label); chip.title = c.detail || ""; crow.append(chip); });
    if (!checks.length) crow.append(el("p", "empty", "No checks evaluated yet."));
    body.append(crow);
  }

  // ---- QC report modal -------------------------------------------------------
  let reportQcId = null;
  async function openReport(qcId) {
    reportQcId = qcId;
    $("cap-report-verdict").textContent = "";
    $("cap-report-meta").textContent = "loading…";
    $("cap-report-checks").innerHTML = "";
    $("cap-report-modal").style.display = "flex";
    try {
      const r = await api(`/api/qc/${qcId}/report`);
      const v = $("cap-report-verdict");
      v.textContent = r.verdict ? r.verdict.toUpperCase() : "";
      v.className = "qc-verdict" + (r.verdict ? " qc-verdict-" + r.verdict : "");
      const started = r.started_at ? new Date(r.started_at * 1000).toLocaleString() : "—";
      const ended = r.ended_at ? new Date(r.ended_at * 1000).toLocaleString() : "—";
      $("cap-report-meta").textContent =
        `#${r.qc_id} · ${r.device_id || "(unidentified)"} · ${r.identified_as || r.protocol_mode}`
        + ` · port ${r.port} · ${r.packet_count} packets · ${started} → ${ended}`
        + (r.operator ? ` · operator: ${r.operator}` : "")
        + (r.prior_session_count ? ` · seen before ×${r.prior_session_count}` : "");
      const host = $("cap-report-checks");
      (r.checks || []).forEach((c) => {
        const chip = el("span", "qc-chip qc-chip-" + c.result, c.label);
        chip.title = c.detail || "";
        host.append(chip);
      });
      if (!(r.checks || []).length) host.append(el("p", "empty", "No checks evaluated yet."));
    } catch (e) {
      $("cap-report-meta").textContent = "failed to load: " + e.message;
    }
  }
  $("cap-report-close").addEventListener("click", () => { $("cap-report-modal").style.display = "none"; });
  $("cap-report-modal").addEventListener("click", (e) => {
    if (e.target.id === "cap-report-modal") $("cap-report-modal").style.display = "none";
  });
  $("cap-report-json").addEventListener("click", () => { if (reportQcId != null) window.open(`/api/qc/${reportQcId}/report`, "_blank"); });
  $("cap-report-txt").addEventListener("click", () => { if (reportQcId != null) window.open(`/api/qc/${reportQcId}/report?fmt=txt`, "_blank"); });

  // ---- Testing: log export + continuous logging ------------------------------
  function openLog() {
    const jid = focusJobId();
    const total = logBuffer.length;
    const sel = logBuffer.filter((r) => r.peer && peerJob.get(r.peer) === jid && !hiddenPeers.has(r.peer)).length;
    $("cap-log-msg").textContent = `${total} records buffered · ${sel} from selected devices on this session`;
    $("cap-log-modal").style.display = "flex";
  }
  const closeLog = () => { $("cap-log-modal").style.display = "none"; };
  function downloadLog() {
    const jid = focusJobId();
    const fmt = $("cap-log-format").value, scope = $("cap-log-scope").value, rawAs = $("cap-log-raw").value;
    const rows = logBuffer.filter((r) => scope === "all"
      || (r.peer && peerJob.get(r.peer) === jid && !hiddenPeers.has(r.peer)));
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
    $("cap-log-msg").textContent = `saved ${rows.length} records`;
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
      b.textContent = s.active ? `■ Stop log (${s.count})` : "● Start log";
      b.classList.toggle("danger", s.active);
      $("cap-recfile").style.display = s.path ? "" : "none";
      $("cap-recfile").title = s.path ? "download " + s.path : "";
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

  // ---- send-command modal ----------------------------------------------------
  function openSend(job, peer) {
    sendCtx = { job, peer };
    $("cap-send-peer").textContent = deviceLabel(peer) === peer ? peer : `${deviceLabel(peer)} (${peer})`;
    $("cap-send-value").value = "";
    msg("cap-send-msg", "", "");
    $("cap-send-modal").style.display = "flex";
    $("cap-send-value").focus();
  }
  const closeSend = () => { $("cap-send-modal").style.display = "none"; sendCtx = null; };
  $("cap-send-cancel").addEventListener("click", closeSend);
  $("cap-send-go").addEventListener("click", async () => {
    if (!sendCtx) return;
    const body = { peer: sendCtx.peer, mode: $("cap-send-mode").value, value: $("cap-send-value").value };
    if (!body.value.trim()) { msg("cap-send-msg", "err", "enter a value"); return; }
    try {
      const r = await postJSON("/api/send/" + sendCtx.job, body);
      msg("cap-send-msg", "ok", `sent ${r.sent} bytes (${r.hex.slice(0, 24)}${r.hex.length > 24 ? "…" : ""})`);
    } catch (e) { msg("cap-send-msg", "err", "send failed: " + e.message); }
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
  function renderAll() { renderTabs(); renderPane(); renderFeed(); applyModeChrome(); }

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
        if (j) { j.peers = m.data.peers; mapJobPeers(j); renderAll(); }
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
