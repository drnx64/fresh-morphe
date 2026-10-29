"""Persistent discovery state helpers."""

import os
from datetime import datetime, timezone

import state_manager


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def discovered_repos_path():
    return os.path.join(state_manager.STATE_DIR, "discovered_repos.json")


def provider_runs_path():
    return os.path.join(state_manager.STATE_DIR, "provider_runs.json")


def review_path():
    return os.path.join(state_manager.STATE_DIR, "discovery_review.json")


def scanned_repos_path():
    return os.path.join(state_manager.STATE_DIR, "scanned_repos.txt")


SCANNED_HEADER = "# Fresh Morphe discovery scan cache: <platform>:<owner/repo>\\t<utc-timestamp>"


def load_scanned():
    """Read the append-only scan cache as ``{key: timestamp}``.

    The cache survives a kill mid-run because it is appended to as each repo is
    verified, while ``discovered_repos.json`` is only rewritten at checkpoints.
    """
    path = scanned_repos_path()
    scanned = {}
    if not os.path.exists(path):
        return scanned
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, stamp = line.partition("\t")
                key = key.strip()
                if key and key not in scanned:
                    scanned[key] = stamp.strip()
    except OSError:
        return {}
    return scanned


def append_scanned(entries):
    """Append unseen ``(key, timestamp)`` pairs to the scan cache.

    Returns the keys that were newly written. Never raises: the cache is an
    optimisation, not a source of truth.
    """
    candidates = {}
    for key, stamp in entries:
        key = str(key or "").strip()
        if key and key not in candidates:
            candidates[key] = stamp or utc_now()
    if not candidates:
        return []

    known = load_scanned()
    fresh = {key: stamp for key, stamp in candidates.items() if key not in known}
    if not fresh:
        return []

    path = scanned_repos_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as handle:
            if new_file:
                handle.write(SCANNED_HEADER + "\n")
            for key, stamp in fresh.items():
                handle.write(f"{key}\t{stamp}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return []
    return list(fresh)


LEGACY_PROVIDER = "awesome"
PROVIDER = "fresh"


def _normalize_sources(value):
    if not isinstance(value, list):
        return value
    normalized = []
    for item in value:
        item = PROVIDER if item == LEGACY_PROVIDER else item
        if item not in normalized:
            normalized.append(item)
    return normalized


def empty_state():
    return {
        "generated_at": "",
        "baseline_done": False,
        "baseline_pending": False,
        "baseline_date": "",
        "repos": {},
    }


def load_state():
    data = state_manager.load_json(discovered_repos_path(), default={})
    if not isinstance(data, dict):
        return empty_state()
    state = empty_state()
    state.update(data)
    if not isinstance(state.get("repos"), dict):
        state["repos"] = {}
    for entry in state["repos"].values():
        if isinstance(entry, dict) and "sources" in entry:
            entry["sources"] = _normalize_sources(entry["sources"])
    return state


def save_state(state):
    state["generated_at"] = utc_now()
    return state_manager.save_json(discovered_repos_path(), state)


def load_provider_runs():
    data = state_manager.load_json(provider_runs_path(), default={})
    if not isinstance(data, dict):
        return {}
    if LEGACY_PROVIDER in data:
        legacy = data.pop(LEGACY_PROVIDER)
        if PROVIDER in data and isinstance(data[PROVIDER], dict) and isinstance(legacy, dict):
            for key, value in legacy.items():
                data[PROVIDER].setdefault(key, value)
        elif PROVIDER not in data:
            data[PROVIDER] = legacy
    return data


def save_provider_runs(data):
    return state_manager.save_json(provider_runs_path(), data)


def build_review(state):
    items = []
    for repo in state.get("repos", {}).values():
        if repo.get("status") != "review":
            continue
        items.append({
            "url": repo.get("url", ""),
            "platform": repo.get("platform", ""),
            "repo": repo.get("repo", ""),
            "sources": repo.get("sources", []),
            "reason": repo.get("reason", ""),
            "evidence": repo.get("evidence", {}),
            "first_seen": repo.get("first_seen", ""),
            "last_seen": repo.get("last_seen", ""),
            "last_error": repo.get("last_error", ""),
        })
    return sorted(items, key=lambda item: item["repo"].lower())


def save_review(state):
    return state_manager.save_json(review_path(), {
        "generated_at": utc_now(),
        "items": build_review(state),
    })


def baseline_pending():
    return bool(load_state().get("baseline_pending"))


def baseline_has_unchecked():
    state = load_state()
    return any(
        entry.get("status") == "new" for entry in state.get("repos", {}).values()
    )


def clear_baseline_pending():
    state = load_state()
    if not state.get("baseline_pending"):
        return False
    state["baseline_pending"] = False
    save_state(state)
    return True
