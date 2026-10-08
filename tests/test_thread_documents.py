"""Documents attached in a thread reach the brain as text.

Every Slack run on Codex is sealed now — guests, you in a channel, and your DM
with `LOKI_OWNER_MODE=restricted` — so the model cannot open a file. A crash
log posted in a thread, or a spreadsheet dropped in the DM, used to arrive as a
bare file name or a local path it had no way to read. Loki reads them instead:
text formats decoded, docx/xlsx/pptx unzipped with the standard library, all
of it inside the context guard as data. Formats with no reader here (pdf, the
legacy binary office files) are named and marked unreadable, not guessed at.
"""
import io
import zipfile

import pytest

from loki.core import files, sessions
from tests.conftest import event

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
PR = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, xml in members.items():
            z.writestr(name, xml)
    return buf.getvalue()


def _slide(*texts) -> str:
    runs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
    return f"<p:sld {A} xmlns:p='p'><p:txBody>{runs}</p:txBody></p:sld>"


XLSX = _zip({
    "xl/workbook.xml": f'<workbook {S} {R}><sheets>'
                       f'<sheet name="TC" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": f'<Relationships {PR}><Relationship Id="rId1" '
                                  f'Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/sharedStrings.xml": f'<sst {S}><si><t>항목</t></si><si><t>결과</t></si></sst>',
    "xl/worksheets/sheet1.xml": (
        f'<worksheet {S}><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2"><v>1</v></c>'
        '<c r="C2" t="inlineStr"><is><t>PASS</t></is></c></row>'
        '</sheetData></worksheet>'),
})


# ── reading one document ─────────────────────────────────────────────────────
def test_a_log_reads_as_text():
    assert files.document_text(b"ERROR boom\n", "crash.log") == "ERROR boom\n"


def test_korean_windows_text_is_decoded():
    assert files.document_text("한글 로그 FATAL".encode("cp949"), "a.txt") == "한글 로그 FATAL"


def test_utf16_with_a_bom_is_decoded():
    assert files.document_text("유니코드 로그".encode("utf-16"), "a.log") == "유니코드 로그"


def test_binary_behind_a_text_name_is_refused():
    assert files.document_text(b"\x00\x01\x02\x03PK", "x.txt") is None


def test_pdf_and_legacy_office_have_no_reader_here():
    for name in ("spec.pdf", "old.doc", "old.xls", "old.ppt"):
        assert files.document_text(b"%PDF-1.7 whatever", name) is None


def test_docx_reads_its_paragraphs():
    xml = (f"<w:document {W}><w:body>"
           "<w:p><w:r><w:t>첫 줄</w:t></w:r></w:p>"
           "<w:p><w:r><w:t>둘째</w:t></w:r><w:r><w:t> 줄</w:t></w:r></w:p>"
           "</w:body></w:document>")
    assert files.document_text(_zip({"word/document.xml": xml}), "spec.docx") \
        == "첫 줄\n둘째 줄"


def test_xlsx_cells_keep_their_columns():
    assert files.document_text(XLSX, "tc.xlsx") == "[sheet TC]\n항목\t\t결과\n1\t\tPASS"


def test_pptx_slides_come_in_slide_order():
    data = _zip({"ppt/slides/slide10.xml": _slide("열째"),
                 "ppt/slides/slide2.xml": _slide("둘째", "줄")})
    assert files.document_text(data, "deck.pptx") == "[slide 1]\n둘째\n줄\n\n[slide 2]\n열째"


def test_a_broken_office_file_is_unreadable_not_a_crash():
    assert files.document_text(b"PK\x03\x04 not really a zip", "a.xlsx") is None


def test_clip_keeps_the_start_and_the_end():
    text = "HEAD" + "." * 1000 + "FATAL at the end"
    clipped = files.clip_text(text, 200)
    assert clipped.startswith("HEAD") and clipped.endswith("FATAL at the end")
    assert "omitted" in clipped and len(clipped) < 300
    assert files.clip_text("short", 200) == "short"


# ── the thread ───────────────────────────────────────────────────────────────
def _replies(adapter, monkeypatch, msgs):
    monkeypatch.setattr(adapter.app.client, "conversations_replies",
                        lambda **kw: {"ok": True, "messages": msgs}, raising=False)


def _file(name, mimetype="text/plain"):
    return {"name": name, "mimetype": mimetype,
            "url_private_download": f"https://files.example/{name}"}


def test_a_document_in_the_thread_reaches_the_context(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text="이 로그 봐줘", channel="C1", ts="1.0", files=[_file("crash.log")]),
        event(text="<@UBOT> 원인 뭐야?", channel="C1", ts="2.0", thread_ts="1.0")])
    monkeypatch.setattr(adapter, "_fetch",
                        lambda url: b"boot ok\nFATAL: null ref at Foo.cs:42\n")
    ctx = adapter._thread_context("C1", "1.0")
    assert "📎 crash.log" in ctx and "FATAL: null ref at Foo.cs:42" in ctx


