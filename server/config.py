"""Configuration for the phone-hosted MorpheTracker API and worker."""
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
DB_PATH = os.environ.get("MORPHE_DB_PATH", os.path.join(DATA_DIR, "morphe.db"))
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
EXPORT_DIR = os.environ.get("MORPHE_EXPORT_DIR", os.path.join(DATA_DIR, "exports"))

API_HOST = os.environ.get("MORPHE_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("MORPHE_API_PORT", "8080"))
ADMIN_TOKEN = os.environ.get("MORPHE_ADMIN_TOKEN", "")

# Keep response bodies below this before deciding to gzip. Cloudflare handles
# the larger edge cache; this only avoids compressing tiny JSON payloads.
GZIP_MIN_BYTES = int(os.environ.get("MORPHE_GZIP_MIN_BYTES", "1024"))

# Normal public cache lifetime. Detail/release endpoints can be cached longer
# by Cloudflare; the API still revalidates with ETags on every request.
PUBLIC_MAX_AGE = int(os.environ.get("MORPHE_PUBLIC_MAX_AGE", "60"))
