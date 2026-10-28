-- MorpheTracker phone database. SQLite 3.35+ (FTS5 enabled).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
  id               INTEGER PRIMARY KEY,
  url              TEXT NOT NULL,
  url_key          TEXT NOT NULL UNIQUE,
  slug             TEXT NOT NULL DEFAULT '',
  platform         TEXT NOT NULL DEFAULT '',
  owner            TEXT NOT NULL DEFAULT '',
  repo             TEXT NOT NULL DEFAULT '',
  name             TEXT NOT NULL DEFAULT '',
  description      TEXT NOT NULL DEFAULT '',
  avatar_url       TEXT NOT NULL DEFAULT '',
  bundle_image_url TEXT NOT NULL DEFAULT '',
  stars            INTEGER NOT NULL DEFAULT 0,
  is_archived      INTEGER NOT NULL DEFAULT 0,
  is_fork          INTEGER NOT NULL DEFAULT 0,
  fork_parent      TEXT NOT NULL DEFAULT '',
  fetched_at       REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS repos_slug_idx ON repos(slug);

CREATE TABLE IF NOT EXISTS bundles (
  id             INTEGER PRIMARY KEY,
  repo_id        INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  bundle         TEXT NOT NULL,
  channel        TEXT NOT NULL CHECK (channel IN ('stable', 'latest', 'dev')),
  version        TEXT NOT NULL DEFAULT '',
  index_version  TEXT NOT NULL DEFAULT '',
  release_tag    TEXT NOT NULL DEFAULT '',
  release_date   TEXT NOT NULL DEFAULT '',
  patches_name   TEXT NOT NULL DEFAULT '',
  description    TEXT NOT NULL DEFAULT '',
  release_notes  TEXT NOT NULL DEFAULT '',
  fingerprint    TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL DEFAULT '',
  is_prerelease  INTEGER NOT NULL DEFAULT 0,
  latest_origin  TEXT NOT NULL DEFAULT '',
  app_count      INTEGER NOT NULL DEFAULT 0,
  imported_at    TEXT NOT NULL DEFAULT '',
  UNIQUE (bundle, channel)
);
CREATE INDEX IF NOT EXISTS bundles_repo_idx ON bundles(repo_id);
CREATE INDEX IF NOT EXISTS bundles_channel_idx ON bundles(channel);
CREATE INDEX IF NOT EXISTS bundles_release_idx ON bundles(release_date);

CREATE TABLE IF NOT EXISTS apps (
  id       INTEGER PRIMARY KEY,
  package  TEXT NOT NULL UNIQUE,
  name     TEXT NOT NULL DEFAULT '',
  icon_url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bundle_apps (
  bundle_id INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
  app_id    INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  name      TEXT NOT NULL DEFAULT '',
  position  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bundle_id, app_id)
);
CREATE INDEX IF NOT EXISTS bundle_apps_app_idx ON bundle_apps(app_id);

CREATE TABLE IF NOT EXISTS patches (
  id                       INTEGER PRIMARY KEY,
  bundle_id                INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
  app_id                   INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  name                     TEXT NOT NULL,
  description              TEXT NOT NULL DEFAULT '',
  use_default              INTEGER NOT NULL DEFAULT 0,
  options_json             TEXT NOT NULL DEFAULT '[]',
  compatible_versions_json TEXT NOT NULL DEFAULT '[]',
  fingerprint              TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS patches_bundle_idx ON patches(bundle_id);
CREATE INDEX IF NOT EXISTS patches_app_idx ON patches(app_id);
CREATE INDEX IF NOT EXISTS patches_name_idx ON patches(name);

CREATE TABLE IF NOT EXISTS patch_targets (
  patch_id    INTEGER NOT NULL REFERENCES patches(id) ON DELETE CASCADE,
  version     TEXT NOT NULL DEFAULT '',
  version_code TEXT NOT NULL DEFAULT '',
  min_sdk     INTEGER,
  experimental INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (patch_id, version, version_code)
);

CREATE TABLE IF NOT EXISTS releases (
  id           INTEGER PRIMARY KEY,
  repo_id      INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  tag          TEXT NOT NULL,
  name         TEXT NOT NULL DEFAULT '',
  body         TEXT NOT NULL DEFAULT '',
  prerelease   INTEGER NOT NULL DEFAULT 0,
  published_at TEXT NOT NULL DEFAULT '',
  fetched_at   REAL NOT NULL DEFAULT 0,
  UNIQUE (repo_id, tag)
);
CREATE INDEX IF NOT EXISTS releases_repo_idx ON releases(repo_id);
CREATE INDEX IF NOT EXISTS releases_date_idx ON releases(published_at);

CREATE TABLE IF NOT EXISTS changes (
  date         TEXT PRIMARY KEY,
  last_checked TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  name TEXT PRIMARY KEY,
  json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,
  stats_json  TEXT NOT NULL DEFAULT '{}',
  error       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS repo_sources (
  url_key    TEXT NOT NULL,
  branch     TEXT NOT NULL,
  hash       TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (url_key, branch)
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS apps_fts USING fts5(
  package,
  name,
  content='apps',
  content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS apps_ai AFTER INSERT ON apps BEGIN
  INSERT INTO apps_fts(rowid, package, name) VALUES (new.id, new.package, new.name);
END;
CREATE TRIGGER IF NOT EXISTS apps_ad AFTER DELETE ON apps BEGIN
  INSERT INTO apps_fts(apps_fts, rowid, package, name)
  VALUES ('delete', old.id, old.package, old.name);
END;
CREATE TRIGGER IF NOT EXISTS apps_au AFTER UPDATE ON apps BEGIN
  INSERT INTO apps_fts(apps_fts, rowid, package, name)
  VALUES ('delete', old.id, old.package, old.name);
  INSERT INTO apps_fts(rowid, package, name) VALUES (new.id, new.package, new.name);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS patches_fts USING fts5(
  name,
  description,
  content='patches',
  content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS patches_ai AFTER INSERT ON patches BEGIN
  INSERT INTO patches_fts(rowid, name, description)
  VALUES (new.id, new.name, new.description);
END;
CREATE TRIGGER IF NOT EXISTS patches_ad AFTER DELETE ON patches BEGIN
  INSERT INTO patches_fts(patches_fts, rowid, name, description)
  VALUES ('delete', old.id, old.name, old.description);
END;
CREATE TRIGGER IF NOT EXISTS patches_au AFTER UPDATE ON patches BEGIN
  INSERT INTO patches_fts(patches_fts, rowid, name, description)
  VALUES ('delete', old.id, old.name, old.description);
  INSERT INTO patches_fts(rowid, name, description)
  VALUES (new.id, new.name, new.description);
END;
