/* Device OTA — Runway as a TCU against the MapmyIndia InTouch OTA cloud.
 *
 * A *profile* is a saved device (environment, endpoint, IMEI, static keys; the comm keys it
 * downloads are kept with it) and lives on the server. A *tab* is one working session on a
 * profile — its four panes, its message type and the history of its exchanges — kept in this
 * browser, so open tabs survive a reload. The panel on the left describes the active tab's
 * profile and folds to an icon strip.
 *
 * The exchange reads as the protocol does: 1 readable request → Encode → 2 encrypted request →
 * Send → 3 encrypted response → Decode → 4 decrypted response. Send runs the whole round trip on
 * the server (downloading comm keys first when needed); Encode and Decode are the local steps.
 */
(() => {
  "use strict";
  const { $, api } = window.Runway;
  const withBody = (method) => (path, obj) => api(path, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj),
  });
  const POST = withBody("POST");
  const PUT = withBody("PUT");
  const DEL = (path) => api(path, { method: "DELETE" });

  // ---- icons: the one map every page shares (shell.js) ------------------------------------
  const ICONS = window.RunwayUI.icons;
  const ic = (name) => window.RunwayUI.icon(name);
  const paintIcons = (root) => window.RunwayUI.paint(root);

  // ---- small helpers -----------------------------------------------------------------------
  const ENV_LABEL = { dev: "Dev", prod: "Prod", custom: "Custom" };
  // Red is for a fault only. Prod is the real platform (accent), Dev the test one (amber, the DEV
  // mark used across these apps), Custom neutral.
  const ENV_TONE = { dev: "warn", prod: "info", custom: "neutral" };
  const TABS_KEY = "runway.ota.tabs";
  const PANEL_KEY = "runway.ota.panel";
  const HISTORY_MAX = 30;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pill = (text, tone, small) =>
    `<span class="ota-pill ${tone || "neutral"}${small ? " sm" : ""}">${esc(text)}</span>`;
  const envPill = (env, small) => pill(ENV_LABEL[env] || env, ENV_TONE[env] || "neutral", small);
  const pad2 = (n) => String(n).padStart(2, "0");
  const fmtDay = (ms) => {
    const d = new Date(ms);
    return `${pad2(d.getDate())}-${pad2(d.getMonth() + 1)}-${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  };
  const fmtTime = (ms) => {
    const d = new Date(ms);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  };
  const fmtTook = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`);
  const pretty = (s) => {
    if (!s) return "";
    try { return JSON.stringify(JSON.parse(s), null, 2); } catch (e) { return s; }
  };
  const hostOf = (url) => {
    try { const u = new URL(url); return u.host + u.pathname.replace(/\/$/, ""); } catch (e) { return url || ""; }
  };
  const keyName = (src, code) => `${src === "static" ? "Static" : "Comm"} key ${code}`;
  const crcOf = (sentence) => {
    const m = /,([0-9A-Fa-f]{1,2})\*\s*$/.exec(sentence || "");
    return m ? m[1].toUpperCase().padStart(2, "0") : "";
  };
  // The key a reply was sent under — the cloud picks its own (it answered a static-1 request
  // under static 2), so say which. Key set by message type, as the server decodes it.
  const replyKey = (sentence) => {
    const m = /^\$MMI,(\d+),[^,]*,(\d+),/.exec((sentence || "").trim());
    if (!m) return "";
    const type = Number(m[1]);
    return keyName(type === 1 || type === 2 ? "static" : "comm", Number(m[2]));
  };

  // ---- profiles (server) ---------------------------------------------------------------------
  let cfg = { environments: {}, message_types: {}, body_templates: {} };
  let profiles = [];
  const findProfile = (env, imei) => profiles.find((p) => p.env === env && p.imei === imei) || null;
  const profileName = (p) => (p && (p.name || p.imei)) || "";
  async function loadProfiles() {
    try { profiles = (await api("/api/ota/profiles")).profiles || []; } catch (e) { profiles = []; }
  }
  function mergeSummary(s) {
    if (!s) return;
    const i = profiles.findIndex((p) => p.env === s.env && p.imei === s.imei);
    if (i >= 0) profiles[i] = s; else profiles.push(s);
  }
  function keyState(p) {
    if (!p || !p.has_comm) return { text: "Not downloaded", tone: "muted", sub: "", action: "Download keys", type: 1 };
    if (p.expired) {
      return { text: "Expired", tone: "warn", sub: p.expire_ts ? `since ${fmtDay(p.expire_ts)}` : "", action: "Refresh keys", type: 2 };
    }
    return { text: `${p.comm_count} keys`, tone: "ok", sub: p.expire_ts ? `until ${fmtDay(p.expire_ts)}` : "", action: "Refresh keys", type: 2 };
  }

  // ---- tabs (this browser) ----------------------------------------------------------------
  let tabs = [];
  let activeId = null;
  let seq = 0;
  const newId = () => "t" + Date.now().toString(36) + (seq++).toString(36);
  function blankTab(env, imei) {
    return {
      id: newId(), env: env || null, imei: imei || null, msgType: 3, keyCode: 1, auto: true,
      p1: "", p2: "", p3: "", p4: "", p1Tpl: "", m1: [], m2: [], m3: [], m4: [],
      send: null, history: [], sel: -1,
    };
  }
  const tab = () => tabs.find((t) => t.id === activeId) || null;
  const hasProfile = (t) => !!(t && t.imei);

  let saveTimer = 0;
  function persist() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try { localStorage.setItem(TABS_KEY, JSON.stringify({ tabs, active: activeId })); } catch (e) { /* full / private */ }
    }, 250);
  }
  function restore() {
    try {
      const d = JSON.parse(localStorage.getItem(TABS_KEY) || "null");
      if (d && Array.isArray(d.tabs)) {
        tabs = d.tabs.map((t) => Object.assign(blankTab(), t));
        activeId = d.active;
      }
    } catch (e) { tabs = []; }
  }

  function templateFor(type, imei) {
    const t = cfg.body_templates[String(type)];
    if (!t) return "";
    return JSON.stringify(JSON.parse(JSON.stringify(t).split("<imei>").join(imei || "")), null, 2);
  }
  // Replace pane 1 with the type's template — unless the person has written their own request.
  function applyTemplate(t, force) {
    if (force || !t.p1.trim() || t.p1 === t.p1Tpl) {
      t.p1 = templateFor(t.msgType, t.imei);
      t.p1Tpl = t.p1;
      t.m1 = [];
    }
  }

  function openTab(env, imei) {
    const t = blankTab(env, imei);
    if (imei) applyTemplate(t, true);
    tabs.push(t);
    activeId = t.id;
    clearNotice();
    render();
    persist();
  }
  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (!tabs.length) tabs.push(blankTab());
    if (activeId === id) activeId = (tabs[Math.max(0, i - 1)] || tabs[0]).id;
    clearNotice();
    render();
    persist();
  }
  function activate(id) {
    if (id === activeId) return;
    activeId = id;
    clearNotice();
    render();
    persist();
  }
  function assignProfile(env, imei) {
    const t = tab();
    if (!t) return;
    Object.assign(t, blankTab(env, imei), { id: t.id });
    applyTemplate(t, true);
    clearNotice();
    render();
    persist();
  }

  // ---- notices & toasts -------------------------------------------------------------------
  function notice(text, tone) {
    const n = $("ota-notice");
    tone = tone || "bad";
    n.className = "ota-notice " + tone;
    n.innerHTML = ic(tone === "ok" ? "check" : "alert") + `<span class="ota-notice-t">${esc(text)}</span>` +
      `<button type="button" class="ota-icon-btn" aria-label="Dismiss">${ICONS.x}</button>`;
    n.querySelector("button").addEventListener("click", clearNotice);
    n.hidden = false;
  }
  function clearNotice() { $("ota-notice").hidden = true; }
  let toastTimer = 0;
  function toast(text) {
    const el = $("ota-toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 1800);
  }
  function busy(btn, on, label) {
    if (on) {
      btn.dataset.idle = btn.innerHTML;
      btn.innerHTML = `<span class="ota-spin"></span>${esc(label)}`;
      btn.disabled = true;
    } else if (btn.dataset.idle != null) {
      btn.innerHTML = btn.dataset.idle;
      delete btn.dataset.idle;
      btn.disabled = false;
    }
  }

  // ---- render ------------------------------------------------------------------------------
  function render() {
    renderTabs();
    renderPanel();
    renderWork();
  }

  function tabLabel(t) {
    if (!hasProfile(t)) return "New tab";
    return profileName(findProfile(t.env, t.imei)) || t.imei;
  }

  function renderTabs() {
    const bar = $("ota-tabs");
    bar.innerHTML = "";
    tabs.forEach((t) => {
      const el = document.createElement("div");
      const active = t.id === activeId;
      el.className = "ota-tab" + (active ? " active" : "") + (hasProfile(t) ? "" : " draft");
      el.setAttribute("role", "tab");
      el.setAttribute("aria-selected", String(active));
      el.tabIndex = active ? 0 : -1;
      el.title = hasProfile(t) ? `${tabLabel(t)} · ${t.imei} · ${ENV_LABEL[t.env] || t.env}` : "Choose a profile";
      el.innerHTML = (hasProfile(t) ? envPill(t.env, true) : "") +
        `<span class="ota-tab-name">${esc(tabLabel(t))}</span>` +
        `<button type="button" class="ota-tab-x" aria-label="Close tab" title="Close tab">${ICONS.x}</button>`;
      el.addEventListener("click", (e) => {
        if (e.target.closest(".ota-tab-x")) closeTab(t.id); else activate(t.id);
      });
      el.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); closeTab(t.id); } });
      el.addEventListener("keydown", (e) => {
        const i = tabs.indexOf(t);
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
          activate(next.id);
          bar.querySelector(".ota-tab.active")?.focus();
        }
      });
      bar.appendChild(el);
    });
    const add = document.createElement("button");
    add.type = "button";
    add.className = "ota-tab-add";
    add.title = "New tab";
    add.setAttribute("aria-label", "New tab");
    add.innerHTML = ICONS.plus;
    add.addEventListener("click", () => openTab());
    bar.appendChild(add);
  }

  function hopResult(h) {
    if (h.http_status == null) return { text: "No reply", tone: "bad" };
    if (h.response_type === 9) return { text: "Refused", tone: "bad" };
    if (!h.ok) return h.http_status === 200 ? { text: "Not decoded", tone: "warn" } : { text: `HTTP ${h.http_status}`, tone: "bad" };
    return { text: String(h.http_status), tone: "ok" };
  }

  function renderPanel() {
    const t = tab();
    const body = $("ota-panel-body");
    const strip = $("ota-panel-strip");
    if (!hasProfile(t)) {
      body.innerHTML = '<div class="ota-psec"><p class="ota-empty-line">No profile in this tab.</p></div>';
      strip.innerHTML = "";
      return;
    }
    const p = findProfile(t.env, t.imei) || { env: t.env, imei: t.imei, name: "", endpoint: "", static_count: 0 };
    const ks = keyState(p);
    const n = t.history.length;
    body.innerHTML =
      `<div class="ota-psec">
        <div class="ota-ident">
          <span class="ota-ident-ico">${ICONS.device}</span>
          <div class="ota-ident-txt">
            <div class="ota-ident-name${p.name ? "" : " mono"}">${esc(profileName(p))}</div>
            ${p.name ? `<div class="ota-ident-sub mono">${esc(p.imei)}</div>` : ""}
          </div>
          <button type="button" class="ota-icon-btn" id="ota-edit" title="Edit profile" aria-label="Edit profile">${ICONS.edit}</button>
        </div>
        <dl class="ota-facts">
          <dt>Environment</dt><dd>${envPill(p.env)}</dd>
          <dt>Endpoint</dt><dd class="mono ota-ellip" title="${esc(p.endpoint)}">${p.endpoint ? esc(hostOf(p.endpoint)) : '<span class="ota-dim">—</span>'}</dd>
        </dl>
      </div>
      <div class="ota-rule"></div>
      <div class="ota-psec">
        <div class="ota-psec-h">Keys</div>
        <dl class="ota-facts">
          <dt>Static</dt><dd>${p.static_count ? esc(`${p.static_count} saved`) : pill("None saved", "warn")}</dd>
          <dt>Comm</dt><dd><span class="ota-keyline">${pill(ks.text, ks.tone)}${ks.sub ? `<span class="ota-keyline-sub">${esc(ks.sub)}</span>` : ""}</span></dd>
        </dl>
        <div class="ota-psec-actions">
          <button type="button" class="ota-btn sm" id="ota-viewkeys">${ic("eye")}View keys</button>
          <button type="button" class="ota-btn sm" id="ota-dlkeys">${ic("refresh")}${esc(ks.action)}</button>
        </div>
      </div>
      <div class="ota-rule"></div>
      <div class="ota-psec">
        <div class="ota-psec-h">History${n ? `<span class="ota-count">${n}</span>` : ""}<span class="spacer"></span>${n ? '<button type="button" class="ota-btn sm quiet" id="ota-hist-clear">Clear</button>' : ""}</div>
        ${n ? '<ol class="ota-hist-list" id="ota-hist-list"></ol>' : '<p class="ota-empty-line">No exchanges yet.</p>'}
      </div>`;
    $("ota-edit").addEventListener("click", () => openProfileDialog(p));
    $("ota-viewkeys").addEventListener("click", openKeys);
    $("ota-dlkeys").addEventListener("click", async () => {
      const r = await downloadKeys(ks.type, $("ota-dlkeys"));
      if (r.ok) toast(ks.type === 2 ? "Comm keys refreshed" : "Comm keys downloaded");
      else notice(r.msg);
    });
    if (n) {
      $("ota-hist-clear").addEventListener("click", clearHistory);
      const list = $("ota-hist-list");
      t.history.forEach((h, i) => {
        const li = document.createElement("li");
        li.className = "ota-hist-item" + (i === t.sel ? " sel" : "");
        li.tabIndex = 0;
        const res = hopResult(h);
        li.title = h.note || h.type_label;
        li.innerHTML = `<span class="ota-hist-t mono">${fmtTime(h.at)}</span>` +
          `<span class="ota-hist-what">${esc(h.type_label)}${h.auto ? '<span class="ota-hist-auto">auto</span>' : ""}</span>` +
          pill(res.text, res.tone, true);
        li.addEventListener("click", () => selectHop(i));
        li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectHop(i); } });
        list.appendChild(li);
      });
    }
    // the folded strip: the same three facts as icons, each opening the panel
    strip.innerHTML =
      `<button type="button" class="ota-strip-btn" title="${esc(`${profileName(p)} · ${p.imei} · ${ENV_LABEL[p.env] || p.env}`)}">${ICONS.device}</button>` +
      `<button type="button" class="ota-strip-btn" title="${esc(`Comm keys: ${ks.text}${ks.sub ? " " + ks.sub : ""}`)}">${ICONS.key}<span class="ota-strip-dot ${ks.tone}"></span></button>` +
      `<button type="button" class="ota-strip-btn" title="${esc(`History: ${n} ${n === 1 ? "exchange" : "exchanges"}`)}">${ICONS.history}${n ? `<span class="ota-strip-badge">${n}</span>` : ""}</button>`;
    strip.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setFolded(false)));
  }

  function setMetas(id, list) {
    $(id).innerHTML = (list || []).map((m) => pill(m.text, m.tone || "neutral", true)).join("");
  }
  function keyOptions(t) {
    const sel = $("ota-keycode");
    const statics = t.msgType === 1 || t.msgType === 2;
    const n = statics ? 3 : 8;
    if (t.keyCode > n) t.keyCode = 1;
    sel.innerHTML = "";
    for (let i = 1; i <= n; i++) {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${statics ? "Static" : "Comm"} ${i}`;
      sel.appendChild(o);
    }
    sel.value = String(t.keyCode);
  }

  function renderWork() {
    const t = tab();
    const chooser = $("ota-chooser");
    const flow = $("ota-flow");
    if (!hasProfile(t)) {
      flow.hidden = true;
      chooser.hidden = false;
      renderChooser();
      return;
    }
    chooser.hidden = true;
    flow.hidden = false;
    $("ota-type").value = String(t.msgType);
    keyOptions(t);
    $("ota-auto").checked = !!t.auto;
    $("ota-p1").value = t.p1;
    $("ota-p2").value = t.p2;
    $("ota-p3").value = t.p3;
    $("ota-p4").value = t.p4;
    setMetas("ota-m1", t.m1);
    setMetas("ota-m2", t.m2);
    setMetas("ota-m3", t.m3);
    setMetas("ota-m4", t.m4);
    const st = $("ota-send-status");
    st.className = "ota-send-status" + (t.send ? " " + t.send.tone : "");
    st.textContent = t.send ? t.send.text : "";
    const p = findProfile(t.env, t.imei);
    $("ota-send").title = p && p.endpoint ? `Send to ${hostOf(p.endpoint)} (Ctrl+Enter)` : "Send to the cloud (Ctrl+Enter)";
  }

  function renderChooser() {
    const grid = $("ota-prof-grid");
    $("ota-prof-count").textContent = profiles.length ? String(profiles.length) : "";
    if (!profiles.length) {
      grid.innerHTML = `<div class="ota-empty" style="grid-column: 1 / -1">${ic("profiles")}` +
        '<p class="ota-empty-t">No profiles yet.</p></div>';
      return;
    }
    grid.innerHTML = "";
    profiles.forEach((p) => {
      const ks = keyState(p);
      const card = document.createElement("button");
      card.type = "button";
      card.className = "ota-prof-card";
      card.innerHTML = `<span class="ota-ident-ico">${ICONS.device}</span>` +
        `<span class="ota-prof-card-txt"><span class="ota-prof-card-name${p.name ? "" : " mono"}">${esc(profileName(p))}</span>` +
        (p.name ? `<span class="ota-prof-card-imei mono">${esc(p.imei)}</span>` : "") + "</span>" +
        `<span class="ota-prof-card-pills">${envPill(p.env, true)}${pill(ks.text, ks.tone, true)}</span>`;
      card.addEventListener("click", () => assignProfile(p.env, p.imei));
      grid.appendChild(card);
    });
  }

  // ---- an exchange shown in the panes ------------------------------------------------------
  function bodyMetas(text) {
    try {
      const o = JSON.parse(text);
      if (o && o.status != null) return [{ text: `Status ${o.status}`, tone: Number(o.status) === 200 ? "ok" : "warn" }];
    } catch (e) { /* not JSON */ }
    return [];
  }
  function showHop(t, h) {
    t.p1 = pretty(h.readable_request);
    t.p1Tpl = "";
    t.p2 = h.encrypted_request || "";
    t.p3 = h.encrypted_response || "";
    t.p4 = pretty(h.decrypted_response);
    t.m1 = h.message_type !== t.msgType ? [{ text: h.type_label }] : [];
    const crc = crcOf(h.encrypted_request);
    t.m2 = [{ text: keyName(h.key_source, h.key_code) }].concat(crc ? [{ text: `CRC ${crc}` }] : []);
    t.m3 = [];
    if (h.http_status != null) t.m3.push({ text: `HTTP ${h.http_status}`, tone: h.http_status === 200 ? "ok" : "bad" });
    if (h.response_type != null) t.m3.push({ text: `Type ${h.response_type}`, tone: h.response_type === 9 ? "bad" : "neutral" });
    if (h.response_checksum_ok === false) t.m3.push({ text: "CRC mismatch", tone: "bad" });
    const rk = replyKey(h.encrypted_response);
    if (rk) t.m3.push({ text: rk });
    t.m4 = bodyMetas(h.decrypted_response);
  }
  function selectHop(i) {
    const t = tab();
    if (!t || !t.history[i]) return;
    showHop(t, t.history[i]);
    t.sel = i;
    t.send = null;
    clearNotice();
    renderPanel();
    renderWork();
    persist();
  }
  async function clearHistory() {
    const t = tab();
    if (!t) return;
    const ok = await confirmDialog("Clear the history?",
      "The exchanges listed for this tab are removed. The profile and its keys are kept.", "Clear");
    if (!ok) return;
    t.history = [];
    t.sel = -1;
    renderPanel();
    persist();
  }

  // A send's chain of hops goes into the history (newest first) and the last one into the panes.
  function takeChain(t, r, took) {
    const at = Date.now();
    const hops = (r.chain || []).map((h) => Object.assign({}, h, { at, auto: r.chain.length > 1 && h.step === "keys" }));
    hops.forEach((h) => t.history.unshift(h));
    if (t.history.length > HISTORY_MAX) t.history.length = HISTORY_MAX;
    const last = hops[hops.length - 1];
    if (!last) return null;
    showHop(t, last);
    t.sel = 0;
    t.send = last.http_status == null
      ? { text: "No reply", tone: "bad" }
      : { text: `HTTP ${last.http_status} · ${fmtTook(took)}`, tone: last.ok ? "ok" : "bad" };
    mergeSummary(r.summary);
    return last;
  }
  function explain(h) {
    if (!h || h.ok) return "";
    if (h.response_type === 9) return "The cloud refused the message — its answer is in pane 4.";
    return h.note ? h.note.charAt(0).toUpperCase() + h.note.slice(1) + "." : "The exchange did not complete.";
  }

  // ---- actions -----------------------------------------------------------------------------
  async function encode() {
    const t = tab();
    if (!hasProfile(t)) return;
    clearNotice();
    try {
      const r = await POST("/api/ota/encode", {
        env: t.env, imei: t.imei, message_type: t.msgType, key_code: t.keyCode, readable: t.p1,
      });
      t.p2 = r.encrypted_request;
      t.m2 = [{ text: keyName(r.key_source, r.key_code) }, { text: `CRC ${r.checksum}` }];
      // a new request: the previous answer no longer belongs to it
      t.p3 = t.p4 = "";
      t.m3 = [];
      t.m4 = [];
      t.send = null;
      t.sel = -1;
      renderWork();
      renderPanel();
      persist();
    } catch (e) { notice(e.message); }
  }

  async function decode() {
    const t = tab();
    if (!hasProfile(t)) return;
    clearNotice();
    if (!t.p3.trim()) { notice("Pane 3 is empty — send the request, or paste a response there.", "warn"); return; }
    try {
      const r = await POST("/api/ota/decode", {
        env: t.env, imei: t.imei, sentence: t.p3.trim(), message_type: t.msgType, key_code: t.keyCode,
      });
      t.p4 = pretty(r.decrypted);
      t.m3 = [{ text: `Type ${r.message_type}`, tone: r.message_type === 9 ? "bad" : "neutral" }];
      if (r.checksum_ok === false) t.m3.push({ text: "CRC mismatch", tone: "bad" });
      t.m3.push({ text: keyName(r.key_source, r.key_code) });
      t.m4 = bodyMetas(r.decrypted);
      renderWork();
      persist();
    } catch (e) { notice(e.message); }
  }

  async function send() {
    const t = tab();
    const btn = $("ota-send");
    if (!hasProfile(t) || btn.disabled) return;
    clearNotice();
    busy(btn, true, "Sending");
    const t0 = performance.now();
    try {
      const body = { env: t.env, imei: t.imei, message_type: t.msgType, key_code: t.keyCode, auto_keys: t.auto };
      if (t.msgType > 2) body.readable = t.p1;
      const r = await POST("/api/ota/send", body);
      const last = takeChain(t, r, performance.now() - t0);
      render();
      persist();
      if (last && !last.ok) notice(explain(last));
    } catch (e) {
      notice(e.message);
    } finally {
      busy(btn, false);
    }
  }

  // Download (type 1) or refresh (type 2) the comm keys of the active tab's profile. The exchange
  // goes into the tab's history and panes like any other; the caller says how it went.
  async function downloadKeys(type, btn) {
    const t = tab();
    if (!hasProfile(t)) return { ok: false, msg: "No profile in this tab." };
    clearNotice();
    busy(btn, true, type === 2 ? "Refreshing" : "Downloading");
    const t0 = performance.now();
    let last = null;
    let err = "";
    try {
      const r = await POST("/api/ota/send", { env: t.env, imei: t.imei, message_type: type, key_code: 1 });
      last = takeChain(t, r, performance.now() - t0);
    } catch (e) { err = e.message; }
    busy(btn, false);
    render();
    persist();
    const msg = err || (last && !last.ok ? explain(last) : "");
    return { ok: !msg, msg };
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { return false; }
  }
  async function copy(text, what) {
    if (!text) { toast("Nothing to copy yet"); return; }
    if (await copyText(text)) toast(`${what} copied`);
    else notice("The browser did not allow copying.");
  }
  // Inside a dialog a toast would sit under it, so the button itself says "copied" for a moment.
  function flashCopied(btn, label) {
    if (btn.dataset.idle != null) return;
    btn.dataset.idle = btn.innerHTML;
    btn.innerHTML = ICONS.tick + (label ? label : "");
    btn.classList.add("copied");
    setTimeout(() => {
      btn.innerHTML = btn.dataset.idle;
      delete btn.dataset.idle;
      btn.classList.remove("copied");
    }, 1300);
  }

  // ---- the panel fold ---------------------------------------------------------------------
  const isFolded = () => document.documentElement.getAttribute("data-ota-panel") === "folded";
  function setFolded(folded) {
    if (folded) document.documentElement.setAttribute("data-ota-panel", "folded");
    else document.documentElement.removeAttribute("data-ota-panel");
    try { localStorage.setItem(PANEL_KEY, folded ? "folded" : "open"); } catch (e) { /* private */ }
    paintFold();
  }
  function paintFold() {
    const folded = isFolded();
    const b = $("ota-fold");
    b.innerHTML = ICONS[folded ? "panel-open" : "panel-left"];
    b.title = folded ? "Open the panel" : "Fold the panel";
    b.setAttribute("aria-label", b.title);
    b.setAttribute("aria-expanded", String(!folded));
  }

  // ---- dialogs -------------------------------------------------------------------------------
  function confirmDialog(title, text, action) {
    const d = $("ota-confirm");
    $("oc-title").textContent = title;
    $("oc-text").textContent = text;
    $("oc-ok").textContent = action;
    d.returnValue = "";
    d.showModal();
    $("oc-cancel").focus();
    return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }));
  }

  // ---- the keys dialog: every key of the active tab's profile ----------------------------
  let kdData = null;     // the device record shown: {static_keys, comm_map, expire_ts, fetched_ms, summary}
  const kdRow = (n, key) =>
    `<div class="ota-kd-row"><span class="ota-key-n">${esc(n)}</span><span class="ota-kd-key">${esc(key)}</span>` +
    `<button type="button" class="ota-icon-btn" data-copy="${esc(key)}" title="Copy" aria-label="Copy key ${esc(n)}">${ICONS.copy}</button></div>`;
  function kdError(msg) {
    const e = $("kd-error");
    e.innerHTML = ic("alert") + `<span class="ota-notice-t">${esc(msg)}</span>`;
    e.hidden = false;
  }
  async function loadKeys() {
    const t = tab();
    if (!hasProfile(t)) return;
    try {
      kdData = await api(`/api/ota/devices/${encodeURIComponent(t.imei)}?env=${encodeURIComponent(t.env)}`);
      renderKeys();
    } catch (e) { kdError(e.message); }
  }
  function renderKeys() {
    const t = tab();
    const d = kdData || {};
    const s = d.summary || {};
    const p = findProfile(t.env, t.imei) || s;
    $("kd-who").textContent = `${profileName(p)} · ${ENV_LABEL[t.env] || t.env}`;
    const statics = d.static_keys || [];
    $("kd-static-n").textContent = statics.length ? String(statics.length) : "";
    $("kd-static").innerHTML = statics.length
      ? statics.map((k, i) => kdRow(i + 1, k)).join("")
      : '<div class="ota-kd-empty">No static keys saved.</div>';
    const comm = Object.entries(d.comm_map || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
    $("kd-comm-n").textContent = comm.length ? String(comm.length) : "";
    $("kd-comm-state").innerHTML = comm.length ? (s.expired ? pill("Expired", "warn", true) : pill("Valid", "ok", true)) : "";
    const when = [];
    if (comm.length && d.expire_ts) when.push(`${s.expired ? "Expired" : "Until"} ${fmtDay(d.expire_ts)}`);
    if (comm.length && d.fetched_ms) when.push(`downloaded ${fmtDay(d.fetched_ms)}`);
    $("kd-comm-when").textContent = when.join(" · ");
    $("kd-comm-when").hidden = !when.length;
    $("kd-comm").innerHTML = comm.length
      ? comm.map(([n, k]) => kdRow(n, k)).join("")
      : '<div class="ota-kd-empty">Not downloaded yet.</div>';
    $("kd-refresh").innerHTML = ic("refresh") + (comm.length ? "Refresh keys" : "Download keys");
    $("kd-copy-all").disabled = !statics.length && !comm.length;
  }
  async function openKeys() {
    kdData = null;
    $("kd-error").hidden = true;
    $("kd-static").innerHTML = $("kd-comm").innerHTML = "";
    $("ota-keys-dlg").showModal();
    await loadKeys();
  }
  function keysAsText() {
    const t = tab();
    const d = kdData || {};
    const p = findProfile(t.env, t.imei) || {};
    const lines = [`${profileName(p)} · ${ENV_LABEL[t.env] || t.env} · IMEI ${t.imei}`, "", "Static keys"];
    (d.static_keys || []).forEach((k, i) => lines.push(`${i + 1}  ${k}`));
    const comm = Object.entries(d.comm_map || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
    lines.push("", `Communication keys${d.expire_ts ? ` (until ${fmtDay(d.expire_ts)})` : ""}`);
    comm.forEach(([n, k]) => lines.push(`${n}  ${k}`));
    return lines.join("\n");
  }

  const HEX32 = /^[0-9a-fA-F]{32}$/;
  const KEY_FIELDS = ["pf-k1", "pf-k2", "pf-k3"];
  let editing = null;          // the profile being edited ({env, imei}), or null for a new one
  let pfEnv = "dev";
  let customUrl = "";          // what was typed for Custom, kept while trying a preset and back

  function setPfEnv(env) {
    pfEnv = env;
    $("pf-env").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.env === env)));
    const ep = $("pf-endpoint");
    if (env === "custom") {
      ep.readOnly = false;
      ep.value = customUrl;
      ep.placeholder = "https://…";
    } else {
      ep.readOnly = true;
      ep.value = cfg.environments[env] || "";
      ep.placeholder = "";
    }
  }
  function checkKey(f) {
    const v = f.value.trim();
    const bad = v !== "" && !HEX32.test(v);
    f.classList.toggle("bad", bad);
    return !bad;
  }
  function profileError(msg) {
    const e = $("pf-error");
    e.innerHTML = ic("alert") + `<span class="ota-notice-t">${esc(msg)}</span>`;
    e.hidden = false;
  }
  function openProfileDialog(p) {
    editing = p ? { env: p.env, imei: p.imei } : null;
    $("pf-title").textContent = p ? "Edit profile" : "New profile";
    $("pf-save").textContent = p ? "Save changes" : "Save profile";
    $("pf-name").value = p ? (p.name || "") : "";
    $("pf-imei").value = p ? p.imei : "";
    customUrl = p && p.env === "custom" ? (p.endpoint || "") : "";
    KEY_FIELDS.forEach((id) => { $(id).value = ""; $(id).classList.remove("bad"); });
    $("pf-error").hidden = true;
    $("pf-delete").hidden = !p;
    setPfEnv(p ? p.env : "dev");
    if (p) {
      api(`/api/ota/devices/${encodeURIComponent(p.imei)}?env=${encodeURIComponent(p.env)}`).then((d) => {
        (d.static_keys || []).forEach((k, i) => { const f = $(KEY_FIELDS[i]); if (f) f.value = k; });
      }).catch(() => { /* the fields stay empty; saving then needs the keys again */ });
    }
    $("ota-prof-dlg").showModal();
    $("pf-name").focus();
  }
  async function saveProfile(ev) {
    ev.preventDefault();
    const imei = $("pf-imei").value.trim();
    const keysOk = KEY_FIELDS.map((id) => checkKey($(id))).every(Boolean);
    if (!imei) { profileError("Enter the device's IMEI."); return; }
    if (!keysOk) { profileError("A static key is 32 hexadecimal characters."); return; }
    const endpoint = $("pf-endpoint").value.trim();
    if (pfEnv === "custom" && !endpoint) { profileError("A custom environment needs its endpoint URL."); return; }
    const body = {
      env: pfEnv, name: $("pf-name").value.trim(), endpoint: pfEnv === "custom" ? endpoint : "",
      static_keys: KEY_FIELDS.map((id) => $(id).value.trim().toUpperCase()).filter(Boolean),
    };
    const btn = $("pf-save");
    busy(btn, true, "Saving");
    try {
      await PUT(`/api/ota/devices/${encodeURIComponent(imei)}`, body);
      const moved = editing && (editing.env !== pfEnv || editing.imei !== imei);
      if (moved) {
        await DEL(`/api/ota/devices/${encodeURIComponent(editing.imei)}?env=${encodeURIComponent(editing.env)}`);
        tabs.forEach((t) => {
          if (t.env === editing.env && t.imei === editing.imei) { t.env = pfEnv; t.imei = imei; }
        });
      }
      await loadProfiles();
      const t = tab();
      $("ota-prof-dlg").close();
      if (!editing && t && !hasProfile(t)) assignProfile(pfEnv, imei);   // made from a new tab: open it here
      else { render(); persist(); }
      toast(editing ? "Profile saved" : "Profile created");
    } catch (e) {
      profileError(e.message);
    } finally {
      busy(btn, false);
    }
  }
  async function deleteProfile() {
    if (!editing) return;
    const p = findProfile(editing.env, editing.imei);
    const ok = await confirmDialog("Delete this profile?",
      `“${profileName(p) || editing.imei}” and its saved keys are removed. Tabs using it go back to choosing a profile.`,
      "Delete");
    if (!ok) return;
    try {
      await DEL(`/api/ota/devices/${encodeURIComponent(editing.imei)}?env=${encodeURIComponent(editing.env)}`);
      tabs.forEach((t) => {
        if (t.env === editing.env && t.imei === editing.imei) Object.assign(t, blankTab(), { id: t.id });
      });
      await loadProfiles();
      $("ota-prof-dlg").close();
      render();
      persist();
      toast("Profile deleted");
    } catch (e) { profileError(e.message); }
  }

  // ---- wiring ------------------------------------------------------------------------------
  function wire() {
    $("ota-fold").addEventListener("click", () => setFolded(!isFolded()));
    // a narrow window: the open panel lies over the work, so working there folds it away
    $("ota-work").addEventListener("pointerdown", () => {
      if (window.innerWidth <= 1000 && !isFolded()) setFolded(true);
    });

    $("ota-new-profile").addEventListener("click", () => openProfileDialog(null));

    $("ota-type").addEventListener("change", () => {
      const t = tab();
      if (!t) return;
      t.msgType = Number($("ota-type").value);
      applyTemplate(t, false);
      keyOptions(t);
      $("ota-p1").value = t.p1;
      setMetas("ota-m1", t.m1);
      persist();
    });
    $("ota-keycode").addEventListener("change", () => {
      const t = tab();
      if (t) { t.keyCode = Number($("ota-keycode").value); persist(); }
    });
    $("ota-auto").addEventListener("change", () => {
      const t = tab();
      if (t) { t.auto = $("ota-auto").checked; persist(); }
    });
    $("ota-p1").addEventListener("input", () => {
      const t = tab();
      if (!t) return;
      t.p1 = $("ota-p1").value;
      if (t.m1.length) { t.m1 = []; setMetas("ota-m1", []); }
      persist();
    });
    $("ota-p3").addEventListener("input", () => {
      const t = tab();
      if (t) { t.p3 = $("ota-p3").value; persist(); }
    });

    $("ota-format").addEventListener("click", () => {
      const t = tab();
      if (!t) return;
      try {
        t.p1 = JSON.stringify(JSON.parse(t.p1), null, 2);
        $("ota-p1").value = t.p1;
        persist();
      } catch (e) { notice("Pane 1 is not valid JSON.", "warn"); }
    });
    $("ota-template").addEventListener("click", () => {
      const t = tab();
      if (!t) return;
      applyTemplate(t, true);
      $("ota-p1").value = t.p1;
      setMetas("ota-m1", t.m1);
      persist();
    });

    $("ota-encode").addEventListener("click", encode);
    $("ota-decode").addEventListener("click", decode);
    $("ota-send").addEventListener("click", send);
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !$("ota-flow").hidden && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        send();
      }
    });

    $("ota-copy2").addEventListener("click", () => copy($("ota-p2").value, "Encrypted request"));
    $("ota-copy3").addEventListener("click", () => copy($("ota-p3").value, "Encrypted response"));
    $("ota-copy4").addEventListener("click", () => copy($("ota-p4").value, "Decrypted response"));
    $("ota-curl").addEventListener("click", () => {
      const t = tab();
      const p = t && findProfile(t.env, t.imei);
      const s = $("ota-p2").value.trim();
      if (!s) { toast("Encode the request first"); return; }
      copy(`curl --location '${p ? p.endpoint : ""}' \\\n  --header 'Content-Type: application/json' \\\n  --data '${s}'`, "curl command");
    });

    // profile dialog
    $("pf-env").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setPfEnv(b.dataset.env)));
    $("pf-endpoint").addEventListener("input", () => { if (pfEnv === "custom") customUrl = $("pf-endpoint").value; });
    KEY_FIELDS.forEach((id) => $(id).addEventListener("input", () => { checkKey($(id)); $("pf-error").hidden = true; }));
    $("ota-prof-form").addEventListener("submit", saveProfile);
    $("pf-delete").addEventListener("click", deleteProfile);
    document.querySelectorAll("#ota-prof-dlg [data-close]").forEach((b) =>
      b.addEventListener("click", () => $("ota-prof-dlg").close()));
    $("ota-prof-dlg").addEventListener("click", (e) => { if (e.target === $("ota-prof-dlg")) $("ota-prof-dlg").close(); });

    // keys dialog
    const kd = $("ota-keys-dlg");
    kd.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => kd.close()));
    kd.addEventListener("click", async (e) => {
      if (e.target === kd) { kd.close(); return; }
      const b = e.target.closest("[data-copy]");
      if (b) { if (await copyText(b.dataset.copy)) flashCopied(b); else kdError("The browser did not allow copying."); }
    });
    $("kd-copy-all").addEventListener("click", async () => {
      if (await copyText(keysAsText())) flashCopied($("kd-copy-all"), "Copied");
      else kdError("The browser did not allow copying.");
    });
    $("kd-refresh").addEventListener("click", async () => {
      const type = kdData && Object.keys(kdData.comm_map || {}).length ? 2 : 1;
      $("kd-error").hidden = true;
      const r = await downloadKeys(type, $("kd-refresh"));
      if (r.ok) await loadKeys(); else { renderKeys(); kdError(r.msg); }
    });

    // confirm dialog
    $("oc-ok").addEventListener("click", () => $("ota-confirm").close("ok"));
    $("oc-cancel").addEventListener("click", () => $("ota-confirm").close("cancel"));
  }

  // ---- start -------------------------------------------------------------------------------
  async function init() {
    paintIcons();
    paintFold();
    wire();
    try {
      cfg = await api("/api/ota/config");
    } catch (e) {
      notice("Could not load the OTA settings from the server — " + e.message);
      return;
    }
    const sel = $("ota-type");
    Object.entries(cfg.message_types)
      .filter(([k]) => Number(k) !== 9)                  // a device never sends the cloud's error reply
      .forEach(([k, v]) => {
        const o = document.createElement("option");
        o.value = k;
        o.textContent = `${k} · ${v}`;
        sel.appendChild(o);
      });
    await loadProfiles();
    restore();
    // a tab whose profile was deleted elsewhere goes back to choosing one
    tabs.forEach((t) => { if (hasProfile(t) && !findProfile(t.env, t.imei)) Object.assign(t, blankTab(), { id: t.id }); });

    // ?env=…&imei=… opens that profile (a device view is a link)
    const q = new URLSearchParams(location.search);
    const linkImei = q.get("imei");
    if (linkImei) {
      const env = q.get("env") || (profiles.find((p) => p.imei === linkImei) || {}).env || "dev";
      if (findProfile(env, linkImei)) {
        const open = tabs.find((t) => t.env === env && t.imei === linkImei);
        if (open) activeId = open.id;
        else { const t = blankTab(env, linkImei); applyTemplate(t, true); tabs.push(t); activeId = t.id; }
      }
    }
    if (!tabs.length) { const t = blankTab(); tabs.push(t); activeId = t.id; }
    if (!tab()) activeId = tabs[0].id;
    render();
    persist();
  }

  init();
})();
