"""Run source discovery, verification, and review state updates."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from repo_utils import parse_repo_url, repo_key
from discovery.providers import (
    FAST_PROVIDERS,
    ProviderUnavailable,
    github_search,
    ignored_repo_keys,
    run_fast_provider,
)
from discovery.state import (
    append_scanned,
    empty_state,
    load_provider_runs,
    load_scanned,
    load_state,
    save_provider_runs,
    save_review,
    save_state,
    utc_now,
    utc_today,
)
from discovery.verify import verify_repo

SEARCH_PROVIDER = "github-search"
SEARCH_INTERVAL_HOURS = 24
VERIFIED_INTERVAL_DAYS = 7
REVIEW_INTERVAL_DAYS = 7
UNAUTHENTICATED_LIMIT = 50
MAX_WORKERS = 8
# State is checkpointed this often so a kill (Termux OOM, SSH drop, CI timeout)
# loses at most one batch instead of the whole run.
VERIFY_BATCH_SIZE = 25


def _age_hours(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600


def _record_run(runs, name, count=0, error="", status="ok"):
    previous = runs.get(name, {})
    now = utc_now()
    entry = {
        **previous,
        "last_attempt": now,
        "status": status,
        "count": count,
    }
    if error:
        entry["error"] = error
    else:
        entry.pop("error", None)
        entry["last_success"] = now
    runs[name] = entry


def _merge_candidate(repos, candidate, now):
    platform = candidate.get("platform", "")
    repo = candidate.get("repo", "")
    key = repo_key(platform, repo)
    if not key:
        return None

    entry = repos.get(key)
    if entry is None:
        entry = {
            "platform": platform,
            "repo": repo,
            "url": candidate.get("url", ""),
            "status": "new",
            "first_seen": now,
            "last_seen": now,
            "sources": [],
            "channels": {},
            "evidence": {},
            "reason": "",
            "last_error": "",
        }
        repos[key] = entry
    entry["last_seen"] = now
    if candidate.get("url"):
        entry["url"] = candidate["url"]
    source = candidate.get("source")
    if source and source not in entry["sources"]:
        entry["sources"].append(source)

    for field in ("default_branch", "description", "topics", "pushed_at", "stars"):
        value = candidate.get(field)
        if value not in (None, "", []) and not entry.get(field):
            entry[field] = value
    return entry


def _search_due(runs, force):
    if force:
        return True
    age = _age_hours(runs.get(SEARCH_PROVIDER, {}).get("last_success"))
    return age is None or age >= SEARCH_INTERVAL_HOURS


def _needs_verify(entry, force):
    if force:
        return True
    status = entry.get("status")
    if status == "verified":
        age = _age_hours(entry.get("last_verified"))
        return age is None or age >= VERIFIED_INTERVAL_DAYS * 24
    if status == "review":
        age = _age_hours(entry.get("last_checked"))
        return age is None or age >= REVIEW_INTERVAL_DAYS * 24
    return True


def _priority(entry):
    status_order = {"new": 0, "review": 1, "verified": 2}
    return (
        status_order.get(entry.get("status"), 0),
        entry.get("last_verified") or entry.get("last_checked") or "",
        entry.get("repo", "").lower(),
    )


def _apply_verification(entry, result, now):
    entry["last_checked"] = now
    entry["last_error"] = ""
    status = result.get("status")
    if status == "verified":
        entry["status"] = "verified"
        entry["channels"] = result.get("channels", {})
        entry["evidence"] = result.get("evidence", {})
        entry["last_verified"] = now
        entry["reason"] = ""
    elif status == "transient":
        entry["last_error"] = result.get("reason", "transient error")
        if entry.get("status") != "verified":
            entry["status"] = "review"
            entry["reason"] = f"verification error: {entry['last_error']}"
    else:
        entry["status"] = "review"
        entry["reason"] = result.get("reason", "verification failed")


def _verify_entries(entries, limit, force, scanned=None, checkpoint=None):
    """Verify due entries, checkpointing progress and the scan cache as it goes.

    ``scanned`` is the append-only ``scanned_repos.txt`` cache. When a previous
    run was killed before ``discovered_repos.json`` was saved, its timestamp is
    used as ``last_checked`` so the repo is not scanned again immediately.
    """
    scanned = scanned or {}
    now = utc_now()

    for entry in entries:
        if entry.get("last_checked"):
            continue
        key = repo_key(entry.get("platform"), entry.get("repo"))
        stamp = scanned.get(key) if key else None
        if stamp:
            entry["last_checked"] = stamp

    candidates = [entry for entry in entries if _needs_verify(entry, force)]
    candidates.sort(key=_priority)
    if limit is not None and limit > 0:
        candidates = candidates[:limit]

    if not candidates:
        return {"checked": 0, "newly_verified": []}

    newly_verified = []
    pending_scan = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_entry = {
            executor.submit(verify_repo, entry): entry for entry in candidates
        }
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            previous_status = entry.get("status")
            try:
                result = future.result()
            except Exception as error:
                result = {"status": "transient", "reason": str(error)}
            _apply_verification(entry, result, now)
            if entry.get("status") == "verified" and previous_status != "verified":
                newly_verified.append(_found_summary(entry))
            key = repo_key(entry.get("platform"), entry.get("repo"))
            if key:
                pending_scan.append((key, now))
            if len(pending_scan) >= VERIFY_BATCH_SIZE:
                append_scanned(pending_scan)
                pending_scan = []
                if checkpoint:
                    checkpoint()

    if pending_scan:
        append_scanned(pending_scan)
    return {"checked": len(candidates), "newly_verified": newly_verified}


def _found_summary(entry):
    evidence = entry.get("evidence") or {}
    return {
        "repo": entry.get("repo", ""),
        "url": entry.get("url", ""),
        "name": evidence.get("name") or entry.get("repo", ""),
        "channels": sorted(entry.get("channels") or {}),
    }


def run(force=False, limit=None, search=True, dry_run=False, quiet=False):
    """Discover candidates, verify due repos, and persist state."""
    state = load_state()
    repos = state["repos"]
    runs = load_provider_runs()
    scanned = load_scanned()
    now = utc_now()
    was_baseline = bool(state.get("baseline_done"))
    if not os.environ.get("GITHUB_TOKEN"):
        if limit is None or limit > UNAUTHENTICATED_LIMIT:
            limit = UNAUTHENTICATED_LIMIT
    started = {
        "started_at": now,
        "providers": {},
        "candidates": 0,
        "verified": 0,
        "new": 0,
        "review": 0,
        "limit": limit,
        "errors": [],
        "found": [],
        "scanned": len(scanned),
    }

    def checkpoint():
        """Persist progress mid-run so a kill cannot lose the whole scan."""
        if dry_run:
            return
        save_state(state)
        save_provider_runs(runs)

    try:
        ignored = ignored_repo_keys()
    except Exception:
        ignored = set()

    def merge(candidates):
        added = 0
        for candidate in candidates:
            key = repo_key(candidate.get("platform"), candidate.get("repo"))
            if not key or key in ignored:
                continue
            _merge_candidate(repos, candidate, now)
            added += 1
        return added

    for label, function_name in FAST_PROVIDERS:
        try:
            candidates = run_fast_provider(function_name)
            count = merge(candidates)
            _record_run(runs, label, count=count)
            started["providers"][label] = {"count": count, "status": "ok"}
        except Exception as error:
            message = str(error) or error.__class__.__name__
            _record_run(runs, label, error=message, status="error")
            started["providers"][label] = {"count": 0, "status": "error"}
            started["errors"].append(f"{label}: {message}")
            if not quiet:
                print(f"[discovery] {label} failed: {message}")
        checkpoint()

    if search and _search_due(runs, force):
        try:
            candidates = github_search()
            count = merge(candidates)
            _record_run(runs, SEARCH_PROVIDER, count=count)
            started["providers"][SEARCH_PROVIDER] = {"count": count, "status": "ok"}
        except ProviderUnavailable as error:
            _record_run(runs, SEARCH_PROVIDER, error=str(error), status="skipped")
            started["providers"][SEARCH_PROVIDER] = {
                "count": 0,
                "status": "skipped",
            }
            started["errors"].append(f"{SEARCH_PROVIDER}: {error}")
            if not quiet:
                print(f"[discovery] {SEARCH_PROVIDER} skipped: {error}")
        except Exception as error:
            message = str(error) or error.__class__.__name__
            _record_run(runs, SEARCH_PROVIDER, error=message, status="error")
            started["providers"][SEARCH_PROVIDER] = {
                "count": 0,
                "status": "error",
            }
            started["errors"].append(f"{SEARCH_PROVIDER}: {message}")
            if not quiet:
                print(f"[discovery] {SEARCH_PROVIDER} failed: {message}")
        checkpoint()

    started["candidates"] = len(repos)
    verify_result = _verify_entries(
        list(repos.values()),
        limit=limit,
        force=force,
        scanned=scanned,
        checkpoint=checkpoint,
    )
    checked_count = verify_result["checked"]
    started["found"] = verify_result["newly_verified"]

    if not was_baseline:
        state["baseline_done"] = True
        state["baseline_pending"] = True
        state["baseline_date"] = utc_today()
        for entry in repos.values():
            if not entry.get("first_seen"):
                entry["first_seen"] = now

    statuses = {}
    for entry in repos.values():
        status = entry.get("status", "new")
        statuses[status] = statuses.get(status, 0) + 1
    started["verified"] = statuses.get("verified", 0)
    started["new"] = statuses.get("new", 0)
    started["review"] = statuses.get("review", 0)
    started["checked_this_run"] = checked_count
    started["baseline"] = not was_baseline

    if not dry_run:
        save_state(state)
        save_provider_runs(runs)
        save_review(state)

    started["finished_at"] = utc_now()
    if started["started_at"]:
        try:
            began = datetime.fromisoformat(started["started_at"].replace("Z", "+00:00"))
            ended = datetime.fromisoformat(started["finished_at"].replace("Z", "+00:00"))
            started["duration_seconds"] = int((ended - began).total_seconds())
        except ValueError:
            started["duration_seconds"] = 0

    if not quiet:
        print(
            "[discovery] "
            f"{started['candidates']} candidates, "
            f"{started['verified']} verified, "
            f"{started['review']} review, "
            f"{checked_count} checked this run, "
            f"{len(started['found'])} new"
        )
    return started


def load_verified_repos():
    """Load verified repository entries for the external fetch stage."""
    state = load_state()
    return [
        entry
        for entry in state.get("repos", {}).values()
        if entry.get("status") == "verified" and entry.get("channels")
    ]


def split_repo_target(entry):
    platform, repo = parse_repo_url(entry.get("url", ""))
    if not platform or not repo:
        return None
    owner, separator, name = repo.partition("/")
    if not separator or not name:
        return None
    return {
        "platform": platform,
        "owner": owner,
        "name": name,
        "repo": repo,
        "url": entry.get("url", ""),
        "channels": entry.get("channels", {}),
        "sources": entry.get("sources", []),
    }
