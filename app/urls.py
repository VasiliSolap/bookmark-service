"""URL checks and normalisation.

Normalisation decides what counts as "the same bookmark" (see README).
"""

from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
DEFAULT_PORTS = {"http": 80, "https": 443}


def validate_url(value: str) -> str:
    """Return the trimmed URL or raise ValueError with a human-readable reason."""
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    if len(value) > MAX_URL_LENGTH:
        raise ValueError(f"must be at most {MAX_URL_LENGTH} characters")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("must not contain spaces or control characters")

    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError on a bad port such as :99999
    except ValueError:
        raise ValueError("is not a valid URL") from None

    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise ValueError("must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("must include a host, e.g. https://example.com")
    del port
    return value


def normalize_url(value: str) -> str:
    """Canonical form used to recognise repeats.

    - scheme and host are lower-cased (they are case-insensitive)
    - the default port is dropped (http://a.com:80 == http://a.com)
    - an empty path becomes "/"  (https://a.com == https://a.com/)
    - the #fragment is dropped (it never reaches the server)
    Path and query are kept as sent: they are case-sensitive on most sites.
    """
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host
    if parts.port is not None and parts.port != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{parts.port}"
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{userinfo}@{netloc}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))
