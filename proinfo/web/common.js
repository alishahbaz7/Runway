/* Runway - shared UI helpers for pages that render a live packet feed (tcp.js, qc.js).
 * Exposed as `window.Runway` (plain script, no build step / no module loader). Pure
 * functions only here (no DOM ids, no state) — page-specific wiring stays in each page's
 * own script. */
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
  function fmtDur(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + "h " : "") + (h || m ? m + "m " : "") + sec + "s";
  }

  const hexSpaced = (h) => (h.match(/.{1,2}/g) || []).join(" ").toUpperCase();
  // Feed-row rendering: drop CR, turn LF into a real line break.
  function hexToAscii(h) {
    let s = "";
    for (let i = 0; i + 1 < h.length; i += 2) {
      const b = parseInt(h.substr(i, 2), 16);
      if (b === 13) continue;
      s += (b === 10) ? "\n" : (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
    }
    return s;
  }
  // Full-packet modal: both CR and LF become a line break (nothing silently dropped).
  function asciiFull(hex) {
    let s = "";
    for (let i = 0; i + 1 < hex.length; i += 2) {
      const b = parseInt(hex.substr(i, 2), 16);
      s += (b === 10 || b === 13) ? "\n" : (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
    }
    return s;
  }

  window.Runway = { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull };
})();
