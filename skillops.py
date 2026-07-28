"""Compatibility alias for :mod:`ops.skills.service`."""
import importlib
import sys


_service = importlib.import_module("ops.skills.service")
sys.modules[__name__] = _service
