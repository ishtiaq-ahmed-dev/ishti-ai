"""Shared dashboard HTTP/WebSocket policy, including loopback and tunnel callers."""
import secrets

from ishti.api_sessions import host_authority, origin_ok

LOOPBACK = frozenset(("127.0.0.1", "::1", "localhost"))
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip",
                 "x-forwarded-host", "x-forwarded-proto")


def request_origin_ok(conn):
    scheme = {"ws": "http", "wss": "https"}.get(conn.scope.get("scheme"), conn.scope.get("scheme", "http"))
    forwarded = conn.headers.get("x-forwarded-proto")
    if forwarded:
        # A proxy header also removes implicit loopback trust in authorized().
        if forwarded not in ("http", "https"):
            return False
        scheme = forwarded
    return origin_ok(conn.headers.get("origin"), conn.headers.get("host"),
                     conn.headers.get("sec-fetch-site"), expected_scheme=scheme)


def token_matches(supplied, token):
    return bool(isinstance(supplied, str) and isinstance(token, str) and supplied and token
                and secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")))


def authorized(conn, token):
    if not request_origin_ok(conn):
        return False
    authority = host_authority(conn.headers.get("host"))
    if (conn.client and conn.client.host in LOOPBACK and authority and authority[0] in LOOPBACK
            and not any(h in conn.headers for h in PROXY_HEADERS)):
        return True
    scheme, _, credential = conn.headers.get("authorization", "").partition(" ")
    bearer = credential.strip() if scheme.lower() == "bearer" else ""
    supplied = bearer or conn.cookies.get("ishti_token") or conn.query_params.get("token")
    return token_matches(supplied, token)
