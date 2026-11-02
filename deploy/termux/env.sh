#!/data/data/com.termux/files/usr/bin/sh
# Shared phone-side environment, sourced by the termux loops.
#
# Keep every value overridable: a caller that already exported a variable wins,
# so a one-off `ICON_FETCH_LIMIT=400 sh run-pipeline.sh` still works.
#
# This file must use LF line endings. CRLF makes `sh` fail on the first line.

# --- icon fetching -----------------------------------------------------------
# The phone runs the pipeline hourly and has no CI time cap, so it can afford a
# far bigger icon budget than the 40/run default that CI uses. The first run
# burns down the backlog; after that most packages are cached or marked
# unlistable and the phase is nearly free.
ICON_FETCH_LIMIT=${ICON_FETCH_LIMIT:-120}
# How long a plain miss waits before the package is probed again.
ICON_RETRY_DAYS=${ICON_RETRY_DAYS:-7}
# How long a package that Play, F-Droid *and* APKMirror all missed stays
# unlistable. Without this the same delisted packages eat budget forever.
ICON_NEGATIVE_DAYS=${ICON_NEGATIVE_DAYS:-90}
# APKMirror needs several page fetches per package; cap them per run so one
# unlucky search cannot exhaust the hour.
MAX_APP_PAGES_PER_RUN=${MAX_APP_PAGES_PER_RUN:-60}

export ICON_FETCH_LIMIT ICON_RETRY_DAYS ICON_NEGATIVE_DAYS MAX_APP_PAGES_PER_RUN
