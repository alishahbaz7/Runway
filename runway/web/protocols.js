/* Runway - Protocols repository: transferable parser files in any format (.py, .json,
 * .yaml, …). Upload/add → preview → save; browse/search/edit/download/delete.
 * Backend: /api/protocols (same repository mechanism as signatures, format-agnostic). */
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
  const fmtSize = (n) => (n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB"
    : (n / 1048576).toFixed(1) + " MB");
  const hasExt = (n) => /\.[A-Za-z0-9]+$/.test(n);
  const resolveName = (n) => (hasExt(n) ? n : n + ".json");

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
    edit: SVG('<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
      + '<path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>'),
    x: SVG('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  };

  let allProtos = [];      // cached repository listing (for search)
  let selected = null;     // name of the saved file being previewed
  let pending = null;      // { name, text } chosen via upload but not yet saved
  let currentName = null;  // what the preview currently shows
  let currentText = null;  // raw file text (for Download / Edit)
  let currentContent = null;  // parsed object when the file is JSON, else null
  let editing = false;     // true while the in-place editor is open
  const msg = (cls, txt) => { const m = $("proto-msg"); m.className = cls; m.textContent = txt; };
  const kindOf = (name) => (name.split(".").pop() || "file").toLowerCase();

  // ---- repository list (left) -------------------------------------------
  async function refresh() {
    try { allProtos = await api("/api/protocols"); } catch (e) { allProtos = []; }
    renderList($("proto-search").value || "");
  }
  function renderList(q) {
    q = q.toLowerCase();
    const ul = $("proto-list");
    ul.innerHTML = "";
    const list = allProtos.filter((s) => s.name.toLowerCase().includes(q));
    if (!list.length) { ul.appendChild(el("li", "empty", allProtos.length ? "No matches" : "No protocols yet")); return; }
    list.forEach((s) => {
      const li = el("li", "proto-item" + (s.name === selected ? " active" : ""));
      const body = el("div", "proto-body");
      body.append(el("div", "proto-name-row", s.name));
      const detail = (s.kind || "file").toUpperCase() + " · " + fmtSize(s.size)
        + (s.kind === "json" && s.valid ? ` · ${s.keys} keys` : "")
        + (s.valid ? "" : " · ⚠ invalid") + " · " + fmtTime(s.modified);
      body.append(el("div", "meta", detail));
      const exp = el("button", "ghost mini icon-only");
      exp.innerHTML = ICON.download;
      exp.title = "export (bundles any other file sharing this protocol's name, e.g. .json + .py)";
      exp.onclick = (e) => { e.stopPropagation(); exportBundle(s.name); };
      const del = el("button", "ghost mini icon-only");
      del.innerHTML = ICON.trash;
      del.title = "delete";
      del.onclick = (e) => { e.stopPropagation(); removeProto(s.name); };
      li.append(body, exp, del);
      li.onclick = () => viewSaved(s.name);
      ul.appendChild(li);
    });
  }

  // ---- preview (right) ---------------------------------------------------
  // JSON files get a structured table view; any other format is shown as code.
  function renderArtifact(name, text, content, headExtras) {
    editing = false;
    currentName = name; currentText = text; currentContent = content;
    if (content && typeof content === "object") renderFields(content, headExtras);
    else renderCode(text, headExtras);
    setActions();
  }

  function head(headExtras) {
    const h = $("proto-view-head");
    h.innerHTML = "";
    h.append(...headExtras);
  }
  function kindTag(name) { return el("span", "tag", kindOf(name).toUpperCase()); }

  function renderCode(text, headExtras) {
    head(headExtras);
    const body = $("proto-view-body");
    body.innerHTML = "";
    const pre = el("pre", "json");          // monospace code block (reuses .json styling)
    pre.textContent = text || "";
    body.appendChild(pre);
  }

  function renderFields(content, headExtras) {
    head(headExtras);
    const body = $("proto-view-body");
    body.innerHTML = "";
    const obj = content && typeof content === "object" ? content : {};

    const props = Object.keys(obj).filter((k) => !Array.isArray(obj[k]));
    if (props.length) {
      body.appendChild(el("div", "proto-key", "Properties"));
      const t = el("table", "proto-table");
      const tb = el("tbody");
      props.forEach((k) => {
        const tr = el("tr");
        const v = obj[k];
        tr.append(el("td", "", k), el("td", "", typeof v === "object" ? JSON.stringify(v) : String(v)));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      body.appendChild(t);
    }

    Object.keys(obj).filter((k) => Array.isArray(obj[k])).forEach((k) => {
      const rows = obj[k];
      body.appendChild(el("div", "proto-key", k));
      const cols = [];
      rows.forEach((r) => (r && typeof r === "object" ? Object.keys(r) : []).forEach((c) => {
        if (!cols.includes(c)) cols.push(c);
      }));
      const t = el("table", "proto-table");
      const htr = el("tr");
      (cols.length ? cols : ["value"]).forEach((c) => htr.appendChild(el("th", "", c)));
      const thead = el("thead"); thead.appendChild(htr); t.appendChild(thead);
      const tb = el("tbody");
      rows.forEach((r) => {
        const tr = el("tr");
        if (cols.length) {
          cols.forEach((c) => {
            const v = r ? r[c] : undefined;
            tr.appendChild(el("td", "", v == null ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v))));
          });
        } else {
          tr.appendChild(el("td", "", typeof r === "object" ? JSON.stringify(r) : String(r)));
        }
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      body.appendChild(t);
    });

    body.appendChild(el("h2", "", "Raw"));
    body.appendChild(el("pre", "json", JSON.stringify(content, null, 2)));
  }

  // ---- in-place editor (Edit) --------------------------------------------
  function openEditor(name, text) {
    editing = true;
    head([el("h2", "", name), kindTag(name), el("span", "spacer"),
      el("span", "proto-banner", "Editing — press Save to write to the repository")]);
    const body = $("proto-view-body");
    body.innerHTML = "";
    const ta = el("textarea", "proto-editor");
    ta.id = "proto-editor";
    ta.value = text || "";
    body.appendChild(ta);
    setActions();
    ta.focus();
  }

  function setActions() {
    $("proto-edit").disabled = currentText == null || editing;
    $("proto-download").disabled = currentText == null;
    $("proto-clear").disabled = currentText == null;
    $("proto-delete").disabled = !selected;       // only a saved file can be deleted
  }
  function clearPreview() {
    pending = null; selected = null; currentName = null; currentText = null; currentContent = null; editing = false;
    $("proto-name").value = ""; $("proto-file-name").textContent = "";
    $("proto-view-head").innerHTML = '<p class="empty">Upload, select or add a protocol to preview it</p>';
    $("proto-view-body").innerHTML = "";
    setActions();
    renderList($("proto-search").value || "");
  }

  async function viewSaved(name) {
    pending = null; selected = name; editing = false;
    renderList($("proto-search").value || "");
    try {
      const d = await api("/api/protocols/" + encodeURIComponent(name));
      renderArtifact(d.name, d.text, d.content, [el("h2", "", d.name), kindTag(d.name)]);
    } catch (e) {
      currentText = null; currentContent = null; setActions();
      $("proto-view-body").innerHTML = "";
      $("proto-view-head").textContent = "Failed to load: " + e.message;
    }
  }

  function previewPending() {
    selected = null; editing = false;
    const name = resolveName($("proto-name").value.trim() || pending.name || "new-protocol");
    let content = null;
    if (kindOf(name) === "json") { try { content = JSON.parse(pending.text); } catch (e) { /* show as text */ } }
    renderList($("proto-search").value || "");
    renderArtifact(name, pending.text, content,
      [el("h2", "", name), kindTag(name), el("span", "spacer"),
       el("span", "proto-banner", "Unsaved — press Save to add to the repository")]);
  }

  function download(name, text) {
    const fname = resolveName(name);
    const blob = new Blob([text ?? ""], { type: "text/plain" });
    const a = el("a"); a.href = URL.createObjectURL(blob); a.download = fname;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  }

  // ---- export / import (instant transfer between systems) ----------------
  function triggerDownload(url) {
    const a = el("a"); a.href = url; a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
  }
  function exportBundle(name) {
    triggerDownload("/api/protocols/export?name=" + encodeURIComponent(name));
  }

  // ---- add / save / delete ----------------------------------------------
  $("proto-upload").addEventListener("click", () => $("proto-file").click());
  $("proto-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      pending = { name: f.name, text: String(reader.result || "") };
      $("proto-file-name").textContent = f.name;
      if (!$("proto-name").value) $("proto-name").value = f.name;   // keep the extension (format matters)
      msg("", "");
      previewPending();
    };
    reader.readAsText(f);
    e.target.value = "";
  });

  $("proto-save").addEventListener("click", async () => {
    const name = $("proto-name").value.trim();
    if (!name) { msg("err", "enter a name (with extension, e.g. acme.py or acme.json)"); return; }
    let text;
    if (editing) text = $("proto-editor").value;
    else if (pending) text = pending.text;
    else { msg("err", "choose a file or click Edit first"); return; }
    if (kindOf(resolveName(name)) === "json") {
      try { JSON.parse(text); } catch (err) { msg("err", "not valid JSON: " + err.message); return; }
    }
    if (!window.confirm(`Save protocol “${resolveName(name)}” to the repository?`)) return;
    try {
      await api("/api/protocols", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, content: text }),
      });
      msg("ok", "saved");
      $("proto-file-name").textContent = "";
      pending = null; editing = false;
      await refresh();
      viewSaved(resolveName(name));
    } catch (e) { msg("err", "save failed: " + e.message); }
  });

  async function removeProto(name) {
    if (!window.confirm(`Delete protocol “${name}”?\n\n`
        + "This permanently removes it from the repository and cannot be undone.")) return;
    try { await api("/api/protocols/" + encodeURIComponent(name), { method: "DELETE" }); }
    catch (e) { /* ignore */ }
    if (selected === name) {
      selected = null; currentName = null; currentText = null; currentContent = null; editing = false;
      $("proto-view-head").innerHTML = '<p class="empty">Upload, select or add a protocol to preview it</p>';
      $("proto-view-body").innerHTML = "";
      setActions();
    }
    await refresh();
  }

  // icon labels
  $("proto-upload").innerHTML = ICON.upload + "<span>Choose file</span>";
  $("proto-import").innerHTML = ICON.upload + "<span>Import .zip</span>";
  $("proto-edit").innerHTML = ICON.edit + "<span>Edit</span>";
  $("proto-download").innerHTML = ICON.download + "<span>Download</span>";
  $("proto-export-all").innerHTML = ICON.download + "<span>Export all</span>";
  $("proto-delete").innerHTML = ICON.trash + "<span>Delete</span>";
  $("proto-clear").innerHTML = ICON.x + "<span>Clear</span>";

  // band actions (next to Save)
  $("proto-edit").addEventListener("click", () => { if (currentText != null) openEditor(currentName, currentText); });
  $("proto-download").addEventListener("click", () => { if (currentText != null) download(currentName, currentText); });
  $("proto-export-all").addEventListener("click", () => triggerDownload("/api/protocols/export"));
  $("proto-delete").addEventListener("click", () => { if (selected) removeProto(selected); });
  $("proto-clear").addEventListener("click", clearPreview);
  $("proto-search").addEventListener("input", () => renderList($("proto-search").value || ""));

  // ---- import a .zip (from Export / Export all on another system) --------
  $("proto-import").addEventListener("click", () => $("proto-import-file").click());
  $("proto-import-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (!window.confirm(`Import protocols from "${f.name}"? Files with matching names will be overwritten.`)) return;
    try {
      const fd = new FormData();
      fd.append("file", f);
      const result = await api("/api/protocols/import", { method: "POST", body: fd });
      const n = result.imported.length, skipped = result.skipped.length;
      msg(skipped ? "err" : "ok",
        `imported ${n} file(s)` + (skipped ? `, skipped ${skipped}: ${result.skipped.map((s) => s.name).join(", ")}` : ""));
      await refresh();
    } catch (err) { msg("err", "import failed: " + err.message); }
  });

  refresh();
})();
