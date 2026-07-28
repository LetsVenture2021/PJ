"""Compatibility alias for :mod:`ops.images.service`."""
import importlib
import sys


_service = importlib.import_module("ops.images.service")
sys.modules[__name__] = _service
