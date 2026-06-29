/* Runway - Protocols repository: upload/add → preview → save; browse/search/edit/download/delete.
 * Mirrors the Signatures page; backend uses the same repository mechanism (/api/protocols). */
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
    edit: SVG('<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
      + '<path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>'),
    x: SVG('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  };

  let allProtos = [];      // cached repository listing (for search)
  let selected = null;     // name of the saved file being previewed
  let pending = null;      // { content } chosen via upload but not yet saved
  let currentName = null;  // what the preview currently shows (for Download)
  let currentContent = null;
  let editing = false;     // true while the in-place JSON editor is open
  const msg = (cls, txt) => { const m = $("proto-msg"); m.className = cls; m.textContent = txt; };

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
      body.append(el("div", "meta",
        `${s.keys} keys · ${s.fields} fields${s.valid ? "" : " · ⚠ invalid"} · ${fmtTime(s.modified)}`));
      const del = el("button", "ghost mini icon-only");
      del.innerHTML = ICON.trash;
      del.title = "delete";
      del.onclick = (e) => { e.stopPropagation(); removeProto(s.name); };
      li.append(body, del);
      li.onclick = () => viewSaved(s.name);
      ul.appendChild(li);
    });
  }

  // ---- preview (right) — generic JSON renderer (any protocol shape) ------
  function renderFields(content, headExtras) {
    editing = false;
    const head = $("proto-view-head");
    head.innerHTML = "";
    head.append(...headExtras);
    const body = $("proto-view-body");
    body.innerHTML = "";
    const obj = content && typeof content === "object" ? content : {};

    // Scalar / object top-level entries → one "Properties" table.
    const props = Object.keys(obj).filter((k) => !Array.isArray(obj[k]));
    if (props.length) {
      body.appendChild(el("div", "proto-key", "Properties"));
      const t = el("table", "proto-table");
      const tb = el("tbody");
      props.forEach((k) => {
        const tr = el("tr");
        const v = obj[k];
        tr.append(el("td", "", k),
          el("td", "", typeof v === "object" ? JSON.stringify(v) : String(v)));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      body.appendChild(t);
    }

    // Each array-of-objects entry → its own table (union of keys as columns).
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

    body.appendChild(el("h2", "", "Raw JSON"));
    body.appendChild(el("pre", "json", JSON.stringify(content, null, 2)));
  }

  // ---- in-place editor (Edit / Add new) ----------------------------------
  function openEditor(name, content) {
    editing = true;
    selected = (name && allProtos.some((p) => p.name === name)) ? name : selected;
    currentName = name || ($("proto-name").value.trim() || "new-protocol");
    currentContent = content;
    $("proto-name").value = (currentName || "").replace(/\.json$/i, "");
    const head = $("proto-view-head");
    head.innerHTML = "";
    head.append(el("h2", "", currentName),
      el("span", "spacer"),
      el("span", "proto-banner", "Editing — press Save to write to the repository"));
    const body = $("proto-view-body");
    body.innerHTML = "";
    const ta = el("textarea", "proto-editor");
    ta.id = "proto-editor";
    ta.value = JSON.stringify(content ?? {}, null, 2);
    body.appendChild(ta);
    setActions();
    ta.focus();
  }

  // The Edit / Download / Delete / Clear actions live in the top band.
  function setActions() {
    $("proto-edit").disabled = currentContent == null || editing;
    $("proto-download").disabled = currentContent == null;
    $("proto-clear").disabled = currentContent == null;
    $("proto-delete").disabled = !selected;       // only a saved file can be deleted
  }
  function clearPreview() {
    pending = null; selected = null; currentName = null; currentContent = null; editing = false;
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
      currentName = d.name; currentContent = d.content;
      setActions();
      renderFields(d.content, [el("h2", "", d.name)]);
    } catch (e) {
      currentContent = null; setActions();
      $("proto-view-body").innerHTML = "";
      $("proto-view-head").textContent = "Failed to load: " + e.message;
    }
  }

  function previewPending() {
    selected = null; editing = false;
    currentName = $("proto-name").value.trim() || "new-protocol";
    currentContent = pending.content;
    setActions();
    renderList($("proto-search").value || "");
    const title = el("h2", "", currentName);
    const spacer = el("span", "spacer");
    const banner = el("span", "proto-banner", "Unsaved — press Save to add to the repository");
    renderFields(pending.content, [title, spacer, banner]);
  }

  function download(name, content) {
    const fname = /\.json$/i.test(name) ? name : name + ".json";
    const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
    const a = el("a"); a.href = URL.createObjectURL(blob); a.download = fname;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  }

  // ---- add / save / delete ----------------------------------------------
  $("proto-upload").addEventListener("click", () => $("proto-file").click());
  $("proto-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      let content;
      try { content = JSON.parse(String(reader.result || "")); }
      catch (err) { msg("err", "not valid JSON: " + err.message); return; }
      pending = { content };
      $("proto-file-name").textContent = f.name;
      if (!$("proto-name").value) $("proto-name").value = f.name.replace(/\.json$/i, "");
      msg("", "");
      previewPending();
    };
    reader.readAsText(f);
    e.target.value = "";
  });

  $("proto-save").addEventListener("click", async () => {
    const name = $("proto-name").value.trim();
    if (!name) { msg("err", "enter a name"); return; }
    let content;
    if (editing) {
      try { content = JSON.parse($("proto-editor").value || ""); }
      catch (err) { msg("err", "editor is not valid JSON: " + err.message); return; }
    } else if (pending) {
      content = pending.content;
    } else {
      msg("err", "choose a file or click Edit first"); return;
    }
    if (!window.confirm(`Save protocol “${name}” to the repository?`)) return;
    try {
      await api("/api/protocols", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, content }),
      });
      msg("ok", "saved");
      $("proto-file-name").textContent = "";
      pending = null; editing = false;
      await refresh();
      viewSaved(/\.json$/i.test(name) ? name : name + ".json");
    } catch (e) { msg("err", "save failed: " + e.message); }
  });

  async function removeProto(name) {
    if (!window.confirm(`Delete protocol “${name}”?\n\n`
        + "This permanently removes it from the repository and cannot be undone.")) return;
    try { await api("/api/protocols/" + encodeURIComponent(name), { method: "DELETE" }); }
    catch (e) { /* ignore */ }
    if (selected === name) {
      selected = null; currentName = null; currentContent = null; editing = false;
      $("proto-view-head").innerHTML = '<p class="empty">Upload, select or add a protocol to preview it</p>';
      $("proto-view-body").innerHTML = "";
      setActions();
    }
    await refresh();
  }

  // icon labels
  $("proto-upload").innerHTML = ICON.upload + "<span>Choose file</span>";
  $("proto-edit").innerHTML = ICON.edit + "<span>Edit</span>";
  $("proto-download").innerHTML = ICON.download + "<span>Download</span>";
  $("proto-delete").innerHTML = ICON.trash + "<span>Delete</span>";
  $("proto-clear").innerHTML = ICON.x + "<span>Clear</span>";

  // band actions (next to Save)
  $("proto-edit").addEventListener("click", () => { if (currentContent != null) openEditor(currentName, currentContent); });
  $("proto-download").addEventListener("click", () => { if (currentContent != null) download(currentName, currentContent); });
  $("proto-delete").addEventListener("click", () => { if (selected) removeProto(selected); });
  $("proto-clear").addEventListener("click", clearPreview);
  $("proto-search").addEventListener("input", () => renderList($("proto-search").value || ""));

  refresh();
})();
