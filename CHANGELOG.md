# Changelog

All notable changes to **Runway** are recorded here (released as *ProInfo* through v1.1.23 —
see the 1.1.24 entry for the rename).
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [1.1.29] — 2026-11-25

### Added
- **The release and a copy run from source run side by side.** The release keeps port 8080, and a
  run from source (`main.py`) now serves on 8180; `--port` still picks another. Each has its own data
  folder and its own window.
  - A start defers only to a copy of its own kind. Finding the other kind on its port, it says so and
    stops, instead of bringing that copy forward as if it were itself. "Open Runway" brings forward
    that copy's own window.
  - `main.py` never stops a release. This checkout's own copy found on 8080, where it ran before, is
    moved to 8180 with its listeners, relay rules and store.
  - `/api/health` says which kind of copy answers (`channel`).
- **Firmware test has a map.** A *Map* tab in the side rail shows the test's packets on Log
  analysis's map as they land, the same component the Dashboard uses. The server reads the packets
  as a drive log and runs the one `analyse` (`/api/fwtest/runs/{id}/track`, `fwtest/maptrack.py`).
  That gives trips, history dimmed, data lost and asleep dotted, stale positions and jumps dashed,
  and every packet with a device time on it. A file test is drawn by the device's time.
  - A click on the track puts that packet under the map, as a row of the live list; a click on the
    row opens it field by field. A click on a gap or a jump shows its two ends, each one click from
    its packet. The packet chosen in the list is ringed when the map opens.
  - The map is made once and kept: it follows new packets at most every 5 s (worked out again only
    when packets came) and keeps the reader's zoom and place while it does.
- **Live packets: all, or live only.** A switch over the list, each side with its count, remembered
  on this computer. *Live* is every packet but history, including a login, a health packet or an
  emergency packet sent as it happened. It is asked of the server (`packets?status=live`), so a
  history flood never pushes the live packets out of the newest shown.

### Changed
- **A firmware test keeps each packet as the device sent it, as text** (`data/fwtest/<id>/packets.log`).
  Until now every packet was written in hex, which nobody can read without a tool. Each line is now
  the received time, the connection and the packet's own text. Only a byte that cannot stand as text
  is written as an escape: `\r \n \t \\ \xNN`. So CR LF, a missing terminator or a stray control byte
  shows exactly where it is. A packet that is mostly not text (a binary protocol) is `0x<hex>`. The
  file's first line explains the columns for whoever opens it.
  - A log written before is still read, and is rewritten as text the first time Runway loads it. The
    packets are checked one by one to read back as the same bytes before the file is replaced. If
    another Runway still holds the file, it is left in hex and converted at the next start.
  - A Runway before 1.1.29 reads only the hex lines of such a log.
- **The checksum is held to the standard practice: the XOR of the bytes between `$` and `*`, both
  left out** (NMEA 0183). AIS-140 calls the checksum optional and names no algorithm. The Locate 140
  4G's sheet used to take the `$` in, because that is what the device's PVTs carried, so V7.06.11's
  PVTs passed and its logins failed. The span is now a setting of the device's spec (`checksum.over`:
  `between`, the default, or `from_dollar`), shown in the check's description.
  - A wrong checksum is tested against the other spans, and the test says which one it matches: the
    `$` taken in, the `*` taken in, or both. Each is a proved defect with its fix. A history packet
    that keeps its live checksum is still found when the XOR also takes the `$` in.
  - Found on V7.06.11 (a day on the bench, 5,242 packets):
    - every PVT, live and history, works the XOR out from the `$` (5,230 packets);
    - the login already follows the standard;
    - the health packet carries no checksum after the `*`;
    - one history PVT reached the server with bytes missing (`$PP,V7.06.11,…`).

### Fixed
- **An emergency packet has its device time (Firmware test).** The EPB carries date and time in one
  field (`DDMMYYYYhhmmss`) and the gate read only the separate date and time other packets carry. So
  an SOS had no time: it was missing from a file test's timeline and would have been missing from the
  map. It is now held to the future-time check like every packet, and one emergency moment sent normal
  (NM) and again stored (SP) is a resend (a warning), as live and history are.
- **An emergency packet's NM / SP read as live / history.** The live list called a normal (NM)
  emergency packet *History*, and the timeline put a stored (SP) one in the live lane. One reading
  now serves the list, the packet table, the packet view, the timeline and the Live switch. The
  maps (Firmware test's and the Dashboard's) draw SP as history too.

## [1.1.28] — 2026-11-23

### Added
- **The Dashboard's map is Log analysis's map, live, for one device or many.** Each shown device's
  latest packets are analysed on the server as a log is (`/api/live/track`, the same `analyse`):
  its track coloured by device, trip or speed, as lines or dots, with direction arrows; history
  dimmed; data lost and asleep dotted; stale positions and jumps dashed; trips numbered at their
  start and ringed at their end; driving events and the last position marked; a card on hover; full
  screen. A bar above the map chooses a device (the rest fade) and then one of its trips. A click
  on the map opens that packet in the detail pane, and a row picked in the table is ringed on the
  map. It follows the live feed (asked again at most every 2 s) and keeps the view while it does.
  The map is one component (`web/trackmap.js`), and Log analysis draws its track with it too — a
  trip clicked on the map still rescopes the whole page, a packet or gap still opens in the rail, full
  screen still floats the area tiles — so both maps are one and stay alike. (The device comparison
  keeps its own map: one vehicle, each device a narrower stripe over the one beneath.)
