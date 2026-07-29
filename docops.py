"""Compatibility alias for :mod:`ops.docs.service`."""

import importlib
import sys


_service = importlib.import_module("ops.docs.service")
_uploads = importlib.import_module("ops.docs.uploads")
_service.DOCOPS_SCHEMAS.extend(_uploads.UPLOAD_SCHEMAS)
_service.DOCOPS_DISPATCH.update(_uploads.UPLOAD_DISPATCH)
_service.list_uploaded_documents = _uploads.list_uploaded_documents
_service.get_uploaded_document = _uploads.get_uploaded_document
sys.modules[__name__] = _service
