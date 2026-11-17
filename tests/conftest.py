"""Test fixtures / global setup.

Isolate all writable state (SQLite DB, logs, signature/protocol repositories, remembered
settings) in a throwaway temp dir so the test-suite never pollutes the developer's real
``data/`` folder — e.g. a listener test used to overwrite the remembered listen port.
Must run before any ``runway`` module that reads ``RUNWAY_DATA_DIR`` at import time.
"""

import os
import tempfile

os.environ.setdefault("RUNWAY_DATA_DIR", tempfile.mkdtemp(prefix="runway-test-"))
# A run from source reports usage to the real server; the test suite never does (tests that check
# reporting build their own reporter with a fake transport).
os.environ["RUNWAY_USAGE"] = "off"
os.environ.pop("RUNWAY_USAGE_URL", None)