- **Trips say what they were.** A trip's chip carries its distance with its start (`1 · 13:52 ·
  12.4 km`), and *List* opens every trip in a table above the map: start, end, how long, how far,
  top and average speed, and what happened on it — device alerts, driving events, overspeed, history
  packets, data lost while driving — with the totals at the foot. A row picks its trip (on Log
  analysis the whole page then shows that trip); the list stays in full screen. On both maps.
- **Firmware test** — a new page (Tools → Firmware test) for the detailed test a new firmware gets
  before release. Point the device at a Runway listener (its relay to the platform carries on
  untouched) or drop in a capture, and every packet is held against the device's sheet:
  - **framing and checksum** — `$` … `*CC` and CR LF; the checksum is the XOR of every byte from
    `$` up to `*`, and a wrong one says what it does match;
  - **every field** — the sheet's datatype and format: dates DDMMYYYY, times hhmmss, the IMEI's
    check digit, ranges, the field count;
  - **the order of packets** — login first on every connection, the same IMEI, firmware and number
    plate as the login (or as the test expects), live frame numbers stepping by one, no frame
    number used twice, live times forward and arriving live, history oldest first, status and
    alert ID agreeing, ignition agreeing with IN / IF;
  - **scenarios** — which packet types and alerts the device has sent (ignition on and off,
    battery out and back, alerts), so the bench knows what is left to trigger.
  Each deviation is a flag on its packet, grouped into one row per check; each scenario counts its
  packets received, passed and failed; the test ends with an Excel report. A test still taking
  packets is *In progress* — its verdict (Fail, Incomplete, Pass) is given when it stops; the
  failures so far show all along. Laid out like Log analysis: one tab per open test, several at
  once, and a side rail — **Insights** (what the test found, in words, worst first: how many packets,
  which, and why — "every history PVT keeps the checksum it had live" — each opening its packets)
  and **Packet** (the one clicked, field by field). A test is told **how the device is set** — ping
  interval with ignition on, with ignition off, when it sleeps and its interval asleep, history
  FIFO or LIFO, and the packet format (the Locate 140 4G's PVT: 50 fields as the sheet, or 51) —
  and holds it to that: every location packet at its interval within the margin for it (judged over
  every packet in the order of its time, live and history together, so history filling a gap
  clears it; hours without a packet are a silence, not packets missing), history in the order set.
  A setting not given is not checked, and Insights says so; *Configuration* on a test corrects
  them and judges the test again from the packets it holds.
- **Test plan — the Locate 140 4G tracker inside Runway.** The tracker's 287 cases in 25 areas
  (location, time sync, sleep and wake, LEDs, driver behaviour, SOS, OTA, EOL, ignition, power,
  battery, inputs and outputs, tamper, cellular, storage, movement, CAN, RS232 / RFID, BLE,
  configuration, watchdog, InTouch, government and emergency servers) are a *Test plan* view of
  each firmware version — every test of that version together:
  - **from the packets** (43 cases): fix validity, position wandering and distance added while
    parked, time to first fix, clock at boot and ahead of Runway, the interval in each state, alarms
    and every Amendment 3 packet type seen, login on every reconnection, format and checksum,
    history order, versions reported, restarts and reset loops, a multi-day soak — each with the
    packets that show it;
  - **bench steps** (88): *Run*, do the step (press SOS, cut main power, ignition on…), and the case
    is judged from the packets that came in its window; Runway can hold back its acknowledgement of
    emergency packets so the device must send them again;
  - **by command** (26): the step sends the device the command typed for the case first;
  - **by hand** (130): status and remarks; the device on several servers' ports at once is compared
    for packets missing between them.
  The tracker sheet comes out in its own layout with the results filled in, and goes back in, so the
  developers' columns and results kept elsewhere carry over. A **Scorecard** in the side rail shows
  the plan at a glance — cases run, the pass rate of those, and pass / fail / partial by how they
  are tested and by each of the tracker's 25 categories; a category opens its cases. The tracker
  sheet carries the same scorecard. One tab per device (IMEI): another test of the same device opens
  in its tab, with a picker between the device's tests, and a device whose tab is closed comes back
  from *Open a test*; the left pane lists the plan's categories, each with how far it has got.
- **From what is wrong to how to fix it.** Every failure becomes a *defect* with its firmware area
  (packet builder, history buffer, GNSS and time, connection manager, alert engine, timers), its
  severity, the likely cause — *proved* where Runway tests it itself (a history packet's checksum
  matches its live version; the XOR matches with the '*' included) — where to look, the fix, the
  impact if shipped, the cases that verify it and the rule's source (sheet field or AIS-140). A
  *Defects* tab in the side rail leads with *fix first*: the fewest causes that explain most
  failing packets; each defect copies as a ready report and is a sheet in the Excel files.
  A **release gate** per firmware version (no failure in SOS, InTouch, government or emergency
  cases; no critical defect; 80% of the plan run) and **firmware against firmware** (fixed,
  regressed, still failing, newly failing; defects fixed and new).
- **Guiding the bench.** **Suites** run bench steps one after another (main power, ignition, SOS,
  tamper, wake from sleep, driving, network loss), each starting when the one before is judged.
  Insights say what is **not exercised yet** and how to make it happen (drive, toggle an input, press
  SOS…). A **timeline** shows the test on one line — live, history, failures (a click opens the
  packet), ignition and reconnections — and a **live coach** names a new failure the moment it comes,
  or a device gone quiet. The timeline **zooms** (the buttons, Ctrl and the wheel where the pointer
  is, the + / − / 0 keys) down to a minute across its width and **scrolls sideways** (scroll bar,
  drag, or sideways scroll) with a clock along it. Zoomed in, it shows every packet that came in the
  window on screen rather than the thinned whole, so a gap seen there is a real gap. A live
  test that is followed at its newest end keeps following as packets come. The side rail's **Live** tab shows the packets as they land on the
  listener, newest first — the time, kind, live or history, frame number, the result, the bytes and
  the first flag; new ones flash once, *Pause* holds the list, a click opens the packet.
  Every case has its **test case, expected behaviour and acceptance
  criteria** on a 5-second hold, and in the tracker sheet; categories show total, pass, fail, in
  progress and pending.
- **Reach.** A device's **protocol sheet** (xlsx with Field and Datatype columns) rebuilds a packet's
  spec and says what changed. A test is held against **the platform's own record** — its drive log
  from Log analysis — so case 23.06 is judged: every packet stored, read the same. A live test
  **writes its report by itself** every 24 hours and when it stops.
- **Checked against whole devices**: a simulated correct Locate 140 4G (a full day: driving, offline
  and history, SOS, tamper, health and emergency packets, standby and sleep, power out and back)
  passes with no flag at all; with each of 16 known faults it is caught and explained. Two false
  failures found that way were fixed: live frame numbers that jump over packets sent later as
  history, and parked positions pooled across different parking places.
- **Packets for the developers.** *Download* on the Packets card gives an Excel file of all the
  packets, the failed ones or those with warnings: each packet with everything wrong in it in
  words; each finding with the field, the value expected and the one sent, the likely cause,
  where to look and the fix; each kind of finding explained once (what the rule says, where it
  comes from, the impact if shipped); and every field of every packet under its sheet name, a
  broken one coloured. Written row by row, so a week-long soak downloads too.
- **TL packets, and what a check weighs per device.** The Locate 140 4G's **TL** packet (live or
  history) is an event packet of the sheet, with a *TL packet* scenario — it failed as an unknown
  packet type. TL and MoveE are different events that share ID 22 for now: a TL on 22 is a
  **warning** (and a minor defect: TL needs an ID of its own), any other ID on TL fails — the
  spec's `alert_ids_interim`, one row to drop when TL gets its ID. A spec can now set how much a check weighs for its device (`levels`):
  for the Locate 140 4G, frame numbers (stepping by one, used once) and a connection that opens
  without its login are **warnings**, not failures. The test-plan cases that test exactly that —
  a login on every connect and reconnect (15.05, 23.01, 24.01) — still fail on it, so the release
  gate still sees it. Movement detection without an ignition wire (FSD-ALL-002): **MoveS** (event
  21, movement start) and **MoveE** (event 22, movement end) are event packets of the sheet, each
  with its scenario — MoveS failed as an unknown packet type.
- **The cases that are part of testing.** *Edit scope* on the Test plan: tick the cases (or a whole
  category) that are part of testing; the rest leave the plan, the categories, the scorecard, the
  release gate, the counts and the tracker sheet — kept aside, not deleted, and put back any time.
  One scope per plan, every firmware version alike; an update of the shipped plan keeps it.
- **Nothing slips through the gate.** Every byte the device sends is judged: bytes the protocol
  cannot read (a broken sentence, a packet cut off, stray bytes) fail as *not a packet* instead of
  vanishing; every packet on the device's connection is tested, so one with a wrong IMEI is caught
  rather than dropped as another device's; a packet type the sheet does not describe fails;
  different history packets with one time fail; fields must agree with the alert (main power on a
  disconnect / reconnect, emergency status on emergency on / off, the cover open on a tamper alert,
  at least 3 satellites with a fix); a packet marked live but over 10 minutes old fails as history
  marked live. A test cannot read *Pass* while its intervals or history order were never given — it
  is *Incomplete* — and a test-plan case passes only on packets that themselves pass the sheet. The spec gained the AIS-140 emergency
  (EPB) and health (HLM) packets and the Amendment 3 packet types DT, OA and BC, by their codes. A live test carries on across a listener restarted
  in Capture and a Runway restart. The first sheet is the **Locate 140 4G** (`runway/fwtest/specs`);
  another device is another spec file. Run against a Locate 140 4G on firmware V7.06.10, it found
  the history packets keeping their live checksum, a 51st field, a six-digit date and a broken
  time in history packets, a login checksum taken over the `*` too, and a health packet without one.
- **Locate 140 4G in the Packet parser**: its login and PVT packets show every field by its name
  from the device's sheets, under *Locate 140 4G · Login packet* and *Locate 140 4G · PVT packet*
  — the PVT's 50 fields showed as `field_1` … `field_50`, and the login's names sat one field off
  (the IMEI shown as the device name, the firmware as the IMEI), because its login has no vendor
  field. The 51-field PVT the device actually sends (V7.06.10, a field 50 before the OTA
  response) is named too, and so is the Locate 140B's 48-field PVT.

### Changed
- **`main.py` runs one copy, always the latest code.** Started from VS Code, it used to find the
  Runway already on port 8080 — a release, or this checkout before the last edit — and only bring
  it forward, so the latest code never ran. Now a copy on older code is stopped and the latest
  starts in its place, with the listeners it ran (relay rules and store) started again in it; the
  same code already running is just brought forward, and no second copy starts. An open page from
  source reloads itself when that server restarts. (`serve --take-over`; a copy is asked to stop
  through `POST /api/app/quit`, from this computer only — a release older than 1.1.28 has no such
  way, so its process is ended.)
- **A copy run from source shows its branch** under the name — *Branch v1.1.28* — where a release
  shows *Version 1.1.27*, so a copy run from a branch is never taken for a released one.
- **Runway's logo is blue again**, as in 1.1.26: the blue tile with its mark, and the name as
  "Run" in the text colour with "way" in blue — the program's icon, the tray icon and the window's
  icon too. The rest of the app keeps the green of 1.1.27.

### Fixed
- **The map draws every packet, where the device put it.** A log over 4,000 positions kept only every
  Nth point of its track, so the line ran straight between them — cutting corners at roundabouts,
  looking broken, and missing the very packet picked on it. Now every packet with a clean position is
  on the track at its own position, however long the log, and a lone packet between two breaks is a
  dot instead of nothing. What is not joined into the line is drawn as what it is: gaps dotted, stale
  positions and jumps dashed. To stay quick at any length the map draws by **level of detail, never by
  leaving data out**: at each zoom a line carries the points that move it by more than half a screen
  pixel — every packet lies within half a pixel of the line drawn, measured (`accuracy()`), and at
  street level all of them are there — and dots are drawn in the view, one per pixel (one sits exactly
  under another). 100,000 packets: 1,859 points drawn at the fitted zoom, a zoom step in 0.2-0.3 s
  (was 0.7 s), dots in 0.2 s; a click still picks the nearest real packet.
- **No chart or measure hides a reading.** The power chart of a long log kept each stretch's lowest
  supply only — a spike vanished; it keeps each stretch's lowest and highest supply and internal
  battery now. The device comparison judged how far apart the devices put the vehicle on a sample
  past 20,000 positions — the worst moment could fall between; it judges every position now.
- **Log analysis's Packet panel shows the internal battery** — its voltage and its level, under the
  supply (the row view now carries both).
- **One moment, one packet** (Firmware test): *History times differ* compared only history packets
  in a row, so a live packet repeating a time, a time coming back later, or a moment sent live and
  again as history passed. *Packet times differ* now holds every packet of one kind (a location
  update, or each alert) to its own time — live or history, in a row or not; a moment sent live and
  again as history is a warning of its own (the platform gets it twice). The two causes are told
  apart field by field: **duplicate packets** (the same data again, only a new frame number — the
  device makes a packet twice) and **different packets at one time** (the clock did not move); both
  are critical defects, fail plan cases 16.01 and 23.04 (InTouch, release-critical) and so block the
  release gate. On the maps a pile of packets at one time is a ring with how many (its card: when,
  where, the frame numbers, the same data or not) — duplicates read as one point otherwise, and the
  frame numbers they used as packets missing; the Dashboard's packet table tags each row that repeats
  a device time (*Same time*). Run on the Locate 140 4G (V7.06.11): 6,553 duplicate packets, 461 at a
  stuck clock — 92 copies of one second alone — and 73 live packets sent again as history.
- **The Dashboard's packet table shows the date with the time** (DD-MM-YYYY HH:MM:SS) — a capture
  over several days read as one; and the device's own battery is named as such: *Internal battery
  level (%)* (from health packets) and *Internal battery (V)* (every location packet), the voltage
  now shown beside the level and in the packet's detail with the input voltage.
  A **Message type** column says what each packet is as the packet says it — its type and alert ID
  (`NR,1`, `MoveS,21`, `TL,22`) — and *Columns* offers every field the packets carried (packet
  status, frame number, number plate, firmware, PDOP, HDOP, operator, main power, cell, digital
  and analog inputs, checksum…), named from the field, in the order first seen.
- **Every time on the Dashboard reads one way — local, 24-hour.** The device list gave a device's last
  packet as *2:49:02 pm*, and a packet's detail its event and received times in raw UTC
  (`2026-11-18T12:10:30Z`); both now read as the table does (`18-11-2026 17:40:30`), and ignition as
  *On* / *Off*. The exact values stay in the decoded packet below.
- **A file test has a timeline** (Firmware test): a capture carries no arrival times, so its timeline
  said *Not enough packets yet* whatever it held. Its packets now stand at the device's own time, in
  that order, history among them — marked *device time*. The Test plan's filters (*Any result*,
  *Every case*) no longer clip their text, and a bench step's *Run* and result fit their column.
- **A broken packet makes no device of its own**: a sentence cut off and glued to the next
  (`$PVT,GTRP,V7.06.11,LB$ ,` then the next packet) had the firmware version where the IMEI goes, and
  the Dashboard showed a device called "V7.06.11". An AIS-140 device is named only by digits; such a
  packet stays the connection's device.
- **Exports never fail on a damaged packet**: a control byte inside a field (the device sent a few
  broken packets) reached a check's example, Excel refused it and *Report* answered an internal
  error. Every Excel file Runway writes — firmware test report, tracker, defects, Log analysis, QC —
  now shows such a byte as `\x02`, so the evidence stays readable.
- **The folded sidebar opens without moving anything**: with *Expand on hover* (and always under
  1000 px), each group heading was a thin rule when folded and a name when open, so opening it on
  hover pushed every page 20-95 px down and the pointer landed on another one — a click opened the
  wrong page. A heading now has one height in both, and an icon the same place.
- **The Test plan never fails as a whole**: a copy started before a newer plan file named a check it
  did not know, and the plan (and with it the categories) answered HTTP 500. A case that cannot be
  judged now says why — *restart Runway to load the latest code* — and the rest are judged.
- **A login's firmware and number plate are read**: the Locate 140 4G writes its firmware as
  `V7.6.0`, which the login reader did not take for a version, so devices showed no firmware.
  Login layouts are now a table by field count, like the PVT's.
- **An update brings new protocol definitions to a copy that has the old ones.** A release copied
  its definitions into `data/protocols` on the first start and never again, so the Locate 140 4G
  names would have reached fresh installs only. Now each start replaces a definition a release
  put there and nobody changed; one edited in Configuration → Protocols is kept.
- **The title bar follows the theme** when you switch it inside Runway's window. Edge fixes its dark
  mode when the window opens — a window opened while Runway was dark kept a black title bar over a
  light Runway, even after a restart (a new window joins the running one). Now a switch in Runway's
  own window reopens it on the same page in the new mode (about a second); a browser tab is left
  alone, and so is any other Runway window.
- **Runway's window no longer offers to sync**: its own Edge profile now opens with sync off, so a
  first start shows Runway, not Edge's "We are now syncing your browsing data".
- **The release build no longer ends on an error** when its messages go to a file or a console in
  the Windows code page: the closing lines carry an arrow, which that code page cannot write. The
  1.1.27 build stopped there after its zip, `latest.json` and notes were already made.

## [1.1.27] — 2026-11-17

### Added
- **Feature control**: the owner decides which pages each copy shows — for everyone, a group, a
  person or one copy — on the shared usage server (`usage.policies`); the copy is told in the
  answer to its usage report and keeps it, so it holds offline. A page switched off leaves the
  sidebar, says *switched off on this copy* when opened, and its switch in Settings is locked
  (*Off centrally*). Each person can still hide more for themselves. By default, pages used now and
  then (Protocols, Signatures) sit folded under *More*, and IMU appears once a device has sent IMU
  data. Needs the usage server's v4 script run once in Supabase; until then the defaults apply.
- **Log analysis** — a new page (Analysis → Log analysis). Drop in a drive log exported from the
  platform and it is judged: a status for each area — completeness, delivery, GPS, power,
  driving, payload — and overall, each tile with its figure. A timeline of the day shows speed,
  ignition, lost and asleep periods and a marker for everything found. The track is drawn on the
  map coloured by speed, with stale positions and jumps dashed. A ranked list of findings closes
  the summary; each finding opens the rows behind it. Details go section by section (gaps, frame
  numbers, GPS jumps, delivery delay and batching, power, trips, events, IMU and CAN values,
  packet types, columns) down to every row, filterable by what was found on it, with every
  exported column one click away. What it finds:
  - data lost with the ignition on, told apart from ignition-off sleep;
  - frame holes that repeat (another packet type) told apart from real loss;
  - delivery delay, rows arriving after newer ones, the platform's history flag against the
    device's own live/history mark, rows without a position and the reconnect bursts after them;
  - stale positions at power-on, jumps and the distance they add, repeated positions while
    moving, reported against position speed, satellites stuck at one value;
  - battery and supply voltage, power cuts;
  - trips, idling, overspeed and the device's driving events;
  - IMU rate and movement while parked, CAN values, alerts that do not follow their value.

  Logs are kept (data/analysis) to reopen, judge again with other limits (Settings), export to
  Excel or delete. Every layout of the export is read; columns and limits are tables.
- **Trips stand out on the analysis map**: each trip in its own colour with a dark outline over a
  greyed street map, numbered at its start and ringed at its end. Choosing a trip above the map
  (or a row of the Trips table) brings it to the front, fades the rest, zooms to it and labels
  its start and end; *Speed* colours the track by speed instead.
  Arrows along the track show the way the vehicle went, evenly spaced at any zoom.
  Missing data is a dotted line from the last position before the gap to the first after it —
  red when the ignition was on (data lost), grey when it was off (asleep).
- **A chosen trip is the whole page**: picking a trip (above the map, on it, or in the Trips table)
  turns the tiles, findings, timeline, charts, Details, the rows and the Excel export into that
  trip's alone — judged by the server from the same rows (`GET /api/analysis/{id}/part`), every
  row link still pointing at the row in the whole log. A pill in the header names the trip; its ×
  goes back to the whole log. The trip is kept in the address, so a reload or a shared link opens it.
- **A side rail on the analysis page** — four tabs like bookmarks on the right edge, folded at
  first, one open at a time (its inner edge widens it): *Findings* (with the count of issues on its
  tab — the findings left the summary for it, and the map has the row to itself), *Scorecard*,
  *Insights* (how each area stands, what the packets-by-mode figures say) and *Packet*. They follow
  what is chosen — the whole log, a trip (beside the whole log), a packet.
- **Pick a packet on the map**: a click on the line (or on a dot) chooses the packet nearest it — a
  ring marks it and *Packet* opens on it: live or history, its mode and trip, what was found on it,
  device and stored time, the delay, the step from the packet before, speed, satellites, position,
  GSM, ignition, supply, frame, alert, and every column; ← → step to the packet before or after,
  *Rows* opens it among the rows, *Show on map* zooms to it. A click on a line used to choose its
  trip — the trip chips and the start pins still do.
- **The map full screen**: a button under the zoom fills the screen with the map (the browser's own
  full screen where it allows it) — the area tiles float over it, the trip chips and legend stay
  on top, the side rail stays at its edge and the map makes room for it; the same button or Esc
  brings it back.
- **Every comment on the map is a card**: jumps and stale positions (from and to — time, position,
  satellites — then how far, in how long, the speed that would take), trips (start, end, how long,
  how far, top and average speed), driving events, the last position and the comparison's tracks
  are shown in the same table card as missing data, in the page's colours and its Inter (Leaflet set
  Helvetica / Arial on every map — now each map, its cards and controls read in the page's font), instead
  of a thin white strip; the trip pins' start and end in a one-line card.
- **A jump or a stale position, picked**: a click on one opens its panel in the side rail — how far,
  in how long, the speed it would take, the satellites, and the packets before and after (each one
  click away), marked on the map. A driving event or the last position opens its packet.
- **Missing data on hover is a small table**: start and end, each with its time and position, then
  the duration, how far apart and what kind — in the page's own colours, not a white strip.
- **What each figure means, on a held pointer**: every scorecard measure (and the comparison's),
  every area tile and each part of the packets-by-mode panels explains what it is and what it is
  used for after the pointer rests on it for 5 s — the house hold-tip, so nothing is printed on the
  page. A test keeps every measure described.
- **Trip times written once**: a trip's start and end read "17-11-2026 · 07:53:32 → 08:31:54" (the
  date once, and not at all on the log's own first day), the map's pins "Start 07:53:32" and
  "End 08:31:54".
- **On time means within a margin that fits the interval**: a step between packets is on time within
  ±1 s at intervals up to 20 s, ±2 s up to 2 min, ±3 s up to 10 min and ±5 s beyond (each band in
  Settings) — no longer a fixed 10% of the interval, which allowed ±90 s on a 15 min sleep ping. The
  scorecard says per mode how many steps came early, on time and late (Attention from 5%, Poor from
  15% early or late), each row showing the interval and margin it was judged by.
- **Missing data, picked on the map**: a click on a dotted line opens *Missing data* in the side
  rail — data lost or asleep, its trip, how long, how far apart its two ends are (and the speed that
  would take), about how many packets went missing, and its start and end: the last packet before
  and the first after, each with time, position and a way to it. The Gaps section (and its Excel
  sheet) gives every gap its start and end position and the distance between them.
- **What is picked shows on a dark map**: a clicked line used to get the browser's focus box,
  near-black and lost on the dark map; the page marks a picked packet or gap itself now, in the text
  colour (light on dark, dark on light).
- **The Device tile leads with alerts**: how many, then the three most frequent by name and count
  (restarts and firmware on hover; restarts still set the tile's status). Tile captions wrap to two
  lines, and a tile's header wraps rather than spilling out of a narrow tile.
- **Lines or dots**: the track can be drawn one dot per packet (live bright, history small and dim,
  no data still dotted lines) — remembered here.
- **The summary fits a narrower page**: with the side rail open, the map and findings stack, the
  packets-by-mode panels stack, and a card's legend wraps onto its own line.
- **A scorecard for every log** — the comparison's scorecard for one device: every measure (data,
  packets by mode, delivery, GPS, driving, power, device, network), each value in the colour of its
  judgement against its limit (green good, amber attention, red poor) with the limit beside it, and
  chips to show only the poor or attention rows. With a trip chosen, the whole log's value sits
  beside the trip's. On the summary, as a Details section, and as a sheet in Excel. One table of
  measures (`scorecard.py`) feeds both scorecards.
- **Packets by mode, mode by mode** — a panel each for Active, Standby and Sleep: packets made
  against those due and how many more or short (and about how many missed); live against history;
  the steps against the interval (early, on time, late, missed); the interval between packets and
  the delay to InTouch, each as average, median, min and max (with 90%, the spread, and live against
  history); how long the device was in the mode; and notes that say what is behind the figures —
  the packet types that made too many, the longest silence, how late history landed, the slow tail,
  a clock running ahead, repeated times, how long standby really lasted. A mode the log never shows
  says so. Due now counts each stretch's first packet, so a device that keeps its interval exactly
  makes 100%; and standby's interval is learned only from the time before sleep (it used to borrow
  the steps sleep was then judged by).
- **Log analysis in tabs**, like Capture's sessions: each log opens in its own tab and keeps its
  own page — the trip chosen, summary or details, the section, the rows' page and scroll — so
  several analyses stay open side by side. The tabs (and which is in front) are remembered.
- **Compare devices** — for benchmarking several devices fitted in one vehicle: *Select* logs, then
  *Compare*. They are judged over the time all of them cover (each with its own intervals), in a
  tab of their own: a tile per device saying how far it put the vehicle from the reference (the
  first ticked: median and 90% apart, speed difference); a scorecard of every figure that tells
  them apart — data, packets by mode, delivery, GPS, agreement, driving, power, device, network —
  the best of each row green and the worst red (ties marked alike); the tracks on one map as
  nested stripes so every device shows on a shared road; speed and delay on one time axis;
  packets by mode per device; and an Excel workbook (`GET /api/analysis/compare`, `…/compare.xlsx`).
- **Back and forward**, like a file explorer: every jump — a finding, a timeline marker, a *Rows*
  button, a section, summary or details, a trip — can be undone with ← (and redone with →) in the
  header, Alt+← / Alt+→ or the mouse's back and forward buttons, returning to the same view,
  section, rows filter and page, trip and scroll. Each tab keeps its own.
- **Three kinds of line on the track**: live packets solid and bright; history packets (sent
  later) in the trip's colour, dimmed and thin; no data dotted. Stretches outside a trip are a
  plain muted line now, so dots only ever mean missing data — and a long hop at a believable speed
  (a 10 s device on a highway) is drawn as driving instead of leaving a hole in the line.
- **Packets by mode** — how the device keeps time in each of its three modes: *Active* (ignition
  on), *Standby* (ignition off, still awake for a while) and *Sleep* (after that, or after its
  deep-sleep packet — the slowest rate). Per mode: the interval it should keep and the one it
  showed most, the packets it made against those due (time in the mode ÷ the interval; every packet
  counts) — *too few* below 90% (Poor), *Attention* below 98%, *too many* above 110% — how many
  steps were on time, early or late, and the delay to InTouch. On the summary as a card, in Details
  as *Modes*, on the timeline as the band under the speed, and in Excel as a *Modes* sheet. The
  intervals come from **device profiles** (Settings → Device profiles: by the device's number or its
  firmware; any number left empty is taken from the log); a device with no profile is judged by
  what its own log shows most, and the finding says so. These replace the old "every N s with
  ignition on/off" findings.
- **A drive log saved again from Excel is refused**, saying why: Excel writes the device times as it
  shows them (16-11-2026 18:07), the seconds gone — it used to analyse to an empty report.
- **Map first, then the day**: the map and findings sit right under the tiles, the timeline below
  them, each trip shaded on it in its own colour (a soft wash, numbered) so a stretch of the day
  reads as its trip. Trip chips carry the day when a log spans several.
- **Findings grouped by remark** — Poor, Attention, Note, Good — each group headed with its count,
  and a button per remark to show that group alone (on the summary and in Details).
- **Delete from the Logs list**: one log by the bin on its row, several by *Select* and ticking
  them, or every log with *select all* — always asked first, removed in one call
  (`POST /api/analysis/delete`).
- **Device on the analysis page**: a new area and section for the device itself.
  - **Starts and restarts**: the frame counter beginning again, how long the device was silent
    before, and its boot report (reset reason, boot count). Frame holes are now counted within each
    start, so a restart's repeated numbers are no longer read as loss. A hole within a minute of an
    ignition change is said so.
  - **AIS-140 alerts** from `otherData` (PacketType + alertId), named from the standard's table
    with the device's own code beside it; codes outside the table are listed. Ignition alerts are
    checked against the ignition input: one that disagrees, a change with no alert, a repeat.
    The ignition flickering (changes within 30 s) is counted. Driving events are named by the
    alert they came with (harsh braking …).
  - **Firmware** version in the header, and any change during the log.
  - **Named now**: `greenDriveType` 1 is harsh acceleration and 2 harsh braking; the DBG packet is
    Zenithra's debug report (it carries the boot report). Driving events are judged once named.
  - **Deep sleep** (DSEN, DeepSleepENable — the last packet before the device sleeps, sent when its
    internal battery is low and the main supply is disconnected): each one with the readings it was
    sent with, whether it came during an engine start, and whether the device really went quiet
    after it. One sent with the battery not low or the supply connected, or followed by more rows,
    is flagged.
- **More checks**: the reporting interval with the ignition on and off and how often it is kept;
  rows stored over 10 minutes late; rows with a GSM signal but no cell id (only for a device that
  reports its cell); the supply's lowest point each time the engine starts (a dip there is no
  longer called a power cut); internal-battery readings of 0 V.
- **A missing battery is critical**: the main (external) battery missing — its supply under the
  floor or the device's own power flag at 0, outside an engine start — and the internal battery
  missing — it reads 0 V — are each flagged Poor with when and for how long, marked on the timeline,
  banded red on the power chart and listed in Details; the device's own alerts for them (AIS-140
  3, main power disconnected; 4, internal battery low) count too. A low internal battery (under
  3.6 V or 20%) is Attention.
- **Power chart**: main supply and the internal battery on one chart, each on its own axis, with
  the ignition-on periods shaded and every engine start marked at its lowest supply.
- **Delay over the day** on the analysis summary: how long each packet took from the device
  making it to landing on InTouch — each period's median and its slowest packet, against the
  30 s limit — next to how many packets took how long.

### Changed
- **Runway looks like Supabase Studio.** Its colours, type and density now follow Supabase's:
  the near-black canvas with a breath of green, surfaces one step lighter, hairline borders,
  soft white text and Supabase's green as the brand (light theme: white surfaces, grey hairlines,
  near-black text). The font is **Inter**, bundled with Runway so it looks the same offline and on
  a PC without it. The main button is a deep green with a green edge, the others quiet fills;
  tables have a quiet header and hairlines instead of stripes; the open page in the sidebar is a
  raised fill instead of a blue bar. Things that were blue to tell them apart from green
  (conditional pass, signature sub-signals, hex, test sessions, running jobs, the Prod
  environment) are now blue-violet. The app and tray icon are green. Headings stay in sentence
  case — Supabase's capitalised labels were not adopted.
- **A fixed banner across the top of every page**, like Supabase's: *Runway* with its version,
  your greeting (*Hello, Name*), and the page you are on — then the page's own figures, the
  Live / Idle chip and the theme switch. The greeting moved there from the Dashboard, so it shows
  on every page.
- **Sidebar control**, as in Supabase: the button at the foot of the sidebar opens a menu —
  *Expanded*, *Collapsed* (icons only) or *Expand on hover* (icons only, opening over the page
  while the pointer is on it). The choice is remembered. A narrow window always expands on hover.
- **The window's title bar matches Runway** instead of showing a light Windows bar over a dark
  app: Runway's window opens in its own Edge profile, in Edge's dark mode while Runway's theme is
  dark. A theme switched while the window is open reaches the title bar the next time it opens.
- **A run from source asks your name, shows it and reports usage too**, so the whole flow can be
  checked without a release. Its reports are marked as a development build, so the user list
  tells them apart; `RUNWAY_USAGE=off` switches reporting off (the tests do).
- **The open tab stands out**: in Capture, Packet parser and Device OTA the selected session tab
  is raised off the bar with a lighter fill and a shadow, so the one you are working in is plain
  to see.
- **Drag to resize, within limits**: the sidebar (180–380 px), every side pane (Capture's session,
  the Dashboard's packet detail, the Parser's summary, the Signatures and Protocols lists, Device
  OTA's profile), the Dashboard's split between the map and the packets, its devices list, and
  every table column. Each keeps a minimum and a maximum so nothing is squeezed out, is remembered,
  and goes back to its default on a double-click of its edge.
- **Runway opens like an app.** It used to start a black command window and then a browser tab;
  now it opens in **its own window** (Edge's app mode: no address bar, no tabs, Runway's title and
  icon) and shows itself **by the clock**: left-click opens Runway, right-click has *Open Runway*,
  *Logs* and *Quit Runway* — which asks first while listeners run, since quitting drops their
  devices. No command window any more; the log it showed is on the Logs page. Underneath it is
  still a server, on purpose: closing the window leaves listeners, relays and storage running
  (24x7), other PCs on the LAN can still open it, and starting Runway again from the pinned icon
  brings the running one to the front instead of failing on a busy port. If another program holds
  the port, a message says so instead of nothing happening. A run from source keeps its console
  but opens the same window.
- **The first start welcomes you instead of warning you.** 1.1.26 opened with "Who is using
  Runway? Runway reports your name, this computer, the app version and how the app is used." — a
  notice that read as being watched. It now asks the way BluePulse does: "Hi! May I have your
  name, please?", then "We'll use it to greet you and to make Runway better for you. Settings →
  Usage reporting shows what is shared." What is shared is unchanged and still listed in Settings.
- **The release build stops when this version's CHANGELOG section is empty** — every copy shows
  those notes in its update offer, so a release without them would offer an update that says
  nothing.

## [1.1.26] — 2026-11-12

### Added
- **Runway updates itself.** People pin one program, `Runway.exe` — a small launcher — and never
  reinstall: each start checks the release channel, a newer version is offered once with its notes
  (*Later* leaves an *Update* chip in the top bar), and *Update* downloads it, refuses it unless its
  SHA-256 matches, unpacks it beside the running version and restarts into it — the page follows on
  its own. The offer says how many listeners the restart restarts; nothing installs until someone
  clicks. The running version is never touched: a failure anywhere leaves it as it was, said only in
  Settings → Updates. The way back: hold Shift while starting `Runway.exe` and pick an earlier
  version (three are kept). Releases are published on Runway's GitHub repo, now public; copies read
  the latest release's `latest.json`. Rehearsed end to end on this machine:
  an installed 1.1.26 updated itself to a 1.1.27 build from a local channel and came back on it.
- **Usage reporting**, the same shared server as BluePulse, as app "Runway", from release builds
  only: the first start asks the person's name once, the Dashboard says "Hello, <name>", and
  Settings → Usage reporting shows the name, install ID, last sent and *Send now*. Reported: who,
  this computer, the version, how long Runway ran, the pages opened, errors, update events and
  Runway's own counts (listeners and QC sessions started, decodes, OTA exchanges, replays, QC
  reports, devices seen). Never device IMEIs, packets, endpoints, keys or files. Silent: reports
  queue on disk while offline and never slow anything.
- **Device OTA — Runway acts as a TCU against the MapmyIndia InTouch OTA cloud.** The mirror of
  Runway's usual role: instead of landing packets from devices, it dials out to the OTA platform
  as if it were a device, building `$MMI,type,serial,keyCode,dataBody,checkSum*` sentences,
  sending them over HTTPS and reading the replies. The crypto is pinned to real traffic —
  AES‑128‑ECB/PKCS7 for the body (verified both directions against the protocol doc and a live
  keys response) and CRC‑8 (poly 0x07, over the dataBody hex) for the checksum (two
  server‑accepted samples). A new `runway/ota/` subsystem keeps the frame/crypto pure and the
  HTTP transport + key lifecycle separate: the 3 static keys bootstrap the 8 communication keys
  (`comMap`), which are downloaded on demand (type 1), refreshed when the `expireTimestamp`
  passes (type 2) or once on a key‑related type‑9, and stored per `(environment, IMEI)`.
  Validated live against the production platform: it accepted our frame and returned the comMap.
  The **Device OTA** page, in the house style (BluePulse / InTouch), sentence case throughout:
  - **Profiles** — a saved device: a name, the environment (Dev / Prod / Custom, each Custom
    profile with its own endpoint), the IMEI and the static keys, made and edited in one dialog
    (*Delete* is red and asks first). Set up once, never typed again.
  - **Tabs**, like Capture: one working session per tab, each on a profile, kept across a reload.
    A new tab opens on *Choose a profile*; `?env=&imei=` opens a given device.
  - **A panel that folds**: the tab's profile (identity, environment, endpoint), its **keys** —
    static and comm, with *View keys* (every key, each copyable, the comm keys' expiry and when
    they were downloaded) and *Download* / *Refresh* — and the tab's **history** of exchanges, any
    one reopening in the panes. Folded it is a strip of icons with a key-state dot and a history
    count; on a narrow window it opens over the work instead of squeezing it.
  - **Four panes, the request on the left and the response on the right**, readable on top and
    the wire below, filling the page. *Encode*, *Decode* and *Send* sit in the toolbar with the
    message type and key, Send showing the HTTP status and round-trip time. Each pane names what
    it holds: the key used (and the key the cloud chose for its reply), the CRC, the HTTP status,
    the message type. Too narrow for two columns, the panes stack in the order the exchange runs.
  - Environments are never red — red is for a fault: Prod is blue, Dev amber, Custom neutral.
  `runway ota keys|send` exercises the same flow from the CLI.
- **Speed on the IMU page**, in both layouts: a speed chart under the IMU charts (half height
  in *Combined*, one more lane in *Lanes*) on the same time axis and crosshair, its value in
  the tooltip, a *Top speed* tile and a `speed_kph` column in the CSV. It is a chart of its own,
  not a second scale on the acceleration or rotation chart: km/h against m/s² or °/s on one
  chart makes lines look related by where their scales happen to meet. Drawn in neutral ink,
  so it is never confused with a series colour. Speed comes from every stored packet (IMU and
  NR); long ranges read it from the per-minute summaries, which now carry speed — a v1.1.25
  database gets the columns, filled from its packets, when it opens.

### Changed
- **A release is a launcher install.** The zip unpacks to a `Runway` folder: `Runway.exe` (the
  launcher), `versions\Runway-v<version>\` (the program, now a PyInstaller one-folder build — it
  starts without unpacking itself to a temp folder) and `data\` beside the launcher, shared by every
  version. Coming from 1.1.25 or earlier (data beside the old exe): move that `data` folder beside
  `Runway.exe` before the first start — once; after that, updates keep it in place. The program and
  the launcher carry Runway's icon. `build_release.py` also writes `latest.json` (the version, the
  notes from this changelog and the zip's checksum) and `notes.md`.
- **One look through the whole app — Device OTA's design on every page.** The approved Device
  OTA page set the bar (BluePulse's language); every page now shares it:
  - **The frame, as in BluePulse**: the rail carries the brand and "Version 1.1.26" on top, its
    modules as sentence-case groups (a rule when folded), the active page tinted with an accent
    bar; it folds to 58 px and is always folded under 1000 px. The top bar holds the page title,
    its controls, the Live / Demo / Idle chip and the theme button. A page's description is no
    longer printed beside the title — it is a hold-tip, shown after the pointer rests on the
    title for 5 s. (The Settings gear left the top bar: *Settings* is in the rail.)
  - **Sentence case everywhere** — headings, labels, table headers, tiles, pills, buttons, the
    rail — and no emoji or text arrows on controls: one shared line-icon set, each icon with a
    word. Every control takes the page font and a row of controls one height.
  - **One kit**: tone pills (Running / Done / Pass / Live…), outlined buttons with one filled
    primary, tables as data grids (a header band, rows a shade apart), outlined tiles, notices,
    session tabs with an accent bar (Capture, Parser and Device OTA alike), and one dialog look.
  - **Side panes fold** like Device OTA's: Capture's session panel, the Parser's summary and
    map, the Dashboard's packet detail and the Protocols / Signatures lists fold to a strip and
    remember it; on a narrow window they open over the page instead of squeezing it.
  - **The app's own confirm dialog** replaces the browser's (`confirm`/`prompt`) everywhere —
    Clear, Delete, Save to the repository, Import, closing a session, a skip reason, renaming a
    Parser tab: a title, one sentence, Cancel focused, red only for what destroys.
  - The light theme's green, amber, blue and red text are darker shades of the same colours, so
    pill and status text passes WCAG AA (4.5:1) on every surface; the dark theme's already did.
  `tests/test_ui.py` keeps it true: the icons every page names exist, nothing forces capitals,
  no glyph stands in for an icon, no browser dialog, no rule on a bare structural element.

### Fixed
- **Dialogs opened behind the Dashboard and Parser maps.** Leaflet's layers stacked above every
  dialog on the page (Settings opened half hidden on the Dashboard); each map now keeps its
  layers to itself.
- **The Converter's table sat in two-thirds of the page** — a global rule meant for the
  Dashboard gave every page's `<main>` a 320 px empty column on the right.
- **The Dashboard map drew a fan of lines after a device reconnected.** Seen live after a
  Runway restart: the device sent the packets it had stored meanwhile (history, `H`) in
  between live ones, and the trail joined points in arrival order — each history packet a line
  back to where the vehicle had been, the next live packet a line forward again. The trail now
  runs in device time (a late point slots into place) and the marker stays on the newest
  position; a record with no device time (a login's last fix) only places the marker until
  timed points arrive. The Parser's map joins points in time order whatever order its table
  is sorted in. Each Dashboard trail keeps its latest 5,000 points (it grew without limit).
- **IMU page: *Follow live* showed an empty chart and no reason.** Following live, the chart
  ends at the current time, so a device quiet for longer than the range leaves it empty — and
  the "No samples in this range" message sat *below* the hidden chart (which keeps its height),
  off screen. The message now sits over the chart, and when following live it names the
  device's newest data and how long ago it was, with *Show its newest data*. The tooltip said
  the right edge was the newest sample; it is the current time.
- **Dead device connections stayed open for ever.** Seen live on a relay listener: a device
  whose modem kept losing its data session re-dialled from a new address 60 times in 40
  minutes and never closed the old connections — no close ever arrives from an address that
  is gone, and Runway never writes to a relayed device's old connection, so neither side
  noticed. All 60 stayed open (Windows listed them as established), each holding its session
  in memory. Runway had no idle timeout. A connection is now closed when its device has
  dialled in again and it has been quiet for 30 s, when it has received nothing for 10
  minutes, or when TCP keepalive probes (after 2 minutes of quiet) go unanswered.
- **A device that had gone was logged in to the platform every 2 minutes.** The newest dead
  connection kept the device's relay link alive; the platform drops a quiet connection after
  ~2 minutes, and Runway dialled straight back — opening, as every dial does, with the
  device's login. 188 logins in 5.6 hours from a device that sent nothing. The link now dials
  again only when there is something to send (shown as *relay idle* meanwhile). A connection
  that sends nothing at all no longer opens a platform connection either.
- **Bytes from a device's older connection could wait in the outbox.** When a device's newer
  connection ended, it took the shared relay link down with it; the older connection, still
  sending, pushed into the finished link and nothing went up until the next reconnect. It
  now takes the link over. Rows already sent are also deleted as soon as their 2 s grace is
  up (it could take up to twice that), so fewer are sent twice when a link breaks.
- **The Capture device list showed one re-dialling device as 93 devices.** The page named a
  connection's device only from packets it still held, and the old connections' packets were
  long gone, so each connection showed up as a separate "device" by IP:port. The server now
  sends each connection's device and protocol. The tab badge counts devices online, not open
  connections (it read 60 for one device).

## [1.1.25] — 2026-11-04

Headline: **devices can land directly on Runway** — each device's raw stream is relayed on to
the next server and, alongside, decoded and kept here — and **the IMU packet is decoded and
charted**: 6-axis samples from Zenithra sCAN (`$MMI`) and Locate 140 VB (`$PVT`) firmware.

### Added — device data lands on Runway, forked two ways
- **Relay to the next server** (Capture → New session → *Relay to* `host:port`). Each device
  connection gets its **own** connection to the next server — a platform identifies a device by
  its connection and login, so devices are never multiplexed onto one socket. Every byte goes up
  exactly as received, before any decoding (checked byte-for-byte on the real 15-minute export);
  the server's replies (ACKs, commands) come back down. **While relaying, Runway sends no ACK of
  its own**: the next server confirms delivery. A relay that points back at its own listener is
  refused. Runway's own command sender still works.
- **Lossless relay — an outbox in front of every link** (`data/relay_outbox.db`). Every byte is
  queued on disk before it is sent and removed only once delivered, so a platform that is down,
  slow or drops the link loses nothing: the device stays connected, its packets queue, and the
  link delivers the backlog in order when the server is back — after the device has gone, and
  after a Runway restart too. The user's platform sends no ACKs, so the device never resends what
  a relay dropped; the earlier build dropped bytes while the server was unreachable. A row is
  deleted 2 s after it went out on a link that stayed up — at-least-once: the last ~2 s before a
  drop arrive twice rather than not at all. A new link sends the device's login first. Capped at
  2 GB (about 2 days of 20 devices), oldest first, counted. Each device row shows its link
  (up / connecting / down) and what is waiting (`12 KB queued`). Checked end to end against the
  real server: a platform taken down mid-stream for 40 packets received all of them when back,
  and the outbox drained to 0.
- **Store** (same form): every packet — raw bytes included — and every IMU sample is kept in
  `data/telemetry.db`, one row per sample with its packet's time and place in the packet. Its own
  file, because it grows by ~36,000 rows an hour per device and `runway.db` is backed up whole
  every day.
- **A relaying or storing listener restarts by itself with the server** — it is a service in
  front of the platform, not a bench session. Stopping or removing it by hand forgets it.
- **Relay & store change on a running session** (⇄ *Relay & store* on the session strip): one
  rule for all devices plus per-device overrides — same as all, no relay, or its own server
  (`host:port`), including IMEIs that have not connected yet — and *Store* on or off. Saved rules
  apply at once and survive a restart. A connected device moved to another server gets a new
  link there with **its login replayed first**, so that server sees a normal session start;
  a device taken off relay is ACKed by Runway again. New connections are held until the device
  is identified (its login, normally milliseconds; at most 3 s) so its own rule applies from the
  first byte; one that never identifies follows the all-devices rule. Runway ACKs exactly the
  devices it does not relay.
- **One row per device on a Testing session**, not one per connection: a device that re-dials
  arrives on a new source port each time and used to look like several devices. The row shows
  the device's current connection and total records, and `↻ 4 connections · ~45s each` when it
  re-dials — unfold it for every connection with its time span and record count. Hiding a device
  in the feed now holds across its reconnects.

### Added — packet archive and replay to a platform (Capture → Archive)
- **Every stored packet is archived exactly as received, one file per device per day** —
  `data/archive/<IMEI>/<IMEI>_<YYYY-MM-DD>.log`, one line per packet: received time, message type,
  the packet (ASCII as it is, anything else as `0x<hex>`, so the bytes come back exactly). A
  device that reconnects the same day keeps appending; a finished day is gzipped (hourly
  housekeeping). The packet is the record: time, position, speed, satellites, voltages, ignition
  and the IMU samples are all extracted from it by the codec when needed, so nothing decoded is
  stored twice. Measured on the live export: **7.7x smaller gzipped — 20 devices x 30 days in
  1.1-3.4 GB**, where decoded rows in SQLite would take 19-96 GB. Written with the listener's
  *Store* switch.
- **Replay a day to a platform** (Archive page → *Replay*): one connection, the device's last
  login first (from an earlier day when the day starts mid-session), every packet in its original
  order and byte for byte, at a chosen pace (max / 200 / 50 / 10 packets/s, held to schedule rather
  than per-packet sleeps, which Windows timers make ~15 ms coarse). Progress, status and *Stop*
  on the page; the platform's replies are counted. Each day can also be downloaded as it is.

### Added — safe to run 24x7 (20 devices, weeks)
- **Retention** (Archive → *Storage*): IMU samples and decoded packets are kept 7 days, their
  per-minute summaries 365 days, the archived packets forever — each changeable, applied hourly
  and at once when shortened. The card shows each store's size on disk and the free space. At
  ~3.6 KB per stored IMU packet, 20 devices fill 0.6-3 GB a day; without a limit the database
  grew until the disk was full.
- **Per-minute IMU summaries**, kept as packets arrive: ranges of 24 h and longer draw from them
  — 0.02 s for 24 h and 0.3 s for 30 days, where reading the samples took 2.7 s per device-day —
  and a day whose samples have expired still shows at one-minute resolution.
- **The device list reads a running summary** instead of scanning every stored packet (~22 s
  at 20 devices x 30 days; the IMU page asks for it every 2 s while following live).
- **A listener frees each connection when it ends.** Devices re-dial from a new source port each
  time; every connection's session, codec and statistics used to stay in memory for the life of
  the listener. Statistics of the last 500 ended connections are kept for the Capture page.

### Added — IMU page (Analysis → IMU)
- A device's stored samples over time: **seven stacked lanes** — ax, ay, az, |a| (m/s²) and the
  gyro's pitch, roll, yaw rates (°/s) — each on its own scale, because az carries gravity (~9.8)
  while ax/ay move around 0 and would flatten on a shared axis. One time axis, one crosshair with
  every lane's value, drag to zoom, double-click to step back; a packet lane marks NR packets,
  history packets and missing frames (shaded across every lane). A *Range* list from 30 s to 24 h and All,
  *Follow live* (redraws every 2 s), tiles (samples, IMU packets, peak |a|, peak rotation and its
  axis, missing frames, history packets), and **⤓ CSV** of what is on screen.
- **Lanes | Combined** toggle (remembered): *Combined* puts ax, ay, az and |a| on one chart and
  pitch, roll, yaw on another — one shared axis per chart (one unit each), every line in its own
  categorical colour (slots 1-4, fixed order, validated on Runway's surfaces) with the chart's
  own legend inside it; *Lanes* keeps one chart per value on its own scale.
- **A timeline scrollbar** under the plot spans the device's whole stored history with the view
  as its thumb — drag it, swipe sideways on a trackpad, or Shift + wheel over the chart; the
  window being scrolled to loads as it arrives (stale responses are dropped).
- The server thins a long range to about one point per pixel — min / max / mean per bucket, drawn
  as a band and a line — and returns every sample once zoomed in far enough (a 24-hour day is
  ~860,000 samples per device).
- **Sample times follow the device's own rule** (sCAN firmware, `SET IMUPKT:1,<ignOnSec>,
  <ignOffSec>`, default `1,10,2`): a packet's 20 samples are spread evenly over the packet
  interval (gap = interval / 20, 100 ms at the fastest) and the interval depends on ignition —
  10 s ON (2 Hz, 500 ms apart), 2 s OFF (10 Hz). Packets therefore join back to back. Each
  ignition state's interval is **measured** from the spacing of the IMU packets in view (median,
  so the device clock's 1 s resolution washes out) and falls back to the `IMUPKT` values on the
  page's *Sample timing* — so a config change on the device (this morning's export ran ignition
  ON at 2 s) needs no setting. The popover says what is in use: "Ignition ON: every 500 ms
  (packets every 10 s, measured)". Nothing is stored derived: changing a setting re-times every
  sample. An early build assumed a fixed 10 Hz and drew a 10 s packet as a 2 s burst plus an
  8 s hole.
- **A hole in the samples breaks the line instead of being bridged.** Every point carries its
  samples' own spacing (`g`: its packet's interval / its sample count), and the line breaks where
  the next sample is more than 2.5 of those away. Seen live: toggling ignition made the next IMU
  packet arrive 3 s later (not 2) and one packet carried 18 samples; the page judged holes by the
  slowest ignition state anywhere in view (500 ms ON) and drew a straight segment over a 1.1 s
  hole in 100 ms OFF data.
- **When a running session receives but does not store**, the IMU page says so — "Port 4082 is
  receiving but not storing" — with a *Turn on Store* button that applies to the running session.

### Added
- **IMU packet** (packet type `IMU`, alert id `200`): the AIS-140 Locate 140 VB layout field for
  field (50 fields), with the samples in the parenthesized last field where a normal packet has
  its OTA response — `(1:x|y|z|p|r|y;2:…;n:…)`. Decoded as `message_type: "imu"` with
  `io["imu"]` = one `{n, ax, ay, az, pitch, roll, yaw}` per sample and `io["imu_sample_count"]`.
  The count is read, not assumed (20 today). A malformed sample is kept verbatim in
  `io["imu_bad_samples"]` instead of failing the packet. Per the firmware: ICM-42670 snapshots —
  x/y/z acceleration in m/s² (±8 g, gravity included), p/r/y gyro rates in °/s (±250 dps). It is
  **data, not an alarm** — no `alert_200` in `alarms`.
- **Custom packets are a registry, not a branch** (`runway/codecs/ais140_custom.py`). They are
  told apart by *identity* (packet type + alert id) while field positions come from the
  sentence's *shape*, so the next custom packet is one `@custom_packet(...)` function — no
  codec, layout or dispatch change — and decodes under every AIS-140-family header. They stay
  inside the session's codec on purpose: they share the connection with normal packets and a
  session's codec is picked once, so a codec of their own would never see them.
- **Layouts are tables** (`NmpLayout` rows in `NMP_LAYOUTS` / `MMI_LAYOUTS`, keyed by field
  count + parenthesized last field) served by one builder, replacing per-codec `if len(f) ==`
  branches. Each record names the layout it was read with in `io["layout"]`.
- **Parser:** a grammar entry can list `"variants"` by field count, and `zenithra.json` names
  the 50-field `$MMI` layout, neighbour cells included. Any list the codec decodes
  (`io["imu"]`, the neighbour cells) is shown one row per item. A real IMU capture ships in the
  release `samples/`.
- **Platform message-log exports are read directly** — in the Parser (paste or **Upload**), in
  Dashboard file replay and in `runway replay` (`--fmt log_export`, or auto-detected). The
  export (`<server time> | <imei> | ,"MMI,...*37 | null"`, one row per message) drops each
  sentence's `$` and line terminator, which is exactly what a codec frames on, so it used to
  decode to nothing. `runway/convert/log_export.py` rebuilds the stream the server received:
  `$` restored, hex messages turned back into bytes, and rows sorted into arrival order (the
  export lists newest first, and history packets arrive late). Each export layout is one regex
  in a table.

### Confirmed on live traffic (sCAN 865577071515754, firmware 5.1.30I, 15-minute export)
- 538 messages, all decoded, none skipped: 448 `IMU` (50 fields, all `ais140_vb`) and 90 `NR`
  (38 fields). Every IMU packet carries exactly 20 samples numbered 1-20, none malformed.
- IMU is sent **every 2 s** (448 packets over 895 s of device time) and NR every 10 s, on one
  shared frame counter that ran 534 → 1071 with no gap. 20 samples per 2 s points to a 10 Hz
  sample rate — still to be confirmed by the firmware team before per-sample timestamps are
  derived. 12 IMU packets arrived as history (`H`), out of order — decode by device time.
- Device time is UTC (server − device = 5 h 30 m + 2-6 s of latency).

### Fixed
- **Every server start opened one more browser tab.** `serve` (and so `run-server.bat`,
  `main.py` and the exe) opened the dashboard unconditionally 1.5 s after starting, so each
  restart left another tab behind. It now opens one only when no Runway page is open: open pages
  check in with `/api/health` every 4 s, so the launcher waits for the server, gives pages 5 s,
  and reads `page_seen_s` (its own `?probe=1` check does not count as a page). A first start
  still opens the dashboard; a restart reuses the tab you have.
- **A 50-field `$MMI` sentence was read with the 38-field layout** — every field from 26 on
  landed one slot off (tamper `0`, signal strength empty, MCC `27`, MNC `404`) and the IMU
  samples went into `tail_raw` unparsed. The same misreading hit the Parser's field names.
- **Locate 140 VB `$PVT` lost its OTA response.** Its 50-field layout has no odometer, but the
  codec treated 50 fields as the v1.9 doc layout, read field 50 (`()` — or IMU samples) as an
  odometer, failed to parse a number and dropped it.
- The AIS-140 resync cap is 4 KB (was 2 KB), so an IMU packet with up to ~100 samples is read
  whole instead of being discarded as garbage.
- **Packet Parser views for sentence protocols (AIS-140, Zenithra, NMEA).** *Grid* only knew
  Teltonika's AVL records and silently fell back to the field list; it now shows one row per
  packet and one column per field (item rows such as IMU samples stay in *By record*, which the
  server marks with `"list"`). *By record* / *Flat* name each packet's type and time in its
  header and stop at 200 packets — a 538-packet export rendered ~50,000 rows. Values stay on
  one line with the full text on hover: the IMU payload's ~2,800-character hex took the whole
  table and wrapped every other value letter by letter. Rows with no bytes (IO elements, IMU
  samples) use the hex column too, so a sample reads whole. The summary shows Teltonika's
  handshake / GPRS / CRC / priority figures only for Teltonika, and *By type* otherwise.

### Notes
- **Upgrading an exe install:** the Parser reads `data/protocols/zenithra.json` from the data
  folder first. A copy seeded by an older version lacks the 50-field variant — delete it (the
  shipped one is used) or replace it from this release.
- The standalone reference parser `data/protocols/zenithra.py` still knows only the 38-field
  layout; it follows once the IMU layout is confirmed on live traffic.

## [1.1.24] — 2026-11-04

Rename release. Headline: **the project is called Runway everywhere.** No behaviour changes.

### Changed — release packaging
- **Everything in a release carries one name, `Runway-v<version>`**: the zip, the folder it
  unpacks to and the exe itself (`Runway-v1.1.24.exe` inside `Runway-v1.1.24\`). The old
  `Runway-<version>-win64.zip` held a bare `Runway.exe`, so two installs side by side were
  indistinguishable in Explorer, the taskbar and a bug report. The name is spelled once, in
  `packaging/build_release.py`; `Runway.spec` imports it.
- **Upgrading needs one manual step, now written down.** The exe keeps its `data` folder
  beside itself and each version unpacks to its own folder, so a new version opens empty until
  the previous `data` folder is moved in. `GETTING_STARTED.txt` says so. On the build machine,
  `build_release.py` carries it over itself — never into the zip — and stops, naming the
  folders, when more than one earlier install holds data rather than guess which one is live.
  A rebuild no longer wipes a `data` folder created by running the exe from its build folder.
- **One version declaration: `runway/__init__.py`.** `pyproject.toml` reads it (`dynamic`), and
  the UI no longer carries its own copy — `/api/health` now reports `code_version` (the code
  on disk) next to `version` (the running process), so the "restart the server / reload the
  page" banner keeps working without a literal to forget. Versions are compared numerically:
  the old string comparison would have ranked `1.1.9` above `1.1.24`.
  `tests/test_version.py` fails the suite on a second version literal, a CHANGELOG whose newest
  section is not the current version, or a version branch that doesn't match it.

### Fixed
- **`Runway-v<version>.exe --version` (and `python main.py --version`) print the version.**
  Both launchers default to `serve` when the first argument isn't a sub-command, so `--version`
  and `--help` were handed to `serve` and rejected as unknown flags — the one command that
  answers "which build is this?" without starting a server didn't work on the exe.

### Changed
- **`ProInfo` → `Runway` across the whole project.** The app, the exe and the UI were already
  branded Runway while the repo, the Python package and the CLI still said `proinfo` — two names
  for one thing, which cost time on every navigation. Now: repository/folder `Runway`, package
  `runway/` (was `proinfo/`), imports `from runway...`, console script **`runway serve`** (was
  `proinfo serve`), distribution name `runway`. 261 occurrences across 61 files; the names that
  were already correct (`Runway.exe`, `RUNWAY_DATA_DIR`, `data/runway.log`, the `runway.*`
  browser-storage keys) are untouched, so **no saved setting or page state is lost**.
- **The database is `data/runway.db`** (was `data/proinfo.db`) and **adopts the old file on the
  first start** — devices, captures, profiles, QC devices and settings all carry over with no
  manual step (`db.adopt_legacy_db`). The `-wal`/`-shm` siblings move with it, so writes that had
  not been checkpointed yet are not lost. Adoption also fires when a *row-empty* `runway.db`
  already exists, which is what you get if the app is started once before migrating — without
  that, a single stray start would have orphaned the history silently. A `runway.db` that holds
  real rows is never overwritten, and a locked file is skipped rather than failing the start.

### Notes
- `ProInfo` (Pro=protocol, Info=information) survives only in this changelog. Releases up to and
  including 1.1.23 were published under that name.
- **After pulling this, restart the app** — a running server is still executing the old
  `proinfo/` module tree, and the database adopts itself on the next start.
- Reinstall if you had an editable/console-script install: `pip install -e ".[dev]"` (the old
  `proinfo` command is gone).

## [1.1.23] — 2026-08-03

Protocol release. Headline: **Zenithra Ax1 sCAN is onboarded** — an AIS-140 base packet that
carries the vehicle CAN bus inline.

### Added
- **New protocol: Zenithra Ax1 sCAN** (`zenithra`) — `$MMI` ASCII/CSV sentences, 38 fields.
  The base packet is AIS-140, so the codec (`runway/codecs/zenithra.py`) *derives* from the
  AIS-140 codec and reuses its framing, field helpers, packet-type/alert vocabularies and
  login/health/emergency builders; what it adds is the `$MMI` layout, which diverges from
  field 26 on (no `emergency_status`, no neighbour-cell triples, no odometer, and
  `frame_number` moved to the very end after the OTA field).
- **CAN data is decoded structurally.** The sentence's CAN field (`;`-separated
  `<hex id>:<hex payload>` frames) is located *by content*, so a firmware that shifts the
  tail still yields its bus data. Each frame arrives in `io["can"]` as
  `{id, id_int, extended, data, dlc}` — raw bytes preserved, plus the numeric id and its
  11-bit/29-bit class — with `io["can_frame_count"]` on every packet (0 when the CAN slot is
  empty, which is a normal packet, not an error). No signal scaling is applied: that needs a
  per-vehicle DBC/CanPlus signature, not a protocol.
- **Protocol repository entries**: `data/protocols/zenithra.json` (declarative field grammar,
  every field mapped against the AIS-140 base, with each assumption flagged) and
  `data/protocols/zenithra.py` (zero-dependency standalone parser for transfer to other
  utilities) — the same pair AIS-140 and Teltonika ship.
- **CAN frames are visualized, not dumped.** A packet's bus frames now render as a table
  (CAN ID · 11/29-bit · DLC · payload bytes) in the **Dashboard inspector** and in the
  **Capture packet view**, and are folded out of the JSON blocks with a `[n frames]`
  placeholder — a single packet carrying two dozen frames used to push every other decoded
  field off the screen. Payloads stay raw on purpose (engineering values need a per-vehicle
  DBC/CanPlus signature, which lives in the Converter). An opt-in **CAN frames** column in
  the Dashboard packet table shows the per-packet frame count.

### Added — bind a vehicle signature to a device
- **A signature bound to a device decodes that device's CAN frames everywhere.** The binding is
  applied by a new pipeline hook (`Pipeline(..., enrich=…)`) that runs once per record *before*
  any sink sees it, so the decoded signals show up on the **Dashboard**, in the **Capture packet
  view**, in a QC session's capture and in the Parser's breakdown — one binding, every surface,
  no per-view wiring. Bindings live in `settings` as `{device_id -> signature}` with `"*"` as the
  any-device default, are looked up per record (so binding takes effect on the **next packet** —
  no restart, no relisten), and are managed on the **Signatures** page (`GET`/`PUT
  /api/signature-bindings`).
- **Decoded signals arrive as `io["can_signals"]`** — `{name, value, can_id, raw}` per signal,
  plus `io["can_signal_count"]` and `io["can_signature"]`. The raw integer and source frame ride
  along so an operator correcting a start bit can see exactly what was read. Dashboard and
  Capture render a **CAN signals** table above the raw frames (and an opt-in *CAN signals* column
  in the Dashboard packet table); both lists are folded out of the JSON dumps with a count.
- **Static capture uses the same decoder**: the Parser's existing Signature control now travels
  with the request (`signature` on `/api/parse`), so a pasted packet reads exactly like a live
  one. Naming no signature falls back to the pasting device's own binding.
- **The signature format's two key styles are kept apart** (`runway/convert/can_signals.py`):
  8-hex-digit keys are CAN frame ids and bind to a device; short decimal keys are Teltonika AVL
  parameter ids and are left to the Parser's AVL view. Without that guard an AVL id like `900`
  would silently bind to CAN id `0x900` and invent signals. Scaling is the canonical DBC
  convention — `physical = raw × multiplier + offset` — matching `CanSignal.decode` and the DBC
  export, and a signature row that overruns its payload is skipped rather than losing the whole
  frame's signals.

### Added — the Parser works for every protocol, not one
- **`Codec.explain()` — a new optional plugin hook.** A codec that can say more than a generic
  walk (bit-level sizes, verified CRC, a parameter catalogue) returns its own breakdown;
  `teltonika.py` does, so its field tree, CRC check and AVL grid are unchanged. Every other
  codec gets the protocol-agnostic walk in `runway/codecs/generic_explain.py` for free. **No
  caller branches on a protocol name any more.**
- **The generic walk is driven by the protocol database.** Framing and values come from the
  codec's own `decode()`; field names, units and value tables come from the declarative grammar
  in `data/protocols/<codec>.json` — so the Parser shows the vendor's field names with per-field
  byte offsets, and clicking a row highlights exactly those characters in the pasted text.
  Grammars are read from the user's repository first and the shipped copy as fallback, so field
  names survive a custom `RUNWAY_DATA_DIR`, a cleared `data/`, and a bare exe.
  CAN payloads are expanded to one row per frame; anything the grammar hasn't caught up with is
  still listed (positionally, or from the decoded record), so a codec with no grammar published
  yet is inspectable the moment it is dropped in.
- **A Protocol selector on the Parser page**, populated from `/api/codecs` — "Auto-detect" by
  default, with any registered codec selectable to force one. The result line names the codec
  that actually read the data, and the Dashboard sync now ingests through that same codec.

### Fixed
- **The Parser could not read any ASCII protocol at all.** Input was filtered down to its
  hex-looking characters before anything parsed it, so a pasted `$MMI,…`/`$GPRMC,…` sentence was
  shredded into nonsense bytes and came back "0/0 packet(s), 0 record(s)"; `/api/parse` then ran
  the Teltonika explainer regardless of what the data was. The endpoint now decides *hex dump vs
  device text* from the content (a paste's missing line terminator is restored for line-framed
  protocols), and picks the codec by detection or by the operator's choice. Text that no codec
  recognises is reported as such instead of being passed off as a decode; an unrecognised **hex**
  dump still falls back to a generic hex view.
- **Auto-detect no longer mis-identifies a Zenithra device as AIS-140.** The device opens the
  connection with a plain AIS-140 `$LGN` login and only then sends `$MMI` data. Since a
  session's codec is chosen once, from the first sample, auto-detect scored `ais140` 0.90 on
  that login and locked the session onto it — every subsequent `$MMI` packet then decoded as
  `message_type: "unknown"` with no fields (seen live on the bench at :4082). The Zenithra
  codec now recognises its own login: the protocol-version field carries the literal `SCAN`
  where AIS-140 sends `AIS140`, which scores 0.95 and wins — while a genuine Locate 140B login
  is untouched and still goes to `ais140`. Covered by a regression test that runs the real
  captured session (login + both `$MMI` packets) through `_pick_codec` end to end.
- The AIS-140 login's firmware-by-content match now accepts a build letter (`5.1.29C`, not
  just `01.3.0`), so firmware shows up in device details for this family.

### Changed
- `Ais140Codec` exposes its sentence-length resync valve as `max_sentence` (was a module
  constant) so a derived sentence family can raise it — Zenithra allows 8 KB because a busy
  CAN bus makes sentences far longer than 2 KB — and its placeholder device id is now derived
  from the codec name (`zenithra-unknown` for the derived codec, `ais140-unknown` unchanged).

### Notes / open items
- Confirmed against a live session: the `$LGN` login plus `$MMI` NR captures with and without
  CAN. Two field readings remain **provisional** and are flagged in both the codec docstring
  and the JSON grammar — field 26 read as `tamper_alert` (assuming AIS-140's `emergency_status`
  is the dropped field) and field 38 read as `frame_number` (not an odometer); neither blocks
  CAN work, so they stay as-is until the vendor doc lands. Only the `NR` packet type has been
  captured; health/emergency sentences have not been seen on this model.
- No QC coverage profile ships for `zenithra` yet, so a QC session on this protocol falls back
  to the generic pass/fail checks instead of a coverage checklist. The downlink command set is
  undocumented for this model, so no `ota_commands` are declared (the sender falls back to its
  free-text/hex mode rather than offering an unverified command).

## [1.1.22] — 2026-07-17

Fix release. Headline: **a QC session now retains its full Summary across any
interruption** — Stop/Resume, a crash, or a full server restart. Resuming yesterday's
session no longer starts the packet tables blank.

### Fixed
- **Resuming a QC session no longer blanks the Summary.** Coverage and device details
  were computed from in-memory records only, so a resume (or a server restart followed
  by Resume) restarted the Summary from zero even though every packet was safely in the
  session's capture file. On resume the capture sink now **rehydrates**: it re-decodes
  its own append-only capture file (`QcCaptureSink.rehydrate`), restoring the packet
  tables, per-item counts and captured samples, device identity, the coverage ledger and
  original receive timestamps — the session carries on exactly where it left off. The
  protocol persisted on the session row is used for re-decoding; sessions that never
  identified fall back to auto-detection, and unreadable lines are skipped.
- **The Summary survives a crash too**: per-device coverage/verdict/packet count are now
  persisted to the DB every few seconds while a session runs (previously only on a clean
  Stop), so a session that dies without one still shows its data in the list, before any
  Resume.

### Changed — QC Capture UI
- **Summary is now the default (and primary) view** of the Live Packets pane; Raw is the
  secondary tab. The Summary/Raw toggle lists Summary first. (An operator who explicitly
  switched to Raw before keeps that choice — only the first-time default changed.)
- **Feed header decluttered**: the "session #N · :port" caption is gone from the QC feed
  header (that identity already lives on the tab and the session strip); the **Checks**
  popup button, and the **Pass w/ conditions** / **Fail** buttons in the Summary panel,
  were removed. (The underlying `/finalize` and `/report` endpoints are unchanged.)

### Changed — Send command
- **IP/port SET drafts pre-fill with the live session's port.** Picking PIP/EIP/GIP in
  the send dialog now drafts `+S*R:SET:EIP#182.71.127.231,<this session's port>;` — the
  host is the bench's constant server address (authored in the protocol definition,
  updated to `182.71.127.231`), the port is whatever port the tab is listening on, so
  the device dials back to the right listener without hand-editing.
