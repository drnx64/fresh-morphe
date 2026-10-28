"""Tests for repoInfo metadata storage helpers."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import repoInfo


def test_update_bundle_files_updates_index_and_split_file(monkeypatch, tmp_path):
    monkeypatch.setattr(repoInfo, "BUNDLES_DIR", str(tmp_path))
    (tmp_path / "_index.json").write_text(json.dumps({
        "Acme_patches:stable": {
            "bundle": "Acme_patches",
            "channel": "stable",
            "repo_url": "https://github.com/acme/patches",
        }
    }), encoding="utf-8")
    (tmp_path / "Acme_patches_stable.json").write_text(json.dumps({
        "bundle": "Acme_patches",
        "channel": "stable",
        "repo_url": "https://github.com/acme/patches",
    }), encoding="utf-8")

    repoInfo.update_bundle_files({
        "https://github.com/acme/patches": {
            "stars": 12,
            "avatarUrl": "https://avatars.githubusercontent.com/u/1?s=128",
            "repoDescription": "Patches",
            "isArchived": False,
            "isFork": True,
            "forkParent": "upstream/patches",
            "bundleImageUrl": "https://github.com/acme/patches/raw/HEAD/patches-bundle.png",
        }
    })

    index = json.loads((tmp_path / "_index.json").read_text(encoding="utf-8"))
    bundle = json.loads(
        (tmp_path / "Acme_patches_stable.json").read_text(encoding="utf-8")
    )
    assert index["Acme_patches:stable"]["stars"] == 12
    assert index["Acme_patches:stable"]["forkParent"] == "upstream/patches"
    assert bundle["avatarUrl"].endswith("s=128")
    assert bundle["isFork"] is True


def test_process_dedupes_repo_urls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repoInfo,
        "_fetch_repo_details",
        lambda url: calls.append(url) or {"stars": 1},
    )
    result = repoInfo.process({
        "a:stable": {"repo_url": "https://github.com/acme/patches"},
        "a:dev": {"repo_url": "https://github.com/acme/patches"},
        "b:stable": {"repo_url": "https://github.com/other/patches"},
    })
    assert len(calls) == 2
    assert set(result) == {
        "https://github.com/acme/patches",
        "https://github.com/other/patches",
    }
