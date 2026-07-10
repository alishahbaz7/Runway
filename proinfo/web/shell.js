/* Runway app shell: a fixed top banner (toggle + brand + settings + data-mode badge)
 * over a collapsible left rail.
 *
 * Layout built at load:
 *   body (column) ─ #topbar (☰ + Runway logo/name … ⚙ settings + Live/Demo badge)
 *                 └ #app (row) ─ #rail (nav, collapsible) | #content (page)
 *
 * Two things worth knowing:
 *   - Data-mode badge is AUTOMATIC (not a switch): it polls /api/health and shows
 *     Live (a listener/QC session is running), Demo (a scripted file replay is running),
 *     or Idle (nothing running). Live wins when both are active.
 *   - Settings (⚙) lets the operator show/hide sidebar features. Unreleased features
 *     (no page yet — "soon") are hidden by default; the choice persists in localStorage.
 */
(() => {
  "use strict";

  const SECTIONS = [
    { title: "Overview", items: [
      { id: "dashboard", label: "Dashboard", icon: "▦", href: "index.html" },
    ] },
    { title: "Tools", items: [
      { id: "parser", label: "Packet Parser", icon: "⤧", href: "parser.html" },
      { id: "converter", label: "Converter", icon: "⇄", href: "converter.html" },
    ] },
    { title: "Capture", items: [
      { id: "tcp", label: "Live TCP", icon: "📡", href: "tcp.html" },
      { id: "qc", label: "Device QC", icon: "✅", href: "qc.html" },
      { id: "captures", label: "Captures", icon: "🗂" },          // no page yet → "soon"
    ] },
    { title: "Library", items: [
      { id: "profiles", label: "Profiles", icon: "🧩" },          // no page yet → "soon"
      { id: "settings", label: "Settings", icon: "⚙", action: "settings" },
    ] },
    { title: "Configuration", items: [
      { id: "protocols", label: "Protocols", icon: "🔌", href: "protocols.html" },
      { id: "signatures", label: "Signatures", icon: "🧬", href: "signatures.html" },
    ] },
    { title: "Diagnostics", items: [
      { id: "logs", label: "Logs", icon: "📜", href: "logs.html" },
    ] },
  ];

  const page = document.body.dataset.page || "";

  // An item with no href and no action has no page yet — it shows as "soon" and is
  // hidden by default (operators can reveal it from Settings to preview it).
  const isSoon = (it) => !it.href && !it.action;

  // Collapsed state (remembered across pages) — applied before build to avoid a flash.
  const COLLAPSE_KEY = "runway.rail.collapsed";
  let collapsed = false;
  try { collapsed = localStorage.getItem(COLLAPSE_KEY) === "1"; } catch (e) { /* ignore */ }
  document.body.classList.toggle("rail-collapsed", collapsed);

  // ---- feature visibility (Settings) -------------------------------------
  // prefs maps item id -> shown(bool). Absent = use the default (released shown,
  // unreleased hidden). Persisted so the operator's choices survive reloads.
  const FEAT_KEY = "runway.features";
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(FEAT_KEY) || "{}") || {}; } catch (e) { prefs = {}; }
  const visible = (it) =>
    Object.prototype.hasOwnProperty.call(prefs, it.id) ? prefs[it.id] !== false : !isSoon(it);
  function savePrefs() {
    try { localStorage.setItem(FEAT_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ }
  }

  // ---- data mode (auto: live / demo / idle) ------------------------------
  const MODES = {
    live: { label: "Live", title: "Live capture running — a TCP listener / QC session is active" },
    demo: { label: "Demo", title: "Demo running — a scripted file replay is active" },
    idle: { label: "Idle", title: "Idle — no replay, listener, or QC session running" },
  };
  let mode = "idle";
  function applyMode() {
    ["live", "demo", "idle"].forEach((m) => document.body.classList.toggle("mode-" + m, m === mode));
    const info = MODES[mode] || MODES.idle;
    const label = document.getElementById("mode-label");
    const badge = document.getElementById("mode-badge");
    if (label) label.textContent = info.label;
    if (badge) badge.title = info.title;
  }
  applyMode();

  // ---- top banner --------------------------------------------------------
  const topbar = document.createElement("div");
  topbar.id = "topbar";
  topbar.innerHTML =
    '<button class="topbar-toggle" title="Collapse / expand sidebar">☰</button>' +
    '<div class="topbar-brand"><span class="topbar-logo">R</span>' +
    '<span class="topbar-name">Runway</span></div>' +
    '<div class="topbar-page" id="topbar-page"></div>' +     // page title + subtitle hoisted here
    '<span class="topbar-spacer"></span>' +
    '<span class="topbar-ctl" id="topbar-ctl"></span>' +     // page header controls hoisted here
    '<button class="topbar-gear" id="settings-btn" title="Settings — show / hide features">⚙</button>' +
    '<span class="topbar-status" id="mode-badge">' +
    '<span class="dot" id="rail-dot"></span>' +
    '<span id="mode-label">Idle</span></span>';
  topbar.querySelector(".topbar-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    document.body.classList.toggle("rail-collapsed", collapsed);
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
  });
  topbar.querySelector("#settings-btn").addEventListener("click", openSettings);

  // ---- left rail: scrollable nav + a pinned foot (version lives here now) -
  const rail = document.createElement("aside");
  rail.id = "rail";
  const railNav = document.createElement("div");
  railNav.id = "rail-nav";
  const railFoot = document.createElement("div");
  railFoot.className = "rail-foot";
  railFoot.innerHTML = '<span class="topbar-version" id="rail-version"></span>';
  rail.appendChild(railNav);
  rail.appendChild(railFoot);

  // Rebuilt whenever feature visibility changes (nav only — the foot is left intact).
  function buildRail() {
    railNav.innerHTML = "";
    SECTIONS.forEach((sec) => {
      const items = sec.items.filter(visible);
      if (!items.length) return;                       // drop sections left empty
      const h = document.createElement("div");
      h.className = "rail-section";
      h.textContent = sec.title;
      railNav.appendChild(h);
      items.forEach((it) => {
        const soon = isSoon(it);
        const a = document.createElement("a");
        a.className = "rail-item" + (it.id === page ? " active" : "") + (soon ? " disabled" : "");
        a.title = it.label;                            // tooltip when collapsed
        if (it.href && !soon) a.href = it.href;
        a.innerHTML = `<span class="rail-ico">${it.icon}</span><span>${it.label}</span>` +
          (soon ? '<span class="rail-soon">soon</span>' : "");
        if (it.action === "settings") {
          a.addEventListener("click", (e) => { e.preventDefault(); openSettings(); });
        }
        railNav.appendChild(a);
      });
    });
  }
  buildRail();

  // ---- assemble: body ▸ topbar + app(rail | content) ---------------------
  const content = document.getElementById("content");
  const app = document.createElement("div");
  app.id = "app";
  app.appendChild(rail);
  if (content) app.appendChild(content);     // move existing page content beside the rail
  document.body.prepend(app);
  document.body.prepend(topbar);
  applyMode();                                 // set the label now the topbar exists

  // Fold the page's own <header> up into the topbar — one bar instead of two (saves a
  // whole row). Title + subtitle sit beside the brand; any header controls (pills) move
  // to the right, before the gear/badge. Nodes are moved (not recreated) so their ids
  // and listeners survive and page JS still finds them.
  const hdr = content ? content.querySelector(":scope > header") : null;
  if (hdr) {
    const pageSlot = topbar.querySelector("#topbar-page");
    const ctlSlot = topbar.querySelector("#topbar-ctl");
    const h1 = hdr.querySelector("h1");
    const tag = hdr.querySelector(".tag");
    if (h1) pageSlot.appendChild(h1);
    if (tag) pageSlot.appendChild(tag);
    Array.from(hdr.children).forEach((c) => {          // leftovers = spacer + pills/buttons
      if (c.classList.contains("spacer")) return;      // topbar has its own spacer
      ctlSlot.appendChild(c);
    });
    hdr.remove();
  }

  // ---- settings modal (show / hide features) -----------------------------
  function escClose(e) { if (e.key === "Escape") closeSettings(); }
  function closeSettings() {
    const o = document.getElementById("settings-overlay");
    if (o) o.remove();
    document.removeEventListener("keydown", escClose);
  }
  function openSettings() {
    closeSettings();
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "settings-overlay";

    const modal = document.createElement("div");
    modal.className = "modal";

    const head = document.createElement("div");
    head.className = "modal-head";
    head.innerHTML = '<h2>Settings</h2><span class="spacer"></span>' +
      '<button class="modal-x" title="Close">×</button>';
    head.querySelector(".modal-x").addEventListener("click", closeSettings);

    const body = document.createElement("div");
    body.className = "modal-body";
    const sub = document.createElement("p");
    sub.className = "sub";
    sub.textContent = "Show or hide sidebar features. Items marked “soon” have no page yet — " +
      "turn them on only to preview. Changes are saved on this device.";
    body.appendChild(sub);

    SECTIONS.forEach((sec) => {
      const toggleable = sec.items.filter((it) => it.action !== "settings");   // keep Settings reachable
      if (!toggleable.length) return;
      const g = document.createElement("div");
      g.className = "feat-group";
      g.textContent = sec.title;
      body.appendChild(g);
      toggleable.forEach((it) => {
        const row = document.createElement("div");
        row.className = "feat-row";
        row.innerHTML = `<span class="feat-ico">${it.icon}</span>` +
          `<span class="feat-label">${it.label}` +
          (isSoon(it) ? '<span class="rail-soon">soon</span>' : "") + "</span>";
        const sw = document.createElement("label");
        sw.className = "switch";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = visible(it);
        cb.addEventListener("change", () => {
          prefs[it.id] = cb.checked;
          savePrefs();
          buildRail();
        });
        const slider = document.createElement("span");
        slider.className = "slider";
        sw.appendChild(cb);
        sw.appendChild(slider);
        row.appendChild(sw);
        body.appendChild(row);
      });
    });

    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const reset = document.createElement("button");
    reset.className = "ghost mini";
    reset.textContent = "Reset to defaults";
    reset.addEventListener("click", () => { prefs = {}; savePrefs(); buildRail(); openSettings(); });
    const done = document.createElement("button");
    done.className = "mini";
    done.textContent = "Done";
    done.addEventListener("click", closeSettings);
    foot.appendChild(reset);
    foot.appendChild(document.createElement("span")).className = "spacer";
    foot.appendChild(done);

    modal.appendChild(head);
    modal.appendChild(body);
    modal.appendChild(foot);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeSettings(); });
    document.addEventListener("keydown", escClose);
  }

  // ---- version + liveness + data mode from the API (polled) --------------
  function refreshStatus() {
    fetch("/api/health").then((r) => r.json()).then((d) => {
      document.body.classList.remove("api-offline");
      const v = document.getElementById("rail-version");
      if (v && d.version) v.textContent = "v" + d.version;
      if (d.mode && d.mode !== mode) { mode = d.mode; applyMode(); }
    }).catch(() => {
      document.body.classList.add("api-offline");      // dot goes red; label keeps last mode
    });
  }
  refreshStatus();
  setInterval(refreshStatus, 4000);
})();