- **Send to multiple devices at once**: when several devices are connected on the port,
  the send dialog lists them with tick-boxes (the device whose ✈ was clicked is ticked
  by default) and sends the command to every ticked one, reporting per-device failures.

### Changed — QC report
- **The Summary sheet no longer carries the per-packet coverage columns** (Login …
  Device Tampered). That detail stays on each device's own IMEI sheet; the lot Summary
  keeps one readable row per device (identity, bench columns, required-coverage
  fraction, verdict, remarks) — matching the reference sheet's altitude.

### Changed — QC skip rule
- **Only bench-waivable items can be skipped.** The QC profile now marks items
  `"skippable": true`; a Skip control appears in the Summary only for those, and
  `POST /api/qc/{id}/skip` rejects (HTTP 400) any attempt to skip a mandatory item — so
  the rule is enforced server-side, not just hidden in the UI. For AIS-140 the skippable
  set is the five hard-to-trigger events: **Low battery, Low battery removed, Harsh
  Braking, Harsh Acceleration, Rash Turning** (all now `required` + `skippable`); every
  other item is mandatory. `skippable` is carried through `engine.coverage` and the ledger.

### Packaging — first shareable release
- **`Runway.exe` now seeds the protocol repository on first run.** The protocol
  definitions (the `ota_commands` catalogue behind the send-command dialog, incl. the
  AIS-140 IP/port setters) ship inside the exe and are copied into an empty
  `data/protocols` on startup — so the send catalogue works from a bare exe and survives
  a `data/` reset. Bundled via `packaging/Runway.spec`; seeded by `app.py:_seed_protocols`.
