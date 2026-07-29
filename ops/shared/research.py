"""Compatibility alias for the research domain (use :mod:`ops.research`)."""

import sys
from ops import research as _implementation

sys.modules[__name__] = _implementation
