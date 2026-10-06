from ishti import files_api, runs


def test_chat_title_from_first_message():
    assert runs.chat_title("  hello   world ") == "hello world"
    assert runs.chat_title("x" * 80) == "x" * 48 + "…"


def test_code_uploads_allowed_apk_not():
    for n in ("a.html", "a.css", "a.js", "a.py"):
        assert files_api.kind_of(n) == "code" and files_api.kind_of(n) in files_api.KINDS
    for suffix in (".apk", ".exe", ".jar", ".dll"):
        assert suffix not in files_api.ALLOWED_EXT