- **Release ZIP refreshed for the QC workflow**: `packaging/build_release.py` now writes a
  QC-first `GETTING_STARTED.txt` (Device QC session → coverage → report → send commands)
  and ships `samples/ais140_sample.log` alongside the Teltonika/NMEA samples.

### Added
- **Coverage ledger** (`runway/qc/ledger.py`): per-device QC state now *accumulates* —
  every record is folded in exactly once as it arrives, so a long session can no longer
  "forget" coverage it already earned when old packets fall out of the bounded in-memory
  window. Also the enforcement point for field validation: one implausible packet marks
  its coverage item INVALID (with the reason) instead of a silent OK.
- **Field validation (Step 2)**: packets matched to a coverage item are sanity-checked
  (coordinate ranges, 0,0-with-fix, event time >1 day in the future). An invalid item is
  a deviation — the device settles as **Conditional Pass** (never a clean PASS) with the
  reason printed on the report's Deviations section.
- **Lot details typed in-app** (▤ Lot details on the QC tab): the bench-only report
  fields — Mobile No, Data on Intouch, All I/O, Harness, Battery Charging, Ignition
  On/Off (Intouch), Plugged/Unplugged Alarm, COTA/FOTA, SMS/GPRS/Serial Command, power
  consumption (mA), Field Testing, Remarks per device, plus Lot/Batch id, Lot Size, PCB
  Number, Observation, Corrective Action and Quality Approved By — are saved with the
  session (`POST /api/qc/{id}/lot`) and **merged into the exported workbook**, so the
  download is the complete deliverable with no hand-editing in Excel.
