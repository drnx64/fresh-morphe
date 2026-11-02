"""Guards for the termux deployment scripts.

The phone side is not exercised by CI, so these are the only checks that its
shell scripts stay runnable: LF endings (CRLF makes `sh` fail on the first
`set -u`), a single shared env file, and the icon budget actually being wired
into the loop that runs the pipeline.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

TERMUX_DIR = os.path.join(os.path.dirname(__file__), "..", "deploy", "termux")

# Every value env.sh must define, with the default the phone should run with.
ICON_DEFAULTS = {
    "ICON_FETCH_LIMIT": "120",
    "ICON_RETRY_DAYS": "7",
    "ICON_NEGATIVE_DAYS": "90",
    "MAX_APP_PAGES_PER_RUN": "60",
}

LOOPS = ("run-pipeline.sh", "worker-loop.sh", "discovery-loop.sh")


def _read(name):
    with open(os.path.join(TERMUX_DIR, name), "rb") as fh:
        return fh.read()


def test_all_termux_scripts_use_lf_endings():
    for name in sorted(os.listdir(TERMUX_DIR)):
        if not name.endswith(".sh"):
            continue
        assert b"\r\n" not in _read(name), f"{name} has CRLF endings"


def test_env_sh_exists_and_defines_the_icon_budget():
    assert os.path.exists(os.path.join(TERMUX_DIR, "env.sh"))
    text = _read("env.sh").decode("utf-8")
    for name, default in ICON_DEFAULTS.items():
        match = re.search(rf"^{name}=\$\{{{name}:-([^}}]+)\}}", text, re.MULTILINE)
        assert match, f"{name} missing from env.sh"
        assert match.group(1) == default


def test_env_sh_exports_every_value_it_defines():
    text = _read("env.sh").decode("utf-8")
    export_line = next(
        (line for line in text.splitlines() if line.startswith("export ")), ""
    )
    for name in ICON_DEFAULTS:
        assert name in export_line, f"{name} is defined but not exported"


def test_loops_source_the_shared_env():
    for name in LOOPS:
        text = _read(name).decode("utf-8")
        assert "deploy/termux/env.sh" in text, f"{name} does not source env.sh"


def test_pipeline_loop_sources_env_before_running_python():
    text = _read("run-pipeline.sh").decode("utf-8")
    assert text.index("deploy/termux/env.sh") < text.index("scripts/run_pipeline.py")
