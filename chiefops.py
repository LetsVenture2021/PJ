"""Compatibility alias for :mod:`ops.chief.service`."""
import importlib
import sys


_service = importlib.import_module("ops.chief.service")
sys.modules[__name__] = _service