- **Skip/finalize audit trail**: every skip asks for a reason (one prompt on "Skip
  missing"), and the report's Deviations section prints *what was skipped, why, by whom
  and when*. Finalize actions are audited too (`audit` on the session view).
- **Printed-report archive**: every successful export is saved verbatim under
  `data/reports/` (with a SHA-256 in the session's print log) and the export dialog
  shows "Printed ×N — last …", so the exact handed-over file is always recoverable and
  a re-print of a since-edited session is a conscious act.
- **Environment self-check banner**: every page compares its own version against
  `/api/health` and warns "restart the server" on mismatch (the stale-process trap),
  and warns when the server's Python lacks `openpyxl` (which used to surface only when
  the operator clicked Download). The server also logs a boot-time warning.
- **Daily DB backup**: one consistent copy per day under `data/backups/` (kept 14 days,
  sqlite online-backup API) — serials, verdicts, lot details and audit now live in that
  file, so it must never be the only copy.
- **`run-server.bat` pins the server's Python**: it creates and uses a repo-local
  `.venv` (installing dependencies on first run / when missing) instead of whatever
  `python` is first on PATH — three different Pythons on the bench machine is how the
  openpyxl surprise happened.

### Housekeeping
- `ais140-unknown` added to `UNKNOWN_DEVICE_IDS` — the placeholder could previously
  surface as a real device row in the QC UI before the IMEI was learned.
- Frozen (PyInstaller) builds: `ais140` added to the plugin fallback list so the codec
  still registers when module discovery comes up empty inside the archive, and the
  default QC coverage profiles (`runway/qc/profiles`) are now bundled into the exe —
  previously the packaged build had no coverage catalogue unless a user copy existed
  under `data/qc/profiles`.

[1.1.22]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.22

## [1.1.21] — 2026-07-17

Feature release. Headline: the **downloadable QC report** (XLS/CSV) — the per-lot
deliverable modelled on the bench's reference workbook (LOCATE 140B), exported straight
from the Capture page.

### Added — QC report export (XLS/CSV)
- **⤓ Report** button in the QC tab's feed header opens the export dialog: pick
  **Excel workbook (.xlsx)** or **CSV (.zip — one file per sheet)** and download.
  The format choice is remembered (localStorage).
- **Device selection lives in "Devices on this port"**: the same tick that filters the
  Raw feed now also decides which devices are in the report (all ticked by default).
  The dialog lists exactly who's included — serial badge, IMEI, live verdict — and
  updates as ticks change.
- **Sheet 1 "Summary"**: inspection header block (date, item name from the QC profile,
  session/port/operator, total devices tested, blank **Lot Size**) + one row per
  device — S.No, IMEI, serial, firmware, vendor, packet count, then **one column per
  coverage item** (OK / SKIP / MISSING / — / INVALID), required-coverage fraction,
  verdict (colour-filled), remarks (skipped/invalid items).
- **Reference-sheet placeholders for the bench-only fields**: every column of the lot
  sheet the utility can't observe ships **blank for hand entry** — Mobile No, Data on
  Intouch, All I/O (On Static), Harness, Battery Charging, Ignition On/Off (Intouch),
  Plugged/Unplugged Alarm on Intouch, COTA, FOTA, SMS/GPRS/Serial Command, power
  consumption (mA) with/without ignition, Field Testing — while GSM Fix / GPS Fix /
  Data on Static auto-fill "OK" from the observed traffic. Below the table sits the
  reference sign-off footer (Observation, Firmware Version, PCB Number, Corrective
  Action, Final Result, Quality Tested/Approved By, tested-on IP:Port).
- **One sheet per device, named by its IMEI** — the QC Summary view on paper: device
  details (identity, vitals, last position/battery), then the Packets / Emergency
  server / Alerts tables with status, code, required, count, last-received time and
  the **actual raw packet** (ASCII for text protocols per the profile's `raw_view`,
  spaced hex otherwise). Deviations listed at the end; protocols without a coverage
  profile fall back to the pass/fail checks table.
- Backend: `GET /api/qc/{id}/export?fmt=xlsx|csv&devices=<imei,...>` (empty = all
  devices; works on live *and* stopped sessions). Builder in `runway/qc/report.py`
  (framework-free); new runtime dependency **openpyxl** (lazy-imported — CSV export
  works without it), bundled in the PyInstaller spec.
- Download name mirrors the bench's own files: `<item label> QC-<dd-mm-yyyy>-S<id>`.
- **Completion gate**: the report is the lot's *final* deliverable, so a device still
  **IN PROGRESS** blocks printing. The export dialog highlights the incomplete devices,
  names the missing required items, and offers one-click **Skip missing** (the device
  closes as **Conditional Pass**) — Download stays disabled until every included device
  has settled (pass / conditional / fail / timeout). The API enforces the same rule
  (HTTP 409), and unticking a device simply leaves it out.

### Changed
- The old checks popup's button is now **✓ Checks** (its JSON/TXT downloads are
  unchanged) — "Report" now always means the XLS/CSV deliverable.

