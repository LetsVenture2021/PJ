"""Compatibility alias for :mod:`ops.strategy.service`."""
import importlib
import sys


_service = importlib.import_module("ops.strategy.service")
sys.modules[__name__] = _service
