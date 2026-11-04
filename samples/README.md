# samples/

Drop real device captures here to test codecs and replay them into the dashboard.

Accepted formats (auto-detected by the file-replay source):

- **raw** — the file *is* the bytes (binary capture, or NMEA text).
- **hex** — one continuous hex string, e.g. `78780d01035109...`
- **hexlines** — one hex-encoded packet per line.
- **log_export** — a platform message-log export, one row per message:
  `Wed Nov 04 12:05:17 IST 2026 | <imei> | ,"MMI,...*37 | null"`. Unwrapped back into the
  stream the server received (`$` restored, rows in arrival order). The Parser's **Upload**
  button reads these too.

Examples:

```bash
runway replay samples/my_gt06_capture.hex --codec gt06 --hex
runway replay samples/nmea_drive.log --codec auto
```

Tip: capture live traffic with `runway listen --codec auto --port 5050` and tee the
raw bytes, or sniff with Wireshark/tcpdump and export the TCP payload as hex.
