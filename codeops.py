"""Compatibility alias for :mod:`ops.code.service`."""
import importlib
import sys


_service = importlib.import_module("ops.code.service")
sys.modules[__name__] = _service
