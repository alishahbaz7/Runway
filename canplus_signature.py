"""Standalone CLI shim — the CanPlus signature parser now lives in the package.

Implementation: ``proinfo/convert/canplus.py`` (imported by the Converter UI too).
This shim keeps ``python canplus_signature.py <file.xml>`` working from the repo root.

    python canplus_signature.py "MG_ZS EV_2020.Config.XML"            # table
    python canplus_signature.py "MG_ZS EV_2020.Config.XML" --dbc      # export DBC
    python canplus_signature.py "MG_ZS EV_2020.Config.XML" --json     # export JSON
"""

from proinfo.convert.canplus import (  # noqa: F401  (re-export for convenience)
    CanSignal, CanSignature, extract_bits, load, model_to_dbc, parse, to_model, _main,
)

if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
