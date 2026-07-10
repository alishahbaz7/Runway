/* Runway - Live TCP: browser-style tabs (one per port session), the devices sending on
 * the focused port (left), and that port's live packet feed (right) with a RAW / Parsed
 * toggle. Backend: /api/listen, /api/jobs, /api/stop|resume/{id}, /api/send/{id},
 * /api/codecs; live updates over /ws. */
(() => {
  "use strict";

  const { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull } = window.Runway;
  const msg = (id, cls, txt) => { const m = $(id); m.className = cls; m.textContent = txt; };
  const fmtBytes = (n) => (!n ? "0 B" : n < 1024 ? n + " B" : n < 1048576
    ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");

  // Placeholder ids codecs emit before a device's real identity (IMEI) is learned from
  // the wire — never shown as a device name (kept in sync with qc/session.py's set).
  const UNKNOWN_IDS = new Set(["", "unknown", "teltonika-unknown", "gt06-unknown", "nmea-device", "device-1"]);

  const jobs = new Map();          // jid -> job (listen jobs = port sessions)
  const feed = [];                 // recent records across all ports (display window)
  const MAX_FEED = 500;
  const logBuffer = [];            // received records kept for log export
  const LOG_MAX = 20000;           // cap the log buffer so memory stays bounded
  const peerJob = new Map();       // peer (ip:port) -> jid, for routing records to a tab
  const peerInfo = new Map();      // peer -> {device_id, protocol} learned from records
  let sendCtx = null;              // { job, peer } for the modal
  let LOCAL_IP = "";               // this machine's auto-detected LAN IP

  // Retained view state (hard rule: settings stick until changed; page state survives
  // navigation within the browser session).
  const RAWFMT_KEY = "runway.tcp.rawfmt";   // "hex" | "ascii"      (localStorage)
  const VIEW_KEY = "runway.tcp.view";       // "raw" | "parsed"     (localStorage)
  const DEVCOLL_KEY = "runway.tcp.devcoll"; // devices section collapsed? (localStorage)
  const FOCUS_KEY = "runway.tcp.focus";     // focused tab's jid    (sessionStorage)
  const HIDDEN_KEY = "runway.tcp.hidden";   // unchecked peers      (sessionStorage)
  const NEW = "__new__";           // sentinel focus id for the unstarted "New session" draft tab
  let draftOpen = false;           // is a draft (unstarted) session tab currently open?
  let rawFmt = "hex";
  let viewMode = "raw";
  let devicesCollapsed = false;    // squeeze the "Devices on this port" list (small screens)
  let focusJid = null;
  const hiddenPeers = new Set();   // peers filtered OUT of the feed (hidden, NOT stopped)
  try {
    rawFmt = localStorage.getItem(RAWFMT_KEY) || "hex";
    viewMode = localStorage.getItem(VIEW_KEY) || "raw";
    devicesCollapsed = localStorage.getItem(DEVCOLL_KEY) === "1";
    focusJid = sessionStorage.getItem(FOCUS_KEY) || null;
    JSON.parse(sessionStorage.getItem(HIDDEN_KEY) || "[]").forEach((p) => hiddenPeers.add(p));
  } catch (e) { /* ignore */ }
  if (focusJid === NEW) draftOpen = true;   // a draft was open when the page was last left
  const saveHidden = () => {
    try { sessionStorage.setItem(HIDDEN_KEY, JSON.stringify([...hiddenPeers])); } catch (e) { /* ignore */ }
  };

  // Log timestamp: DD-MMM-YYYY HH:MM:SS:zzz  (local time, millisecond precision)
  const _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtLogTs(t) {
    const d = new Date(t);
    if (isNaN(d.getTime())) return "";
    const p2 = (n) => String(n).padStart(2, "0");
    const p3 = (n) => String(n).padStart(3, "0");
    return `${p2(d.getDate())}-${_MON[d.getMonth()]}-${d.getFullYear()} `
      + `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}:${p3(d.getMilliseconds())}`;
  }
  // Feed-row date subtitle: DD-MMM-YYYY in the browser's local time.
  function fmtDate(t) {
    const d = new Date(t);
    if (isNaN(d.getTime())) return "";
    return `${String(d.getDate()).padStart(2, "0")}-${_MON[d.getMonth()]}-${d.getFullYear()}`;
  }
  // The browser's local timezone abbreviation (e.g. "IST", "UTC") — feed times render in
  // local time, so the Timestamp header is labelled with it once instead of repeating it
  // on every row. Some locales report the short zone as an offset ("GMT+5:30") rather than
  // an abbreviation; in that case derive initials from the long name ("India Standard
  // Time" -> "IST"), which is stable across locales, and fall back to the offset otherwise.
  const TZ_LABEL = (() => {
    try {
      const now = new Date();
      const nameOf = (style) => {
        const p = new Intl.DateTimeFormat(undefined, { timeZoneName: style })
          .formatToParts(now).find((x) => x.type === "timeZoneName");
        return p ? p.value : "";
      };
      const short = nameOf("short");
      if (/^[A-Za-z]{2,5}$/.test(short)) return short;      // already an abbreviation
      const initials = nameOf("long").split(/\s+/)
        .filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).join("");
      return initials.length >= 2 ? initials : short;       // "India Standard Time" -> "IST"
    } catch (e) { return ""; }
  })();

  function rawView(hex) {
    if (!hex) return "";
    return rawFmt === "ascii" ? hexToAscii(hex) : hexSpaced(hex);   // complete packet (cell wraps)
  }

  // Classify a decoded record for the feed's colour coding (see the legend). Precedence:
  // an alarm packet also carries a position, but the alarm is what matters, so it wins.
  //   alarm   (violet) — record has any alarms[]
  //   unknown (white)  — undecoded: message_type raw/unknown, or the generic_hex fallback
  //   history (yellow) — AIS-140 buffered data (io.packet_status "H"); only protocol with the flag
  //   live    (green)  — real-time position (packet_status "L", or a normal position packet)
  //   other   (neutral)— protocol housekeeping: login/heartbeat/health/ack/command/response
  const POS_TYPES = ["normal", "location", "avl", "rmc", "gga"];
  function packetKind(r) {
    if (r.alarms && r.alarms.length) return "alarm";
    const mt = (r.message_type || "").toLowerCase();
    if (mt === "raw" || mt === "unknown" || r.protocol === "generic_hex") return "unknown";
    const ps = String((r.io && r.io.packet_status) || "").trim().toUpperCase();
    if (ps === "H") return "history";
    if (ps === "L") return "live";
    return POS_TYPES.includes(mt) ? "live" : "other";
  }

  // ---- record -> port session routing + device identity -------------------
  function mapJobPeers(j) {
    (j.peers || []).forEach((p) => peerJob.set(p, j.id));
    (j.clients || []).forEach((c) => peerJob.set(c.peer, j.id));
  }
  function learnDevice(r) {
    if (!r.peer) return;
    const info = peerInfo.get(r.peer) || { device_id: "", protocol: "" };
    if (r.device_id && !UNKNOWN_IDS.has(r.device_id)) info.device_id = r.device_id;
    // Prefer a real protocol match; only show generic_hex if that's all we've ever seen.
    if (r.protocol && (r.protocol !== "generic_hex" || !info.protocol)) info.protocol = r.protocol;
    peerInfo.set(r.peer, info);
  }
  const deviceLabel = (peer) => (peerInfo.get(peer) || {}).device_id || peer || "";

  const jobNum = (jid) => Number(String(jid).replace(/\D/g, "")) || 0;   // "job-12" -> 12
  const listenJobs = () => [...jobs.values()].filter((j) => j.kind === "listen")
    .sort((a, b) => jobNum(a.id) - jobNum(b.id));      // oldest left, new tabs append right

  function focusedJob() {
    if (focusJid === NEW) return null;                 // the draft tab has no job yet
    const list = listenJobs();
    let j = list.find((x) => x.id === focusJid);
    if (!j && list.length) { j = list[list.length - 1]; focusJid = j.id; }
    return j || null;
  }
  function setFocus(jid) {
    focusJid = jid;
    try { sessionStorage.setItem(FOCUS_KEY, jid || ""); } catch (e) { /* ignore */ }
    renderAll();
  }

  // ---- draft (unstarted "New session") tab ---------------------------------
  // One draft at a time: clicking "+" opens it (or just focuses it if already open).
  // Starting it (below) turns it into a real running session; ✕ discards it.
  function openDraft() {
    if (draftOpen) { setFocus(NEW); return; }
    draftOpen = true;
    msg("tcp-msg", "", "");
    loadDefaults();                 // refill Protocol/Port/IP defaults + the local-IP hint
    setFocus(NEW);
  }
  function closeDraft() {
    draftOpen = false;
    if (focusJid === NEW) focusJid = null;
    if (!listenJobs().length) { openDraft(); return; }   // never leave the page with nothing
    renderAll();
  }

  // ---- start form ----------------------------------------------------------
  async function loadCodecs(selected) {
    const sel = $("tcp-codec");
    sel.innerHTML = "";
    sel.appendChild(new Option("Auto (detect)", "auto"));
    sel.appendChild(new Option("Generic (raw — no protocol)", "generic_hex"));
    try {
      (await api("/api/codecs")).forEach((c) => {
        if (c.name === "generic_hex" || c.name === "custom_template") return;  // surfaced above / hidden
        sel.appendChild(new Option(c.display_name || c.name, c.name));
      });
    } catch (e) { /* ignore */ }
    if (selected) sel.value = selected;
  }
  async function loadDefaults() {
    try {
      const d = await api("/api/listen/defaults");
      LOCAL_IP = d.local_ip || "";
      $("tcp-port").value = d.port;
      $("tcp-host").value = d.local_ip || d.host || "0.0.0.0";   // auto-filled with this machine's IP
      $("tcp-localip").textContent = LOCAL_IP
        ? `This machine: ${LOCAL_IP}${d.hostname ? " (" + d.hostname + ")" : ""} — devices dial in here`
        : "";
      await loadCodecs("auto");            // Protocol defaults to Auto (detect)
    } catch (e) { await loadCodecs("auto"); }
  }

  $("tcp-start").addEventListener("click", async () => {
    const body = { codec: $("tcp-codec").value, host: $("tcp-host").value.trim() || "0.0.0.0",
      port: Number($("tcp-port").value) };
    if (!body.port || body.port < 1 || body.port > 65535) { msg("tcp-msg", "err", "invalid port"); return; }
    try {
      const r = await api("/api/listen", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      jobs.set(r.job.id, r.job);
      mapJobPeers(r.job);
      draftOpen = false;                   // the draft has become a real running session
      msg("tcp-msg", "ok", `listening on ${body.host}:${body.port}`);
      setFocus(r.job.id);                  // it opens as the active tab
    } catch (e) { msg("tcp-msg", "err", "start failed: " + e.message); }
  });

  async function stopJob(jid) {
    try {
      await api("/api/stop/" + jid, { method: "POST" });
      const j = jobs.get(jid);                        // optimistic: flip + freeze uptime now
      if (j) { j.status = "stopped"; j.ended = j.ended || Date.now() / 1000; }
      renderAll();
      msg("tcp-msg", "ok", "stopped " + jid);
    } catch (e) { msg("tcp-msg", "err", "stop failed: " + e.message); }
  }
  async function resumeJob(jid) {
    try {
      const r = await api("/api/resume/" + jid, { method: "POST" });
      jobs.set(jid, { ...jobs.get(jid), ...r.job });
      renderAll();
      msg("tcp-msg", "ok", "resumed " + jid);
    } catch (e) { msg("tcp-msg", "err", "resume failed: " + e.message); }
  }
  async function closeTab(jid) {
    const j = jobs.get(jid);
    const running = j && (j.status === "running" || j.status === "pending");
    const label = j ? `:${j.port} (${j.codec})` : jid;
    if (!window.confirm(running
      ? `Stop port session ${label} and close its tab?`
      : `Close tab ${label}?`)) return;
    try {
      if (running) await api("/api/stop/" + jid, { method: "POST" });
      // Stop cancels the job asynchronously — retry the remove briefly until the server
      // has actually torn the task down (otherwise it answers "stop the job first").
      for (let i = 0; ; i++) {
        try { await api("/api/jobs/" + jid, { method: "DELETE" }); break; }
        catch (e) {
          if (i >= 20) throw e;
          await new Promise((res) => setTimeout(res, 100));
        }
      }
    } catch (e) { msg("tcp-msg", "err", "close failed: " + e.message); return; }
    jobs.delete(jid);
    if (focusJid === jid) focusJid = null;
    if (!listenJobs().length && !draftOpen) { openDraft(); return; }  // never leave nothing
    renderAll();
  }

  // ---- tab strip (top): one tab per port session + a "New session" draft/"+" -----
  function renderTabs() {
    const host = $("tcp-tabs");
    host.innerHTML = "";
    listenJobs().forEach((j) => {
      const running = j.status === "running" || j.status === "pending";
      const tab = el("div", "tcp-tab" + (running ? " on" : " off") + (j.id === focusJid ? " active" : ""));
      tab.onclick = () => setFocus(j.id);
      tab.append(el("span", "tcp-dot " + (running ? "live" : "dead")));
      tab.append(el("span", "tcp-tab-port", String(j.port)));
      tab.append(el("span", "tcp-tab-sub muted", j.codec));
      const clients = j.clients || [];
      const conn = clients.filter((c) => c.connected).length;
      if (conn) tab.append(el("span", "tcp-tab-count", String(conn)));
      const x = el("button", "tcp-tab-x", "✕");
      x.title = running ? "stop this port session and close the tab" : "close this tab";
      x.onclick = (e) => { e.stopPropagation(); closeTab(j.id); };
      tab.append(x);
      host.appendChild(tab);
    });
    if (draftOpen) {                       // the unstarted "New session" tab
      const tab = el("div", "tcp-tab tcp-tab-draft" + (focusJid === NEW ? " active" : ""));
      tab.onclick = () => setFocus(NEW);
      tab.append(el("span", "tcp-dot"));
      tab.append(el("span", "tcp-tab-port", "New"));
      const x = el("button", "tcp-tab-x", "✕");
      x.title = "discard this new session";
      x.onclick = (e) => { e.stopPropagation(); closeDraft(); };
      tab.append(x);
      host.appendChild(tab);
    }
    const add = el("button", "tcp-tab-add", "+");    // always available: open a new draft
    add.title = "start a new port session";
    add.onclick = () => openDraft();
    host.appendChild(add);
  }

  // ---- focused session strip + device list (left) --------------------------
  function renderSession() {
    const host = $("tcp-session");
    host.innerHTML = "";
    const j = focusedJob();
    if (!j) return;
    const running = j.status === "running" || j.status === "pending";

    const head = el("div", "tcp-card-head");
    head.append(el("span", "tcp-dot " + (running ? "live" : "dead")));
    // Full reachable address (IP:port) — the address to give devices.
    const addr = (LOCAL_IP || j.host) ? `${LOCAL_IP || j.host}:${j.port}` : `:${j.port}`;
    const portEl = el("span", "tcp-port", addr);
    portEl.title = "point devices to this address";
    head.append(portEl);
    head.append(el("span", "tcp-codec-tag", j.codec));
    head.append(el("span", "tcp-status " + j.status, j.status));
    head.append(el("span", "spacer"));
    if (running) {
      const stop = el("button", "ghost mini danger", "■ Stop");
      stop.onclick = () => stopJob(j.id);
      head.append(stop);
    } else {
      const res = el("button", "ghost mini", "▶ Resume");
      res.onclick = () => resumeJob(j.id);
      head.append(res);
    }
    host.append(head);

    const meta = el("div", "tcp-card-meta");
    // Running: tick from `started`. Stopped: freeze at `ended - started`.
    const upSecs = running && j.started ? (Date.now() / 1000 - j.started)
      : (j.started && j.ended ? (j.ended - j.started) : null);
    meta.dataset.started = running && j.started ? j.started : "";   // only running ticks
    meta.append(el("span", "tcp-up", `▲ ${upSecs == null ? "—" : fmtDur(upSecs)}`));
    meta.append(el("span", "", `${j.records || 0} records`));
    meta.append(el("span", "", fmtBytes(j.bytes)));
    if (j.error) meta.append(el("span", "err", j.error));
    host.append(meta);
  }

  function renderDevices() {
    const host = $("tcp-devices");
    host.innerHTML = "";
    const j = focusedJob();
    if (!j) { host.append(el("p", "empty", "Start a port session to see devices here.")); return; }
    const running = j.status === "running" || j.status === "pending";
    const clients = j.clients || (j.peers || []).map((p) => ({ peer: p, records: 0, last: 0, connected: true }));

    const head = el("div", "tcp-peers-head");
    // Collapse/expand toggle — squeeze the device list on small screens. The header (with
    // the count) always stays, so you still see how many devices are on the port.
    const caret = el("button", "tcp-collapse", devicesCollapsed ? "▸" : "▾");
    caret.title = devicesCollapsed ? "expand the device list" : "collapse the device list";
    const toggle = () => {
      devicesCollapsed = !devicesCollapsed;
      try { localStorage.setItem(DEVCOLL_KEY, devicesCollapsed ? "1" : "0"); } catch (e) { /* ignore */ }
      renderDevices();
    };
    caret.onclick = toggle;
    head.append(caret);
    const title = el("span", "tcp-peers-title", `Devices on this port (${clients.length})`);
    title.style.cursor = "pointer";
    title.title = caret.title;
    title.onclick = toggle;
    head.append(title);
    head.append(el("span", "spacer"));
    if (clients.length && !devicesCollapsed) {
      const all = el("button", "ghost mini", "All");
      all.title = "show all devices' packets";
      all.onclick = () => { clients.forEach((c) => hiddenPeers.delete(c.peer)); saveHidden(); renderDevices(); renderFeed(); };
      const none = el("button", "ghost mini", "None");
      none.title = "hide all devices' packets";
      none.onclick = () => { clients.forEach((c) => hiddenPeers.add(c.peer)); saveHidden(); renderDevices(); renderFeed(); };
      head.append(all, none);
    }
    host.append(head);

    if (devicesCollapsed) return;               // header only — rows hidden

    if (!clients.length) {
      host.append(el("p", "empty", running ? "Waiting for devices to dial in…" : "No devices connected."));
      return;
    }
    clients.forEach((c) => {
      const p = c.peer;
      const info = peerInfo.get(p) || {};
      const row = el("div", "tcp-peer tcp-client-row");
      row.dataset.peer = p;
      row.dataset.last = c.last || 0;
      const cb = el("input"); cb.type = "checkbox"; cb.checked = !hiddenPeers.has(p);
      cb.title = "show this device's packets in the feed";
      cb.onchange = () => { cb.checked ? hiddenPeers.delete(p) : hiddenPeers.add(p); saveHidden(); renderFeed(); };
      row.append(cb);
      row.append(el("span", "tcp-dot " + (c.connected ? "live" : "dead")));
      const name = el("div", "tcp-device-id");
      name.append(el("span", "mono tcp-client-addr" + (c.connected ? "" : " muted"),
        info.device_id || p));
      // Once the device's real id (IMEI) is known, keep its address as the secondary line.
      if (info.device_id) name.append(el("span", "muted tcp-device-peer", p));
      row.append(name);
      // Fixed-size cluster (never shrinks) so `name` above is the only element that gives
      // way when the pane is narrow — truncating with an ellipsis instead of the whole
      // row's layout fighting over space.
      const meta = el("div", "tcp-peer-meta");
      if (info.protocol) meta.append(el("span", "tcp-codec-tag", info.protocol));
      meta.append(el("span", "tcp-client-count", String(c.records || 0)));
      meta.append(el("span", "tcp-client-idle muted", fmtIdle(c.last || 0)));
      if (running && c.connected) {
        const send = el("button", "ghost mini", "✉ Send");
        send.onclick = () => openSend(j.id, p);
        meta.append(send);
      }
      row.append(meta);
      host.append(row);
    });
  }

  // tick uptime + per-device idle once a second without re-fetching
  setInterval(() => {
    const m = $("tcp-session").querySelector(".tcp-card-meta");
    if (m) {
      const st = Number(m.dataset.started);          // only set for running sessions
      const up = m.querySelector(".tcp-up");
      if (st && up) up.textContent = "▲ " + fmtDur(Date.now() / 1000 - st);
    }
    document.querySelectorAll("#tcp-devices .tcp-client-row").forEach((row) => {
      const i = row.querySelector(".tcp-client-idle");
      if (i) i.textContent = fmtIdle(Number(row.dataset.last) || 0);
    });
  }, 1000);

  // ---- live feed (right) ----------------------------------------------------
  // idle from a server epoch-seconds timestamp (per-device "last packet" time)
  const fmtIdle = (lastSec) => (!lastSec ? "—" : Math.max(0, Math.round(Date.now() / 1000 - lastSec)) + "s");

  function ingest(rec) {                 // a live record arrived — feed + log buffer only
    learnDevice(rec);
    logBuffer.push(rec);
    if (logBuffer.length > LOG_MAX) logBuffer.shift();
    feed.unshift(rec);
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    if (rec.peer && peerJob.get(rec.peer) === focusJid) renderFeed();
  }

  function focusRows() {
    // The feed shows the focused tab's port only: its checked (visible) devices.
    return feed.filter((r) => r.peer && peerJob.get(r.peer) === focusJid && !hiddenPeers.has(r.peer));
  }
  function renderFeed() {
    const host = $("tcp-feed");
    const j = focusedJob();
    $("tcp-feed-for").textContent = j ? `— :${j.port}` : "";
    const rows = focusRows();
    $("tcp-feed-count").textContent = rows.length ? `${rows.length} shown` : "";
    if (!rows.length) {
      host.innerHTML = `<p class="empty">${j
        ? "Decoded packets appear here as devices send data."
        : "Start a port session to see packets here."}</p>`;
      return;
    }
    host.innerHTML = "";
    const t = el("table", "tcp-feed-table");
    const thead = el("thead");
    const htr = el("tr");
    htr.append(el("th", "", TZ_LABEL ? `Timestamp (${TZ_LABEL})` : "Timestamp"));
    htr.append(el("th", "", "Device"));
    if (viewMode === "parsed") {
      htr.append(el("th", "", "Type"));
      htr.append(el("th", "", "Position"));
      htr.append(el("th", "", "Speed"));
    } else {
      htr.append(el("th", "", "Raw packet"));
    }
    thead.append(htr); t.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr", "tcp-feed-row pk-" + packetKind(r));   // colour code (see legend)
      tr.title = "click to see the full packet";
      tr.onclick = () => openPacket(r);
      // Prefer the device's reported time; fall back to when we received it (raw packets
      // have no decoded event_time), so every row is timestamped.
      const tm = r.event_time || r.received_at;
      const tcell = el("td", "tcp-time");
      if (tm) {
        const d = new Date(tm);
        tcell.append(el("div", "tcp-time-hms", d.toLocaleTimeString()));
        tcell.append(el("div", "tcp-time-date", fmtDate(tm)));   // date subtitle
      }
      tr.append(tcell);
      tr.append(el("td", "mono", deviceLabel(r.peer)));
      if (viewMode === "parsed") {
        const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
        const pos = (!isRaw && r.position && r.position.latitude != null)
          ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "";
        tr.append(el("td", "", r.message_type || ""));
        tr.append(el("td", "mono", pos));
        tr.append(el("td", "num", !isRaw && r.speed_kph != null ? String(r.speed_kph) : ""));
      } else {
        tr.append(el("td", "mono tcp-raw", rawView(r.raw_hex)));
      }
      tb.append(tr);
    });
    t.append(tb); host.append(t);
  }
  $("tcp-feed-clear").addEventListener("click", () => {
    // Clear the focused tab's packets only (other port sessions keep theirs).
    if (focusJid) {
      const mine = (r) => r.peer && peerJob.get(r.peer) === focusJid;
      for (let i = feed.length - 1; i >= 0; i--) if (mine(feed[i])) feed.splice(i, 1);
      for (let i = logBuffer.length - 1; i >= 0; i--) if (mine(logBuffer[i])) logBuffer.splice(i, 1);
    } else { feed.length = 0; logBuffer.length = 0; }
    renderFeed();
  });

  // RAW | Parsed view + HEX | ASCII raw format (both retained until changed)
  function setViewMode(v) {
    viewMode = v;
    try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* ignore */ }
    $("tcp-view-raw").classList.toggle("on", v === "raw");
    $("tcp-view-parsed").classList.toggle("on", v === "parsed");
    $("tcp-rawfmt-seg").style.display = v === "raw" ? "" : "none";  // HEX/ASCII only applies to RAW
    renderFeed();
  }
  $("tcp-view-raw").addEventListener("click", () => setViewMode("raw"));
  $("tcp-view-parsed").addEventListener("click", () => setViewMode("parsed"));
  function setRawFmt(fmt) {
    rawFmt = fmt;
    try { localStorage.setItem(RAWFMT_KEY, fmt); } catch (e) { /* ignore */ }
    $("tcp-fmt-hex").classList.toggle("on", fmt === "hex");
    $("tcp-fmt-ascii").classList.toggle("on", fmt === "ascii");
    renderFeed();
  }
  $("tcp-fmt-hex").addEventListener("click", () => setRawFmt("hex"));
  $("tcp-fmt-ascii").addEventListener("click", () => setRawFmt("ascii"));

  // colour-legend popover (ⓘ) — toggle it, and close on an outside click or Escape
  const legendBtn = $("tcp-legend-btn");
  const legendPop = $("tcp-legend");
  legendBtn.addEventListener("click", (e) => { e.stopPropagation(); legendPop.hidden = !legendPop.hidden; });
  document.addEventListener("click", (e) => {
    if (!legendPop.hidden && !legendPop.contains(e.target) && e.target !== legendBtn) legendPop.hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") legendPop.hidden = true; });

  // full-packet view (click a feed row) — parsed fields + complete HEX + ASCII
  function openPacket(r) {
    const hex = r.raw_hex || "";
    const bytes = hex.length / 2;
    const when = r.event_time ? new Date(r.event_time).toLocaleString() : "";
    $("tcp-pkt-meta").textContent =
      `${deviceLabel(r.peer) || r.device_id || "—"} · ${r.message_type || ""} · ${bytes} bytes${when ? " · " + when : ""}`;
    const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
    if (isRaw) {
      $("tcp-pkt-parsed").textContent = "(raw packet — no protocol matched, nothing parsed)";
    } else {
      const parsed = { ...r };
      delete parsed.raw_hex;                 // shown in full under HEX below
      delete parsed.seq;
      $("tcp-pkt-parsed").textContent = JSON.stringify(parsed, null, 2);
    }
    $("tcp-pkt-ascii").textContent = hex ? asciiFull(hex) : "(no raw bytes on this record)";
    $("tcp-pkt-hex").textContent = hex ? hexSpaced(hex) : "";
    $("tcp-pkt-modal").style.display = "flex";
  }
  $("tcp-pkt-close").addEventListener("click", () => { $("tcp-pkt-modal").style.display = "none"; });
  $("tcp-pkt-modal").addEventListener("click", (e) => {
    if (e.target.id === "tcp-pkt-modal") $("tcp-pkt-modal").style.display = "none";
  });

  // ---- log export (TXT / CSV) ----------------------------------------------
  function openLog() {
    const total = logBuffer.length;
    const sel = logBuffer.filter((r) => r.peer && peerJob.get(r.peer) === focusJid && !hiddenPeers.has(r.peer)).length;
    $("tcp-log-msg").textContent = `${total} records buffered · ${sel} from selected devices on this port`;
    $("tcp-log-modal").style.display = "flex";
  }
  function closeLog() { $("tcp-log-modal").style.display = "none"; }
  function downloadLog() {
    const fmt = $("tcp-log-format").value;      // txt | csv
    const scope = $("tcp-log-scope").value;     // all | selected
    const rawAs = $("tcp-log-raw").value;       // ascii | hex
    const rows = logBuffer.filter((r) => scope === "all"
      || (r.peer && peerJob.get(r.peer) === focusJid && !hiddenPeers.has(r.peer)));
    const oneLineAscii = (h) => hexToAscii(h).replace(/\n/g, " ");   // keep each record on one line
    const rawOf = (r) => (rawAs === "hex" ? hexSpaced(r.raw_hex || "") : oneLineAscii(r.raw_hex || ""));
    const tsOf = (r) => fmtLogTs(r.event_time || r.received_at);   // DD-MMM-YYYY HH:MM:SS:zzz
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
    a.download = `runway-tcp-log-${stamp}.${ext}`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
    $("tcp-log-msg").textContent = `saved ${rows.length} records`;
  }
  $("tcp-log").addEventListener("click", openLog);
  $("tcp-log-cancel").addEventListener("click", closeLog);
  $("tcp-log-close").addEventListener("click", closeLog);
  $("tcp-log-go").addEventListener("click", downloadLog);
  $("tcp-log-modal").addEventListener("click", (e) => { if (e.target.id === "tcp-log-modal") closeLog(); });

  // ---- continuous logging (server-side start/stop) --------------------------
  let logRecActive = false;
  let logDefaultDir = "";
  async function refreshLogStatus() {
    try {
      const s = await api("/api/log/status");
      logRecActive = s.active;
      logDefaultDir = s.default_dir || "";
      const b = $("tcp-rec");
      b.textContent = s.active ? `■ Stop log (${s.count})` : "● Start log";
      b.classList.toggle("danger", s.active);
      $("tcp-recfile").style.display = s.path ? "" : "none";
      $("tcp-recfile").title = s.path ? "download " + s.path : "";
    } catch (e) { /* ignore */ }
  }
  $("tcp-rec").addEventListener("click", () => {
    if (logRecActive) { stopRec(); return; }
    $("tcp-rec-msg").textContent = "";
    $("tcp-rec-dir").value = logDefaultDir;      // prefill with the server default folder
    $("tcp-rec-name").value = "";                // blank = auto timestamped
    $("tcp-rec-modal").style.display = "flex";
  });
  const closeRec = () => { $("tcp-rec-modal").style.display = "none"; };
  $("tcp-rec-close").addEventListener("click", closeRec);
  $("tcp-rec-cancel").addEventListener("click", closeRec);
  $("tcp-rec-modal").addEventListener("click", (e) => { if (e.target.id === "tcp-rec-modal") closeRec(); });
  $("tcp-rec-go").addEventListener("click", async () => {
    const body = {
      fmt: $("tcp-rec-format").value, raw: $("tcp-rec-raw").value,
      name: $("tcp-rec-name").value.trim(), directory: $("tcp-rec-dir").value.trim(),
    };
    try {
      const s = await api("/api/log/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      closeRec(); msg("tcp-msg", "ok", "logging to " + (s.path || "file")); refreshLogStatus();
    } catch (e) { $("tcp-rec-msg").textContent = "start failed: " + e.message; }
  });
  async function stopRec() {
    try { const s = await api("/api/log/stop", { method: "POST" }); msg("tcp-msg", "ok", `logging stopped (${s.count} packets)`); }
    catch (e) { msg("tcp-msg", "err", "stop failed: " + e.message); }
    refreshLogStatus();
  }
  $("tcp-recfile").addEventListener("click", () => {
    const a = el("a"); a.href = "/api/log/file"; document.body.appendChild(a); a.click(); a.remove();
  });

  // ---- send-command modal ----------------------------------------------------
  function openSend(job, peer) {
    sendCtx = { job, peer };
    $("tcp-send-peer").textContent = deviceLabel(peer) === peer ? peer : `${deviceLabel(peer)} (${peer})`;
    $("tcp-send-value").value = "";
    msg("tcp-send-msg", "", "");
    $("tcp-send-modal").style.display = "flex";
    $("tcp-send-value").focus();
  }
  function closeSend() { $("tcp-send-modal").style.display = "none"; sendCtx = null; }
  $("tcp-send-cancel").addEventListener("click", closeSend);
  $("tcp-send-go").addEventListener("click", async () => {
    if (!sendCtx) return;
    const body = { peer: sendCtx.peer, mode: $("tcp-send-mode").value, value: $("tcp-send-value").value };
    if (!body.value.trim()) { msg("tcp-send-msg", "err", "enter a value"); return; }
    try {
      const r = await api("/api/send/" + sendCtx.job, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      msg("tcp-send-msg", "ok", `sent ${r.sent} bytes (${r.hex.slice(0, 24)}${r.hex.length > 24 ? "…" : ""})`);
    } catch (e) { msg("tcp-send-msg", "err", "send failed: " + e.message); }
  });

  // ---- refresh + WebSocket ---------------------------------------------------
  // The left pane is EITHER the draft config form (a "New" tab is focused) OR the focused
  // session's strip + device list. Toggle which one is visible, then render it.
  function renderPane() {
    const draft = focusJid === NEW;
    $("tcp-config").style.display = draft ? "" : "none";
    $("tcp-session").style.display = draft ? "none" : "";
    $("tcp-devices").style.display = draft ? "none" : "";
    if (!draft) { renderSession(); renderDevices(); }
  }
  function renderAll() { renderTabs(); renderPane(); renderFeed(); }

  async function refreshJobs() {
    try {
      (await api("/api/jobs")).forEach((j) => { jobs.set(j.id, j); mapJobPeers(j); });
      renderAll();
    } catch (e) { /* ignore */ }
    refreshLogStatus();      // keep the Start/Stop-log button + count in sync
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    sock.onclose = () => setTimeout(connect, 2000);
    sock.onerror = () => sock.close();
    sock.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "snapshot") {
        // Re-seed feed + log buffer from the server's recent buffer so returning to this
        // page (e.g. after the Dashboard) doesn't come back empty. Per-device counts come
        // from the server (job.clients), so they survive reloads on their own.
        feed.length = 0; logBuffer.length = 0;
        const recent = m.data.recent || [];
        recent.forEach((r) => { learnDevice(r); logBuffer.push(r); });
        recent.slice(-MAX_FEED).reverse().forEach((r) => feed.push(r));
        renderFeed();
      } else if (m.type === "record") {
        ingest(m.data);
      } else if (m.type === "job") {
        const j = { ...jobs.get(m.data.id), ...m.data };
        jobs.set(j.id, j);
        mapJobPeers(j);
        renderAll();
      } else if (m.type === "conn") {
        const j = jobs.get(m.data.job);
        if (j) { j.peers = m.data.peers; mapJobPeers(j); renderAll(); }
        msg("tcp-msg", "ok", `${m.data.peer} ${m.data.state === "up" ? "connected" : "disconnected"}`);
      }
    };
  }

  setViewMode(viewMode);           // apply the retained view + raw-format modes on load
  setRawFmt(rawFmt);
  loadDefaults();
  // Populate jobs (and the peer->port-session map) BEFORE opening the WebSocket:
  // otherwise the initial snapshot's records could arrive before any job/peer mapping
  // exists and the focused tab would look empty until the next poll.
  refreshJobs().finally(() => {
    // Fresh page (or all tabs closed) → open a draft so the config form is right there,
    // replacing the old always-visible top banner.
    if (!listenJobs().length && !draftOpen) { draftOpen = true; focusJid = NEW; }
    renderAll();
    setInterval(refreshJobs, 2500);        // keep session status / records / devices live
    connect();
  });
})();
