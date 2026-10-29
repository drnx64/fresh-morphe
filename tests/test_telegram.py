"""Tests for telegram.py send/edit state machine."""
import io
import json
import os
import re
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import telegram as tg
from datetime import datetime, timezone

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TOKEN = "test-token"
CHAT = "-100123"


def http_error(body, code=400):
    fp = io.BytesIO(body.encode("utf-8"))
    return urllib.error.HTTPError("https://api.telegram.org", code, "Bad Request", {}, fp)


class FakeTelegram:
    """Stands in for the Telegram Bot API."""

    def __init__(self):
        self.messages = {}
        self.calls = []
        self.next_id = 100
        self.edit_error = None       # HTTP error body raised on every edit
        self.edit_unreachable = False

    def __call__(self, method, payload, token=None):
        self.calls.append((method, dict(payload)))
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = payload["text"]
            return {"ok": True, "result": {"message_id": self.next_id}}

        if method == "editMessageText":
            if self.edit_error:
                raise http_error(self.edit_error)
            if self.edit_unreachable:
                raise urllib.error.URLError("connection refused")
            mid = payload["message_id"]
            if mid not in self.messages:
                raise http_error("Bad Request: message to edit not found")
            if self.messages[mid] == payload["text"]:
                raise http_error("Bad Request: message is not modified")
            self.messages[mid] = payload["text"]
            return {"ok": True, "result": {"message_id": mid}}

        if method == "deleteMessage":
            self.messages.pop(payload["message_id"], None)
            return {"ok": True, "result": True}

        raise AssertionError(f"unexpected method: {method}")

    def of(self, name):
        return [payload for method, payload in self.calls if method == name]

    def reset(self):
        self.calls.clear()


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake = FakeTelegram()
    monkeypatch.setattr(tg, "_tg_api", fake)
    path = str(tmp_path / "last_tg_msg.json")
    monkeypatch.setattr(tg, "LAST_MSG_PATH", path)
    return fake, path


def write_state(path, date, messages):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"date": date, "messages": messages}, handle)


