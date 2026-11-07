"""Switch Rooms agent client: SSE in, HTTP out.

DEPRECATION: the current Switch agent protocol is WebSocket in / HTTP out
(/switch-rooms/internals/agent-protocol.mdx). The SSE stream used here is
"deprecated and will be removed after a transition period"
(/switch-rooms/internals/agent-connection.mdx, "Clients built before the
WebSocket"). We keep SSE because the project brief says "SSE in, HTTP out".

Everything below comes from the official docs (page in brackets):

- Auth: ``Authorization: Bearer <agent API key>`` on the stream and every call
  [internals/agent-protocol.mdx "Transport and auth"; internals/identity-and-access.mdx "Entry points"].
- Stream: ``GET /agents/{agent_id}/events?connection_id=&scope=&filter=&start_from=
  [&rooms=][&client=&client_version=]``, ``Accept: text/event-stream``,
  ``Last-Event-ID: <sequence>`` (wins over start_from); sequence arrives on the
  ``id:`` line as text; a bare SSE comment every 15 s when idle
  [internals/agent-connection.mdx "Clients built before the WebSocket"].
  Open errors: 400 bad params, 403 room not a member, 409 protocol mismatch or
  room already claimed [same section].
- connection_id: one UUID, reused on every reconnect
  [internals/agent-protocol.mdx "Writing a client" step 5; agent-connection.mdx "Open the socket"].
- Heartbeat: ``POST /agents/{agent_id}/connection/beat`` body
  ``{"connection_id", "cursor"}`` every 2 s, TTL 6 s. 200 ok, 401 bad key,
  404 reopen stream, 409 ``code`` in body (``taken_over`` is final, else reopen),
  422 malformed [agent-connection.mdx "Clients built before the WebSocket"].
- Domain events ``message`` / ``command`` / ``room_join`` carry ``type, room_id,
  bridge_id, channel_type, payload, sequence``; control frames include ``gap``
  (take ``resumed_at`` as the new cursor) and ``evicted`` (``code``)
  [agent-connection.mdx "Frames", "Gaps", "Eviction codes"].
- Message payload: ``addressed, sender, sender_name, message_id, body,
  timestamp (ms), thread_id, attachments`` [internals/agent-events.mdx "The event catalog"].
  ``sender`` is an opaque participant id ``@<localpart>:<server>``: match it, never parse it
  [agent-events.mdx "Participant ids"].
- Post: ``POST /agents/{agent_id}/message`` with ``{room_id, content}``, no connection needed
  [internals/agent-protocol.mdx "Writing a client" step 10].
- Operation discovery: ``GET /agents/{agent_id}/ops`` [agent-protocol.mdx "Discovery and invocation"].
- Operation call: ``POST /agents/{agent_id}/ops/{op}``, body = the arguments object itself,
  ``X-Switch-Connection-Id: <connection id>`` binds the call to the connection's current room,
  response ``{"result": ...}``, errors 400/403/404/409 [agent-protocol.mdx "Discovery and invocation"].
- Threaded reply: the ``post_message`` operation takes ``{"body": str, "thread_id": str|null}``,
  room implicit from the connection (live ``GET /agents/{id}/ops`` dump, switch/ops-schemas.json).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

import httpx

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 2.0
BACKOFF_START_S = 1.0
BACKOFF_CAP_S = 30.0
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
DOMAIN_EVENTS = frozenset({"message", "command", "room_join"})


class SwitchError(Exception):
    """Any failure talking to Switch."""


class SwitchAuthError(SwitchError):
    """401: missing or invalid agent API key. Never retried."""


class SwitchTakenOver(SwitchError):
    """``taken_over``: another open took this connection. Final, do not reconnect."""


class _Reconnect(Exception):
    """Internal: drop the current stream and reopen it."""


@dataclass(frozen=True)
class RoomMessage:
    room_id: str
    message_id: str | None
    thread_id: str | None
    sender_id: str | None  # payload.sender, opaque participant id "@<localpart>:<server>"
    sender_name: str | None  # payload.sender_name, display name of the author
    text: str  # payload.body
    addressed: bool  # payload.addressed: Switch decided this message addresses the agent
    timestamp_ms: int | None
    sequence: int | None


def _unwrap(event: dict) -> tuple[str | None, dict]:
    """Return (kind, body). Accepts the documented envelope {"event","data","id"} or a bare body."""
    if isinstance(event.get("data"), dict) and "event" in event:
        return event["event"], event["data"]
    return event.get("type"), event


def parse_message(event: dict) -> RoomMessage | None:
    """Normalise a ``message`` domain event; anything else returns None."""
    kind, body = _unwrap(event)
    if kind != "message":
        return None
    payload = body.get("payload") or {}
    return RoomMessage(
        room_id=body.get("room_id"),
        message_id=payload.get("message_id"),
        thread_id=payload.get("thread_id"),
        sender_id=payload.get("sender"),
        sender_name=payload.get("sender_name"),
        text=payload.get("body") or "",
        addressed=bool(payload.get("addressed")),
        timestamp_ms=payload.get("timestamp"),
        sequence=body.get("sequence"),
    )


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, str, str | None]]:
    """Parse SSE lines into (event, data, id) frames. Comment lines (":...") are ignored."""
    event, data, event_id = "message", [], None
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data), event_id
            event, data, event_id = "message", [], None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
        elif field == "id":
            event_id = value
    if data:
        yield event, "\n".join(data), event_id


class SwitchClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str,
        *,
        rooms: Iterable[str] | None = None,
        event_filter: str = "all",
        client_name: str = "disputedesk",
        http_client: httpx.Client | None = None,
        scope: str = "single",
        own_sender_ids: Iterable[str] = (),
        agent_name: str | None = None,
        sleep: Callable[[float], Any] | None = None,
        on_connect: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.rooms = list(rooms or [])
        self.event_filter = event_filter
        self.client_name = client_name
        self.scope = scope
        # OPEN QUESTION: the docs never say how an agent learns its own participant id
        # (payload.sender). We match agent_id, any ids the caller supplies, and agent_name.
        self.own_sender_ids = {agent_id, *own_sender_ids}
        self.agent_name = agent_name
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http_client or httpx.Client(timeout=TIMEOUT)
        self._sleep = sleep
        self._jitter: Callable[[float], float] = lambda d: d * random.uniform(0.5, 1.0)
        self.cursor: int | None = None
        self._generation: Any = None
        # One connection id per client run, reused on every reconnect (agent-protocol.mdx step 5).
        self.connection_id = str(uuid.uuid4())
        self.on_connect = on_connect
        self.connected = threading.Event()  # set while the SSE stream is open

    # ---------- HTTP out ----------

    def post_message(self, room_id: str, text: str, *, thread_id: str | None = None) -> Any:
        """Root post via POST /agents/{id}/message; threaded reply via the post_message op.

        The op has no room_id: it posts to the room this connection holds (claimed with
        ``rooms=`` on the stream), so room_id is only used for root posts.
        """
        if thread_id:
            return self.call_op("post_message", {"body": text, "thread_id": thread_id})
        resp = self._request("POST", f"/agents/{self.agent_id}/message",
                             json={"room_id": room_id, "content": text})
        return resp.json() if resp.content else {}

    def call_op(self, op: str, args: dict, *, room_bound: bool = True) -> Any:
        """POST /agents/{id}/ops/{op} with the arguments as the body; returns ``result``."""
        headers = {"X-Switch-Connection-Id": self.connection_id} if room_bound else {}
        resp = self._request("POST", f"/agents/{self.agent_id}/ops/{op}", json=args, headers=headers)
        return resp.json().get("result")

    def list_operations(self) -> dict:
        """GET /agents/{id}/ops: the live operation registry (shows post_message's real schema)."""
        return self._request("GET", f"/agents/{self.agent_id}/ops").json()

    def _request(self, method: str, path: str, *, headers: dict | None = None,
                 **kw: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, self.base_url + path,
                                      headers={**self._headers, **(headers or {})}, **kw)
        except httpx.HTTPError as err:
            raise SwitchError(f"{method} {path} failed: {err}") from err
        if resp.status_code == 401:
            raise SwitchAuthError(f"{method} {path}: 401, agent API key missing or invalid")
        if resp.is_error:
            raise SwitchError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp

    def close(self) -> None:
        self._http.close()

    # ---------- SSE in ----------

    def events(self, stop: threading.Event | None = None) -> Iterator[dict]:
        """Yield domain events (the envelope's ``data`` object) forever, skipping own messages."""
        stop = stop or threading.Event()
        sleep = self._sleep or stop.wait
        connection_id = self.connection_id
        attempt = 0
        while not stop.is_set():
            try:
                for event in self._stream_once(connection_id, stop):
                    attempt = 0
                    yield event
                attempt = 0  # clean end of stream (server closed): reconnect promptly
            except (httpx.HTTPError, _Reconnect) as err:
                if stop.is_set():
                    return
                delay = self._jitter(min(BACKOFF_CAP_S, BACKOFF_START_S * 2 ** attempt))
                attempt += 1
                log.warning("switch stream dropped (%s); reconnecting in %.1fs", err, delay)
                sleep(delay)

    def _stream_once(self, connection_id: str, stop: threading.Event) -> Iterator[dict]:
        params = {"connection_id": connection_id, "scope": self.scope,
                  "filter": self.event_filter, "start_from": "head",
                  "client": self.client_name, "client_version": "0.1.0"}
        if self.rooms:
            params["rooms"] = ",".join(self.rooms)
        if self._generation is not None:
            params["expected_generation"] = self._generation
        headers = {**self._headers, "Accept": "text/event-stream"}
        if self.cursor is not None:
            headers["Last-Event-ID"] = str(self.cursor)
        url = f"{self.base_url}/agents/{self.agent_id}/events"
        with self._http.stream("GET", url, params=params, headers=headers, timeout=TIMEOUT) as resp:
            self._check_open(resp)
            log.info("switch stream open (connection %s, cursor %s)", connection_id, self.cursor)
            self.connected.set()
            if self.on_connect is not None:
                try:
                    self.on_connect()
                except Exception:
                    log.exception("on_connect callback failed; stream continues")
            failure: list[BaseException] = []
            done = threading.Event()
            beat = threading.Thread(target=self._beat_loop,
                                    args=(connection_id, resp, done, stop, failure), daemon=True)
            beat.start()
            try:
                for kind, data, event_id in iter_sse(resp.iter_lines()):
                    event = self._handle_frame(kind, data, event_id)
                    if event is not None:
                        yield event
                        self.cursor = max(self.cursor or 0, event.get("sequence") or 0)
            except httpx.HTTPError:
                if failure:
                    raise failure[0] from None
                raise
            finally:
                done.set()
                self.connected.clear()
            if failure:
                raise failure[0]

    def _check_open(self, resp: httpx.Response) -> None:
        if resp.status_code == 200:
            return
        resp.read()
        if resp.status_code == 401:
            raise SwitchAuthError("stream open: 401, agent API key missing or invalid")
        if resp.status_code == 409 and "taken_over" in resp.text:
            raise SwitchTakenOver("stream open refused: taken_over")
        if resp.status_code in (404, 409) or resp.status_code >= 500:
            raise _Reconnect(f"stream open HTTP {resp.status_code}")
        raise SwitchError(f"stream open HTTP {resp.status_code}: {resp.text[:200]}")

    def _handle_frame(self, kind: str, data: str, event_id: str | None) -> dict | None:
        try:
            raw = json.loads(data)
        except json.JSONDecodeError:
            log.warning("ignoring non-JSON SSE frame (event=%s)", kind)
            return None
        # OPEN QUESTION: the docs show the WebSocket envelope {"event","data","id"} but not what
        # the SSE data: line holds. We accept the envelope or a bare body.
        env_kind, body = _unwrap(raw) if isinstance(raw, dict) else (None, {})
        kind = env_kind or kind
        if kind in DOMAIN_EVENTS:
            if body.get("sequence") is None and event_id and event_id.isdigit():
                body = {**body, "sequence": int(event_id)}
            if self._is_own(body):
                self.cursor = max(self.cursor or 0, body.get("sequence") or 0)
                return None
            return body
        if kind == "connection_state":
            self._generation = body.get("generation", self._generation)
        elif kind == "gap":
            log.warning("switch gap: %s; re-read room history before replying", body.get("reason"))
            if isinstance(body.get("resumed_at"), int):
                self.cursor = body["resumed_at"]
        elif kind == "evicted":
            code = body.get("code")
            if code == "taken_over":
                raise SwitchTakenOver("evicted: taken_over")
            raise _Reconnect(f"evicted: {code}")
        return None

    def _is_own(self, body: dict) -> bool:
        if body.get("type") != "message":
            return False
        payload = body.get("payload") or {}
        if payload.get("sender") in self.own_sender_ids:
            return True
        return self.agent_name is not None and payload.get("sender_name") == self.agent_name

    def _beat_loop(self, connection_id: str, resp: httpx.Response, done: threading.Event,
                   stop: threading.Event, failure: list[BaseException]) -> None:
        while not done.wait(HEARTBEAT_INTERVAL_S):
            try:
                if stop.is_set():
                    raise _Reconnect("stopped")
                self._beat(connection_id)
            except (SwitchError, _Reconnect) as err:
                failure.append(err)
                resp.close()  # unblocks the reader thread
                return

    def _beat(self, connection_id: str) -> dict:
        body = {"connection_id": connection_id, "cursor": self.cursor or 0}
        try:
            resp = self._http.post(f"{self.base_url}/agents/{self.agent_id}/connection/beat",
                                   json=body, headers=self._headers)
        except httpx.HTTPError as err:
            raise _Reconnect(f"beat failed: {err}") from err
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            raise SwitchAuthError("beat: 401, agent API key missing or invalid")
        if resp.status_code == 409:
            code = (resp.json() if resp.content else {}).get("code")
            if code == "taken_over":
                raise SwitchTakenOver("beat: 409 taken_over")
            raise _Reconnect(f"beat: 409 {code}")
        if resp.status_code == 404:
            raise _Reconnect("beat: 404 connection unknown or dead")
        raise SwitchError(f"beat: HTTP {resp.status_code} {resp.text[:200]}")
