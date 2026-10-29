"""Localhost-aware logging switch for the phone API."""
import os


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def normalize_host(value):
    """Strip ports and IPv6 brackets from a Host header or address."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:
        return text.rsplit(":", 1)[0]
    return text


def is_local_host(value):
    return normalize_host(value) in LOCAL_HOSTS


def dev_logging_enabled(host):
    """MORPHE_DEBUG overrides detection; otherwise only loopback is verbose."""
    override = os.environ.get("MORPHE_DEBUG", "").strip().lower()
    if override in TRUE_VALUES:
        return True
    if override in FALSE_VALUES:
        return False
    return is_local_host(host)
