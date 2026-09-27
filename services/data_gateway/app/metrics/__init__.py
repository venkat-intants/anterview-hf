"""The governed metric layer — PH5-C2.

One definition of every hiring metric, read by every consumer (the HR
analytics endpoints, the requisition dashboard, the copilot, the nightly
watcher). See ``app.metrics.definitions`` for the registry and
``app.metrics.compute`` for the one query shape every consumer calls.

This package never writes. See ``app.metrics.definitions`` module docstring
for the guarantee and the test that holds it.
"""

from __future__ import annotations
