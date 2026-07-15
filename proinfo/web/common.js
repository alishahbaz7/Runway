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

  // Feed-row date subtitle: DD-MMM-YYYY in the browser's local time.
  const _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtDate(t) {
    const d = new Date(t);
    if (isNaN(d.getTime())) return "";
    return `${String(d.getDate()).padStart(2, "0")}-${_MON[d.getMonth()]}-${d.getFullYear()}`;
  }

  // The browser's local timezone abbreviation (e.g. "IST", "UTC") — feed times render in
  // local time, so the Timestamp header is labelled with it once. Some locales report the
  // short zone as an offset ("GMT+5:30"); in that case derive initials from the long name
  // ("India Standard Time" -> "IST"), which is stable across locales.
  const TZ_LABEL = (() => {
    try {
      const now = new Date();
      const nameOf = (style) => {
        const p = new Intl.DateTimeFormat(undefined, { timeZoneName: style })
          .formatToParts(now).find((x) => x.type === "timeZoneName");
        return p ? p.value : "";
      };
      const short = nameOf("short");
      if (/^[A-Za-z]{2,5}$/.test(short)) return short;      // already an abbreviation
      const initials = nameOf("long").split(/\s+/)
        .filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).join("");
      return initials.length >= 2 ? initials : short;       // "India Standard Time" -> "IST"
    } catch (e) { return ""; }
  })();

  // Classify a decoded record for the feed's colour coding (see the ⓘ legend). Precedence:
  // an alarm packet also carries a position, but the alarm is what matters, so it wins.
  //   alarm   (violet) — record has any alarms[]
  //   unknown (white)  — undecoded: message_type raw/unknown, or the generic_hex fallback
  //   history (yellow) — AIS-140 buffered data (io.packet_status "H"); only protocol with the flag
  //   live    (green)  — real-time position (packet_status "L", or a normal position packet)
  //   other   (neutral)— protocol housekeeping: login/heartbeat/health/ack/command/response
  const POS_TYPES = ["normal", "location", "avl", "rmc", "gga"];
  function packetKind(r) {
    if (r.alarms && r.alarms.length) return "alarm";
    const mt = (r.message_type || "").toLowerCase();
    if (mt === "raw" || mt === "unknown" || r.protocol === "generic_hex") return "unknown";
    const ps = String((r.io && r.io.packet_status) || "").trim().toUpperCase();
    if (ps === "H") return "history";
    if (ps === "L") return "live";
    return POS_TYPES.includes(mt) ? "live" : "other";
  }

  // idle from a server epoch-seconds timestamp (per-device "last packet" time)
  const fmtIdle = (lastSec) => (!lastSec ? "—" : Math.max(0, Math.round(Date.now() / 1000 - lastSec)) + "s");

  window.Runway = { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull,
                    fmtDate, TZ_LABEL, packetKind, fmtIdle };
})();
