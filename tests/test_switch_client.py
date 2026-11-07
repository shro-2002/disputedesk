"""SwitchClient against a fake Switch (httpx.MockTransport). No network, no real sleeps."""

import json

import httpx
import pytest

from disputedesk.switch_client import (
    SwitchAuthError,
    SwitchClient,
    SwitchError,
    SwitchTakenOver,
    iter_sse,
    parse_message,
)

BASE = "http://switch.test"
AGENT = "agent-123"
KEY = "sk-test-key"


def msg_event(seq, sender="@switch-user-ab12:switch.local", body="hello", thread=None):
    """Documented shape: agent-connection.mdx envelope + agent-events.mdx message payload."""
    return {
        "event": "message",
        "data": {
            "type": "message", "room_id": "room-1", "bridge_id": None,
            "channel_type": "channel_public", "sequence": seq,
            "payload": {"addressed": True, "sender": sender, "sender_name": "priya",
                        "message_id": f"m{seq}", "body": body, "timestamp": 1700000000000,
                        "thread_id": thread, "attachments": []},
        },
        "id": seq,
    }


def sse(event):
    return f"event: {event['event']}\nid: {event['id']}\ndata: {json.dumps(event)}\n\n".encode()


class FakeSwitch:
    """Each GET /events pops the next scripted stream (list of byte chunks or an int status)."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/events"):
            script = self.streams.pop(0) if self.streams else 404
            if isinstance(script, int):
                return httpx.Response(script, text="nope")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=iter(script))
        if request.url.path.endswith("/connection/beat"):
            return httpx.Response(200, json={"ok": True, "rooms": [], "cursor": 0})
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json={"ok": True})
        if "/ops/" in request.url.path:
            if request.url.path.endswith("/ops/missing"):
                return httpx.Response(404, text="unknown operation " + "x" * 500)
            return httpx.Response(200, json={"result": {"posted": True}})
        return httpx.Response(404)

    @property
    def stream_requests(self):
        return [r for r in self.requests if r.url.path.endswith("/events")]


def dropping(*chunks):
    def gen():
        yield from chunks
        raise httpx.ReadError("connection reset")
    return gen()


def make_client(fake, sleeps=None, **kw):
    client = SwitchClient(BASE, AGENT, KEY, http_client=httpx.Client(transport=httpx.MockTransport(fake)),
                          sleep=(sleeps.append if sleeps is not None else lambda s: None), **kw)
    client._jitter = lambda d: d
    return client


def take(it, n):
    return [next(it) for _ in range(n)]


def test_sse_parsing_multiline_data_and_comments():
    lines = [": keep-alive", "", "event: message", "id: 7", "data: {\"a\":", "data: 1}", "",
             ":another comment", "data:x", ""]
    assert list(iter_sse(lines)) == [("message", '{"a":\n1}', "7"), ("message", "x", None)]


def test_events_yield_domain_body_and_skip_comments():
    fake = FakeSwitch([[b": keep-alive\n\n", sse(msg_event(1)), sse(msg_event(2))]])
    events = take(make_client(fake).events(), 2)
    assert [e["sequence"] for e in events] == [1, 2]
    req = fake.stream_requests[0]
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["accept"] == "text/event-stream"
    assert req.url.params["filter"] == "all" and req.url.params["start_from"] == "head"
    assert req.url.params["connection_id"]


def test_own_messages_are_skipped():
    own = msg_event(1, sender="@switch-agent-me:switch.local")
    fake = FakeSwitch([[sse(own), sse(msg_event(2))]])
    client = make_client(fake, own_sender_ids=["@switch-agent-me:switch.local"])
    assert next(client.events())["sequence"] == 2


def test_own_messages_skipped_by_agent_name():
    other = msg_event(3)
    other["data"]["payload"]["sender_name"] = "finops-lead"
    fake = FakeSwitch([[sse(msg_event(1)), sse(msg_event(2)), sse(other)]])
    client = make_client(fake, agent_name="priya")  # events 1 and 2 have sender_name "priya"
    assert next(client.events())["sequence"] == 3
    assert client.cursor == 2  # skipped own messages still advance the cursor


def test_reconnect_resumes_with_last_event_id_same_connection_id():
    fake = FakeSwitch([dropping(sse(msg_event(5))), [sse(msg_event(6))]])
    events = take(make_client(fake).events(), 2)
    assert [e["sequence"] for e in events] == [5, 6]
    first, second = fake.stream_requests
    assert "last-event-id" not in first.headers
    assert second.headers["last-event-id"] == "5"
    assert first.url.params["connection_id"] == second.url.params["connection_id"]


def test_backoff_grows_and_caps():
    sleeps = []
    fake = FakeSwitch([404] * 8 + [[sse(msg_event(1))]])
    next(make_client(fake, sleeps).events())
    assert sleeps == [1, 2, 4, 8, 16, 30, 30, 30]


def test_401_raises_without_retry():
    sleeps = []
    fake = FakeSwitch([401])
    with pytest.raises(SwitchAuthError):
        next(make_client(fake, sleeps).events())
    assert sleeps == [] and len(fake.stream_requests) == 1


def test_evicted_taken_over_stops():
    evicted = b'event: evicted\ndata: {"code": "taken_over", "reason": "x"}\n\n'
    fake = FakeSwitch([[evicted], [sse(msg_event(1))]])
    with pytest.raises(SwitchTakenOver):
        next(make_client(fake).events())
    assert len(fake.stream_requests) == 1


def test_beat_409_taken_over_raises_and_posts_documented_body():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(409, json={"code": "taken_over"})

    client = make_client(handler)
    client.cursor = 4813
    with pytest.raises(SwitchTakenOver):
        client._beat("conn-1")
    req = seen[0]
    assert req.method == "POST" and req.url.path == f"/agents/{AGENT}/connection/beat"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {"connection_id": "conn-1", "cursor": 4813}


def test_beat_ok_returns_body():
    fake = FakeSwitch([])
    assert make_client(fake)._beat("conn-1")["ok"] is True


def test_post_message_root_uses_message_endpoint():
    fake = FakeSwitch([])
    assert make_client(fake).post_message("room-1", "hi") == {"ok": True}
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path == f"/agents/{AGENT}/message"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {"room_id": "room-1", "content": "hi"}


def test_post_message_threaded_uses_post_message_op():
    fake = FakeSwitch([])
    client = make_client(fake)
    assert client.post_message("room-1", "hi", thread_id="m9") == {"posted": True}
    req = fake.requests[0]
    assert req.url.path == f"/agents/{AGENT}/ops/post_message"
    assert req.headers["x-switch-connection-id"] == client.connection_id
    assert json.loads(req.content) == {"body": "hi", "thread_id": "m9"}


def test_call_op_room_bound_and_unbound():
    fake = FakeSwitch([])
    client = make_client(fake)
    assert client.call_op("list_rooms", {}, room_bound=False) == {"posted": True}
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path == f"/agents/{AGENT}/ops/list_rooms"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert "x-switch-connection-id" not in req.headers
    assert json.loads(req.content) == {}


def test_call_op_error_has_status_and_excerpt_not_key():
    client = make_client(FakeSwitch([]))
    with pytest.raises(SwitchError) as exc:
        client.call_op("missing", {"a": 1})
    msg = str(exc.value)
    assert "404" in msg and "unknown operation" in msg and KEY not in msg and len(msg) < 300


def test_connection_id_stable_across_reconnects_and_matches_client():
    fake = FakeSwitch([dropping(sse(msg_event(1))), [sse(msg_event(2))]])
    client = make_client(fake)
    take(client.events(), 2)
    ids = {r.url.params["connection_id"] for r in fake.stream_requests}
    assert ids == {client.connection_id}


def test_on_connect_runs_on_every_open_and_failures_do_not_kill_stream():
    calls = []

    def on_connect():
        calls.append(client.connected.is_set())
        raise RuntimeError("role re-assume failed")

    fake = FakeSwitch([dropping(sse(msg_event(1))), [sse(msg_event(2))]])
    client = make_client(fake, on_connect=on_connect)
    it = client.events()
    assert [e["sequence"] for e in take(it, 2)] == [1, 2]
    assert calls == [True, True]  # once per successful open, connected already set
    assert client.connected.is_set()
    it.close()
    assert not client.connected.is_set()


def test_connected_cleared_after_drop():
    states = []
    fake = FakeSwitch([dropping(sse(msg_event(1))), 404, [sse(msg_event(2))]])
    client = make_client(fake, sleeps=None)
    client._sleep = lambda s: states.append(client.connected.is_set())
    take(client.events(), 2)
    assert states == [False, False]


def test_post_message_401_raises():
    client = make_client(lambda r: httpx.Response(401))
    with pytest.raises(SwitchAuthError):
        client.post_message("room-1", "hi")


def test_parse_message_documented_shape():
    m = parse_message(msg_event(9, thread="t-root"))
    assert (m.room_id, m.message_id, m.thread_id, m.sender_id, m.sender_name, m.text, m.addressed,
            m.sequence) == ("room-1", "m9", "t-root", "@switch-user-ab12:switch.local", "priya",
                            "hello", True, 9)
    assert parse_message(msg_event(9, thread="t-root")["data"]) == m  # bare body works too
    assert parse_message({"type": "room_join", "payload": {}}) is None
