"""Attachments — inbound allowlist, inbox hygiene, outbound WORK_DIR fence."""
import os
import time

import pytest

from loki.core import files


# ── inbound classification ───────────────────────────────────────────────────
def test_classify_images_and_docs():
    assert files.classify_inbound("shot.png", "image/png") == "image"
    assert files.classify_inbound("SHOT.JPEG", "") == "image"
    assert files.classify_inbound("report.pdf", "application/pdf") == "doc"
    assert files.classify_inbound("data.csv", "text/csv") == "doc"
    assert files.classify_inbound("notes.md", "") == "doc"
    assert files.classify_inbound("main.py", "") == "doc"


def test_classify_rejects_binaries_and_archives():
    for name in ("setup.exe", "lib.dll", "run.bat", "bundle.zip",
                 "backup.tar.gz", "app.jar", "disk.iso"):
        assert files.classify_inbound(name, "application/octet-stream") is None


def test_classify_blocked_extension_beats_mimetype():
    # A lying mimetype must not smuggle an executable past the allowlist.
    assert files.classify_inbound("payload.exe", "text/plain") is None
    assert files.classify_inbound("payload.exe", "image/png") is None


def test_classify_unknown_extension_fails_closed():
    assert files.classify_inbound("thing.qqq", "") is None
    assert files.classify_inbound("archive.dat", "application/octet-stream") is None


def test_classify_extensionless_falls_back_to_mimetype():
    assert files.classify_inbound("clipboard", "image/png") == "image"
    assert files.classify_inbound("stdout", "text/plain") == "doc"
    assert files.classify_inbound("blob", "application/octet-stream") is None
    # extensionless build files still classify by name
    assert files.classify_inbound("Dockerfile", "") == "doc"


# ── inbox naming + hygiene ───────────────────────────────────────────────────
def test_safe_filename_strips_traversal_and_separators():
    name = files.safe_filename("../../etc/passwd", 3)
    assert "/" not in name and "\\" not in name and ".." not in name
    assert name.endswith("_3_passwd")


def test_safe_filename_handles_empty_and_dotfiles():
    assert files.safe_filename("", 0).endswith("_0_file0")
    assert not files.safe_filename(".bashrc", 1).split("_", 2)[2].startswith(".")


def test_prune_old_removes_stale_only(tmp_path):
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_text("x")
    new.write_text("y")
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))

    assert files.prune_old(tmp_path, days=7) == 1
    assert not old.exists() and new.exists()


def test_prune_old_survives_missing_dir(tmp_path):
    assert files.prune_old(tmp_path / "nope") == 0


# ── outbound resolution ──────────────────────────────────────────────────────
@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    (w / "reports").mkdir(parents=True)
    (w / "reports" / "q3.pdf").write_text("pdf")
    (w / "reports" / "q4.pdf").write_text("pdf")
    (w / "notes.md").write_text("hi")
    monkeypatch.setattr(files.config, "WORK_DIR", str(w))
    return w


def test_resolve_relative_and_absolute(work):
    paths, err, _ = files.resolve_outbound("notes.md")
    assert err == "" and [p.name for p in paths] == ["notes.md"]

    paths, err, _ = files.resolve_outbound(str(work / "reports" / "q3.pdf"))
    assert err == "" and [p.name for p in paths] == ["q3.pdf"]


def test_resolve_glob_sorted_and_capped(work):
    for i in range(6):
        (work / "reports" / f"extra{i}.pdf").write_text("x")
    paths, err, _ = files.resolve_outbound("reports/*.pdf", limit=4)
    assert err == "" and len(paths) == 4
    assert paths == sorted(paths)          # deterministic order


def test_resolve_rejects_outside_work_dir(work, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    paths, err, _ = files.resolve_outbound(str(secret))
    assert paths == [] and err == "send_outside"


def test_resolve_rejects_parent_traversal(work):
    paths, err, _ = files.resolve_outbound("../secret.txt")
    assert paths == [] and err == "send_outside"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges")
def test_resolve_rejects_symlink_escape(work, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (work / "link.md").symlink_to(outside)
    paths, err, _ = files.resolve_outbound("link.md")
    assert paths == [] and err == "send_outside"


def test_resolve_missing_and_empty(work):
    assert files.resolve_outbound("ghost.pdf")[1] == "send_not_found"
    assert files.resolve_outbound("reports/*.zip")[1] == "send_not_found"
    assert files.resolve_outbound("   ")[1] == "send_usage"


def test_resolve_size_cap(work, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 4)
    (work / "big.md").write_text("way too much")
    paths, err, detail = files.resolve_outbound("big.md")
    assert paths == [] and err == "send_too_big" and detail == "big.md"


def test_resolve_unwraps_slack_link_and_quotes(work):
    assert files.resolve_outbound("`notes.md`")[1] == ""
    assert files.resolve_outbound('"notes.md"')[1] == ""
    link = f"<file://{work / 'notes.md'}|notes.md>"
    assert files.resolve_outbound(link)[1] == ""


def test_resolve_directory_is_not_a_file(work):
    assert files.resolve_outbound("reports")[1] == "send_not_found"


# ── reply auto-upload detection ──────────────────────────────────────────────
def test_find_reply_files_only_allowlisted_under_work(work, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_text("x")
    (work / "script.py").write_text("x")     # real file, not an upload type
    text = (f"Wrote {work / 'reports' / 'q3.pdf'} and {work / 'script.py'} "
            f"plus {outside} — done.")
    found = files.find_reply_files(text)
    assert [p.name for p in found] == ["q3.pdf"]


def test_find_reply_files_dedupes_and_caps(work):
    p = work / "reports" / "q3.pdf"
    found = files.find_reply_files(f"{p} {p} {p}")
    assert len(found) == 1


def test_find_reply_files_ignores_nonexistent(work):
    assert files.find_reply_files(str(work / "ghost.pdf")) == []
