/* Runway — usage reporting and self-update in the page (the app-control and app-updates skills).
 *
 *   - First start: the name, asked once, before anything else; the dialog cannot be dismissed.
 *   - The Dashboard greets the person: "Hello, Name".
 *   - Updates: a newer version is offered once per run, with its notes and the listeners its
 *     restart restarts; Later leaves a chip in the top bar. Update downloads, verifies, installs
 *     beside this version and restarts into it — the page follows on its own.
 *   - Settings: Usage reporting (name, install ID, last sent, Send now) and Updates (Check now).
 *
 * Silent: a check or a send that fails is said only in Settings, never in a banner.
 * Loaded by shell.js on every page; uses window.RunwayUI.
 */
(() => {
  "use strict";
  const UI = window.RunwayUI;
  if (!UI) return;

  const get = (path) => fetch(path).then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))));
  const post = (path, body) => fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(async (r) => {
    if (r.ok) return r.json();
    let detail = "HTTP " + r.status;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(detail);
  });
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const pad = (n) => String(n).padStart(2, "0");
  // 24-hour, DD-MM-YYYY; today's times without the date
  const when = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return "—";
    const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return d.toDateString() === new Date().toDateString()
      ? `Today ${t}` : `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()} ${t}`;
  };
  const mb = (n) => (n == null ? "" : (n / 1048576).toFixed(1) + " MB");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  let usage = null;           // GET /api/usage
  let upd = null;             // GET /api/update

  // ---- first start: the name ---------------------------------------------------------------------
  // A welcome, not a notice: the app getting to know its person (the user, 12-11-2026: "Who is
  // using Runway? Runway reports your name…" read as someone watching — "not acceptable at all").
  // The same words as BluePulse, so every app greets the same way; what is shared is in Settings.
  // Name only; Continue once something is typed; no Esc, no backdrop click — but nothing else waits
  // on the server: the name is saved at once and sent in the background.
  const WELCOME = "Hi! May I have your name, please?";
  const WELCOME_TEXT = "We'll use it to greet you and to make Runway better for you. "
    + "Settings → Usage reporting shows what is shared.";
  function askName(edit) {
    return new Promise((resolve) => {
      const d = el("dialog", "ui-dlg");
      d.innerHTML =
        '<form method="dialog"><div class="ui-dlg-head"><h2></h2></div>' +
        '<div class="ui-dlg-body"></div>' +
        '<div class="ui-dlg-foot"><span class="spacer"></span></div></form>';
      d.querySelector("h2").textContent = edit ? "Your name" : WELCOME;
      const body = d.querySelector(".ui-dlg-body");
      body.appendChild(el("p", "ui-dlg-text", WELCOME_TEXT));
      const lab = el("label", "ui-dlg-field", "Name");
      const input = el("input");
      input.maxLength = 80;
      input.autocomplete = "name";
      input.spellcheck = false;
      input.value = edit && usage ? usage.name || "" : "";
      lab.appendChild(input);
      body.appendChild(lab);
      const err = el("div", "ui-notice warn");
      err.hidden = true;
      body.appendChild(err);
      const foot = d.querySelector(".ui-dlg-foot");
      if (edit) {
        const cancel = el("button", "ghost", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", () => d.close());
        foot.appendChild(cancel);
      }
      const ok = el("button", "", edit ? "Save" : "Continue");
      ok.type = "submit";
      foot.appendChild(ok);
      const sync = () => { ok.disabled = !input.value.trim(); };
      input.addEventListener("input", sync);
      sync();
      if (!edit) d.addEventListener("cancel", (e) => e.preventDefault());     // Esc does nothing
      d.querySelector("form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) return;
        ok.disabled = true;
        try {
          usage = await post("/api/usage/name", { name });
          d.close();
        } catch (ex) {
          err.textContent = "Not saved: " + ex.message;
          err.hidden = false;
          ok.disabled = false;
        }
      });
      d.addEventListener("close", () => { d.remove(); greet(); resolve(); });
      document.body.appendChild(d);
      d.showModal();
      input.focus();
    });
  }

  // ---- the greeting, on the Dashboard ------------------------------------------------------------
  function greet() {
    const g = document.getElementById("dash-hello");
    if (!g) return;
    const name = usage && usage.enabled && usage.name;
    g.textContent = name ? "Hello, " + name : "";
    g.hidden = !name;
  }

  // ---- updates: the chip, the offer, the progress ------------------------------------------------
  const chip = document.getElementById("update-chip");
  let offerDlg = null;
  let following = false;      // this page asked for the install: follow it through the restart

  function paintChip() {
    if (!chip) return;
    const s = upd;
    const show = s && s.available && s.stage !== "disabled";
    chip.hidden = !show;
    if (!show) return;
    let text = "Update " + s.latest;
    let ico = "download";
    if (s.stage === "downloading") {
      text = s.total ? `Updating ${Math.floor((s.received / s.total) * 100)}%` : "Updating";
    } else if (s.stage === "installing") {
      text = "Installing";
    } else if (s.stage === "restarting") {
      text = "Restarting";
      ico = "refresh";
    }
    UI.setLabel(chip, ico, text);
    chip.classList.toggle("is-required", !!s.required);
    chip.title = s.required ? `Runway ${s.latest} is required` : `Runway ${s.latest} is available`;
  }
  if (chip) chip.addEventListener("click", () => openOffer());

  async function refreshUpdate() {
    try { upd = await get("/api/update"); } catch (e) { return; }
    paintChip();
    if (offerDlg) paintOffer();
  }

  // Once per run (the server remembers across pages), after the name question.
  async function maybeOffer() {
    if (!upd || !upd.available || upd.offered || upd.stage === "disabled") return;
    try { upd = await post("/api/update/offered"); } catch (e) { return; }
    openOffer();
  }

  function openOffer() {
    if (offerDlg || !upd || !upd.available) return;
    const d = el("dialog", "ui-dlg upd-dlg");
    d.innerHTML =
      '<div class="ui-dlg-head"><h2></h2><span class="spacer"></span></div>' +
      '<div class="ui-dlg-body"></div>' +
      '<div class="ui-dlg-foot"></div>';
    d.addEventListener("cancel", (e) => {
      if (upd && (upd.required || ["downloading", "installing", "restarting"].includes(upd.stage))) e.preventDefault();
    });
    d.addEventListener("close", () => { d.remove(); offerDlg = null; });
    document.body.appendChild(d);
    offerDlg = d;
    paintOffer();
    d.showModal();
  }

  function paintOffer() {
    const d = offerDlg;
    const s = upd;
    if (!d || !s) return;
    const head = d.querySelector(".ui-dlg-head");
    head.querySelector("h2").textContent = `Runway ${s.latest} is available`;
    head.querySelectorAll(".ui-pill").forEach((p) => p.remove());
    if (s.required) head.appendChild(el("span", "ui-pill warn", "Required"));

    const body = d.querySelector(".ui-dlg-body");
    body.innerHTML = "";
    const facts = [`Installed ${s.current}`];
    if (s.size) facts.push(mb(s.size));
    if (s.published) facts.push("published " + when(s.published).replace("Today ", "today "));
    body.appendChild(el("div", "upd-facts", facts.join(" · ")));
    // the changelog section, as text: markdown's #, ** and ` markers dropped
    if (s.notes) body.appendChild(el("div", "upd-notes",
      s.notes.replace(/^#{1,6}\s+/gm, "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")));

    const busy = ["downloading", "installing", "restarting"].includes(s.stage);
    if (busy) {
      const prog = el("div", "upd-prog");
      const pct = s.total ? Math.floor((s.received / s.total) * 100) : null;
      const line = el("div", "upd-prog-line");
      line.append(el("span", "ui-spin"),
        el("span", "", s.stage === "downloading"
          ? (pct == null ? "Downloading" : `Downloading ${pct}% · ${mb(s.received)} of ${mb(s.total)}`)
          : s.stage === "installing" ? "Installing beside this version"
          : `Restarting into ${s.latest}`));
      const bar = el("div", "upd-bar");
      const fill = el("div", "upd-fill");
      fill.style.width = (s.stage === "downloading" ? (pct || 0) : 100) + "%";
      bar.appendChild(fill);
      prog.append(line, bar);
      body.appendChild(prog);
    } else if (s.stage === "failed" && s.error && following) {
      const n = el("div", "ui-notice warn");
      n.innerHTML = UI.icon("alert");
      n.appendChild(el("span", "ui-notice-t", "Not installed: " + s.error));
      body.appendChild(n);
    } else if (s.listeners > 0) {
      const n = el("div", "ui-notice warn");
      n.innerHTML = UI.icon("tcp");
      n.appendChild(el("span", "ui-notice-t",
        `${s.listeners} ${s.listeners === 1 ? "listener restarts" : "listeners restart"}; devices reconnect on their own`));
      body.appendChild(n);
    }

    const foot = d.querySelector(".ui-dlg-foot");
    foot.innerHTML = "";
    foot.appendChild(el("span", "spacer"));
    if (!busy) {
      if (!s.required) {
        const later = el("button", "ghost", "Later");
        later.type = "button";
        later.addEventListener("click", () => { post("/api/update/later").catch(() => {}); offerDlg.close(); });
        foot.appendChild(later);
      }
      const go = el("button", "");
      go.type = "button";
      UI.setLabel(go, s.can_install_here ? "download" : "relay",
        s.can_install_here ? (s.stage === "failed" && following ? "Try again" : "Update") : "Open the release");
      go.addEventListener("click", startInstall);
      foot.appendChild(go);
    }
  }

  async function startInstall() {
    following = true;
    let s;
    try { s = await post("/api/update/install"); } catch (e) { return; }
    if (s.open) {                                 // not a launcher install: the release page
      window.open(s.open, "_blank", "noopener");
      if (offerDlg) offerDlg.close();
      return;
    }
    upd = s;
    paintChip();
    paintOffer();
    follow();
  }

  // Poll the install, then the restart: when the server answers with the new version, reload.
  async function follow() {
    while (upd && ["downloading", "installing"].includes(upd.stage)) {
      await sleep(600);
      await refreshUpdate();
    }
    if (!upd || upd.stage !== "restarting") return;
    const target = upd.latest;
    const t0 = Date.now();
    while (Date.now() - t0 < 120000) {
      await sleep(1500);
      try {
        const h = await get("/api/health");
        if (h.version === target) { location.reload(); return; }
      } catch (e) { /* the server is between versions */ }
    }
    if (offerDlg) {
      const body = offerDlg.querySelector(".ui-dlg-body");
      body.innerHTML = "";
      const n = el("div", "ui-notice warn");
      n.innerHTML = UI.icon("alert");
      n.appendChild(el("span", "ui-notice-t", `Runway ${target} has not answered yet. Start it from Runway.exe.`));
      body.appendChild(n);
    }
  }

  // ---- Settings: Usage reporting and Updates ------------------------------------------------------
  function fact(dl, k, v, mono) {
    dl.append(el("dt", "", k), el("dd", mono ? "mono" : "", v));
  }
  function notice(sec, text, tone) {
    const n = el("div", "ui-notice " + (tone || "warn"));
    n.innerHTML = UI.icon("alert");
    n.appendChild(el("span", "ui-notice-t", text));
    sec.appendChild(n);
  }

  function usageSection(sec) {
    const render = () => {
      sec.innerHTML = "";
      const h = el("div", "set-h");
      const title = el("span", "set-title", "Usage reporting");
      title.dataset.tip = "Reported: your name, this computer, the Runway version, how long it ran, the pages opened, errors and how many listeners, decodes and exchanges it ran. Never reported: IMEIs, packets, endpoints, keys or files.";
      h.appendChild(title);
      sec.appendChild(h);
      const s = usage;
      if (!s) { notice(sec, "Usage reporting is starting"); return; }
      if (!s.enabled) { notice(sec, s.disabled_reason || "Usage reporting is off in this build", "muted"); return; }
      h.appendChild(el("span", "spacer"));
      const edit = UI.setLabel(el("button", "ghost mini"), "edit", "Edit name");
      edit.type = "button";
      edit.addEventListener("click", async () => { await askName(true); render(); });
      const send = UI.setLabel(el("button", "ghost mini"), "send", s.sending ? "Sending" : "Send now");
      send.type = "button";
      send.disabled = !!s.sending;
      send.addEventListener("click", async () => {
        send.disabled = true;
        UI.setLabel(send, "send", "Sending");
        try { await post("/api/usage/send"); } catch (e) { /* said below on the next read */ }
        await sleep(1500);
        try { usage = await get("/api/usage"); } catch (e) { /* keep the last */ }
        render();
      });
      h.append(edit, send);
      const dl = el("dl", "set-facts");
      fact(dl, "Name", s.name || "—");
      fact(dl, "Install ID", s.install_id || "—", true);
      fact(dl, "Reporting to", s.reporting_to || "—", true);
      fact(dl, "Last sent", s.sending ? "Sending" : when(s.last_sent));
      fact(dl, "Waiting", String(s.waiting || 0));
      sec.appendChild(dl);
      if (s.error) notice(sec, "Not sent: " + s.error);
    };
    render();
  }

  function updatesSection(sec) {
    const render = () => {
      sec.innerHTML = "";
      const h = el("div", "set-h");
      const title = el("span", "set-title", "Updates");
      title.dataset.tip = "Runway checks for a newer version each time it starts and offers it once; Later leaves an Update chip in the top bar. Hold Shift while starting Runway.exe to start an earlier version.";
      h.appendChild(title);
      sec.appendChild(h);
      const s = upd;
      if (!s) { notice(sec, "Updates are starting"); return; }
      if (s.stage === "disabled") { notice(sec, s.disabled_reason || "Updates are off in this build", "muted"); return; }
      h.appendChild(el("span", "spacer"));
      const check = UI.setLabel(el("button", "ghost mini"), "refresh", s.stage === "checking" ? "Checking" : "Check now");
      check.type = "button";
      check.disabled = ["checking", "downloading", "installing", "restarting"].includes(s.stage);
      check.addEventListener("click", async () => {
        check.disabled = true;
        UI.setLabel(check, "refresh", "Checking");
        try { upd = await post("/api/update/check"); } catch (e) { /* said on the next read */ }
        for (let i = 0; i < 70 && upd && upd.stage === "checking"; i++) { await sleep(1000); await refreshUpdate(); }
        render();
        if (upd && upd.available) openOffer();
      });
      h.appendChild(check);
      const dl = el("dl", "set-facts");
      fact(dl, "Version", s.current);
      const latest = s.available ? `${s.latest} available` : s.stage === "up_to_date" ? "Up to date" : (s.latest || "—");
      fact(dl, "Latest", latest);
      fact(dl, "Last checked", s.stage === "checking" ? "Checking" : when(s.last_checked));
      fact(dl, "Channel", (() => { try { return new URL(s.channel).host + new URL(s.channel).pathname.split("/").slice(0, 3).join("/"); } catch (e) { return "—"; } })(), true);
      sec.appendChild(dl);
      if (s.available) {
        const open = UI.setLabel(el("button", ""), "download", `Update to ${s.latest}`);
        open.type = "button";
        open.className = "set-cta";
        open.addEventListener("click", () => {
          const settings = document.getElementById("settings-overlay");
          if (settings) settings.remove();
          openOffer();
        });
        sec.appendChild(open);
      }
      if (s.stage === "failed" && s.error) notice(sec, "Not checked: " + s.error);
    };
    render();
  }

  UI.settingsSections.push(usageSection, updatesSection);

  // ---- start ---------------------------------------------------------------------------------------
  (async () => {
    try { usage = await get("/api/usage"); } catch (e) { usage = null; }
    greet();
    if (usage && usage.needs_name) await askName(false);     // the update offer waits for the name
    await refreshUpdate();
    // the first check runs at start-up; follow it briefly so a fresh start can offer at once
    for (let i = 0; i < 40 && upd && upd.stage === "checking"; i++) { await sleep(1500); await refreshUpdate(); }
    await maybeOffer();
    if (upd && ["downloading", "installing", "restarting"].includes(upd.stage)) { following = true; follow(); }
    setInterval(refreshUpdate, 60000);
  })();
})();
