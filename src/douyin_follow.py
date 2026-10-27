# Local migration: one account feed supplements the existing room polling loop.
# The feed contains currently live followed rooms, rather than the complete follow list.
# Only positive, fresh matches can replace an individual room request.
# Missing rooms, empty feeds and failed requests all retain individual polling.
# A room disappearing from this cache never means it has gone offline.
# Numeric web_rid values match configured room URLs exactly, without prefix matching.
# Short links and profile URLs continue through the fork's existing resolvers.
# Recommended and explicitly unfollowed entries are excluded before caching.
# The live-only feed can encode status zero while supplying valid live streams.
# Both HLS and FLV maps are required before normalizing that status to live.
# The internal room ID is retained for the fork's optional danmaku collector.
# Deep copies isolate cached stream maps from the mutable stream-quality selector.
# Cookie values remain in memory and never enter exception messages here.
# The signer is copied from the old local-source to avoid changing its signature format.
# Each response is validated before a revision is published to room workers.
# Revisions let already-running workers wake only for a newer positive snapshot.
# Configuration changes invalidate cached hints before the next account request.
# An old in-flight response cannot overwrite a newer Cookie's cache.
# A failed request invalidates existing hints immediately rather than extending their life.
# Initial readiness is signaled on success, failure, or disabling the feature.
# The initial wait is bounded so an unreachable feed cannot block startup forever.
# Polling settings are reloaded by the existing main configuration loop.
# One daemon thread serves every configured anchor and checks shutdown each second.
# start() is idempotent because the Web engine may enter main() more than once.
# No account mutations or automatic additions to URL_config.ini occur here.
# Shared followed-live hints; missing rooms always retain individual polling.
import threading
import time
from copy import deepcopy
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
FOLLOW_ENDPOINT = "https://www.douyin.com/webcast/web/feed/follow/"


def room_id(url: str) -> str | None:
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    return path if parsed.hostname == "live.douyin.com" and path.isdigit() else None


def parse_follow_feed(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("status_code") != 0:
        raise ValueError("关注接口返回失败状态（请检查抖音 Cookie）")
    container = payload.get("data")
    items = container.get("data") if isinstance(container, dict) else container
    if not isinstance(items, list):
        raise ValueError("关注接口响应格式不符合预期")
    rooms = {}
    for item in items:
        if not isinstance(item, dict) or item.get("is_recommend") not in (None, 0, False):
            continue
        room = item.get("room") or {}
        if not isinstance(room, dict):
            continue
        owner = room.get("owner") or {}
        if not isinstance(owner, dict):
            continue
        followed = (owner.get("follow_info") or {}).get("follow_status")
        if followed is not None and str(followed) not in {"1", "2"}:
            continue
        rid = str(item.get("web_rid") or "")
        streams = room.get("stream_url") or {}
        # This live-only feed often serializes the room status as protobuf default 0.
        # Require real stream URLs, an owner, and an explicitly followed/non-recommended entry.
        if (
            not rid.isdigit()
            or not owner.get("nickname")
            or not isinstance(streams, dict)
            or str(room.get("status", 0)) not in {"0", "2"}
        ):
            continue
        if not streams.get("flv_pull_url") or not streams.get("hls_pull_url_map"):
            continue
        rooms[rid] = {
            "anchor_name": owner["nickname"],
            "title": room.get("title") or "",
            "status": 2,
            "id_str": str(room.get("id_str") or room.get("id") or ""),
            "stream_url": deepcopy(streams),
        }
    return rooms


def fetch_follow_feed(cookie: str, proxy: str) -> Any:
    from src.ab_sign import ab_sign

    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "scene": "aweme_pc_follow_top",
        "count": "50",
        "cursor": "0",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
    }
    query = urlencode(params)
    url = FOLLOW_ENDPOINT + "?" + query + "&a_bogus=" + ab_sign(query, USER_AGENT)
    with httpx.Client(proxy=proxy or None, timeout=15, http2=True, follow_redirects=True) as client:
        response = client.get(
            url, headers={"user-agent": USER_AGENT, "cookie": cookie, "referer": "https://www.douyin.com/follow"}
        )
        response.raise_for_status()
        return response.json()


class DouyinFollowMonitor:
    def __init__(
        self,
        stop_event: threading.Event,
        log: Callable[..., Any] = print,
        fetch: Callable[..., Any] = fetch_follow_feed,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stop = stop_event
        self._wake = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._settings = (False, "", 60, "")
        self._thread: threading.Thread | None = None
        self._rooms: dict[str, Any] = {}
        self._last_success: float | None = None
        self._revision = 0
        self._log = log
        self._fetch = fetch
        self._clock = clock

    def configure(self, enabled: bool, cookie: str, interval: int = 60, proxy: str = "") -> None:
        settings = (bool(enabled), cookie.strip(), max(15, int(interval)), proxy or "")
        with self._lock:
            if settings == self._settings:
                return
            self._settings = settings
            self._ready.clear()
            if not settings[0] or not settings[1]:
                self._ready.set()
            self._rooms = {}
            self._last_success = None
        self._wake.set()

    def wait_initial(self, timeout: float = 20) -> bool:
        return self._ready.wait(timeout)

    def get_room(self, url: str) -> tuple[int, dict[str, Any] | None]:
        rid = room_id(url)
        with self._lock:
            enabled, cookie, interval, _ = self._settings
            fresh = self._last_success is not None and self._clock() - self._last_success <= interval + 20
            if not enabled or not cookie or not fresh or self._stop.is_set():
                return self._revision, None
            return self._revision, deepcopy(self._rooms.get(rid)) if rid is not None else None

    def poll_once(self) -> None:
        with self._lock:
            settings = self._settings
        enabled, cookie, _, proxy = settings
        if not enabled or not cookie or self._stop.is_set():
            return
        try:
            rooms = parse_follow_feed(self._fetch(cookie, proxy))
        except Exception as error:
            with self._lock:
                if settings == self._settings:
                    self._rooms = {}
                    self._last_success = None
                    self._ready.set()
            # Never log exception details: HTTP errors can contain signed request URLs.
            self._log(f"抖音关注轮询失败（{type(error).__name__}），使用逐房间检测；请检查网络或 Cookie。", flush=True)
            return
        with self._lock:
            if settings != self._settings or self._stop.is_set():
                return
            self._rooms = rooms
            self._last_success = self._clock()
            self._revision += 1
            self._ready.set()
        self._log(f"抖音关注轮询成功：收到 {len(rooms)} 个关注直播间，按现有主播列表匹配。", flush=True)

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.poll_once()
            with self._lock:
                interval = self._settings[2]
            # Check shutdown every second, including when disabled.
            deadline = self._clock() + interval
            while not self._stop.is_set() and not self._wake.is_set() and self._clock() < deadline:
                self._wake.wait(timeout=min(1, max(0, deadline - self._clock())))

    def start(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        thread = threading.Thread(target=self.run, name="douyin-follow-poll", daemon=True)
        self._thread = thread
        thread.start()
        return thread
