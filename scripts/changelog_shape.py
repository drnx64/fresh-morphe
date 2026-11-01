"""Changelog payload shape — history keeps patch names, not patch prose.

`data/changelog.json` is downloaded by every visitor who opens the Changelog
page, served by the phone API (`/v1/changelog`), and mirrored into SQLite.
Measured over 15 days / 7.7k app entries it was 12 MB, and ~4.6 MB of that
was `patch_diff` prose: `description` and `previous_description` copied onto
every added/removed/modified patch.

No historical consumer renders that prose:

- `pages/changelog.js` renders app badges + names, and does not even pass
  `patch_diff` to the app modal.
- `bundleHistoryModal` reads dates and badges only.
- `generate_site.py` RSS digests and `whats_new.py` Telegram digests read
  patch *names* (`_patch_summary` / `p["name"]`).
- `appDetailModal` renders prose only for today's entries, which come from
  `changes.json` — that payload keeps full detail.

So finalized days store `name` (+ `changes` tags for modified patches) and
drop the prose. Shape is unchanged otherwise, so every reader keeps working
and `slim_day()` is idempotent.
"""

PATCH_DIFF_KEYS = ("patches_added", "patches_modified", "patches_removed")

# Everything a historical reader can still use: the label it prints, and the
# list of fields that changed ("description updated", "compatible versions
# changed"). Descriptions themselves are dropped.
PATCH_ITEM_FIELDS = ("name", "changes")


def slim_patch_item(item):
    """Strip one patch-diff item down to its name and change tags."""
    if not isinstance(item, dict):
        # Legacy entries carried bare name strings; leave them alone.
        return item
    slim = {key: item[key] for key in PATCH_ITEM_FIELDS if key in item}
    # An item with neither field would lose its identity, so keep it whole.
    return slim or item


def slim_patch_diff(patch_diff):
    """Return a copy of `patch_diff` with every item slimmed.

    Empty lists are omitted rather than written as `[]`: 7.7k app entries made
    those ~15.5k empty arrays cost 0.36 MB of the payload for nothing, and every
    reader already treats a missing key as "no changes in this bucket".
    """
    if not isinstance(patch_diff, dict):
        return patch_diff
    slim = dict(patch_diff)
    for key in PATCH_DIFF_KEYS:
        items = patch_diff.get(key)
        if not isinstance(items, list):
            continue
        if items:
            slim[key] = [slim_patch_item(item) for item in items]
        else:
            slim.pop(key, None)
    return slim


def slim_app(app):
    """Return a copy of one affected app with a slimmed `patch_diff`.

    A diff that carries no changes at all is dropped: `hasChanges` in the app
    modal already checks the lists, so an empty object buys nothing.
    """
    if not isinstance(app, dict) or "patch_diff" not in app:
        return app
    slim = dict(app)
    patch_diff = slim_patch_diff(app["patch_diff"])
    if isinstance(patch_diff, dict) and not any(
        isinstance(patch_diff.get(key), list) and patch_diff[key]
        for key in PATCH_DIFF_KEYS
    ):
        slim.pop("patch_diff", None)
    else:
        slim["patch_diff"] = patch_diff
    return slim


def slim_day(day):
    """Return a copy of one finalized changelog day with slim app entries."""
    if not isinstance(day, dict):
        return day
    slim = dict(day)
    bundles = day.get("affected_bundles")
    if isinstance(bundles, list):
        slim_bundles = []
        for bundle in bundles:
            if not isinstance(bundle, dict):
                slim_bundles.append(bundle)
                continue
            slim_bundle = dict(bundle)
            apps = bundle.get("apps")
            if isinstance(apps, list):
                slim_bundle["apps"] = [slim_app(app) for app in apps]
            slim_bundles.append(slim_bundle)
        slim["affected_bundles"] = slim_bundles
    return slim


def slim_changelog(changelog):
    """Slim every day in a changelog list (safe to re-run)."""
    if not isinstance(changelog, list):
        return changelog
    return [slim_day(day) for day in changelog]
