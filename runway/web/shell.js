/* Runway app shell — the frame every page shares, in BluePulse's shape.
 *
 *   body ─ #app (row) ─ #rail  (brand + version, then the nav — folds to a 58px icon rail)
 *                     └ #main-col (column) ─ #topbar   (☰ · page title · page controls · chips)
 *                                          ├ #env-warn (server/page mismatch, missing deps)
 *                                          └ #content  (the page)
 *
 * Also the shared UI pieces every page uses, as window.RunwayUI:
 *   icons / icon(name) / paint(root)   one line-icon family (Feather style, currentColor);
 *                                      `<span data-icon="name">` is painted on load
 *   setLabel(button, icon, text)       an icon and a word on a button (no emoji, no text arrows)
 *   foldable(pane, opts)               a side pane that folds to a strip and remembers it
 * and the hold-tip: any element with data-tip="…" shows it after the pointer rests 5 s.
 *
 * Two things worth knowing:
 *   - The data-mode chip is AUTOMATIC: it polls /api/health and shows Live (a listener/QC session
 *     runs), Demo (a scripted replay runs) or Idle. Live wins when both are active.
 *   - Settings lets the operator show/hide rail items. Items with no page yet ("Soon") are hidden
 *     by default; the choice persists in localStorage.
 */
(() => {
  "use strict";

  // ---- theme (light / dark) — set on <html> at once to avoid a flash, remembered per device ----
  const THEME_KEY = "runway.theme";
  let theme = "dark";
  try { theme = localStorage.getItem(THEME_KEY) || "dark"; } catch (e) { /* private mode */ }
  document.documentElement.setAttribute("data-theme", theme);

  // ---- icons: one consistent line family, sized by the control around it ----------------------
  const svg = (body) =>
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    body + "</svg>";
  const ICONS = {
    // pages
    "dashboard": svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
    "parser": svg('<path d="M8 6 3 12l5 6"/><path d="M16 6l5 6-5 6"/>'),
    "imu": svg('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
    "archive": svg('<path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/>'),
    "converter": svg('<path d="M7 4 3 8l4 4"/><path d="M3 8h13"/><path d="M17 12l4 4-4 4"/><path d="M21 16H8"/>'),
    "tcp": svg('<path d="M4.9 16.1a9 9 0 0 1 0-8.2"/><path d="M7.8 13.3a5 5 0 0 1 0-2.6"/><path d="M16.2 10.7a5 5 0 0 1 0 2.6"/><path d="M19.1 7.9a9 9 0 0 1 0 8.2"/><circle cx="12" cy="12" r="1.6"/>'),
    "ota": svg('<path d="M12 21v-8"/><path d="m8.5 16.5 3.5-3.5 3.5 3.5"/><path d="M20 17.6A4.5 4.5 0 0 0 17.5 9H16A6.5 6.5 0 1 0 5 14.6"/>'),
    "qc": svg('<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5 11 15l4.5-5"/>'),
    "captures": svg('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>'),
    "profiles": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16.5c.7-1.7 5.7-1.7 6.4 0"/><path d="M15 10h3"/><path d="M15 13.5h3"/>'),
    "settings": svg('<path d="M4 7h8"/><path d="M16 7h4"/><circle cx="14" cy="7" r="2"/><path d="M4 17h4"/><path d="M12 17h8"/><circle cx="8" cy="17" r="2"/>'),
    "protocols": svg('<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>'),
    "signatures": svg('<path d="M2 12A10 10 0 0 1 18 4"/><path d="M5 19.5C5.6 18 6 15.5 6 12c0-.7.1-1.4.3-2"/><path d="M12 10a2 2 0 0 0-2 2c0 2-.2 4-.7 6"/><path d="M14 12c0 3 0 6-1 9"/><path d="M9 6.8A6 6 0 0 1 18 12c0 .7 0 1.3-.1 2"/>'),
    "logs": svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h5"/>'),
    // frame
    "sidebar": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
    "sun": svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    "moon": svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>'),
    "panel-left": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m16 10-2 2 2 2"/>'),
    "panel-open": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m14 10 2 2-2 2"/>'),
    "panel-right": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/><path d="m8 10 2 2-2 2"/>'),
    "panel-right-open": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/><path d="m10 10-2 2 2 2"/>'),
    // actions
    "plus": svg('<path d="M12 5v14"/><path d="M5 12h14"/>'),
    "x": svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
    "edit": svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
    "trash": svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m19 6-1 14H6L5 6"/>'),
    "copy": svg('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
    "terminal": svg('<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>'),
    "send": svg('<path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/>'),
    "lock": svg('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
    "unlock": svg('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.6-1.7"/>'),
    "refresh": svg('<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>'),
    "eye": svg('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'),
    "tick": svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
    "key": svg('<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3"/><path d="m17 6 3 3"/><path d="m14 9 2 2"/>'),
    "template": svg('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>'),
    "format": svg('<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1"/><path d="M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>'),
    "history": svg('<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/>'),
    "device": svg('<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>'),
    "alert": svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16h.01"/>'),
    "check": svg('<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>'),
    "upload": svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>'),
    "download": svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>'),
    "search": svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    "play": svg('<path d="M7 4.5v15l12-7.5Z"/>'),
    "stop": svg('<rect x="6" y="6" width="12" height="12" rx="1.5"/>'),
    "record": svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.2"/>'),
    "columns": svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="M15 4v16"/>'),
    "chevron-down": svg('<path d="m6 9 6 6 6-6"/>'),
    "chevron-right": svg('<path d="m9 6 6 6-6 6"/>'),
    "info": svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/>'),
    "relay": svg('<path d="M7 17 17 7"/><path d="M8 7h9v9"/>'),
    "store": svg('<ellipse cx="12" cy="6" rx="7" ry="2.6"/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>'),
    "clock": svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "repeat": svg('<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>'),
    "zoom-out": svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/><path d="M8 11h6"/>'),
    "clipboard": svg('<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1"/><path d="M9 10h6"/><path d="M9 14h6"/><path d="M9 18h3"/>'),
    "file": svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>'),
    "map": svg('<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2Z"/><path d="M9 4v14"/><path d="M15 6v14"/>'),
    "replay": svg('<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="m10.5 9 4.5 3-4.5 3Z"/>'),
    "more": svg('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
  };
  const BRAND = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 19h6"/><path d="M6.5 15.5 18.5 6"/><path d="M13.5 6h5v5"/></svg>';

  const icon = (name) => '<span class="ui-ico">' + (ICONS[name] || "") + "</span>";
  function paint(root) {
    (root || document).querySelectorAll("[data-icon]").forEach((n) => {
      if (n.dataset.painted) return;
      n.innerHTML = ICONS[n.dataset.icon] || "";
      n.classList.add("ui-ico");
      n.dataset.painted = "1";
    });
  }
  // An icon and a word on a button: the word is text, never parsed as HTML.
  function setLabel(btn, name, text) {
    btn.innerHTML = name ? icon(name) : "";
    if (text != null && text !== "") btn.appendChild(document.createTextNode(text));
    return btn;
  }

  const SECTIONS = [
    { title: "Overview", items: [
      { id: "dashboard", label: "Dashboard", icon: "dashboard", href: "index.html" },
    ] },
    { title: "Tools", items: [
      { id: "parser", label: "Packet parser", icon: "parser", href: "parser.html" },
      { id: "converter", label: "Converter", icon: "converter", href: "converter.html" },
      { id: "ota", label: "Device OTA", icon: "ota", href: "ota.html" },   // act as a TCU vs the OTA cloud
    ] },
    { title: "Analysis", items: [
      { id: "imu", label: "IMU", icon: "imu", href: "imu.html" },
    ] },
    { title: "Capture", items: [
      { id: "capture", label: "Capture", icon: "tcp", href: "capture.html" },   // Testing + QC (per-tab)
      { id: "archive", label: "Archive", icon: "archive", href: "archive.html" },  // stored packets, replay
      { id: "captures", label: "Captures", icon: "captures" },        // no page yet → "Soon"
    ] },
    { title: "Library", items: [
      { id: "profiles", label: "Profiles", icon: "profiles" },        // no page yet → "Soon"
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
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // An item with no href and no action has no page yet — "Soon", hidden by default (Settings
  // can reveal it to preview the shape of the app).
  const isSoon = (it) => !it.href && !it.action;

  // Folded rail, remembered across pages. Under 1000px it is always a rail (the toggle hides).
  const COLLAPSE_KEY = "runway.rail.collapsed";
  let collapsed = false;
  try { collapsed = localStorage.getItem(COLLAPSE_KEY) === "1"; } catch (e) { /* ignore */ }
  const narrow = window.matchMedia("(max-width: 1000px)");
  const applyRail = () => document.body.classList.toggle("rail-collapsed", collapsed || narrow.matches);
  applyRail();
  narrow.addEventListener("change", applyRail);

  // ---- feature visibility (Settings) ----------------------------------------------------------
  const FEAT_KEY = "runway.features";
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(FEAT_KEY) || "{}") || {}; } catch (e) { prefs = {}; }
  const visible = (it) =>
    Object.prototype.hasOwnProperty.call(prefs, it.id) ? prefs[it.id] !== false : !isSoon(it);
  function savePrefs() {
    try { localStorage.setItem(FEAT_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ }
  }

  // ---- data mode (auto: live / demo / idle; offline when the API is unreachable) ---------------
  const MODES = {
    live: { label: "Live", tip: "A listener or QC session is running" },
    demo: { label: "Demo", tip: "A scripted file replay is running" },
    idle: { label: "Idle", tip: "No replay, listener or QC session is running" },
  };
  let mode = "idle";
  let offline = false;
  function applyMode() {
    ["live", "demo", "idle"].forEach((m) => document.body.classList.toggle("mode-" + m, m === mode));
    const info = MODES[mode] || MODES.idle;
    const label = document.getElementById("mode-label");
    const chip = document.getElementById("mode-badge");
    if (label) label.textContent = offline ? "Offline" : info.label;
    if (chip) chip.title = offline ? "The Runway server is not answering" : info.tip;
  }

  // ---- the rail: brand + version, then the nav --------------------------------------------------
  const rail = document.createElement("aside");
  rail.id = "rail";
  rail.innerHTML =
    '<div class="rail-brand">' +
      '<span class="rail-logo">' + BRAND + "</span>" +
      '<span class="rail-brand-txt"><span class="rail-name">Run<b>way</b></span>' +
      '<span class="rail-version" id="rail-version"></span></span>' +
    "</div>" +
    '<div id="rail-nav"></div>';
  const railNav = rail.querySelector("#rail-nav");

  function buildRail() {
    railNav.innerHTML = "";
    SECTIONS.forEach((sec) => {
      const items = sec.items.filter(visible);
      if (!items.length) return;
      const h = document.createElement("div");
      h.className = "rail-section";
      h.innerHTML = '<span class="rail-section-t">' + esc(sec.title) + '</span><span class="rail-section-rule"></span>';
      railNav.appendChild(h);
      items.forEach((it) => {
        const soon = isSoon(it);
        const a = document.createElement("a");
        a.className = "rail-item" + (it.id === page ? " active" : "") + (soon ? " disabled" : "");
        a.title = sec.title + " · " + it.label;
        if (it.id === page) a.setAttribute("aria-current", "page");
        if (it.href && !soon) a.href = it.href;
        a.innerHTML = '<span class="rail-ico">' + (ICONS[it.icon] || "") + "</span>" +
          '<span class="rail-label">' + esc(it.label) + "</span>" +
          (soon ? '<span class="rail-soon" title="Not built yet">Soon</span>' : "");
        if (it.action === "settings") {
          a.href = "#";
          a.addEventListener("click", (e) => { e.preventDefault(); openSettings(); });
        }
        railNav.appendChild(a);
      });
    });
  }
  buildRail();

  // ---- the top bar: fold · page title (its description is a hold-tip) · controls · chips ------
  const topbar = document.createElement("div");
  topbar.id = "topbar";
  topbar.innerHTML =
    '<button class="topbar-btn topbar-toggle" type="button" title="Fold the sidebar" aria-label="Fold the sidebar">' + ICONS.sidebar + "</button>" +
    '<div class="topbar-page" id="topbar-page"></div>' +
    '<span class="topbar-spacer"></span>' +
    '<span class="topbar-ctl" id="topbar-ctl"></span>' +
    '<button class="ui-chip update-chip" id="update-chip" type="button" hidden></button>' +   // appctl.js
    '<span class="ui-chip topbar-status" id="mode-badge"><span class="ui-chip-dot"></span><span id="mode-label">Idle</span></span>' +
    '<button class="topbar-btn" id="theme-btn" type="button"></button>';
  topbar.querySelector(".topbar-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    applyRail();
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
    // maps and canvases re-measure once the rail has finished moving
    setTimeout(() => window.dispatchEvent(new Event("resize")), 230);
  });
  topbar.querySelector("#theme-btn").addEventListener("click", () => {
    theme = theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
    applyTheme();
  });
  // the sun in dark mode (click → light), the moon in light mode (click → dark)
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    const b = document.getElementById("theme-btn");
    if (b) {
      b.innerHTML = theme === "dark" ? ICONS.sun : ICONS.moon;
      b.title = theme === "dark" ? "Light theme" : "Dark theme";
      b.setAttribute("aria-label", b.title);
    }
  }

  // ---- assemble ---------------------------------------------------------------------------------
  const content = document.getElementById("content");
  const envWarn = document.createElement("div");
  envWarn.id = "env-warn";
  envWarn.className = "ui-notice warn";
  envWarn.hidden = true;
  const mainCol = document.createElement("div");
  mainCol.id = "main-col";
  mainCol.append(topbar, envWarn);
  if (content) mainCol.appendChild(content);
  const app = document.createElement("div");
  app.id = "app";
  app.append(rail, mainCol);
  document.body.prepend(app);
  applyMode();
  applyTheme();

  // The page's own <header> folds up into the top bar: its <h1> is the title, its .tag the
  // title's hold-tip (a description is never printed — it is help text), and anything else it
  // holds (pills, buttons) moves to the right, before the chips. Nodes are moved, not recreated,
  // so their ids and listeners survive.
  const hdr = content ? content.querySelector(":scope > header") : null;
  if (hdr) {
    const pageSlot = topbar.querySelector("#topbar-page");
    const ctlSlot = topbar.querySelector("#topbar-ctl");
    const h1 = hdr.querySelector("h1");
    const tag = hdr.querySelector(".tag");
    if (h1) pageSlot.appendChild(h1);
    if (tag && tag.textContent.trim()) pageSlot.dataset.tip = tag.textContent.trim();
    if (tag) tag.remove();
    Array.from(hdr.children).forEach((c) => {
      if (c.classList.contains("spacer")) return;
      ctlSlot.appendChild(c);
    });
    hdr.remove();
  }
  paint(document);

  // ---- hold-tips: help for whoever lingers, invisible to everyone else ---------------------------
  // The pointer rests on an element with data-tip for HOLD_MS (the user's figure, as in BluePulse
  // and InTouch) and the tip appears beside it; moving away, a click, a scroll or Esc hides it.
  const HOLD_MS = 5000;
  let tipFor = null, tipTimer = 0, tipEl = null;
  function hideTip() {
    clearTimeout(tipTimer);
    tipFor = null;
    if (tipEl) tipEl.hidden = true;
  }
  function showTip(target) {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.className = "ui-holdtip";
      tipEl.setAttribute("role", "tooltip");
      document.body.appendChild(tipEl);
    }
    tipEl.textContent = target.dataset.tip;
    tipEl.hidden = false;
    const r = target.getBoundingClientRect();
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    let left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
    tipEl.style.left = left + "px";
    tipEl.style.top = top + "px";
  }
  document.addEventListener("pointerover", (e) => {
    const t = e.target && e.target.closest ? e.target.closest("[data-tip]") : null;
    if (t === tipFor) return;
    hideTip();
    if (!t || !t.dataset.tip) return;
    tipFor = t;
    tipTimer = setTimeout(() => { if (tipFor === t) showTip(t); }, HOLD_MS);
  });
  document.addEventListener("pointerdown", hideTip, true);
  document.addEventListener("scroll", hideTip, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });

  // ---- foldable side panes -----------------------------------------------------------------------
  // A pane marked data-fold="key" (or handed to foldable()) folds to a narrow strip and remembers
  // it per key. Its fold button is the [data-fold-toggle] inside it; folded, the strip shows the
  // unfold button and the pane's name. Under 1000px a pane with no saved choice starts folded and,
  // opened, lies over the page instead of squeezing it. Panes sharing a key fold together (one per
  // Parser tab).
  const foldGroups = new Map();
  const foldKey = (k) => "runway.fold." + k;
  function foldedFor(key) {
    let v = null;
    try { v = localStorage.getItem(foldKey(key)); } catch (e) { /* ignore */ }
    return v === null ? narrow.matches : v === "1";
  }
  function paintFold(pane, folded) {
    const side = pane.dataset.foldSide || "left";
    pane.classList.toggle("is-folded", folded);
    const btn = pane.querySelector("[data-fold-toggle]");
    if (btn) {
      btn.innerHTML = ICONS[side === "left" ? "panel-left" : "panel-right"];
      btn.title = "Fold " + (pane.dataset.foldTitle || "the panel").toLowerCase();
      btn.setAttribute("aria-label", btn.title);
    }
  }
  function setFolded(key, folded) {
    try { localStorage.setItem(foldKey(key), folded ? "1" : "0"); } catch (e) { /* ignore */ }
    (foldGroups.get(key) || []).forEach((p) => paintFold(p, folded));
    // maps and canvases re-measure — now, and once the column has finished moving
    window.dispatchEvent(new Event("resize"));
    setTimeout(() => window.dispatchEvent(new Event("resize")), 230);
  }
  function foldable(pane, opts) {
    if (!pane || pane.dataset.foldReady) return;
    const o = opts || {};
    const key = o.key || pane.dataset.fold;
    const side = o.side || pane.dataset.foldSide || "left";
    const title = o.title || pane.dataset.foldTitle || "Panel";
    pane.dataset.fold = key; pane.dataset.foldSide = side; pane.dataset.foldTitle = title;
    pane.dataset.foldReady = "1";
    pane.classList.add("ui-foldable", "fold-" + side);
    const strip = document.createElement("div");
    strip.className = "ui-fold-strip";
    strip.innerHTML =
      '<button type="button" class="ui-icon-btn" title="Show ' + esc(title.toLowerCase()) + '" aria-label="Show ' + esc(title.toLowerCase()) + '">' +
      ICONS[side === "left" ? "panel-open" : "panel-right-open"] + "</button>" +
      '<span class="ui-fold-name">' + esc(title) + "</span>";
    strip.addEventListener("click", () => setFolded(key, false));
    pane.appendChild(strip);
    const btn = pane.querySelector("[data-fold-toggle]");
    if (btn) btn.addEventListener("click", () => setFolded(key, true));
    if (!foldGroups.has(key)) foldGroups.set(key, new Set());
    foldGroups.get(key).add(pane);
    paintFold(pane, foldedFor(key));
  }
  document.querySelectorAll("[data-fold]").forEach((p) => foldable(p));

  // ---- the house confirm / prompt: a <dialog>, never window.confirm (unstyled, shows the host,
  // and the browser lets people suppress it). A title, one sentence, Cancel focused, the action on
  // the right — red only when it destroys. User strings are set as text, never parsed.
  function ask(o) {
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "ui-dlg";
      d.innerHTML =
        '<form method="dialog"><div class="ui-dlg-head"><h2></h2></div>' +
        '<div class="ui-dlg-body"><p class="ui-dlg-text"></p></div>' +
        '<div class="ui-dlg-foot"><span class="spacer"></span>' +
        '<button type="button" class="ghost" data-cancel>Cancel</button>' +
        '<button type="submit" data-ok></button></div></form>';
      d.querySelector("h2").textContent = o.title || "Are you sure?";
      const p = d.querySelector(".ui-dlg-text");
      if (o.text) p.textContent = o.text; else p.remove();
      let field = null;
      if (o.input) {
        const lab = document.createElement("label");
        lab.className = "ui-dlg-field";
        lab.textContent = o.input.label || "";
        field = document.createElement("input");
        field.value = o.input.value || "";
        if (o.input.placeholder) field.placeholder = o.input.placeholder;
        field.spellcheck = false;
        lab.appendChild(field);
        d.querySelector(".ui-dlg-body").appendChild(lab);
      }
      const ok = d.querySelector("[data-ok]");
      ok.textContent = o.action || "OK";
      if (o.tone === "danger") ok.className = "danger-fill";
      let result = null;
      d.querySelector("[data-cancel]").addEventListener("click", () => d.close());
      d.querySelector("form").addEventListener("submit", (e) => {
        e.preventDefault();
        result = field ? field.value : true;
        d.close();
      });
      d.addEventListener("close", () => {
        d.remove();
        resolve(result === null ? (field ? null : false) : result);
      });
      document.body.appendChild(d);
      d.showModal();
      if (field) { field.focus(); field.select(); } else d.querySelector("[data-cancel]").focus();
    });
  }
  const confirmDlg = (o) => ask(o);                                  // → true / false
  const promptDlg = (o) => ask(Object.assign({}, o, { input: { label: o.label, value: o.value, placeholder: o.placeholder } }));  // → text / null

  // Sections other scripts add to Settings, above the sidebar switches (appctl.js: Usage reporting,
  // Updates). Each is a function that fills the element it is given.
  const settingsSections = [];

  window.RunwayUI = { icons: ICONS, icon, paint, setLabel, foldable, esc, confirm: confirmDlg, prompt: promptDlg,
                      settingsSections, openSettings: () => openSettings() };

  // ---- settings (show / hide rail items) -------------------------------------------------------
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
    modal.innerHTML =
      '<div class="modal-head"><h2 data-tip="Usage reporting, updates, and which items the sidebar shows">Settings</h2>' +
      '<span class="spacer"></span><button class="ui-icon-btn modal-x" type="button" aria-label="Close">' + ICONS.x + "</button></div>" +
      '<div class="modal-body"></div>' +
      '<div class="modal-foot"><button class="ghost" type="button" data-reset>Reset to defaults</button>' +
      '<span class="spacer"></span><button type="button" data-done>Done</button></div>';
    modal.querySelector(".modal-x").addEventListener("click", closeSettings);
    modal.querySelector("[data-done]").addEventListener("click", closeSettings);
    modal.querySelector("[data-reset]").addEventListener("click", () => {
      prefs = {}; savePrefs(); buildRail(); openSettings();
    });
    const body = modal.querySelector(".modal-body");
    settingsSections.forEach((fill) => {
      const sec = document.createElement("div");
      sec.className = "set-sec";
      body.appendChild(sec);
      try { fill(sec); } catch (e) { sec.remove(); }
    });
    if (settingsSections.length) {
      const h = document.createElement("div");
      h.className = "set-h";
      h.innerHTML = '<span class="set-title" data-tip="Show or hide items in the sidebar. Items marked Soon have no page yet.">Sidebar</span>';
      body.appendChild(h);
    }
    SECTIONS.forEach((sec) => {
      const toggleable = sec.items.filter((it) => it.action !== "settings");   // keep Settings reachable
      if (!toggleable.length) return;
      const g = document.createElement("div");
      g.className = "feat-group";
      g.textContent = sec.title;
      body.appendChild(g);
      toggleable.forEach((it) => {
        const row = document.createElement("label");
        row.className = "feat-row";
        row.innerHTML = '<span class="feat-ico">' + (ICONS[it.icon] || "") + "</span>" +
          '<span class="feat-label">' + esc(it.label) +
          (isSoon(it) ? '<span class="rail-soon">Soon</span>' : "") + "</span>" +
          '<span class="switch"><input type="checkbox" /><span class="slider"></span></span>';
        const cb = row.querySelector("input");
        cb.checked = visible(it);
        cb.addEventListener("change", () => { prefs[it.id] = cb.checked; savePrefs(); buildRail(); });
        body.appendChild(row);
      });
    });
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeSettings(); });
    document.addEventListener("keydown", escClose);
  }

  // ---- favicon: the brand mark on an accent tile ------------------------------------------------
  (function setFavicon() {
    const mark =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' +
      '<rect width="24" height="24" rx="6" fill="#3ecf8e"/>' +
      '<g fill="none" stroke="#0b1a12" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M4 19h6"/><path d="M6.5 15.5 18.5 6"/><path d="M13.5 6h5v5"/></g></svg>';
    let link = document.querySelector('link[rel="icon"]');
    if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
    link.type = "image/svg+xml";
    link.href = "data:image/svg+xml," + encodeURIComponent(mark);
  })();

  // ---- environment notice: server/page mismatch + missing dependencies --------------------------
  // Static files are read from disk on every load, but API routes live in the server PROCESS —
  // after a code update the two can silently diverge. Same for a missing optional dependency
  // (openpyxl). Say both up top. This page's version is the code that was on disk when it loaded
  // (/api/health's code_version from the first answer), so there is no literal here to bump.
  let UI_VERSION = null;
  const cmpVersion = (a, b) => {           // 1.1.9 vs 1.1.24: part by part, as numbers
    const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const diff = (pa[i] || 0) - (pb[i] || 0);
      if (diff) return diff;
    }
    return 0;
  };

  function refreshStatus() {
    fetch("/api/health").then((r) => r.json()).then((d) => {
      document.body.classList.remove("api-offline");
      offline = false;
      if (UI_VERSION === null) UI_VERSION = d.code_version || d.version || null;
      const v = document.getElementById("rail-version");
      if (v && d.version) {
        v.textContent = "Version " + d.version;
        v.title = "Server " + d.version + (d.code_version && d.code_version !== d.version
          ? " · code on disk " + d.code_version : "");
      }
      if (d.mode && d.mode !== mode) mode = d.mode;
      applyMode();
      const warns = [];
      if (d.version && UI_VERSION && d.version !== UI_VERSION) {
        // The rail shows the SERVER's version: a page older than the server is a cached tab
        // (reload); a page newer than the server means the process runs the previous code.
        warns.push(cmpVersion(d.version, UI_VERSION) > 0
          ? `This page is ${UI_VERSION}, the server ${d.version}. Reload the page (Ctrl+Shift+R).`
          : `The server is ${d.version}, this page ${UI_VERSION}. Restart the server to load the new code.`);
      }
      if (d.deps && d.deps.openpyxl === false) {
        warns.push("openpyxl is missing from the server's Python, so the Excel QC report cannot be built (CSV works). pip install openpyxl");
      }
      envWarn.innerHTML = warns.length ? icon("alert") + '<span class="ui-notice-t"></span>' : "";
      if (warns.length) envWarn.querySelector(".ui-notice-t").textContent = warns.join("  ·  ");
      envWarn.hidden = !warns.length;
    }).catch(() => {
      document.body.classList.add("api-offline");
      offline = true;
      applyMode();
    });
  }
  refreshStatus();
  setInterval(refreshStatus, 4000);

  // Usage reporting and updates (the first-start welcome, the greeting, the update offer and chip,
  // their Settings sections) — on every page, without each page loading it.
  const appctl = document.createElement("script");
  appctl.src = "appctl.js";
  document.body.appendChild(appctl);
})();
