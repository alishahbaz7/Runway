# Packaging — the launcher install, and releases copies update to

People pin **one program**, `Runway.exe` — a small launcher — and never reinstall: every copy
checks the release channel on start and updates itself (the app-updates skill; BluePulse's design).
Everything in a release carries one name, **`Runway-v<version>`** — the zip, the version folder and
the program — spelled once, in `build_release.py` (`RELEASE_NAME`, from `runway.__version__`).

## Build

From the **repo root**, in the project's Python environment (`pip install pyinstaller`; Pillow only
to redraw the icon):

```bash
python packaging/build_release.py --rebuild --no-carry    # program + launcher + zip + latest.json
```

| Output | What it is |
|---|---|
| `dist/Runway-v<version>.zip` | the install: unzip anywhere the person can write, pin `Runway\Runway.exe`. Copies already on a launcher install take it as their update |
| `dist/Runway-v<version>/` | the same install, runnable from here (its `data/` survives rebuilds) |
| `dist/latest.json` | version, notes, the zip's SHA-256 and size — what every copy reads |
| `dist/notes.md` | the CHANGELOG section, for the release text |

The pieces: `Runway.spec` (PyInstaller **one-folder** build into `build/pyinstaller/`),
`launcher/Launcher.cs` + `launcher/build.ps1` (the launcher, compiled by the C# compiler that ships
with Windows — nothing to install), `runway.ico` (drawn by `make_icon.py`).

## The install

```
Runway\Runway.exe                                   the launcher — the one program to start and pin
Runway\README.txt, CHANGELOG.md, samples\
Runway\data\                                        created on first start; shared by every version
Runway\launcher.log                                 one line per start: which version it started
Runway\versions\Runway-v<version>\Runway-v<version>.exe   + _internal\
```

- **Not Program Files** (updates could not land) and better not a OneDrive-synced folder.
- **Shift** while starting `Runway.exe` (or `--choose`) lists the installed versions — the way back.
  Three are kept: the running one, the newest, and the one before.
- **Coming from 1.1.25 or earlier** (a `Runway-v<x>` folder with `data\` beside the exe): move that
  `data` folder beside `Runway.exe` before the first start. Those copies have no updater, so the
  first launcher release is installed by hand; every release after it arrives by itself.

## Publish — so copies update

Releases are published on Runway's own **public** repo, **`alishahbaz7/Runway`**
(`runway/update/config.py`). On github.com → the repo → *Releases* → *Draft a new release*:

1. Tag `v<version>`, title `Runway <version>`, the text from `dist/notes.md`.
2. Attach `Runway-v<version>.zip` **and** `latest.json`.
3. Publish as the **latest** release — never a pre-release: copies read
   `releases/latest/download/latest.json`, and a pre-release is invisible there.

Then **watch a real copy update**: start an installed older version from its `Runway.exe`; it must
offer the new one, install it, restart into it, and Settings → Updates must show the new version.
`latest.json` may carry `"minimumVersion"` (`--minimum X.Y.Z`) — copies below it must update, with
no *Later*. Use it for critical fixes only.

## Rehearse without publishing

A released program's channel is fixed, but a test channel can be set from outside: start an
installed copy with `RUNWAY_UPDATE_MANIFEST_URL=http://127.0.0.1:8401/latest.json`, serve a newer
build's zip and a `latest.json` pointing at it with `python -m http.server 8401`, and click *Update*.
Set `RUNWAY_USAGE_URL` to a dead local address at the same time, or the test copy adds a row to the
real usage list.

## Usage reporting

Release builds report to the shared usage server (the app-control skill) as app "Runway"; the build
prints where. A copy released without it can never be monitored afterwards.

## Notes

- **Windows Defender / SmartScreen** may warn on unsigned programs — "More info → Run anyway".
- The command line still works on the program itself:
  `versions\Runway-v<version>\Runway-v<version>.exe serve --port 9000 --no-browser`, `codecs`, `--version`.
