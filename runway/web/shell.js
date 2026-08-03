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

  // ---- theme (light / dark) — set on <html> ASAP to avoid a flash, remembered
  // per device. Default dark (the CSS :root is dark; light overrides under
  // :root[data-theme="light"]).
  const THEME_KEY = "runway.theme";
  let theme = "dark";
  try { theme = localStorage.getItem(THEME_KEY) || "dark"; } catch (e) { /* ignore */ }
  document.documentElement.setAttribute("data-theme", theme);

  // ---- icon set: one consistent line-icon family (Feather/Lucide style) ---
  // Monochrome, 1.75px stroke, currentColor so each adopts its context's colour.
  // Emoji were replaced by these so the whole UI reads as one designed system.
  const svg = (body) =>
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    body + "</svg>";
  const ICONS = {
    dashboard: svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
    parser: svg('<path d="M8 6 3 12l5 6"/><path d="M16 6l5 6-5 6"/>'),
    converter: svg('<path d="M7 4 3 8l4 4"/><path d="M3 8h13"/><path d="M17 12l4 4-4 4"/><path d="M21 16H8"/>'),
    tcp: svg('<path d="M4.9 16.1a9 9 0 0 1 0-8.2"/><path d="M7.8 13.3a5 5 0 0 1 0-2.6"/><path d="M16.2 10.7a5 5 0 0 1 0 2.6"/><path d="M19.1 7.9a9 9 0 0 1 0 8.2"/><circle cx="12" cy="12" r="1.6"/>'),
    qc: svg('<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5 11 15l4.5-5"/>'),
    captures: svg('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>'),
    profiles: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16.5c.6-1.6 4.4-1.6 6 0"/><path d="M14.5 10H18"/><path d="M14.5 13.5H18"/>'),
    settings: svg('<path d="M4 7h8"/><path d="M16 7h4"/><circle cx="14" cy="7" r="2"/><path d="M4 17h4"/><path d="M12 17h8"/><circle cx="8" cy="17" r="2"/>'),
    protocols: svg('<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>'),
    signatures: svg('<path d="M2 12A10 10 0 0 1 18 4"/><path d="M5 19.5C5.6 18 6 15.5 6 12c0-.7.1-1.4.3-2"/><path d="M12 10a2 2 0 0 0-2 2c0 2-.2 4-.7 6"/><path d="M14 12c0 3 0 6-1 9"/><path d="M9 6.8A6 6 0 0 1 18 12c0 .7 0 1.3-.1 2"/>'),
    logs: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h5"/>'),
    // topbar glyphs
    sidebar: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
    sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    moon: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>'),
    // brand mark — a takeoff vector rising off a runway strip ("Runway")
    brand: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M4 19h6"/><path d="M6.5 15.5 18.5 6"/><path d="M13.5 6h5v5"/></svg>',
  };

  const SECTIONS = [
    { title: "Overview", items: [
      { id: "dashboard", label: "Dashboard", icon: "dashboard", href: "index.html" },
    ] },
    { title: "Tools", items: [
      { id: "parser", label: "Packet Parser", icon: "parser", href: "parser.html" },
      { id: "converter", label: "Converter", icon: "converter", href: "converter.html" },
    ] },
    { title: "Capture", items: [
      { id: "capture", label: "Capture", icon: "tcp", href: "capture.html" },   // Testing + QC (per-tab)
      { id: "captures", label: "Captures", icon: "captures" },        // no page yet → "soon"
    ] },
    { title: "Library", items: [
      { id: "profiles", label: "Profiles", icon: "profiles" },        // no page yet → "soon"
      { id: "settings", label: "Settings", icon: "settings", action: "settings" },
    ] },
    { title: "Configuration", items: [
      { id: "protocols", label: "Protocols", icon: "protocols", href: "protocols.html" },
      { id: "signatures", label: "Signatures", icon: "signatures", href: "signatures.html" },
    ] },
    { title: "Diagnostics", items: [
      { id: "logs", label: "Logs", icon: "logs", href: "logs.html" },
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
    '<button class="topbar-toggle" title="Collapse / expand sidebar">' + ICONS.sidebar + '</button>' +
    '<div class="topbar-brand"><span class="topbar-logo">' + ICONS.brand + '</span>' +
    '<span class="topbar-name">Runway</span></div>' +
    '<div class="topbar-page" id="topbar-page"></div>' +     // page title + subtitle hoisted here
    '<span class="topbar-spacer"></span>' +
    '<span class="topbar-ctl" id="topbar-ctl"></span>' +     // page header controls hoisted here
    '<button class="topbar-gear" id="theme-btn"></button>' +
    '<button class="topbar-gear" id="settings-btn" title="Settings — show / hide features">' + ICONS.settings + '</button>' +
    '<span class="topbar-status" id="mode-badge">' +
    '<span class="dot" id="rail-dot"></span>' +
    '<span id="mode-label">Idle</span></span>';
  topbar.querySelector(".topbar-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    document.body.classList.toggle("rail-collapsed", collapsed);
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
  });
  topbar.querySelector("#settings-btn").addEventListener("click", openSettings);
  topbar.querySelector("#theme-btn").addEventListener("click", () => {
    theme = theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
    applyTheme();
  });

  // Reflect the current theme on the toggle: show the sun in dark mode (click → light),
  // the moon in light mode (click → dark).
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    const b = document.getElementById("theme-btn");
    if (b) {
      b.innerHTML = theme === "dark" ? ICONS.sun : ICONS.moon;
      b.title = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
    }
  }

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
        a.innerHTML = `<span class="rail-ico">${ICONS[it.icon] || ""}</span><span>${it.label}</span>` +
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
  applyTheme();                                // paint the theme toggle's icon

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
        row.innerHTML = `<span class="feat-ico">${ICONS[it.icon] || ""}</span>` +
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

  // ---- favicon: the brand mark on an accent tile (none was set before) ----
  (function setFavicon() {
    const mark =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' +
      '<rect width="24" height="24" rx="6" fill="#5ab0f0"/>' +
      '<g fill="none" stroke="#04121d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M4 19h6"/><path d="M6.5 15.5 18.5 6"/><path d="M13.5 6h5v5"/></g></svg>';
    let link = document.querySelector('link[rel="icon"]');
    if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
    link.type = "image/svg+xml";
    link.href = "data:image/svg+xml," + encodeURIComponent(mark);
  })();

  // ---- environment banner: server/UI mismatch + missing dependencies ------
  // Static files are re-read from disk on every load, but API routes live in the server
  // PROCESS — after a code update the two can silently diverge (new button, old API:
  // "Not Found" at the worst moment). Same for a missing optional dependency (openpyxl):
  // it used to surface only when the operator clicked Download. Say both loudly, up top.
  const UI_VERSION = "1.1.24";   // bump together with pyproject.toml / __init__.py
  const envWarn = document.createElement("div");
  envWarn.id = "env-warn";
  envWarn.style.display = "none";
  const appEl = document.getElementById("app");
  document.body.insertBefore(envWarn, appEl);

  // ---- version + liveness + data mode from the API (polled) --------------
  function refreshStatus() {
    fetch("/api/health").then((r) => r.json()).then((d) => {
      document.body.classList.remove("api-offline");
      const v = document.getElementById("rail-version");
      if (v && d.version) v.textContent = "v" + d.version;
      if (d.mode && d.mode !== mode) { mode = d.mode; applyMode(); }
      const warns = [];
      if (d.version && d.version !== UI_VERSION) {
        // Say which side is behind, and what actually fixes it. The version pill in the rail
        // shows the SERVER's version, so "restart the server" on a stale page sent the operator
        // in the wrong direction. Assets are served no-cache, so a page older than the server
        // means a cached tab (reload) — a page newer than the server means the process is still
        // running the previous code (restart).
        warns.push(d.version > UI_VERSION
          ? `This page is v${UI_VERSION} but the server is v${d.version} — reload the page (Ctrl+Shift+R) to load the updated UI.`
          : `The server is v${d.version} but this page is v${UI_VERSION} — restart the server to load the updated code.`);
      }
      if (d.deps && d.deps.openpyxl === false) {
        warns.push("openpyxl is not installed in the server's Python — the XLS QC report will fail (CSV still works). Run: pip install openpyxl");
      }
      envWarn.textContent = warns.join("  ·  ");
      envWarn.style.display = warns.length ? "" : "none";
    }).catch(() => {
      document.body.classList.add("api-offline");      // dot goes red; label keeps last mode
    });
  }
  refreshStatus();
  setInterval(refreshStatus, 4000);
})();
