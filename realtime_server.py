#!/usr/bin/env python3
"""Compatibility entry point for :mod:`ops.realtime.server`."""
import importlib
import os
import sys


_server = importlib.import_module("ops.realtime.server")

if __name__ == "__main__":
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set - source ~/.env first")
    _server.run()
else:
    sys.modules[__name__] = _server
