/* Runway - Signatures repository: upload → preview → save; browse/search/download/delete. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  };
  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.status;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
  }
  const fmtTime = (m) => (m ? new Date(m * 1000).toLocaleString() : "");

  // Inline stroke icons (no external libs — CSP-safe).
  const SVG = (p) => `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"`
    + ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  const ICON = {
    trash: SVG('<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
      + '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>'
      + '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'),
    download: SVG('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
      + '<line x1="12" y1="15" x2="12" y2="3"/>'),
    upload: SVG('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/>'
      + '<line x1="12" y1="3" x2="12" y2="15"/>'),
    x: SVG('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  };

  let allSigs = [];        // cached repository listing (for search)
  let selected = null;     // name of the saved file being previewed
  let pending = null;      // { content } chosen via upload but not yet saved
  let currentName = null;  // what the preview currently shows (for Download)
  let currentContent = null;
  const msg = (cls, txt) => { const m = $("sig-msg"); m.className = cls; m.textContent = txt; };

  // ---- repository list (left) -------------------------------------------
  async function refresh() {
    try { allSigs = await api("/api/signatures"); } catch (e) { allSigs = []; }
    renderList($("sig-search").value || "");
  }
  function renderList(q) {
    q = q.toLowerCase();
    const ul = $("sig-list");
    ul.innerHTML = "";
    const list = allSigs.filter((s) => s.name.toLowerCase().includes(q));
    if (!list.length) { ul.appendChild(el("li", "empty", allSigs.length ? "No matches" : "No signatures yet")); return; }
    list.forEach((s) => {
      const li = el("li", "sig-item" + (s.name === selected ? " active" : ""));
      const body = el("div", "sig-body");
      body.append(el("div", "sig-name-row", s.name));
      body.append(el("div", "meta",
        `${s.keys} keys · ${s.fields} fields${s.valid ? "" : " · ⚠ invalid"} · ${fmtTime(s.modified)}`));
      const del = el("button", "ghost mini icon-only");
      del.innerHTML = ICON.trash;
      del.title = "delete";
      del.onclick = (e) => { e.stopPropagation(); removeSig(s.name); };
      li.append(body, del);
      li.onclick = () => viewSaved(s.name);
      ul.appendChild(li);
    });
  }

  // ---- preview (right) ---------------------------------------------------
  function renderFields(content, headExtras) {
    const head = $("sig-view-head");
    head.innerHTML = "";
    head.append(...headExtras);
    const body = $("sig-view-body");
    body.innerHTML = "";
    Object.keys(content || {}).forEach((key) => {
      const fields = Array.isArray(content[key]) ? content[key] : [];
      body.appendChild(el("div", "sig-key", `Parameter ${key}`));
      const table = el("table", "sig-table");
      const htr = el("tr");
      ["Field", "Start bit", "Size", "Mult", "Offset", "Signed", "Reverse"].forEach((h) =>
        htr.appendChild(el("th", "", h)));
      const thead = el("thead"); thead.appendChild(htr); table.appendChild(thead);
      const tb = el("tbody");
      fields.forEach((f) => {
        const tr = el("tr");
        tr.append(
          el("td", "", f.fieldName), el("td", "num", f.startingBit), el("td", "num", f.size),
          el("td", "num", f.multiplier), el("td", "num", f.offset),
          el("td", "", f.signed ? "yes" : "no"), el("td", "num", f.reverse),
        );
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      body.appendChild(table);
    });
    body.appendChild(el("h2", "", "Raw JSON"));
    body.appendChild(el("pre", "json", JSON.stringify(content, null, 2)));
  }

  // The Download / Delete / Clear actions live in the top band; enable them per state.
  function setActions() {
    $("sig-download").disabled = !currentContent;
    $("sig-clear").disabled = !currentContent;
    $("sig-delete").disabled = !selected;        // only a saved file can be deleted
  }
  function clearPreview() {
    pending = null; selected = null; currentName = null; currentContent = null;
    $("sig-view-head").innerHTML = '<p class="empty">Upload or select a signature to preview it</p>';
    $("sig-view-body").innerHTML = "";
    setActions();
    renderList($("sig-search").value || "");
  }

  async function viewSaved(name) {
    pending = null; selected = name;
    renderList($("sig-search").value || "");
    try {
      const d = await api("/api/signatures/" + encodeURIComponent(name));
      currentName = d.name; currentContent = d.content;
      setActions();
      renderFields(d.content, [el("h2", "", d.name)]);
    } catch (e) {
      currentContent = null; setActions();
      $("sig-view-body").innerHTML = "";
      $("sig-view-head").textContent = "Failed to load: " + e.message;
    }
  }

  function previewPending() {
    selected = null;
    currentName = $("sig-name").value.trim() || "new-signature";
    currentContent = pending.content;
    setActions();
    renderList($("sig-search").value || "");
    const title = el("h2", "", currentName);
    const spacer = el("span", "spacer");
    const banner = el("span", "sig-banner", "Unsaved — press Save to add to the repository");
    renderFields(pending.content, [title, spacer, banner]);
  }

  function download(name, content) {
    const fname = /\.json$/i.test(name) ? name : name + ".json";
    const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
    const a = el("a"); a.href = URL.createObjectURL(blob); a.download = fname;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  }

  // ---- add / save / delete ----------------------------------------------
  $("sig-upload").addEventListener("click", () => $("sig-file").click());
  $("sig-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      let content;
      try { content = JSON.parse(String(reader.result || "")); }
      catch (err) { msg("err", "not valid JSON: " + err.message); return; }
      pending = { content };
      $("sig-file-name").textContent = f.name;
      if (!$("sig-name").value) $("sig-name").value = f.name.replace(/\.json$/i, "");
      msg("", "");
      previewPending();
    };
    reader.readAsText(f);
    e.target.value = "";
  });

  $("sig-save").addEventListener("click", async () => {
    const name = $("sig-name").value.trim();
    if (!name) { msg("err", "enter a name"); return; }
    if (!pending) { msg("err", "choose a file first"); return; }
    if (!window.confirm(`Save signature “${name}” to the repository?`)) return;
    try {
      await api("/api/signatures", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, content: pending.content }),
      });
      msg("ok", "saved");
      $("sig-name").value = ""; $("sig-file-name").textContent = "";
      pending = null;
      await refresh();
      viewSaved(/\.json$/i.test(name) ? name : name + ".json");
    } catch (e) { msg("err", "save failed: " + e.message); }
  });

  async function removeSig(name) {
    if (!window.confirm(`Delete signature “${name}”?\n\n`
        + "This permanently removes it from the repository and cannot be undone.")) return;
    try { await api("/api/signatures/" + encodeURIComponent(name), { method: "DELETE" }); }
    catch (e) { /* ignore */ }
    if (selected === name) {
      selected = null; currentName = null; currentContent = null;
      $("sig-view-head").innerHTML = '<p class="empty">Upload or select a signature to preview it</p>';
      $("sig-view-body").innerHTML = "";
      setActions();
    }
    await refresh();
  }

  // icon labels
  $("sig-upload").innerHTML = ICON.upload + "<span>Choose file</span>";
  $("sig-download").innerHTML = ICON.download + "<span>Download</span>";
  $("sig-delete").innerHTML = ICON.trash + "<span>Delete</span>";
  $("sig-clear").innerHTML = ICON.x + "<span>Clear</span>";

  // band actions (next to Save)
  $("sig-download").addEventListener("click", () => { if (currentContent) download(currentName, currentContent); });
  $("sig-delete").addEventListener("click", () => { if (selected) removeSig(selected); });
  $("sig-clear").addEventListener("click", clearPreview);
  $("sig-search").addEventListener("input", () => renderList($("sig-search").value || ""));

  refresh();
})();
