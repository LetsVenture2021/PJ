"""Compatibility alias for :mod:`ops.docs.service`."""
import importlib
import sys


_service = importlib.import_module("ops.docs.service")
sys.modules[__name__] = _service
