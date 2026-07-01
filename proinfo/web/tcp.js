/* Runway - Live TCP: start/stop TCP listeners, watch connected devices and a live
 * decoded-packet feed, and send downlink commands. Backend: /api/listen, /api/jobs,
 * /api/stop/{id}, /api/send/{id}, /api/codecs; live updates over /ws. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.status;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
  }
  const msg = (id, cls, txt) => { const m = $(id); m.className = cls; m.textContent = txt; };
  const fmtBytes = (n) => (!n ? "0 B" : n < 1024 ? n + " B" : n < 1048576
    ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");
  function fmtDur(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + "h " : "") + (h || m ? m + "m " : "") + sec + "s";
  }

  const jobs = new Map();          // jid -> job
  const feed = [];                 // recent decoded records
  const MAX_FEED = 200;
  let sendCtx = null;              // { job, peer } for the modal
  let LOCAL_IP = "";               // this machine's auto-detected LAN IP
  const RAWFMT_KEY = "runway.tcp.rawfmt";   // persisted view mode (retained until changed)
  let rawFmt = "hex";              // how to render raw/unknown packet bytes: "hex" | "ascii"
  try { rawFmt = localStorage.getItem(RAWFMT_KEY) || "hex"; } catch (e) { /* ignore */ }

  const hexSpaced = (h) => (h.match(/.{1,2}/g) || []).join(" ").toUpperCase();
  function hexToAscii(h) {
    let s = "";
    for (let i = 0; i + 1 < h.length; i += 2) {
      const b = parseInt(h.substr(i, 2), 16);
      s += (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
    }
    return s;
  }
  function rawView(hex) {
    if (!hex) return "";
    return rawFmt === "ascii" ? hexToAscii(hex) : hexSpaced(hex);   // complete packet (cell wraps)
  }

  // ---- start form --------------------------------------------------------
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
      msg("tcp-msg", "ok", `listening on ${body.host}:${body.port}`);
      renderListeners();
    } catch (e) { msg("tcp-msg", "err", "start failed: " + e.message); }
  });

  async function stopJob(jid) {
    try {
      await api("/api/stop/" + jid, { method: "POST" });
      const j = jobs.get(jid);                        // optimistic: flip + freeze uptime now
      if (j) { j.status = "stopped"; j.ended = j.ended || Date.now() / 1000; }
      renderListeners();
      msg("tcp-msg", "ok", "stopped " + jid);
    } catch (e) { msg("tcp-msg", "err", "stop failed: " + e.message); }
  }
  async function resumeJob(jid) {
    try {
      const r = await api("/api/resume/" + jid, { method: "POST" });
      jobs.set(jid, { ...jobs.get(jid), ...r.job });
      renderListeners();
      msg("tcp-msg", "ok", "resumed " + jid);
    } catch (e) { msg("tcp-msg", "err", "resume failed: " + e.message); }
  }
  async function removeJob(jid) {
    const j = jobs.get(jid);
    const label = j ? `:${j.port} (${j.codec})` : jid;
    if (!window.confirm(`Remove listener ${label} from the list?`)) return;
    try { await api("/api/jobs/" + jid, { method: "DELETE" }); } catch (e) { /* ignore */ }
    jobs.delete(jid);
    renderListeners();
  }

  // ---- listeners (left) --------------------------------------------------
  function renderListeners() {
    const host = $("tcp-listeners");
    host.innerHTML = "";
    const list = [...jobs.values()].filter((j) => j.kind === "listen")
      .sort((a, b) => (a.id < b.id ? 1 : -1));
    if (!list.length) { host.appendChild(el("p", "empty", "No listeners running. Start one above.")); return; }
    list.forEach((j) => {
      const running = j.status === "running" || j.status === "pending";
      const card = el("div", "tcp-card" + (running ? " on" : " off"));

      const head = el("div", "tcp-card-head");
      head.append(el("span", "tcp-dot " + (running ? "live" : "dead")));
      // Full reachable address (IP:port) — the address to give devices. Shown consistently
      // whether running or stopped (same order), once (no separate reachable line).
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
        // ✕ first (left, with a gap), Resume rightmost — same slot Stop occupies when running.
        const rm = el("button", "ghost mini tcp-x", "✕");
        rm.title = "remove from list";
        rm.onclick = () => removeJob(j.id);
        const res = el("button", "ghost mini", "▶ Resume");
        res.onclick = () => resumeJob(j.id);
        head.append(rm, res);
      }
      card.append(head);

      const meta = el("div", "tcp-card-meta");
      // Running: tick from `started`. Stopped: freeze at `ended - started`.
      const upSecs = running && j.started ? (Date.now() / 1000 - j.started)
        : (j.started && j.ended ? (j.ended - j.started) : null);
      meta.dataset.started = running && j.started ? j.started : "";   // only running ticks
      meta.append(el("span", "tcp-up", `▲ ${upSecs == null ? "—" : fmtDur(upSecs)}`));
      meta.append(el("span", "", `${j.records || 0} records`));
      meta.append(el("span", "", fmtBytes(j.bytes)));
      const peers = j.peers || [];
      meta.append(el("span", "", `${peers.length} client${peers.length === 1 ? "" : "s"}`));
      if (j.error) meta.append(el("span", "err", j.error));
      card.append(meta);

      const pl = el("div", "tcp-peers");
      if (!peers.length) pl.append(el("span", "muted", running ? "waiting for devices…" : ""));
      peers.forEach((p) => {
        const row = el("div", "tcp-peer");
        row.append(el("span", "tcp-dot live"));
        row.append(el("span", "mono", p));
        const sp2 = el("span", "spacer"); row.append(sp2);
        if (running) {
          const send = el("button", "ghost mini", "✉ Send");
          send.onclick = () => openSend(j.id, p);
          row.append(send);
        }
        pl.append(row);
      });
      card.append(pl);
      host.appendChild(card);
    });
  }

  // tick uptimes once a second without re-fetching
  setInterval(() => {
    $("tcp-listeners").querySelectorAll(".tcp-card-meta").forEach((m) => {
      const st = Number(m.dataset.started);          // only set for running listeners
      const up = m.querySelector(".tcp-up");
      if (st && up) up.textContent = "▲ " + fmtDur(Date.now() / 1000 - st);
    });
  }, 1000);

  // ---- live feed (right) -------------------------------------------------
  function pushFeed(rec) {
    feed.unshift(rec);
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    renderFeed();
  }
  function renderFeed() {
    const host = $("tcp-feed");
    $("tcp-feed-count").textContent = feed.length ? `${feed.length} shown` : "";
    if (!feed.length) { host.innerHTML = '<p class="empty">Decoded packets appear here as devices send data.</p>'; return; }
    host.innerHTML = "";
    const t = el("table", "tcp-feed-table");
    const tb = el("tbody");
    feed.forEach((r) => {
      const tr = el("tr", "tcp-feed-row");
      tr.title = "click to see the full packet";
      tr.onclick = () => openPacket(r);
      const tm = r.event_time ? new Date(r.event_time).toLocaleTimeString() : "";
      tr.append(el("td", "muted tcp-time", tm));
      // Generic / unmatched packet: just timestamp + the complete packet (no wrap).
      const isRaw = r.message_type === "raw" || r.protocol === "generic_hex";
      if (isRaw) {
        const pkt = el("td", "mono tcp-raw", rawView(r.raw_hex));
        pkt.colSpan = 4;
        tr.append(pkt);
      } else {
        const pos = (r.position && r.position.latitude != null)
          ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "";
        tr.append(el("td", "mono", r.device_id || ""));
        tr.append(el("td", "", r.message_type || ""));
        tr.append(el("td", "mono", pos));
        tr.append(el("td", "num", r.speed_kph != null ? r.speed_kph : ""));
      }
      tb.append(tr);
    });
    t.append(tb); host.append(t);
  }
  $("tcp-feed-clear").addEventListener("click", () => { feed.length = 0; renderFeed(); });

  // full-packet view (click a feed row) — complete HEX + ASCII, untruncated
  function asciiFull(hex) {
    let s = "";
    for (let i = 0; i + 1 < hex.length; i += 2) {
      const b = parseInt(hex.substr(i, 2), 16);
      s += (b === 10 || b === 13) ? "\n" : (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
    }
    return s;
  }
  function openPacket(r) {
    const hex = r.raw_hex || "";
    const bytes = hex.length / 2;
    const when = r.event_time ? new Date(r.event_time).toLocaleString() : "";
    $("tcp-pkt-meta").textContent =
      `${r.device_id || "—"} · ${r.message_type || ""} · ${bytes} bytes${when ? " · " + when : ""}`;
    $("tcp-pkt-ascii").textContent = hex ? asciiFull(hex) : "(no raw bytes on this record)";
    $("tcp-pkt-hex").textContent = hex ? hexSpaced(hex) : "";
    $("tcp-pkt-modal").style.display = "flex";
  }
  $("tcp-pkt-close").addEventListener("click", () => { $("tcp-pkt-modal").style.display = "none"; });
  $("tcp-pkt-modal").addEventListener("click", (e) => {
    if (e.target.id === "tcp-pkt-modal") $("tcp-pkt-modal").style.display = "none";
  });
  function setRawFmt(fmt) {
    rawFmt = fmt;
    try { localStorage.setItem(RAWFMT_KEY, fmt); } catch (e) { /* ignore */ }
    $("tcp-fmt-hex").classList.toggle("on", fmt === "hex");
    $("tcp-fmt-ascii").classList.toggle("on", fmt === "ascii");
    renderFeed();
  }
  $("tcp-fmt-hex").addEventListener("click", () => setRawFmt("hex"));
  $("tcp-fmt-ascii").addEventListener("click", () => setRawFmt("ascii"));
  setRawFmt(rawFmt);               // apply the retained view mode on load

  // ---- send-command modal ------------------------------------------------
  function openSend(job, peer) {
    sendCtx = { job, peer };
    $("tcp-send-peer").textContent = peer;
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

  // ---- refresh + WebSocket ----------------------------------------------
  async function refreshJobs() {
    try {
      (await api("/api/jobs")).forEach((j) => jobs.set(j.id, j));
      renderListeners();
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
        // Re-seed the feed from the server's recent buffer so returning to this page
        // (e.g. after visiting the Dashboard) doesn't come back empty.
        feed.length = 0;
        (m.data.recent || []).slice(-MAX_FEED).reverse().forEach((r) => feed.push(r));
        renderFeed();
      } else if (m.type === "record") {
        pushFeed(m.data);
      } else if (m.type === "job") {
        jobs.set(m.data.id, { ...jobs.get(m.data.id), ...m.data });
        renderListeners();
      } else if (m.type === "conn") {
        const j = jobs.get(m.data.job);
        if (j) { j.peers = m.data.peers; renderListeners(); }
        msg("tcp-msg", "ok", `${m.data.peer} ${m.data.state === "up" ? "connected" : "disconnected"}`);
      }
    };
  }

  loadDefaults();
  refreshJobs();
  setInterval(refreshJobs, 2500);        // keep listener status / records / clients live
  connect();
})();
