"""Adapter wiring for attachments — inbound classification and `!send`."""
from conftest import event


def _file(name, mimetype="", url="https://files.slack.test/x"):
    return {"name": name, "mimetype": mimetype, "url_private_download": url}


# ── inbound ──────────────────────────────────────────────────────────────────
def test_image_attachment_is_queued(adapter):
    adapter._dispatch({"event_id": "e1"},
                      event(text="", files=[_file("shot.png", "image/png")]),
                      is_mention=False)
    job = adapter.submitted[0]
    assert job["attachments"] == [
        {"url": "https://files.slack.test/x", "name": "shot.png", "kind": "image"}]
    assert job["text"]                       # captionless → default ask


def test_document_attachment_is_queued(adapter):
    adapter._dispatch({"event_id": "e2"},
                      event(text="summarize this",
                            files=[_file("q3.pdf", "application/pdf")]),
                      is_mention=False)
    job = adapter.submitted[0]
    assert job["attachments"][0]["kind"] == "doc"
    assert job["text"] == "summarize this"


def test_mixed_attachments_keep_their_kinds(adapter):
    adapter._dispatch({"event_id": "e3"},
                      event(text="look", files=[_file("a.png", "image/png"),
                                                _file("b.csv", "text/csv")]),
                      is_mention=False)
    kinds = [a["kind"] for a in adapter.submitted[0]["attachments"]]
    assert kinds == ["image", "doc"]


def test_blocked_attachment_is_rejected_with_notice(adapter):
    adapter._dispatch({"event_id": "e4"},
                      event(text="", files=[_file("payload.zip")]),
                      is_mention=False)
    assert adapter.submitted == []           # nothing to do → no job
    assert "payload.zip" in adapter.app.client.texts()[0]


def test_blocked_attachment_still_answers_the_text(adapter):
    adapter._dispatch({"event_id": "e5"},
                      event(text="what's up", files=[_file("setup.exe")]),
                      is_mention=False)
    assert adapter.submitted[0]["text"] == "what's up"
    assert adapter.submitted[0]["attachments"] == []
    assert "setup.exe" in adapter.app.client.texts()[0]


def test_guest_attachments_are_ignored(adapter):
    adapter._dispatch({"event_id": "e6"},
                      event(text="read this", user="UGUEST", channel="C0PUB",
                            files=[_file("q3.pdf", "application/pdf")]),
                      is_mention=True)
    assert adapter.submitted[0]["attachments"] == []


def test_inbound_attachment_count_is_capped(adapter):
    many = [_file(f"f{i}.md") for i in range(20)]
    adapter._dispatch({"event_id": "e7"}, event(text="x", files=many),
                      is_mention=False)
    assert len(adapter.submitted[0]["attachments"]) == 8


# ── outbound (!send) ─────────────────────────────────────────────────────────
def test_send_uploads_file_into_the_thread(adapter):
    (adapter.work / "notes.md").write_text("hello")
    adapter._dispatch({"event_id": "s1"}, event(text="!send notes.md"),
                      is_mention=False)

    uploads = adapter.app.client.of("files_upload_v2")
    assert len(uploads) == 1
    assert uploads[0]["file"].endswith("notes.md")
    assert uploads[0]["thread_ts"] == "1.1"
    assert adapter.submitted == []           # a command, not a Claude job


def test_send_glob_uploads_several(adapter):
    (adapter.work / "r").mkdir()
    for n in ("a.pdf", "b.pdf"):
        (adapter.work / "r" / n).write_text("x")
    adapter._dispatch({"event_id": "s2"}, event(text="!send r/*.pdf"),
                      is_mention=False)
    assert len(adapter.app.client.of("files_upload_v2")) == 2


def test_send_refuses_outside_work_dir(adapter, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("nope")
    adapter._dispatch({"event_id": "s3"}, event(text=f"!send {secret}"),
                      is_mention=False)
    assert adapter.app.client.of("files_upload_v2") == []
    assert "WORK_DIR" in adapter.app.client.texts()[0]


def test_send_reports_missing_file(adapter):
    adapter._dispatch({"event_id": "s4"}, event(text="!send ghost.pdf"),
                      is_mention=False)
    assert adapter.app.client.of("files_upload_v2") == []
    assert "ghost.pdf" in adapter.app.client.texts()[0]


def test_bare_send_shows_usage(adapter):
    adapter._dispatch({"event_id": "s5"}, event(text="!send"), is_mention=False)
    assert "!send" in adapter.app.client.texts()[0]


def test_send_reports_upload_failure(adapter):
    (adapter.work / "notes.md").write_text("hello")
    adapter.app.client.fail = {"files_upload_v2"}
    adapter._dispatch({"event_id": "s6"}, event(text="!send notes.md"),
                      is_mention=False)
    assert any("1" in tx for tx in adapter.app.client.texts())


def test_guest_cannot_send(adapter):
    (adapter.work / "notes.md").write_text("hello")
    adapter._dispatch({"event_id": "s7"},
                      event(text="!send notes.md", user="UGUEST",
                            channel="C0PUB"),
                      is_mention=True)
    assert adapter.app.client.of("files_upload_v2") == []
    # falls through to the brain as ordinary (read-only) text
    assert adapter.submitted[0]["permission_mode"] == "plan"


def test_korean_send_alias(adapter):
    (adapter.work / "notes.md").write_text("hello")
    adapter._dispatch({"event_id": "s8"}, event(text="!전송 notes.md"),
                      is_mention=False)
    assert len(adapter.app.client.of("files_upload_v2")) == 1


def test_send_is_owner_only_even_for_org_members(adapter, monkeypatch):
    (adapter.work / "notes.md").write_text("hello")
    monkeypatch.setattr(adapter.orgs, "resolve", lambda u, c: "acme")
    monkeypatch.setattr(adapter.orgs, "allows_command", lambda o, c: True)
    adapter._dispatch({"event_id": "s9"},
                      event(text="!send notes.md", user="UGUEST",
                            channel="C0PUB"),
                      is_mention=True)
    assert adapter.app.client.of("files_upload_v2") == []
