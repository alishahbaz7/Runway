/* Runway - Converter: upload a CanPlus signature XML -> editable signal table ->
 * export to JSON / DBC.  Backend: /api/convert/parse + /api/convert/export. */
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
    return res.json();
  }
  const msg = (cls, txt) => { const m = $("conv-msg"); m.className = cls; m.textContent = txt; };

  let model = null;        // { vehicle, signals: [...] }

  // ---- editable inputs ---------------------------------------------------
  const num = (val, key, w) => {
    const i = el("input", "conv-in");
    i.type = "text"; i.value = (val ?? "");
    i.dataset.key = key; if (w) i.style.width = w + "px";
    return i;
  };
  const hexId = (val) => {
    const i = el("input", "conv-in");
    i.type = "text"; i.style.width = "70px"; i.dataset.key = "can_id";
    i.value = (val == null ? "" : "0x" + Number(val).toString(16).toUpperCase());
    i.placeholder = "0x3AC";
    return i;
  };

  function renderRows(filter) {
    const f = (filter || "").toLowerCase();
    const tb = $("conv-rows");
    tb.innerHTML = "";
    let shown = 0;
    model.signals.forEach((s, idx) => {
      if (f && !(`${s.name} ${s.var_id}`.toLowerCase().includes(f))) return;
      shown++;
      const tr = el("tr");
      tr.dataset.idx = idx;
      tr.append(el("td", "mono", s.var_id));
      const tdName = el("td"); tdName.append(num(s.name, "name", 180)); tr.append(tdName);
      const tdId = el("td"); tdId.append(hexId(s.can_id)); tr.append(tdId);
      const tdStart = el("td"); tdStart.append(num(s.start_bit, "start_bit", 48)); tr.append(tdStart);
      const tdLen = el("td"); tdLen.append(num(s.length, "length", 44)); tr.append(tdLen);

      const tdOrder = el("td");
      const sel = el("select", "conv-in"); sel.dataset.key = "byte_order";
      ["big", "little"].forEach((o) => {
        const op = el("option", "", o === "big" ? "big (Motorola)" : "little (Intel)");
        op.value = o; if ((s.byte_order || "big") === o) op.selected = true; sel.appendChild(op);
      });
      tdOrder.append(sel); tr.append(tdOrder);

      const tdSign = el("td");
      const cb = el("input"); cb.type = "checkbox"; cb.dataset.key = "signed"; cb.checked = !!s.signed;
      tdSign.append(cb); tr.append(tdSign);

      const tdMul = el("td"); tdMul.append(num(s.multiplier, "multiplier", 56)); tr.append(tdMul);
      const tdDiv = el("td"); tdDiv.append(num(s.divider, "divider", 56)); tr.append(tdDiv);
      const tdOff = el("td"); tdOff.append(num(s.offset, "offset", 48)); tr.append(tdOff);
      tr.append(el("td", "num", s.decimals));
      const tdUnit = el("td"); tdUnit.append(num(s.unit, "unit", 56)); tr.append(tdUnit);
      tr.append(el("td", "muted", s.function || ""));
      tb.appendChild(tr);
    });
    if (!shown) tb.appendChild(el("tr")).appendChild(el("td", "empty", "No matches")).colSpan = 13;
  }

  // Pull the (possibly edited) values back out of the table into the model.
  function harvest() {
    $("conv-rows").querySelectorAll("tr[data-idx]").forEach((tr) => {
      const s = model.signals[+tr.dataset.idx];
      tr.querySelectorAll("[data-key]").forEach((inp) => {
        const k = inp.dataset.key;
        if (k === "signed") { s.signed = inp.checked; return; }
        if (k === "byte_order") { s.byte_order = inp.value; return; }
        let v = inp.value.trim();
        if (k === "can_id") {
          s.can_id = v === "" ? null : (v.toLowerCase().startsWith("0x") ? parseInt(v, 16) : parseInt(v, 10));
          if (Number.isNaN(s.can_id)) s.can_id = null;
        } else if (["start_bit", "length", "multiplier", "divider", "offset"].includes(k)) {
          s[k] = v === "" ? 0 : Number(v);
        } else {
          s[k] = v;                         // name, unit
        }
      });
    });
    return model;
  }

  function showModel() {
    const v = model.vehicle || {};
    $("conv-veh").textContent = [v.brand, v.model, v.year].filter(Boolean).join(" ")
      + ` · ${model.signals.length} signals`;
    $("conv-empty").style.display = "none";
    $("conv-table-wrap").style.display = "";
    $("conv-json").disabled = $("conv-dbc").disabled = false;
    renderRows($("conv-search").value);
  }

  // ---- actions -----------------------------------------------------------
  $("conv-upload").addEventListener("click", () => $("conv-file").click());
  $("conv-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        model = await api("/api/convert/parse", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ fmt: "canplus", text: String(reader.result || "") }),
        });
        msg("ok", "loaded " + file.name);
        showModel();
      } catch (err) { msg("err", "parse failed: " + err.message); }
    };
    reader.readAsText(file);
    e.target.value = "";
  });

  async function exportAs(fmt) {
    if (!model) return;
    harvest();
    try {
      const out = await api("/api/convert/export", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ fmt, vehicle: model.vehicle, signals: model.signals }),
      });
      const blob = new Blob([out.content], { type: out.mime || "text/plain" });
      const a = el("a"); a.href = URL.createObjectURL(blob); a.download = out.filename;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
      msg("ok", "exported " + out.filename);
    } catch (err) { msg("err", "export failed: " + err.message); }
  }

  $("conv-json").addEventListener("click", () => exportAs("json"));
  $("conv-dbc").addEventListener("click", () => exportAs("dbc"));
  $("conv-search").addEventListener("input", () => { if (model) renderRows($("conv-search").value); });

  // icon labels
  $("conv-upload").innerHTML = "⤒ <span>Upload XML</span>";
})();
