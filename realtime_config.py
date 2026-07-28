"""Compatibility alias for :mod:`ops.realtime.config`."""
import importlib
import sys


_config = importlib.import_module("ops.realtime.config")
sys.modules[__name__] = _config