### Fixed
- **A QC session that ended without a clean Stop lost its protocol identity** (the
  self-heal path persisted devices/coverage but not `identified_as`/`packet_count`),
  so the Summary and the report fell back to HEX raw packets and 0 packets. Three-layer
  fix: identity is now written to the DB the moment the first packet identifies the
  protocol; the self-heal path persists identity + counters; and on startup, rows
  already damaged get their protocol back by matching their persisted coverage keys
  against the QC profiles.
- **Raw view is now declarative**: the session carries the profile's `raw_view`, the UI
  and the report use it instead of a hardcoded protocol list — and when the identity is
  genuinely unknown, printable packets are sniffed and shown as ASCII rather than hex.

[1.1.21]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.21

## [1.1.20] — 2026-07-16

Feature release. Headline: **two-way communication** — a command picker sends the
Locate 140B's GPRS bench commands to a connected device — plus live **device vitals**
and a dedicated **Emergency server** coverage section.

### Added — downlink command sender (two-way)
- The device row's Send control is now a compact paper-plane **icon** (name appears on a
  ~2s hold-hover tooltip). Clicking opens the restructured send dialog:
  **Send to `<IMEI>`** (the transient ip:port moves to a hover tooltip) → **Command type**
  (SET / GET / CLR) → **Command** list *filtered to the commands supporting that type*
  (e.g. STOP is SET-only) → editable prefilled value. Mode (text/hex) hides for catalogue
  commands — the format is device-defined — and returns for custom entry.
