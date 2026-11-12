/* Runway - Archive page: every stored packet, one file per device per day (storage/archive.py).
 *
 * Each device-day can be downloaded as it is (the packets exactly as received, one per line) or
 * replayed to a platform: one connection, the device's login first, every packet in its original
 * order, at a chosen pace. Running replays show their progress and can be stopped. The Storage
 * card shows what each store takes on disk and sets how long it keeps data.
 */
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
    const r = await fetch(path, opts);
    if (!r.ok) {
      let d; try { d = await r.json(); } catch (e) { /* ignore */ }
      throw new Error((d && d.detail) || r.status);
    }
    return r.json();
  }
  const TARGET_KEY = "runway.archive.target";
  const fmtBytes = (n) => (n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB"
    : n >= 1e3 ? (n / 1e3).toFixed(1) + " KB" : n + " B");
  const fmtDay = (d) => d.split("-").reverse().join("-");            // YYYY-MM-DD -> DD-MM-YYYY
  const RATES = [[0, "As fast as possible"], [200, "200 packets/s"], [50, "50 packets/s"], [10, "10 packets/s"]];
  const STATUS = { running: "Running", done: "Done", error: "Error", stopped: "Stopped" };
  const UI = window.RunwayUI;

  let days = [], replays = [], timer = null;

  // ---- days -------------------------------------------------------------------
  function renderDays() {
    const dev = $("arch-device").value;
    const rows = days.filter((d) => !dev || d.device_id === dev);
    const t = $("arch-days"); t.innerHTML = "";
    $("arch-days-count").textContent = rows.length ? `${rows.length}` : "";
    $("arch-total").textContent = rows.length
      ? `${fmtBytes(rows.reduce((s, d) => s + d.bytes, 0))} on disk` : "";
    $("arch-empty").hidden = !!rows.length;
    $("arch-empty").textContent = days.length ? "No days for this device." : "Nothing archived yet.";
    if (!rows.length) return;
    const head = t.createTHead().insertRow();
    [["Device", ""], ["Day", ""], ["Size", "num"], ["File", ""], ["", "fit"]]
      .forEach(([h, cls]) => head.appendChild(el("th", cls, h)));
    const body = t.createTBody();
    rows.forEach((d) => {
      const tr = body.insertRow();
      tr.append(el("td", "mono", d.device_id), el("td", "mono", fmtDay(d.day)),
                el("td", "num", fmtBytes(d.bytes)),
                el("td", "muted", d.compressed ? "Gzipped" : "Today · open"));
      const act = el("td", "arch-act");
      const dl = UI.setLabel(el("a", "btn-link"), "download", "Download");
      dl.href = `/api/archive/${encodeURIComponent(d.device_id)}/${d.day}`;
      dl.setAttribute("download", d.file);
      const rp = UI.setLabel(el("button", "ghost mini"), "replay", "Replay");
      rp.type = "button";
      rp.onclick = () => openReplay(d);
      act.append(dl, rp);
      tr.append(act);
    });
  }
  async function loadDays() {
    try { days = await api("/api/archive"); } catch (e) { days = []; }
    const sel = $("arch-device"), cur = sel.value;
    const devs = [...new Set(days.map((d) => d.device_id))];
    sel.innerHTML = "";
    sel.appendChild(new Option("All devices", ""));
    devs.forEach((d) => sel.appendChild(new Option(d, d)));
    if (devs.includes(cur)) sel.value = cur;
    renderDays();
  }

  // ---- storage: size of each store and how long it keeps data ------------------------
  // samples_days: IMU samples + decoded packets; minute_days: their per-minute summaries (long
  // ranges on the IMU page); archive_days: these day files, 0 = forever. The outbox holds only
  // what a relay has not delivered yet.
  const KEEP = {
    samples_days: [3, 7, 14, 30, 60, 90],
    minute_days: [30, 90, 180, 365, 730],
    archive_days: [0, 7, 30, 90, 180, 365],
  };
  const fmtKeep = (v) => (v === 0 ? "Forever" : `${v} days`);
  let storage = null;

  function keepSelect(key) {
    const sel = el("select", "arch-keep");
    const opts = [...new Set([...KEEP[key], storage.retention[key]])].sort((x, y) => (x || 1e9) - (y || 1e9));
    opts.forEach((v) => sel.appendChild(new Option(fmtKeep(v), v)));
    sel.value = String(storage.retention[key]);
    sel.onchange = async () => {
      sel.disabled = true;
      try {
        storage = await api("/api/storage/retention", { method: "PUT", headers: { "content-type": "application/json" },
          body: JSON.stringify({ ...storage.retention, [key]: Number(sel.value) }) });
      } catch (e) { $("arch-free").textContent = e.message; }
      renderStorage();
    };
    return sel;
  }
  function renderStorage() {
    const t = $("arch-store"); t.innerHTML = "";
    if (!storage) return;
    const z = storage.sizes, hk = storage.last_housekeeping;
    $("arch-free").textContent = [storage.free != null ? `${fmtBytes(storage.free)} free` : "",
      hk ? `cleaned ${new Date(hk.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}` : ""]
      .filter(Boolean).join(" · ");
    const head = t.createTHead().insertRow();
    [["Store", ""], ["File", ""], ["Size", "num"], ["Keep", "fit"]].forEach(([h, cls]) => head.appendChild(el("th", cls, h)));
    const body = t.createTBody();
    const row = (name, file, size, keep) => {
      const tr = body.insertRow();
      const k = el("td", "fit"); k.append(keep);
      tr.append(el("td", "", name), el("td", "mono muted", file), el("td", "num", size), k);
    };
    row("IMU samples and packets", "telemetry.db", fmtBytes(z.telemetry), keepSelect("samples_days"));
    row("Minute summaries", "telemetry.db", "—", keepSelect("minute_days"));
    row("Packet archive", "archive/", fmtBytes(z.archive), keepSelect("archive_days"));
    row("Relay outbox", "relay_outbox.db", fmtBytes(z.outbox),
        el("span", "muted", z.outbox_queued ? `${fmtBytes(z.outbox_queued)} to deliver` : "Until delivered"));
  }
  async function loadStorage() {
    try { storage = await api("/api/storage"); } catch (e) { storage = null; }
    renderStorage();
  }

  // ---- replays ----------------------------------------------------------------
  function renderReplays() {
    const wrap = $("arch-replays-wrap"), t = $("arch-replays");
    wrap.hidden = !replays.length;
    t.innerHTML = "";
    if (!replays.length) return;
    $("arch-replays-count").textContent = `${replays.length}`;
    const head = t.createTHead().insertRow();
    [["Device", ""], ["Day", ""], ["Target", ""], ["Progress", ""], ["Status", "fit"], ["", "fit"]]
      .forEach(([h, cls]) => head.appendChild(el("th", cls, h)));
    const body = t.createTBody();
    replays.forEach((r) => {
      const tr = body.insertRow();
      const pct = r.total ? Math.round(r.sent / r.total * 100) : 0;
      const prog = el("td", "arch-prog");
      const bar = el("div", "arch-bar"); const fill = el("div", "arch-fill " + r.status);
      fill.style.width = (r.total ? pct : 0) + "%"; bar.append(fill);
      prog.append(bar, el("span", "mono", r.total == null ? "Reading"
        : `${r.sent.toLocaleString()} / ${r.total.toLocaleString()}`));
      const status = el("td", "", "");
      status.append(el("span", "arch-pill " + r.status, STATUS[r.status] || r.status));
      if (r.error) status.title = r.error;
      const act = el("td", "arch-act");
      if (r.status === "running") {
        const stop = UI.setLabel(el("button", "ghost mini danger"), "stop", "Stop");
        stop.type = "button";
        stop.onclick = async () => { try { await api(`/api/archive/replays/${r.id}/stop`, { method: "POST" }); } catch (e) { /* gone */ } loadReplays(); };
        act.append(stop);
      }
      tr.append(el("td", "mono", r.device), el("td", "mono", fmtDay(r.day)), el("td", "mono", r.target),
                prog, status, act);
    });
  }
  async function loadReplays() {
    try { replays = await api("/api/archive/replays"); } catch (e) { replays = []; }
    renderReplays();
    clearTimeout(timer);
    timer = setTimeout(loadReplays, replays.some((r) => r.status === "running") ? 1000 : 5000);
  }

  // ---- replay dialog ---------------------------------------------------------------
  async function openReplay(d) {
    let target = "";
    try { target = localStorage.getItem(TARGET_KEY) || ""; } catch (e) { /* ignore */ }
    if (!target) { try { target = (await api("/api/listen/defaults")).relay || ""; } catch (e) { /* none */ } }

    const overlay = el("div", "modal-overlay");
    const modal = el("div", "modal relay-modal");
    const head = el("div", "modal-head");
    const x = el("button", "modal-x"); x.type = "button"; x.innerHTML = UI.icons.x;
    x.setAttribute("aria-label", "Close"); x.onclick = () => overlay.remove();
    head.append(el("span", "", "Replay to platform"), x);
    const body = el("div", "relay-body");
    body.append(el("div", "arch-what", `${d.device_id} · ${fmtDay(d.day)} · ${fmtBytes(d.bytes)}`));
    const tIn = el("input", "relay-target"); tIn.placeholder = "host:port"; tIn.value = target; tIn.spellcheck = false;
    const tRow = el("label", "imu-fld", "Platform"); tRow.append(tIn);
    const rate = el("select");
    RATES.forEach(([v, label]) => rate.appendChild(new Option(label, v)));
    rate.value = "50";
    const rRow = el("label", "imu-fld", "Pace"); rRow.append(rate);
    const err = el("div", "relay-err");
    body.append(tRow, rRow,
      el("div", "arch-note", "Sent in their original order on one connection, the device's login first. "
        + "Packets the platform already has arrive again."), err);
    const foot = el("div", "modal-foot");
    const cancel = el("button", "ghost", "Cancel"); cancel.onclick = () => overlay.remove();
    const go = UI.setLabel(el("button", ""), "replay", "Start replay");
    go.type = "button";
    go.onclick = async () => {
      if (!tIn.value.trim()) { err.textContent = "Enter the platform's host:port"; tIn.focus(); return; }
      go.disabled = true;
      try {
        await api("/api/archive/replay", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ device: d.device_id, day: d.day, target: tIn.value.trim(), rate: Number(rate.value) }) });
        try { localStorage.setItem(TARGET_KEY, tIn.value.trim()); } catch (e) { /* ignore */ }
        overlay.remove();
        loadReplays();
      } catch (e) { err.textContent = e.message; go.disabled = false; }
    };
    foot.append(cancel, go);
    modal.append(head, body, foot);
    overlay.append(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.append(overlay);
    tIn.focus();
  }

  $("arch-device").addEventListener("change", renderDays);
  $("arch-refresh").addEventListener("click", () => { loadDays(); loadReplays(); loadStorage(); });
  loadStorage();
  loadDays();
  loadReplays();
})();
