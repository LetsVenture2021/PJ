"""Compatibility alias for :mod:`ops.presentations.service`."""
import importlib
import sys


_service = importlib.import_module("ops.presentations.service")
sys.modules[__name__] = _service