- The 13-command **bench GPRS catalogue** (VRN, STOP, HBL, HAL, RTL, LOGS, LOG2, EMTD,
  HPTI, PIP, EIP, GIP, APN with real SET/GET/CLR strings) lives declaratively in
  `data/protocols/ais140.json` → `ota_commands` — edit the JSON to change the picker.
  That file is now **tracked in git** (previously ignored under `data/`): it's a shared
  cross-tool asset the QC references and the send picker both depend on.

### Added — QC Summary
- **Device details vitals**, live from the newest traffic: last packet type
  (Location live/history · Alarm (id N) · login/health), when it landed on the utility
  (IST), latest lat/long + satellites + fix status, and external/internal battery
  voltages. Position/battery each fall back to the newest record that carries them.
- **Emergency server section** (between Packets and Alerts): EPB traffic split into
  **Emergency ON (EMR)** and **Emergency OFF (SEM)**, both required, with received
  sentences inline and the EPB reference (updated to a real firmware-2.2.5 capture)
  highlighted per row. The old combined "Emergency packet" row is gone.
- Removed the "Test cases" chips from the Summary (still computed backend-side and in
  the report); removed the repetitive device-nav/verdict/session-meta header — the shown
  device is bound by its Serial badge, and session/port/verdict live once in the feed
  header, session strip and status pill ("session #N" now spelled out there).

### Fixed
- Selecting "— custom —" in the send dialog silently resolved to the first catalogue
  command (empty option value coerced to index 0).
- Item labels in the coverage tables now word-wrap (an app-wide `tbody td` nowrap rule
  forbade line breaks); tiny Req/Count cells no longer break "yes" mid-word; the QC tab
  title no longer shows an IMEI; the feed header wraps on narrow windows instead of
  overlapping; the per-row reference-example substitution was off by one token.

[1.1.20]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.20

## [1.1.14] — 2026-07-16

Feature release. Headline: the QC feed gains an inline **Raw | Summary** view (no more
popup), and a **persistent, IMEI-keyed device registry** gives every device a stable
serial number and automatic recall of its last QC result.

