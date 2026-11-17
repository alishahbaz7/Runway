/* Log analysis — upload a platform report (the drive log today), see it judged, dig into it.
 *
 *   list     the logs analysed so far (left pane): status dot, device, period, status
 *   summary  a tile per area (status, a figure), the day on a timeline, the track, the findings,
 *            delivery delay, packet types, supply voltage
 *   details  one section per part of the report (gaps, frames, GPS, delivery, power, trips, events,
 *            payload, packet types, columns) and every row, filterable by what was found on it
 *
 * Each open log is a tab (like Capture's sessions) keeping its own page; a comparison of several
 * logs — devices fitted in one vehicle — is a tab too.
 *
 * The report comes from /api/analysis/{id}; rows a page at a time from …/rows; a comparison from
 * /api/analysis/compare. Every finding, timeline marker and table row links to the rows it is about.
 */
(() => {
  "use strict";
  const UI = window.RunwayUI;
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let d = "HTTP " + r.status;
      try { d = (await r.json()).detail || d; } catch (e) { /* not JSON */ }
      throw new Error(d);
    }
    return r.json();
  };
  const svgNS = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs, parent) => {
    const e = document.createElementNS(svgNS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const pad = (n) => String(n).padStart(2, "0");
  const num = (x, d) => (x == null || x === "" ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 1 : d }));
  const dt = (iso) => (iso ? new Date(iso) : null);
  // 24-hour; the date only when the day differs from the log's first day
  let day0 = "";
  const when = (iso, withDate) => {
    const d = dt(iso);
    if (!d || isNaN(d)) return "—";
    const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    const ds = `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
    return withDate || ds !== day0 ? `${ds} ${t}` : t;
  };
  // "07:53:32 → 08:31:54", with the date once in front when it is not the log's first day
  const range = (a, b, withDate) => {
    const da = dt(a), db = dt(b);
    if (!da || !db) return `${when(a)} → ${when(b)}`;
    const day = (d) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
    const t = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    if (day(da) !== day(db)) return `${when(a, true)} → ${when(b, true)}`;
    return (withDate || day(da) !== day0 ? `${day(da)} · ` : "") + `${t(da)} → ${t(db)}`;
  };
  const dur = (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return s + " s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " min" + (s % 60 && m < 10 ? " " + (s % 60) + " s" : "");
    return Math.floor(m / 60) + " h " + (m % 60) + " min";
  };
  const TONE = { ok: "ok", info: "info", warn: "warn", bad: "bad", none: "none" };
  const LEVEL_WORD = { ok: "Good", info: "Note", warn: "Attention", bad: "Poor" };
  const AREA_ICON = { data: "list", delivery: "send", gps: "satellite", power: "power", driving: "route", device: "device", payload: "chip" };
  const FLAG = {
    loss: ["After lost data", "bad"], gap: ["After a gap", "info"], stale: ["Stale position", "bad"], spike: ["Spike", "bad"],
    jump: ["Jump", "warn"], frozen: ["Repeated position", "warn"], speed: ["Speed differs", "warn"],
    nofix: ["No satellites", "warn"], noposition: ["No position", "warn"], late: ["Arrived late", "warn"],
    history: ["History", "info"], history_mismatch: ["History flag differs", "warn"], frames: ["Frames lost before", "bad"],
    clock: ["Clock ahead", "warn"], event: ["Driving event", "info"], overspeed: ["Overspeed", "warn"],
    restart: ["Restart", "bad"], alert: ["Device alert", "info"], alert_mismatch: ["Alert disagrees", "warn"],
    flicker: ["Ignition flicker", "warn"], start_dip: ["Engine start", "info"], very_late: ["Over 10 min late", "warn"],
    deep_sleep: ["Deep sleep", "info"], main_missing: ["Main battery missing", "bad"],
    internal_missing: ["Internal battery missing", "bad"], internal_low: ["Internal battery low", "warn"],
  };
  const SETTING_LABELS = {
    overspeed_kmh: "Overspeed above (km/h)", moving_kmh: "Moving above (km/h)", trip_stop_min: "A stop ends a trip after (min)",
    gap_min_s: "A gap is at least (s)", jump_kmh: "A jump is faster than (km/h)", jump_min_m: "… and longer than (m)",
    frozen_kmh: "Repeated position counts above (km/h)", speed_diff_kmh: "Speeds differ by more than (km/h)",
    parked_motion_ms2: "Movement while parked above (m/s²)",
    margin_20s: "On time within (s) — every 20 s or less", margin_2min: "… every 2 min or less (s)",
    margin_10min: "… every 10 min or less (s)", margin_over: "… longer intervals (s)", enough_pct: "Enough packets from (% of due)",
    too_few_pct: "Too few packets below (% of due)", too_many_pct: "Too many packets above (% of due)",
  };

  let items = [];
  let rep = null;         // the report on screen: the whole log's, or one trip's
  let full = null;        // the whole log's report (the map and the trip chips always read it)
  let scope = null;       // the trip the page shows ({n, start, end}), or null for the whole log
  let current = null;     // its id
  let view = "summary";
  let section = "findings";
  let map = null, mapLayers = null;
  let settings = {}, defaults = {}, profiles = [];
  const rowsState = { offset: 0, limit: 200, flag: "", type: "", at: null, time: null };

  // ---- URL state: ?id=…&view=details&section=gps ----------------------------------------------
  function readUrl() {
    const p = new URLSearchParams(location.search);
    return { id: p.get("id"), view: p.get("view"), section: p.get("section"), trip: Number(p.get("trip")) || null,
             compare: p.get("compare") };
  }
  function writeUrl() {
    const p = new URLSearchParams();
    const t = tabOf(focusKey);
    if (t && t.kind === "compare") {
      history.replaceState(null, "", location.pathname + "?compare=" + t.ids.map(encodeURIComponent).join(","));
      return;
    }
    if (current) p.set("id", current);
    if (scope) p.set("trip", scope.n);
    if (view !== "summary") p.set("view", view);
    if (view === "details" && section !== "findings") p.set("section", section);
    history.replaceState(null, "", location.pathname + (p.toString() ? "?" + p : ""));
  }

  // ---- the list ---------------------------------------------------------------------------------
  async function loadList(selectId) {
    try {
      const d = await api("/api/analysis");
      items = d.items || [];
      settings = d.settings || {};
      defaults = d.defaults || {};
      profiles = d.profiles || [];
    } catch (e) {
      items = [];
    }
    renderList();
    const has = (id) => items.some((i) => i.id === id);
    // a tab whose log is gone closes
    tabs = tabs.filter((t) => (t.kind === "log" ? has(t.id) : t.ids.every(has)));
    if (focusKey && !tabOf(focusKey)) { focusKey = null; current = null; rep = full = null; }
    if (selectId && has(selectId)) return openLog(selectId, true);
    if (!restored) {
      restored = true;
      const u = readUrl(), saved = readTabs() || {};
      (saved.tabs || []).forEach((x) => {
        if (x.kind === "log" && has(x.id) && !tabOf("log:" + x.id)) tabs.push({ key: "log:" + x.id, kind: "log", id: x.id });
        if (x.kind === "compare" && (x.ids || []).every(has) && !tabOf("cmp:" + x.ids.join(","))) tabs.push({ key: "cmp:" + x.ids.join(","), kind: "compare", ids: x.ids });
      });
      if (u.compare) {
        const ids = u.compare.split(",").filter(Boolean);
        if (ids.length > 1 && ids.every(has)) return openCompare(ids);
      }
      if (u.id && has(u.id)) return openLog(u.id);
      if (saved.focus && tabOf(saved.focus)) return focusTab(saved.focus);
      if (tabs.length) return focusTab(tabs[0].key);
      if (items.length) return openLog(items[0].id);
      return showDrop();
    }
    if (focusKey) { renderTabs(); return; }
    if (tabs.length) return focusTab(tabs[tabs.length - 1].key);
    renderTabs();
    showDrop();
  }
  function periodText(i) {
    const a = dt(i.first), b = dt(i.last);
    if (!a) return "";
    const ds = `${pad(a.getDate())}-${pad(a.getMonth() + 1)}-${a.getFullYear()}`;
    const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return `${ds} ${hm(a)}–${b ? hm(b) : ""}`;
  }
  // the list: click a log to open it; its bin deletes it; Select ticks several (or all) to delete together
  let selecting = false;
  const selected = new Set();
  function renderList() {
    const ul = $("an-list");
    ul.innerHTML = "";
    $("an-count").textContent = items.length ? String(items.length) : "";
    $("an-select").hidden = !items.length || selecting;
    $("an-selbar").hidden = !selecting;
    [...selected].forEach((id) => { if (!items.some((i) => i.id === id)) selected.delete(id); });
    paintSelbar();
    if (!items.length) {
      ul.appendChild(el("li", "empty", "No logs yet"));
      return;
    }
    items.forEach((i) => {
      const li = el("li", "an-item tone-" + (TONE[i.status] || "none") + (i.id === current ? " active" : "") +
                    (selecting ? " selecting" : "") + (selected.has(i.id) ? " picked" : ""));
      if (selecting) {
        const cb = el("input", "an-item-cb");
        cb.type = "checkbox";
        cb.checked = selected.has(i.id);
        cb.setAttribute("aria-label", "Select " + (i.device || i.file));
        cb.addEventListener("click", (e) => e.stopPropagation());
        cb.addEventListener("change", () => toggle(i.id, cb.checked));
        li.appendChild(cb);
      } else {
        li.appendChild(el("span", "an-item-dot"));
      }
      li.appendChild(el("span", "an-item-dev", i.device || i.file));
      const pill = el("span", "ui-pill sm tone-" + (TONE[i.status] || "none"), i.status_word || "—");
      li.appendChild(pill);
      li.appendChild(el("span", "an-item-when", periodText(i) + (i.issues ? ` · ${i.issues} issue${i.issues === 1 ? "" : "s"}` : "")));
      if (!selecting) {
        const del = el("button", "ui-icon-btn an-item-del");
        del.type = "button";
        del.innerHTML = UI.icons.trash;
        del.title = "Delete";
        del.setAttribute("aria-label", "Delete " + (i.device || i.file));
        del.addEventListener("click", (e) => { e.stopPropagation(); removeLogs([i.id]); });
        li.appendChild(del);
      }
      li.title = i.file;
      li.addEventListener("click", () => (selecting ? toggle(i.id, !selected.has(i.id)) : openLog(i.id)));
      ul.appendChild(li);
    });
  }
  function toggle(id, on) {
    if (on) selected.add(id); else selected.delete(id);
    renderList();
  }
  function paintSelbar() {
    const n = selected.size;
    $("an-selcount").textContent = n === items.length && n ? `All ${n} selected` : `${n} selected`;
    const all = $("an-selall");
    all.checked = !!items.length && n === items.length;
    all.indeterminate = n > 0 && n < items.length;
    $("an-seldel").disabled = !n;
    $("an-selcmp").disabled = n < 2 || n > 8;
  }
  function setSelecting(on) {
    selecting = on;
    if (!on) selected.clear();
    renderList();
  }
  $("an-select").addEventListener("click", () => setSelecting(true));
  $("an-selcancel").addEventListener("click", () => setSelecting(false));
  $("an-selall").addEventListener("change", (e) => {
    selected.clear();
    if (e.target.checked) items.forEach((i) => selected.add(i.id));
    renderList();
  });
  $("an-seldel").addEventListener("click", () => removeLogs([...selected]));
  // compare the chosen logs, in the order they were ticked: the first is the reference positions are
  // measured from (select all: the list's order)
  $("an-selcmp").addEventListener("click", () => {
    const ids = [...selected].filter((id) => items.some((i) => i.id === id));
    setSelecting(false);
    openCompare(ids);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && selecting && !document.querySelector("dialog[open]")) setSelecting(false); });
  // delete one, several or every log — always asked first
  async function removeLogs(ids) {
    if (!ids.length) return;
    const every = ids.length === items.length && items.length > 1;
    const names = ids.map((id) => { const i = items.find((x) => x.id === id) || {}; return i.device || i.file || id; });
    const title = ids.length === 1 ? "Delete this analysis?" : every ? `Delete all ${ids.length} analyses?` : `Delete ${ids.length} analyses?`;
    const text = (ids.length <= 6 ? names.join(", ") : `${names.slice(0, 5).join(", ")} and ${ids.length - 5} more`) +
      ` — the uploaded file${ids.length === 1 ? "" : "s"} and ${ids.length === 1 ? "its report" : "their reports"}.`;
    const ok = await UI.confirm({ title, text, action: every ? "Delete all" : "Delete", tone: "danger" });
    if (!ok) return;
    try {
      await api("/api/analysis/delete", { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(every ? { all: true } : { ids }) });
    } catch (err) {
      await UI.notice({ title: "Not deleted", text: err.message });
      return;
    }
    selecting = false;
    selected.clear();
    await loadList();
  }

  // ---- upload: the button, the drop zone, dropping anywhere on the work area -----------------------
  function showDrop() {
    rep = null; current = null;
    $("an-report").hidden = true;
    $("an-compare").hidden = true;
    renderRail();
    $("an-busy").hidden = true;
    $("an-drop").hidden = false;
    writeUrl();
  }
  function upload(file) {
    if (!file) return;
    $("an-drop").hidden = true;
    $("an-busy").hidden = false;
    $("an-busy-t").textContent = "Uploading " + file.name;
    $("an-busy-bar").style.width = "0%";
    $("an-busy-pct").textContent = "";
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    fd.append("file", file, file.name);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((100 * e.loaded) / e.total);
      $("an-busy-bar").style.width = p + "%";
      $("an-busy-pct").textContent = p + "%";
      if (p >= 100) { $("an-busy-t").textContent = "Analysing " + file.name; $("an-busy-pct").textContent = ""; }
    };
    xhr.onload = async () => {
      $("an-busy").hidden = true;
      if (xhr.status >= 200 && xhr.status < 300) {
        const entry = JSON.parse(xhr.responseText);
        view = "summary";
        await loadList(entry.id);
      } else {
        let d = "HTTP " + xhr.status;
        try { d = JSON.parse(xhr.responseText).detail || d; } catch (e) { /* not JSON */ }
        await UI.notice({ title: "Not analysed", text: file.name + ": " + d });
        if (focusKey) focusTab(focusKey); else showDrop();
      }
    };
    xhr.onerror = () => { $("an-busy").hidden = true; if (focusKey) focusTab(focusKey); else showDrop(); };
    $("an-report").hidden = true;
    $("an-compare").hidden = true;
    xhr.open("POST", "/api/analysis");
    xhr.send(fd);
  }
  $("an-add").addEventListener("click", () => $("an-file").click());
  $("an-choose").addEventListener("click", () => $("an-file").click());
  $("an-file").addEventListener("change", (e) => { upload(e.target.files[0]); e.target.value = ""; });
  const work = $("an-work");
  ["dragenter", "dragover"].forEach((ev) => work.addEventListener(ev, (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault(); work.classList.add("is-drag");
  }));
  ["dragleave", "drop"].forEach((ev) => work.addEventListener(ev, (e) => {
    if (ev === "dragleave" && work.contains(e.relatedTarget)) return;
    work.classList.remove("is-drag");
  }));
  work.addEventListener("drop", (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    upload(e.dataTransfer.files[0]);
  });

  // ---- tabs: each open log, and each comparison, is a tab (like Capture's sessions) -----------------
  // A log's tab keeps its own page — the report, the trip chosen, summary or details, the section,
  // the rows' position — so going back to it finds it as it was. The tabs are remembered here.
  const TABS_KEY = "runway.analysis.tabs";
  let tabs = [];                  // {key, kind: "log", id, st} | {key, kind: "compare", ids, data}
  let focusKey = null;
  let restored = false;
  const tabOf = (key) => tabs.find((t) => t.key === key);
  function stash() {
    const t = tabOf(focusKey);
    if (t && t.kind === "log" && full && current === t.id) {
      t.st = { rep, full, scope, view, section, sectionFilter, rows: { ...rowsState }, remarks: { ...remarkShown }, mapTrip, day0, mapPoint, pointAt, mapGap,
               back: navBack, fwd: navFwd, scroll: work.scrollTop, rowsScroll: placeOf().rowsScroll };
    }
  }
  function unstash(t) {
    const st = t.st;
    current = t.id; rep = st.rep; full = st.full; scope = st.scope; view = st.view; section = st.section;
    sectionFilter = st.sectionFilter; mapTrip = st.mapTrip; day0 = st.day0; mapPoint = st.mapPoint == null ? null : st.mapPoint; pointAt = st.pointAt || null; mapGap = st.mapGap || null;
    Object.assign(rowsState, st.rows);
    Object.keys(remarkShown).forEach((x) => delete remarkShown[x]);
    Object.assign(remarkShown, st.remarks);
    navBack = st.back || []; navFwd = st.fwd || [];
    pendingPlace = { scroll: st.scroll || 0, rowsScroll: st.rowsScroll };
  }
  function saveTabs() {
    try {
      localStorage.setItem(TABS_KEY, JSON.stringify({
        tabs: tabs.map((t) => (t.kind === "log" ? { kind: "log", id: t.id } : { kind: "compare", ids: t.ids })), focus: focusKey }));
    } catch (e) { /* private browsing: tabs are not remembered */ }
  }
  function readTabs() {
    try { return JSON.parse(localStorage.getItem(TABS_KEY) || "null"); } catch (e) { return null; }
  }
  function openLog(id, fresh) {
    let t = tabOf("log:" + id);
    if (!t) {
      t = { key: "log:" + id, kind: "log", id };
      const at = tabs.findIndex((x) => x.key === focusKey);
      tabs.splice(at < 0 ? tabs.length : at + 1, 0, t);        // beside the tab on screen
    } else if (fresh) t.fresh = true;
    return focusTab(t.key);
  }
  function openCompare(ids) {
    const key = "cmp:" + ids.join(",");
    if (!tabOf(key)) tabs.push({ key, kind: "compare", ids });
    return focusTab(key);
  }
  async function focusTab(key) {
    if (mapFull) setMapFull(false);
    stash();
    focusKey = key;
    const t = tabOf(key);
    saveTabs();
    if (!t) { renderTabs(); return showDrop(); }
    if (t.fresh) { t.st = null; t.data = null; t.fresh = false; }
    if (t.kind === "compare") {
      current = null; rep = full = null;
      renderTabs(); renderList();
      return showCompare(t);
    }
    $("an-compare").hidden = true;
    if (t.st) {
      unstash(t);
      renderTabs(); renderList();
      $("an-drop").hidden = true;
      $("an-report").hidden = false;
      renderHead();
      setView(view);
      return restoreScroll();
    }
    return loadLog(t);
  }
  function closeTab(key) {
    const i = tabs.findIndex((t) => t.key === key);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (key !== focusKey) { saveTabs(); renderTabs(); return; }
    focusKey = null; current = null; rep = full = null;
    const next = tabs[i] || tabs[i - 1];
    if (next) return focusTab(next.key);
    saveTabs(); renderTabs(); renderList();
    showDrop();
  }
  function renderTabs() {
    const host = $("an-tabs");
    host.innerHTML = "";
    tabs.forEach((t) => {
      const tab = el("div", "tcp-tab" + (t.key === focusKey ? " active" : ""));
      tab.addEventListener("click", () => { if (t.key !== focusKey) focusTab(t.key); });
      if (t.kind === "log") {
        const it = items.find((i) => i.id === t.id) || {};
        tab.append(el("span", "an-tab-dot tone-" + (TONE[it.status] || "none")), el("span", "tcp-tab-port", it.device || it.file || t.id));
        const d = dt(it.first);
        if (d) tab.append(el("span", "tcp-tab-sub muted", `${pad(d.getDate())}-${pad(d.getMonth() + 1)}`));
        const sc = t.key === focusKey ? scope : t.st && t.st.scope;
        if (sc) { const tp = el("span", "an-tab-trip", `Trip ${sc.n}`); tp.style.setProperty("--c", tripColour(sc.n)); tab.append(tp); }
        tab.title = it.file || "";
      } else {
        const ic = el("span", "an-tab-ico");
        ic.innerHTML = UI.icon("columns");
        tab.append(ic, el("span", "an-tab-cmp", "Comparison"), el("span", "tcp-tab-count", String(t.ids.length)));
        tab.title = t.ids.map((id) => (items.find((i) => i.id === id) || {}).device || id).join(" · ");
      }
      const x = el("button", "tcp-tab-x");
      x.type = "button";
      x.innerHTML = UI.icon("x");
      x.title = "Close";
      x.setAttribute("aria-label", "Close");
      x.addEventListener("click", (e) => { e.stopPropagation(); closeTab(t.key); });
      tab.append(x);
      host.appendChild(tab);
    });
    const add = el("button", "tcp-tab-add");
    add.type = "button";
    add.innerHTML = UI.icon("plus");
    add.title = "Add a log";
    add.setAttribute("aria-label", "Add a log");
    add.addEventListener("click", () => $("an-file").click());
    host.appendChild(add);
  }

  // ---- open a log's report --------------------------------------------------------------------------
  async function loadLog(t) {
    const id = t.id;
    current = id;
    renderTabs();
    renderList();
    let r;
    try { r = await api("/api/analysis/" + encodeURIComponent(id)); } catch (e) { return closeTab(t.key); }
    if (focusKey !== t.key) return;                 // another tab was chosen meanwhile
    rep = full = r;
    mapTrip = null;
    mapPoint = null; pointAt = null; mapGap = null;
    scope = null;
    scopeSeq++;
    view = "summary"; section = "findings"; sectionFilter = null;
    navBack = []; navFwd = [];
    Object.assign(rowsState, { offset: 0, flag: "", type: "", at: null, time: null });
    Object.keys(remarkShown).forEach((x) => delete remarkShown[x]);
    day0 = (() => { const d = dt(rep.summary && rep.summary.first); return d ? `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}` : ""; })();
    const u = readUrl();
    if (u.id === id) { if (u.view) view = u.view; if (u.section) section = u.section; }
    $("an-drop").hidden = true;
    $("an-busy").hidden = true;
    $("an-report").hidden = false;
    renderHead();
    setView(view);
    if (u.id === id && u.trip) chooseTrip(u.trip);
  }

  // a trip chosen — above the map, on it, or in the Trips table — and the whole page (tiles,
  // findings, timeline, charts, details, Excel) shows that trip alone; the map keeps every trip, the
  // chosen one on top. The trip's report is worked out by the server from the same rows.
  let scopeSeq = 0;
  async function chooseTrip(n) {
    const t = n == null ? null : ((full.driving || {}).trips || []).find((x) => x.n === n);
    const seq = ++scopeSeq, id = current;
    mapTrip = t ? t.n : null;
    if (!t) { scope = null; rep = full; return showScope(); }
    scope = { n: t.n, start: t.start, end: t.end };
    $("an-report").classList.add("an-scoping");
    renderTripBar();
    let r;
    try {
      r = await api(`/api/analysis/${encodeURIComponent(id)}/part?` + new URLSearchParams({ start: t.start, end: t.end }));
    } catch (e) {
      if (seq !== scopeSeq) return;
      scope = null; mapTrip = null; rep = full;
      showScope();
      return UI.notice({ title: "Trip not analysed", text: e.message });
    }
    if (seq !== scopeSeq || id !== current) return;
    rep = r;
    showScope();
  }
  function showScope() {
    $("an-report").classList.remove("an-scoping");
    renderTabs();
    if (!pendingPlace) { rowsState.offset = 0; rowsState.at = null; rowsState.time = null; }
    renderHead();
    if (view === "summary") renderSummary(); else renderDetails();
    writeUrl();
  }

  function renderHead() {
    const e = rep.entry || {}, s = rep.summary || {}, m = rep.meta || {};
    $("an-title").textContent = e.device || m.device || e.file || "Log";
    const st = $("an-status");
    st.className = "ui-pill tone-" + (TONE[rep.status] || "none");
    st.textContent = rep.status_word + (rep.issues ? ` · ${rep.issues} issue${rep.issues === 1 ? "" : "s"}` : "");
    $("an-sub").textContent = [
      range(s.first, s.last, true), `${num(s.rows, 0)} rows`, s.step_s ? `every ${dur(s.step_s)}` : "",
      m.columns ? `${m.columns} columns` : "",
      (s.firmware || []).length ? `firmware ${s.firmware.join(" → ")}` : "",
    ].filter(Boolean).join(" · ");
    $("an-title").dataset.tip = e.file || "";
    const sc = $("an-scope");
    sc.hidden = !scope;
    sc.innerHTML = "";
    if (scope) {
      const sw = el("i");
      sw.style.background = tripColour(scope.n);
      const x = el("button", "an-scope-x");
      x.type = "button";
      x.title = "Whole log";
      x.setAttribute("aria-label", "Whole log");
      x.innerHTML = UI.icon("x");
      x.addEventListener("click", () => pickTrip(null));
      sc.append(sw, document.createTextNode(`Trip ${scope.n}`), x);
    }
    const q = scope ? "?" + new URLSearchParams({ start: scope.start, end: scope.end, trip: scope.n }) : "";
    $("an-xlsx").href = `/api/analysis/${encodeURIComponent(current)}/report.xlsx${q}`;
    paintNav();
    renderRail();
  }

  document.querySelectorAll(".an-views .seg").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.view === view) return;
    remember();
    setView(b.dataset.view);
  }));
  function setView(v) {
    view = v === "details" ? "details" : "summary";
    if (view !== "summary" && mapFull) setMapFull(false);
    document.querySelectorAll(".an-views .seg").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
    $("an-summary").hidden = view !== "summary";
    $("an-details").hidden = view !== "details";
    if (view === "summary") renderSummary(); else renderDetails();
    writeUrl();
  }
  function goDetails(sec, filt) {
    remember();
    section = sec || "findings";
    if (sec === "rows" && filt) {
      rowsState.flag = filt.flag || "";
      rowsState.type = filt.type != null ? String(filt.type) : "";
      rowsState.at = filt.k != null ? filt.k : null;
      rowsState.time = filt.time || null;
      rowsState.offset = 0;
    }
    sectionFilter = filt || null;
    setView("details");
    work.scrollTop = 0;
  }
  let sectionFilter = null;

  // ---- back and forward, like a file explorer: every jump (a finding, a marker, a Rows button, a
  // section, summary or details, a trip) is a move; Back returns to the place before it — the view,
  // section, rows filter and page, the trip, and how far it was scrolled. Each tab has its own.
  let navBack = [], navFwd = [];
  let pendingPlace = null;                   // a place being returned to: its scroll, set once drawn
  const NAV_MAX = 60;
  function placeOf() {
    const grid = view === "details" && section === "rows" ? document.querySelector("#an-dbody .an-table-wrap") : null;
    return { view, section, filter: sectionFilter, rows: { ...rowsState, at: null, time: null }, trip: scope ? scope.n : null,
             scroll: work.scrollTop, rowsScroll: grid ? grid.scrollTop : null };
  }
  function placeName(p) {
    const sec = (SECTIONS.find((x) => x[0] === p.section) || [, p.section])[1];
    return (p.view === "summary" ? "Summary" : sec) + (p.trip ? ` · trip ${p.trip}` : "");
  }
  function remember() {
    if (!full) return;
    navBack.push(placeOf());
    if (navBack.length > NAV_MAX) navBack.shift();
    navFwd = [];
    paintNav();
  }
  function paintNav() {
    const b = $("an-back"), f = $("an-fwd");
    b.disabled = !navBack.length;
    f.disabled = !navFwd.length;
    b.title = navBack.length ? "Back to " + placeName(navBack[navBack.length - 1]) : "Back";
    f.title = navFwd.length ? "Forward to " + placeName(navFwd[navFwd.length - 1]) : "Forward";
  }
  async function goPlace(dir) {
    const from = dir < 0 ? navBack : navFwd, to = dir < 0 ? navFwd : navBack;
    if (!full || !from.length) return;
    const p = from.pop();
    to.push(placeOf());
    view = p.view; section = p.section; sectionFilter = p.filter;
    Object.assign(rowsState, p.rows);
    pendingPlace = p;
    paintNav();
    if ((scope ? scope.n : null) !== p.trip) await chooseTrip(p.trip);        // draws the page itself
    else { renderHead(); setView(view); }
    restoreScroll();
  }
  // the scroll of a place returned to; the rows table sets its own once its page has come
  function restoreScroll() {
    const p = pendingPlace;
    if (!p || (view === "details" && section === "rows")) return;
    pendingPlace = null;
    const set = () => { work.scrollTop = p.scroll || 0; };
    requestAnimationFrame(set);
    setTimeout(set, 120);                    // again once charts drawn after the section have their height
  }
  function pickTrip(n) {
    remember();
    return chooseTrip(n);
  }
  $("an-back").addEventListener("click", () => goPlace(-1));
  $("an-fwd").addEventListener("click", () => goPlace(1));
  const logOnScreen = () => !$("an-report").hidden && !document.querySelector("dialog[open]");
  document.addEventListener("keydown", (e) => {
    if (!e.altKey || !logOnScreen() || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    goPlace(e.key === "ArrowLeft" ? -1 : 1);
  });
  // the mouse's own back and forward buttons — kept on the page instead of leaving it
  ["mousedown", "mouseup"].forEach((ev) => window.addEventListener(ev, (e) => {
    if ((e.button !== 3 && e.button !== 4) || !logOnScreen()) return;
    e.preventDefault();
    if (ev === "mouseup") goPlace(e.button === 3 ? -1 : 1);
  }));

  // ---- summary ------------------------------------------------------------------------------------
  function renderSummary() {
    renderTiles();
    renderFloatTiles();
    drawTimeline();
    drawMap();
    renderModes($("an-modes"), false);
    renderScorecard($("an-score"), "summary");
    drawLatency($("an-latency"));
    drawDelay($("an-delay"));
    drawVolts($("an-volts"));
  }

  // packets by mode — active, standby, sleep: the packets made against those due (time in the mode ÷
  // its interval), how evenly, and their delay. The summary card and the Modes section share it.
  const SOURCE_WORD = { profile: "profile", log: "this log" };
  function modeSource() {
    const p = (rep.generation || {}).profile;
    return p ? pill(`Profile · ${p.name || "unnamed"}`, "info") : pill("Learned from this log", "none");
  }
  function enoughCell(g) {
    if (g.sufficiency_pct == null) return "—";
    const box = el("div", "an-enough tone-" + (TONE[g.level] || "none"));
    const bar = el("span", "an-enough-bar");
    const fill = el("i");
    fill.style.width = Math.min(100, g.sufficiency_pct) + "%";
    bar.appendChild(fill);
    box.append(bar, el("b", null, num(g.sufficiency_pct, 1) + "%"));
    return box;
  }
  function modeCols(all) {
    const cols = [
      { label: "Mode", fmt: (g) => pill(g.label, TONE[g.level] || "none"), sort: (g) => g.label },
      { label: "Expected every", fmt: (g) => (g.interval_s ? `${dur(g.interval_s)} · ${SOURCE_WORD[g.source] || ""}` : "—"), sort: (g) => g.interval_s || 0 },
      { label: "Seen most", fmt: (g) => (g.seen_s ? dur(g.seen_s) : "—"), num: true, sort: (g) => g.seen_s || 0 },
      { label: "Packets", fmt: (g) => (g.expected != null ? `${num(g.packets, 0)} of ${num(g.expected, 0)}` : num(g.packets, 0)), num: true, sort: (g) => g.packets },
      { label: "Enough", fmt: enoughCell, sort: (g) => g.sufficiency_pct || 0 },
      { label: "On time", fmt: (g) => (g.on_time_pct == null ? "—" : num(g.on_time_pct, 0) + "%"), num: true, sort: (g) => g.on_time_pct || 0 },
      { label: "Delay median", fmt: (g) => dur(g.delay_median_s), num: true, sort: (g) => g.delay_median_s || 0 },
      { label: "Delay 90%", fmt: (g) => dur(g.delay_p90_s), num: true, sort: (g) => g.delay_p90_s || 0 },
    ];
    if (all) {
      cols.splice(3, 0, { label: "Time", fmt: (g) => dur(g.seconds), num: true, sort: (g) => g.seconds },
                  { label: "Times", key: "periods", num: true });
      cols.splice(cols.length - 2, 0, { label: "Early", key: "early", num: true }, { label: "Late", key: "late", num: true },
                  { label: "Longest step", fmt: (g) => dur(g.longest_s), num: true, sort: (g) => g.longest_s || 0 });
    }
    return cols;
  }
  function profilesBtn() {
    const b = el("button", "ghost mini");
    b.type = "button";
    UI.setLabel(b, "settings", "Device profiles");
    b.addEventListener("click", (e) => { e.stopPropagation(); openSettings(true); });
    return b;
  }
  function renderModes(box, all, target) {
    box.innerHTML = "";
    const src = $("an-modes-src");
    if (src && !all) { src.innerHTML = ""; src.append(modeSource(), profilesBtn()); }
    renderModePanels(box, target);
    if (all) {
      const t = el("div", "an-subhead", "Every figure");
      box.appendChild(t);
      grid(box, modeCols(true), (rep.generation || {}).modes || [],
           { empty: "No ignition state to tell the modes apart", target: target ? (g) => g.mode === target : null });
    }
  }

  // one panel per mode: packets made against due, live and history, how evenly they were made
  // (early / on time / late / missed), the interval and the delay (average, median, min, max), notes
  const MODE_LIST = [["active", "Active"], ["standby", "Standby"], ["sleep", "Sleep"]];
  const NOT_SEEN = { active: "Never active in this log", standby: "No standby in this log", sleep: "Never asleep in this log" };
  const signedDur = (v) => (v == null ? "—" : v < 0 ? "−" + dur(-v) : dur(v));
  function stackBar(parts) {
    const total = parts.reduce((a, p) => a + (p[1] || 0), 0);
    const bar = el("div", "an-mbar");
    const lg = el("div", "an-mlegend");
    parts.forEach(([label, n, tone]) => {
      if (!n) return;
      const seg = el("i", "tone-" + tone);
      seg.style.flexGrow = String(n);
      seg.title = `${label} ${num(n, 0)}`;
      bar.appendChild(seg);
      const it = el("span");
      const sw = el("i", "tone-" + tone);
      it.append(sw, document.createTextNode(`${label} ${num(n, 0)}`));
      lg.appendChild(it);
    });
    if (!total) bar.classList.add("none");
    return [bar, lg];
  }
  const tipped = (node, tip) => { node.dataset.tip = tip; return node; };
  const MODE_ABOUT = {
    active: "Active: the ignition is on — the device reports at its fastest rate.",
    standby: "Standby: the ignition went off; the device stays awake for a while, reporting more slowly.",
    sleep: "Sleep: after standby, or after its deep-sleep packet — the slowest rate, to save energy.",
  };
  const STAT_ABOUT = {
    "Interval between packets": "The time from one packet to the next in this mode — average, median, shortest and longest; 90% within, the spread (standard deviation) and the most common step below.",
    "Delay to InTouch": "From the device making a packet to it being stored on InTouch (stored time less device time) — average, median, shortest and longest; live and history apart below.",
  };
  function statRow(title, st, fmt, foot) {
    const box = el("div", "an-mstat");
    box.appendChild(tipped(el("div", "an-mstat-t", title), STAT_ABOUT[title] || ""));
    const g = el("div", "an-mstat-g");
    [["avg", "average"], ["median", "median"], ["min", "min"], ["max", "max"]].forEach(([k, l]) => {
      const c = el("div");
      c.append(el("b", null, st && st[k] != null ? fmt(st[k]) : "—"), el("span", null, l));
      g.appendChild(c);
    });
    box.appendChild(g);
    if (foot) box.appendChild(el("div", "an-mstat-foot", foot));
    return box;
  }
  function renderModePanels(box, target) {
    const gen = rep.generation || {};
    const byMode = Object.fromEntries((gen.modes || []).map((g) => [g.mode, g]));
    const wrap = el("div", "an-mpanels");
    MODE_LIST.forEach(([m, label]) => {
      const g = byMode[m];
      const p = el("div", "an-mpanel" + (g ? "" : " empty") + (target === m ? " is-target" : ""));
      const head = el("div", "an-mpanel-head");
      const mp = pill(label, g ? TONE[g.level] || "none" : "none");
      mp.dataset.tip = MODE_ABOUT[m];
      head.appendChild(mp);
      const every = (gen.values || {})[m + "_s"], src = (gen.source || {})[m + "_s"];
      head.appendChild(el("span", "an-mpanel-every", every ? `every ${dur(every)} · ${SOURCE_WORD[src] || ""}` : "no interval known"));
      p.appendChild(head);
      if (!g) { p.appendChild(el("div", "an-empty", NOT_SEEN[m])); wrap.appendChild(p); return; }
      // made against due
      const pk = el("div", "an-mpk");
      pk.dataset.tip = "Packets made in this mode against those due: the time in the mode divided by its interval, plus one for each stretch. Every packet counts, whatever its type.";
      pk.append(el("b", null, num(g.packets, 0)), el("span", null, g.expected != null ? `of ${num(g.expected, 0)} due` : "packets"));
      p.appendChild(pk);
      if (g.sufficiency_pct != null) p.appendChild(enoughCell(g));
      if (g.diff != null) {
        p.appendChild(el("div", "an-mpanel-diff tone-" + (TONE[g.level] || "none"),
          g.diff > 0 ? `${num(g.diff, 0)} more than due` : g.diff < 0 ? `${num(-g.diff, 0)} short${g.missed ? ` · about ${num(g.missed, 0)} missed` : ""}` : "As many as due"));
      }
      // live and history
      const lh = el("div", "an-mstat");
      lh.appendChild(tipped(el("div", "an-mstat-t", "Live and history"),
        "Live: sent as it was made. History: kept on the device while it could not send, and sent later."));
      lh.append(...stackBar([["Live", g.live, "ok"], ["History", g.history, "info"]]));
      p.appendChild(lh);
      // how evenly
      if (every) {
        const ev = el("div", "an-mstat");
        ev.appendChild(tipped(el("div", "an-mstat-t", `Steps against ${dur(every)}` + (g.tolerance_s ? ` ± ${dur(g.tolerance_s)}` : "")),
          "Each step between one packet and the next against the interval: early (shorter than the interval less the margin), on time (within the margin), late (up to one and a half intervals) or missed (longer). The margin grows with the interval — Settings."));
        ev.append(...stackBar([["Early", g.early, "info"], ["On time", g.on_time, "ok"], ["Late", g.late, "warn"], ["Missed", g.missed_steps, "bad"]]));
        p.appendChild(ev);
      }
      const gs = g.gen || {};
      p.appendChild(statRow("Interval between packets", gs, dur,
        [gs.p90 != null ? `90% within ${dur(gs.p90)}` : "", gs.jitter != null ? `spread ±${dur(gs.jitter)}` : "", g.seen_s ? `most often ${dur(g.seen_s)}` : ""].filter(Boolean).join(" · ")));
      const dl = g.delay || {};
      p.appendChild(statRow("Delay to InTouch", dl, signedDur,
        [dl.p90 != null ? `90% within ${dur(dl.p90)}` : "",
         (g.delay_live || {}).median != null && g.history ? `live ${signedDur(g.delay_live.median)}` : "",
         (g.delay_history || {}).median != null ? `history ${signedDur(g.delay_history.median)}` : ""].filter(Boolean).join(" · ")));
      p.appendChild(el("div", "an-mpanel-foot", g.seconds
        ? `${dur(g.seconds)} in ${num(g.periods, 0)} stretch${g.periods === 1 ? "" : "es"}` +
          (g.stretch_avg_s != null && g.periods > 1 ? `, ${dur(g.stretch_avg_s)} each on average` : "")
        : `${num(g.periods, 0)} time${g.periods === 1 ? "" : "s"}, ${g.packets === g.periods ? "one packet each" : "no time between packets"}`));
      if ((g.notes || []).length) {
        const ul = el("ul", "an-mnotes");
        g.notes.forEach((t) => ul.appendChild(el("li", null, t)));
        p.appendChild(ul);
      }
      wrap.appendChild(p);
    });
    box.appendChild(wrap);
  }

  // ---- the side rail: tabs like bookmarks — the findings, the scorecard, the insights, the packet chosen on
  // the map. One open at a time, folded at first. They follow what is chosen: the whole log, a trip, a
  // packet. The panel widens and narrows from its inner edge.
  const RAIL = { findings: ["Findings", "alert"], score: ["Scorecard", "check"], insight: ["Insights", "info"], packet: ["Packet", "file"] };
  let railTab = null;
  function renderRail() {
    const rail = $("an-rail");
    const show = !!full && !$("an-report").hidden;
    const was = rail.classList.contains("is-open");
    rail.hidden = !show;
    document.querySelectorAll(".an-rail-tab").forEach((b) => {
      b.classList.toggle("on", show && b.dataset.rail === railTab);
      b.setAttribute("aria-selected", String(show && b.dataset.rail === railTab));
    });
    const issues = show ? (rep.findings || []).filter((f) => f.level === "warn" || f.level === "bad").length : 0;
    $("an-rail-n").textContent = issues ? String(issues) : "";
    const open = show && !!railTab;
    $("an-rail-panel").hidden = !open;
    rail.classList.toggle("is-open", open);
    if (was !== open) window.dispatchEvent(new Event("resize"));     // the map and charts take the room left
    fitFull();
    if (!open) return;
    const [title] = RAIL[railTab];
    $("an-rail-title").textContent = title;
    const hop = mapGap && (mapGap._type === "jump" || mapGap._type === "stale");
    if (railTab === "packet" && mapGap) $("an-rail-title").textContent = hop ? (mapGap._type === "stale" ? "Stale position" : "Jump") : "Missing data";
    $("an-rail-scope").textContent = railTab === "packet" ? (mapGap ? (hop ? distText(mapGap.metres) : mapGap.duration) : mapPoint != null ? `Row ${num(mapPoint + 1, 0)}` : "")
      : scope ? `Trip ${scope.n}` : "Whole log";
    const body = $("an-rail-body");
    if (railTab === "findings") {
      body.innerHTML = "";
      const wrap = el("div");
      const ul = el("ul", "an-findings");
      ul.style.maxHeight = "none";
      wrap.appendChild(ul);
      body.appendChild(wrap);
      renderFindings(ul, rep.findings.filter((f) => f.level !== "ok"), true, "summary");
    } else if (railTab === "score") { body.innerHTML = ""; const h = el("div"); body.appendChild(h); renderScorecard(h, "rail", true); }
    else if (railTab === "insight") renderInsights(body);
    else renderPacket(body);
  }
  document.querySelectorAll(".an-rail-tab").forEach((b) => b.addEventListener("click", () => {
    railTab = railTab === b.dataset.rail ? null : b.dataset.rail;
    renderRail();
  }));
  $("an-rail-close").addEventListener("click", () => { railTab = null; renderRail(); });
  UI.resizable($("an-rail-panel"), { key: "analysis-rail", edge: "left", host: $("an-main"), cssVar: "--an-rail-w",
                                     min: 300, max: 720, keep: 640, title: "the side panel" });

  // insights: how each area stands, and what the modes' figures say
  function renderInsights(box) {
    box.innerHTML = "";
    const head = el("div", "an-ins-head");
    head.append(pill(rep.status_word + (rep.issues ? ` · ${rep.issues} issue${rep.issues === 1 ? "" : "s"}` : ""), TONE[rep.status] || "none"),
                el("span", "an-ins-when", `${when((rep.summary || {}).first, true)} – ${when((rep.summary || {}).last)}`));
    box.appendChild(head);
    const areas = el("div", "an-ins-areas");
    (rep.areas || []).forEach((a) => {
      const row = el("button", "an-ins-area");
      row.type = "button";
      const ic = el("span", "an-ins-ico");
      ic.innerHTML = UI.icon(AREA_ICON[a.id] || "info");
      row.append(ic, el("span", "an-ins-label", a.label), el("span", "an-ins-fig", a.figure || "—"), pill(a.status_word, TONE[a.status] || "none"));
      if (a.about) row.dataset.tip = a.about;
      row.addEventListener("click", () => goDetails("findings", { area: a.id }));
      areas.appendChild(row);
    });
    box.appendChild(areas);
    const modes = ((rep.generation || {}).modes || []).filter((g) => (g.notes || []).length);
    if (modes.length) {
      box.appendChild(el("div", "an-subhead", "Packets by mode"));
      modes.forEach((g) => {
        const m = el("div", "an-ins-mode");
        m.appendChild(pill(g.label, TONE[g.level] || "none"));
        const ul = el("ul", "an-mnotes");
        g.notes.forEach((t) => ul.appendChild(el("li", null, t)));
        m.appendChild(ul);
        box.appendChild(m);
      });
    }
  }

  // the packet chosen on the map: what it says, where it sits among its neighbours, every column
  let packetSeq = 0;
  // a stretch of missing data: when and where it began and ended, how long, how far apart
  const GAP_KIND = { loss: ["Data lost", "bad", "ignition on"], sleep: ["Asleep", "none", "ignition off"], unknown: ["No ignition state", "warn", ""] };
  function renderGap(box, g) {
    box.innerHTML = "";
    const [word, tone, ign] = GAP_KIND[g.kind] || GAP_KIND.unknown;
    const tags = el("div", "an-flags an-pk-tags");
    tags.appendChild(pill(word, tone));
    if (ign) tags.appendChild(pill(ign[0].toUpperCase() + ign.slice(1), "none"));
    const tn = tripAt(g.k, (full.driving || {}).trips || []) || tripAt(g.k_from, (full.driving || {}).trips || []);
    if (tn) { const tp = el("span", "an-tab-trip", `Trip ${tn}`); tp.style.setProperty("--c", tripColour(tn)); tags.appendChild(tp); }
    box.appendChild(tags);
    const kmh = g.seconds > 0 && g.metres != null ? (g.metres / g.seconds) * 3.6 : null;
    const vals = (full.generation || {}).values || {};
    const every = g.kind === "loss" ? vals.active_s : null;
    const missed = every ? Math.max(0, Math.round(g.seconds / every) - 1) : null;
    const head = el("dl", "an-pk-kv");
    [["Duration", g.duration || dur(g.seconds)], ["Apart", distText(g.metres)],
     ["Average speed if driven straight", kmh == null ? "—" : num(kmh, 0) + " km/h"],
     ["Packets missing (about)", missed == null ? "—" : `${num(missed, 0)} at every ${dur(every)}`]]
      .forEach(([a, b]) => head.append(el("dt", null, a), el("dd", null, b)));
    box.appendChild(head);
    [["Start — the last packet before", g.start, g.from, g.k_from], ["End — the first packet after", g.end, g.to, g.k]].forEach(([label, t, q, k]) => {
      const sec = el("div", "an-gap-end");
      sec.appendChild(el("div", "an-mstat-t", label));
      const dl = el("dl", "an-pk-kv flat");
      dl.append(el("dt", null, "Time"), el("dd", null, when(t, true)), el("dt", null, "Position"), el("dd", null, pos(q)));
      sec.appendChild(dl);
      const b = el("button", "ghost mini");
      b.type = "button";
      UI.setLabel(b, "file", "This packet");
      b.addEventListener("click", () => selectPoint(k, q));
      sec.appendChild(b);
      box.appendChild(sec);
    });
    const acts = el("div", "an-pk-acts");
    const go = el("button", "ghost mini");
    go.type = "button";
    UI.setLabel(go, "file", "Rows");
    go.addEventListener("click", () => goDetails("rows", { k: g.k }));
    const ctr = el("button", "ghost mini");
    ctr.type = "button";
    UI.setLabel(ctr, "map", "Show on map");
    ctr.addEventListener("click", () => {
      if (view !== "summary") { remember(); setView("summary"); }
      setTimeout(() => { if (map) { map.fitBounds([g.from, g.to], { padding: [60, 60], maxZoom: 17 }); markPoint(); } }, 120);
    });
    acts.append(go, ctr);
    box.appendChild(acts);
  }

  // a jump or a stale position: from and to (each its packet), how far, in how long, how fast that would be
  function renderHop(box, x) {
    box.innerHTML = "";
    const stale = x._type === "stale";
    const tags = el("div", "an-flags an-pk-tags");
    tags.appendChild(pill(stale ? "Stale position" : "Jump", stale ? "bad" : "warn"));
    const tn = tripAt(x.k, (full.driving || {}).trips || []) || tripAt(x.k_from, (full.driving || {}).trips || []);
    if (tn) { const tp = el("span", "an-tab-trip", `Trip ${tn}`); tp.style.setProperty("--c", tripColour(tn)); tags.appendChild(tp); }
    box.appendChild(tags);
    const kv = el("dl", "an-pk-kv");
    [["Apart", distText(x.metres)], ["In", x.seconds == null ? "—" : dur(x.seconds)],
     ["Speed it would take", x.kmh == null ? "—" : num(x.kmh, 0) + " km/h"],
     ["Satellites", (x.sats || []).map((v) => (v == null ? "—" : v)).join(" → ") || "—"],
     stale && x.stale ? ["The stale fix", pos(x.stale)] : null].filter(Boolean)
      .forEach(([a, b]) => kv.append(el("dt", null, a), el("dd", null, b)));
    box.appendChild(kv);
    [["From — the packet before", x.from_time, x.from, x.k_from], ["To — the packet after", x.time, x.to, x.k]].forEach(([label, t, q, k]) => {
      const sec = el("div", "an-gap-end");
      sec.appendChild(el("div", "an-mstat-t", label));
      const dl = el("dl", "an-pk-kv flat");
      dl.append(el("dt", null, "Time"), el("dd", null, when(t, true)), el("dt", null, "Position"), el("dd", null, pos(q)));
      sec.appendChild(dl);
      if (k != null) {
        const b = el("button", "ghost mini");
        b.type = "button";
        UI.setLabel(b, "file", "This packet");
        b.addEventListener("click", () => selectPoint(k, q));
        sec.appendChild(b);
      }
      box.appendChild(sec);
    });
    const acts = el("div", "an-pk-acts");
    const go = el("button", "ghost mini");
    go.type = "button";
    UI.setLabel(go, "file", "Rows");
    go.addEventListener("click", () => goDetails("rows", { k: x.k }));
    const ctr = el("button", "ghost mini");
    ctr.type = "button";
    UI.setLabel(ctr, "map", "Show on map");
    ctr.addEventListener("click", () => {
      if (view !== "summary") { remember(); setView("summary"); }
      setTimeout(() => { if (map) { map.fitBounds([x.from, x.to], { padding: [60, 60], maxZoom: 17 }); markPoint(); } }, 120);
    });
    acts.append(go, ctr);
    box.appendChild(acts);
  }

  async function renderPacket(box) {
    box.innerHTML = "";
    if (mapGap && (mapGap._type === "jump" || mapGap._type === "stale")) return renderHop(box, mapGap);
    if (mapGap) return renderGap(box, mapGap);
    if (mapPoint == null) { box.appendChild(el("div", "an-empty", "No packet chosen")); return; }
    const k = mapPoint, id = current, seq = ++packetSeq;
    box.appendChild(el("div", "an-empty", "Reading row " + num(k + 1, 0)));
    let page, one;
    try {
      [page, one] = await Promise.all([api(`/api/analysis/${encodeURIComponent(id)}/rows?at=${k}&limit=41`),
                                       api(`/api/analysis/${encodeURIComponent(id)}/rows/${k}`)]);
    } catch (e) { if (seq === packetSeq) { box.innerHTML = ""; box.appendChild(el("div", "an-empty", e.message)); } return; }
    if (seq !== packetSeq || id !== current || k !== mapPoint) return;
    box.innerHTML = "";
    const i = page.rows.findIndex((x) => x.k === k);
    const r = page.rows[i], prev = i > 0 ? page.rows[i - 1] : null;
    if (!r) { box.appendChild(el("div", "an-empty", "No such row")); return; }
    if (r.lat != null && r.lon != null && (r.lat || r.lon)) { pointAt = [r.lat, r.lon]; markPoint(); } else { pointAt = null; markPoint(); }
    // where: the time, a step to the packet before or after
    const nav = el("div", "an-pk-nav");
    const back = el("button", "ui-icon-btn");
    back.type = "button"; back.innerHTML = UI.icon("arrow-left"); back.title = "The packet before"; back.disabled = k <= 0;
    back.setAttribute("aria-label", "The packet before");
    back.addEventListener("click", () => selectPoint(k - 1));
    const fwd = el("button", "ui-icon-btn");
    fwd.type = "button"; fwd.innerHTML = UI.icon("arrow-right"); fwd.title = "The packet after";
    fwd.disabled = k >= ((full.summary || {}).rows || 0) - 1;
    fwd.setAttribute("aria-label", "The packet after");
    fwd.addEventListener("click", () => selectPoint(k + 1));
    nav.append(back, el("span", "an-pk-when", when(r.t, true)), fwd);
    box.appendChild(nav);
    // what it is: live or history, its mode, its trip, what was found on it
    const tags = el("div", "an-flags an-pk-tags");
    tags.appendChild(pill(r.history ? "History" : "Live", r.history ? "info" : "ok"));
    const ms = dt(r.t).getTime();
    const sp = ((full.generation || {}).spans || []).find((x) => ms >= dt(x.start).getTime() && ms <= dt(x.end).getTime());
    if (sp) tags.appendChild(pill(sp.mode[0].toUpperCase() + sp.mode.slice(1), "none"));
    const tn = tripAt(k, (full.driving || {}).trips || []);
    if (tn) { const tp = el("span", "an-tab-trip", `Trip ${tn}`); tp.style.setProperty("--c", tripColour(tn)); tags.appendChild(tp); }
    (r.flags || []).filter((f) => f !== "history").forEach((f) => tags.appendChild(flagPill(f)));   // said above
    box.appendChild(tags);
    const step = prev ? (dt(r.t) - dt(prev.t)) / 1000 : null;
    const kv = [
      ["Device time", when(r.t, true)], ["Stored", r.ins ? when(r.ins, true) : "—"],
      ["Delay to InTouch", r.delay == null ? "—" : signedDur(r.delay)], ["Since the packet before", step == null ? "—" : dur(step)],
      ["Speed", r.speed == null ? "—" : num(r.speed, 0) + " km/h"], ["Satellites", r.sats == null ? "—" : String(r.sats)],
      ["Position", r.lat == null ? "—" : `${r.lat.toFixed(6)}, ${r.lon.toFixed(6)}`], ["GSM", r.gsm == null ? "—" : String(r.gsm)],
      ["Ignition", r.ign == null ? "—" : r.ign ? "on" : "off"],
      ["Supply", r.supply == null ? "—" : (r.supply > 100 ? r.supply / 1000 : r.supply).toFixed(1) + " V"],
      ["Frame", r.frame || "—"], ["Mark", r.mark || "—"], ["Alert", r.alert || "—"], ["Type", r.type || "—"],
    ];
    const grid_ = el("dl", "an-pk-kv");
    kv.forEach(([a, b]) => grid_.append(el("dt", null, a), el("dd", null, b)));
    box.appendChild(grid_);
    const acts = el("div", "an-pk-acts");
    const go = el("button", "ghost mini");
    go.type = "button";
    UI.setLabel(go, "file", "Rows");
    go.addEventListener("click", () => goDetails("rows", { k }));
    acts.appendChild(go);
    if (pointAt && map) {
      const ctr = el("button", "ghost mini");
      ctr.type = "button";
      UI.setLabel(ctr, "map", "Show on map");
      ctr.addEventListener("click", () => {
        if (view !== "summary") { remember(); setView("summary"); }
        setTimeout(() => { map.setView(pointAt, Math.max(map.getZoom(), 16)); markPoint(); }, 120);
      });
      acts.appendChild(ctr);
    }
    box.appendChild(acts);
    const all = el("details", "an-pk-all");
    all.appendChild(el("summary", null, "Every column"));
    const dl = el("dl", "an-row-kv");
    Object.entries(one.columns || {}).forEach(([c, v]) => { if (v !== "") dl.append(el("dt", null, c), el("dd", null, v)); });
    all.appendChild(dl);
    box.appendChild(all);
  }

  // ---- the scorecard: every measure of this log (or trip), judged against its limit --------------------
  const SCORE_SHOW = {};
  function renderScorecard(box, key, compact) {
    box.innerHTML = "";
    const rows = rep.scorecard || [];
    const bar = box.previousElementSibling && box.previousElementSibling.classList.contains("an-fchips") ? box.previousElementSibling : null;
    if (bar) bar.remove();
    if (!rows.length) { box.appendChild(el("div", "an-empty", "Nothing to score")); return; }
    const whole = scope && full && full !== rep ? Object.fromEntries((full.scorecard || []).map((x) => [x.key, x])) : null;
    // a chip per judgement: all, poor, attention, good
    const counts = { bad: 0, warn: 0, ok: 0 };
    rows.forEach((x) => { if (counts[x.level] != null) counts[x.level]++; });
    const show = SCORE_SHOW[key] || "";
    const chips = el("div", "an-fchips");
    [["", "All", rows.length], ["bad", "Poor", counts.bad], ["warn", "Attention", counts.warn], ["ok", "Good", counts.ok]].forEach(([lv, label, n]) => {
      if (lv && !n) return;
      const b = el("button", "an-chip tone-" + (lv ? TONE[lv] : "none") + (show === lv ? " on" : ""));
      b.type = "button";
      b.append(document.createTextNode(label), el("b", null, String(n)));
      b.addEventListener("click", () => { SCORE_SHOW[key] = lv; renderScorecard(box, key); });
      chips.appendChild(b);
    });
    box.parentNode.insertBefore(chips, box);
    const table = el("table", "an-cmp-table an-score-table");
    const hr = el("tr");
    hr.append(el("th", null, "Measure"), el("th", "num", scope ? `Trip ${scope.n}` : "Value"));
    if (whole) hr.appendChild(el("th", "num", "Whole log"));
    if (!compact) hr.appendChild(el("th", null, "Attention · poor at"));
    const thead = el("thead");
    thead.appendChild(hr);
    const tbody = el("tbody");
    let group = null;
    rows.filter((x) => !show || x.level === show).forEach((x) => {
      if (x.group !== group) {
        group = x.group;
        const gr = el("tr", "an-cmp-group");
        const td = el("td", null, group);
        td.colSpan = (whole ? 3 : 2) + (compact ? 0 : 1);
        gr.appendChild(td);
        tbody.appendChild(gr);
      }
      const tr = el("tr");
      const name = el("td", null, x.label);
      if (x.about) name.dataset.tip = x.about;
      tr.appendChild(name);
      const val = el("td", "num" + (x.level && x.level !== "info" ? " lv-" + x.level : ""), fmtMetric(x.value, x.unit));
      if (compact && x.limit) val.title = "Attention · poor at " + x.limit;
      tr.appendChild(val);
      if (whole) {
        const w = whole[x.key];
        tr.appendChild(el("td", "num dim" + (w && w.level && w.level !== "info" && w.level !== "ok" ? " lv-" + w.level + " soft" : ""), w ? fmtMetric(w.value, w.unit) : "—"));
      }
      if (!compact) tr.appendChild(el("td", "an-score-limit", x.limit || ""));
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    box.appendChild(table);
  }

  function renderTiles() {
    const box = $("an-tiles");
    box.innerHTML = "";
    rep.areas.forEach((a) => {
      const t = el("button", "an-tile tone-" + (TONE[a.status] || "none"));
      t.type = "button";
      const top = el("div", "an-tile-top");
      const lab = el("span", "an-tile-label");
      lab.innerHTML = UI.icon(AREA_ICON[a.id] || "info");
      lab.appendChild(document.createTextNode(a.label));
      top.appendChild(lab);
      const p = el("span", "ui-pill sm", a.status_word + (a.issues ? " · " + a.issues : ""));
      top.appendChild(p);
      t.appendChild(top);
      t.appendChild(el("div", "an-tile-fig", a.figure || "—"));
      t.appendChild(el("div", "an-tile-cap", a.caption || ""));
      const about = [a.about, a.tip].filter(Boolean).join(" — ");
      if (about) t.dataset.tip = about;
      t.addEventListener("click", () => goDetails("findings", { area: a.id }));
      box.appendChild(t);
    });
  }

  // the findings, grouped by their remark (Poor, Attention, Note, Good), with a button per remark to
  // show that group alone; the choice is kept per list (summary, details) while the log is open
  const REMARKS = [["bad", "Poor"], ["warn", "Attention"], ["info", "Note"], ["ok", "Good"]];
  const remarkShown = {};
  function renderFindings(ul, list, compact, key) {
    ul.innerHTML = "";
    const bar = ul.previousElementSibling && ul.previousElementSibling.classList.contains("an-fchips") ? ul.previousElementSibling : null;
    if (bar) bar.remove();
    if (!list.length) { ul.appendChild(el("li", "an-empty", "Nothing to report")); return; }
    const k = key || "list";
    const counts = Object.fromEntries(REMARKS.map(([lv]) => [lv, list.filter((f) => f.level === lv).length]));
    if (remarkShown[k] && !counts[remarkShown[k]]) remarkShown[k] = "";
    const chips = el("div", "an-fchips");
    const chip = (lv, label, n) => {
      const b = el("button", "an-chip tone-" + (lv ? TONE[lv] : "none") + ((remarkShown[k] || "") === lv ? " on" : ""));
      b.type = "button";
      b.appendChild(document.createTextNode(label));
      b.appendChild(el("b", null, String(n)));
      b.addEventListener("click", () => { remarkShown[k] = lv; renderFindings(ul, list, compact, key); });
      chips.appendChild(b);
    };
    chip("", "All", list.length);
    REMARKS.forEach(([lv, label]) => { if (counts[lv]) chip(lv, label, counts[lv]); });
    ul.parentNode.insertBefore(chips, ul);
    const area = Object.fromEntries((rep.areas || []).map((a) => [a.id, a.label]));
    const show = remarkShown[k] || "";
    REMARKS.forEach(([lv, label]) => {
      const group = list.filter((f) => f.level === lv);
      if (!group.length || (show && show !== lv)) return;
      const h = el("li", "an-fgroup tone-" + TONE[lv]);
      h.append(el("span", "an-finding-dot"), el("span", null, label), el("span", "ui-count", String(group.length)));
      ul.appendChild(h);
      group.forEach((f) => renderFinding(ul, f, compact, area));
    });
  }
  function renderFinding(ul, f, compact, area) {
    const li = el("li", "an-finding tone-" + (TONE[f.level] || "info"));
    li.appendChild(el("span", "an-finding-dot"));
    const body = el("div");
    body.appendChild(el("div", null, f.text));
    if (compact) body.appendChild(el("div", "an-finding-area", area[f.area] || f.area));
    li.appendChild(body);
    const go = el("span");
    go.innerHTML = UI.icon("chevron-right");
    li.appendChild(go);
    li.addEventListener("click", () => goDetails(f.view || "findings", f.filter || null));
    ul.appendChild(li);
  }

  // the timeline: speed, ignition, gaps (lost / asleep), and a marker for each thing found ----------
  const MARK = { stale: ["Stale position", "var(--danger)"], jump: ["Jump", "var(--warn)"], frames: ["Frames lost", "var(--danger)"],
                 noposition: ["No position", "var(--warn)"], event: ["Driving event", "var(--info)"], overspeed: ["Overspeed", "var(--warn)"],
                 restart: ["Restart", "var(--danger)"], alert: ["Device alert", "var(--accent)"],
                 battery: ["Battery missing", "var(--danger)"] };
  function drawTimeline() {
    const box = $("an-timeline");
    box.innerHTML = "";
    const s = rep.series;
    if (!s || !s.points.length) { box.appendChild(el("div", "an-empty", "No rows")); return; }
    const W = Math.max(400, box.clientWidth - 20), H = 236;
    const L = 40, R = 10, T = 24, B = 40, plotH = H - T - B - 14;
    const t0 = dt(s.start).getTime(), t1 = Math.max(t0 + 1000, dt(s.end).getTime() + s.bucket_s * 1000);
    const x = (ms) => L + ((ms - t0) / (t1 - t0)) * (W - L - R);
    const vmax = Math.max(20, ...s.points.map((p) => p[2]));
    const ymax = Math.ceil(vmax / 20) * 20;
    const y = (v) => T + plotH - (v / ymax) * plotH;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, box);
    for (let v = 0; v <= ymax; v += ymax / 4) {
      sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }, g);
      sv("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, g).textContent = Math.round(v);
    }
    sv("text", { x: L - 6, y: T - 12, "text-anchor": "end" }, g).textContent = "km/h";
    // each trip a soft wash of its colour, fading downwards, with its number at the top — the same
    // colours as on the map, so a stretch of the day reads as its trip
    const defs = sv("defs", {}, g);
    const trips = (full.driving || {}).trips || [];
    trips.forEach((t) => {
      const a = Math.max(t0, dt(t.start).getTime()), b = Math.min(t1, dt(t.end).getTime());
      if (b <= a) return;
      const colour = tripColour(t.n), gid = "an-tz-" + t.n;
      const lgr = sv("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      sv("stop", { offset: "0", "stop-color": colour, "stop-opacity": 0.15 }, lgr);
      sv("stop", { offset: "1", "stop-color": colour, "stop-opacity": 0.02 }, lgr);
      const X = x(a), wd = Math.max(2, x(b) - X);
      sv("rect", { x: X, y: T, width: wd, height: plotH, fill: `url(#${gid})` }, g);
      sv("rect", { x: X, y: T, width: wd, height: 2, fill: colour, "fill-opacity": 0.45 }, g);
      if (wd >= 16) sv("text", { x: X + 4, y: T + 13, class: "trip-n", fill: colour }, g).textContent = t.n;
    });
    // gaps, under the speed
    (s.gaps || []).forEach((gp) => {
      const a = x(dt(gp.start).getTime()), b = x(dt(gp.end).getTime());
      const r = sv("rect", { x: a, y: T, width: Math.max(1.5, b - a), height: plotH + 14, class: "gap-" + gp.kind }, g);
      sv("title", {}, r).textContent = `${gp.kind === "loss" ? "Lost" : gp.kind === "sleep" ? "Asleep" : "Gap"} ${when(gp.start)} – ${when(gp.end)}`;
    });
    // speed area and line
    const bw = (s.bucket_s * 1000);
    let dPath = "", aPath = "";
    let prev = null;
    s.points.forEach((p) => {
      const ms = dt(p[0]).getTime();
      const X = x(ms + bw / 2), Y = y(p[2]);
      const broke = prev !== null && ms - prev > bw * 1.5;
      dPath += (dPath === "" || broke ? "M" : "L") + X.toFixed(1) + " " + Y.toFixed(1) + " ";
      prev = ms;
    });
    // the area: one closed shape per unbroken run
    let run = [];
    const flush = () => {
      if (run.length > 1) aPath += `M${run[0][0]} ${y(0)} ` + run.map((q) => `L${q[0]} ${q[1]}`).join(" ") + ` L${run[run.length - 1][0]} ${y(0)} Z `;
      run = [];
    };
    prev = null;
    s.points.forEach((p) => {
      const ms = dt(p[0]).getTime();
      if (prev !== null && ms - prev > bw * 1.5) flush();
      run.push([x(ms + bw / 2).toFixed(1), y(p[2]).toFixed(1)]);
      prev = ms;
    });
    flush();
    sv("path", { d: aPath, class: "speed-area" }, g);
    sv("path", { d: dPath, class: "speed" }, g);
    // the band under the speed: the device's mode (active, standby, sleep), else the ignition
    const by = T + plotH + 4;
    const spans = (rep.generation || {}).spans || [];
    const MODE_BAND = { active: "ign-on", standby: "ign-off", sleep: "mode-sleep" };
    if (spans.length) {
      spans.forEach((sp) => {
        const a = x(dt(sp.start).getTime()), b = x(dt(sp.end).getTime());
        sv("rect", { x: a, y: by, width: Math.max(1, b - a), height: 8, class: MODE_BAND[sp.mode] }, g);
      });
    } else {
      s.points.forEach((p) => {
        if (p[3] == null) return;
        const ms = dt(p[0]).getTime();
        sv("rect", { x: x(ms), y: by, width: Math.max(1, x(ms + bw) - x(ms) + 0.4), height: 8, class: p[3] ? "ign-on" : "ign-off" }, g);
      });
    }
    // time axis
    const span = (t1 - t0) / 1000;
    const stepH = span > 36 * 3600 ? 6 : span > 12 * 3600 ? 2 : span > 4 * 3600 ? 1 : span > 3600 ? 0.5 : 0.25;
    const first = new Date(t0); first.setMinutes(0, 0, 0);
    for (let ms = first.getTime(); ms <= t1; ms += stepH * 3600 * 1000) {
      if (ms < t0) continue;
      const X = x(ms), d = new Date(ms);
      sv("line", { x1: X, x2: X, y1: by + 10, y2: by + 14, class: "ax" }, g);
      sv("text", { x: X, y: by + 26, "text-anchor": "middle" }, g).textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    // markers along the top
    const seen = new Set();
    (s.markers || []).forEach((mk) => {
      const info = MARK[mk.kind];
      if (!info) return;
      const X = x(dt(mk.t).getTime());
      const key = mk.kind + Math.round(X / 3);
      if (seen.has(key)) return;
      seen.add(key);
      const c = sv("path", { d: `M${X - 4} ${T - 12} L${X + 4} ${T - 12} L${X} ${T - 5} Z`, class: "mk", fill: info[1] }, g);
      sv("title", {}, c).textContent = `${info[0]} ${when(mk.t)}`;
      c.addEventListener("click", () => goDetails("rows", { k: mk.k, time: mk.k == null ? mk.t : null }));
    });
    // hover: time, speed, rows; click: the rows at that time
    const tip = el("div", "an-tip");
    tip.hidden = true;
    box.appendChild(tip);
    const hl = sv("line", { y1: T, y2: by + 8, class: "hover-line", visibility: "hidden" }, g);
    const hit = sv("rect", { x: L, y: T, width: W - L - R, height: plotH + 14, fill: "transparent", style: "cursor: crosshair" }, g);
    const scale = () => box.querySelector("svg").getBoundingClientRect().width / W;
    hit.addEventListener("mousemove", (e) => {
      const rect = box.querySelector("svg").getBoundingClientRect();
      const X = (e.clientX - rect.left) / scale();
      const ms = t0 + ((X - L) / (W - L - R)) * (t1 - t0);
      const i = Math.min(s.points.length - 1, Math.max(0, bisect(s.points, ms)));
      const p = s.points[i];
      hl.setAttribute("x1", X); hl.setAttribute("x2", X); hl.setAttribute("visibility", "visible");
      tip.hidden = false;
      const tn = trips.find((t) => ms >= dt(t.start).getTime() && ms <= dt(t.end).getTime());
      tip.textContent = `${tn ? `Trip ${tn.n} · ` : ""}${when(p[0])} · ${num(p[2], 0)} km/h · ${p[1]} rows${p[3] == null ? "" : p[3] ? " · ignition on" : " · ignition off"}${p[4] ? ` · ${p[4]} history` : ""}`;
      const tx = e.clientX - box.getBoundingClientRect().left + 12;
      tip.style.left = Math.min(tx, box.clientWidth - tip.offsetWidth - 6) + "px";
      tip.style.top = "6px";
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; hl.setAttribute("visibility", "hidden"); });
    hit.addEventListener("click", (e) => {
      const rect = box.querySelector("svg").getBoundingClientRect();
      const X = (e.clientX - rect.left) / scale();
      const ms = t0 + ((X - L) / (W - L - R)) * (t1 - t0);
      const d = new Date(ms);
      const iso = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      goDetails("rows", { time: iso });
    });
    const lg = $("an-legend");
    lg.innerHTML = "";
    const gk = new Set((s.gaps || []).map((x) => x.kind));
    const mk = new Set(spans.map((x) => x.mode));
    [["Speed", "var(--accent)", "line"], spans.length ? mk.has("active") && ["Active", "var(--ok)"] : ["Ignition on", "var(--ok)"],
     mk.has("standby") && ["Standby", "var(--muted)"], mk.has("sleep") && ["Sleep", "var(--info)"], gk.has("loss") && ["Lost", "var(--danger)"],
     gk.has("sleep") && ["Asleep", "var(--muted)"], gk.has("unknown") && ["Gap", "var(--warn)"]].filter(Boolean).forEach(([t, c, k]) => {
      const sp = el("span");
      const i = el("i", k || "");
      i.style.setProperty("--c", c);
      sp.append(i, document.createTextNode(t));
      lg.appendChild(sp);
    });
    const kinds = new Set((s.markers || []).map((m) => m.kind));
    Object.entries(MARK).forEach(([k, [t, c]]) => {
      if (!kinds.has(k)) return;
      const sp = el("span");
      const i = el("i", "dot");
      i.style.setProperty("--c", c);
      sp.append(i, document.createTextNode(t));
      lg.appendChild(sp);
    });
  }
  function bisect(points, ms) {
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (dt(points[mid][0]).getTime() <= ms) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // the track on the map ------------------------------------------------------------------------------
  // Coloured by trip (each its own colour, outlined so it reads on any street map, numbered at its
  // start) or by speed. Three kinds of line, never confused: live packets solid and bright; history
  // packets (sent later) the same colour, dimmed and thin; no data dotted. A trip chosen above the
  // map is drawn on top, the rest faded, and the map zooms to it. Stale positions and jumps are
  // dashed, never joined into the track.
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  // the map's own tones, tuned per theme (analysis.css): the quiet line and the asleep dots dark enough
  // for a light map, how strongly faded and history lines show
  const mapNum = (n, fallback) => { const v = parseFloat(cssVar(n)); return Number.isFinite(v) ? v : fallback; };
  const TRIP_COLOURS = ["#3ecf8e", "#4f8ef7", "#f5a524", "#b07cf8", "#22c3d6", "#f472b6", "#9ad94a", "#fb7a3c"];
  let mapMode = "trips";
  let mapTrip = null;                         // a trip's number, or null for every trip
  let mapPoint = null;                        // the packet chosen on the map (its row), or null
  let mapGap = null;                          // or the stretch of missing data chosen (a break of the track)
  const DRAW_KEY = "runway.analysis.draw";
  let mapDraw = (() => { try { return localStorage.getItem(DRAW_KEY) === "dots" ? "dots" : "lines"; } catch (e) { return "lines"; } })();
  let dotRenderer = null, pointLayer = null;
  const tripColour = (n) => TRIP_COLOURS[(n - 1) % TRIP_COLOURS.length];
  function speedColour(v) {
    const lim = (full.settings && full.settings.overspeed_kmh) || 80;
    return v > lim ? cssVar("--danger") : v > lim * 0.75 ? cssVar("--warn") : v > 3 ? cssVar("--accent") : cssVar("--map-quiet");
  }
  function tripAt(k, trips) {
    for (const t of trips) if (k >= t.k && k <= t.k_end) return t.n;
    return null;
  }
  function renderTripBar() {
    const bar = $("an-trip-bar");
    bar.innerHTML = "";
    const trips = (full.driving || {}).trips || [];
    bar.hidden = !trips.length;
    if (!trips.length) return;
    const chip = (label, n, colour, tip) => {
      const b = el("button", "an-trip" + (mapTrip === n ? " on" : ""));
      b.type = "button";
      if (colour) { const sw = el("i"); sw.style.background = colour; b.appendChild(sw); }
      b.appendChild(document.createTextNode(label));
      if (tip) b.title = tip;
      b.addEventListener("click", () => pickTrip(mapTrip === n ? null : n));
      bar.appendChild(b);
    };
    chip("All trips", null, null);
    // the start time; with the day too (DD-MM) when it is not the log's first day
    const at = (iso) => { const w = when(iso); return w.length > 8 ? `${w.slice(0, 5)} ${w.slice(-8, -3)}` : w.slice(0, 5); };
    trips.forEach((t) => chip(`${t.n} · ${at(t.start)}`, t.n, mapMode === "trips" ? tripColour(t.n) : cssVar("--accent"),
      `Trip ${t.n}: ${range(t.start, t.end)}, ${t.km.toFixed(1)} km`));
    const t = trips.find((x) => x.n === mapTrip);
    if (t) {
      bar.appendChild(el("span", "an-trip-info",
        `${range(t.start, t.end)} · ${t.duration} · ${t.km.toFixed(1)} km · top ${num(t.max_speed, 0)} km/h · average ${num(t.avg_speed, 0)} km/h`));
    }
  }
  function pin(latlng, text, colour, cls) {
    const icon = L.divIcon({ className: "an-pin-wrap", html: `<span class="an-pin ${cls || ""}" style="--pc:${colour}">${text}</span>`,
                             iconSize: [22, 22], iconAnchor: [11, 11] });
    return L.marker(latlng, { icon, keyboard: false });
  }
  // direction: an arrow every ARROW_GAP pixels along each moving stretch, pointing the way the
  // vehicle went — re-placed on every zoom so they stay evenly spaced on screen
  const ARROW_GAP = 70, ARROW_MAX = 700;
  let arrowLayer = null, arrowPaths = [];
  function drawArrows() {
    if (!map || !arrowLayer) return;
    arrowLayer.clearLayers();
    const casing = getComputedStyle($("an-map")).getPropertyValue("--map-casing").trim() || "#10120f";
    let count = 0;
    arrowPaths.forEach((path) => {
      let carry = ARROW_GAP / 2;                     // the first arrow half a gap in from the start
      for (let i = 1; i < path.length && count < ARROW_MAX; i++) {
        const a = map.latLngToLayerPoint([path[i - 1][0], path[i - 1][1]]);
        const b = map.latLngToLayerPoint([path[i][0], path[i][1]]);
        const len = a.distanceTo(b);
        if (len < 0.5) continue;
        let at = carry;
        const deg = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
        while (at <= len && count < ARROW_MAX) {
          const f = at / len;
          const ll = map.layerPointToLatLng(L.point(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f));
          const icon = L.divIcon({
            className: "an-arrow-wrap", iconSize: [18, 18], iconAnchor: [9, 9],
            html: `<span class="an-arrow" style="color:${path[i][2]};--ac:${casing};transform:rotate(${deg.toFixed(0)}deg)">${UI.icons.direction}</span>`,
          });
          L.marker(ll, { icon, interactive: false, keyboard: false, zIndexOffset: -500 }).addTo(arrowLayer);
          count++;
          at += ARROW_GAP;
        }
        carry = at - len;
      }
    });
  }
  function drawMap() {
    const box = $("an-map");
    if (!window.L) { box.textContent = "The map needs the internet"; return; }
    if (!map) {
      // no keyboard handler: it focuses the map on a press, which scrolls a half-hidden map into view
      // between press and release — and the click on a line or dot is lost
      map = L.map(box, { zoomControl: true, attributionControl: true, keyboard: false }).setView([20.59, 78.96], 5);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
      mapLayers = L.layerGroup().addTo(map);
      arrowLayer = L.layerGroup().addTo(map);
      pointLayer = L.layerGroup().addTo(map);
      // the dots on a canvas of their own, under the lines: the lines' layer lets a click through
      // where it has nothing drawn (analysis.css), so a dot is clickable and so is every line
      map.createPane("an-dots").style.zIndex = 390;
      dotRenderer = L.canvas({ padding: 0.5, pane: "an-dots" });
      const Full = L.Control.extend({
        options: { position: "topleft" },
        onAdd() {
          const wrap = L.DomUtil.create("div", "leaflet-bar an-full-ctl");
          fullBtn = L.DomUtil.create("a", "", wrap);
          fullBtn.href = "#";
          fullBtn.setAttribute("role", "button");
          fullBtn.innerHTML = UI.icon("maximize");
          fullBtn.title = "Full screen";
          fullBtn.setAttribute("aria-label", "Full screen");
          L.DomEvent.on(fullBtn, "click", (e) => { L.DomEvent.stop(e); setMapFull(!mapFull); });
          L.DomEvent.disableClickPropagation(wrap);
          return wrap;
        },
      });
      new Full().addTo(map);
      map.on("zoomend", drawArrows);
    }
    document.querySelectorAll(".an-map-mode .seg").forEach((b) => b.classList.toggle("on", b.dataset.mode === mapMode));
    document.querySelectorAll(".an-map-draw .seg").forEach((b) => b.classList.toggle("on", b.dataset.draw === mapDraw));
    mapLayers.clearLayers();
    renderTripBar();
    const s = full.series || {};
    const d = full.driving || {};
    const trips = d.trips || [];
    const casing = getComputedStyle(box).getPropertyValue("--map-casing").trim() || "#10120f";
    const all = [], chosen = [];
    // runs of consecutive points with one colour; the chosen trip is drawn last, on top
    const runs = [];
    let anyHistory = false;
    (s.track || []).forEach((seg) => {
      let run = null;
      seg.forEach((p) => {
        const n = tripAt(p[3], trips);
        const h = p[4] ? 1 : 0;
        if (h) anyHistory = true;
        const colour = mapMode === "trips" ? (n ? tripColour(n) : cssVar("--map-quiet")) : speedColour(p[2]);
        if (!run || run.colour !== colour || run.n !== n || run.h !== h) {
          const prev = run && run.pts[run.pts.length - 1];
          const prevK = run && run.ks[run.ks.length - 1];
          run = { colour, n, h, pts: prev ? [prev] : [], ks: prev ? [prevK] : [], joined: !!prev };
          runs.push(run);
        }
        run.pts.push([p[0], p[1]]);
        run.ks.push(p[3]);
        all.push([p[0], p[1]]);
        if (mapTrip !== null && n === mapTrip) chosen.push([p[0], p[1]]);
      });
    });
    // the stretches that get direction arrows: each trip (only the chosen one, when one is chosen); by
    // speed, every stretch the vehicle was moving
    arrowPaths = [];
    (s.track || []).forEach((seg) => {
      let cur = null, key = null;
      seg.forEach((p) => {
        const n = tripAt(p[3], trips);
        const moving = mapMode === "trips" ? n !== null : p[2] > 3;
        if (!moving || (mapTrip !== null && n !== mapTrip)) { cur = null; return; }
        const k = mapMode === "trips" ? n : 1;
        if (!cur || key !== k) { cur = []; key = k; arrowPaths.push(cur); }
        cur.push([p[0], p[1], mapMode === "trips" ? tripColour(n) : speedColour(p[2])]);
      });
    });
    const faded = (r) => mapTrip !== null && r.n !== mapTrip;
    // the card on a trip's line: when, how far, how fast; live or history
    const tripTip = (n, hist) => {
      const t = trips.find((x) => x.n === n);
      if (!t) return cardTip(hist ? "History · not in a trip" : "Not in a trip");
      return cardTip(`Trip ${t.n}${hist ? " · history" : ""}`, { body: [["Start", when(t.start, true)], ["End", when(t.end, true)]] },
        [["Took", t.duration], ["Distance", t.km.toFixed(1) + " km"], ["Top", num(t.max_speed, 0) + " km/h"], ["Average", num(t.avg_speed, 0) + " km/h"]]);
    };
    // drawn bottom to top: faded, not in a trip, history, live in a trip
    const rank = (r) => (faded(r) ? 0 : r.n === null && mapMode === "trips" ? 1 : r.h ? 2 : 3);
    const fade = mapNum("--map-fade", 0.22), hist = mapNum("--map-hist", 0.45);
    // a click on a line picks the packet nearest the click
    const pickOn = (r) => (e) => {
      let best = 0, bd = Infinity;
      r.pts.forEach((q, i) => { const dd = map.distance(e.latlng, q); if (dd < bd) { bd = dd; best = i; } });
      selectPoint(r.ks[best], r.pts[best]);
    };
    const order = runs.filter((r) => r.pts.length > (mapDraw === "dots" ? 0 : 1)).sort((a, b) => rank(a) - rank(b));
    // dots: one per packet, live bright, history small and dim — each picks its packet
    if (mapDraw === "dots") arrowPaths = [];
    order.forEach((r) => {
      if (mapDraw !== "dots") return;
      const fd = faded(r), inTrip = r.n !== null;
      r.pts.forEach((q, i) => {
        if (i === 0 && r.joined) return;
        L.circleMarker(q, { renderer: dotRenderer, radius: r.h ? 2.6 : inTrip || mapMode === "speed" ? 3.6 : 2.8, color: casing,
                            weight: r.h ? 0.5 : 1, fillColor: r.colour, fillOpacity: fd ? fade : r.h ? hist : 1, opacity: fd ? fade * 0.7 : r.h ? hist * 0.8 : 0.8 })
          .on("click", () => selectPoint(r.ks[i], q)).addTo(mapLayers);
      });
    });
    order.forEach((r) => {
      if (mapDraw === "dots") return;
      const inTrip = r.n !== null;
      if (faded(r)) {
        L.polyline(r.pts, { color: r.colour, weight: inTrip && !r.h ? 3 : 2, opacity: fade }).on("click", pickOn(r)).addTo(mapLayers);
        return;
      }
      if (r.h) {                                  // history: the same colour, dimmed and thin
        const line = L.polyline(r.pts, { color: r.colour, weight: inTrip || mapMode === "speed" ? 2.5 : 1.8, opacity: hist }).addTo(mapLayers);
        line.bindTooltip(tripTip(r.n, true), TIP).on("click", pickOn(r));
        return;
      }
      const bold = inTrip || mapMode === "speed";
      if (bold) L.polyline(r.pts, { color: casing, weight: inTrip ? 8 : 6, opacity: 0.6 }).addTo(mapLayers);
      const line = L.polyline(r.pts, { color: r.colour, weight: bold ? (inTrip ? 4.5 : 3.5) : 2.5, opacity: bold ? 1 : 0.7 }).addTo(mapLayers);
      line.bindTooltip(tripTip(r.n, false), TIP);
      line.on("click", pickOn(r));
    });
    // no data: dotted from the last position before the gap to the first after it
    const BREAK = { loss: ["--danger", "ignition on — data lost"], sleep: ["--map-asleep", "ignition off"], unknown: ["--warn", "no ignition state"] };
    (s.breaks || []).forEach((g) => {
      const inChosen = mapTrip === null || tripAt(g.k, trips) === mapTrip || tripAt(g.k_from, trips) === mapTrip;
      const [tone, words] = BREAK[g.kind] || BREAK.unknown;
      L.polyline([g.from, g.to], { color: cssVar(tone), weight: 3.5, dashArray: "1 9", lineCap: "round", opacity: inChosen ? 0.95 : Math.max(0.3, fade) })
        .bindTooltip(gapTip(g, words), { sticky: true, className: "an-tipbox" })
        .on("click", () => selectGap(g)).addTo(mapLayers);
    });
    (s.stale || []).forEach((x) => {
      if (!x.stale) return;
      const real = x.stale[0] === x.from[0] && x.stale[1] === x.from[1] ? x.to : x.from;
      L.polyline([real, x.stale], { color: cssVar("--danger"), weight: 3, dashArray: "6 6", opacity: mapTrip === null ? 0.9 : Math.max(0.35, fade) })
        .bindTooltip(hopTip(x), TIP).on("click", () => selectHop(x)).addTo(mapLayers);
      L.circleMarker(x.stale, { radius: 7, color: cssVar("--danger"), weight: 2, fillOpacity: 0.25 })
        .bindTooltip(hopTip(x), TIP).on("click", () => selectHop(x)).addTo(mapLayers);
    });
    (s.jumps || []).forEach((x) => {
      L.polyline([x.from, x.to], { color: cssVar("--warn"), weight: 3, dashArray: "4 6", opacity: mapTrip === null ? 0.9 : Math.max(0.35, fade) })
        .bindTooltip(hopTip(x), TIP).on("click", () => selectHop(x)).addTo(mapLayers);
    });
    // each trip numbered at its start, its end ringed; the chosen one labelled with its times
    trips.forEach((t) => {
      if (mapTrip !== null && t.n !== mapTrip) return;
      const colour = mapMode === "trips" ? tripColour(t.n) : cssVar("--accent");
      if (t.to[0] != null) {
        pin(t.to, "", colour, "end").bindTooltip(`End ${when(t.end).slice(-8)}`, { permanent: mapTrip === t.n, direction: "auto", offset: [10, 0], className: "an-tipbox an-pintip" })
          .addTo(mapLayers);
      }
      if (t.from[0] != null) {
        pin(t.from, String(t.n), colour).bindTooltip(`Start ${when(t.start).slice(-8)}`, { permanent: mapTrip === t.n, direction: "auto", offset: [12, 0], className: "an-tipbox an-pintip" })
          .on("click", () => pickTrip(t.n)).addTo(mapLayers);
      }
    });
    (d.events || []).forEach((e) => {
      if (!e.at[0]) return;
      const n = tripAt(e.k, trips);
      if (mapTrip !== null && n !== mapTrip) return;
      L.circleMarker(e.at, { radius: 6, color: casing, weight: 1.5, fillColor: cssVar("--info"), fillOpacity: 1 })
        .bindTooltip(cardTip(`Driving event · ${e.name || "code " + e.code}`, { body: [["Time", when(e.time, true)], ["Speed", num(e.speed, 0) + " km/h"], ["Position", pos(e.at)]] }), TIP)
        .on("click", () => selectPoint(e.k, e.at)).addTo(mapLayers);
    });
    // where it stood last (the only mark at all for a vehicle that never moved)
    const lastSeg = (s.track || []).length ? s.track[s.track.length - 1] : null;
    const last = lastSeg && lastSeg[lastSeg.length - 1];
    if (last && mapTrip === null) {
      pin([last[0], last[1]], "", cssVar("--text"), "last").bindTooltip(cardTip("Last position", { body: [["Time", when(s.end, true)], ["Position", pos([last[0], last[1]])]] }), TIP)
        .on("click", () => selectPoint(last[3], [last[0], last[1]])).addTo(mapLayers);
      if (!all.length) all.push([last[0], last[1]]);
    }
    const fit = chosen.length ? chosen : all;
    setTimeout(() => {
      map.invalidateSize();
      if (fit.length) map.fitBounds(fit, { padding: [28, 28], maxZoom: 17, animate: false });
      drawArrows();
      markPoint();
    }, 50);
    const lg = $("an-map-legend");
    lg.innerHTML = "";
    const lim = (full.settings && full.settings.overspeed_kmh) || 80;
    const kinds = new Set((s.breaks || []).map((g) => g.kind));
    const dots = mapDraw === "dots";
    const items = [["Live", "--text", dots ? "dot" : "line"]].concat(anyHistory ? [["History", "--text", dots ? "dot dim" : "line thin"]] : [])
      .concat(kinds.has("loss") ? [["Data lost", "--danger", "dots"]] : [], kinds.has("sleep") ? [["Asleep", "--map-asleep", "dots"]] : [])
      .concat(mapMode === "trips"
        ? [["Not in a trip", "--map-quiet", "line"], ["Stale position", "--danger", "dot"], ["Driving event", "--info", "dot"]]
        : [[`Over ${lim} km/h`, "--danger"], ["Moving", "--accent"], ["Standing", "--map-quiet"], ["Stale position", "--danger", "dot"]]);
    items.forEach(([t, c, k]) => {
      const sp = el("span");
      const i = el("i", k || "line");
      i.style.setProperty("--c", `var(${c})`);
      sp.append(i, document.createTextNode(t));
      lg.appendChild(sp);
    });
  }
  document.querySelectorAll(".an-map-mode .seg").forEach((b) => b.addEventListener("click", () => { mapMode = b.dataset.mode; drawMap(); }));
  document.querySelectorAll(".an-map-draw .seg").forEach((b) => b.addEventListener("click", () => {
    mapDraw = b.dataset.draw;
    try { localStorage.setItem(DRAW_KEY, mapDraw); } catch (e) { /* not remembered */ }
    drawMap();
  }));
  // a stretch of missing data, as a small table: start and end, each its time and position; then how
  // long, how far apart, what kind
  function cardTip(title, rows, foot) {
    const e = UI.esc;
    const head = rows && rows.head ? `<thead><tr>${rows.head.map((h) => `<th>${e(h)}</th>`).join("")}</tr></thead>` : "";
    const body = (rows && rows.body ? rows.body : []).map((r) => `<tr><th>${e(r[0])}</th>${r.slice(1).map((c) => `<td>${e(c)}</td>`).join("")}</tr>`).join("");
    return `<div class="an-gtip"><div class="an-gtip-h">${e(title)}</div>` + (body ? `<table>${head}<tbody>${body}</tbody></table>` : "") +
      (foot && foot.length ? `<div class="an-gtip-f">${foot.map((f) => `<span>${f[1] == null ? e(f[0]) : `${e(f[0])} <b>${e(f[1])}</b>`}</span>`).join("")}</div>` : "") + "</div>";
  }
  const TIP = { sticky: true, className: "an-tipbox" };
  // a jump or a stale position: from and to, how far, in how long, how fast that would be
  function hopTip(x) {
    const stale = x.kind === "stale";
    return cardTip(`${stale ? "Stale position" : "Jump"} · ${when(x.time)}`,
      { head: ["", "Time", "Position", "Satellites"],
        body: [["From", when(x.from_time), pos(x.from), (x.sats || [])[0] == null ? "—" : String(x.sats[0])],
               ["To", when(x.time), pos(x.to), (x.sats || [])[1] == null ? "—" : String(x.sats[1])]] },
      [["Apart", distText(x.metres)], ["In", x.seconds == null ? "—" : dur(x.seconds)], ["Would be", x.kmh == null ? "—" : num(x.kmh, 0) + " km/h"]]);
  }
  function gapTip(g, words) {
    const e = UI.esc;
    const da = dt(g.start), db = dt(g.end);
    const day = (d) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
    const oneDay = da && db && day(da) === day(db);
    const t = (iso) => (oneDay ? when(iso, true).slice(-8) : when(iso, true));
    return `<div class="an-gtip"><div class="an-gtip-h">No data${oneDay ? ` · ${e(day(da))}` : ""}</div>` +
      `<table><thead><tr><th></th><th>Time</th><th>Position</th></tr></thead><tbody>` +
      `<tr><th>Start</th><td>${e(t(g.start))}</td><td>${e(pos(g.from))}</td></tr>` +
      `<tr><th>End</th><td>${e(t(g.end))}</td><td>${e(pos(g.to))}</td></tr></tbody></table>` +
      `<div class="an-gtip-f"><span>Duration <b>${e(g.duration || dur(g.seconds))}</b></span><span>Apart <b>${e(distText(g.metres))}</b></span>` +
      `<span>${e(words[0].toUpperCase() + words.slice(1))}</span></div></div>`;
  }

  // ---- the map full screen: the map fills the screen, the area tiles float over it, the side rail stays at
  // its edge; the same button (or Esc) brings it back. In the browser's own full screen where it is allowed.
  let mapFull = false;
  function fitFull() {
    if (!mapFull) return;
    const rail = $("an-rail");
    document.querySelector(".an-map-card").style.right = (rail.hidden ? 0 : rail.offsetWidth) + "px";
  }
  function renderFloatTiles() {
    const box = $("an-map-tiles");
    box.innerHTML = "";
    if (!mapFull || !rep) return;
    (rep.areas || []).forEach((a) => {
      const t = el("button", "an-ftile tone-" + (TONE[a.status] || "none"));
      t.type = "button";
      const lab = el("span", "an-ftile-l");
      lab.innerHTML = UI.icon(AREA_ICON[a.id] || "info");
      lab.appendChild(document.createTextNode(a.label));
      t.append(lab, el("b", null, a.figure || "—"), el("span", "ui-pill sm", a.status_word));
      if (a.about) t.dataset.tip = a.about;
      t.addEventListener("click", () => { setMapFull(false); goDetails("findings", { area: a.id }); });
      box.appendChild(t);
    });
  }
  function setMapFull(on) {
    if (on === mapFull) return;
    mapFull = on;
    document.body.classList.toggle("an-mapfull", on);
    const card = document.querySelector(".an-map-card");
    if (!on) card.style.right = "";
    if (fullBtn) {
      fullBtn.innerHTML = UI.icon(on ? "minimize" : "maximize");
      fullBtn.title = on ? "Exit full screen" : "Full screen";
      fullBtn.setAttribute("aria-label", fullBtn.title);
    }
    try {
      if (on && document.documentElement.requestFullscreen && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    } catch (e) { /* the page fills the window instead */ }
    renderFloatTiles();
    fitFull();
    setTimeout(() => { fitFull(); if (map) map.invalidateSize(); }, 120);
    setTimeout(() => { if (map) map.invalidateSize(); }, 400);
  }
  let fullBtn = null;
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && mapFull) setMapFull(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && mapFull && !document.querySelector("dialog[open]")) setMapFull(false);
  });
  window.addEventListener("resize", fitFull);

  // the packet chosen: a ring on the map, and the Packet panel opens on it
  let pointAt = null;                         // where it is on the map, when it has a position
  function selectPoint(k, latlng) {
    if (k == null) return;
    mapPoint = k;
    mapGap = null;
    pointAt = latlng || null;
    markPoint();
    railTab = "packet";
    renderRail();
  }
  function selectHop(x) {
    mapGap = Object.assign({}, x, { _type: x.kind === "stale" ? "stale" : "jump" });
    mapPoint = null; pointAt = null;
    markPoint();
    railTab = "packet";
    renderRail();
  }
  function selectGap(g) {
    mapGap = g;
    mapPoint = null; pointAt = null;
    markPoint();
    railTab = "packet";
    renderRail();
  }
  // what is picked, marked in the page's text colour — light on the dark map, dark on the light one
  function markPoint() {
    if (!pointLayer) return;
    pointLayer.clearLayers();
    const ink = cssVar("--text");
    if (mapGap) {
      const g = mapGap;
      const hop = g._type === "jump" || g._type === "stale";
      L.polyline([g.from, g.to], { color: ink, weight: 10, opacity: 0.22, lineCap: "round", interactive: false }).addTo(pointLayer);
      L.polyline([g.from, g.to], { color: cssVar(hop ? (g._type === "stale" ? "--danger" : "--warn") : g.kind === "loss" ? "--danger" : g.kind === "sleep" ? "--map-asleep" : "--warn"),
                                   weight: 4, dashArray: hop ? "6 6" : "1 9", lineCap: "round", interactive: false }).addTo(pointLayer);
      [g.from, g.to].forEach((q) => L.circleMarker(q, { radius: 7, color: ink, weight: 2.5, fill: false, interactive: false }).addTo(pointLayer));
      return;
    }
    if (mapPoint == null || !pointAt) return;
    L.circleMarker(pointAt, { radius: 10, color: cssVar("--text"), weight: 2.5, fill: false, interactive: false }).addTo(pointLayer);
    L.circleMarker(pointAt, { radius: 4, color: cssVar("--text"), weight: 0, fillColor: cssVar("--text"), fillOpacity: 1, interactive: false }).addTo(pointLayer);
  }
  // from the Trips table: that trip, on the summary (a trip's own report numbers its trips from 1, so
  // it is found in the whole log by its first row)
  function showTrip(t) {
    const n = tripAt(t.k, (full.driving || {}).trips || []) || t.n;
    remember();
    setView("summary");
    if (!scope || scope.n !== n) chooseTrip(n);
    setTimeout(() => { const c = document.querySelector(".an-map-card"); if (c) c.scrollIntoView({ block: "start", behavior: "smooth" }); }, 80);
  }

  // delivery delay histogram ---------------------------------------------------------------------------
  function drawLatency(box) {
    box.innerHTML = "";
    const lt = (rep.delivery || {}).latency;
    if (!lt || !lt.count) { box.appendChild(el("div", "an-empty", "No arrival times")); return; }
    const labels = ["<5s", "5s", "10s", "20s", "30s", "1m", "2m", "5m", "10m+"];      // each bar from this up to the next
    const hist = lt.hist.slice(1);           // the first bucket (under 0) is the clock running ahead
    const W = Math.max(260, box.clientWidth - 20), H = 170, L = 8, B = 34, T = 16;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, box);
    const mx = Math.max(1, ...hist);
    const bw = (W - L * 2) / hist.length;
    hist.forEach((c, i) => {
      const h = (c / mx) * (H - T - B);
      const r = sv("rect", { x: L + i * bw + 3, y: H - B - h, width: bw - 6, height: Math.max(c ? 1 : 0, h), rx: 2,
                             class: "bar" + (i >= 6 ? " bad" : i >= 4 ? " warn" : "") }, g);
      sv("title", {}, r).textContent = `${labels[i]}: ${num(c, 0)} rows`;
      if (c) sv("text", { x: L + i * bw + bw / 2, y: H - B - h - 4, "text-anchor": "middle" }, g).textContent = c >= 1000 ? (c / 1000).toFixed(1) + "k" : c;
      sv("text", { x: L + i * bw + bw / 2, y: H - B + 16, "text-anchor": "middle" }, g).textContent = labels[i];
    });
    sv("text", { x: W / 2, y: H - 4, "text-anchor": "middle" }, g).textContent =
      `median ${dur(lt.p50)} · 90% ${dur(lt.p90)} · max ${dur(lt.max)}`;
  }

  // the delay from making to landing, through the day: each period's median as a line, its slowest
  // packet as a bar, on a log scale (a second and ten minutes both readable); the attention limit dashed
  function drawDelay(box) {
    box.innerHTML = "";
    const s = rep.series || {};
    const pts = (s.points || []).filter((p) => p[7] != null);
    if (!pts.length) { box.appendChild(el("div", "an-empty", "No arrival times")); return; }
    const W = Math.max(260, box.clientWidth - 20), H = 170, L = 40, R = 8, T = 10, B = 26;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, box);
    const bw = s.bucket_s * 1000;
    const t0 = dt(s.start).getTime(), t1 = Math.max(t0 + 1000, dt(s.end).getTime() + bw);
    const x = (ms) => L + ((ms - t0) / (t1 - t0)) * (W - L - R);
    const top = Math.max(60, ...pts.map((p) => p[5]));
    const ticks = [[1, "1 s"], [10, "10 s"], [60, "1 min"], [600, "10 min"], [3600, "1 h"], [36000, "10 h"]].filter(([v]) => v <= top * 1.5);
    const lmax = Math.log10(Math.max(top, ticks[ticks.length - 1][0]));
    const y = (v) => T + (H - T - B) * (1 - Math.log10(Math.max(1, v)) / lmax);
    ticks.forEach(([v, lab]) => {
      sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }, g);
      sv("text", { x: L - 5, y: y(v) + 4, "text-anchor": "end" }, g).textContent = lab;
    });
    const lim = ((rep.limits || {}).latency_p90_s || [30])[0];
    sv("line", { x1: L, x2: W - R, y1: y(lim), y2: y(lim), class: "limit" }, g);
    const colW = Math.max(1, x(t0 + bw) - x(t0) - 0.6);
    pts.forEach((p) => {
      const X = x(dt(p[0]).getTime());
      const hgt = y(1) - y(p[5]);
      if (hgt > 0.5) sv("rect", { x: X, y: y(p[5]), width: colW, height: hgt, class: "bar" + (p[5] >= lim * 4 ? " bad" : p[5] >= lim ? " warn" : "") + " slow" }, g);
    });
    let d = "", prev = null;
    pts.forEach((p) => {
      const ms = dt(p[0]).getTime();
      d += (prev === null || ms - prev > bw * 1.5 ? "M" : "L") + (x(ms) + colW / 2).toFixed(1) + " " + y(p[7]).toFixed(1) + " ";
      prev = ms;
    });
    sv("path", { d, class: "median" }, g);
    sv("text", { x: W - R, y: y(lim) - 4, "text-anchor": "end", class: "limit-t" }, g).textContent = `${dur(lim)} limit`;
    const a = new Date(t0), b = new Date(t1 - bw);
    sv("text", { x: L, y: H - 6 }, g).textContent = `${pad(a.getHours())}:${pad(a.getMinutes())}`;
    sv("text", { x: W - R, y: H - 6, "text-anchor": "end" }, g).textContent = `${pad(b.getHours())}:${pad(b.getMinutes())}`;
    // hover reads a period; a click opens its rows
    const tip = el("div", "an-tip");
    tip.hidden = true;
    box.appendChild(tip);
    const hit = sv("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", style: "cursor: crosshair" }, g);
    const at = (e) => {
      const rect = box.querySelector("svg").getBoundingClientRect();
      const X = ((e.clientX - rect.left) / rect.width) * W;
      return t0 + ((X - L) / (W - L - R)) * (t1 - t0);
    };
    hit.addEventListener("mousemove", (e) => {
      const p = pts[Math.min(pts.length - 1, Math.max(0, bisect(pts, at(e))))];
      tip.hidden = false;
      tip.textContent = `${when(p[0])} · median ${dur(p[7])} · slowest ${dur(p[5])} · ${num(p[1], 0)} rows`;
      const tx = e.clientX - box.getBoundingClientRect().left + 12;
      tip.style.left = Math.max(4, Math.min(tx, box.clientWidth - tip.offsetWidth - 6)) + "px";
      tip.style.top = "4px";
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    hit.addEventListener("click", (e) => {
      const d2 = new Date(at(e));
      goDetails("rows", { time: `${d2.getFullYear()}-${pad(d2.getMonth() + 1)}-${pad(d2.getDate())}T${pad(d2.getHours())}:${pad(d2.getMinutes())}:${pad(d2.getSeconds())}` });
    });
    const lg = $("an-delay-legend");
    if (lg) {
      lg.innerHTML = "";
      [["Median", "var(--accent)", "line"], ["Slowest", "var(--muted)"]].forEach(([t, c, k]) => {
        const sp = el("span");
        const i = el("i", k || "");
        i.style.setProperty("--c", c);
        sp.append(i, document.createTextNode(t));
        lg.appendChild(sp);
      });
    }
  }

  // main supply and the internal battery on one chart, each on its own axis (main on the left,
  // internal on the right), with the ignition-on periods shaded — how each behaves with the engine
  // running and standing. Engine starts are marked where the supply fell lowest.
  function drawVolts(box, opts) {
    const o = opts || {};
    box.innerHTML = "";
    const raw = ((rep.power || {}).line || []).map((p) => (p.length === 2 ? [p[0], p[1], null, null, null] : p));
    if (!raw.length) { box.appendChild(el("div", "an-empty", "No supply or battery readings in this log")); return; }
    const hasMain = raw.some((p) => p[1] != null), hasInt = raw.some((p) => p[2] != null && p[2] > 0);
    const W = Math.max(280, box.clientWidth - 20), H = o.tall ? 250 : 170;
    const L = 40, R = hasInt ? 44 : 10, T = 12, B = o.tall ? 30 : 26;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, box);
    const t0 = dt(raw[0][0]).getTime(), t1 = Math.max(t0 + 1000, dt(raw[raw.length - 1][0]).getTime());
    const x = (ms) => L + ((ms - t0) / (t1 - t0)) * (W - L - R);
    const plotB = H - B;
    const span = (vals, pad, minRange) => {
      let lo = Math.min(...vals) - pad, hi = Math.max(...vals) + pad;
      if (hi - lo < minRange) { const m = (hi + lo) / 2; lo = m - minRange / 2; hi = m + minRange / 2; }
      return [lo, hi];
    };
    const mv = raw.filter((p) => p[1] != null).map((p) => p[1]);
    const iv = raw.filter((p) => p[2] != null && p[2] > 0).map((p) => p[2]);
    const [mlo, mhi] = hasMain ? span(mv, 0.4, 2) : [0, 1];
    const [ilo, ihi] = hasInt ? span(iv, 0.1, 0.6) : [0, 1];
    const ym = (v) => T + (plotB - T) * (1 - (v - mlo) / (mhi - mlo));
    const yi = (v) => T + (plotB - T) * (1 - (v - ilo) / (ihi - ilo));
    // ignition on: shaded behind everything
    let runStart = null;
    const shade = (a, b) => sv("rect", { x: x(a), y: T, width: Math.max(1, x(b) - x(a)), height: plotB - T, class: "ign-shade" }, g);
    raw.forEach((p, i) => {
      const ms = dt(p[0]).getTime();
      if (p[3] === 1 && runStart === null) runStart = ms;
      if ((p[3] !== 1 || i === raw.length - 1) && runStart !== null) { shade(runStart, ms); runStart = null; }
    });
    // the axes: main on the left, internal on the right
    const ticks = (lo, hi, n) => { const st = (hi - lo) / n; return Array.from({ length: n + 1 }, (_, i) => lo + i * st); };
    if (hasMain) {
      ticks(mlo, mhi, 4).forEach((v) => {
        sv("line", { x1: L, x2: W - R, y1: ym(v), y2: ym(v), class: "grid" }, g);
        sv("text", { x: L - 5, y: ym(v) + 4, "text-anchor": "end", class: "ax-main" }, g).textContent = v.toFixed(v >= 20 ? 0 : 1);
      });
    }
    if (hasInt) {
      ticks(ilo, ihi, 4).forEach((v) => {
        sv("text", { x: W - R + 5, y: yi(v) + 4, class: "ax-int" }, g).textContent = v.toFixed(2);
      });
    }
    const path = (idx, y) => {
      let d = "", prev = null;
      raw.forEach((p) => {
        if (p[idx] == null || (idx === 2 && p[idx] === 0)) { prev = null; return; }      // 0 V inside: missing
        const ms = dt(p[0]).getTime();
        d += (prev === null || ms - prev > 10 * 60 * 1000 ? "M" : "L") + x(ms).toFixed(1) + " " + y(p[idx]).toFixed(1) + " ";
        prev = ms;
      });
      return d;
    };
    // a battery missing: a band along the foot of the plot (main above, internal below)
    const band = (eps, row, cls, label) => (eps || []).forEach((e) => {
      const a = Math.max(t0, dt(e.start).getTime()), b = Math.max(a + 1000, dt(e.end).getTime() + (e.seconds ? 0 : 10000));
      const r = sv("rect", { x: x(a), y: plotB - 10 + row * 5, width: Math.max(3, x(b) - x(a)), height: 4, class: cls }, g);
      sv("title", {}, r).textContent = `${label} · ${when(e.start)} – ${when(e.end)}${e.duration ? " · " + e.duration : ""}`;
    });
    band((rep.power || {}).cuts, 0, "miss-main", "Main battery missing");
    band((rep.power || {}).internal_missing, 1, "miss-int", "Internal battery missing");
    if (hasInt) sv("path", { d: path(2, yi), class: "volt-int" }, g);
    if (hasMain) sv("path", { d: path(1, ym), class: "volt" }, g);
    // engine starts: where the supply fell lowest
    ((rep.power || {}).starts || []).forEach((st) => {
      const ms = dt(st.time).getTime();
      if (ms < t0 || ms > t1 || !hasMain) return;
      const c = sv("circle", { cx: x(ms), cy: ym(st.lowest), r: 3.5, class: "start-dot" }, g);
      sv("title", {}, c).textContent = `Engine start ${when(st.time)} · ${st.lowest.toFixed(1)} V`;
    });
    const a = new Date(t0), b = new Date(t1);
    sv("text", { x: L, y: H - 8 }, g).textContent = `${pad(a.getHours())}:${pad(a.getMinutes())}`;
    sv("text", { x: W - R, y: H - 8, "text-anchor": "end" }, g).textContent = `${pad(b.getHours())}:${pad(b.getMinutes())}`;
    // hover: both readings and the ignition at that moment; a click opens the rows there
    const tip = el("div", "an-tip");
    tip.hidden = true;
    box.appendChild(tip);
    const hl = sv("line", { y1: T, y2: plotB, class: "hover-line", visibility: "hidden" }, g);
    const hit = sv("rect", { x: L, y: T, width: W - L - R, height: plotB - T, fill: "transparent", style: "cursor: crosshair" }, g);
    const at = (e) => {
      const rect = box.querySelector("svg").getBoundingClientRect();
      const X = ((e.clientX - rect.left) / rect.width) * W;
      return [X, t0 + ((X - L) / (W - L - R)) * (t1 - t0)];
    };
    hit.addEventListener("mousemove", (e) => {
      const [X, ms] = at(e);
      const p = raw[Math.min(raw.length - 1, Math.max(0, bisect(raw, ms)))];
      hl.setAttribute("x1", X); hl.setAttribute("x2", X); hl.setAttribute("visibility", "visible");
      tip.hidden = false;
      tip.textContent = [when(p[0]), p[1] != null ? `main ${p[1].toFixed(1)} V` : "", p[2] != null ? `internal ${p[2].toFixed(2)} V` : "",
                         p[4] != null ? `${p[4]}%` : "", p[3] == null ? "" : p[3] ? "ignition on" : "ignition off"].filter(Boolean).join(" · ");
      const tx = e.clientX - box.getBoundingClientRect().left + 12;
      tip.style.left = Math.max(4, Math.min(tx, box.clientWidth - tip.offsetWidth - 6)) + "px";
      tip.style.top = "4px";
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; hl.setAttribute("visibility", "hidden"); });
    hit.addEventListener("click", (e) => {
      const d2 = new Date(at(e)[1]);
      goDetails("rows", { time: `${d2.getFullYear()}-${pad(d2.getMonth() + 1)}-${pad(d2.getDate())}T${pad(d2.getHours())}:${pad(d2.getMinutes())}:${pad(d2.getSeconds())}` });
    });
    // the legend, in the card's head when it has one
    const head = box.closest(".an-card") && box.closest(".an-card").querySelector(".an-card-head");
    if (head) {
      let lg = head.querySelector(".an-legend");
      if (!lg) { lg = el("span", "an-legend"); head.appendChild(lg); }
      lg.innerHTML = "";
      [hasMain && [o.tall ? (hasInt ? "Main supply, V (left)" : "Main supply, V") : "Main", "var(--info)", "line"],
       hasInt && [o.tall ? "Internal battery, V (right)" : "Internal", "var(--warn)", "line"],
       raw.some((p) => p[3] === 1) && [o.tall ? "Ignition on" : "Ignition", "var(--ok)"],
       ((rep.power || {}).cuts || []).length + ((rep.power || {}).internal_missing || []).length > 0 &&
         [o.tall ? "Battery missing" : "Missing", "var(--danger)"]].filter(Boolean).forEach(([t, c, k]) => {
        const sp = el("span");
        const i = el("i", k || "");
        i.style.setProperty("--c", c);
        sp.append(i, document.createTextNode(t));
        lg.appendChild(sp);
      });
    }
  }

  // ---- details ------------------------------------------------------------------------------------
  const SECTIONS = [
    ["findings", "Findings", "alert"], ["scorecard", "Scorecard", "check"], ["gaps", "Gaps", "clock"], ["modes", "Modes", "repeat"], ["frames", "Frames", "list"], ["gps", "GPS", "satellite"],
    ["delivery", "Delivery", "send"], ["power", "Power", "power"], ["trips", "Trips", "route"], ["events", "Events", "gauge"],
    ["device", "Device", "device"], ["payload", "Payload", "chip"], ["types", "Packet types", "columns"],
    ["columns", "Columns", "template"], ["rows", "Rows", "file"],
  ];
  function sectionCount(id) {
    switch (id) {
      case "findings": return rep.findings.filter((f) => f.level === "warn" || f.level === "bad").length;
      case "gaps": return (rep.gaps || []).length;
      case "modes": return ((rep.generation || {}).modes || []).length;
      case "scorecard": return (rep.scorecard || []).filter((x) => x.level === "warn" || x.level === "bad").length;
      case "frames": return ((rep.frames || {}).holes || []).length;
      case "gps": return ((rep.gps || {}).stale || []).length + ((rep.gps || {}).jumps || []).length;
      case "trips": return ((rep.driving || {}).trips || []).length;
      case "events": return ((rep.driving || {}).events || []).length + ((rep.driving || {}).overspeed || []).length;
      case "device": return ((rep.device || {}).alerts || []).length;
      case "types": return (rep.types || []).length;
      case "columns": return (rep.columns || []).length;
      case "rows": return (rep.summary || {}).rows;
      default: return null;
    }
  }
  function renderDetails() {
    const nav = $("an-dnav");
    nav.innerHTML = "";
    SECTIONS.forEach(([id, label, ic]) => {
      const b = el("button", (id === section ? "on" : ""));
      b.type = "button";
      b.innerHTML = UI.icon(ic);
      b.appendChild(document.createTextNode(label));
      // a dot when this section holds a finding worth attention, else its count
      const own = (rep.findings || []).filter((f) => f.view === id && (f.level === "warn" || f.level === "bad"));
      const st = id === "findings" || id === "rows" ? null : own.some((f) => f.level === "bad") ? "bad" : own.length ? "warn" : null;
      if (st) {
        const d = el("span", "an-dnav-dot tone-" + st);
        b.appendChild(d);
      } else {
        const n = sectionCount(id);
        if (n) b.appendChild(el("span", "an-dnav-n", num(n, 0)));
      }
      b.addEventListener("click", () => {
        if (id === section && !sectionFilter) return;
        remember();
        section = id; sectionFilter = null; renderDetails(); writeUrl(); work.scrollTop = 0;
      });
      nav.appendChild(b);
    });
    const body = $("an-dbody");
    body.innerHTML = "";
    (DETAIL[section] || DETAIL.findings)(body, sectionFilter || {});
  }

  function card(parent, title, tip) {
    const c = el("div", "an-card");
    const h = el("div", "an-card-head");
    const t = el("span", "an-card-title", title);
    if (tip) t.dataset.tip = tip;
    h.appendChild(t);
    c.appendChild(h);
    parent.appendChild(c);
    return { card: c, head: h };
  }
  function figs(parent, list) {
    const box = el("div", "an-figs");
    list.filter(Boolean).forEach(([v, l]) => {
      const f = el("div", "an-fig");
      f.append(el("span", "an-fig-v", v), el("span", "an-fig-l", l));
      box.appendChild(f);
    });
    parent.appendChild(box);
    return box;
  }
  // a sortable grid: cols = [{key, label, fmt(row) → text|Node, num, cls, sort(row)}]
  function grid(parent, cols, rows, opts) {
    const o = opts || {};
    const wrap = el("div", "an-table-wrap");
    if (!rows.length) { wrap.appendChild(el("div", "an-empty", o.empty || "Nothing here")); parent.appendChild(wrap); return wrap; }
    const table = el("table");
    const thead = el("thead"), tr = el("tr");
    let sortBy = null, dir = 1;
    cols.forEach((c, i) => {
      const th = el("th", c.num ? "num" : "", c.label);
      th.addEventListener("click", () => { dir = sortBy === i ? -dir : 1; sortBy = i; fill(); });
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    table.appendChild(thead);
    const tbody = el("tbody");
    table.appendChild(tbody);
    function fill() {
      tbody.innerHTML = "";
      let list = rows.slice();
      if (sortBy !== null) {
        const c = cols[sortBy];
        const key = c.sort || ((r) => r[c.key]);
        list.sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * dir; });
      }
      [...thead.querySelectorAll("th")].forEach((th, i) => {
        th.querySelectorAll(".sort").forEach((s) => s.remove());
        if (i === sortBy) {
          const mark = el("span", "sort");
          mark.innerHTML = UI.icons["chevron-down"];
          if (dir > 0) mark.style.transform = "rotate(180deg)";
          th.appendChild(mark);
        }
      });
      list.forEach((r) => {
        const row = el("tr");
        if (o.target && o.target(r)) row.classList.add("is-target");
        cols.forEach((c) => {
          const td = el("td", (c.num ? "num " : "") + (c.cls || ""));
          const v = c.fmt ? c.fmt(r) : r[c.key];
          if (v instanceof Node) td.appendChild(v); else td.textContent = v == null || v === "" ? "—" : String(v);
          row.appendChild(td);
        });
        if (o.onRow) { row.addEventListener("click", () => o.onRow(r)); } else row.style.cursor = "default";
        tbody.appendChild(row);
      });
    }
    fill();
    wrap.appendChild(table);
    parent.appendChild(wrap);
    return wrap;
  }
  const pill = (text, tone) => { const p = el("span", "ui-pill sm tone-" + tone, text); return p; };
  const flagPill = (f) => { const [t, tone] = FLAG[f] || [f, "info"]; const p = el("span", "an-flag tone-" + tone, t); return p; };
  const rowsBtn = (filt) => {
    const b = el("button", "ghost mini an-go");
    b.type = "button";
    UI.setLabel(b, "file", "Rows");
    b.addEventListener("click", (e) => { e.stopPropagation(); goDetails("rows", filt); });
    return b;
  };
  const pos = (p) => (p && p[0] != null ? `${Number(p[0]).toFixed(5)}, ${Number(p[1]).toFixed(5)}` : "—");
  const distText = (m) => (m == null ? "—" : m >= 1000 ? num(m / 1000, 2) + " km" : num(m, 0) + " m");

  const DETAIL = {
    findings(body, f) {
      const area = f.area;
      const label = area ? ((rep.areas || []).find((a) => a.id === area) || {}).label : null;
      const { card: c, head } = card(body, label ? `Findings · ${label}` : "Findings");
      if (area) {
        const all = el("button", "ghost mini");
        all.type = "button";
        all.textContent = "All areas";
        all.style.marginLeft = "auto";
        all.addEventListener("click", () => { sectionFilter = null; renderDetails(); });
        head.appendChild(all);
      }
      const ul = el("ul", "an-findings");
      ul.style.maxHeight = "none";
      c.appendChild(ul);
      renderFindings(ul, rep.findings.filter((x) => !area || x.area === area), !area, "details");
    },
    gaps(body, f) {
      const s = rep.summary || {};
      const { card: c } = card(body, "Gaps", `A gap is a silence longer than ${dur(s.gap_s)}. Lost: the ignition was on. Asleep: it was off.`);
      const gaps = rep.gaps || [];
      const cad = s.cadence || {};
      figs(c, [[dur(s.loss_s), "lost with ignition on"], [s.coverage_pct != null ? s.coverage_pct + "%" : "—", "ignition-on time covered"],
               [dur(s.ignition_on_s), "ignition on"], [dur(s.ignition_off_s), "ignition off"],
               cad.on ? [`every ${dur(cad.on.seconds)}`, `ignition on · ${cad.on.on_time_pct}% on time`] : [dur(s.step_s), "usual step"],
               cad.off ? [`every ${dur(cad.off.seconds)}`, `ignition off · ${cad.off.on_time_pct}% on time`] : null]);
      const KIND = { loss: ["Lost", "bad"], sleep: ["Asleep", "none"], unknown: ["Unknown", "warn"] };
      grid(c, [
        { label: "Start", fmt: (g) => when(g.start), sort: (g) => g.start },
        { label: "End", fmt: (g) => when(g.end), sort: (g) => g.end },
        { label: "Duration", fmt: (g) => g.duration, num: true, sort: (g) => g.seconds },
        { label: "Kind", fmt: (g) => pill(...(KIND[g.kind] || [g.kind, "info"])), sort: (g) => g.kind },
        { label: "Ignition", fmt: (g) => `${g.ign_before == null ? "?" : g.ign_before ? "on" : "off"} → ${g.ign_after == null ? "?" : g.ign_after ? "on" : "off"}` },
        { label: "Speed", fmt: (g) => `${num(g.speed_before, 0)} → ${num(g.speed_after, 0)} km/h` },
        { label: "From", fmt: (g) => pos(g.from), cls: "mono" }, { label: "To", fmt: (g) => pos(g.to), cls: "mono" },
        { label: "Apart", fmt: (g) => distText(g.metres), num: true, sort: (g) => g.metres || 0 },
        { label: "", fmt: (g) => rowsBtn({ k: g.k }) },
      ], gaps.filter((g) => !f.kind || g.kind === f.kind), { empty: "No gaps" });
    },
    scorecard(body) {
      const { card: c } = card(body, scope ? `Scorecard · trip ${scope.n}` : "Scorecard",
        "Every measure of this log, judged against its limit (Settings): green good, amber attention, red poor. With a trip chosen, the whole log beside it");
      const holder = el("div");
      c.appendChild(holder);
      renderScorecard(holder, "details");
    },
    modes(body, f) {
      const gen = rep.generation || {};
      const v = gen.values || {}, src = gen.source || {};
      const { card: c, head } = card(body, "Packets by mode",
        "Active: ignition on. Standby: ignition off, still awake. Sleep: after standby (or the deep-sleep packet), the slowest. Due = time in the mode ÷ its interval; every packet counts");
      const side = el("span", "an-card-side");
      side.append(modeSource(), profilesBtn());
      head.appendChild(side);
      const from = (k) => (src[k] ? ` · ${SOURCE_WORD[src[k]]}` : "");
      figs(c, [[v.active_s ? dur(v.active_s) : "—", "active, every" + from("active_s")],
               [v.standby_s ? dur(v.standby_s) : "—", "standby, every" + from("standby_s")],
               [v.standby_min != null ? dur(v.standby_min * 60) : "—", "standby lasts" + from("standby_min")],
               [v.sleep_s ? dur(v.sleep_s) : "—", "sleep, every" + from("sleep_s")]]);
      const holder = el("div");
      c.appendChild(holder);
      renderModes(holder, true, f.mode);
    },
    frames(body) {
      const fr = rep.frames || {};
      const { card: c } = card(body, "Frame numbers", "The device numbers every packet. A hole that repeats at a fixed spacing is another packet type using the counter, not loss.");
      if (!fr.numbered) { c.appendChild(el("div", "an-empty", "This log has no frame numbers")); return; }
      figs(c, [[num(fr.numbered, 0), "numbered rows"], [fr.range ? `${fr.range[0]}–${fr.range[1]}` : "—", "range"],
               [num(fr.lost, 0), "frame numbers missing"], [(fr.lost_pct || 0).toFixed(2) + "%", "missing"],
               [num((fr.starts || []).length, 0), "counter starts"],
               [num(fr.periodic_holes, 0), fr.period ? `periodic holes, every ${fr.period}` : "periodic holes"], [num(fr.resets, 0), "restarts"]]);
      grid(c, [
        { label: "After", key: "after", num: true, cls: "mono" }, { label: "Before", key: "before", num: true, cls: "mono" },
        { label: "Missing", key: "missing", num: true }, { label: "Time", fmt: (h) => when(h.time), sort: (h) => h.time },
        { label: "Kind", fmt: (h) => h.periodic ? pill("Periodic", "none") : pill("Missing", "bad"), sort: (h) => h.periodic },
        { label: "Near an ignition change", fmt: (h) => (h.near_ignition ? "yes" : ""), sort: (h) => !!h.near_ignition },
        { label: "", fmt: (h) => rowsBtn({ k: h.k }) },
      ], fr.holes || [], { empty: "No holes" });
    },
    gps(body, f) {
      const g = rep.gps || {};
      const { card: c } = card(body, "Position quality");
      figs(c, [[(100 - (g.no_fix_pct || 0)).toFixed(1) + "%", "rows with satellites"], [num(g.stale_points, 0), "stale positions"],
               [num((g.jumps || []).length, 0), "other jumps"], [(g.driven_km || 0).toFixed(1) + " km", "driven"],
               [(g.jump_km || 0).toFixed(1) + " km", "in jumps"], [(g.drift_km || 0).toFixed(2) + " km", "drift while standing"],
               [num(g.frozen, 0), "repeated positions while moving"], [num(g.speed_mismatch, 0), "speed differs"]]);
      const list = (g.stale || []).concat(g.jumps || []).filter((x) => !f.kind || x.kind === f.kind);
      const { card: c2 } = card(body, "Jumps", "Hops faster than the jump limit, or to and from a position with no satellites");
      grid(c2, [
        { label: "Kind", fmt: (x) => x.kind === "stale" ? pill("Stale position", "bad") : pill("Jump", "warn"), sort: (x) => x.kind },
        { label: "From", fmt: (x) => when(x.from_time), sort: (x) => x.from_time }, { label: "To", fmt: (x) => when(x.time), sort: (x) => x.time },
        { label: "Distance", fmt: (x) => x.metres >= 1000 ? (x.metres / 1000).toFixed(1) + " km" : x.metres + " m", num: true, sort: (x) => x.metres },
        { label: "Speed", fmt: (x) => x.kmh == null ? "—" : num(x.kmh, 0) + " km/h", num: true, sort: (x) => x.kmh || 0 },
        { label: "Satellites", fmt: (x) => `${x.sats[0] == null ? "?" : x.sats[0]} → ${x.sats[1] == null ? "?" : x.sats[1]}` },
        { label: "Types", fmt: (x) => `${x.types[0]} → ${x.types[1]}` },
        { label: "From", fmt: (x) => pos(x.from), cls: "mono" }, { label: "To", fmt: (x) => pos(x.to), cls: "mono" },
        { label: "", fmt: (x) => rowsBtn({ k: x.k }) },
      ], list, { empty: "No jumps" });
      const { card: c3 } = card(body, "Satellites and signal");
      const box = el("div", "an-grid2");
      box.style.padding = "8px 14px 14px";
      box.append(distChart("Satellites", g.sats || []), distChart("GSM signal", g.gsm || []));
      c3.appendChild(box);
    },
    delivery(body) {
      const d = rep.delivery || {}, lt = d.latency || {};
      const { card: c } = card(body, "Delivery");
      figs(c, [[dur(lt.p50), "median delay"], [dur(lt.p90), "90% within"], [dur(lt.p99), "99% within"], [dur(lt.max), "slowest"],
               [num(d.late, 0), "arrived after a newer row"], [num(d.history, 0), "flagged history"],
               [d.marked ? num(d.history_mismatch.live_as_history + d.history_mismatch.history_as_live, 0) : "—", "history flag differs from the device"],
               [num(d.clock_ahead, 0), "stored before the device's time"], [num(d.no_position, 0), "rows without a position"],
               [num(d.very_late || 0, 0), "stored over 10 min late"]]);
      const nw = rep.network || {};
      if (nw.considered) {
        const { card: cn } = card(body, "Network");
        figs(cn, [[(nw.missing_pct || 0).toFixed(0) + "%", "rows with a signal but no cell id"], [num(nw.cells, 0), "cells"],
                  [num(nw.changes, 0), "cell changes"], [nw.gsm_median != null ? nw.gsm_median : "—", "median GSM level"],
                  [(nw.operators || []).map(([o, n]) => o).join(", ") || "—", "operator (MCC/MNC)"]]);
      } else if (nw.never) {
        card(body, "Network").card.appendChild(el("div", "an-empty", "This device does not report its cell"));
      }
      const ch = el("div", "an-chart");
      c.appendChild(ch);
      setTimeout(() => drawLatency(ch), 0);
      const { card: c2 } = card(body, "Upload batches", "How many rows the platform stored at the same second");
      grid(c2, [{ label: "Rows at once", key: 0, num: true, fmt: (r) => r[0] }, { label: "Times", key: 1, num: true, fmt: (r) => num(r[1], 0) },
                { label: "Rows", num: true, fmt: (r) => num(r[0] * r[1], 0), sort: (r) => r[0] * r[1] }], (d.batches || {}).sizes || []);
      if (d.no_position) {
        const { card: c3 } = card(body, "Rows without a position");
        const list = (d.no_position_times || []).map((t) => ({ t }));
        grid(c3, [{ label: "Time", fmt: (r) => when(r.t), sort: (r) => r.t }, { label: "", fmt: (r) => rowsBtn({ time: r.t, flag: "noposition" }) }], list);
      }
    },
    power(body) {
      const p = rep.power || {};
      const { card: c } = card(body, "Power");
      if (!p.rows && !p.internal) { c.appendChild(el("div", "an-empty", "No power readings in this log")); return; }
      const ex = p.internal || {};
      figs(c, [p.off ? [p.off.median.toFixed(2) + " V", `ignition off (${p.off.min.toFixed(1)}–${p.off.max.toFixed(1)})`] : null,
               p.on ? [p.on.median.toFixed(2) + " V", `ignition on (${p.on.min.toFixed(1)}–${p.on.max.toFixed(1)})`] : null,
               [p.system + " V", "system"], [num((p.cuts || []).length, 0), "main battery missing"],
               [num((p.internal_missing || []).length, 0), "internal battery missing"],
               ex.volts ? [ex.volts[0] === ex.volts[1] ? `${ex.volts[0].toFixed(2)} V` : `${ex.volts[0].toFixed(2)}–${ex.volts[1].toFixed(2)} V`, "internal battery"] : null,
               ex.level ? [ex.level[0] === ex.level[1] ? `${ex.level[0]}%` : `${ex.level[0]}–${ex.level[1]}%`, "internal battery level"] : null,
               ex.zero_rows ? [num(ex.zero_rows, 0), "rows reading 0 V inside"] : null]);
      const ch = el("div", "an-chart");
      c.appendChild(ch);
      setTimeout(() => drawVolts(ch, { tall: true }), 0);
      if ((p.starts || []).length) {
        const { card: cs } = card(body, "Engine starts", "The lowest supply from 5 s before to a minute after the ignition comes on — the starter's pull on the battery");
        const lowLim = ((rep.limits || {}).start_v || [9.6, 7.5]).map((v) => v * (p.system / 12));
        grid(cs, [{ label: "Ignition on", fmt: (x) => when(x.time), sort: (x) => x.time },
                  { label: "Lowest supply", fmt: (x) => pill(x.lowest.toFixed(1) + " V", x.lowest < lowLim[1] ? "bad" : x.lowest < lowLim[0] ? "warn" : "ok"),
                    num: true, sort: (x) => x.lowest },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], p.starts);
      }
      const missing = (p.cuts || []).map((e) => ({ ...e, kind: "Main battery" }))
        .concat((p.internal_missing || []).map((e) => ({ ...e, kind: "Internal battery" })));
      if (missing.length) {
        const { card: c2 } = card(body, "Battery missing", "Main battery: the supply under 7 V (12 V system) or the device's own power flag at 0, outside an engine start. Internal battery: it reads 0 V.");
        grid(c2, [{ label: "Battery", fmt: (x) => pill(x.kind, "bad"), sort: (x) => x.kind },
                  { label: "From", fmt: (x) => when(x.start), sort: (x) => x.start }, { label: "To", fmt: (x) => when(x.end) + (x.to_end ? " (log ends)" : "") },
                  { label: "Duration", fmt: (x) => x.duration || "—", num: true, sort: (x) => x.seconds },
                  { label: "Rows", key: "rows", num: true },
                  { label: "Lowest", fmt: (x) => (x.kind === "Main battery" && x.lowest != null ? x.lowest.toFixed(1) + " V" : "0 V"), num: true },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], missing);
      }
      if ((p.internal_low || []).length) {
        const { card: c3 } = card(body, "Internal battery low", "Under 3.6 V, or its level at 20% or less");
        grid(c3, [{ label: "From", fmt: (x) => when(x.start), sort: (x) => x.start }, { label: "To", fmt: (x) => when(x.end) },
                  { label: "Duration", fmt: (x) => x.duration || "—", num: true, sort: (x) => x.seconds },
                  { label: "Lowest", fmt: (x) => x.lowest.toFixed(2) + " V", num: true, sort: (x) => x.lowest },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], p.internal_low);
      }
    },
    trips(body) {
      const d = rep.driving || {};
      const { card: c } = card(body, "Trips", `Moving above ${num((rep.settings || {}).moving_kmh, 0)} km/h; a stop of ${num((rep.settings || {}).trip_stop_min, 0)} min ends a trip`);
      figs(c, [[num(d.trips.length, 0), "trips"], [(d.distance_km || 0).toFixed(1) + " km", "distance"], [num(d.max_speed, 0) + " km/h", "top speed"],
               [dur(d.ignition_on_s), "ignition on"], [dur(d.idle_s), "idling"], [dur(d.ignition_off_s), "ignition off"]]);
      grid(c, [
        { label: "#", key: "n", num: true }, { label: "Start", fmt: (t) => when(t.start), sort: (t) => t.start },
        { label: "End", fmt: (t) => when(t.end), sort: (t) => t.end }, { label: "Duration", key: "duration", num: true, sort: (t) => t.seconds },
        { label: "Distance", fmt: (t) => t.km.toFixed(2) + " km", num: true, sort: (t) => t.km },
        { label: "Top", fmt: (t) => num(t.max_speed, 0) + " km/h", num: true, sort: (t) => t.max_speed },
        { label: "Average", fmt: (t) => num(t.avg_speed, 0) + " km/h", num: true, sort: (t) => t.avg_speed },
        { label: "From", fmt: (t) => pos(t.from), cls: "mono" }, { label: "To", fmt: (t) => pos(t.to), cls: "mono" },
        { label: "", fmt: (t) => rowsBtn({ k: t.k }) },
      ], d.trips || [], { empty: "Not driven", onRow: (t) => showTrip(t) });
      const { card: c2 } = card(body, "Ignition");
      grid(c2, [{ label: "Time", fmt: (x) => when(x.time), sort: (x) => x.time }, { label: "Ignition", fmt: (x) => x.to ? pill("On", "ok") : pill("Off", "none") },
                { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], d.ignition || [], { empty: "No ignition changes" });
    },
    events(body, f) {
      const d = rep.driving || {};
      if (!f.kind || f.kind === "overspeed") {
        const { card: c } = card(body, `Over ${num((rep.settings || {}).overspeed_kmh, 0)} km/h`);
        grid(c, [{ label: "Start", fmt: (e) => when(e.start), sort: (e) => e.start }, { label: "Duration", key: "duration", num: true, sort: (e) => e.seconds },
                 { label: "Top speed", fmt: (e) => num(e.max, 0) + " km/h", num: true, sort: (e) => e.max }, { label: "At", fmt: (e) => pos(e.at), cls: "mono" },
                 { label: "", fmt: (e) => rowsBtn({ k: e.k }) }], d.overspeed || [], { empty: "Never over the limit" });
      }
      if (!f.kind || f.kind === "driving") {
        const { card: c } = card(body, "Driving events", "greenDriveType: the device's own driving events");
        grid(c, [{ label: "Time", fmt: (e) => when(e.time), sort: (e) => e.time }, { label: "Code", key: "code", num: true },
                 { label: "Event", fmt: (e) => e.name || "" },
                 { label: "Value", key: "value" }, { label: "Speed", fmt: (e) => num(e.speed, 0) + " km/h", num: true, sort: (e) => e.speed || 0 },
                 { label: "Type", key: "type" }, { label: "At", fmt: (e) => pos(e.at), cls: "mono" }, { label: "", fmt: (e) => rowsBtn({ k: e.k }) }],
             d.events || [], { empty: "No driving events" });
      }
    },
    device(body, f) {
      const d = rep.device || {}, fr = rep.frames || {};
      const { card: c } = card(body, "Device");
      figs(c, [[num((fr.starts || []).length, 0), "counter starts"], [num((d.restarts || []).length, 0), "restarts"],
               [num(d.alerts_total || 0, 0), "alerts"], [(d.firmware || []).join(" → ") || "—", "firmware"],
               d.ignition && d.ignition.checked ? [num(d.ignition.disagree + d.ignition.missing, 0), "ignition alerts wrong or missing"] : null,
               [num((d.flicker || []).length, 0), "ignition flickers"], [num(d.ota || 0, 0), "changed over the air"],
               d.tamper_open ? [num(d.tamper_open, 0), "rows with the box open"] : null]);
      if ((fr.starts || []).length) {
        const boots = Object.fromEntries((d.boots || []).map((b) => [b.time, b]));
        const { card: c1 } = card(body, "Starts", "Where the frame counter begins again: the device started. Its boot report, when it sends one, says why.");
        grid(c1, [{ label: "Time", fmt: (x) => when(x.time), sort: (x) => x.time },
                  { label: "First frame", key: "frame", num: true },
                  { label: "Silent before", fmt: (x) => (x.gap_before == null ? (x.first_row ? "first row" : "—") : dur(x.gap_before)), num: true,
                    sort: (x) => x.gap_before || 0 },
                  { label: "Kind", fmt: (x) => (x.first_row ? pill("Log starts here", "none") : pill("Restart", "bad")) },
                  { label: "Boot report", fmt: (x) => (x.boot ? Object.entries(x.boot).map(([k, v]) => `${k} ${v}`).join(", ") : ""), cls: "mono" },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], fr.starts);
      }
      if ((d.alerts || []).length) {
        const { card: c2 } = card(body, "Alerts", "The device's own alerts (AIS-140 PacketType and alertId in otherData)");
        const counts = {};
        d.alerts.forEach((a) => { const key = a.code + "|" + a.id; counts[key] = counts[key] || { code: a.code, id: a.id, name: a.name, n: 0 }; counts[key].n++; });
        grid(c2, [{ label: "Code", key: "code", cls: "mono" }, { label: "Id", key: "id", num: true }, { label: "Alert", key: "name" },
                  { label: "Times", key: "n", num: true }], Object.values(counts).sort((a, b) => b.n - a.n));
        const mism = new Set(((d.ignition || {}).rows || []).filter((x) => x.disagrees).map((x) => x.k));
        const repd = new Set(((d.ignition || {}).rows || []).filter((x) => x.repeated).map((x) => x.k));
        const { card: c3 } = card(body, "Alerts in time order");
        grid(c3, [{ label: "Time", fmt: (a) => when(a.time), sort: (a) => a.time }, { label: "Alert", key: "name" },
                  { label: "Ignition input", fmt: (a) => (a.ign == null ? "—" : a.ign ? "on" : "off") },
                  { label: "", fmt: (a) => (mism.has(a.k) ? pill("Disagrees", "warn") : repd.has(a.k) ? pill("Repeated", "info") : "") },
                  { label: "Speed", fmt: (a) => num(a.speed, 0) + " km/h", num: true, sort: (a) => a.speed || 0 },
                  { label: "Supply", fmt: (a) => (a.supply == null ? "—" : a.supply.toFixed(1) + " V"), num: true, sort: (a) => a.supply || 0 },
                  { label: "", fmt: (a) => rowsBtn({ k: a.k }) }],
             d.alerts.filter((a) => !f.kind || f.kind !== "ignition" || a.id === "7" || a.id === "8"), { empty: "No alerts" });
      }
      if ((d.deep_sleep || []).length) {
        const { card: cd } = card(body, "Deep sleep", "DSEN: the last packet before the device sleeps — sent when its internal battery is low and the main supply is disconnected");
        grid(cd, [{ label: "Time", fmt: (x) => when(x.time), sort: (x) => x.time },
                  { label: "Main supply", fmt: (x) => (x.supply == null ? "—" : x.supply.toFixed(1) + " V"), num: true, sort: (x) => x.supply || 0 },
                  { label: "Internal battery", fmt: (x) => (x.internal_v == null ? "—" : x.internal_v.toFixed(2) + " V" + (x.internal_pct == null ? "" : ` · ${x.internal_pct}%`)), num: true },
                  { label: "Engine starting", fmt: (x) => (x.during_start ? "yes" : "") },
                  { label: "Then", fmt: (x) => (x.slept ? pill(x.next_after_s == null ? "Log ends" : `Silent ${dur(x.next_after_s)}`, "ok")
                                                         : pill(`Reported again after ${dur(x.next_after_s)}`, "warn")) },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], d.deep_sleep);
      }
      if ((d.flicker || []).length) {
        const { card: c4 } = card(body, "Ignition flicker", "The ignition input changing within 30 s of its previous change");
        grid(c4, [{ label: "Time", fmt: (x) => when(x.time), sort: (x) => x.time }, { label: "To", fmt: (x) => (x.to ? "on" : "off") },
                  { label: "After", fmt: (x) => dur(x.after_s), num: true, sort: (x) => x.after_s }, { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], d.flicker);
      }
      if ((d.firmware_changes || []).length) {
        const { card: c5 } = card(body, "Firmware changes");
        grid(c5, [{ label: "Time", fmt: (x) => when(x.time) }, { label: "From", key: "from", cls: "mono" }, { label: "To", key: "to", cls: "mono" },
                  { label: "", fmt: (x) => rowsBtn({ k: x.k }) }], d.firmware_changes);
      }
    },
    payload(body) {
      const p = rep.payload || {};
      const imu = p.imu;
      if (imu) {
        const { card: c } = card(body, "IMU");
        figs(c, [[num(imu.packets, 0), "packets"], [imu.samples_per_packet + "", "samples per packet"], [imu.rate_hz.toFixed(1) + " Hz", "sample rate"],
                 [num(imu.parked_packets, 0), `with ignition off (${imu.parked_pct.toFixed(0)}%)`], [num(imu.motion_minutes, 0), "minutes moving while parked"],
                 [(p.mb_per_hour || 0).toFixed(2) + " MB", "of log an hour"]]);
        if (imu.motion_times && imu.motion_times.length) {
          grid(c, [{ label: "Minute moving while parked", fmt: (r) => when(r.t), sort: (r) => r.t }, { label: "", fmt: (r) => rowsBtn({ time: r.t }) }],
               imu.motion_times.map((t) => ({ t })));
        }
      }
      if (p.can && p.can.length) {
        const { card: c } = card(body, "CAN values", "From the canData column");
        grid(c, [{ label: "Value", key: "key", cls: "mono" }, { label: "Rows", key: "rows", num: true, fmt: (r) => num(r.rows, 0) },
                 { label: "Distinct", key: "distinct", num: true }, { label: "Min", key: "min", num: true }, { label: "Max", key: "max", num: true },
                 { label: "Mean", key: "mean", num: true }, { label: "Most common", fmt: (r) => r.top.map(([v, n]) => `${v} ×${num(n, 0)}`).join(" · "), cls: "wrap" }], p.can);
        (p.alert_mismatch || []).forEach((a) => {
          const { card: c2 } = card(body, `${a.alert} and ${a.value}`);
          figs(c2, [[a.mean_set.toFixed(1), `${a.value} while ${a.alert} is set`], [a.mean_clear.toFixed(1), "while it is clear"], [num(a.rows, 0), "rows set"]]);
        });
      }
      if (!imu && !(p.can && p.can.length)) card(body, "Payload").card.appendChild(el("div", "an-empty", "No IMU or CAN data in this log"));
    },
    types(body) {
      const { card: c } = card(body, "Packet types");
      grid(c, [
        { label: "Type", key: "name" }, { label: "Rows", fmt: (t) => num(t.rows, 0), num: true, sort: (t) => t.rows }, { label: "%", key: "pct", num: true },
        { label: "First", fmt: (t) => when(t.first), sort: (t) => t.first }, { label: "Last", fmt: (t) => when(t.last), sort: (t) => t.last },
        { label: "With position", fmt: (t) => t.with_position_pct + "%", num: true, sort: (t) => t.with_position_pct },
        { label: "Moving", fmt: (t) => t.moving_pct + "%", num: true, sort: (t) => t.moving_pct },
        { label: "Satellites", fmt: (t) => t.sats.map(([v, n]) => `${v} ×${num(n, 0)}`).join(" · ") },
        { label: "History", fmt: (t) => num(t.history, 0), num: true, sort: (t) => t.history },
        { label: "Columns filled", fmt: (t) => t.columns.join(", "), cls: "wrap" },
        { label: "", fmt: (t) => rowsBtn({ type: t.type }) },
      ], rep.types || []);
    },
    columns(body) {
      const types = (rep.types || []).map((t) => t.type);
      const { card: c } = card(body, "Columns", "What each exported column holds, overall and per packet type");
      const cols = [
        { label: "Column", key: "column", cls: "mono" },
        { label: "Filled", fmt: (r) => r.filled_pct + "%", num: true, sort: (r) => r.filled_pct },
        { label: "Distinct", fmt: (r) => num(r.distinct, 0), num: true, sort: (r) => r.distinct },
        { label: "Min", key: "min", num: true }, { label: "Max", key: "max", num: true },
      ];
      types.slice(0, 6).forEach((t) => cols.push({ label: `Type ${t}`, fmt: (r) => (r.by_type && r.by_type[t] != null ? r.by_type[t] + "%" : "—"), num: true,
                                                    sort: (r) => (r.by_type || {})[t] || 0 }));
      cols.push({ label: "Most common", fmt: (r) => (r.top || []).map(([v, n]) => `${v} ×${num(n, 0)}`).join(" · "), cls: "wrap" });
      grid(c, cols, rep.columns || []);
    },
    rows(body) { renderRows(body); },
  };

  function distChart(title, pairs) {
    const box = el("div");
    box.appendChild(el("div", "an-fig-l", title));
    const ch = el("div", "an-chart sm");
    box.appendChild(ch);
    setTimeout(() => {
      if (!pairs.length) { ch.appendChild(el("div", "an-empty", "No readings")); return; }
      const W = Math.max(240, ch.clientWidth - 20), H = 130, B = 22, T = 10;
      const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, ch);
      const mx = Math.max(1, ...pairs.map((p) => p[1]));
      const bw = (W - 8) / pairs.length;
      pairs.forEach(([v, n], i) => {
        const h = (n / mx) * (H - T - B);
        const r = sv("rect", { x: 4 + i * bw + 1, y: H - B - h, width: Math.max(1, bw - 2), height: h, rx: 1.5, class: "bar" + (v === 0 ? " bad" : "") }, g);
        sv("title", {}, r).textContent = `${v}: ${num(n, 0)} rows`;
        if (pairs.length <= 24 || i % Math.ceil(pairs.length / 24) === 0) sv("text", { x: 4 + i * bw + bw / 2, y: H - 6, "text-anchor": "middle" }, g).textContent = v;
      });
    }, 0);
    return box;
  }

  // ---- the rows -------------------------------------------------------------------------------------
  async function renderRows(body) {
    const { card: c } = card(body, "Rows");
    const bar = el("div", "an-rowbar");
    c.appendChild(bar);
    const counts = rep.flag_counts || {};
    const mk = (flag, label, n, tone) => {
      const b = el("button", "an-chip tone-" + (tone || "info") + (rowsState.flag === flag ? " on" : ""));
      b.type = "button";
      b.appendChild(document.createTextNode(label));
      if (n != null) b.appendChild(el("b", null, num(n, 0)));
      b.addEventListener("click", () => { rowsState.flag = flag; rowsState.offset = 0; rowsState.at = null; rowsState.time = null; renderDetails(); });
      bar.appendChild(b);
    };
    mk("", "All", (rep.summary || {}).rows, "none");
    Object.keys(FLAG).filter((f) => counts[f]).forEach((f) => mk(f, FLAG[f][0], counts[f], FLAG[f][1]));
    const sel = el("select");
    sel.appendChild(new Option("Every type", ""));
    (rep.types || []).forEach((t) => sel.appendChild(new Option(`${t.name} (${num(t.rows, 0)})`, t.type)));
    sel.value = rowsState.type;
    sel.addEventListener("change", () => { rowsState.type = sel.value; rowsState.offset = 0; rowsState.at = null; renderDetails(); });
    bar.appendChild(sel);
    const pager = el("div", "an-pager");
    bar.appendChild(pager);
    const holder = el("div");
    c.appendChild(holder);
    const q = new URLSearchParams({ offset: rowsState.offset, limit: rowsState.limit });
    if (scope) { q.set("start", scope.start); q.set("end", scope.end); }
    if (rowsState.flag) q.set("flag", rowsState.flag);
    if (rowsState.type !== "") q.set("type", rowsState.type);
    if (rowsState.at != null) q.set("at", rowsState.at);
    else if (rowsState.time) q.set("time", rowsState.time);
    let d;
    try { d = await api(`/api/analysis/${encodeURIComponent(current)}/rows?${q}`); } catch (e) { holder.appendChild(el("div", "an-empty", e.message)); return; }
    const target = rowsState.at != null ? rowsState.at : rowsState.time ? (d.rows.find((r) => r.t >= rowsState.time) || {}).k : null;
    rowsState.offset = d.offset;
    rowsState.at = null; rowsState.time = null;
    const from = d.total ? d.offset + 1 : 0, to = Math.min(d.total, d.offset + d.rows.length);
    const prev = el("button", "ghost"); prev.type = "button"; prev.innerHTML = UI.icons["chevron-right"]; prev.style.transform = "scaleX(-1)";
    const next = el("button", "ghost"); next.type = "button"; next.innerHTML = UI.icons["chevron-right"];
    prev.disabled = d.offset <= 0; next.disabled = to >= d.total;
    prev.title = "Previous rows"; next.title = "Next rows";
    prev.addEventListener("click", () => { rowsState.offset = Math.max(0, d.offset - rowsState.limit); renderDetails(); });
    next.addEventListener("click", () => { rowsState.offset = d.offset + rowsState.limit; renderDetails(); });
    pager.append(el("span", null, `${num(from, 0)}–${num(to, 0)} of ${num(d.total, 0)}`), prev, next);
    const cols = [
      { label: "#", fmt: (r) => r.k + 1, num: true, sort: (r) => r.k },
      { label: "Device time", fmt: (r) => when(r.t), sort: (r) => r.t },
      { label: "Stored", fmt: (r) => when(r.ins), sort: (r) => r.ins || "" },
      { label: "Delay", fmt: (r) => r.delay == null ? "—" : dur(r.delay), num: true, sort: (r) => r.delay || 0 },
      { label: "Type", key: "type" },
      { label: "Speed", fmt: (r) => num(r.speed, 0), num: true, sort: (r) => r.speed || 0 },
      { label: "Latitude", fmt: (r) => r.lat == null ? "—" : r.lat.toFixed(6), cls: "mono", sort: (r) => r.lat || 0 },
      { label: "Longitude", fmt: (r) => r.lon == null ? "—" : r.lon.toFixed(6), cls: "mono", sort: (r) => r.lon || 0 },
      { label: "Sats", key: "sats", num: true }, { label: "GSM", key: "gsm", num: true },
      { label: "Ign", fmt: (r) => r.ign == null ? "—" : r.ign ? "on" : "off" },
      { label: "Supply", fmt: (r) => r.supply == null ? "—" : (r.supply > 100 ? r.supply / 1000 : r.supply).toFixed(1) + " V", num: true, sort: (r) => r.supply || 0 },
      { label: "History", fmt: (r) => r.history == null ? "—" : r.history ? "yes" : "no" },
      { label: "Frame", key: "frame", cls: "mono" }, { label: "Mark", key: "mark" }, { label: "Alert", key: "alert", cls: "mono" },
      { label: "Found", fmt: (r) => { const b = el("div", "an-flags"); (r.flags || []).forEach((f) => b.appendChild(flagPill(f))); return b; } },
    ];
    const w = grid(holder, cols, d.rows, { empty: "No rows match", onRow: openRow, target: (r) => r.k === target });
    w.style.maxHeight = "640px";
    if (pendingPlace) {
      const p = pendingPlace;
      pendingPlace = null;
      requestAnimationFrame(() => { w.scrollTop = p.rowsScroll || 0; work.scrollTop = p.scroll || 0; });
    } else if (target != null) setTimeout(() => { const tr = w.querySelector("tr.is-target"); if (tr) tr.scrollIntoView({ block: "center" }); }, 30);
  }

  async function openRow(r) {
    let d;
    try { d = await api(`/api/analysis/${encodeURIComponent(current)}/rows/${r.k}`); } catch (e) { return; }
    $("an-row-title").textContent = `Row ${r.k + 1} · ${when(r.t, true)}`;
    const fl = $("an-row-flags");
    fl.innerHTML = "";
    (d.flags || []).forEach((f) => fl.appendChild(flagPill(f)));
    const kv = $("an-row-kv");
    kv.innerHTML = "";
    Object.entries(d.columns).forEach(([k, v]) => {
      if (v === "") return;
      kv.append(el("dt", null, k), el("dd", null, v));
    });
    UI.paint($("an-row-dlg"));
    $("an-row-dlg").showModal();
  }

  // ---- settings, delete -----------------------------------------------------------------------------
  $("an-settings").addEventListener("click", () => openSettings(false));
  function openSettings(toProfiles) {
    const g = $("an-set-grid");
    g.innerHTML = "";
    const used = (rep && rep.settings) || settings;
    Object.keys(SETTING_LABELS).forEach((k) => {
      const lab = el("label", null, SETTING_LABELS[k]);
      lab.htmlFor = "an-set-" + k;
      const inp = el("input");
      inp.type = "number"; inp.step = "any"; inp.min = "0"; inp.id = "an-set-" + k; inp.dataset.key = k;
      inp.value = used[k] != null ? used[k] : defaults[k];
      const mark = () => inp.classList.toggle("changed", Number(inp.value) !== Number(defaults[k]));
      inp.addEventListener("input", mark);
      mark();
      g.append(lab, inp);
    });
    renderProfiles(profiles);
    $("an-set-dlg").showModal();
    if (toProfiles) setTimeout(() => { $("an-prof").scrollIntoView({ block: "start" }); const i = $("an-prof").querySelector("input"); if (i) i.focus(); }, 30);
  }

  // device profiles: the expected interval per mode for a device, or a firmware; a number left out
  // is taken from the log. Kept for every log (saved with either button of the dialog).
  const PROF_COLS = [["name", "Name", "", "text"], ["devices", "Devices", "1001, 1002", "text"],
                     ["firmware", "Firmware starts with", "7.5", "text"], ["active_s", "Active (s)", "", "num"],
                     ["standby_s", "Standby (s)", "", "num"], ["standby_min", "Standby for (min)", "", "num"],
                     ["sleep_s", "Sleep (s)", "", "num"]];
  function profileRow(p) {
    const tr = el("tr");
    PROF_COLS.forEach(([k, label, ph, kind]) => {
      const td = el("td");
      const inp = el("input");
      inp.type = kind === "num" ? "number" : "text";
      if (kind === "num") { inp.min = "0"; inp.step = "any"; }
      inp.placeholder = ph;
      inp.dataset.key = k;
      inp.setAttribute("aria-label", label);
      inp.value = p[k] == null ? "" : p[k];
      td.appendChild(inp);
      tr.appendChild(td);
    });
    const td = el("td");
    const x = el("button", "ui-icon-btn");
    x.type = "button";
    x.title = "Remove";
    x.setAttribute("aria-label", "Remove");
    x.innerHTML = UI.icon("x");
    x.addEventListener("click", () => { tr.remove(); paintProfiles(); });
    td.appendChild(x);
    tr.appendChild(td);
    return tr;
  }
  function paintProfiles() { $("an-prof-none").hidden = !!$("an-prof-body").children.length; }
  function renderProfiles(list) {
    const head = $("an-prof-headrow");
    head.innerHTML = "";
    PROF_COLS.forEach(([k, label, , kind]) => {
      const th = el("th", kind === "num" ? "num" : "", label);
      if (kind === "num") th.dataset.tip = "Left empty: as the log shows it";
      head.appendChild(th);
    });
    head.appendChild(el("th"));
    const body = $("an-prof-body");
    body.innerHTML = "";
    (list || []).forEach((p) => body.appendChild(profileRow(p)));
    paintProfiles();
  }
  $("an-prof-add").addEventListener("click", () => {
    const tr = profileRow({});
    $("an-prof-body").appendChild(tr);
    paintProfiles();
    tr.querySelector("input").focus();
  });
  const readProfiles = () => [...$("an-prof-body").querySelectorAll("tr")].map((tr) => Object.fromEntries(
    [...tr.querySelectorAll("input")].map((i) => [i.dataset.key, i.type === "number" ? (i.value === "" ? null : Number(i.value)) : i.value.trim()])));
  async function saveProfiles() {
    const d = await api("/api/analysis/profiles", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profiles: readProfiles() }) });
    profiles = d.profiles || [];
  }
  const readSettings = () => Object.fromEntries([...$("an-set-grid").querySelectorAll("input")].map((i) => [i.dataset.key, Number(i.value)]));
  $("an-set-dlg").querySelector("[data-cancel]").addEventListener("click", () => $("an-set-dlg").close());
  $("an-set-reset").addEventListener("click", () => {
    $("an-set-grid").querySelectorAll("input").forEach((i) => { i.value = defaults[i.dataset.key]; i.dispatchEvent(new Event("input")); });
  });
  $("an-set-save").addEventListener("click", async () => {
    try {
      await api("/api/analysis/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: readSettings() }) });
      await saveProfiles();
    }
    catch (e) { await UI.notice({ title: "Not saved", text: e.message }); return; }
    settings = readSettings();
    $("an-set-save").textContent = "Saved";
    setTimeout(() => { $("an-set-save").textContent = "Save as default"; }, 1500);
  });
  $("an-set-dlg").querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const s = readSettings();
    try { await saveProfiles(); } catch (err) { await UI.notice({ title: "Profiles not saved", text: err.message }); return; }
    $("an-set-dlg").close();
    if (!current) return;
    try {
      await api(`/api/analysis/${encodeURIComponent(current)}/analyse`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: s }) });
    } catch (err) { await UI.notice({ title: "Not analysed", text: err.message }); return; }
    await loadList(current);
  });
  $("an-delete").addEventListener("click", () => { if (current) removeLogs([current]); });

  // ---- comparison: devices fitted in one vehicle, side by side ------------------------------------------
  // Each log judged over the time all of them cover; a scorecard of every figure that tells them apart
  // (best and worst marked), the tracks on one map, speed and delay on one time axis, packets by mode.
  const DEV_COLOURS = TRIP_COLOURS;
  const devColour = (i) => DEV_COLOURS[i % DEV_COLOURS.length];
  let cmap = null, cmapLayers = null;
  const cmpHidden = new Set();
  async function showCompare(t) {
    $("an-report").hidden = true;
    renderRail();
    $("an-drop").hidden = true;
    writeUrl();
    if (!t.data) {
      $("an-compare").hidden = true;
      $("an-busy").hidden = false;
      $("an-busy-t").textContent = `Comparing ${t.ids.length} logs`;
      $("an-busy-bar").style.width = "100%";
      $("an-busy-pct").textContent = "";
      let d;
      try {
        d = await api("/api/analysis/compare?" + t.ids.map((id) => "ids=" + encodeURIComponent(id)).join("&"));
      } catch (e) {
        $("an-busy").hidden = true;
        await UI.notice({ title: "Not compared", text: e.message });
        return closeTab(t.key);
      }
      t.data = d;
      if (focusKey !== t.key) return;
      $("an-busy").hidden = true;
    }
    $("an-compare").hidden = false;
    cmpHidden.clear();
    renderCompare(t);
  }
  function fmtMetric(v, unit) {
    if (v == null || v === "") return "—";
    switch (unit) {
      case "%": return num(v, 1) + "%";
      case "s": return dur(v);
      case "min": return num(v, 1) + " min";
      case "km": return num(v, 1) + " km";
      case "km/h": return num(v, 0) + " km/h";
      case "m": return num(v, 0) + " m";
      case "V": return num(v, 1) + " V";
      default: return num(v, Number.isInteger(v) ? 0 : 1);
    }
  }
  function devLabel(d, i) {
    const b = el("span", "an-cmp-dev");
    const sw = el("i");
    sw.style.background = devColour(i);
    b.append(sw, el("span", "mono", d.device || d.file || d.id));
    return b;
  }
  function renderCompare(t) {
    const c = t.data;
    const w = c.window || {};
    $("an-cmp-n").textContent = `${c.devices.length} devices`;
    $("an-cmp-sub").textContent = w.common
      ? `${when(w.start, true)} – ${when(w.end, true)} · ${dur(w.seconds)} in common`
      : `No time in common — each whole log · ${when(w.start, true)} – ${when(w.end, true)}`;
    $("an-cmp-xlsx").href = "/api/analysis/compare.xlsx?" + t.ids.map((id) => "ids=" + encodeURIComponent(id)).join("&");
    // a tile per device: its colour, status, period; a click opens its log
    const devs = $("an-cmp-devs");
    devs.innerHTML = "";
    c.devices.forEach((d, i) => {
      const tile = el("button", "an-tile an-cmp-tile tone-" + (TONE[d.status] || "none"));
      tile.type = "button";
      tile.style.setProperty("--dev", devColour(i));
      const top = el("div", "an-tile-top");
      top.append(devLabel(d, i), el("span", "ui-pill sm", d.status_word + (d.issues ? " · " + d.issues : "")));
      tile.append(top);
      const ag = d.agreement || {};
      tile.append(el("div", "an-tile-fig", i === 0 ? "Reference" : ag.apart_median_m != null ? `${num(ag.apart_median_m, 0)} m apart` : "—"));
      tile.append(el("div", "an-tile-cap", i === 0
        ? [(d.firmware || []).join(", "), d.profile ? "profile " + d.profile : ""].filter(Boolean).join(" · ") || "Positions are measured from it"
        : ag.apart_median_m != null ? `median · 90% within ${num(ag.apart_p90_m, 0)} m · speed ±${num(ag.speed_diff_median, 1)} km/h` : "No moment in common with the reference"));
      tile.addEventListener("click", () => openLog(d.id));
      devs.appendChild(tile);
    });
    // the scorecard
    const box = $("an-cmp-score");
    box.innerHTML = "";
    const table = el("table", "an-cmp-table");
    const hr = el("tr");
    hr.appendChild(el("th", null, "Measure"));
    c.devices.forEach((d, i) => { const th = el("th", "num"); th.appendChild(devLabel(d, i)); hr.appendChild(th); });
    const thead = el("thead");
    thead.appendChild(hr);
    const tbody = el("tbody");
    let group = null;
    c.metrics.forEach((m) => {
      if (m.group !== group) {
        group = m.group;
        const gr = el("tr", "an-cmp-group");
        const td = el("td", null, group);
        td.colSpan = c.devices.length + 1;
        gr.appendChild(td);
        tbody.appendChild(gr);
      }
      const tr = el("tr");
      const lab = el("td", null, m.label);
      const better = m.better === "high" ? "Higher is better." : m.better === "low" ? "Lower is better." : m.better === "100" ? "Closest to 100% is best." : "";
      if (m.about || better) lab.dataset.tip = [m.about, better].filter(Boolean).join(" ");
      tr.appendChild(lab);
      m.values.forEach((v, i) => {
        const td = el("td", "num" + ((m.best || []).includes(i) ? " best" : (m.worst || []).includes(i) ? " worst" : ""), fmtMetric(v, m.unit));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    box.appendChild(table);
    // packets by mode, device by device
    const modes = [];
    c.devices.forEach((d, i) => (d.modes || []).forEach((g) => modes.push({ ...g, _d: d, _i: i })));
    const mb = $("an-cmp-modes");
    mb.innerHTML = "";
    grid(mb, [{ label: "Device", fmt: (g) => devLabel(g._d, g._i), sort: (g) => g._i }].concat(modeCols(false)), modes,
         { empty: "No ignition state to tell the modes apart" });
    drawCmpMap(c);
    drawCmpLines($("an-cmp-speed"), $("an-cmp-speed-legend"), c, 2, "km/h", false);
    drawCmpLines($("an-cmp-delay"), null, c, 7, "delay", true);
  }
  function drawCmpMap(c) {
    const box = $("an-cmp-map");
    if (!window.L) { box.textContent = "The map needs the internet"; return; }
    if (!cmap) {
      cmap = L.map(box, { zoomControl: true, attributionControl: true, keyboard: false }).setView([20.59, 78.96], 5);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(cmap);
      cmapLayers = L.layerGroup().addTo(cmap);
    }
    cmapLayers.clearLayers();
    const casing = getComputedStyle(box).getPropertyValue("--map-casing").trim() || "#10120f";
    const all = [];
    c.series.forEach((s, i) => {
      if (cmpHidden.has(i)) return;
      const colour = devColour(i), name = c.devices[i].device || "";
      (s.track || []).forEach((seg) => {
        let run = null;
        const runs = [];
        seg.forEach((p) => {
          const h = p[4] ? 1 : 0;
          if (!run || run.h !== h) { const prev = run && run.pts[run.pts.length - 1]; run = { h, pts: prev ? [prev] : [] }; runs.push(run); }
          run.pts.push([p[0], p[1]]);
          all.push([p[0], p[1]]);
        });
        // one vehicle, one road: each device a stripe narrower than the one under it, so all show
        const wd = Math.max(2, 9 - 2.2 * i);
        runs.filter((r) => r.pts.length > 1).forEach((r) => {
          if (r.h) { L.polyline(r.pts, { color: colour, weight: Math.max(1.5, wd / 2.5), opacity: mapNum("--map-hist", 0.45) }).bindTooltip(cardTip(`${name} · history`), TIP).addTo(cmapLayers); return; }
          if (i === 0) L.polyline(r.pts, { color: casing, weight: wd + 3, opacity: 0.5 }).addTo(cmapLayers);
          L.polyline(r.pts, { color: colour, weight: wd, opacity: 0.95 }).bindTooltip(cardTip(name), TIP).addTo(cmapLayers);
        });
      });
      (s.breaks || []).forEach((g) => {
        L.polyline([g.from, g.to], { color: colour, weight: 3.5, dashArray: "1 9", lineCap: "round", opacity: 0.95 })
          .bindTooltip(gapTip(g, name + " · " + (g.kind === "loss" ? "ignition on — data lost" : g.kind === "sleep" ? "ignition off" : "no ignition state")), TIP).addTo(cmapLayers);
      });
    });
    setTimeout(() => { cmap.invalidateSize(); if (all.length) cmap.fitBounds(all, { padding: [28, 28], maxZoom: 17, animate: false }); }, 50);
    const lg = $("an-cmp-map-legend");
    lg.innerHTML = "";
    c.devices.forEach((d, i) => {
      const b = el("button", "an-cmp-toggle" + (cmpHidden.has(i) ? " off" : ""));
      b.type = "button";
      b.appendChild(devLabel(d, i));
      b.title = cmpHidden.has(i) ? "Show" : "Hide";
      b.addEventListener("click", () => { if (cmpHidden.has(i)) cmpHidden.delete(i); else cmpHidden.add(i); drawCmpMap(c); });
      lg.appendChild(b);
    });
    [["Live", "--text", "line"], ["History", "--text", "line thin"], ["No data", "--text", "dots"]].forEach(([tx, col, k]) => {
      const sp = el("span");
      const ic = el("i", k);
      ic.style.setProperty("--c", `var(${col})`);
      sp.append(ic, document.createTextNode(tx));
      lg.appendChild(sp);
    });
  }
  // one line per device on the time they were compared over: speed, or the delay to InTouch (log scale)
  function drawCmpLines(box, legend, c, field, unit, logScale) {
    box.innerHTML = "";
    const w = c.window || {};
    const t0 = dt(w.start).getTime(), t1 = Math.max(t0 + 1000, dt(w.end).getTime());
    const W = Math.max(400, box.clientWidth - 20), H = logScale ? 200 : 230;
    const L_ = 46, R = 10, T = 14, B = 26, plotH = H - T - B;
    const x = (ms) => L_ + ((ms - t0) / (t1 - t0)) * (W - L_ - R);
    const vals = [];
    c.series.forEach((s) => (s.points || []).forEach((p) => { if (p[field] != null) vals.push(p[field]); }));
    if (!vals.length) { box.appendChild(el("div", "an-empty", "Nothing to draw")); return; }
    const vmax = logScale ? Math.max(10, ...vals) : Math.max(20, ...vals);
    const ymax = logScale ? Math.ceil(Math.log10(vmax)) : Math.ceil(vmax / 20) * 20;
    const y = logScale ? (v) => T + plotH * (1 - Math.log10(Math.max(1, v)) / ymax) : (v) => T + plotH - (v / ymax) * plotH;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, height: H }, box);
    const ticks = logScale ? Array.from({ length: ymax + 1 }, (_, i) => Math.pow(10, i)) : [0, ymax / 4, ymax / 2, (3 * ymax) / 4, ymax];
    ticks.forEach((v) => {
      sv("line", { x1: L_, x2: W - R, y1: y(v), y2: y(v), class: "grid" }, g);
      sv("text", { x: L_ - 6, y: y(v) + 4, "text-anchor": "end" }, g).textContent = logScale ? dur(v) : Math.round(v);
    });
    const span = (t1 - t0) / 1000;
    const stepH = span > 36 * 3600 ? 6 : span > 12 * 3600 ? 2 : span > 4 * 3600 ? 1 : span > 3600 ? 0.5 : 0.25;
    const first = new Date(t0); first.setMinutes(0, 0, 0);
    for (let ms = first.getTime(); ms <= t1; ms += stepH * 3600 * 1000) {
      if (ms < t0) continue;
      const d = new Date(ms);
      sv("text", { x: x(ms), y: H - 8, "text-anchor": "middle" }, g).textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    c.series.forEach((s, i) => {
      const bw = (s.bucket_s || 60) * 1000;
      let d = "", prev = null;
      (s.points || []).forEach((p) => {
        if (p[field] == null) { prev = null; return; }
        const ms = dt(p[0]).getTime();
        const broke = prev === null || ms - prev > bw * 1.5;
        d += (broke ? "M" : "L") + x(ms + bw / 2).toFixed(1) + " " + y(p[field]).toFixed(1) + " ";
        prev = ms;
      });
      sv("path", { d, fill: "none", stroke: devColour(i), "stroke-width": 1.5, opacity: 0.9 }, g);
    });
    // hover: each device's value at that moment
    const tip = el("div", "an-tip");
    tip.hidden = true;
    box.appendChild(tip);
    const hl = sv("line", { y1: T, y2: T + plotH, class: "hover-line", visibility: "hidden" }, g);
    const hit = sv("rect", { x: L_, y: T, width: W - L_ - R, height: plotH, fill: "transparent", style: "cursor: crosshair" }, g);
    hit.addEventListener("mousemove", (e) => {
      const rect = box.querySelector("svg").getBoundingClientRect();
      const X = ((e.clientX - rect.left) / rect.width) * W;
      const ms = t0 + ((X - L_) / (W - L_ - R)) * (t1 - t0);
      hl.setAttribute("x1", X); hl.setAttribute("x2", X); hl.setAttribute("visibility", "visible");
      const parts = [when(new Date(ms).toISOString().slice(0, 19))];
      c.series.forEach((s, i) => {
        const pts = s.points || [];
        if (!pts.length) return;
        const p = pts[Math.min(pts.length - 1, Math.max(0, bisect(pts, ms)))];
        const near = Math.abs(dt(p[0]).getTime() - ms) <= (s.bucket_s || 60) * 1500;
        parts.push(`${c.devices[i].device}: ${near && p[field] != null ? (logScale ? dur(p[field]) : num(p[field], 0) + " " + unit) : "—"}`);
      });
      tip.hidden = false;
      tip.textContent = parts.join(" · ");
      const tx = e.clientX - box.getBoundingClientRect().left + 12;
      tip.style.left = Math.min(tx, box.clientWidth - tip.offsetWidth - 6) + "px";
      tip.style.top = "6px";
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; hl.setAttribute("visibility", "hidden"); });
    if (legend) {
      legend.innerHTML = "";
      c.devices.forEach((d, i) => legend.appendChild(devLabel(d, i)));
    }
  }

  // redraw the charts when the window (or a pane) changes width
  let rt = 0;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => {
      if (rep && view === "summary" && !$("an-report").hidden) { drawTimeline(); drawLatency($("an-latency")); drawDelay($("an-delay")); drawVolts($("an-volts")); if (map) map.invalidateSize(); }
      const t = tabOf(focusKey);
      if (t && t.kind === "compare" && t.data && !$("an-compare").hidden) {
        drawCmpLines($("an-cmp-speed"), $("an-cmp-speed-legend"), t.data, 2, "km/h", false);
        drawCmpLines($("an-cmp-delay"), null, t.data, 7, "delay", true);
        if (cmap) cmap.invalidateSize();
      }
    }, 150);
  });

  UI.paint(document);
  loadList();
})();
