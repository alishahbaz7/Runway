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

  // ---- CAN payload ---------------------------------------------------------
  // Protocols that carry the vehicle bus inline (Zenithra Ax1 sCAN) put one entry per CAN
  // frame in io.can: {id, id_int, extended, data, dlc}. One packet can carry dozens, so the
  // frames get their own table wherever a packet is inspected — left in the JSON dump they
  // bury every other field. Payloads are shown as raw bytes on purpose: turning them into
  // engineering values needs a per-vehicle DBC/CanPlus signature, not the protocol.
  const canOf = (r) => (r && r.io && Array.isArray(r.io.can) ? r.io.can : []);

  // The wire form is zero-padded to 8 digits (000000E1), which reads as noise when every id
  // starts with five zeros — show 11-bit ids as 0x0E1 and only extended ones in full.
  function canIdLabel(f) {
    const n = f.id_int != null ? f.id_int : parseInt(f.id, 16);
    if (isNaN(n)) return String(f.id || "");
    return "0x" + n.toString(16).toUpperCase().padStart(f.extended ? 8 : 3, "0");
  }

  function canTable(frames) {
    const tbl = el("table", "can-tbl");
    const thead = el("thead"), htr = el("tr");
    ["CAN ID", "", "DLC", "Payload"].forEach((h) => htr.append(el("th", "", h)));
    thead.append(htr); tbl.append(thead);
    const tb = el("tbody");
    (frames || []).forEach((f) => {
      const tr = el("tr");
      tr.append(el("td", "mono can-id", canIdLabel(f)));
      tr.append(el("td", "can-ext", f.extended ? "ext" : ""));
      tr.append(el("td", "num", f.dlc != null ? String(f.dlc) : ""));
      tr.append(el("td", "mono", hexSpaced(String(f.data || ""))));
      tb.append(tr);
    });
    tbl.append(tb);
    const wrap = el("div", "can-wrap");
    wrap.append(tbl);
    return wrap;
  }

  // Named engineering values decoded from those frames by the device's bound vehicle
  // signature (io.can_signals: [{name, value, can_id, raw}]). This is what an operator reads;
  // the raw frames below it are the evidence.
  const canSignalsOf = (r) =>
    (r && r.io && Array.isArray(r.io.can_signals) ? r.io.can_signals : []);

  function canSignalsTable(signals) {
    const tbl = el("table", "can-tbl");
    const thead = el("thead"), htr = el("tr");
    ["Signal", "Value", "CAN ID", "raw"].forEach((h) => htr.append(el("th", "", h)));
    thead.append(htr); tbl.append(thead);
    const tb = el("tbody");
    (signals || []).forEach((s) => {
      const tr = el("tr");
      tr.append(el("td", "", s.name || "?"));
      tr.append(el("td", "mono can-id", String(s.value)));
      tr.append(el("td", "mono can-ext", String(s.can_id || "")));
      tr.append(el("td", "num", s.raw == null ? "" : String(s.raw)));
      tb.append(tr);
    });
    tbl.append(tb);
    const wrap = el("div", "can-wrap");
    wrap.append(tbl);
    return wrap;
  }

  // For the JSON views: keep the frame/signal lists out of the dump (the tables above show
  // them), but never silently — the placeholder says how many rows were folded away.
  function ioWithoutCan(io) {
    if (!io) return io;
    const out = { ...io };
    if (Array.isArray(io.can)) out.can = `[${io.can.length} frames — see the CAN table]`;
    if (Array.isArray(io.can_signals)) {
      out.can_signals = `[${io.can_signals.length} signals — see the CAN signals table]`;
    }
    return out;
  }

  window.Runway = { $, el, api, fmtDur, hexSpaced, hexToAscii, asciiFull,
                    fmtDate, TZ_LABEL, packetKind, fmtIdle,
                    canOf, canIdLabel, canTable, ioWithoutCan,
                    canSignalsOf, canSignalsTable };
})();