def test_a_file_only_message_still_says_who_posted_it(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text="", channel="C1", ts="1.0", files=[_file("tc.xlsx")]),
        event(text="<@UBOT> 요약해줘", channel="C1", ts="2.0", thread_ts="1.0")])
    monkeypatch.setattr(adapter, "_fetch", lambda url: XLSX)
    ctx = adapter._thread_context("C1", "1.0")
    assert "[tester] 📎 tc.xlsx" in ctx and "항목\t\t결과" in ctx


def test_archives_executables_and_images_are_never_fetched(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text="파일들", channel="C1", ts="1.0",
              files=[_file("build.zip", "application/zip"),
                     _file("setup.exe", "application/octet-stream"),
                     _file("shot.png", "image/png")]),
        event(text="<@UBOT> 봐줘", channel="C1", ts="2.0", thread_ts="1.0")])
    monkeypatch.setattr(adapter, "_fetch", lambda url: pytest.fail(f"fetched {url}"))
    ctx = adapter._thread_context("C1", "1.0")
    assert "📎 shot.png" in ctx                      # named, so the talk makes sense


def test_only_the_newest_documents_are_read(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text=f"log {i}", channel="C1", ts=f"{i}.0", files=[_file(f"run{i}.log")])
        for i in range(1, 7)])
    fetched = []
    monkeypatch.setattr(adapter, "_fetch", lambda url: fetched.append(url) or b"x")
    adapter._thread_context("C1", "1.0")
    assert [u.rsplit("/", 1)[1] for u in fetched] == ["run6.log", "run5.log",
                                                     "run4.log", "run3.log"]


def test_an_unreadable_format_is_named_as_such(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text="기획서", channel="C1", ts="1.0", files=[_file("spec.pdf", "application/pdf")]),
        event(text="<@UBOT> 요약", channel="C1", ts="2.0", thread_ts="1.0")])
    monkeypatch.setattr(adapter, "_fetch", lambda url: b"%PDF-1.7 \x00\x01")
    ctx = adapter._thread_context("C1", "1.0")
    assert "📎 spec.pdf" in ctx and "(pdf: no text could be read)" in ctx


def test_a_long_log_keeps_its_end(adapter, monkeypatch):
    _replies(adapter, monkeypatch, [
        event(text="", channel="C1", ts="1.0", files=[_file("big.log")]),
        event(text="<@UBOT> 에러?", channel="C1", ts="2.0", thread_ts="1.0")])
    monkeypatch.setattr(adapter, "_fetch",
                        lambda url: (b"noise line\n" * 20000) + b"FATAL: out of memory\n")
    ctx = adapter._thread_context("C1", "1.0")
    assert "FATAL: out of memory" in ctx and len(ctx) < 20000


# ── the download: the bot token never leaves Slack ───────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_bot_token_only_goes_to_slack(adapter, monkeypatch):
    """A file in a thread can be anyone's; the token must not follow its URL."""
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("token sent off Slack"))
    for url in ("https://evil.example/f.log", "http://files.slack.com/f.log",
                "https://files.slack.com.evil.example/f.log", ""):
        assert adapter._fetch(url) is None, url


def test_a_slack_file_is_fetched_with_the_token(adapter, monkeypatch):
    seen = {}

    def fake_open(req, timeout=None):
        seen["auth"] = req.headers.get("Authorization")
        return _Resp(b"hello")

    monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_open)
    assert adapter._fetch("https://files.slack.com/files-pri/T1-F1/a.log") == b"hello"
    assert seen["auth"] == "Bearer xoxb-test"


# ── your DM: a document that opens the conversation ──────────────────────────
def _dm_job(**extra):
    job = {"text": "요약해줘", "user": "UOWNER", "id": "j1", "channel": "D0OWNER",
           "ts": "1.1", "kind": "owner", "event_id": "e1",
           "permission_mode": "bypassPermissions",
           "session_key": sessions.key_for("D0OWNER", None, True),
           "attachments": [{"url": "u", "name": "notes.txt", "kind": "doc"}]}
    job.update(extra)
    return job


def _capture_prompt(adapter, monkeypatch):
    seen = {}

    def fake_run(p, *a, **kw):
        seen["prompt"] = p
        return {"text": "ok", "session_id": "s", "error": False, "reason": "ok"}

    monkeypatch.setattr(adapter.brain, "run_claude", fake_run)
    return seen


def test_a_document_dropped_in_your_dm_is_read(adapter, monkeypatch, tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("회의록: 10/22 확정", encoding="utf-8")
    monkeypatch.setattr(adapter, "_download_attachments", lambda items: ([], [str(doc)]))
    seen = _capture_prompt(adapter, monkeypatch)
    adapter._handle(_dm_job())
    assert "회의록: 10/22 확정" in seen["prompt"]


def test_in_a_thread_the_thread_reads_it_once(adapter, monkeypatch, tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("회의록: 10/22 확정", encoding="utf-8")
    monkeypatch.setattr(adapter, "_download_attachments", lambda items: ([], [str(doc)]))
    monkeypatch.setattr(adapter, "_thread_context",
                        lambda c, ts: "[tester] 📎 notes.txt\n\n--- 📎 notes.txt ---\n회의록: 10/22 확정")
    seen = _capture_prompt(adapter, monkeypatch)
    adapter._handle(_dm_job(in_thread=True, thread="1.0"))
    assert seen["prompt"].count("회의록: 10/22 확정") == 1
