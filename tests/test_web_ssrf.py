"""SSRF boundary of the shared web fetcher (tools/web/fetch.safe_get). Offline: IP literals need no DNS and
requests.get is faked, so no packet leaves the machine."""
import pytest

from tools.web import fetch


class FakeResponse:
    def __init__(self, status, location=None, body=b"<html><title>t</title>ok</html>"):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.content = body
        self.raw = None

    def close(self):
        pass


def test_public_page_redirecting_to_this_pc_is_refused(monkeypatch):
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        assert kw["allow_redirects"] is False  # every hop must come back here to be checked
        return FakeResponse(302, "http://127.0.0.1:8888/api/facts")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    with pytest.raises(fetch.BlockedURL, match="private or local"):
        fetch.safe_get("http://8.8.8.8/start")
    assert seen == ["http://8.8.8.8/start"]  # the local address was never requested


@pytest.mark.parametrize("url", ["http://127.0.0.1:8888/api/facts", "http://10.0.0.1/", "http://169.254.169.254/",
                                 "http://[::1]/", "ftp://8.8.8.8/x", "http://u:p@8.8.8.8/"])
def test_non_public_urls_are_refused_before_any_request(monkeypatch, url):
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: pytest.fail("request sent to " + url))
    with pytest.raises(fetch.BlockedURL):
        fetch.safe_get(url)


def test_public_redirect_chain_is_followed_and_capped(monkeypatch):
    hops = iter([FakeResponse(301, "/next"), FakeResponse(200)])
    monkeypatch.setattr(fetch.requests, "get", lambda url, **kw: next(hops))
    assert fetch.safe_get("http://8.8.8.8/").url == "http://8.8.8.8/next"
    monkeypatch.setattr(fetch.requests, "get", lambda url, **kw: FakeResponse(302, "/loop"))
    with pytest.raises(fetch.BlockedURL, match="too many"):
        fetch.safe_get("http://8.8.8.8/")


def test_oversized_body_is_refused(monkeypatch):
    monkeypatch.setattr(fetch.requests, "get", lambda url, **kw: FakeResponse(200, body=b"x" * 50))
    with pytest.raises(fetch.BlockedURL, match="larger"):
        fetch.safe_get("http://8.8.8.8/", max_bytes=10)
