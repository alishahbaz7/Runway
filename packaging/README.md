# Packaging — standalone `Runway.exe`

Build a single-file Windows executable your teammates can run **without installing Python**.

## Build

From the **repo root**, in the project's Python environment:

```bash
pip install pyinstaller
pyinstaller packaging/Runway.spec --noconfirm
```

Output: **`dist/Runway.exe`** (~26 MB).

## Run / share

- Send `dist/Runway.exe` to your teammate (it's fully self-contained).
- They **double-click it** → the server starts and the dashboard opens at
  <http://127.0.0.1:8080>. A console window shows the version/branch banner, the URL, and
  live logs.
- On first run a **`data/` folder is created next to the exe** (SQLite DB, `runway.log`,
  and the signatures/protocols repositories). Delete it to reset to a clean state.
- To connect real devices, open **Capture → Live TCP** and start a listener on the port
  your trackers dial (bind `0.0.0.0` so it's reachable on the LAN).

### CLI flags still work

```
Runway.exe                       # serve + open browser (default)
Runway.exe serve --port 9000 --no-browser
Runway.exe codecs
Runway.exe replay dump.hex --codec teltonika
```

## Notes

- **Windows Defender / SmartScreen** may warn on an unsigned exe — "More info → Run anyway".
  Code-signing removes this but isn't set up here.
- The build is **not** committed to git (`dist/` and `build/` are ignored). Rebuild it, or
  attach `Runway.exe` to a GitHub Release for distribution.
- The exe is Windows-only and matches the Python/arch it was built on. For macOS/Linux,
  run `pyinstaller packaging/Runway.spec` on that platform, or share the wheel/source.
