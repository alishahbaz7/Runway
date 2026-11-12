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

  // the one icon map every page shares (shell.js)
  const ICON = window.RunwayUI.icons;
  const EMPTY_VIEW = '<p class="empty">No signature selected</p>';

  let allSigs = [];        // cached repository listing (for search)
  let selected = null;     // name of the saved file being previewed
  let pending = null;      // { content } chosen via upload but not yet saved
  let currentName = null;  // what the preview currently shows (for Download)
  let currentContent = null;
  const msg = (cls, txt) => { const m = $("sig-msg"); m.className = cls; m.textContent = txt; };

  // ---- repository list (left) -------------------------------------------
  async function refresh() {
    try { allSigs = await api("/api/signatures"); } catch (e) { allSigs = []; }
    $("sig-count").textContent = allSigs.length ? String(allSigs.length) : "";
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
        `${s.keys} keys · ${s.fields} fields${s.valid ? "" : " · Invalid"} · ${fmtTime(s.modified)}`));
      const del = el("button", "ui-icon-btn");
      del.type = "button";
      del.innerHTML = ICON.trash;
      del.title = "Delete";
      del.setAttribute("aria-label", "Delete " + s.name);
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
          el("td", "", f.signed ? "Yes" : "No"), el("td", "num", f.reverse),
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
    $("sig-view-head").innerHTML = EMPTY_VIEW;
    $("sig-view-body").innerHTML = "";
    setActions();
    renderList($("sig-search").value || "");
  }

  // ---- device binding -----------------------------------------------------------------
  // A signature bound to a device decodes THAT device's CAN frames on every path — live
  // listener, QC session, replay and pasted data — because the decode happens in the pipeline
  // before any view sees the record. Only CAN-id keyed signatures can bind: an AVL-id keyed one
  // slices a Teltonika parameter's bytes, which is the Parser's job, not a bus frame's.
  const isCanKeyed = (content) => {
    const keys = Object.keys(content || {}).filter((k) => Array.isArray(content[k]));
    if (!keys.length || !keys.every((k) => /^(0x)?[0-9a-fA-F]{1,8}$/.test(k))) return false;
    return keys.some((k) => k.replace(/^0x/i, "").length >= 5 || /[a-fA-F]/.test(k));
  };

  async function bindPanel(name, content) {
    const wrap = el("div", "sig-bind");
    if (!isCanKeyed(content)) {
      const kind = el("span", "ui-pill muted", "AVL parameter signature");
      kind.dataset.tip = "Applied from the Parser's Signature control. Only a signature keyed by CAN id binds to a device.";
      wrap.append(kind);
      return wrap;
    }
    let data = { bindings: {} };
    try { data = await api("/api/signature-bindings"); } catch (e) { /* offline */ }
    const bound = Object.entries(data.bindings || {}).filter(([, v]) => v === name).map(([k]) => k);
    wrap.append(el("span", "addbar-label", "Decode CAN for device"));
    const input = el("input", "conv-in");
    input.placeholder = "IMEI, IMEI or *";
    input.value = bound.join(", ");
    input.title = "Device IMEIs, separated by commas. * binds every device that has no binding of its own.";
    const btn = el("button", "ghost", "Bind");
    btn.type = "button";
    const msg = el("span", "muted");
    btn.onclick = async () => {
      const wanted = input.value.split(",").map((x) => x.trim()).filter(Boolean);
      try {
        const cur = (await api("/api/signature-bindings")).bindings || {};
        // this signature owns exactly the devices listed; other signatures' bindings stay
        Object.keys(cur).forEach((k) => { if (cur[k] === name) delete cur[k]; });
        wanted.forEach((k) => { cur[k] = name; });
        await api("/api/signature-bindings", {
          method: "PUT", headers: { "content-type": "application/json" },
          body: JSON.stringify({ bindings: cur }),
        });
        msg.className = "ok";
        msg.textContent = wanted.length ? `Bound to ${wanted.join(", ")} from the next packet` : "Unbound";
      } catch (e) { msg.className = "err"; msg.textContent = "Could not bind: " + e.message; }
    };
    wrap.append(input, btn, msg);
    return wrap;
  }

  async function viewSaved(name) {
    pending = null; selected = name;
    renderList($("sig-search").value || "");
    try {
      const d = await api("/api/signatures/" + encodeURIComponent(name));
      currentName = d.name; currentContent = d.content;
      setActions();
      renderFields(d.content, [el("h2", "", d.name), await bindPanel(d.name, d.content)]);
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
    const banner = el("span", "sig-banner", "Unsaved");
    banner.dataset.tip = "Save adds it to the repository";
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
      catch (err) { msg("err", "Not valid JSON: " + err.message); return; }
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
    if (!name) { msg("err", "Enter a name"); return; }
    if (!pending) { msg("err", "Choose a file first"); return; }
    const ok = await window.RunwayUI.confirm({
      title: "Save to the repository?",
      text: `“${name}” is written to the signature repository.`, action: "Save" });
    if (!ok) return;
    try {
      await api("/api/signatures", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, content: pending.content }),
      });
      msg("ok", "Saved");
      $("sig-name").value = ""; $("sig-file-name").textContent = "";
      pending = null;
      await refresh();
      viewSaved(/\.json$/i.test(name) ? name : name + ".json");
    } catch (e) { msg("err", "Save failed: " + e.message); }
  });

  async function removeSig(name) {
    const ok = await window.RunwayUI.confirm({
      title: "Delete this signature?",
      text: `“${name}” is removed from the repository for good.`, action: "Delete", tone: "danger" });
    if (!ok) return;
    try { await api("/api/signatures/" + encodeURIComponent(name), { method: "DELETE" }); }
    catch (e) { /* ignore */ }
    if (selected === name) {
      selected = null; currentName = null; currentContent = null;
      $("sig-view-head").innerHTML = EMPTY_VIEW;
      $("sig-view-body").innerHTML = "";
      setActions();
    }
    await refresh();
  }

  // band actions (next to Save)
  $("sig-download").addEventListener("click", () => { if (currentContent) download(currentName, currentContent); });
  $("sig-delete").addEventListener("click", () => { if (selected) removeSig(selected); });
  $("sig-clear").addEventListener("click", clearPreview);
  $("sig-search").addEventListener("input", () => renderList($("sig-search").value || ""));

  refresh();
})();
