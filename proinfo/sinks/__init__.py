"""Output plugins. Each module registers a Sink via @register_sink.

Sinks consume normalized records: print them, store them, broadcast them to the
dashboard, or relay them onward to a platform (TCP / webhook / API).
"""