### Added — QC Summary view (inline, replaces the details popup)
- **Raw | Summary toggle** right on the Live Packets pane: *Raw* is the packet stream,
  *Summary* shows the selected device's full qualification view (device details, Packets +
  Alerts coverage, deviations, test cases, Pass-with-conditions / Fail actions) inline in
  the same pane — live-updating, no modal overlay. The status pill (`IN PROGRESS · 2/13`)
  stays visible in both views and jumps to Summary on click.
- **Multi-device Raw selection**: each QC device row has a checkbox (plus All/None) to show
  every device, a partial set, or a single device in the Raw stream — same pattern as the
  Testing tab's per-peer filtering. Row-click still separately selects the Summary device.
- **Received column** in the coverage tables shows an inline preview of the actual latest
  captured packet per item (click for the full parsed/ASCII/HEX view), replacing the old
  "view" button; the Skip checkbox hides once an item has been received.
- **Per-row reference tooltips**: hovering an Item name (and only the name, after a ~2s
  rest) shows its description plus the reference sentence rewritten for *that* row — the
  expected packet-type code and alert id substituted in and highlighted (e.g. Emergency
  state ON highlights `EA`,`10`), instead of one generic example for every alert.
- Coverage tables use fixed column widths with proper word-wrap (Received gets the
  majority share); alert rows read `Label (id - CODE)`, e.g. `Connect back to main
  battery (6 - BR)`.

### Added — persistent device registry (IMEI-keyed)
- New durable `qc_devices` table: every device that ever identifies on a QC session gets a
  **stable serial number** (`#1`, `#2`, …) — the same IMEI keeps its number across
  reconnects, sessions, and restarts; a genuinely new IMEI gets the next one. Shown as a
  badge in the device list and in the Summary device details.
- **Automatic QC-status recall**: the moment a known IMEI connects to any listener, the
  Summary panel shows its last FINAL result ("Previously tested: CONDITIONAL (session
  #N)") while the new run is still in progress. In-progress sessions never overwrite the
  registry — only final verdicts (pass / conditional pass / fail / timeout) are recorded.

### Fixed
- Coverage-table Item labels never wrapped (an app-wide `tbody td { white-space: nowrap }`
  rule silently forbade line breaks) and overflowed under the Req column; they now wrap
  within their own cell.
- The QC tab title no longer shows an IMEI (a port can host many devices) — protocol only.
- On narrow windows the feed-header controls could overlap and block clicks; the header
  now wraps onto a second row.
- The per-row reference example previously corrupted two fields via an off-by-one token
  substitution (the `$NMP` header occupies token 0); fixed and verified against the real
  sentence layout.

[1.1.14]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.14

## [1.1.8] — 2026-07-15

Feature release. Headline: **Device QC** grows from a basic pass/fail into a real
per-device qualification workflow, and **Live TCP + Device QC merge into one Capture
page** with a per-tab Testing/QC mode.

### Added — Capture page (Live TCP + Device QC merged)
- **One page, one core module** (`capture.html`/`capture.js`) replaces the separate Live TCP
  and Device QC pages — `tcp.html`/`qc.html` now redirect here. Each session tab picks
  **Testing** (raw inspection, any protocol, log/download — today's Live TCP behavior) or
  **QC** (validated per a coverage profile) when it's started; both kinds of tabs coexist,
  each with its own feed chrome.
- Shared core: browser-style tabs, per-port/session device list, colour-coded live feed
  (live/history/alarm/unknown, ⓘ legend popover), packet/send modals, collapsible device
  list, draft/"+" new-session flow — implemented once instead of duplicated across pages.
- Rail collapses "Live TCP" + "Device QC" into a single **Capture** item.

### Added — Device QC: per-device coverage & verdict
- **Per-device grouping (by IMEI)**: a device that reconnects (new peer/port) merges into
  one row instead of showing as a duplicate; QC coverage, checks, and verdict are computed
  **per physical device**, not lumped across the whole port session. Multiple devices on one
  QC port each get their own coverage view — click a device row to select it (drives the
  feed, the summary pill, and the details modal).
- **Coverage-driven verdict states**: `in_progress → timeout → pass / conditional_pass /
  fail`, replacing a flat pass/fail. A session goes to **timeout** after ~10 minutes of no
  data (regardless of whether a device ever identified) and can be **Resumed**. A required
  item the operator marks **optional/skip** no longer blocks completion — it settles as
  **Conditional Pass** instead, with every skip/deviation listed for the record.
  Operator actions: **Pass with conditions** / **Mark Fail** / clear back to automatic.
- **QC details modal redesign**: device details (IMEI / firmware / vendor id), coverage
  split into **Packets** and **Alerts** sections (alert rows show `Label (id - CODE)`,
  e.g. `Connect back to main battery (6 - BR)`), a **Received** column linking to the
  captured sample packet, and hover-to-see the item's description + reference packet +
  what was actually received. Actions live in the modal header (top-right) to keep the
  body free for coverage. A single verdict+coverage pill (e.g. `IN PROGRESS · 2/13`)
  replaces the old separate checks-chip row and duplicate verdict badge.
- **QC coverage profile** (`runway/qc/profiles/<protocol>.json`, overridable under
  `data/qc/profiles/`): declares the expected packet types + alert ids for a protocol,
  which are required vs optional, and display grouping — editable without code changes.
- New endpoints: `POST /api/qc/{id}/skip`, `POST /api/qc/{id}/finalize` (both per-device);
  `/api/qc` and the report now return per-device `devices[]` with their own coverage.

### Fixed
- **AIS-140 / Locate 140B codec**: real firmware 2.2.5 sends the health packet as `$HEL`
  (the v1.9 doc only names `$HLP`/`$HLM`) — health packets were misclassified as
  `unknown`; now parsed correctly. `alert_id`, `packet_type`, `vendor_id` and
  `firmware_version` are now exposed on every PVT/NMP record (needed for coverage keying
  and the device-details panel).
- **Live TCP / Capture: sessions could not be closed while a device stayed connected.**
  `TcpServerSource.close()` called `server.wait_closed()`, which blocks forever with a live
  socket still open — a cancelled session's task never left the running set, so stop+close
  looped on "stop the session first". `close()` now drops every live connection (and bounds
  the wait with a timeout) so teardown always completes.
- QC sessions surviving a server restart (stale `job_id`, "running" forever) are now
  reconciled on startup and self-heal on view; closing a tab no longer resurrects via a
  late poll/WebSocket update.

[1.1.8]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.8

## [1.1.0] — 2026-06-30

Feature release. Headline: a **Live TCP server**. Also adds the **Protocols** repository,
the **Converter** (CanPlus XML → JSON/DBC), and a **standalone `Runway.exe`** build.

### Added — Live TCP server (headline feature)
- **Capture → Live TCP** page: start a TCP listener (codec + port + bind address), see all
  running listeners (status, uptime, records, bytes, connected clients), and stop them — all
  updating live over the WebSocket.
- **Connected-clients list** per listener: the TCP source now emits connect/disconnect events
  to the hub (`{"type":"conn"}`); the running-job view reports live `records`/`bytes`/`peers`.
- **Live decoded-packet feed** on the page (time / device / type / position / speed).
- **Downlink / send command** to a connected device — `POST /api/send/{job_id}` (`text`
  built via the codec's `build_command`, or raw `hex`); `TcpServerSource.send(peer, data)`.
- **Remembered settings**: last codec/port/bind stored in `Config`; `GET /api/listen/defaults`
  pre-fills the Start form (no auto-start — fresh-start convention kept).
- Decoded live traffic continues to flow to the Dashboard store + WebSocket as before.
- **Resume / Remove**: a stopped listener can be resumed **in place** (same card, same
  config) instead of spawning a new one (`POST /api/resume/{id}`); finished/stopped cards
  can be removed (`DELETE /api/jobs/{id}`).
- **Raw view for unknown packets**: the live feed shows each packet's raw bytes with a
  **HEX / ASCII** toggle — so traffic from an unmatched protocol is still readable.
- Labels: **Codec → Protocol**, **Bind → IP**; the IP field auto-fills with this machine's
  detected LAN IP and the page shows where devices should dial in. Protocol defaults to
  **Auto**; the **last-used port** is remembered.
- Fixes: a stopped listener's uptime now **freezes** (it no longer keeps counting after the
  server/listener stops). Tests run against an isolated `RUNWAY_DATA_DIR` temp folder so they
  never overwrite your real remembered settings.
- **Generic listener**: explicit "Generic (raw)" protocol option; generic/unmatched rows show
  **timestamp + the complete packet** (no wrap, horizontal scroll for any length); click a row
  for the full HEX/ASCII view.
- UX: listeners show a single consistent `IP:port`; **Stop/Resume** stay in one position with a
  spaced, confirming **Remove**; live card refresh + instant Stop; the packet feed persists
  across navigation (re-seeds from the server snapshot). The HEX/ASCII view mode is remembered.
- **Per-client control**: pick which connected clients' packets appear in the feed (hide ≠
  stop) with per-client checkboxes + All/None; each client shows a **packet count** and
  **idle-since-last-packet**, tracked **server-side** so they survive page reloads.
- **Continuous packet logging** (server-side): **Start/Stop log** streams every new packet to
  a file in a **chosen folder + filename** (TXT/CSV, ASCII/HEX), timestamped
  `DD-MMM-YYYY HH:MM:SS:zzz`. Durable — **flushed per packet + fsync'd** (crash/power-safe),
  **append-only** (never truncates). Separate from the one-time **Download**; a **log-file**
  button downloads the server file. Endpoints: `POST /api/log/start|stop`, `GET /api/log/status|file`.
- Feed: sticky **`Timestamp | Payload`** header; ASCII view renders `CR`/`LF` as line breaks
  so multi-sentence dumps read cleanly. Destructive **Close / Clear / Stop** highlighted red.

### Added — Standalone build
- **`Runway.exe`** one-file Windows build via PyInstaller (`packaging/Runway.spec` +
  `packaging/runway_main.py`) — teammates run it with no Python install; double-click starts
  the server + opens the dashboard. `DATA_DIR`/`WEB_DIR` are now frozen-aware and plugin
  discovery has a frozen fallback so codecs still load inside the exe. See `packaging/README.md`.

## [1.0.0] — 2026-06-30

First release. Runway — "where telematics data lands & takes off" — parses, visualizes and
relays telematics packets from any IoT tracking device, regardless of OEM/protocol.

### Core
- **Source → Codec → Sink pipeline** with a normalized `UnifiedRecord` contract; streaming,
  framing-aware decode (`decode(buffer, session) → DecodeOutput(consumed, …)`), per-session
  codec caching, and plugin auto-discovery (`@register_codec/source/sink`).
- **Codecs:** Teltonika (AVL Codec 8 / 8E / 16 + GPRS 12 / 13 / 14, IMEI handshake,
  CRC-16/IBM, server ACK), GT06 / Concox, NMEA-0183, generic hex, and a copy-me template.
- **Sources/sinks:** file replay, TCP server; console, in-memory store, WebSocket broadcast,
  webhook, TCP relay.

### App & UI
- **FastAPI** REST + WebSocket server hosting a single-page UI with a collapsible left rail
  (Runway banner, Live/Demo mode).
- **Dashboard:** live map, device registry with multi-select visibility + source/liveness
  badges, recent-packets window, click-to-highlight on the map.
- **Packet Parser:** multi-tab (one independent session per device), paste/upload hex,
  streaming decode with live progress, resilient skip-and-resync on corrupt frames,
  transposed signal grid, AVL-ID → name resolution, foldable map, selective CSV export,
  Summary panel; state persists per browser session.
- **Configuration → Signatures:** repository to add/preview/edit/delete decode signatures,
  with signature-driven sub-signal visualization in the parser.
- **Configuration → Protocols:** format-agnostic repository of transferable parser files
  (`.py`, `.json`, `.yaml`, …) — drop a file in, another utility can parse with it.
- **Tools → Converter:** upload a CanPlus `*.Config.XML` vehicle signature, view/edit the
  CAN signals, and export to **JSON** and **DBC**.
- **Diagnostics → Logs:** rotating activity log + a kept errors/warnings log, with a viewer
  (source/level filter, search, auto-refresh, download, clear) and a global exception handler.

### Persistence
- Thread-safe SQLite storage rail (`Storage` + `Config`) under `data/` (git-ignored).
  A fresh server/browser session starts clean; the parser retains within a session.

### Notes
- Internal connectivity verified: `runway/convert` is isolated (lazily imported by its
  endpoints; not a registered plugin), so the pipeline, dashboard and live store/hub are
  unaffected.

[1.1.0]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.0
[1.0.0]: https://github.com/alishahbaz7/Runway/releases/tag/v1.0.0
