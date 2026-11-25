/* Firmware test — a device on a new firmware, every packet held against its sheet.
 *
 *   tabs      one per device (IMEI), like Log analysis — a picker between the device's tests; a device
 *             whose tab is closed comes back from "Open a test"; each keeps its filter and scroll
 *   left      the test plan's categories (the tracker's), with how far each has got
 *   a test    its verdict and state, tiles (packets, failed, warnings, checksum, scenarios), the checks
 *             (one row per kind of deviation, worst first), the scenarios (received, passed, failed),
 *             and every packet — a tile, a check, a scenario or an insight shows its packets
 *   rail      bookmark tabs on the right edge, folded at first: Live (the packets as they land — all, or
 *             live only), Map (the test's track, as Log analysis draws a log), Scorecard, Defects, Insights
 *             (what the test found, in words, worst first) and Packet (the one clicked, field by field)
 *
 * A live test takes packets from the listener on its port (the device keeps relaying to its platform)
 * and the page polls the test on screen; a file test is judged once. All of it from /api/fwtest/….
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
  const ic = (name) => {                 // an icon from the shell's one map, as an element
    const s = el("span", "ui-ico");
    s.innerHTML = UI.icons[name] || "";
    return s;
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
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
  };
  const TABS_KEY = "runway.fwtest.tabs";
  const RAIL_KEY = "runway.fwtest.rail";
  const PAGE = 100;

  const VERDICT = { fail: ["Fail", "bad"], incomplete: ["Incomplete", "warn"], pass: ["Pass", "ok"],
                    in_progress: ["In progress", "info"] };   // a running test: the verdict comes when it stops
  const LEVEL = { fail: ["Fail", "bad"], warn: ["Warning", "warn"], pass: ["Pass", "ok"] };
  const INSIGHT = { fail: ["alert", "bad", "Failing"], warn: ["info", "warn", "Worth a look"],
                    info: ["clock", "info", "On the bench"], ok: ["tick", "ok", "As the sheet says"] };

  let runs = [];
  let cur = null;          // the test on screen
  let filt = { level: "", check: "", scenario: "", label: "" };
  let shown = 0;           // packets listed so far
  let poll = null;
  let polls = 0;
  let tabs = [];           // [{id, st: {filt, scroll}}]
  let focusId = null;
  let railTab = store.get(RAIL_KEY) || null;
  let pkOpen = null;       // the packet in the rail
  let pkData = null;

  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const fmtShort = (d) => {
    const now = new Date();
    const same = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return same ? `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` : fmt(d);
  };
  const whenEpoch = (s) => (s ? fmtShort(new Date(s * 1000)) : "");
  const whenUtc = (s) => (s ? fmtShort(new Date(s.replace(" ", "T") + "Z")) : "");
  const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
  // live or history: AIS-140's L / H, and an emergency packet's NM (normal) / SP (stored) — as the gate reads it
  const LATE = { L: false, NM: false, H: true, SP: true };
  const isLate = (s) => LATE[s] === true;
  const liveWord = (s) => (LATE[s] === false ? "Live" : LATE[s] === true ? "History" : s || "");
  const runOf = (id) => runs.find((r) => r.id === id);
  const devOf = (v) => v.device || Object.keys((v.summary && v.summary.devices) || {})[0] || v.title;

  // the device's settings, in words: "Set to on 10 s, off 60 s, asleep 600 s after 10 min, history LIFO, PVT 51 fields"
  function configWords(c) {
    const b = [];
    if (c.ign_on_s) b.push(`on ${c.ign_on_s} s`);
    if (c.ign_off_s) b.push(`off ${c.ign_off_s} s`);
    if (c.sleep_s) b.push(`asleep ${c.sleep_s} s` + (c.sleep_after_min ? ` after ${c.sleep_after_min} min` : ""));
    if (c.history_order) b.push(`history ${c.history_order.toUpperCase()}`);
    for (const [h, f] of Object.entries(c.formats || {})) b.push(`${h} ${f} fields`);
    return b.length ? "Set to " + b.join(", ") : "Configuration not given";
  }

  function source(v) {
    const s = v.source || {};
    if (s.kind === "live") {
      if (v.state !== "live") return `Listener :${s.port}, stopped`;
      return v.listening ? `Listening on :${s.port}` : `Waiting for the listener on :${s.port}`;
    }
    return s.name || "A file";
  }

  async function loadRuns() {
    runs = await api("/api/fwtest/runs");
    renderTabs();
  }

  // ── tabs: one per open test ────────────────────────────────────────────────────────────────
  function saveTabs() {
    store.set(TABS_KEY, JSON.stringify({ tabs: tabs.map((t) => t.id), focus: focusId }));
  }
  function stash() {
    const t = tabs.find((x) => x.id === focusId);
    if (t) t.st = { filt: { ...filt }, scroll: $("fw-work").scrollTop };
  }
  // one tab per device (IMEI): another test of a device with a tab takes that tab
  const devKey = (id) => { const v = runOf(id); return v ? devOf(v) : id; };
  function openTab(id) {
    if (!tabs.find((t) => t.id === id)) {
      const same = tabs.find((t) => devKey(t.id) === devKey(id));
      if (same) {
        if (same.id === focusId) stash();
        same.id = id;
        same.st = null;
        if (same.id !== focusId) focusId = null;
      } else {
        const at = tabs.findIndex((t) => t.id === focusId);
        tabs.splice(at < 0 ? tabs.length : at + 1, 0, { id, st: null });     // beside the tab on screen
      }
    }
    return focusTab(id);
  }
  async function focusTab(id) {
    if (id !== focusId) stash();
    focusId = id;
    saveTabs();
    const t = tabs.find((x) => x.id === id);
    filt = t && t.st ? { ...t.st.filt } : { level: "", check: "", scenario: "", label: "" };
    pkOpen = null;
    pkData = null;
    renderTabs();
    if (!t) return showEmpty();
    try {
      cur = await api("/api/fwtest/runs/" + encodeURIComponent(id));
    } catch (e) {
      return closeTab(id);
    }
    if (focusId !== id) return;                       // another tab was chosen meanwhile
    renderRun();
    loadCats();
    loadDefects();
    coachKeys = null;
    tlKey = "";
    liveKey = "";
    liveFresh = 0;
    if (railTab === "live") loadLive(true).catch(() => {});
    loadTimeline().catch(() => {});
    await loadPackets(true);
    if (view === "plan") loadPlan();
    if (t.st) $("fw-work").scrollTop = t.st.scroll || 0;
    if (railTab === "packet") renderRail();
    schedule();
  }
  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (id !== focusId) { saveTabs(); renderTabs(); return; }
    focusId = null;
    cur = null;
    const next = tabs[i] || tabs[i - 1];
    if (next) return focusTab(next.id);
    saveTabs();
    renderTabs();
    showEmpty();
  }
  function renderTabs() {
    const host = $("fw-tabs");
    host.textContent = "";
    for (const t of tabs) {
      const v = runOf(t.id);
      const tab = el("div", "tcp-tab" + (t.id === focusId ? " active" : ""));
      tab.addEventListener("click", () => { if (t.id !== focusId) focusTab(t.id); });
      const [, tone] = v ? VERDICT[v.summary.verdict] || ["", "none"] : ["", "none"];
      tab.appendChild(el("span", "fw-item-dot tone-" + tone + (v && v.state === "live" && v.listening ? " live" : "")));
      tab.appendChild(el("span", "tcp-tab-port", v ? devOf(v) : t.id));
      if (v) tab.appendChild(el("span", "tcp-tab-sub muted", v.source && v.source.kind === "live" ? `:${v.source.port}` : "File"));
      const x = el("button", "tcp-tab-x");
      x.type = "button";
      x.appendChild(ic("x"));
      x.title = "Close";
      x.setAttribute("aria-label", "Close");
      x.addEventListener("click", (e) => { e.stopPropagation(); closeTab(t.id); });
      tab.appendChild(x);
      host.appendChild(tab);
    }
    const add = el("button", "tcp-tab-add");
    add.type = "button";
    add.appendChild(ic("plus"));
    add.title = "New test";
    add.setAttribute("aria-label", "New test");
    add.addEventListener("click", newTest);
    host.appendChild(add);
    // a device without a tab: its newest test, reopened from here
    const open = new Set(tabs.map((t) => devKey(t.id)));
    const closed = {};
    for (const r of runs) {
      const d = devOf(r);
      if (!open.has(d) && (!closed[d] || r.created > closed[d].created)) closed[d] = r;
    }
    const list = Object.values(closed).sort((a, b) => b.created - a.created);
    if (list.length) {
      const sel = el("select", "fw-reopen");
      sel.setAttribute("aria-label", "Open a test");
      const first = el("option", "", `Open a test (${list.length})`);
      first.value = "";
      sel.appendChild(first);
      for (const r of list) {
        const o = el("option", "", `${devOf(r)} · ${fmtShort(new Date(r.created * 1000))} · ${(VERDICT[r.summary.verdict] || [""])[0]}`);
        o.value = r.id;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => { if (sel.value) openTab(sel.value); });
      host.appendChild(sel);
    }
  }
  function showEmpty() {
    clearTimeout(poll);
    $("fw-run").hidden = true;
    $("fw-empty").hidden = false;
    renderRail();
  }

  // ── one test ───────────────────────────────────────────────────────────────────────────────
  function schedule() {
    clearTimeout(poll);
    if (cur && cur.state === "live") poll = setTimeout(refresh, 2500);
  }

  async function refresh() {
    if (!cur) return;
    const id = cur.id;
    try {
      const v = await api("/api/fwtest/runs/" + encodeURIComponent(id));
      if (focusId !== id) return;
      const grew = v.summary.packets !== cur.summary.packets;
      cur = v;
      const i = runs.findIndex((r) => r.id === v.id);
      if (i >= 0) runs[i] = v;
      if (++polls % 4 === 0) {
        await loadRuns();
        if (firmwareOf(cur)) fetchPlan(firmwareOf(cur)).catch(() => {});      // the categories keep up
      } else renderTabs();
      renderRun();
      coach(v);
      if (grew && railTab === "live") loadLive().catch(() => {});
      if (grew && railTab === "map") wantTrack();
      $("fw-rail-live").classList.toggle("on", v.state === "live" && !!v.listening);
      if (grew && polls % 2 === 0) loadTimeline().catch(() => {});
      if (grew && polls % 4 === 0) loadDefects();
      if (grew && shown <= PAGE) await loadPackets(true);
    } catch (e) { /* the next turn tries again */ }
    schedule();
  }

  function tile(label, icon, fig, cap, tone, onClick, tip) {
    const t = el(onClick ? "button" : "div", "fw-tile tone-" + tone);
    if (onClick) { t.type = "button"; t.addEventListener("click", onClick); }
    if (tip) t.dataset.tip = tip;
    const l = el("span", "fw-tile-label");
    l.appendChild(ic(icon));
    l.appendChild(document.createTextNode(label));
    t.appendChild(l);
    t.appendChild(el("span", "fw-tile-fig", fig));
    t.appendChild(el("span", "fw-tile-cap", cap));
    return t;
  }

  function renderRun() {
    const v = cur;
    const s = v.summary;
    $("fw-empty").hidden = true;
    $("fw-run").hidden = false;
    $("fw-title").textContent = devOf(v);
    $("fw-spec").textContent = s.spec;
    const [word, tone] = VERDICT[s.verdict] || ["", "none"];
    const vp = $("fw-verdict");
    vp.className = "ui-pill " + tone;
    vp.textContent = word;
    vp.dataset.tip = s.verdict === "in_progress"
      ? "The verdict is given when the test stops. So far: " + (VERDICT[s.outcome] || [""])[0] +
        (s.failed ? ` — ${num(s.failed)} packet${s.failed === 1 ? "" : "s"} failed` : "")
      : "Fail: a packet broke the sheet. Incomplete: nothing failed, but not every scenario was seen. Pass: every scenario seen, nothing failed";
    const st = $("fw-state");
    st.className = "ui-pill sm " + (v.state === "live" ? (v.listening ? "ok" : "warn") : "muted");
    st.textContent = source(v);
    const mine = runs.filter((r) => devOf(r) === devOf(v)).sort((a, b) => b.created - a.created);
    const sel = $("fw-test-sel");
    sel.hidden = mine.length < 2;
    if (mine.length > 1 && sel.dataset.for !== mine.map((r) => r.id).join()) {
      sel.textContent = "";
      for (const r of mine) {
        const o = el("option", "", `${fmtShort(new Date(r.created * 1000))} · ${source(r)}`);
        o.value = r.id;
        sel.appendChild(o);
      }
      sel.dataset.for = mine.map((r) => r.id).join();
    }
    sel.value = v.id;
    const fw = Object.keys(s.firmware || {});
    const bits = [];
    if (fw.length) bits.push("Firmware " + fw.join(", "));
    if (s.first) bits.push(`${fmt(new Date(s.first * 1000))} → ${fmtShort(new Date(s.last * 1000))}`);
    const pinned = Object.values(s.expect || {});
    if (pinned.length) bits.push("Expected " + pinned.join(", "));
    bits.push(configWords(s.config || {}));
    $("fw-sub").textContent = bits.join(" · ");
    const live = v.source && v.source.kind === "live";
    $("fw-stop").hidden = !(live && v.state === "live");
    $("fw-resume").hidden = !(live && v.state !== "live");
    $("fw-xlsx").href = "/api/fwtest/runs/" + encodeURIComponent(v.id) + "/report.xlsx";

    const tiles = $("fw-tiles");
    tiles.textContent = "";
    const pct = (n) => (s.packets ? Math.round((n * 100) / s.packets) + "% of packets" : "");
    tiles.appendChild(tile("Packets", "list", num(s.packets), `${num(s.live)} live · ${num(s.history)} history`, "info",
      () => setFilter({}), "Every packet the device sent in this test"));
    tiles.appendChild(tile("Failed", "alert", num(s.failed), pct(s.failed), s.failed ? "bad" : "ok",
      () => setFilter({ level: "fail" }), "Packets that broke the sheet in at least one way"));
    tiles.appendChild(tile("Warnings", "info", num(s.warned), pct(s.warned), s.warned ? "warn" : "ok",
      () => setFilter({ level: "warn" }), "Packets with something worth a look — resent, late, out of order — failed or not"));
    const c = s.checksum || {};
    const checked = (c.ok || 0) + (c.bad || 0) + (c.missing || 0);
    const cbad = (c.bad || 0) + (c.missing || 0);
    tiles.appendChild(tile("Checksum", "check", checked ? `${num(c.ok || 0)} of ${num(checked)}` : "—",
      cbad ? [c.bad ? `${num(c.bad)} wrong` : "", c.missing ? `${num(c.missing)} missing` : ""].filter(Boolean).join(" · ") : "All correct",
      cbad ? "bad" : "ok", cbad ? () => setFilter({ check: "checksum", label: "Checksum" }) : null,
      "The XOR of every byte from $ up to *, against the two digits the device sent"));
    tiles.appendChild(tile("Scenarios", "tick", `${s.scenarios_seen} of ${s.scenarios_total}`,
      s.scenarios_seen < s.scenarios_total ? `${s.scenarios_total - s.scenarios_seen} not seen yet` : "All seen",
      s.scenarios_seen < s.scenarios_total ? "warn" : "ok", null, "Packet types and alerts the test has seen the device send"));

    const tb = $("fw-checks").querySelector("tbody");
    tb.textContent = "";
    $("fw-checks-n").textContent = s.checks.length ? String(s.checks.length) : "";
    $("fw-checks-none").hidden = !!s.checks.length;
    for (const ch of s.checks) {
      const tr = el("tr", filt.check === ch.key ? "on" : "");
      const [w, t] = LEVEL[ch.level];
      const r = el("td", "c-res");
      r.appendChild(el("span", "ui-pill sm " + t, w));
      tr.appendChild(r);
      const name = el("td", "", ch.label);
      if (ch.about || ch.source) name.dataset.tip = [ch.about, ch.source ? "Rule from — " + ch.source : ""].filter(Boolean).join("\n");
      tr.appendChild(name);
      tr.appendChild(el("td", "num", num(ch.packets)));
      tr.appendChild(el("td", "ex", ch.example));
      tr.addEventListener("click", () => setFilter({ check: ch.key, label: ch.label }));
      tb.appendChild(tr);
    }

    // per scenario: received, passed (did not fail), failed — a row shows its packets, Failed only those
    const sb = $("fw-scen").querySelector("tbody");
    sb.textContent = "";
    $("fw-scen-n").textContent = `${s.scenarios_seen} of ${s.scenarios_total}`;
    for (const sc of s.scenarios) {
      const tr = el("tr", (sc.count ? "" : "none") + (filt.scenario === sc.key ? " on" : ""));
      const name = el("td", "sc");
      const mark = ic(sc.count ? "tick" : "clock");
      mark.classList.add(sc.count ? (sc.failed ? "failed" : "seen") : "unseen");
      name.appendChild(mark);
      name.appendChild(el("span", "", sc.label));
      tr.appendChild(name);
      if (!sc.count) {
        const ns = el("td", "num dim", "Not seen");
        ns.colSpan = 3;
        tr.appendChild(ns);
      } else {
        tr.appendChild(el("td", "num", num(sc.count)));
        tr.appendChild(el("td", "num" + (sc.passed ? " good" : ""), num(sc.passed)));
        const f = el("td", "num" + (sc.failed ? " bad" : ""), num(sc.failed));
        if (sc.failed) {
          f.addEventListener("click", (ev) => {
            ev.stopPropagation();
            setFilter({ scenario: sc.key, level: "fail", label: sc.label });
          });
        }
        tr.appendChild(f);
        tr.addEventListener("click", () => setFilter({ scenario: sc.key, label: sc.label }));
      }
      sb.appendChild(tr);
    }
    renderFilter();
    renderRail();
  }

  function renderFilter() {
    const has = filt.check || filt.scenario;
    $("fw-filter").hidden = !has;
    $("fw-filter-t").textContent = has ? filt.label : "";
    for (const b of $("fw-levels").querySelectorAll("button")) b.classList.toggle("on", b.dataset.level === filt.level);
  }

  function setFilter(f, toPackets) {
    filt = { level: f.level || "", check: f.check || "", scenario: f.scenario || "", label: f.label || "" };
    renderRun();
    loadPackets(true).then(() => {
      if (toPackets) $("fw-packets").closest(".fw-card").scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  // ── the packets ────────────────────────────────────────────────────────────────────────────
  async function loadPackets(reset) {
    if (!cur) return;
    const offset = reset ? 0 : shown;
    const q = new URLSearchParams({ level: filt.level, check: filt.check, scenario: filt.scenario, offset, limit: PAGE });
    const d = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/packets?${q}`);
    const tb = $("fw-packets").querySelector("tbody");
    if (reset) { tb.textContent = ""; shown = 0; }
    for (const r of d.rows) tb.appendChild(packetRow(r));
    shown += d.rows.length;
    $("fw-pk-n").textContent = d.total ? num(d.total) : "";
    $("fw-pk-none").hidden = d.total > 0;
    $("fw-more").hidden = shown >= d.total;
  }

  function packetRow(r) {
    const tr = el("tr", r.n === pkOpen ? "on" : "");
    tr.dataset.n = String(r.n);
    tr.appendChild(el("td", "num", String(r.n)));
    tr.appendChild(el("td", "", whenEpoch(r.received)));
    tr.appendChild(el("td", "", whenUtc(r.time)));
    tr.appendChild(el("td", "mono", [r.header, r.type, r.alert].filter(Boolean).join(" ")));
    tr.appendChild(el("td", "", liveWord(r.status)));
    tr.appendChild(el("td", "num mono", r.frame || ""));
    const [w, t] = LEVEL[r.level];
    const res = el("td", "c-res");
    res.appendChild(el("span", "ui-pill sm " + t, w));
    tr.appendChild(res);
    // the flag the list is filtered by leads; else the worst
    const first = (filt.check && r.flags.find((f) => f.key === filt.check)) ||
      (filt.level === "warn" && r.flags.find((f) => f.level === "warn")) ||
      r.flags.find((f) => f.level === r.level) || r.flags[0];
    const more = r.flags.length > 1 ? ` (+${r.flags.length - 1})` : "";
    tr.appendChild(el("td", "flag", first ? first.label + ": " + first.text + more : ""));
    tr.addEventListener("click", () => openPacket(r.n));
    return tr;
  }

  async function openPacket(n) {
    if (!cur || n < 1 || n > cur.summary.packets) return;
    pkData = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/packets/${n}`);
    pkOpen = n;
    for (const tr of $("fw-packets").querySelectorAll("tbody tr")) tr.classList.toggle("on", tr.dataset.n === String(n));
    railTab = "packet";
    store.set(RAIL_KEY, railTab);
    renderRail();
  }

  // ── the side rail ──────────────────────────────────────────────────────────────────────────
  const RAIL = { live: "Live packets", map: "Map", score: "Scorecard", defects: "Defects", insight: "Insights", packet: "Packet" };

  function renderRail() {
    const rail = $("fw-rail");
    const show = !!cur && !$("fw-run").hidden;
    rail.hidden = !show;
    for (const b of document.querySelectorAll(".fw-rail-tab")) {
      b.classList.toggle("on", show && b.dataset.rail === railTab);
      b.setAttribute("aria-selected", String(show && b.dataset.rail === railTab));
    }
    const fails = show ? (cur.insights || []).filter((i) => i.level === "fail").length : 0;
    $("fw-rail-n").textContent = fails ? String(fails) : "";
    const open = show && !!railTab;
    $("fw-rail-panel").hidden = !open;
    if (!open) return;
    $("fw-rail-title").textContent = RAIL[railTab];
    const body = $("fw-rail-body");
    const place = (cur && cur.id) + ":" + railTab + (railTab === "packet" ? ":" + pkOpen : "");
    body.classList.toggle("is-map", railTab === "map");
    if (railTab === "map" && body.dataset.place === place && mapPane && mapPane.parentNode === body) {
      mapShown();                    // the map stays as the reader left it: a poll's redraw never rebuilds it
      return;
    }
    const keep = body.dataset.place === place ? body.scrollTop : 0;     // a poll's redraw keeps the reader's place
    body.textContent = "";
    if (railTab === "live") renderLive(body);
    else if (railTab === "map") renderMap(body);
    else if (railTab === "insight") renderInsights(body);
    else if (railTab === "score") renderScore(body);
    else if (railTab === "defects") renderDefects(body);
    else renderPacket(body);
    body.dataset.place = place;
    body.scrollTop = keep;
  }

  // ── live packets: what lands from the device, as it lands ───────────────────────────────────────
  const LIVE_N = 80;
  let liveRows = [];
  let liveKey = "";
  let liveFresh = 0;           // the newest packet before the last load — those after it are new
  let liveFlashed = new Set(); // new packets already flashed: the rail redraws on every poll
  let livePaused = false;
  const LIVE_SHOW_KEY = "runway.fwtest.liveshow";
  let liveShow = store.get(LIVE_SHOW_KEY) === "live" ? "live" : "all";   // all, or live only: every packet but history

  async function loadLive(force) {
    if (!cur || (livePaused && !force)) return;
    const key = cur.id + ":" + cur.summary.packets + ":" + liveShow;
    if (key === liveKey && !force) return;
    const d = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/packets?limit=${LIVE_N}` +
                        (liveShow === "live" ? "&status=live" : ""));
    if (!cur || !key.startsWith(cur.id + ":")) return;
    const same = liveKey.startsWith(cur.id + ":") && liveKey.endsWith(":" + liveShow);
    liveFresh = same && liveRows.length ? liveRows[0].n : 0;     // the first load of a test or a set flashes nothing
    liveFlashed = new Set();
    liveKey = key;
    liveRows = d.rows;
    if (railTab === "live") renderRail();
  }

  function renderLive(body) {
    if (!liveKey.startsWith(cur.id + ":")) {
      body.appendChild(el("div", "fw-none", "Loading"));
      loadLive(true).catch(() => {});
      return;
    }
    const live = cur.state === "live";
    $("fw-rail-sub").textContent = live ? (cur.listening ? `on :${cur.source.port}` : "waiting for the listener") : "test stopped";
    const bar = el("div", "fw-live-bar");
    const dot = el("span", "fw-live-dot" + (live && cur.listening && !livePaused ? " on" : ""));
    bar.appendChild(dot);
    bar.appendChild(el("span", "fw-live-t", livePaused ? "Paused" : live ? "Landing as it comes" : "The last packets"));
    bar.appendChild(el("span", "spacer"));
    // all, or live only — each with how many: the live ones are every packet but history
    const seg = el("div", "tcp-seg fw-live-seg");
    seg.setAttribute("role", "tablist");
    seg.setAttribute("aria-label", "Packets shown");
    const hist = cur.summary.late != null ? cur.summary.late : cur.summary.history || 0;
    for (const [id, label, n, tip] of [
      ["all", "All", cur.summary.packets, "Every packet, history included"],
      ["live", "Live", cur.summary.packets - hist, "Every packet but history — what the device sends as it happens"]]) {
      const b = el("button", "seg" + (liveShow === id ? " on" : ""));
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(liveShow === id));
      b.dataset.tip = tip;
      b.appendChild(document.createTextNode(label + " "));
      b.appendChild(el("span", "fw-live-n", num(n)));
      b.addEventListener("click", () => {
        if (liveShow === id) return;
        liveShow = id;
        store.set(LIVE_SHOW_KEY, id);
        loadLive(true).catch(() => {});
      });
      seg.appendChild(b);
    }
    bar.appendChild(seg);
    if (live) {
      const pb = el("button", "ghost mini");
      pb.type = "button";
      pb.appendChild(ic(livePaused ? "play" : "stop"));
      pb.appendChild(document.createTextNode(livePaused ? "Resume" : "Pause"));
      pb.addEventListener("click", () => { livePaused = !livePaused; if (!livePaused) loadLive(true); else renderRail(); });
      bar.appendChild(pb);
    }
    body.appendChild(bar);
    if (!liveRows.length) {
      body.appendChild(el("div", "fw-none", liveShow === "live" && cur.summary.packets ? "No live packet yet" : "No packet yet"));
      return;
    }
    const ul = el("ul", "fw-live");
    for (const r of liveRows) {
      const isNew = liveFresh && r.n > liveFresh && !liveFlashed.has(r.n);
      if (isNew) liveFlashed.add(r.n);
      const li = el("li", "fw-live-row lv-" + r.level + (isNew ? " new" : ""));
      const top = el("div", "fw-live-top");
      top.appendChild(el("span", "fw-live-time", whenEpoch(r.received)));
      top.appendChild(el("span", "fw-live-kind mono", [r.header, r.type, r.alert].filter(Boolean).join(" ")));
      if (r.status) top.appendChild(el("span", "ui-pill sm " + (isLate(r.status) ? "info" : "ok"), liveWord(r.status)));
      if (r.frame) top.appendChild(el("span", "fw-live-frame mono", "#" + r.frame));
      const [w, t] = LEVEL[r.level];
      top.appendChild(el("span", "ui-pill sm " + t, w));
      li.appendChild(top);
      li.appendChild(el("div", "fw-live-raw mono", r.raw));
      if (r.flags.length) {
        const f = r.flags.find((x) => x.level === r.level) || r.flags[0];
        li.appendChild(el("div", "fw-live-flag", `${f.label}: ${f.text}` + (r.flags.length > 1 ? ` (+${r.flags.length - 1})` : "")));
      }
      li.addEventListener("click", () => openPacket(r.n));
      ul.appendChild(li);
    }
    body.appendChild(ul);
  }

  // ── the map: the test's packets on Log analysis's map, as they land ─────────────────────────────
  // The server reads the packets as a drive log and runs the one analyse (/api/fwtest/runs/{id}/track) —
  // trips, data lost, asleep, stale positions, jumps, every packet with a device time — so the map is the
  // Dashboard's and Log analysis's. It is made once and kept: the rail's redraw moves it back in. A point
  // picked on it shows its packet under the map; a click on that opens it field by field.
  let mapPane = null;
  let tm = null;
  let mapPick = null;
  let trk = null;              // { id, n, data } — the track drawn: the test, its packets then, the analysis
  let trkBusy = false;
  let trkAt = 0;
  let trkTimer = null;
  let picked = null;           // { n, row } a packet, or { gap } a stretch — what the card under the map shows

  function renderMap(body) {
    if (!mapPane) {
      mapPane = el("div", "fw-map");
      const host = el("div", "fw-map-host");
      mapPick = el("div", "fw-map-pick");
      mapPane.append(host, mapPick);
      body.appendChild(mapPane);
      tm = window.RunwayMap.track(host, { drawKey: "runway.fwtest.draw", onPoint: mapPoint, onGap: mapGap });
      if (window.ResizeObserver) new ResizeObserver(() => tm.invalidate()).observe(host);   // the rail resized
    } else body.appendChild(mapPane);
    if (trk && trk.id !== cur.id) {                // another test: its own track, from its own fit
      trk = null;
      picked = null;
      tm.set([]);
      tm.clearMark();
    }
    if (!trk) tm.note("Loading");
    renderPick();
    mapShown();
    loadTrack(true);
  }

  // the map back on screen (a tab, an unfolded rail, a redraw): its size, its count, the chosen packet
  function mapShown() {
    requestAnimationFrame(() => tm && tm.invalidate());
    const d = trk && trk.id === cur.id ? trk.data : null;
    $("fw-rail-sub").textContent = d && d.rows ? `${num(d.rows)} packets` : "";
    if (d && pkOpen && (!picked || picked.n !== pkOpen)) markPacket(pkOpen, false);
  }

  async function loadTrack(force) {
    if (!cur || !tm || railTab !== "map") return;
    const id = cur.id, n = cur.summary.packets;
    if (!force && trk && trk.id === id && trk.n === n) return;
    if (trkBusy) { wantTrack(); return; }
    trkBusy = true;
    trkAt = Date.now();
    try {
      const d = await api(`/api/fwtest/runs/${encodeURIComponent(id)}/track`);
      if (!cur || cur.id !== id || railTab !== "map") return;
      const refit = !trk || trk.id !== id;
      trk = { id, n, data: d };
      tm.set(d.rows ? [{ id: "test", name: devOf(cur), colour: window.RunwayMap.tripColour(1), rows: d.rows,
                         series: d.series, driving: d.driving, settings: d.settings }] : [], { refit });
      tm.note(((d.series || {}).track || []).some((sg) => sg.length) ? ""
        : cur.summary.packets ? "No position in the packets yet" : "No packet yet");
      if (picked && picked.n) markPacket(picked.n, false);
      else if (refit && pkOpen) markPacket(pkOpen, true);
      mapShown();
    } catch (e) {
      // the page is newer than the Runway serving it: say so, not a blank map
      tm.note(e.message === "Not Found" ? "This Runway started before the map — run main.py again to restart it"
                                        : `The map could not be drawn: ${e.message}`);
    } finally {
      trkBusy = false;
    }
  }

  // packets came: the map follows — at most every 5 s while they stream in, the last of them always
  function wantTrack() {
    if (trkTimer || railTab !== "map") return;
    trkTimer = setTimeout(() => { trkTimer = null; loadTrack(); }, Math.max(0, 5000 - (Date.now() - trkAt)));
  }

  // a packet ringed on the map, by its number (the analysis gives each row's: seqs[k])
  function markPacket(n, pan) {
    const k = trk && trk.data.seqs ? trk.data.seqs.indexOf(n) : -1;
    if (k >= 0) tm.mark("test", k, pan); else tm.clearMark();
  }

  async function mapPoint(id, k) {
    const n = trk && trk.data.seqs ? trk.data.seqs[k] : null;
    if (n == null) return;
    picked = { n, row: null };
    renderPick();
    try {
      const d = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/packets/${n}`);
      if (!picked || picked.n !== n) return;
      picked.row = d;
      pkOpen = n;                                    // the packet chosen: the list marks it, Packet shows it
      pkData = d;
      for (const tr of $("fw-packets").querySelectorAll("tbody tr")) tr.classList.toggle("on", tr.dataset.n === String(n));
    } catch (e) {
      if (picked && picked.n === n) picked.error = e.message;
    }
    renderPick();
  }

  function mapGap(id, g) {
    picked = { gap: g };
    renderPick();
  }

  // the card under the map: the packet picked (as a live row — a click opens it), or the stretch
  function renderPick() {
    if (!mapPick) return;
    mapPick.textContent = "";
    mapPick.hidden = !picked;
    if (!picked) return;
    if (picked.gap) {
      pickGap(picked.gap);
      return;
    }
    const r = picked.row;
    if (!r) {
      mapPick.appendChild(el("div", "fw-none", picked.error ? `#${picked.n} could not be read: ${picked.error}` : "Loading"));
      return;
    }
    const row = el("div", "fw-live-row lv-" + r.level);
    const top = el("div", "fw-live-top");
    top.appendChild(el("span", "fw-live-time", r.received ? whenEpoch(r.received) : whenUtc(r.time)));
    top.appendChild(el("span", "fw-live-kind mono", [r.header, r.type, r.alert].filter(Boolean).join(" ")));
    if (r.status) top.appendChild(el("span", "ui-pill sm " + (isLate(r.status) ? "info" : "ok"), liveWord(r.status)));
    top.appendChild(el("span", "fw-live-frame mono", "#" + r.n));
    const [w, t] = LEVEL[r.level];
    top.appendChild(el("span", "ui-pill sm " + t, w));
    row.appendChild(top);
    const facts = [r.time && r.received ? "Device time " + whenUtc(r.time) : "", r.speed != null ? `${r.speed} km/h` : "",
                   r.ign === "1" ? "Ignition on" : r.ign === "0" ? "Ignition off" : ""].filter(Boolean).join(" · ");
    if (facts) row.appendChild(el("div", "fw-map-facts", facts));
    row.appendChild(el("div", "fw-live-raw mono", r.raw));
    if (r.flags.length) {
      const f = r.flags.find((x) => x.level === r.level) || r.flags[0];
      row.appendChild(el("div", "fw-live-flag", `${f.label}: ${f.text}` + (r.flags.length > 1 ? ` (+${r.flags.length - 1})` : "")));
    }
    row.dataset.tip = "Open the packet field by field";
    row.addEventListener("click", () => openPacket(r.n));
    mapPick.appendChild(row);
  }

  // a stretch without data, a stale position or a jump: what it was, and its two packets
  function pickGap(g) {
    const hop = g._type === "jump" || g._type === "stale";
    const local = (iso) => (iso ? fmtShort(new Date(iso)) : "—");        // the analysis's times are local
    const seqs = (trk && trk.data.seqs) || [];
    const box = el("div", "fw-map-gap");
    const top = el("div", "fw-live-top");
    top.appendChild(el("span", "fw-live-kind", hop ? (g._type === "stale" ? "Stale position" : "Jump") : "Missing data"));
    if (!hop) top.appendChild(el("span", "ui-pill sm " + (g.kind === "loss" ? "bad" : "muted"),
                                 g.kind === "loss" ? "Ignition on" : g.kind === "sleep" ? "Ignition off" : "Ignition not known"));
    box.appendChild(top);
    const kv = el("dl", "fw-map-kv");
    const add = (k, v) => { if (v == null || v === "") return; kv.append(el("dt", "", k), el("dd", "", String(v))); };
    add("From", local(hop ? g.from_time : g.start) + (seqs[g.k_from] != null ? ` · #${seqs[g.k_from]}` : ""));
    add("To", local(hop ? g.time : g.end) + (seqs[g.k] != null ? ` · #${seqs[g.k]}` : ""));
    add("Apart", g.metres == null ? null : g.metres >= 1000 ? (g.metres / 1000).toFixed(2) + " km" : g.metres + " m");
    if (hop) add("Would be", g.kmh == null ? null : Math.round(g.kmh) + " km/h");
    else add("Duration", g.duration);
    box.appendChild(kv);
    const acts = el("div", "fw-defect-acts");
    for (const [label, k] of [["From packet", g.k_from], ["To packet", g.k]]) {
      if (seqs[k] == null) continue;
      const b = el("button", "ghost mini");
      b.type = "button";
      b.appendChild(ic("file"));
      b.appendChild(document.createTextNode(label));
      b.addEventListener("click", () => openPacket(seqs[k]));
      acts.appendChild(b);
    }
    box.appendChild(acts);
    mapPick.appendChild(box);
  }

  // ── defects: what is wrong, why, where, how to fix — fix first what explains most ─────────────
  const SEV = { critical: ["Critical", "bad"], major: ["Major", "warn"], minor: ["Minor", "muted"] };
  let defData = null;
  let defKey = "";

  async function loadDefects() {
    if (!cur) return;
    const key = cur.id + ":" + cur.summary.packets;
    if (key === defKey) return;
    try {
      defData = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/defects`);
      defKey = key;
      const crit = defData.defects.filter((d) => d.severity === "critical").length;
      $("fw-rail-dn").textContent = crit ? String(crit) : "";
      if (railTab === "defects") renderRail();
    } catch (e) { /* the next turn tries again */ }
  }

  function renderDefects(body) {
    if (!defData || !defKey.startsWith(cur.id + ":")) {
      body.appendChild(el("div", "fw-none", "Diagnosing"));
      loadDefects();
      return;
    }
    const d = defData;
    $("fw-rail-sub").textContent = d.defects.length ? `${d.defects.length} found` : "";
    if (!d.defects.length) {
      body.appendChild(el("div", "fw-none", "No defect found"));
      return;
    }
    const ff = d.fix_first;
    if (ff.causes.length) {
      const head = el("div", "fw-fixfirst");
      head.appendChild(el("div", "fw-fixfirst-t", `Fix first: ${ff.causes.length} cause${ff.causes.length > 1 ? "s explain" : " explains"} ` +
        `${num(ff.explained)} of ${num(ff.failing)} failing packets (${Math.round((100 * ff.explained) / ff.failing)}%)`));
      const ol = el("ol");
      for (const c of ff.causes) ol.appendChild(el("li", "", `${c.label} — ${num(c.adds)} packets`));
      head.appendChild(ol);
      body.appendChild(head);
    }
    const byArea = {};
    for (const x of d.defects) (byArea[x.area_label] = byArea[x.area_label] || []).push(x);
    for (const [area, list] of Object.entries(byArea)) {
      body.appendChild(el("div", "fw-ins-group", `${area} · ${list.length}`));
      const ul = el("ul", "fw-defects");
      for (const x of list) ul.appendChild(defectCard(x));
      body.appendChild(ul);
    }
  }

  function defectCard(x) {
    const li = el("li", "fw-defect");
    const top = el("div", "fw-defect-top");
    const [w, t] = SEV[x.severity];
    top.appendChild(el("span", "ui-pill sm " + t, w));
    top.appendChild(el("span", "fw-defect-t", x.label));
    const pr = el("span", "ui-pill sm " + (x.proved ? "info" : "muted"), x.proved ? "Proved" : "Likely");
    pr.dataset.tip = x.proved ? "Runway checked this cause against the packets itself" : "The likely cause, from the pattern of the packets";
    top.appendChild(pr);
    li.appendChild(top);
    const split = [x.live ? `${num(x.live)} live` : "", x.history ? `${num(x.history)} history` : ""].filter(Boolean).join(", ");
    li.appendChild(el("div", "fw-defect-n", `${num(x.packets)} packets${split ? " (" + split + ")" : ""} · #${x.first}–#${x.last}`));
    li.appendChild(el("div", "fw-defect-ex mono", x.example));
    const dl = el("dl", "fw-defect-kv");
    for (const [k, v] of [["Cause", x.cause], ["Where", x.where], ["Fix", x.fix], ["Impact", x.impact],
                          ["Verify", (x.verify.length ? x.verify.join(", ") : "Run the test again") + " — no packet may fail"]]) {
      if (!v) continue;
      dl.appendChild(el("dt", "", k));
      dl.appendChild(el("dd", "", v));
    }
    li.appendChild(dl);
    const acts = el("div", "fw-defect-acts");
    const pk = el("button", "ghost mini");
    pk.type = "button";
    pk.appendChild(ic("list"));
    pk.appendChild(document.createTextNode("Packets"));
    pk.addEventListener("click", () => { setView("results"); setFilter({ check: x.key, label: x.label }, true); });
    const cp = el("button", "ghost mini");
    cp.type = "button";
    cp.appendChild(ic("copy"));
    cp.appendChild(document.createTextNode("Copy report"));
    cp.dataset.tip = "The defect as a report — title, firmware, device, how often, expected and got, cause, fix, verify, evidence — to paste into the tracker or a ticket";
    cp.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(x.report);
        cp.lastChild.textContent = "Copied";
        setTimeout(() => { cp.lastChild.textContent = "Copy report"; }, 1500);
      } catch (e) {
        await UI.notice({ title: "Defect report", text: x.report });
      }
    });
    acts.append(pk, cp);
    li.appendChild(acts);
    return li;
  }

  // ── suites: bench steps one after another ──────────────────────────────────────────────────────
  function renderSuite() {
    const d = planData;
    const sel = $("fw-suite-sel");
    if (!sel.dataset.filled && d.suites) {
      for (const s of d.suites) {
        const o = el("option", "", `${s.label} · ${s.steps} step${s.steps === 1 ? "" : "s"}`);
        o.value = s.key;
        sel.appendChild(o);
      }
      sel.dataset.filled = "1";
    }
    $("fw-suite-run").hidden = !sel.value || !!(d.suite && !d.suite.done);
    const box = $("fw-suite");
    const su = d.suite;
    if (!su) { box.hidden = true; return; }
    box.hidden = false;
    box.textContent = "";
    const head = el("div", "fw-area-head");
    head.appendChild(el("span", "fw-area-t", su.done ? `${su.label} suite — done` : `${su.label} suite — step ${su.i + 1} of ${su.steps.length}`));
    const stop = el("button", "ghost mini");
    stop.type = "button";
    stop.appendChild(ic(su.done ? "x" : "stop"));
    stop.appendChild(document.createTextNode(su.done ? "Close" : "Stop suite"));
    stop.style.marginLeft = "auto";
    stop.addEventListener("click", async () => {
      await api("/api/fwtest/plan/suite/stop", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmware: d.firmware }) });
      loadPlan();
    });
    head.appendChild(stop);
    box.appendChild(head);
    if (!su.done) box.appendChild(el("div", "fw-suite-now", su.steps[su.i].do));
    const ol = el("ol", "fw-suite-steps");
    su.steps.forEach((s, i) => {
      const li = el("li", i === su.i && !su.done ? "now" : "");
      li.appendChild(pill(s.status, true));
      li.appendChild(el("span", "", `${s.case} — ${s.do}`));
      ol.appendChild(li);
    });
    box.appendChild(ol);
  }

  async function runSuite() {
    const key = $("fw-suite-sel").value;
    if (!key || !cur) return;
    try {
      await api("/api/fwtest/plan/suite", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmware: planData.firmware, suite: key, run: cur.id }) });
      loadPlan();
    } catch (e) {
      await UI.notice({ title: "Suite", text: e.message });
    }
  }

  // ── the timeline: the test on one line ──────────────────────────────────────────────────────────
  const svgNS = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs, parent) => {
    const e = document.createElementNS(svgNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  // Zoomed in, the track is as wide as the test at that scale and scrolls sideways; the drawing is one
  // screen wide, stays in view (sticky) and is redrawn for the time on screen — so a week-long soak
  // zoomed to minutes costs no more than the whole test does. The whole test comes thinned; the window
  // on screen is fetched again with every packet in it, so a gap seen zoomed in is a real gap.
  const TL_H = 104, TL_L = 8, TL_R = 8, TL_MIN_SPAN = 60;
  const TL_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
  let tlKey = "";
  let tlData = null;          // the whole test, thinned
  let tlDetail = null;        // { a, b, points }: every packet that reached Runway in a ≤ t ≤ b
  let tlSpan = null;          // the seconds across the width; null = the whole test (fit)
  let tlStart = 0;            // the time at the left edge, zoomed in
  let tlFollow = true;        // the right edge stays on the newest packet as packets come
  let tlDetailTimer = null;
  let tlFrame = 0;
  let tlDrag = null;
  let tlDragged = false;

  async function loadTimeline() {
    if (!cur) return;
    const key = cur.id + ":" + cur.summary.packets;
    if (key === tlKey) return;
    if (!tlKey.startsWith(cur.id + ":")) { tlSpan = null; tlFollow = true; tlDetail = null; }     // another test: the whole of it
    tlKey = key;
    const d = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/timeline`);
    if (!cur || !key.startsWith(cur.id + ":")) return;
    tlData = d;
    drawTimeline();
    tlWantDetail();
  }

  function tlGeom() {
    const host = $("fw-tl");
    const pts = tlData.points;
    const t0 = pts[0][1], t1 = Math.max(pts[pts.length - 1][1], t0 + 1);
    const cw = Math.max(300, host.clientWidth);
    const inner = cw - TL_L - TL_R, T = t1 - t0;
    const span = tlSpan && tlSpan < T ? tlSpan : T;
    const k = inner / span;                                   // px per second
    return { host, t0, t1, T, cw, inner, span, k, total: Math.round(T * k) + TL_L + TL_R };
  }

  function drawTimeline() {
    const host = $("fw-tl");
    const d = tlData;
    $("fw-tl-n").textContent = d && d.axis === "device" ? "device time"
      : d && d.connections > 1 ? `${d.connections} connections` : "";
    if (!d || d.points.length < 2) {
      host.textContent = "";
      host.appendChild(el("div", "fw-none", "Not enough packets yet"));
      $("fw-tl-span").textContent = "";
      for (const b of ["fw-tl-in", "fw-tl-out", "fw-tl-fit"]) $(b).disabled = true;
      return;
    }
    if (!host.clientWidth) return;                            // hidden (the test plan is on screen)
    let track = host.querySelector(".fw-tl-track");
    if (!track) {
      host.textContent = "";
      track = el("div", "fw-tl-track");
      host.appendChild(track);
      sv("svg", { height: TL_H, class: "fw-tl-svg" }, track);
    }
    const g = tlGeom();
    track.style.width = g.total + "px";
    const maxLeft = Math.max(0, g.total - g.cw);
    const left = !tlSpan ? 0 : tlFollow ? maxLeft : Math.round((tlStart - g.t0) * g.k);
    host.scrollLeft = Math.min(maxLeft, Math.max(0, left));
    host.classList.toggle("zoomed", !!tlSpan);
    paintTimeline();
  }

  function tlPoints() {
    const all = tlData.points;
    if (!tlDetail) return all;
    const { a, b, points } = tlDetail;
    return all.filter((p) => p[1] < a).concat(points, all.filter((p) => p[1] > b));
  }

  function spanWords(s) {
    if (s < 120) return `${Math.round(s)} s`;
    if (s < 7200) return `${Math.round(s / 60)} min`;
    if (s < 172800) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return m ? `${h} h ${m} min` : `${h} h`; }
    return `${(s / 86400).toFixed(1).replace(/\.0$/, "")} days`;
  }

  function paintTimeline() {
    const host = $("fw-tl");
    const svg = host.querySelector("svg.fw-tl-svg");
    if (!svg || !tlData || tlData.points.length < 2) return;
    const g = tlGeom();
    const left = host.scrollLeft, W = g.cw;
    svg.setAttribute("width", W);
    svg.setAttribute("viewBox", `0 0 ${W} ${TL_H}`);
    svg.textContent = "";
    const x = (t) => TL_L + (t - g.t0) * g.k - left;
    const ta = g.t0 + (left - TL_L) / g.k, tb = ta + W / g.k;           // the time on screen
    const inView = (t) => t >= ta - 6 / g.k && t <= tb + 6 / g.k;
    // the clock: round steps of the local time, a label every ~110 px, the date where the day turns
    const step = TL_STEPS.find((s) => s * g.k >= 110) || TL_STEPS[TL_STEPS.length - 1];
    const tz = new Date().getTimezoneOffset() * 60;
    let day = "";
    for (let t = Math.ceil((ta - tz) / step) * step + tz; t <= tb; t += step) {
      const px = x(t);
      if (px < 0) continue;
      sv("line", { x1: px, x2: px, y1: 2, y2: TL_H - 18, class: "tick" }, svg);
      const dt = new Date(t * 1000);
      const dd = `${pad(dt.getDate())}-${pad(dt.getMonth() + 1)}`;
      const clock = `${pad(dt.getHours())}:${pad(dt.getMinutes())}` + (step < 60 ? `:${pad(dt.getSeconds())}` : "");
      const anchor = px < 40 ? "start" : px > W - 40 ? "end" : "middle";
      sv("text", { x: px, y: TL_H - 4, class: "tl-t", "text-anchor": anchor }, svg).textContent = dd !== day ? `${dd} ${clock}` : clock;
      day = dd;
    }
    const pts = tlPoints();
    // ignition on, as bands
    const band = (a, b) => {
      if (b < ta || a > tb) return;
      const xa = Math.max(-2, x(a)), xb = Math.min(W + 2, x(b));
      sv("rect", { x: xa, y: 6, width: Math.max(1, xb - xa), height: 10, class: "ign" }, svg);
    };
    let on = null;
    for (const p of pts) {
      if (p[4] === "1" && on === null) on = p[1];
      if (p[4] === "0" && on !== null) { band(on, p[1]); on = null; }
    }
    if (on !== null) band(on, g.t1);
    // packets: live above, history below; failing ones as red marks on top, each opens its packet
    let conn = pts[0][5];
    const fails = [];
    for (const p of pts) {
      if (p[5] !== conn) {
        if (inView(p[1])) sv("line", { x1: x(p[1]), x2: x(p[1]), y1: 2, y2: TL_H - 18, class: "conn" }, svg);
        conn = p[5];
      }
      if (!inView(p[1])) continue;
      if (p[3] === "fail") { fails.push(p); continue; }
      sv("circle", { cx: x(p[1]), cy: isLate(p[2]) ? 62 : 38, r: 2, class: isLate(p[2]) ? "hist" : "live" }, svg);
    }
    for (const p of fails) {
      const c = sv("circle", { cx: x(p[1]), cy: isLate(p[2]) ? 62 : 38, r: 3, class: (isLate(p[2]) ? "hist" : "live") + " fail" }, svg);
      c.addEventListener("click", () => openPacket(p[0]));
    }
    $("fw-tl-span").textContent = `${spanWords(g.span)} on screen`;
    $("fw-tl-in").disabled = g.span <= TL_MIN_SPAN + 0.5;
    $("fw-tl-out").disabled = !tlSpan;
    $("fw-tl-fit").disabled = !tlSpan;
  }

  // zoom by ``f`` (above 1: in), keeping the time at ``px`` (from the left of the timeline) where it is
  function tlZoom(f, px) {
    if (!tlData || tlData.points.length < 2) return;
    const g = tlGeom();
    if (px == null) px = g.cw / 2;
    const tAt = g.t0 + ($("fw-tl").scrollLeft + px - TL_L) / g.k;
    const span = Math.max(TL_MIN_SPAN, g.span / f);
    if (span >= g.T) return tlFit();
    const k = g.inner / span;
    tlSpan = span;
    tlStart = Math.min(g.t1 - span, Math.max(g.t0, tAt - (px - TL_L) / k));
    tlFollow = tlStart >= g.t1 - span - 2 / k;
    drawTimeline();
    tlWantDetail();
  }
  function tlFit() {
    tlSpan = null;
    tlFollow = true;
    tlDetail = null;
    clearTimeout(tlDetailTimer);
    drawTimeline();
  }

  // the window on screen with every packet in it, once the view settles
  function tlWantDetail() {
    clearTimeout(tlDetailTimer);
    if (!tlData || !tlData.thinned || !tlSpan || tlData.points.length < 2) return;
    tlDetailTimer = setTimeout(async () => {
      if (!cur || !tlSpan) return;
      const g = tlGeom();
      const ta = g.t0 + $("fw-tl").scrollLeft / g.k, tb = ta + g.cw / g.k;
      if (tlDetail && ta >= tlDetail.a && tb <= tlDetail.b) return;
      const a = ta - (tb - ta) / 2, b = tb + (tb - ta) / 2;            // half a screen either side
      const id = cur.id;
      const d = await api(`/api/fwtest/runs/${encodeURIComponent(id)}/timeline?start=${a}&end=${b}&points=3000`);
      if (!cur || cur.id !== id || !tlSpan) return;
      const cap = Math.min(b, tlData.points[tlData.points.length - 1][1]);   // newer packets: the whole test's points
      tlDetail = { a, b: cap, points: d.points.filter((p) => p[1] <= cap) };
      paintTimeline();
    }, 250);
  }

  (function wireTimeline() {
    const host = $("fw-tl");
    host.addEventListener("scroll", () => {
      if (!tlData || tlData.points.length < 2) return;
      if (tlSpan) {
        const g = tlGeom();
        tlStart = g.t0 + host.scrollLeft / g.k;
        tlFollow = host.scrollLeft >= g.total - g.cw - 2;
      }
      cancelAnimationFrame(tlFrame);
      tlFrame = requestAnimationFrame(paintTimeline);
      tlWantDetail();
    });
    host.addEventListener("wheel", (e) => {                          // Ctrl + wheel (or a pinch): zoom here
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const dy = Math.max(-300, Math.min(300, e.deltaY * (e.deltaMode === 1 ? 33 : 1)));
      tlZoom(Math.exp(-dy * 0.003), e.clientX - host.getBoundingClientRect().left);
    }, { passive: false });
    host.addEventListener("pointerdown", (e) => {                    // drag to move along it
      tlDragged = false;
      if (e.button !== 0 || !tlSpan) return;
      tlDrag = { x: e.clientX, left: host.scrollLeft, moved: false };
    });
    window.addEventListener("pointermove", (e) => {
      if (!tlDrag) return;
      const dx = e.clientX - tlDrag.x;
      if (Math.abs(dx) > 3) tlDrag.moved = true;
      if (tlDrag.moved) { host.scrollLeft = tlDrag.left - dx; host.classList.add("dragging"); }
    });
    window.addEventListener("pointerup", () => {
      if (tlDrag && tlDrag.moved) {
        tlDragged = true;                                            // the click that ends a drag opens nothing
        setTimeout(() => { tlDragged = false; }, 0);
      }
      tlDrag = null;
      host.classList.remove("dragging");
    });
    host.addEventListener("click", (e) => { if (tlDragged) { e.stopPropagation(); tlDragged = false; } }, true);
    host.addEventListener("keydown", (e) => {
      if (e.key === "+" || e.key === "=") tlZoom(2);
      else if (e.key === "-" || e.key === "_") tlZoom(0.5);
      else if (e.key === "0") tlFit();
      else return;
      e.preventDefault();
    });
    $("fw-tl-in").addEventListener("click", () => tlZoom(2));
    $("fw-tl-out").addEventListener("click", () => tlZoom(0.5));
    $("fw-tl-fit").addEventListener("click", tlFit);
    let w = 0;
    new ResizeObserver(() => {
      const cw = host.clientWidth;
      if (cw === w) return;
      w = cw;
      if (cw && tlData) drawTimeline();                              // a new width, or shown again
    }).observe(host);
  })();

  // ── the live coach: what just changed, said as it happens ───────────────────────────────────────
  let coachKeys = null;
  let coachTimer = null;
  function coach(v) {
    const box = $("fw-coach");
    const live = v.state === "live";
    const keys = new Set((v.summary.checks || []).filter((c) => c.level === "fail").map((c) => c.key));
    let say = "";
    if (coachKeys && live) {
      const fresh = (v.summary.checks || []).find((c) => c.level === "fail" && !coachKeys.has(c.key));
      if (fresh) say = `New failure: ${fresh.label} — ${fresh.example}`;
    }
    coachKeys = keys;
    const last = v.summary.last;
    if (!say && live && last && Date.now() / 1000 - last > 180) {
      say = `No packet for ${Math.round((Date.now() / 1000 - last) / 60)} min — is the device still connected?`;
    }
    if (!say) return;
    box.textContent = say;
    box.hidden = false;
    clearTimeout(coachTimer);
    coachTimer = setTimeout(() => { box.hidden = true; }, 20000);
  }

  // ── the release gate, and another firmware version beside this one ─────────────────────────────
  let gateData = null;
  async function loadGate(fw) {
    try {
      const d = await api("/api/fwtest/plan/defects?firmware=" + encodeURIComponent(fw));
      gateData = d;
      if (railTab === "score") renderRail();
      const g = d.gate;
      const p = $("fw-gate");
      p.hidden = false;
      p.className = "ui-pill " + (g.ready ? "ok" : "bad");
      p.textContent = g.ready ? "Release: ready" : "Release: not ready";
      p.dataset.tip = g.ready ? "No failure in SOS, InTouch, government or emergency cases; no critical defect; enough of the plan run"
        : "Not ready —\n" + g.reasons.join("\n");
    } catch (e) { $("fw-gate").hidden = true; }
  }

  async function fillCompare(fw) {
    const sel = $("fw-plan-cmp");
    const keep = sel.value;
    const list = await api("/api/fwtest/plan/firmwares");
    sel.textContent = "";
    const none = el("option", "", "Compare with…");
    none.value = "";
    sel.appendChild(none);
    for (const x of list) {
      if (x.firmware === fw) continue;
      const o = el("option", "", `${x.firmware} (${x.tests} test${x.tests === 1 ? "" : "s"})`);
      o.value = x.firmware;
      sel.appendChild(o);
    }
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "";
    sel.hidden = list.length < 2;
  }

  async function renderCompare() {
    const box = $("fw-cmp");
    const other = $("fw-plan-cmp").value;
    if (!other || !planData) { box.hidden = true; return; }
    box.hidden = false;
    box.textContent = "";
    box.appendChild(el("div", "fw-none", "Comparing"));
    const c = await api(`/api/fwtest/plan/compare?a=${encodeURIComponent(other)}&b=${encodeURIComponent(planData.firmware)}`);
    box.textContent = "";
    const head = el("div", "fw-area-head");
    head.appendChild(el("span", "fw-area-t", `${c.b} against ${c.a}`));
    box.appendChild(head);
    const grid = el("div", "fw-cmp-grid");
    const col = (title, tone, rows, fmt) => {
      const d = el("div", "fw-cmp-col");
      d.appendChild(el("div", "fw-cmp-h t-" + tone, `${title} · ${rows.length}`));
      for (const r of rows.slice(0, 40)) d.appendChild(el("div", "fw-cmp-r", fmt(r)));
      if (!rows.length) d.appendChild(el("div", "fw-cmp-r t-none", "None"));
      grid.appendChild(d);
    };
    col("Fixed", "ok", c.fixed, (r) => `${r.id} ${r.what}`);
    col("Regressed", "bad", c.regressed, (r) => `${r.id} ${r.what}`);
    col("Still failing", "warn", c.still_failing, (r) => `${r.id} ${r.what}`);
    col("Newly failing", "bad", c.newly_failing, (r) => `${r.id} ${r.what}`);
    col("Defects fixed", "ok", c.defects.fixed, (r) => r.label);
    col("Defects new", "bad", c.defects.new, (r) => `${r.label} (${r.severity})`);
    box.appendChild(grid);
  }

  function renderInsights(body) {
    const list = cur.insights || [];
    const s = cur.summary;
    $("fw-rail-sub").textContent = s.verdict === "in_progress" ? "In progress" : (VERDICT[s.verdict] || [""])[0];
    const head = el("div", "fw-ins-head",
      `${num(s.packets)} packets · ${num(s.failed)} failed · ${num(s.warned)} with warnings`);
    body.appendChild(head);
    if (!list.length) {
      body.appendChild(el("div", "fw-none", "No packets yet"));
      return;
    }
    let group = null;
    let ul = null;
    for (const it of list) {
      const [icon, tone, words] = INSIGHT[it.level] || INSIGHT.info;
      if (it.level !== group) {
        group = it.level;
        body.appendChild(el("div", "fw-ins-group", words));
        ul = el("ul", "fw-ins");
        body.appendChild(ul);
      }
      const li = el("li", "tone-" + tone);
      li.appendChild(ic(icon));
      li.appendChild(el("span", "fw-ins-t", it.title));
      li.appendChild(el("span", "fw-ins-x", it.text));
      const f = it.filter || {};
      if (f.check || f.scenario) {
        const b = el("button", "ghost mini");
        b.type = "button";
        b.appendChild(ic("list"));
        b.appendChild(document.createTextNode("Packets"));
        b.addEventListener("click", () => setFilter({ ...f, label: it.title }, true));
        li.appendChild(b);
      }
      ul.appendChild(li);
    }
  }

  function renderPacket(body) {
    const d = pkData;
    if (!d) {
      $("fw-rail-sub").textContent = "";
      body.appendChild(el("div", "fw-none", "Click a packet in the list"));
      return;
    }
    $("fw-rail-sub").textContent = `#${d.n} · ${d.packet}${d.type ? " " + d.type : ""}`;
    const nav = el("div", "fw-pk-nav");
    const prev = el("button", "ui-icon-btn");
    prev.type = "button";
    prev.appendChild(ic("arrow-left"));
    prev.setAttribute("aria-label", "Previous packet");
    prev.disabled = d.n <= 1;
    prev.addEventListener("click", () => openPacket(d.n - 1));
    const next = el("button", "ui-icon-btn");
    next.type = "button";
    next.appendChild(ic("arrow-right"));
    next.setAttribute("aria-label", "Next packet");
    next.disabled = d.n >= cur.summary.packets;
    next.addEventListener("click", () => openPacket(d.n + 1));
    const when = [whenEpoch(d.received), liveWord(d.status)].filter(Boolean).join(" · ");
    nav.append(prev, el("span", "fw-pk-when", when), next);
    body.appendChild(nav);

    const fl = el("div", "fw-pk-flags");
    for (const f of d.flags.length ? d.flags : [{ level: "pass", label: "", text: "Everything as the sheet says" }]) {
      const row = el("div", "fw-pk-flag");
      const [w, t] = LEVEL[f.level];
      row.appendChild(el("span", "ui-pill sm " + t, w));
      row.appendChild(el("span", "", (f.label ? f.label + ": " : "") + f.text));
      fl.appendChild(row);
    }
    body.appendChild(fl);
    body.appendChild(el("div", "fw-pk-raw mono", d.raw));

    const wrap = el("div", "fw-pk-fields");
    const table = el("table", "fw-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const [h, cls] of [["#", "num"], ["Field", ""], ["Value", ""], ["Result", "c-res"]]) hr.appendChild(el("th", cls, h));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tb = el("tbody");
    for (const f of d.fields) {
      const tr = el("tr", f.note ? "has-note" : "");
      tr.appendChild(el("td", "num", String(f.index)));
      const lab = el("td", "", f.label);
      if (f.source) lab.dataset.tip = "Rule from — " + f.source;
      tr.appendChild(lab);
      tr.appendChild(el("td", "mono", f.value === "" ? "(empty)" : f.value));
      const [w, t] = LEVEL[f.level];
      const res = el("td", "c-res");
      res.appendChild(el("span", "ui-pill sm " + t, w));
      tr.appendChild(res);
      tb.appendChild(tr);
      if (f.note) {
        const nr = el("tr");
        nr.appendChild(el("td"));
        const nd = el("td", "note", f.note);
        nd.colSpan = 3;
        nr.appendChild(nd);
        tb.appendChild(nr);
      }
    }
    table.appendChild(tb);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  for (const b of document.querySelectorAll(".fw-rail-tab")) {
    b.addEventListener("click", () => {
      railTab = railTab === b.dataset.rail ? null : b.dataset.rail;
      store.set(RAIL_KEY, railTab || "");
      renderRail();
    });
  }
  $("fw-rail-close").addEventListener("click", () => { railTab = null; store.set(RAIL_KEY, ""); renderRail(); });
  UI.resizable($("fw-rail-panel"), { key: "fwtest-rail", edge: "left", host: $("fw-main"), cssVar: "--fw-rail-w",
                                     min: 320, max: 720, keep: 640, title: "the side panel" });

  // ── the test plan: this firmware version's tracker ────────────────────────────────────────────
  const PSTATUS = { pass: ["Pass", "ok"], fail: ["Fail", "bad"], partial: ["Partial", "warn"], running: ["Running", "info"],
                    not_run: ["Not run", "muted"], blocked: ["Blocked", "warn"], na: ["Not applicable", "muted"] };
  const HOW = { automatic: "From packets", assisted: "Bench step", command: "By command", manual: "By hand" };
  let view = "results";
  let planData = null;
  let planTimer = null;
  let stepCase = null;

  const firmwareOf = (v) => {
    const fw = (v && v.summary && v.summary.firmware) || {};
    return Object.keys(fw).sort((a, b) => fw[b] - fw[a])[0] || "";
  };

  function setView(v) {
    view = v;
    for (const b of document.querySelectorAll(".fw-views button")) b.classList.toggle("on", b.dataset.view === v);
    $("fw-run").classList.toggle("is-plan", v === "plan");
    $("fw-planview").hidden = v !== "plan";
    clearTimeout(planTimer);
    if (v === "plan") loadPlan();
  }

  async function fetchPlan(fw) {
    planData = await api("/api/fwtest/plan?firmware=" + encodeURIComponent(fw));
    planAt = Date.now();
    if (railTab === "score") renderRail();
    renderCats();
    return planData;
  }
  let planAt = 0;

  // ── the scorecard: the test plan at a glance, by how it is tested and by category ───────────
  const BAR = [["pass", "ok"], ["fail", "bad"], ["partial", "warn"], ["running", "info"], ["not_run", "none"]];

  function sbar(counts, total) {
    const bar = el("div", "fw-sbar");
    for (const [k, tone] of BAR) {
      const n = counts[k] || 0;
      if (!n) continue;
      const seg = el("span", "tone-" + tone);
      seg.style.width = (100 * n) / total + "%";
      seg.title = `${PSTATUS[k][0]} ${n}`;
      bar.appendChild(seg);
    }
    return bar;
  }

  // the progress of a set of cases, always the same five figures: total, pass, fail, in progress
  // (a bench step running, or a case partly seen), pending (not run yet)
  function figures(counts) {
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const f = el("span", "fw-sfig fw-sfig5");
    const cell = (n, words, tone, tip) => {
      const c = el("span", "fw-sf " + (n ? "t-" + tone : "t-zero"));
      c.appendChild(el("b", "", num(n)));
      c.appendChild(el("span", "", words));
      if (tip) c.title = tip;
      f.appendChild(c);
    };
    cell(total, "total", "text");
    cell(counts.pass || 0, "pass", "ok");
    cell(counts.fail || 0, "fail", "bad");
    cell((counts.running || 0) + (counts.partial || 0), "in progress", "info", "A bench step running, or a case partly seen");
    cell(counts.not_run || 0, "pending", "none", "Not run yet");
    return f;
  }

  function tallyOf(list) {
    const c = {};
    for (const x of list) c[x.result.status] = (c[x.result.status] || 0) + 1;
    return c;
  }

  function renderScore(body) {
    const fw = firmwareOf(cur);
    if (!fw) {
      $("fw-rail-sub").textContent = "";
      body.appendChild(el("div", "fw-none", "No firmware version reported yet"));
      return;
    }
    if (!planData || planData.firmware !== fw || Date.now() - planAt > 10000) {
      if (!planData || planData.firmware !== fw) body.appendChild(el("div", "fw-none", "Loading the test plan"));
      fetchPlan(fw).catch(() => {});
      if (!planData || planData.firmware !== fw) return;
    }
    const d = planData;
    const all = d.areas.flatMap((a) => a.cases);
    const t = tallyOf(all);
    const run = all.length - (t.not_run || 0);
    const judged = (t.pass || 0) + (t.fail || 0) + (t.partial || 0);
    $("fw-rail-sub").textContent = `${d.firmware} · ${d.runs.length} test${d.runs.length === 1 ? "" : "s"}`;

    const head = el("div", "fw-score-head");
    const big = el("div", "fw-score-big");
    big.appendChild(el("span", "fw-score-n", `${run} of ${all.length}`));
    big.appendChild(el("span", "fw-score-l", `cases run (${Math.round((100 * run) / all.length)}%)`));
    head.appendChild(big);
    if (judged) {
      const rate = el("div", "fw-score-big");
      rate.appendChild(el("span", "fw-score-n " + ((t.fail || 0) ? "t-bad" : "t-ok"), Math.round((100 * (t.pass || 0)) / judged) + "%"));
      rate.appendChild(el("span", "fw-score-l", "pass rate of those"));
      head.appendChild(rate);
    }
    body.appendChild(head);
    const overall = el("div", "fw-score-row top");
    overall.appendChild(sbar(t, all.length));
    overall.appendChild(figures(t));
    body.appendChild(overall);

    if (gateData && gateData.firmware === d.firmware) {
      const g = gateData.gate;
      const box = el("div", "fw-gatebox " + (g.ready ? "ok" : "bad"));
      box.appendChild(el("div", "fw-gatebox-t", g.ready ? "Release: ready" : "Release: not ready"));
      for (const r of g.reasons) box.appendChild(el("div", "fw-gatebox-r", r));
      body.appendChild(box);
    }
    body.appendChild(el("div", "fw-ins-group", "By how it is tested"));
    const kinds = el("div", "fw-score-list");
    for (const k of ["automatic", "assisted", "command", "manual"]) {
      const list = all.filter((c) => c.kind === k);
      if (!list.length) continue;
      const row = el("button", "fw-score-row");
      row.type = "button";
      row.appendChild(el("span", "fw-score-name", `${HOW[k]} · ${list.length}`));
      row.appendChild(sbar(tallyOf(list), list.length));
      row.appendChild(figures(tallyOf(list)));
      row.addEventListener("click", () => {
        $("fw-plan-kind").value = k;
        $("fw-plan-status").value = "";
        setView("plan");
      });
      kinds.appendChild(row);
    }
    body.appendChild(kinds);
    // the reports a test writes by itself — every 24 h live, and when it stops
    api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/reports`).then((list) => {
      if (!list.length || railTab !== "score") return;
      body.appendChild(el("div", "fw-ins-group", "Saved reports"));
      const ul = el("ul", "fw-reports");
      for (const n of list) {
        const li = el("li");
        const a = el("a", "", n.replace(/\.xlsx$/, ""));
        a.href = `/api/fwtest/runs/${encodeURIComponent(cur.id)}/reports/${encodeURIComponent(n)}`;
        li.appendChild(a);
        ul.appendChild(li);
      }
      body.appendChild(ul);
    }).catch(() => {});

  }

  // the left pane: the plan's categories (the tracker's), with how far each has got
  function loadCats() {
    const fw = firmwareOf(cur);
    if (!fw) {
      $("fw-cats").textContent = "";
      $("fw-cats").appendChild(el("div", "fw-none", "No firmware version reported yet"));
      return;
    }
    if (planData && planData.firmware === fw) renderCats();
    else fetchPlan(fw).catch((e) => {
      // say why, never a blank pane
      $("fw-cats").textContent = "";
      $("fw-cats").appendChild(el("div", "fw-none", "The test plan could not be read: " + e.message));
    });
  }
  function renderCats() {
    if (!planData || planData.firmware !== firmwareOf(cur)) return;
    const host = $("fw-cats");
    host.textContent = "";
    host.appendChild(el("div", "fw-cats-head", `${planData.plan} · ${planData.firmware}`));
    for (const a of planData.areas) {
      const tl = tallyOf(a.cases);
      const row = el("button", "fw-score-row" + (tl.fail ? " has-fail" : ""));
      row.type = "button";
      row.dataset.tip = a.goal;
      const name = el("span", "fw-score-name");
      name.appendChild(el("b", "", a.id));
      name.appendChild(document.createTextNode(" " + a.title));
      row.appendChild(name);
      row.appendChild(sbar(tl, a.cases.length));
      row.appendChild(figures(tl));
      row.addEventListener("click", () => showArea(a.id));
      host.appendChild(row);
    }
  }

  function showArea(id) {
    $("fw-plan-kind").value = "";
    $("fw-plan-status").value = "";
    $("fw-plan-q").value = "";
    setView("plan");
    const go = () => {
      const card = document.querySelector(`#fw-plan-areas [data-area="${id}"]`);
      if (!card) return setTimeout(go, 150);
      card.scrollIntoView({ block: "start", behavior: "smooth" });
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1600);
    };
    go();
  }

  // ── scope: choose the cases that are part of testing ────────────────────────────────────────────
  let scopeEdit = null;          // a Set of the case ids in scope while choosing; null otherwise

  async function editScope() {
    const fw = firmwareOf(cur);
    if (!fw) return;
    clearTimeout(planTimer);
    planData = await api(`/api/fwtest/plan?firmware=${encodeURIComponent(fw)}&all=1`);
    scopeEdit = new Set(planData.areas.flatMap((a) => a.cases.filter((c) => c.in_scope).map((c) => c.id)));
    $("fw-scopebar").hidden = false;
    renderPlan();
  }
  function scopeCount() {
    const all = planData.areas.flatMap((a) => a.cases).length;
    $("fw-scope-t").textContent = `Choose the cases that are part of testing — ${scopeEdit.size} of ${all} in scope`;
  }
  async function saveScope() {
    const out = planData.areas.flatMap((a) => a.cases.map((c) => c.id)).filter((id) => !scopeEdit.has(id));
    try {
      await api("/api/fwtest/plan/scope", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmware: planData.firmware, out }) });
    } catch (e) {
      await UI.notice({ title: "Scope not saved", text: e.message });
      return;
    }
    scopeEdit = null;
    $("fw-scopebar").hidden = true;
    planAt = 0;
    loadPlan();
  }
  function cancelScope() {
    scopeEdit = null;
    $("fw-scopebar").hidden = true;
    loadPlan();
  }

  async function loadPlan() {
    if (scopeEdit) return;                       // choosing the scope: the list stays as it is
    clearTimeout(planTimer);
    const fw = firmwareOf(cur);
    if (!fw) {
      $("fw-plan-title").textContent = "Test plan";
      $("fw-plan-areas").textContent = "";
      $("fw-plan-areas").appendChild(el("div", "fw-none", "No firmware version reported yet"));
      return;
    }
    try {
      await fetchPlan(fw);
    } catch (e) {
      $("fw-plan-areas").textContent = "";
      $("fw-plan-areas").appendChild(el("div", "fw-none", e.message));
      return;
    }
    $("fw-plan-xlsx").href = "/api/fwtest/plan.xlsx?firmware=" + encodeURIComponent(fw);
    renderPlan();
    renderSuite();
    loadGate(fw);
    fillCompare(fw).then(() => { if ($("fw-plan-cmp").value) renderCompare(); }).catch(() => {});
    const running = planData.areas.some((a) => a.cases.some((c) => c.result.status === "running"));
    if (view === "plan") planTimer = setTimeout(loadPlan, running || (cur && cur.state === "live") ? 3000 : 15000);
  }

  function pill(status, sm) {
    const [w, t] = PSTATUS[status] || [status, "muted"];
    return el("span", "ui-pill " + (sm ? "sm " : "") + t, w);
  }

  function renderPlan() {
    const d = planData;
    $("fw-plan-title").textContent = `Test plan · ${d.plan} · firmware ${d.firmware} · ${d.runs.length} test${d.runs.length === 1 ? "" : "s"}` +
      (d.out_of_scope && !scopeEdit ? ` · ${d.out_of_scope} out of scope` : "");
    const tally = $("fw-plan-tally");
    tally.textContent = "";
    for (const k of ["fail", "partial", "running", "pass", "not_run"]) {
      if (!d.tally[k]) continue;
      const p = pill(k, true);
      p.textContent = `${PSTATUS[k][0]} ${num(d.tally[k])}`;
      tally.appendChild(p);
    }
    const kind = $("fw-plan-kind").value, st = $("fw-plan-status").value, q = $("fw-plan-q").value.trim().toLowerCase();
    const host = $("fw-plan-areas");
    host.textContent = "";
    if (scopeEdit) scopeCount();
    for (const a of d.areas) {
      const rows = a.cases.filter((c) => (!kind || c.kind === kind) && (!st || c.result.status === st) &&
        (!q || (c.id + " " + c.what + " " + c.group + " " + (c.entry.remarks || "")).toLowerCase().includes(q)));
      if (!rows.length) continue;
      const card = el("div", "fw-card");
      card.dataset.area = a.id;
      const head = el("div", "fw-area-head");
      head.appendChild(el("span", "fw-area-id", a.id));
      const t = el("span", "fw-area-t", a.title);
      t.dataset.tip = a.goal;
      head.appendChild(t);
      const n = el("span", "fw-area-n");
      const c = {};
      for (const x of a.cases) c[x.result.status] = (c[x.result.status] || 0) + 1;
      for (const k of ["fail", "partial", "pass"]) if (c[k]) { const p = pill(k, true); p.textContent = `${PSTATUS[k][0]} ${c[k]}`; n.appendChild(p); }
      head.appendChild(n);
      if (scopeEdit) {
        // a whole category in or out at once
        const allIn = a.cases.every((x) => scopeEdit.has(x.id));
        const tog = el("label", "fw-scope-all");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = allIn;
        cb.indeterminate = !allIn && a.cases.some((x) => scopeEdit.has(x.id));
        cb.addEventListener("change", () => {
          for (const x of a.cases) { if (cb.checked) scopeEdit.add(x.id); else scopeEdit.delete(x.id); }
          renderPlan();
        });
        tog.append(cb, document.createTextNode("Whole category"));
        head.appendChild(tog);
      }
      card.appendChild(head);
      const wrap = el("div", "fw-table-wrap");
      wrap.style.maxHeight = "none";
      const table = el("table", "fw-table fw-plan");
      const cols = el("colgroup");
      for (const w of [scopeEdit ? "80px" : "50px", "23%", "100px", "84px", "", "226px", "13%"]) {
        const col = el("col");
        if (w) col.style.width = w;
        cols.appendChild(col);
      }
      table.appendChild(cols);
      const thead = el("thead");
      const hr = el("tr");
      for (const [h, tip] of [["ID", ""], ["Test case", "Hold on a case for its expected behaviour and acceptance criteria"],
                              ["How", "From packets, a bench step, by command, or by hand"], ["Result", ""],
                              ["Evidence", "What the result rests on — and the packets that show it"],
                              ["Action", "Run a bench or command step; set a result by hand"], ["Remarks", "Defect ID / remarks"]]) {
        const th = el("th", "", h);
        if (tip) th.dataset.tip = tip;
        hr.appendChild(th);
      }
      thead.appendChild(hr);
      table.appendChild(thead);
      const tb = el("tbody");
      for (const cs of rows) tb.appendChild(caseRow(cs));
      table.appendChild(tb);
      wrap.appendChild(table);
      card.appendChild(wrap);
      host.appendChild(card);
    }
    if (!host.children.length) host.appendChild(el("div", "fw-none", "No case matches"));
  }

  function caseRow(cs) {
    const tr = el("tr", scopeEdit && !scopeEdit.has(cs.id) ? "fw-out" : "");
    const idc = el("td", "id");
    if (scopeEdit) {
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = scopeEdit.has(cs.id);
      cb.setAttribute("aria-label", "Part of testing");
      cb.addEventListener("change", () => {
        if (cb.checked) scopeEdit.add(cs.id); else scopeEdit.delete(cs.id);
        tr.classList.toggle("fw-out", !cb.checked);
        scopeCount();
      });
      idc.appendChild(cb);
      idc.appendChild(document.createTextNode(" "));
    }
    idc.appendChild(document.createTextNode(cs.id));
    tr.appendChild(idc);
    const what = el("td", "what", cs.what);
    what.appendChild(el("small", "", cs.group));
    if (cs.about) {
      what.dataset.tip = `Test case — ${cs.about.test}\nExpected behaviour — ${cs.about.expected}\n` +
        `Acceptance criteria — ${cs.about.accept}\nHow — ${cs.about.how}`;
    }
    tr.appendChild(what);
    const how = el("td", "act how");
    how.appendChild(el("span", "ui-pill sm muted", HOW[cs.kind] || cs.kind));
    tr.appendChild(how);
    const res = el("td", "act res");
    res.appendChild(pill(cs.result.status, true));
    tr.appendChild(res);
    const note = el("td", "note", cs.result.note || "");
    const f = cs.result.filter || {};
    if ((f.check || f.scenario) && cs.result.run) {
      const b = el("button", "ghost mini");
      b.type = "button";
      b.appendChild(ic("list"));
      b.appendChild(document.createTextNode("Packets"));
      b.addEventListener("click", async () => {
        await openTab(cs.result.run);
        setView("results");
        setFilter({ ...f, label: cs.id + " " + cs.what }, true);
      });
      note.appendChild(document.createTextNode(" "));
      note.appendChild(b);
    }
    tr.appendChild(note);
    const act = el("td", "act acts");
    if (cs.kind === "assisted" || cs.kind === "command") {
      const run = el("button", "ghost mini");
      run.type = "button";
      run.appendChild(ic("play"));
      run.appendChild(document.createTextNode(cs.result.status === "running" ? "Stop" : "Run"));
      run.disabled = !(cur && cur.state === "live" && cur.listening) && cs.result.status !== "running";
      if (run.disabled) run.dataset.tip = "A step runs on a live test taking packets from its listener";
      run.addEventListener("click", () => (cs.result.status === "running" ? stopStep(cs) : openStep(cs)));
      act.appendChild(run);
    }
    if (cs.kind !== "automatic") {
      const sel = el("select");
      for (const k of ["", "pass", "fail", "partial", "blocked", "na"]) {
        const o = el("option", "", k ? PSTATUS[k][0] : "Not run");
        o.value = k;
        sel.appendChild(o);
      }
      sel.value = cs.entry.status || "";
      sel.setAttribute("aria-label", "Status set by hand");
      sel.addEventListener("change", () => saveCase(cs, { status: sel.value }));
      act.appendChild(sel);
    }
    tr.appendChild(act);
    const rem = el("td", "rem");
    const inp = el("input");
    inp.placeholder = "Remarks";
    inp.setAttribute("aria-label", "Defect ID / remarks");
    inp.value = cs.entry.remarks || "";
    inp.addEventListener("change", () => saveCase(cs, { remarks: inp.value }));
    rem.appendChild(inp);
    tr.appendChild(rem);
    return tr;
  }

  async function saveCase(cs, values) {
    try {
      await api("/api/fwtest/plan/case", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmware: planData.firmware, case: cs.id, values }) });
      await loadPlan();
    } catch (e) {
      await UI.notice({ title: "Not saved", text: e.message });
    }
  }

  function openStep(cs) {
    stepCase = cs;
    $("fw-s-title").textContent = `${cs.id} · ${HOW[cs.kind]}`;
    $("fw-s-what").textContent = cs.what;
    const j = cs.judge || {};
    const labels = {};
    for (const sc of (cur && cur.summary.scenarios) || []) labels[sc.key] = sc.label;
    const waits = [...(j.scenarios || []), ...(((j.needs || {}).scenarios) || [])].map((k) => labels[k] || k);
    $("fw-s-expect").textContent = waits.length ? "Runway waits for: " + waits.join(", ")
      : j.measure ? "Judged over the packets of the window" : "Nothing in the packets judges this case: set its result by hand";
    $("fw-s-cmdf").hidden = cs.kind !== "command";
    $("fw-s-cmd").value = cs.entry.command || "";
    $("fw-s-holdf").hidden = !j.hold_ack;
    $("fw-s-hold").checked = !!j.hold_ack;
    $("fw-s-err").hidden = true;
    $("fw-step-dlg").showModal();
  }

  async function startStep(ev) {
    ev.preventDefault();
    const cs = stepCase;
    try {
      await api("/api/fwtest/plan/step", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmware: planData.firmware, case: cs.id, run: cur.id, window_s: Number($("fw-s-win").value) || 180,
                               command: cs.kind === "command" ? $("fw-s-cmd").value : "", hold_ack: $("fw-s-hold").checked }) });
      $("fw-step-dlg").close();
      await loadPlan();
    } catch (e) {
      $("fw-s-err").textContent = e.message;
      $("fw-s-err").hidden = false;
    }
  }

  async function stopStep(cs) {
    await api("/api/fwtest/plan/step/stop", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ firmware: planData.firmware, case: cs.id }) });
    await loadPlan();
  }

  // ── the platform's record, and a protocol sheet in ─────────────────────────────────────────────
  async function openPlatform() {
    if (!cur) return;
    const list = await api("/api/fwtest/analyses?device=" + encodeURIComponent(devOf(cur)));
    const sel = $("fw-plat-sel");
    sel.textContent = "";
    for (const a of list) {
      const o = el("option", "", `${a.device || "—"} · ${a.file || a.id}`);
      o.value = a.id;
      sel.appendChild(o);
    }
    $("fw-plat-none").hidden = list.length > 0;
    sel.closest("label").hidden = !list.length;
    $("fw-plat-go").disabled = !list.length;
    $("fw-plat-err").hidden = true;
    $("fw-plat-dlg").showModal();
  }
  async function comparePlatform(ev) {
    ev.preventDefault();
    try {
      const r = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/platform`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ analysis: $("fw-plat-sel").value }) });
      $("fw-plat-dlg").close();
      await UI.notice({ title: r.status === "pass" ? "Platform log: as sent" : r.status === "fail" ? "Platform log: differs" : "Platform log",
                        text: r.note + ". Times without a zone are read as India's (UTC+5:30)." });
      if (view === "plan") loadPlan();
    } catch (e) {
      $("fw-plat-err").textContent = e.message;
      $("fw-plat-err").hidden = false;
    }
  }
  async function importSheet() {
    const f = $("fw-d-sheetfile").files[0];
    $("fw-d-sheetfile").value = "";
    if (!f) return;
    const header = await UI.prompt({ title: "Import a protocol sheet", label: "The packet this sheet describes", value: "PVT", action: "Import" });
    if (!header) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("spec", $("fw-d-spec").value);
    fd.append("header", header);
    try {
      const r = await api("/api/fwtest/specs/import", { method: "POST", body: fd });
      const bits = [`${r.header}: ${r.fields} fields (was ${r.was})`];
      if (r.added.length) bits.push("Added: " + r.added.join(", "));
      if (r.removed.length) bits.push("Removed: " + r.removed.join(", "));
      if (r.moved.length) bits.push("Moved: " + r.moved.join(", "));
      await UI.notice({ title: "Sheet imported", text: bits.join("\n") });
      specs = await api("/api/fwtest/specs");
    } catch (e) {
      await UI.notice({ title: "Not imported", text: e.message });
    }
  }

  // ── a new test ─────────────────────────────────────────────────────────────────────────────
  let src = "live";
  let listeners = [];
  let file = null;

  function setSrc(s) {
    src = s;
    for (const b of $("fw-dlg").querySelectorAll("[data-src]")) b.classList.toggle("on", b.dataset.src === s);
    $("fw-d-live").hidden = s !== "live";
    $("fw-d-file").hidden = s !== "file";
  }

  function fillDevices() {
    const sel = $("fw-d-device");
    sel.textContent = "";
    const l = listeners.find((x) => String(x.port) === sel.dataset.port) || listeners[0];
    const any = el("option", "", "The first device to log in");
    any.value = "";
    sel.appendChild(any);
    const seen = new Set();
    for (const c of (l && l.clients) || []) {
      if (!c.device_id || seen.has(c.device_id)) continue;
      seen.add(c.device_id);
      const o = el("option", "", c.device_id + (c.connected ? " · connected" : ""));
      o.value = c.device_id;
      sel.appendChild(o);
    }
    if (seen.size === 1) sel.selectedIndex = 1;
  }

  let specs = [];
  let editing = null;      // the test whose configuration the dialog edits; null = a new test

  function fillConfig(c, spec) {
    $("fw-c-on").value = c.ign_on_s || "";
    $("fw-c-off").value = c.ign_off_s || "";
    $("fw-c-after").value = c.sleep_after_min || "";
    $("fw-c-sleep").value = c.sleep_s || "";
    $("fw-c-order").value = c.history_order || "";
    const host = $("fw-c-formats");
    host.textContent = "";
    for (const [h, choices] of Object.entries((spec && spec.formats) || {})) {
      const lab = el("label", "ui-dlg-field", `${h} format `);
      const sel = el("select");
      sel.dataset.header = h;
      for (const [k, words] of Object.entries(choices)) {
        const o = el("option", "", words);
        o.value = k;
        sel.appendChild(o);
      }
      sel.value = (c.formats || {})[h] || Object.keys(choices)[0];
      lab.appendChild(sel);
      host.appendChild(lab);
    }
  }
  function readConfig() {
    const formats = {};
    for (const sel of $("fw-c-formats").querySelectorAll("select")) formats[sel.dataset.header] = sel.value;
    return { ign_on_s: $("fw-c-on").value, ign_off_s: $("fw-c-off").value, sleep_after_min: $("fw-c-after").value,
             sleep_s: $("fw-c-sleep").value, history_order: $("fw-c-order").value, formats };
  }
  function specOf(name) { return specs.find((s) => s.name === name) || specs[0]; }

  async function editConfig() {
    if (!cur) return;
    if (!specs.length) specs = await api("/api/fwtest/specs");
    editing = cur.id;
    $("fw-d-err").hidden = true;
    $("fw-d-title").textContent = "Device configuration";
    $("fw-d-start-t").textContent = "Save";
    $("fw-d-specf").hidden = true;
    $("fw-d-srcrow").hidden = true;
    $("fw-d-live").hidden = true;
    $("fw-d-file").hidden = true;
    const e = cur.summary.expect || {};
    $("fw-d-imei").value = e.imei || "";
    $("fw-d-fw").value = e.firmware_version || "";
    $("fw-d-reg").value = e.vehicle_reg_no || "";
    fillConfig(cur.summary.config || {}, specOf(cur.spec));
    $("fw-dlg").showModal();
  }

  async function newTest() {
    editing = null;
    $("fw-d-title").textContent = "New firmware test";
    $("fw-d-start-t").textContent = "Start";
    $("fw-d-specf").hidden = false;
    $("fw-d-srcrow").hidden = false;
    for (const id of ["fw-d-imei", "fw-d-fw", "fw-d-reg"]) $(id).value = "";
    $("fw-d-err").hidden = true;
    file = null;
    $("fw-d-filename").textContent = "";
    const [sp, ls] = await Promise.all([api("/api/fwtest/specs"), api("/api/fwtest/listeners")]);
    specs = sp;
    const ss = $("fw-d-spec");
    ss.textContent = "";
    for (const s of sp) {
      const o = el("option", "", s.name);
      o.value = s.name;
      ss.appendChild(o);
    }
    fillConfig((sp[0] && sp[0].config) || {}, sp[0]);
    listeners = ls;
    const ps = $("fw-d-port");
    ps.textContent = "";
    for (const l of ls) {
      const o = el("option", "", `:${l.port}` + (l.relay ? ` → ${l.relay}` : "") + ` · ${l.clients.length} device${l.clients.length === 1 ? "" : "s"}`);
      o.value = String(l.port);
      ps.appendChild(o);
    }
    $("fw-d-nolisten").hidden = ls.length > 0;
    ps.closest("label").hidden = !ls.length;
    $("fw-d-device").closest("label").hidden = !ls.length;
    $("fw-d-device").dataset.port = ls.length ? String(ls[0].port) : "";
    fillDevices();
    setSrc(ls.length ? "live" : "file");
    $("fw-dlg").showModal();
  }

  async function start(ev) {
    ev.preventDefault();
    const err = $("fw-d-err");
    err.hidden = true;
    const expect = { imei: $("fw-d-imei").value.trim(), firmware_version: $("fw-d-fw").value.trim(),
                     vehicle_reg_no: $("fw-d-reg").value.trim() };
    const config = readConfig();
    try {
      let v;
      if (editing) {
        cur = await api(`/api/fwtest/runs/${encodeURIComponent(editing)}/config`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config, expect }) });
        $("fw-dlg").close();
        renderRun();
        await loadPackets(true);
        await loadRuns();
        return;
      }
      if (src === "live") {
        if (!listeners.length) throw new Error("No listener is running");
        v = await api("/api/fwtest/runs", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ spec: $("fw-d-spec").value, port: Number($("fw-d-port").value),
                                 device: $("fw-d-device").value, expect, config }) });
      } else {
        if (!file) throw new Error("Choose a file of packets");
        const fd = new FormData();
        fd.append("file", file);
        fd.append("spec", $("fw-d-spec").value);
        fd.append("expect_imei", expect.imei);
        fd.append("expect_firmware_version", expect.firmware_version);
        fd.append("expect_vehicle_reg_no", expect.vehicle_reg_no);
        fd.append("config", JSON.stringify(config));
        v = await api("/api/fwtest/runs/file", { method: "POST", body: fd });
      }
      $("fw-dlg").close();
      await loadRuns();
      await openTab(v.id);
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  // ── wiring ─────────────────────────────────────────────────────────────────────────────────
  $("fw-new").addEventListener("click", newTest);
  $("fw-test-sel").addEventListener("change", () => openTab($("fw-test-sel").value));
  $("fw-packets").closest(".fw-card").classList.add("fw-pk-card");
  for (const b of document.querySelectorAll(".fw-views button")) b.addEventListener("click", () => setView(b.dataset.view));
  for (const id of ["fw-plan-kind", "fw-plan-status"]) $(id).addEventListener("change", () => planData && renderPlan());
  $("fw-plan-q").addEventListener("input", () => planData && renderPlan());
  $("fw-suite-sel").addEventListener("change", () => planData && renderSuite());
  $("fw-suite-run").addEventListener("click", runSuite);
  $("fw-plan-cmp").addEventListener("change", () => renderCompare().catch((e) => UI.notice({ title: "Compare", text: e.message })));
  $("fw-plan-import").addEventListener("click", () => $("fw-plan-file").click());
  $("fw-scope-edit").addEventListener("click", () => editScope().catch((e) => UI.notice({ title: "Scope", text: e.message })));
  $("fw-scope-save").addEventListener("click", saveScope);
  $("fw-scope-cancel").addEventListener("click", cancelScope);
  $("fw-plan-file").addEventListener("change", async () => {
    const f = $("fw-plan-file").files[0];
    if (!f || !planData) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("firmware", planData.firmware);
    try {
      const r = await api("/api/fwtest/plan/import", { method: "POST", body: fd });
      await UI.notice({ title: "Tracker imported", text: `${r.cases} cases taken from ${f.name}` });
      await loadPlan();
    } catch (e) {
      await UI.notice({ title: "Not imported", text: e.message });
    }
    $("fw-plan-file").value = "";
  });
  $("fw-step-dlg").querySelector("form").addEventListener("submit", startStep);
  $("fw-step-dlg").querySelector("[data-cancel]").addEventListener("click", () => $("fw-step-dlg").close());
  $("fw-config").addEventListener("click", editConfig);
  $("fw-plat").addEventListener("click", () => openPlatform().catch((e) => UI.notice({ title: "Platform log", text: e.message })));
  $("fw-plat-dlg").querySelector("form").addEventListener("submit", comparePlatform);
  $("fw-plat-dlg").querySelector("[data-cancel]").addEventListener("click", () => $("fw-plat-dlg").close());
  $("fw-d-sheet").addEventListener("click", () => $("fw-d-sheetfile").click());
  $("fw-d-sheetfile").addEventListener("change", importSheet);
  $("fw-d-spec").addEventListener("change", () => fillConfig(specOf($("fw-d-spec").value).config || {}, specOf($("fw-d-spec").value)));
  $("fw-dlg").querySelector("form").addEventListener("submit", start);
  $("fw-dlg").querySelector("[data-cancel]").addEventListener("click", () => $("fw-dlg").close());
  for (const b of $("fw-dlg").querySelectorAll("[data-src]")) b.addEventListener("click", () => setSrc(b.dataset.src));
  $("fw-d-port").addEventListener("change", () => { $("fw-d-device").dataset.port = $("fw-d-port").value; fillDevices(); });
  $("fw-d-choose").addEventListener("click", () => $("fw-d-fileinput").click());
  $("fw-d-fileinput").addEventListener("change", () => {
    file = $("fw-d-fileinput").files[0] || null;
    $("fw-d-filename").textContent = file ? file.name : "";
  });
  for (const b of $("fw-levels").querySelectorAll("button")) {
    b.addEventListener("click", () => setFilter({ ...filt, level: b.dataset.level }));
  }
  $("fw-filter-x").addEventListener("click", () => setFilter({ level: filt.level }));
  // the packets for the developers: all, failed or with warnings — the set on screen offered first
  $("fw-pk-dl").addEventListener("click", (e) => {
    if (!cur) return;
    const s = cur.summary;
    const get = (which) => {
      const a = document.createElement("a");
      a.href = `/api/fwtest/runs/${encodeURIComponent(cur.id)}/packets.xlsx?which=${which}`;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    };
    UI.menu(e.currentTarget, "Download packets", [
      { label: "All packets", sub: num(s.packets), on: !filt.level, run: () => get("all") },
      { label: "Failed packets", sub: num(s.failed), on: filt.level === "fail", run: () => get("fail") },
      { label: "Packets with warnings", sub: num(s.warned), on: filt.level === "warn", run: () => get("warn") },
    ]);
  });
  $("fw-more-btn").addEventListener("click", () => loadPackets(false));
  $("fw-stop").addEventListener("click", async () => {
    cur = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/stop`, { method: "POST" });
    renderRun();
    await loadRuns();
    schedule();
  });
  $("fw-resume").addEventListener("click", async () => {
    cur = await api(`/api/fwtest/runs/${encodeURIComponent(cur.id)}/resume`, { method: "POST" });
    renderRun();
    await loadRuns();
    schedule();
  });
  $("fw-delete").addEventListener("click", async () => {
    const ok = await UI.confirm({ title: "Delete this test?",
      text: "Its packets and its result are deleted; the device and its listener carry on.", action: "Delete", tone: "danger" });
    if (!ok) return;
    const id = cur.id;
    try {
      await api("/api/fwtest/runs/" + encodeURIComponent(id), { method: "DELETE" });
    } catch (e) {
      await UI.notice({ title: "Not deleted", text: e.message });
      return;
    }
    await loadRuns();
    closeTab(id);
  });

  async function boot() {
    await loadRuns();
    let saved = null;
    try { saved = JSON.parse(store.get(TABS_KEY) || "null"); } catch (e) { saved = null; }
    tabs = [];
    for (const id of (saved && saved.tabs) || []) {                     // one per device, as saved
      if (runOf(id) && !tabs.some((t) => devKey(t.id) === devKey(id))) tabs.push({ id, st: null });
    }
    const want = saved && runOf(saved.focus) ? saved.focus : null;
    if (want) return focusTab(want);
    if (tabs.length) return focusTab(tabs[tabs.length - 1].id);
    if (runs.length) return openTab(runs[runs.length - 1].id);
    renderTabs();
    showEmpty();
  }

  UI.paint(document);
  boot().catch((e) => UI.notice({ title: "Firmware test", text: e.message }));
})();
