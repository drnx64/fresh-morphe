"""Configuration for the phone-hosted Fresh Morphe API and worker."""
import os
import re


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path=".env"):
    """Load KEY=VALUE pairs from the repo root without overriding the shell."""
    env_path = os.path.join(BASE_DIR, path)
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
            if not match:
                continue
            key, value = match.group(2), match.group(3).strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()

DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_DIR = os.path.join(DATA_DIR, "state")


def _env(name, legacy, default=""):
    """Read a Fresh env var, falling back to the pre-rebrand name."""
    return os.environ.get(name) or os.environ.get(legacy) or default


_DEFAULT_DB = os.path.join(DATA_DIR, "fresh.db")
_LEGACY_DB = os.path.join(DATA_DIR, "morphe.db")
DB_PATH = _env("FRESH_DB_PATH", "MORPHE_DB_PATH", _DEFAULT_DB)
if DB_PATH == _DEFAULT_DB and not os.path.exists(_DEFAULT_DB) and os.path.exists(_LEGACY_DB):
    DB_PATH = _LEGACY_DB

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
EXPORT_DIR = _env("FRESH_EXPORT_DIR", "MORPHE_EXPORT_DIR", os.path.join(DATA_DIR, "exports"))

API_HOST = _env("FRESH_API_HOST", "MORPHE_API_HOST", "127.0.0.1")
API_PORT = int(_env("FRESH_API_PORT", "MORPHE_API_PORT", "8080"))
ADMIN_TOKEN = _env("FRESH_ADMIN_TOKEN", "MORPHE_ADMIN_TOKEN", "")

# Keep response bodies below this before deciding to gzip. Cloudflare handles
# the larger edge cache; this only avoids compressing tiny JSON payloads.
GZIP_MIN_BYTES = int(_env("FRESH_GZIP_MIN_BYTES", "MORPHE_GZIP_MIN_BYTES", "1024"))

# Normal public cache lifetime. Detail/release endpoints can be cached longer
# by Cloudflare; the API still revalidates with ETags on every request.
PUBLIC_MAX_AGE = int(_env("FRESH_PUBLIC_MAX_AGE", "MORPHE_PUBLIC_MAX_AGE", "60"))
