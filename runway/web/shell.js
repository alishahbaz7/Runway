/* Runway app shell — the frame every page shares: BluePulse's shape, Supabase Studio's look.
 *
 *   body ─ #banner (fixed, full width: logo · Runway · version / Hello, Name / page · controls · chips)
 *        └ #app (row) ─ #rail  (the nav; a fold button at its foot — folds to a 58px icon rail)
 *                     └ #main-col (column) ─ #env-warn (server/page mismatch, missing deps)
 *                                          └ #content  (the page)
 *
 * Also the shared UI pieces every page uses, as window.RunwayUI:
 *   icons / icon(name) / paint(root)   one line-icon family (Feather style, currentColor);
 *                                      `<span data-icon="name">` is painted on load
 *   setLabel(button, icon, text)       an icon and a word on a button (no emoji, no text arrows)
 *   foldable(pane, opts)               a side pane that folds to a strip and remembers it; dragged
 *                                      by its inner edge, it is also resized (between a min and a max)
 *   resizable(target, opts)            any edge dragged to resize, remembered; declaratively with
 *                                      data-resize="key" data-resize-edge="right|left|top" …
 * and the hold-tip: any element with data-tip="…" shows it after the pointer rests 5 s. Table
 * columns are resized by dragging the edge of their header; a double-click on any edge resets it.
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
    "arrow-left": svg('<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>'),
    "arrow-right": svg('<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>'),
    "info": svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/>'),
    "relay": svg('<path d="M7 17 17 7"/><path d="M8 7h9v9"/>'),
    "store": svg('<ellipse cx="12" cy="6" rx="7" ry="2.6"/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>'),
    "clock": svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "repeat": svg('<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>'),
    "maximize": svg('<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>'),
    "minimize": svg('<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="M14 10l7-7"/><path d="M3 21l7-7"/>'),
    "zoom-out": svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/><path d="M8 11h6"/>'),
    "zoom-in": svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/><path d="M8 11h6"/><path d="M11 8v6"/>'),
    "clipboard": svg('<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1"/><path d="M9 10h6"/><path d="M9 14h6"/><path d="M9 18h3"/>'),
    "file": svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>'),
    "map": svg('<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2Z"/><path d="M9 4v14"/><path d="M15 6v14"/>'),
    "replay": svg('<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="m10.5 9 4.5 3-4.5 3Z"/>'),
    "more": svg('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
    "analysis": svg('<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="5" rx=".5"/><rect x="12" y="8" width="3" height="9" rx=".5"/><rect x="17" y="5" width="3" height="12" rx=".5"/>'),
    "gauge": svg('<path d="M12 14l3.5-3.5"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/><circle cx="12" cy="14" r="1.2"/>'),
    "route": svg('<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H17a3.5 3.5 0 0 0 0-7H7a3.5 3.5 0 0 1 0-7h8.5"/>'),
    "power": svg('<path d="M13 2 4 14h7l-1 8 9-12h-7Z"/>'),
    "satellite": svg('<path d="m13.5 6.5 4 4"/><path d="m4.5 15.5 4 4"/><rect x="8" y="8" width="8" height="8" rx="1" transform="rotate(45 12 12)"/><path d="M19 13a6 6 0 0 1-6 6"/><path d="M22 13a9 9 0 0 1-9 9"/>'),
    "chip": svg('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
    "list": svg('<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>'),
    "direction": svg('<path d="M6 5 19 12 6 19 9.5 12Z" fill="currentColor"/>'),   // a filled arrowhead (map direction)
    "user": svg('<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c1.4-3.6 4.2-5.5 7.5-5.5s6.1 1.9 7.5 5.5"/>'),
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
      { id: "firmware", label: "Firmware test", icon: "chip", href: "firmware.html" },   // a new firmware, every packet against its sheet
      { id: "converter", label: "Converter", icon: "converter", href: "converter.html" },
      { id: "ota", label: "Device OTA", icon: "ota", href: "ota.html" },   // act as a TCU vs the OTA cloud
    ] },
    { title: "Analysis", items: [
      { id: "analysis", label: "Log analysis", icon: "analysis", href: "analysis.html" },   // platform reports, judged
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

  // The sidebar's mode, as Supabase's "Sidebar control": Expanded, Collapsed (the icon rail) or
  // Expand on hover (the icon rail, opening over the page while the pointer is on it). Remembered
  // across pages. Under 1000px it always behaves as Expand on hover.
  const RAIL_KEY = "runway.rail.mode";
  const RAIL_MODES = [
    { id: "expanded", label: "Expanded" },
    { id: "collapsed", label: "Collapsed" },
    { id: "hover", label: "Expand on hover" },
  ];
  let railMode = "expanded";
  try {
    railMode = localStorage.getItem(RAIL_KEY) ||
      (localStorage.getItem("runway.rail.collapsed") === "1" ? "collapsed" : "expanded");   // the old switch
  } catch (e) { /* private mode */ }
  if (!RAIL_MODES.some((m) => m.id === railMode)) railMode = "expanded";
  let peek = false;                     // Expand on hover: the pointer is on the rail
  const narrow = window.matchMedia("(max-width: 1000px)");
  const hoverMode = () => railMode === "hover" || narrow.matches;
  function applyRail() {
    const hov = hoverMode();
    const b = document.body.classList;
    b.toggle("rail-hover", hov);
    b.toggle("rail-peek", hov && peek);
    b.toggle("rail-collapsed", hov ? !peek : railMode === "collapsed");
  }
  applyRail();
  narrow.addEventListener("change", () => { peek = false; applyRail(); });

  // ---- which pages this copy shows -----------------------------------------------------------
  // In order: what the owner switched (the policy from the usage server, /api/features) — off is off
  // for everyone on this copy; then the person's own choice in Settings; then the page's default:
  // shown, folded under More, or (IMU) shown once data exists. The last answer is kept, so the
  // sidebar is right on the first paint and the server's answer only confirms it.
  const FEAT_KEY = "runway.features";
  const RESOLVED_KEY = "runway.features.resolved";
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(FEAT_KEY) || "{}") || {}; } catch (e) { prefs = {}; }
  let resolved = {};
  try { resolved = JSON.parse(localStorage.getItem(RESOLVED_KEY) || "{}") || {}; } catch (e) { resolved = {}; }
  let featuresKey = null;
  const hasPref = (it) => Object.prototype.hasOwnProperty.call(prefs, it.id);
  const ownerOff = (it) => !!resolved[it.id] && resolved[it.id].by === "owner" && resolved[it.id].state === "hidden";
  const visible = (it) => {
    if (ownerOff(it)) return false;
    if (hasPref(it)) return prefs[it.id] !== false;
    if (resolved[it.id]) return resolved[it.id].state !== "hidden";
    return !isSoon(it);
  };
  const inMore = (it) => !hasPref(it) && !!resolved[it.id] && resolved[it.id].state === "more";
  function savePrefs() {
    try { localStorage.setItem(FEAT_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ }
  }
  function loadFeatures() {
    return fetch("/api/features").then((r) => r.json()).then((d) => {
      resolved = d.features || {};
      featuresKey = d.key || null;
      try { localStorage.setItem(RESOLVED_KEY, JSON.stringify(resolved)); } catch (e) { /* ignore */ }
      buildRail();
      guardPage();
    }).catch(() => { /* the server is away: the kept answer stands */ });
  }
  // a page the owner switched off says so, instead of working half-hidden
  function guardPage() {
    const it = SECTIONS.reduce((f, sec) => f || sec.items.find((x) => x.id === page), null);
    const content = document.getElementById("content");
    if (!it || !content || !ownerOff(it) || document.getElementById("feature-off")) return;
    [...content.children].forEach((c) => { c.hidden = true; });
    const n = document.createElement("div");
    n.id = "feature-off";
    n.className = "feature-off";
    n.innerHTML = '<span class="ui-ico">' + ICONS.lock + "</span>" +
      '<p class="feature-off-t">' + esc(it.label) + " is switched off on this copy</p>" +
      '<a class="feature-off-a" href="index.html">Dashboard</a>';
    content.appendChild(n);
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

  // ---- the rail: the nav, and the fold button at its foot (the brand lives in the banner) -------
  const rail = document.createElement("aside");
  rail.id = "rail";
  rail.innerHTML =
    '<div id="rail-nav"></div>' +
    '<div class="rail-foot"><button class="rail-fold" id="rail-ctl" type="button" aria-haspopup="menu" ' +
      'title="Sidebar control" aria-label="Sidebar control"><span class="rail-ico">' + ICONS.sidebar + "</span>" +
      "</button></div>";
  const railNav = rail.querySelector("#rail-nav");

  // Expand on hover: open a moment after the pointer arrives, close a moment after it leaves
  let peekTimer = 0;
  rail.addEventListener("pointerenter", (e) => {
    if (!hoverMode() || e.pointerType === "touch") return;
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => { peek = true; applyRail(); }, 120);
  });
  rail.addEventListener("pointerleave", () => {
    clearTimeout(peekTimer);
    if (!peek || document.getElementById("rail-menu")) return;
    peekTimer = setTimeout(() => { peek = false; applyRail(); }, 180);
  });

  // ---- Sidebar control: the menu at the rail's foot -------------------------------------------
  function setRailMode(id) {
    railMode = id;
    peek = false;
    try { localStorage.setItem(RAIL_KEY, id); } catch (e) { /* ignore */ }
    applyRail();
    // maps and canvases re-measure once the rail has finished moving
    setTimeout(() => window.dispatchEvent(new Event("resize")), 230);
  }
  function closeRailMenu() {
    const m = document.getElementById("rail-menu");
    if (m) m.remove();
    document.removeEventListener("pointerdown", railMenuOutside, true);
    document.removeEventListener("keydown", railMenuKey, true);
    const ctl = rail.querySelector("#rail-ctl");
    if (ctl) ctl.setAttribute("aria-expanded", "false");
    if (peek && !rail.matches(":hover")) { peek = false; applyRail(); }
  }
  function railMenuOutside(e) {
    const m = document.getElementById("rail-menu");
    if (m && !m.contains(e.target) && !e.target.closest("#rail-ctl")) closeRailMenu();
  }
  function railMenuKey(e) {
    const m = document.getElementById("rail-menu");
    if (!m) return;
    if (e.key === "Escape") { e.preventDefault(); closeRailMenu(); rail.querySelector("#rail-ctl").focus(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(m.querySelectorAll(".ui-menu-item"));
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  }
  function openRailMenu() {
    const ctl = rail.querySelector("#rail-ctl");
    const m = document.createElement("div");
    m.id = "rail-menu";
    m.className = "ui-menu";
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", "Sidebar control");
    m.innerHTML = '<div class="ui-menu-h">Sidebar control</div>';
    RAIL_MODES.forEach((mode) => {
      const it = document.createElement("button");
      it.type = "button";
      it.className = "ui-menu-item" + (mode.id === railMode ? " is-on" : "");
      it.setAttribute("role", "menuitemradio");
      it.setAttribute("aria-checked", String(mode.id === railMode));
      it.innerHTML = '<span class="ui-menu-dot" aria-hidden="true"></span>';
      it.appendChild(document.createTextNode(mode.label));
      it.addEventListener("click", () => { closeRailMenu(); setRailMode(mode.id); });
      m.appendChild(it);
    });
    document.body.appendChild(m);
    const r = ctl.getBoundingClientRect();
    m.style.left = Math.max(8, r.left) + "px";
    m.style.top = Math.max(8, r.top - m.offsetHeight - 6) + "px";
    ctl.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", railMenuOutside, true);
    document.addEventListener("keydown", railMenuKey, true);
    (m.querySelector(".is-on") || m.querySelector(".ui-menu-item")).focus();
  }
  rail.querySelector("#rail-ctl").addEventListener("click", () => {
    if (document.getElementById("rail-menu")) closeRailMenu(); else openRailMenu();
  });

  const MORE_KEY = "runway.rail.more";
  function buildRail() {
    railNav.innerHTML = "";
    const more = [];
    const item = (sec, it) => {
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
      return a;
    };
    SECTIONS.forEach((sec) => {
      const items = sec.items.filter(visible);
      items.filter(inMore).forEach((it) => more.push([sec, it]));
      const here = items.filter((it) => !inMore(it));
      if (!here.length) return;
      const h = document.createElement("div");
      h.className = "rail-section";
      h.innerHTML = '<span class="rail-section-t">' + esc(sec.title) + '</span><span class="rail-section-rule"></span>';
      railNav.appendChild(h);
      here.forEach((it) => railNav.appendChild(item(sec, it)));
    });
    // used now and then: folded under More, opened by its heading (and always for the open page)
    if (more.length) {
      let open = false;
      try { open = localStorage.getItem(MORE_KEY) === "1"; } catch (e) { /* ignore */ }
      if (more.some(([, it]) => it.id === page)) open = true;
      const h = document.createElement("button");
      h.type = "button";
      h.className = "rail-section rail-more" + (open ? " open" : "");
      h.setAttribute("aria-expanded", String(open));
      h.innerHTML = '<span class="rail-section-t">More</span><span class="rail-more-ico">' + ICONS["chevron-down"] +
        '</span><span class="rail-section-rule"></span>';
      h.title = "More";
      h.addEventListener("click", () => {
        try { localStorage.setItem(MORE_KEY, open ? "0" : "1"); } catch (e) { /* ignore */ }
        buildRail();
      });
      railNav.appendChild(h);
      if (open) more.forEach(([sec, it]) => railNav.appendChild(item(sec, it)));
    }
  }
  buildRail();

  // ---- the banner: fixed across the top, Supabase's breadcrumb line — the app, the person, the
  // page — then the page's own controls and the chips. The page's description is the page crumb's
  // hold-tip; the greeting appears once the person has given their name (appctl.js).
  const here = SECTIONS.reduce((f, s) => f || s.items.find((it) => it.id === page), null);
  const topbar = document.createElement("div");
  topbar.id = "banner";
  topbar.innerHTML =
    // the name and its version nested, one above the other — the brand stays as narrow as the name
    '<a class="bn-brand" href="index.html" title="Dashboard"><span class="bn-logo">' + BRAND + "</span>" +
      '<span class="bn-id"><span class="bn-name">Run<b>way</b></span><span class="bn-version" id="app-version"></span></span></a>' +
    '<span class="bn-sep" aria-hidden="true"></span>' +
    '<span class="bn-crumb bn-hello" id="banner-hello" hidden>' + icon("user") + '<span id="banner-hello-t"></span></span>' +
    '<span class="bn-sep" id="banner-hello-sep" aria-hidden="true" hidden></span>' +
    '<span class="bn-crumb bn-page" id="topbar-page">' + (here ? icon(here.icon) : "") + "</span>" +
    '<span class="bn-spacer"></span>' +
    '<span class="topbar-ctl" id="topbar-ctl"></span>' +
    '<button class="ui-chip update-chip" id="update-chip" type="button" hidden></button>' +   // appctl.js
    '<span class="ui-chip topbar-status" id="mode-badge"><span class="ui-chip-dot"></span><span id="mode-label">Idle</span></span>' +
    '<button class="topbar-btn" id="theme-btn" type="button"></button>';
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
    paintTitleBar();
  }
  // The window's title bar takes the banner's colour, so Runway's own window reads as one piece.
  // Edge's app window ignores the page's theme-color (kept for a browser that honours it), so the
  // server colours its own windows through Windows (/api/desktop/titlebar).
  function paintTitleBar() {
    const cs = getComputedStyle(document.documentElement);
    const bg = cs.getPropertyValue("--panel").trim() || "#171918";
    const fg = cs.getPropertyValue("--text").trim() || "#edefee";
    let m = document.querySelector('meta[name="theme-color"]');
    if (!m) {
      m = document.createElement("meta");
      m.name = "theme-color";
      document.head.appendChild(m);
    }
    m.content = bg;
    fetch("/api/desktop/titlebar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      // Runway's own window has no toolbar; a browser tab does — only the window reopens in the new mode
      body: JSON.stringify({ background: bg, text: fg, dark: theme === "dark", url: location.href,
                             app: !(window.toolbar && window.toolbar.visible) || matchMedia("(display-mode: standalone)").matches }),
    }).catch(() => { /* not on Windows, or the server is away: the title bar stays as it is */ });
  }

  // ---- assemble ---------------------------------------------------------------------------------
  const content = document.getElementById("content");
  const envWarn = document.createElement("div");
  envWarn.id = "env-warn";
  envWarn.className = "ui-notice warn";
  envWarn.hidden = true;
  const mainCol = document.createElement("div");
  mainCol.id = "main-col";
  mainCol.append(envWarn);
  if (content) mainCol.appendChild(content);
  const app = document.createElement("div");
  app.id = "app";
  app.append(rail, mainCol);
  document.body.prepend(topbar, app);
  applyMode();
  applyTheme();
  setTimeout(paintTitleBar, 1500);     // a window just opened may not carry its title yet

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

  // ---- resizing: drag an edge, between a min and a max, remembered (Supabase's panels) -----------
  // The handle is a thin strip on the target's edge, placed in the target's parent so a pane that
  // scrolls does not carry it away. Dragging writes a CSS variable on the parent, which the page's
  // CSS reads (a grid column, a height), or calls opts.apply. A double-click puts the default back;
  // with the handle focused, the arrow keys move it 16 px. Under 1000px only vertical splits resize
  // (side panes then lie over the page). The max always leaves `keep` px for the rest of the parent.
  const sizeKey = (k) => "runway.size." + k;
  function readSize(k) {
    try { const v = parseFloat(localStorage.getItem(sizeKey(k))); return isFinite(v) ? v : null; } catch (e) { return null; }
  }
  function saveSize(k, v) {
    if (!k) return;
    try {
      if (v == null) localStorage.removeItem(sizeKey(k)); else localStorage.setItem(sizeKey(k), String(Math.round(v)));
    } catch (e) { /* private mode */ }
  }
  let kickFrame = 0;
  function kickResize() {             // maps and canvases re-measure, at most once a frame
    if (kickFrame) return;
    kickFrame = requestAnimationFrame(() => { kickFrame = 0; window.dispatchEvent(new Event("resize")); });
  }
  function dragging(axis, on) {
    document.body.classList.toggle("ui-resizing", on);
    document.body.classList.toggle("ui-resizing-" + axis, on);
  }
  function resizable(target, opts) {
    if (!target || target.dataset.resizeReady) return null;
    const o = opts || {};
    const d = target.dataset;
    const edge = o.edge || d.resizeEdge || "right";
    const vertical = edge === "top" || edge === "bottom";
    const key = o.key || d.resize;
    const cssVar = o.cssVar || d.resizeVar;
    const min = +(o.min || d.resizeMin || 160);
    const maxOpt = +(o.max || d.resizeMax || 0);
    const keep = +(o.keep || d.resizeKeep || 320);
    const title = o.title || d.resizeTitle || "the panel";
    const host = o.host || target.parentElement;
    if (!host) return null;
    d.resizeReady = "1";
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    const grow = edge === "right" || edge === "bottom" ? 1 : -1;
    const apply = o.apply || ((px) => {
      if (px == null) host.style.removeProperty(cssVar); else host.style.setProperty(cssVar, px + "px");
    });
    const size = () => { const r = target.getBoundingClientRect(); return vertical ? r.height : r.width; };
    // folded (a strip far under the minimum) or not on screen: no handle
    const enabled = o.enabled || (() => !(narrow.matches && !vertical) &&
      !target.classList.contains("is-folded") && target.offsetParent !== null && size() >= min * 0.75);
    const maxNow = () => {
      const room = (vertical ? host.clientHeight : host.clientWidth) - keep;
      return Math.max(min, maxOpt ? Math.min(maxOpt, room) : room);
    };
    const clamp = (v) => Math.round(Math.min(Math.max(v, min), maxNow()));

    const h = document.createElement("div");
    h.className = "ui-resize " + (vertical ? "is-y" : "is-x");
    h.setAttribute("role", "separator");
    h.setAttribute("aria-orientation", vertical ? "horizontal" : "vertical");
    h.setAttribute("aria-label", "Resize " + title);
    h.tabIndex = 0;
    h.dataset.tip = "Drag to resize " + title + ". A double-click puts it back.";
    host.appendChild(h);

    function place() {
      if (!enabled()) { h.hidden = true; return; }
      h.hidden = false;
      const t = target.getBoundingClientRect(), p = host.getBoundingClientRect();
      if (vertical) {
        const y = (edge === "top" ? t.top : t.bottom) - p.top + host.scrollTop;
        h.style.cssText = "left:" + (t.left - p.left) + "px;width:" + t.width + "px;top:" + (y - 3) + "px";
      } else {
        const x = (edge === "left" ? t.left : t.right) - p.left + host.scrollLeft;
        h.style.cssText = "top:" + (t.top - p.top) + "px;height:" + t.height + "px;left:" + (x - 3) + "px";
      }
    }
    function set(px, save) {
      apply(px);
      if (save) saveSize(key, px);
      place();
      kickResize();
    }
    const saved = key ? readSize(key) : null;   // the parent may not be laid out yet: min/max only
    if (saved != null) apply(Math.max(min, maxOpt ? Math.min(maxOpt, saved) : saved));

    h.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      const start = vertical ? e.clientY : e.clientX;
      const from = size();
      let last = from;
      dragging(vertical ? "y" : "x", true);
      h.classList.add("is-active");
      const move = (ev) => { last = clamp(from + ((vertical ? ev.clientY : ev.clientX) - start) * grow); set(last, false); };
      const up = () => {
        h.removeEventListener("pointermove", move);
        h.removeEventListener("pointerup", up);
        h.removeEventListener("pointercancel", up);
        dragging(vertical ? "y" : "x", false);
        h.classList.remove("is-active");
        saveSize(key, last);
        window.dispatchEvent(new Event("resize"));
      };
      h.addEventListener("pointermove", move);
      h.addEventListener("pointerup", up);
      h.addEventListener("pointercancel", up);
    });
    h.addEventListener("dblclick", () => set(null, true));
    h.addEventListener("keydown", (e) => {
      const step = (vertical ? { ArrowDown: 1, ArrowUp: -1 } : { ArrowRight: 1, ArrowLeft: -1 })[e.key];
      if (!step) return;
      e.preventDefault();
      set(clamp(size() + step * 16 * grow), true);
    });
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => place());
      ro.observe(target);
      ro.observe(host);
    }
    window.addEventListener("resize", place);
    narrow.addEventListener("change", place);
    requestAnimationFrame(place);
    return { place, reset: () => set(null, true) };
  }

  // the rail: Supabase's side menu widens and narrows; folded, it is the icon rail
  resizable(rail, {
    key: "rail", edge: "right", host: app, min: 180, max: 380, keep: 480, title: "the sidebar",
    apply: (px) => {
      if (px == null) document.documentElement.style.removeProperty("--rail-w");
      else document.documentElement.style.setProperty("--rail-w", px + "px");
    },
    enabled: () => railMode === "expanded" && !narrow.matches,
  });

  // ---- table columns: drag the edge of a header cell, as in Supabase's grid ----------------------
  // The first drag fixes every column of that table at the width it had, then the dragged one
  // follows the pointer (between COL_MIN and COL_MAX); the table grows or shrinks with it. Widths
  // are remembered per page, table and column name, and put back when the table is drawn again.
  // A double-click on an edge gives the table its natural widths back.
  const COLS_KEY = "runway.cols." + (page || "page");
  const EDGE_PX = 6, COL_MIN = 44, COL_MAX = 900;
  let colPrefs = {};
  try { colPrefs = JSON.parse(localStorage.getItem(COLS_KEY) || "{}") || {}; } catch (e) { colPrefs = {}; }
  const saveCols = () => { try { localStorage.setItem(COLS_KEY, JSON.stringify(colPrefs)); } catch (e) { /* ignore */ } };
  function tableKey(t) {
    if (t.id) return t.id;
    const owner = t.parentElement && t.parentElement.closest("[id]");
    const cls = String(t.className || "").replace("ui-cols", "").trim().split(/\s+/)[0] || "table";
    return (owner ? owner.id : "page") + "/" + cls;
  }
  function headCells(t) {
    const rows = t && t.tHead ? t.tHead.rows : null;
    return rows && rows.length === 1 && rows[0].cells.length > 1 ? Array.from(rows[0].cells) : null;
  }
  const colName = (th, i) => (th.textContent || "").replace(/\s+/g, " ").trim().slice(0, 48) || "#" + i;
  const colSig = (cells) => cells.map(colName).join("|");
  function unfreeze(t, cells) {
    t.classList.remove("ui-cols");
    t.style.width = "";
    cells.forEach((c) => { c.style.width = ""; });
  }
  function freeze(t, cells) {
    if (t.classList.contains("ui-cols")) return;
    const ws = cells.map((c) => c.getBoundingClientRect().width);
    cells.forEach((c, i) => { c.style.width = ws[i] + "px"; });
    t.classList.add("ui-cols");
    t.style.width = ws.reduce((a, b) => a + b, 0) + "px";
  }
  function applyCols(t) {
    const cells = headCells(t);
    if (!cells) return;
    const saved = colPrefs[tableKey(t)];
    const sig = colSig(cells);
    const sized = t.classList.contains("ui-cols");
    if (t.dataset.colsSig === sig && (!sized || cells.every((c) => c.style.width))) return;   // as it was
    t.dataset.colsSig = sig;
    unfreeze(t, cells);
    if (!saved || !cells.some((c, i) => saved[colName(c, i)])) return;
    freeze(t, cells);
    let total = 0;
    cells.forEach((c, i) => {
      const w = saved[colName(c, i)];
      if (w) c.style.width = w + "px";
      total += w || parseFloat(c.style.width) || 0;
    });
    t.style.width = total + "px";
  }
  const nearEdge = (th, x) => th.getBoundingClientRect().right - x <= EDGE_PX;
  let swallowClick = false;
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !e.target.closest) return;
    const th = e.target.closest("thead th");
    const t = th && th.closest("table");
    const cells = headCells(t);
    if (!cells || !cells.includes(th) || !nearEdge(th, e.clientX)) return;
    e.preventDefault();
    e.stopPropagation();
    freeze(t, cells);
    const startX = e.clientX;
    const from = parseFloat(th.style.width) || th.getBoundingClientRect().width;
    const others = cells.reduce((s, c) => s + (c === th ? 0 : parseFloat(c.style.width) || 0), 0);
    let w = from;
    dragging("x", true);
    const move = (ev) => {
      w = Math.round(Math.min(COL_MAX, Math.max(COL_MIN, from + ev.clientX - startX)));
      th.style.width = w + "px";
      t.style.width = (others + w) + "px";
    };
    const up = () => {
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", up, true);
      dragging("x", false);
      const k = tableKey(t);
      const m = colPrefs[k] || (colPrefs[k] = {});
      cells.forEach((c, i) => { m[colName(c, i)] = Math.round(parseFloat(c.style.width) || 0) || undefined; });
      saveCols();
      t.dataset.colsSig = colSig(cells);
      swallowClick = true;                         // the click that ends a drag is not a sort
      setTimeout(() => { swallowClick = false; }, 0);
    };
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerup", up, true);
  }, true);
  document.addEventListener("click", (e) => {
    if (swallowClick) { e.stopPropagation(); e.preventDefault(); swallowClick = false; }
  }, true);
  document.addEventListener("dblclick", (e) => {
    const th = e.target.closest ? e.target.closest("thead th") : null;
    const t = th && th.closest("table");
    const cells = headCells(t);
    if (!cells || !nearEdge(th, e.clientX)) return;
    e.preventDefault();
    delete colPrefs[tableKey(t)];
    saveCols();
    unfreeze(t, cells);
  }, true);
  // tables drawn later, or drawn again (a column added, a new decode): put their widths back
  let colFrame = 0;
  const colTables = new Set();
  function queueTables(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.tagName === "TABLE") colTables.add(node);
    else if (node.closest("table")) colTables.add(node.closest("table"));
    else node.querySelectorAll("table").forEach((t) => colTables.add(t));
  }
  if (content && window.MutationObserver) {
    new MutationObserver((muts) => {
      if (!Object.keys(colPrefs).length) return;
      for (const m of muts) {
        const n = m.target;
        if (n.nodeType !== 1 || n.tagName === "TBODY" || n.closest("tbody")) continue;  // rows come and go
        queueTables(n);
      }
      if (colTables.size && !colFrame) {
        colFrame = requestAnimationFrame(() => {
          colFrame = 0;
          colTables.forEach((t) => { if (t.isConnected) applyCols(t); });
          colTables.clear();
        });
      }
    }).observe(content, { childList: true, subtree: true });
    if (Object.keys(colPrefs).length) content.querySelectorAll("table").forEach(applyCols);
  }

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
    // its inner edge resizes it — every pane of the group together (one per Parser tab)
    const cssVar = side === "left" ? "--pane-l-w" : "--pane-r-w";
    resizable(pane, {
      key: "pane." + key, edge: side === "left" ? "right" : "left", cssVar,
      min: +(o.min || pane.dataset.foldMin || 240), max: +(o.max || pane.dataset.foldMax || 720), keep: 380,
      title: title.toLowerCase(),
      apply: (px) => (foldGroups.get(key) || [pane]).forEach((p) => {
        const h = p.parentElement;
        if (!h) return;
        if (px == null) h.style.removeProperty(cssVar); else h.style.setProperty(cssVar, px + "px");
      }),
    });
  }
  document.querySelectorAll("[data-fold]").forEach((p) => foldable(p));
  document.querySelectorAll("[data-resize]").forEach((t) => resizable(t));

  // ---- the house confirm / prompt: a <dialog>, never window.confirm (unstyled, shows the host,
  // and the browser lets people suppress it). A title, one sentence, Cancel focused, the action on
  // the right — red only when it destroys. A notice (something did not work) has OK alone, focused.
  // User strings are set as text, never parsed.
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
      if (o.notice) d.querySelector("[data-cancel]").remove();
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
      if (field) { field.focus(); field.select(); } else (d.querySelector("[data-cancel]") || ok).focus();
    });
  }
  const confirmDlg = (o) => ask(o);                                  // → true / false
  const noticeDlg = (o) => ask(Object.assign({}, o, { notice: true }));  // OK only
  const promptDlg = (o) => ask(Object.assign({}, o, { input: { label: o.label, value: o.value, placeholder: o.placeholder } }));  // → text / null

  // Sections other scripts add to Settings, above the sidebar switches (appctl.js: Usage reporting,
  // Updates). Each is a function that fills the element it is given.
  const settingsSections = [];

  // ---- a small menu under a button: choices with a figure beside each (the Sidebar control's look) ----
  // items: [{label, sub?, on?, run}] — Esc, a click elsewhere or a choice closes it; arrows move.
  function menuAt(anchor, title, items) {
    const old = document.getElementById("ui-menu-open");
    if (old) { const same = old._anchor === anchor; old._close(); if (same) return; }
    const m = document.createElement("div");
    m.id = "ui-menu-open";
    m.className = "ui-menu";
    m.setAttribute("role", "menu");
    m._anchor = anchor;
    if (title) {
      const h = document.createElement("div");
      h.className = "ui-menu-h";
      h.textContent = title;
      m.appendChild(h);
    }
    const close = () => {
      m.remove();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", key, true);
      anchor.setAttribute("aria-expanded", "false");
    };
    m._close = close;
    const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) close(); };
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(); anchor.focus(); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const all = Array.from(m.querySelectorAll(".ui-menu-item"));
      const i = all.indexOf(document.activeElement);
      all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length].focus();
    };
    for (const it of items) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ui-menu-item" + (it.on ? " is-on" : "");
      b.setAttribute("role", "menuitem");
      b.innerHTML = '<span class="ui-menu-dot" aria-hidden="true"></span>';
      b.appendChild(document.createTextNode(it.label));
      if (it.sub != null) {
        const s = document.createElement("span");
        s.className = "ui-menu-sub";
        s.textContent = it.sub;
        b.appendChild(s);
      }
      b.addEventListener("click", () => { close(); it.run(); });
      m.appendChild(b);
    }
    document.body.appendChild(m);
    const r = anchor.getBoundingClientRect();
    const below = r.bottom + 6 + m.offsetHeight <= window.innerHeight - 8;
    m.style.left = Math.max(8, Math.min(r.right - m.offsetWidth, window.innerWidth - m.offsetWidth - 8)) + "px";
    m.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - m.offsetHeight - 6)) + "px";
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", key, true);
    const first = m.querySelector(".ui-menu-item.is-on") || m.querySelector(".ui-menu-item");
    if (first) first.focus();
  }

  window.RunwayUI = { icons: ICONS, icon, paint, setLabel, foldable, resizable, esc, menu: menuAt, confirm: confirmDlg, notice: noticeDlg, prompt: promptDlg,
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
      h.innerHTML = '<span class="set-title" data-tip="Show or hide pages in the sidebar for yourself. Off centrally: switched off for this copy by Runway\'s owner. More: folded under More until you switch it on. With data: shown once a device has sent its data.">Sidebar</span>';
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
        const r = resolved[it.id];
        const note = ownerOff(it) ? '<span class="ui-pill sm bad feat-note">Off centrally</span>'
          : r && r.by === "owner" ? '<span class="ui-pill sm ok feat-note">On centrally</span>'
          : !hasPref(it) && r && r.state === "more" ? '<span class="ui-pill sm muted feat-note">More</span>'
          : r && r.by === "data" ? '<span class="ui-pill sm muted feat-note">' + (r.state === "shown" ? "Has data" : "With data") + "</span>"
          : "";
        row.innerHTML = '<span class="feat-ico">' + (ICONS[it.icon] || "") + "</span>" +
          '<span class="feat-label">' + esc(it.label) +
          (isSoon(it) ? '<span class="rail-soon">Soon</span>' : "") + note + "</span>" +
          '<span class="switch"><input type="checkbox" /><span class="slider"></span></span>';
        const cb = row.querySelector("input");
        cb.checked = visible(it) && !inMore(it);
        if (ownerOff(it)) { cb.disabled = true; row.classList.add("is-locked"); }
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
      '<rect width="24" height="24" rx="6" fill="#5ab0f0"/>' +
      '<g fill="none" stroke="#04121d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
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
  let SERVER_STARTED = null;               // a run from source restarted (main.py again): load the latest pages
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
      if (d.started) {
        if (SERVER_STARTED === null) SERVER_STARTED = d.started;
        else if (d.branch && d.started !== SERVER_STARTED) { location.reload(); return; }
      }
      const v = document.getElementById("app-version");
      if (v && d.version) {
        // a run from source names its branch, so it is never taken for a released copy of that version
        v.textContent = d.branch ? "Branch " + d.branch : "Version " + d.version;
        v.title = (d.branch ? "Running from source · version " : "Server ") + d.version +
          (d.code_version && d.code_version !== d.version ? " · code on disk " + d.code_version : "");
      }
      if (d.mode && d.mode !== mode) mode = d.mode;
      if (d.features_key && d.features_key !== featuresKey) loadFeatures();
      applyMode();
      const warns = [];
      if (d.version && UI_VERSION && d.version !== UI_VERSION) {
        // The banner shows the SERVER's version: a page older than the server is a cached tab
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
  loadFeatures();
  refreshStatus();
  setInterval(refreshStatus, 4000);

  // Usage reporting and updates (the first-start welcome, the greeting, the update offer and chip,
  // their Settings sections) — on every page, without each page loading it.
  const appctl = document.createElement("script");
  appctl.src = "appctl.js";
  document.body.appendChild(appctl);
})();
