"""Reusable validation and redaction helpers."""
import re
from urllib.parse import urlsplit, urlunsplit


_URL_IN_TEXT = re.compile(r"https?://[^\s<>'\"]+")


def public_url(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme or not parsed.hostname:
        return value.split("?", 1)[0]
    host = parsed.hostname
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def sanitize_text_urls(value):
    if not isinstance(value, str):
        return value
    return _URL_IN_TEXT.sub(
        lambda match: public_url(match.group(0).rstrip(".,);]")),
        value,
    )
