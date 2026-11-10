"""Discord and Slack remote channels against fake platform APIs: owner-only, history is not replayed, commands and
forbidden actions are handled like WhatsApp/Telegram, yes/no answers reach a pending approval."""
import datetime as dt
import json
import time

import httpx
import pytest

from ishti.channels import dm_channels

OWNER, STRANGER = "111111", "999999"


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


class FakeDiscord:
    def __init__(self):
        self.msgs, self.sent = [{"id": "100", "author": {"id": OWNER}, "content": "old history", "timestamp": _iso(time.time())}], []

    def add(self, author, text, age=0, bot=False):
        mid = str(int(self.msgs[-1]["id"]) + 1)
        self.msgs.append({"id": mid, "author": {"id": author, "bot": bot}, "content": text, "timestamp": _iso(time.time() - age)})

    def handler(self, request: httpx.Request):
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "D1"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"username": "ishti-bot"})
        if request.method == "POST":
            self.sent.append(json.loads(request.content)["content"])
            return httpx.Response(200, json={"id": "x"})
        after = request.url.params.get("after")
        rows = [m for m in self.msgs if after is None or int(m["id"]) > int(after)]
        rows = rows[-1:] if request.url.params.get("limit") == "1" else rows
        return httpx.Response(200, json=list(reversed(rows)))


@pytest.fixture
def discord(tmp_path):
    fake = FakeDiscord()
    api = dm_channels.DiscordAPI("t", http=httpx.Client(base_url="https://discord.com/api/v10",
                                                        transport=httpx.MockTransport(fake.handler)))
    calls = []
    channel = dm_channels.DMChannel("discord", api, OWNER, background=False, allow_message=lambda s: True,
                                    cursor_path=str(tmp_path / "cursor.json"),
                                    dispatch=lambda text, session_id, channel: calls.append((text, session_id, channel))
                                    or {"final_answer": f"done: {text}"})
    return fake, channel, calls


def test_history_is_not_replayed_and_only_the_owner_is_obeyed(discord):
    fake, channel, calls = discord
    assert channel.poll_once() == 0 and not calls                      # first poll only records where "now" is
    fake.add(STRANGER, "delete everything")
    fake.add(OWNER, "ishti reply", bot=True)
    fake.add(OWNER, "what's the weather")
    channel.poll_once()
    assert calls == [("what's the weather", f"discord:{OWNER}", "discord")]
    assert fake.sent == ["done: what's the weather"]


def test_commands_forbidden_and_stale(discord, monkeypatch):
    fake, channel, calls = discord
    channel.poll_once()
    monkeypatch.setattr(dm_channels.Gate, "classify_forbidden", staticmethod(lambda t: "format a drive" if "format" in t else None))
    fake.add(OWNER, "/help")
    fake.add(OWNER, "format my C drive")
    fake.add(OWNER, "send the report", age=3600)
    channel.poll_once()
    assert not calls
    assert "Commands:" in fake.sent[0] and "Refused" in fake.sent[1] and "stale" in fake.sent[2]


def test_yes_reaches_a_pending_approval(discord):
    fake, channel, calls = discord
    channel.poll_once()
    channel.dispatch = lambda text, session_id, channel: {"final_answer": "Send it? (yes/no)" if text != "yes" else "Sent."}
    fake.add(OWNER, "send hi to Ali")
    fake.add(OWNER, "Y")
    channel.poll_once()
    assert fake.sent == ["Send it? (yes/no)", "Sent."]


def test_cursor_survives_a_restart(discord, tmp_path):
    fake, channel, calls = discord
    channel.poll_once()
    fake.add(OWNER, "hello")
    channel.poll_once()
    again = dm_channels.DMChannel("discord", channel.api, OWNER, background=False, allow_message=lambda s: True,
                                  cursor_path=channel.cursor_path, dispatch=channel.dispatch)
    assert again.poll_once() == 0 and len(calls) == 1                  # "hello" is not run twice


def test_slack_reads_the_dm_and_replies():
    state = {"msgs": [{"ts": "1.000001", "user": OWNER, "text": "old"}], "sent": []}

    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "conversations.open":
            return httpx.Response(200, json={"ok": True, "channel": {"id": "C1"}})
        if method == "chat.postMessage":
            state["sent"].append(form["text"])
            return httpx.Response(200, json={"ok": True})
        rows = [m for m in state["msgs"] if "oldest" not in form or float(m["ts"]) > float(form["oldest"])]
        return httpx.Response(200, json={"ok": True, "messages": list(reversed(rows[-1:] if form.get("limit") == "1" else rows))})

    api = dm_channels.SlackAPI("t", http=httpx.Client(base_url="https://slack.com/api/", transport=httpx.MockTransport(handler)))
    import tempfile, os
    seen = []
    ch = dm_channels.DMChannel("slack", api, OWNER, background=False, allow_message=lambda s: True,
                               cursor_path=os.path.join(tempfile.mkdtemp(), "c.json"),
                               dispatch=lambda text, session_id, channel: seen.append(channel) or {"final_answer": "ok"})
    ch.poll_once()
    state["msgs"].append({"ts": f"{time.time():.6f}", "user": OWNER, "text": "status of my reminders"})
    state["msgs"].append({"ts": f"{time.time() + 1:.6f}", "bot_id": "B1", "text": "ok"})
    ch.poll_once()
    assert seen == ["slack"] and state["sent"] == ["ok"]


def test_a_rejected_token_is_reported_not_retried_every_poll():
    api = dm_channels.DiscordAPI("bad", http=httpx.Client(base_url="https://discord.com/api/v10",
                                                          transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(dm_channels.ChannelError):
        api.me()


def test_channel_setup_asks_first_and_masks_the_token():
    from ishti.skills.extensions import skill as ext
    s = {}
    out = ext.run("connect discord bot MTIzNDU2Nzg5MDEyMzQ1Njc4OTA.abc.def owner 123456789012345678", s)
    assert "(yes/no)" in out["reply"] and "123456789012345678" in out["reply"]
    assert "MTIzNDU2Nzg5MDEyMzQ1Njc4OTA.abc.def" in s["redact"] and "MTIzNDU2" not in out["reply"]


def test_service_reports_discord_and_slack():
    from ishti.channels.service import RemoteService

    class Bridge:
        @staticmethod
        def status():
            return {"connected": False}
    health = RemoteService(bridge=Bridge()).health()
    assert health["discord"]["running"] is False and health["slack"]["enabled"] is False
