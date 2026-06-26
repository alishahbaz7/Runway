/* Runway - log viewer: tail data/runway.log with filter, level, auto-refresh, download, clear. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let lines = [];
  let timer = null;

  const levelOf = (l) => (/\bERROR\b/.test(l) ? "ERROR"
    : /\bWARN/.test(l) ? "WARN"
    : /\bDEBUG\b/.test(l) ? "DEBUG" : "INFO");

  const source = () => $("log-source").value;

  async function load() {
    try {
      const d = await fetch(`/api/logs?limit=2000&source=${source()}`).then((r) => r.json());
      lines = d.lines || [];
      $("log-info").textContent = `${lines.length} lines · ${(d.size / 1024).toFixed(1)} KB`;
      render();
    } catch (e) {
      $("log-view").className = "empty"; $("log-view").textContent = "failed to load log: " + e.message;
    }
  }

  function render() {
    const q = ($("log-search").value || "").toLowerCase();
    const lvl = $("log-level").value;
    const view = $("log-view");
    const show = lines.filter((l) => {
      if (q && !l.toLowerCase().includes(q)) return false;
      const L = levelOf(l);
      if (lvl === "ERROR" && L !== "ERROR") return false;
      if (lvl === "WARN" && !(L === "ERROR" || L === "WARN")) return false;
      return true;
    });
    view.innerHTML = "";
    if (!show.length) {
      view.className = "empty";
      view.textContent = lines.length ? "no matching lines" : "log is empty";
      return;
    }
    view.className = "";
    const frag = document.createDocumentFragment();
    show.forEach((l) => {
      const div = document.createElement("div");
      div.className = "log-line lvl-" + levelOf(l);
      div.textContent = l;
      frag.appendChild(div);
    });
    view.appendChild(frag);
    view.scrollTop = view.scrollHeight;   // newest at the bottom
  }

  $("log-refresh").addEventListener("click", load);
  $("log-search").addEventListener("input", render);
  $("log-level").addEventListener("change", render);
  $("log-source").addEventListener("change", load);
  $("log-download").addEventListener("click", () => { window.location.href = `/api/logs/download?source=${source()}`; });
  $("log-clear").addEventListener("click", async () => {
    const what = source() === "errors" ? "kept errors & warnings log" : "activity log";
    if (!confirm(`Clear the ${what}?`)) return;
    try { await fetch(`/api/logs?source=${source()}`, { method: "DELETE" }); } catch (e) { /* ignore */ }
    load();
  });
  $("log-auto").addEventListener("change", () => {
    if ($("log-auto").checked) timer = setInterval(load, 3000);
    else { clearInterval(timer); timer = null; }
  });

  load();
})();
