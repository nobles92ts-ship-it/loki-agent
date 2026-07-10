"""i18n — en/ko key parity, fallback, and template sanity."""
import string

from loki.core import config


def test_key_parity():
    assert set(config.MSG["en"]) == set(config.MSG["ko"])


def test_unknown_lang_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(config, "LANG", "xx")
    assert config.t("nothing_running") == config.MSG["en"]["nothing_running"]


def test_format_kwargs():
    assert "3" in config.t("queued", n=3)


def test_every_template_formats():
    for lang in ("en", "ko"):
        for key, tpl in config.MSG[lang].items():
            fields = {f for _, f, _, _ in string.Formatter().parse(tpl) if f}
            tpl.format(**{f: "x" for f in fields})   # raises on stray braces
