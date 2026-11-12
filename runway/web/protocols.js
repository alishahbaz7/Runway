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

  // the one icon map every page shares (shell.js)
  const ICON = window.RunwayUI.icons;
  const EMPTY_VIEW = '<p class="empty">No protocol selected</p>';

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
    $("proto-count").textContent = allProtos.length ? String(allProtos.length) : "";
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
        + (s.valid ? "" : " · Invalid") + " · " + fmtTime(s.modified);
      body.append(el("div", "meta", detail));
      const exp = el("button", "ui-icon-btn");
      exp.type = "button";
      exp.innerHTML = ICON.download;
      exp.title = "Export (with any other file of the same name, such as .json and .py)";
      exp.setAttribute("aria-label", "Export " + s.name);
      exp.onclick = (e) => { e.stopPropagation(); exportBundle(s.name); };
      const del = el("button", "ui-icon-btn");
      del.type = "button";
      del.innerHTML = ICON.trash;
      del.title = "Delete";
      del.setAttribute("aria-label", "Delete " + s.name);
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
  function kindTag(name) { return el("span", "ui-pill accent", kindOf(name).toUpperCase()); }   // a file type, as written

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
    const banner = el("span", "proto-banner", "Editing");
    banner.dataset.tip = "Save writes it to the repository";
    head([el("h2", "", name), kindTag(name), el("span", "spacer"), banner]);
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
    $("proto-view-head").innerHTML = EMPTY_VIEW;
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
    const banner = el("span", "proto-banner", "Unsaved");
    banner.dataset.tip = "Save adds it to the repository";
    renderArtifact(name, pending.text, content,
      [el("h2", "", name), kindTag(name), el("span", "spacer"), banner]);
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
    if (!name) { msg("err", "Enter a name with its extension, such as acme.py"); return; }
    let text;
    if (editing) text = $("proto-editor").value;
    else if (pending) text = pending.text;
    else { msg("err", "Choose a file or press Edit first"); return; }
    if (kindOf(resolveName(name)) === "json") {
      try { JSON.parse(text); } catch (err) { msg("err", "Not valid JSON: " + err.message); return; }
    }
    const ok = await window.RunwayUI.confirm({
      title: "Save to the repository?",
      text: `“${resolveName(name)}” is written to the protocol repository.`, action: "Save" });
    if (!ok) return;
    try {
      await api("/api/protocols", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, content: text }),
      });
      msg("ok", "Saved");
      $("proto-file-name").textContent = "";
      pending = null; editing = false;
      await refresh();
      viewSaved(resolveName(name));
    } catch (e) { msg("err", "Save failed: " + e.message); }
  });

  async function removeProto(name) {
    const ok = await window.RunwayUI.confirm({
      title: "Delete this protocol?",
      text: `“${name}” is removed from the repository for good.`, action: "Delete", tone: "danger" });
    if (!ok) return;
    try { await api("/api/protocols/" + encodeURIComponent(name), { method: "DELETE" }); }
    catch (e) { /* ignore */ }
    if (selected === name) {
      selected = null; currentName = null; currentText = null; currentContent = null; editing = false;
      $("proto-view-head").innerHTML = EMPTY_VIEW;
      $("proto-view-body").innerHTML = "";
      setActions();
    }
    await refresh();
  }

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
    const ok = await window.RunwayUI.confirm({
      title: "Import these protocols?",
      text: `Every file in “${f.name}” is added; one with the same name as a protocol already here replaces it.`,
      action: "Import" });
    if (!ok) return;
    try {
      const fd = new FormData();
      fd.append("file", f);
      const result = await api("/api/protocols/import", { method: "POST", body: fd });
      const n = result.imported.length, skipped = result.skipped.length;
      msg(skipped ? "err" : "ok",
        `Imported ${n} ${n === 1 ? "file" : "files"}` + (skipped ? `, skipped ${skipped}: ${result.skipped.map((s) => s.name).join(", ")}` : ""));
      await refresh();
    } catch (err) { msg("err", "Import failed: " + err.message); }
  });

  refresh();
})();
