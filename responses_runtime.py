"""Compatibility alias for :mod:`ops.realtime.orchestration`."""
import importlib
import sys


_orchestration = importlib.import_module("ops.realtime.orchestration")
sys.modules[__name__] = _orchestration
