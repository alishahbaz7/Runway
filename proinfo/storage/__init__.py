"""Storage layer: the in-memory live store + WS hub, and the durable persistence rail.

Process-wide singletons live in :mod:`proinfo.storage.live`:
- ``store`` / ``hub`` — in-memory live state + WS fan-out (hot path).
- ``db`` / ``config`` — durable SQLite rail (``Storage``) and ``Config`` over it.
"""

from .live import config, db, hub, store  # noqa: F401

