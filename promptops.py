"""Compatibility alias for :mod:`ops.prompting.service`."""
import importlib
import sys


_service = importlib.import_module("ops.prompting.service")
sys.modules[__name__] = _service