def read_state(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class TestStamps:
    def test_stamp_format(self):
        assert re.fullmatch(r"\d{1,2}:\d{2} (AM|PM) UTC", tg._utc_stamp())

    def test_truncate_stays_under_limit(self):
        assert len(tg._truncate("x" * 9000)) <= 4096

    def test_truncate_passes_short_text_through(self):
        assert tg._truncate("short") == "short"


class TestFirstDelivery:
    def test_sends_one_message_per_chunk_without_stamp(self, env):
        fake, path = env
        assert tg.send_or_edit(["alpha", "beta"], token=TOKEN, chat_id=CHAT) is True
        sent = fake.of("sendMessage")
        assert len(sent) == 2
        assert all("Updated" not in p["text"] for p in sent)

    def test_state_records_every_chunk(self, env):
        fake, path = env
        tg.send_or_edit(["alpha", "beta"], token=TOKEN, chat_id=CHAT)
        state = read_state(path)
        assert state["date"] == TODAY
        assert [m["chunk"] for m in state["messages"]] == [0, 1]

    def test_state_never_persists_chat_id(self, env):
        # the file is git-tracked and copied into the public site build
        fake, path = env
        tg.send_or_edit(["alpha"], token=TOKEN, chat_id=CHAT)
        assert "chat_id" not in read_state(path)

    def test_missing_credentials_is_skipped(self, env, monkeypatch):
        fake, path = env
        monkeypatch.delenv("TG_TOKEN", raising=False)
        monkeypatch.delenv("TG_CHAT", raising=False)
        assert tg.send_or_edit(["alpha"]) is False
        assert fake.of("sendMessage") == []


class TestEditInPlace:
    def test_rerun_edits_instead_of_sending(self, env):
        fake, path = env
        tg.send_or_edit(["alpha", "beta"], token=TOKEN, chat_id=CHAT)
        fake.reset()
        tg.send_or_edit(["alpha v2", "beta v2"], token=TOKEN, chat_id=CHAT)
        assert fake.of("sendMessage") == []
        assert len(fake.of("editMessageText")) == 2

    def test_stamp_only_on_last_message_when_editing(self, env):
        fake, path = env
        tg.send_or_edit(["alpha", "beta"], token=TOKEN, chat_id=CHAT)
        fake.reset()
        tg.send_or_edit(["alpha v2", "beta v2"], token=TOKEN, chat_id=CHAT)
        texts = {p["message_id"]: p["text"] for p in fake.of("editMessageText")}
        assert len(texts) == 2
        first, last = texts[min(texts)], texts[max(texts)]
        assert "Updated" not in first
        assert re.search(r"Updated \d{1,2}:\d{2} (AM|PM) UTC", last)

    def test_identical_content_skips_the_api(self, env):
        """Unchanged digests must not re-edit: CI and the phone share one message."""
        fake, path = env
        tg.send_or_edit(["same"], token=TOKEN, chat_id=CHAT)
        fake.reset()
        assert tg.send_or_edit(["same"], token=TOKEN, chat_id=CHAT) is True
        assert fake.of("sendMessage") == []
        assert fake.of("editMessageText") == []
        assert read_state(path)["messages"][0]["message_id"] == 101

    def test_growth_sends_new_chunk_and_keeps_older_ids(self, env):
        fake, path = env
        fake.messages[500] = "old"
        write_state(path, TODAY, [{"message_id": 500, "chunk": 0}])
        tg.send_or_edit(["a", "b"], token=TOKEN, chat_id=CHAT)
        assert len(fake.of("editMessageText")) == 1
        assert len(fake.of("sendMessage")) == 1
        state = read_state(path)
        assert state["messages"][0]["message_id"] == 500
        assert state["messages"][1]["chunk"] == 1
        assert fake.messages[500].startswith("a")

    def test_shrink_deletes_surplus_message(self, env):
        fake, path = env
        fake.messages[500] = "old"
        fake.messages[501] = "old"
        write_state(path, TODAY, [{"message_id": 500, "chunk": 0},
                                  {"message_id": 501, "chunk": 1}])
        tg.send_or_edit(["only"], token=TOKEN, chat_id=CHAT)
        assert [p["message_id"] for p in fake.of("deleteMessage")] == [501]
        assert [m["message_id"] for m in read_state(path)["messages"]] == [500]


class TestFailureHandling:
    def test_stale_message_resends_without_deleting(self, env):
        fake, path = env
        write_state(path, TODAY, [{"message_id": 777, "chunk": 0}])
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert len(fake.of("sendMessage")) == 1
        assert fake.of("deleteMessage") == []
        state = read_state(path)
        assert state["messages"][0]["message_id"] == 101

    def test_transient_edit_failure_keeps_id_for_retry(self, env):
        fake, path = env
        fake.messages[500] = "old"
        fake.edit_unreachable = True
        write_state(path, TODAY, [{"message_id": 500, "chunk": 0}])
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert fake.of("sendMessage") == []
        assert fake.of("deleteMessage") == []
        assert read_state(path)["messages"] == [{"message_id": 500, "chunk": 0}]

        fake.edit_unreachable = False
        fake.reset()
        tg.send_or_edit(["hi again"], token=TOKEN, chat_id=CHAT)
        assert len(fake.of("editMessageText")) == 1
        assert fake.of("sendMessage") == []
        assert fake.messages[500].startswith("hi again")

    def test_rate_limit_is_treated_as_transient(self, env):
        fake, path = env
        fake.messages[500] = "old"
        fake.edit_error = "Too Many Requests: 429"
        write_state(path, TODAY, [{"message_id": 500, "chunk": 0}])
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert fake.of("sendMessage") == []
        assert read_state(path)["messages"] == [{"message_id": 500, "chunk": 0}]


class TestDayRollover:
    def test_new_day_sends_fresh_and_keeps_history(self, env):
        fake, path = env
        fake.messages[500] = "yesterday"
        write_state(path, "2020-01-01", [{"message_id": 500, "chunk": 0}])
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert len(fake.of("sendMessage")) == 1
        assert fake.of("deleteMessage") == []
        assert fake.messages[500] == "yesterday"
        assert read_state(path)["date"] == TODAY

    def test_missing_state_file_sends_fresh(self, env):
        fake, path = env
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert len(fake.of("sendMessage")) == 1
        assert read_state(path)["date"] == TODAY

    def test_legacy_single_message_state_is_upgraded(self, env):
        fake, path = env
        fake.messages[500] = "old"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"date": TODAY, "chat_id": CHAT, "message_id": 500}, handle)
        tg.send_or_edit(["hi"], token=TOKEN, chat_id=CHAT)
        assert fake.of("sendMessage") == []
        assert len(fake.of("editMessageText")) == 1
        assert read_state(path)["messages"] == [{"message_id": 500, "chunk": 0}]
