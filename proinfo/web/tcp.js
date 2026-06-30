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

  // ---- start form --------------------------------------------------------
  async function loadCodecs(selected) {
    const sel = $("tcp-codec");
    sel.innerHTML = "";
    sel.appendChild(new Option("auto (detect)", "auto"));
    try {
      (await api("/api/codecs")).forEach((c) => sel.appendChild(new Option(c.display_name || c.name, c.name)));
    } catch (e) { /* ignore */ }
    if (selected) sel.value = selected;
  }
  async function loadDefaults() {
    try {
      const d = await api("/api/listen/defaults");
      $("tcp-port").value = d.port; $("tcp-host").value = d.host;
      await loadCodecs(d.codec);
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
    try { await api("/api/stop/" + jid, { method: "POST" }); msg("tcp-msg", "ok", "stopped " + jid); }
    catch (e) { msg("tcp-msg", "err", "stop failed: " + e.message); }
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
      head.append(el("span", "tcp-port", ":" + j.port));
      head.append(el("span", "tcp-codec-tag", j.codec));
      head.append(el("span", "tcp-status " + j.status, j.status));
      const sp = el("span", "spacer"); head.append(sp);
      if (running) {
        const stop = el("button", "ghost mini danger", "■ Stop");
        stop.onclick = () => stopJob(j.id);
        head.append(stop);
      }
      card.append(head);

      const meta = el("div", "tcp-card-meta");
      const up = j.started ? fmtDur(Date.now() / 1000 - j.started) : "—";
      meta.dataset.started = j.started || "";
      meta.append(el("span", "", `▲ ${up}`));
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
      const st = Number(m.dataset.started);
      if (st) m.firstChild.textContent = "▲ " + fmtDur(Date.now() / 1000 - st);
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
      const tr = el("tr");
      const tm = r.event_time ? new Date(r.event_time).toLocaleTimeString() : "";
      const pos = (r.position && r.position.latitude != null)
        ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : "";
      tr.append(el("td", "muted", tm));
      tr.append(el("td", "mono", r.device_id || ""));
      tr.append(el("td", "", r.message_type || ""));
      tr.append(el("td", "mono", pos));
      tr.append(el("td", "num", r.speed_kph != null ? r.speed_kph : ""));
      tb.append(tr);
    });
    t.append(tb); host.append(t);
  }
  $("tcp-feed-clear").addEventListener("click", () => { feed.length = 0; renderFeed(); });

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
      if (m.type === "record") {
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
  connect();
})();
