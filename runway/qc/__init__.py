"""Device QC module — Target 1: intake + identify + record.

A QC session wraps a live TCP listener with a purpose-built capture sink
(:class:`QcCaptureSink`) that learns the device's protocol + id from the decoded
records and writes them to a per-session file on disk. Orchestration (starting the
listener, the DB row, the WebSocket events) lives in ``runway.api.app`` alongside the
existing Live TCP job plumbing it reuses.
"""

from .session import QcCaptureSink

__all__ = ["QcCaptureSink"]
