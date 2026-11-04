# Packaging — standalone `Runway-v<version>.exe`

Build a single-file Windows executable your teammates can run **without installing Python**.
Everything in a release carries one name, **`Runway-v<version>`** — the zip, the folder it
unpacks to and the exe. The name is spelled once, in `build_release.py` (`RELEASE_NAME`, from
`runway.__version__`); `Runway.spec` imports it.

## Build

From the **repo root**, in the project's Python environment:

```bash
pip install pyinstaller
pyinstaller packaging/Runway.spec --noconfirm
```

Output: **`dist/Runway-v<version>.exe`** (~26 MB).

## Build a shareable ZIP (recommended for handoff)

```bash
python packaging/build_release.py                    # builds the exe if missing, then zips
python packaging/build_release.py --rebuild          # force a fresh exe build first
python packaging/build_release.py --carry-from DIR   # take data/ from this earlier install
python packaging/build_release.py --no-carry         # leave every data/ folder where it is
```

Output: **`dist/Runway-v<version>.zip`**, staged in **`dist/Runway-v<version>/`** — unpacks
to one folder of the same name containing:

```
Runway-v<version>.exe   the standalone app (no Python needed)
GETTING_STARTED.txt     how to run + test (and how to upgrade)
CHANGELOG.md            what's in this build
samples/                demo inputs (ais140, zenithra, teltonika, nmea) to try
```

Send that ZIP to your teammate. Neither the exe nor the ZIP is committed to git
(`dist/` is ignored) — rebuild it, or attach the ZIP to a GitHub Release.

**Data is never zipped and never deleted.** The staging folder is also a runnable install, so
a rebuild replaces only what the build makes and leaves a `data/` folder there alone. If an
earlier install under `dist/` holds data, the build copies it into the new folder; if more than
one does, it names them and exits with status 2 (the zip is still complete) instead of guessing
which one is live — re-run with `--carry-from` or `--no-carry`.

## Run / share

- They **double-click `Runway-v<version>.exe`** → the server starts and the dashboard opens at
  <http://127.0.0.1:8080>. A console window shows the version/branch banner, the URL, and
  live logs.
- On first run a **`data/` folder is created next to the exe** (SQLite DB, `runway.log`,
  and the signatures/protocols repositories). Delete it to reset to a clean state.
- **Upgrading:** each version unpacks to its own folder, so move the previous version's
  `data/` folder in next to the new exe **before** its first start — otherwise it opens empty.
- To connect real devices, open **Capture → Live TCP** and start a listener on the port
  your trackers dial (bind `0.0.0.0` so it's reachable on the LAN).

### CLI flags still work

```
Runway-v<version>.exe                       # serve + open browser (default)
Runway-v<version>.exe serve --port 9000 --no-browser
Runway-v<version>.exe codecs
Runway-v<version>.exe replay dump.hex --codec teltonika
Runway-v<version>.exe --version
```

## Notes

- **Windows Defender / SmartScreen** may warn on an unsigned exe — "More info → Run anyway".
  Code-signing removes this but isn't set up here.
- The build is **not** committed to git (`dist/` and `build/` are ignored). Rebuild it, or
  attach the release ZIP to a GitHub Release for distribution.
- The exe is Windows-only and matches the Python/arch it was built on. For macOS/Linux,
  run `pyinstaller packaging/Runway.spec` on that platform, or share the wheel/source.
