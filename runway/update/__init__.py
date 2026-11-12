"""Over-the-air updates for Runway (the app-updates skill; BluePulse's updater, ported).

People pin one program — the launcher, ``Runway.exe`` — and every copy updates itself: on start it
reads ``latest.json`` from the public release channel, offers a newer version with its notes,
downloads it, refuses it unless the SHA-256 matches, unpacks it beside the running version and
restarts into it through the launcher. The running version is never touched; a failure anywhere
leaves it as it was, and is said only in Settings.

    config.py     the channel (the GitHub releases of alishahbaz7/Runway) and names
    semver.py     x.y.z comparison
    manifest.py   latest.json, read and validated
    transport.py  HTTP: text with a size cap, downloads hashed while they stream, time limits
    installer.py  the launcher install: unpack beside, rename into place, keep three versions
    service.py    the states and the flow the API and the page drive
"""
