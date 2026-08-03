"""Plugin registries + auto-discovery.

Each plugin module decorates its class with ``@register_codec`` /
``@register_source`` / ``@register_sink``. Calling :func:`load_plugins` imports every
module under the three packages so those decorators run, after which the maps below
are fully populated. This is what lets you "just drop a file in ``codecs/``".
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Type

from .interfaces import Codec, Sink, Source

log = logging.getLogger("runway.registry")

CODECS: dict[str, Type[Codec]] = {}
SOURCES: dict[str, Type[Source]] = {}
SINKS: dict[str, Type[Sink]] = {}

_loaded = False


def register_codec(cls: Type[Codec]) -> Type[Codec]:
    CODECS[cls.name] = cls
    return cls


def register_source(cls: Type[Source]) -> Type[Source]:
    SOURCES[cls.name] = cls
    return cls


def register_sink(cls: Type[Sink]) -> Type[Sink]:
    SINKS[cls.name] = cls
    return cls


# When frozen (PyInstaller), pkgutil.iter_modules can come up empty for a package that
# lives inside the archive. Fall back to this explicit list so plugins still register.
# Keep in sync when adding a plugin module (the normal filesystem path needs no change).
_FROZEN_FALLBACK = {
    "runway.codecs": ["teltonika", "gt06", "ais140", "zenithra", "nmea", "generic_hex",
                       "custom_template"],
    "runway.sources": ["file_replay", "tcp_server"],
    "runway.sinks": ["console", "memory_store", "ws_broadcast", "webhook", "tcp_relay"],
}


def _discover(package_name: str) -> None:
    pkg = importlib.import_module(package_name)
    names = [m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")]
    if not names:                                    # frozen / empty — use the fallback list
        names = _FROZEN_FALLBACK.get(package_name, [])
    for name in names:
        try:
            importlib.import_module(f"{package_name}.{name}")
        except Exception:  # a broken plugin must not take the whole app down
            log.exception("Failed to import plugin module %s.%s", package_name, name)


def load_plugins(force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    for pkg in ("runway.codecs", "runway.sources", "runway.sinks"):
        _discover(pkg)
    _loaded = True
    log.info("Loaded plugins: %d codecs, %d sources, %d sinks",
             len(CODECS), len(SOURCES), len(SINKS))


def get_codec(name: str) -> Codec:
    load_plugins()
    if name not in CODECS:
        raise KeyError(f"Unknown codec '{name}'. Available: {sorted(CODECS)}")
    return CODECS[name]()


def get_source(name: str, **kwargs) -> Source:
    load_plugins()
    if name not in SOURCES:
        raise KeyError(f"Unknown source '{name}'. Available: {sorted(SOURCES)}")
    return SOURCES[name](**kwargs)


def get_sink(name: str, **kwargs) -> Sink:
    load_plugins()
    if name not in SINKS:
        raise KeyError(f"Unknown sink '{name}'. Available: {sorted(SINKS)}")
    return SINKS[name](**kwargs)


def list_codecs() -> list[dict]:
    load_plugins()
    return [{"name": c.name, "display_name": c.display_name,
             "description": getattr(c, "description", "")} for c in CODECS.values()]
