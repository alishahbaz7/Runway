/* Runway app shell: a fixed top banner (toggle + brand) over a collapsible left rail.
 *
 * Layout built at load:
 *   body (column) ─ #topbar (☰ + Runway logo/name, full width)
 *                 └ #app (row) ─ #rail (nav, collapsible) | #content (page)
 *
 * Both pages set <body data-page="dashboard|parser">. The ☰ toggle folds the rail to an
 * icon-only strip; the choice is remembered across pages.
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
      { id: "captures", label: "Captures", icon: "🗂", disabled: true },
    ] },
    { title: "Library", items: [
      { id: "profiles", label: "Profiles", icon: "🧩", disabled: true },
      { id: "settings", label: "Settings", icon: "⚙", disabled: true },
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

  // Collapsed state (remembered across pages) — applied before build to avoid a flash.
  const COLLAPSE_KEY = "runway.rail.collapsed";
  let collapsed = false;
  try { collapsed = localStorage.getItem(COLLAPSE_KEY) === "1"; } catch (e) { /* ignore */ }
  document.body.classList.toggle("rail-collapsed", collapsed);

  // Data mode: "demo" shows the Load-sample button, "live" hides it. Applied before build.
  const MODE_KEY = "runway.mode";
  let mode = "demo";
  try { mode = localStorage.getItem(MODE_KEY) || "demo"; } catch (e) { /* ignore */ }
  function applyMode() {
    document.body.classList.toggle("mode-live", mode === "live");
    document.body.classList.toggle("mode-demo", mode === "demo");
    const l = document.getElementById("mode-label");
    if (l) l.textContent = mode === "live" ? "Live" : "Demo";
  }
  applyMode();

  // ---- top banner --------------------------------------------------------
  const topbar = document.createElement("div");
  topbar.id = "topbar";
  topbar.innerHTML =
    '<button class="topbar-toggle" title="Collapse / expand sidebar">☰</button>' +
    '<div class="topbar-brand"><span class="topbar-logo">R</span>' +
    '<span class="topbar-name">Runway</span>' +
    '<span class="topbar-version" id="rail-version"></span></div>' +
    '<span class="topbar-tag">Where telematics data lands &amp; takes off</span>' +
    '<span class="topbar-spacer"></span>' +
    '<span class="topbar-status" id="mode-btn" title="data mode — click to switch">' +
    '<span class="dot" id="rail-dot"></span>' +
    '<span id="mode-label">Demo</span><span class="caret">▾</span></span>';
  topbar.querySelector(".topbar-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    document.body.classList.toggle("rail-collapsed", collapsed);
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
  });

  // ---- left rail (nav only) ----------------------------------------------
  const rail = document.createElement("aside");
  rail.id = "rail";
  SECTIONS.forEach((sec) => {
    const h = document.createElement("div");
    h.className = "rail-section";
    h.textContent = sec.title;
    rail.appendChild(h);
    sec.items.forEach((it) => {
      const a = document.createElement("a");
      a.className = "rail-item" + (it.id === page ? " active" : "") + (it.disabled ? " disabled" : "");
      a.title = it.label;                          // tooltip when collapsed
      if (it.href && !it.disabled) a.href = it.href;
      a.innerHTML = `<span class="rail-ico">${it.icon}</span><span>${it.label}</span>` +
        (it.disabled ? '<span class="rail-soon">soon</span>' : "");
      rail.appendChild(a);
    });
  });

  // ---- assemble: body ▸ topbar + app(rail | content) ---------------------
  const content = document.getElementById("content");
  const app = document.createElement("div");
  app.id = "app";
  app.appendChild(rail);
  if (content) app.appendChild(content);     // move existing page content beside the rail
  document.body.prepend(app);
  document.body.prepend(topbar);
  applyMode();                                 // set the label now the topbar exists

  // ---- mode switcher (Live / Demo) ---------------------------------------
  function setMode(m) { mode = m; try { localStorage.setItem(MODE_KEY, m); } catch (e) { /* ignore */ } applyMode(); }
  document.getElementById("mode-btn").addEventListener("click", () => {
    if (document.getElementById("mode-pop")) { document.getElementById("mode-pop").remove(); return; }
    const pop = document.createElement("div"); pop.id = "mode-pop"; pop.className = "mode-pop";
    [["live", "● Live"], ["demo", "● Demo"]].forEach(([m, label]) => {
      const b = document.createElement("button");
      b.textContent = label;
      if (m === mode) b.className = "sel";
      b.addEventListener("click", () => { setMode(m); pop.remove(); });
      pop.appendChild(b);
    });
    document.body.appendChild(pop);
    const r = document.getElementById("mode-btn").getBoundingClientRect();
    pop.style.top = `${r.bottom + 4}px`;
    pop.style.left = `${Math.max(8, r.right - pop.offsetWidth)}px`;
    setTimeout(() => document.addEventListener("click", onDoc), 0);
    function onDoc(e) {
      if (!pop.contains(e.target) && !document.getElementById("mode-btn").contains(e.target)) {
        pop.remove(); document.removeEventListener("click", onDoc);
      }
    }
  });

  // Version + liveness from the API (best-effort).
  fetch("/api/health").then((r) => r.json()).then((d) => {
    const v = document.getElementById("rail-version");
    if (v && d.version) v.textContent = "v" + d.version;
    const dot = document.getElementById("rail-dot");
    if (dot) dot.classList.add("ok");
  }).catch(() => {
    const dot = document.getElementById("rail-dot");
    if (dot) dot.classList.remove("ok");
  });
})();
