# 抖音私信（网页版 IM）读取与直播分享解析。
#
# 为什么不用浏览器：网页版私信页本身由 SDK 通过 HTTP protobuf 拉取历史消息，
# `imapi.douyin.com/v1/message/get_by_conversation` 只认 Cookie（实测无需 a_bogus/msToken）。
# 这里直接复用该只读接口，避免把 Chromium/Playwright 拖进录制镜像。
#
# 请求/响应是 protobuf，字段号来自网页前端 im_proto；本模块只解析读取所需字段，
# 不发送消息、不修改会话、不调用任何写接口。 Cookie 只从调用方传入，绝不落盘或写日志。
#
# 直播分享消息的 content 是 JSON：有 `room_id` + `room_owner_sec_id`，但没有 web_rid。
# web_rid 通过 IM 用户信息接口换到 unique_id（抖音号）后拼成
# `https://live.douyin.com/<unique_id>`，再用 web/enter 校验是否在播。
#
# 所有网络/解析错误只记日志并跳过本轮，绝不影响录制主流程。

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

import i18n
from src.logger import logger

IM_BASE = "https://imapi.douyin.com"
USER_INFO_URL = "https://www.douyin.com/aweme/v1/web/im/user/info/"
LIVE_ENTER_URL = "https://live.douyin.com/webcast/room/web/enter/"
CHAT_REFERER = "https://www.douyin.com/chat?isPopup=1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
# 网页当前使用的 IM 构建号；字段本身不参与鉴权，但缺少它会被 SDK 侧判成旧客户端。
IM_BUILD = "0d50935:feat/pc-im-group"
IM_SDK_VERSION = "0.1.8"
# 服务端用 future timestamp 表示「从最新往前翻」；沿用网页 SDK 的哨兵值。
LATEST_TIMESTAMP = 9999999999999999
# 单次读取上限；网页 SDK 默认 50。
MESSAGE_PAGE_SIZE = 50
# 分享去重窗口：同一房间在一天内只尝试一次，避免重复分享刷配置。
SHARE_TTL_SECONDS = 24 * 60 * 60
# 用户信息缓存：昵称/抖音号变化很少，避免每个分享都多打一次接口。
USER_CACHE_TTL_SECONDS = 30 * 60
# 单轮最多处理的会话数，防止账号会话极多时一轮打太多请求。
MAX_CONVERSATIONS_PER_POLL = 50
# 单轮最多新增直播间数，超出留到下一轮（避免同一波分享瞬间拉满配置）。
MAX_ADDS_PER_POLL = 5

# append_room_line 的返回值（机器码，供日志/测试断言，不翻译）。
ADDED = "added"
EXISTS_ENABLED = "exists"
EXISTS_DISABLED = "disabled"
INVALID = "invalid"
WRITE_FAILED = "write_failed"


class DmApiError(RuntimeError):
    # 私信接口返回非 0 状态 / 非法 protobuf / 登录态失效。
    pass


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    short_id: int
    last_activity: int = 0


@dataclass(frozen=True)
class DmMessage:
    server_id: str
    type_code: int
    sender_uid: str
    sender_sec_uid: str
    created_ms: int
    content: dict[str, Any]


@dataclass(frozen=True)
class LiveShare:
    room_id: str
    owner_uid: str
    owner_sec_uid: str
    owner_name: str
    server_id: str
    created_ms: int


# 可变长整数编码（protobuf varint）。
def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


# 构造一个 varint 字段。
def _f_varint(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


# 构造一个长度前缀字段（bytes/字符串）。
def _f_bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


# 构造一个 UTF-8 字符串字段。
def _f_text(field: int, value: str) -> bytes:
    return _f_bytes(field, value.encode("utf-8"))


# 读取一个 varint；越界或超长一律抛 ValueError，避免坏包把解析循环拖死。
# 上限 70 位移位：抖音当前字段远小于 64 位，超过这个长度只能是坏包/恶意帧。
def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            break
    raise ValueError("truncated varint")


# 把 protobuf 消息拆成 (字段号, wire type, 值) 列表；只处理 0/1/2/5 四种 wire type。
def _parse_fields(data: bytes) -> list[tuple[int, int, Any]]:
    fields: list[tuple[int, int, Any]] = []
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > 10000:
            raise ValueError("invalid field")
        value: Any
        if wire == 0:
            value, offset = _read_varint(data, offset)
        elif wire == 1:
            if offset + 8 > len(data):
                raise ValueError("truncated fixed64")
            value = data[offset : offset + 8]
            offset += 8
        elif wire == 2:
            length, offset = _read_varint(data, offset)
            if offset + length > len(data):
                raise ValueError("truncated bytes")
            value = data[offset : offset + length]
            offset += length
        elif wire == 5:
            if offset + 4 > len(data):
                raise ValueError("truncated fixed32")
            value = data[offset : offset + 4]
            offset += 4
        else:
            raise ValueError("unsupported wire type")
        fields.append((number, wire, value))
    return fields


# 取第一个指定字段；没有返回 None。
def _field(fields: list[tuple[int, int, Any]], number: int) -> Any:
    for field_number, _, value in fields:
        if field_number == number:
            return value
    return None


# 取全部指定字段（repeated）。
def _all_fields(fields: list[tuple[int, int, Any]], number: int) -> list[Any]:
    return [value for field_number, _, value in fields if field_number == number]


# 字段转字符串；非 bytes 返回空串。
def _as_text(value: Any) -> str:
    if not isinstance(value, bytes):
        return ""
    return value.decode("utf-8", errors="replace")


# 字段转 int；非 int 返回 0。
def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# 构造 IM 请求信封：外层 PushFrame 字段 1/2/3/5/6/7/8/9/11/14/18/21。
# 字段 8 里再套一层 command 字段，与网页 SDK 的 RequestBody 同形。
def build_im_envelope(command: int, body: bytes, request_id: int = 10006) -> bytes:
    return (
        _f_varint(1, command)
        + _f_varint(2, request_id)
        + _f_text(3, IM_SDK_VERSION)
        + _f_varint(5, 3)
        + _f_varint(6, 0)
        + _f_text(7, IM_BUILD)
        + _f_bytes(8, _f_bytes(command, body))
        + _f_text(9, "0")
        + _f_text(11, "douyin_pc")
        + _f_text(14, "360000")
        + _f_varint(18, 1)
        + _f_text(21, "douyin_pc")
    )


# 校验响应状态：字段 3=状态码，字段 4=状态文案（成功为 OK）。
# 状态码非 0 或文案非 OK 都抛 DmApiError，调用方据此判登录态/接口变化。
# 空 body / 截断帧同样收敛到 DmApiError，不让异常穿透轮询线程。
def _check_im_response(payload: bytes) -> list[tuple[int, int, Any]]:
    try:
        top = _parse_fields(payload)
    except ValueError as error:
        raise DmApiError("invalid_response") from error
    status = _field(top, 3)
    message = _as_text(_field(top, 4))
    if isinstance(status, int) and status != 0:
        raise DmApiError(f"status_{status}")
    if message and message != "OK":
        raise DmApiError("rejected")
    return top


# 解析 `/v1/conversation/get_list`：f6 -> f2006 -> repeated f1 会话项。
# 会话项 f1=conversation_id、f2=short_id、f50=最后一条消息摘要（f4 秒级时间）。
def parse_conversations(payload: bytes) -> list[Conversation]:
    top = _check_im_response(payload)
    body = _field(top, 6)
    if not isinstance(body, bytes):
        return []
    listing = _field(_parse_fields(body), 2006)
    if not isinstance(listing, bytes):
        return []
    result: list[Conversation] = []
    for item in _all_fields(_parse_fields(listing), 1):
        if not isinstance(item, bytes):
            continue
        fields = _parse_fields(item)
        conversation_id = _as_text(_field(fields, 1))
        short_id = _as_int(_field(fields, 2))
        if not conversation_id or short_id <= 0:
            continue
        last_activity = 0
        last = _field(fields, 50)
        if isinstance(last, bytes):
            last_activity = _as_int(_field(_parse_fields(last), 4))
        result.append(Conversation(conversation_id, short_id, last_activity))
    return result


# 解析 `/v1/message/get_by_conversation`：f6 -> f301 -> repeated f1 消息项。
# 消息项：f3=server_id、f6=type、f7=sender_uid、f8=content JSON、f10=毫秒时间、f14=sender_sec_uid。
def parse_messages(payload: bytes) -> list[DmMessage]:
    top = _check_im_response(payload)
    body = _field(top, 6)
    if not isinstance(body, bytes):
        return []
    listing = _field(_parse_fields(body), 301)
    if not isinstance(listing, bytes):
        return []
    result: list[DmMessage] = []
    for item in _all_fields(_parse_fields(listing), 1):
        if not isinstance(item, bytes):
            continue
        fields = _parse_fields(item)
        raw_content = _field(fields, 8)
        content: dict[str, Any] = {}
        if isinstance(raw_content, bytes):
            try:
                parsed = json.loads(raw_content.decode("utf-8"))
                if isinstance(parsed, dict):
                    content = parsed
            except UnicodeDecodeError, json.JSONDecodeError:
                content = {}
        result.append(
            DmMessage(
                server_id=str(_as_int(_field(fields, 3))),
                type_code=_as_int(_field(fields, 6)),
                sender_uid=str(_as_int(_field(fields, 7))),
                sender_sec_uid=_as_text(_field(fields, 14)),
                created_ms=_as_int(_field(fields, 10)),
                content=content,
            )
        )
    return result


# 从消息 content 提取直播分享；分享消息的特征是同时带 room_id 与 room_owner_sec_id。
# 普通文本、视频分享（aweType=800）、商品分享都不满足该条件，直接返回 None。
def extract_live_share(message: DmMessage) -> Optional[LiveShare]:
    content = message.content
    room_id = str(content.get("room_id") or "").strip()
    owner_sec_uid = str(content.get("room_owner_sec_id") or "").strip()
    if not room_id.isdigit() or not owner_sec_uid.startswith("MS4w"):
        return None
    owner_name = str(content.get("room_owner_name") or content.get("push_detail") or "").strip()
    return LiveShare(
        room_id=room_id,
        owner_uid=str(content.get("room_owner_id") or "").strip(),
        owner_sec_uid=owner_sec_uid,
        owner_name=owner_name,
        server_id=message.server_id,
        created_ms=message.created_ms,
    )


# 抖音号/短 ID 拼成网页直播间地址；只允许字母数字下划线，杜绝路径注入。
def live_url_for(unique_id: str) -> Optional[str]:
    identity = unique_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
        return None
    return f"https://live.douyin.com/{identity}"


# 只读 IM 客户端：Cookie + 网页 UA；不发送消息、不写任何平台状态。
# 每个轮询周期创建一个实例（httpx.Client 不是跨线程安全的），由 with 保证关闭。
class DouyinDmClient:
    def __init__(self, cookie: str, proxy: str = "", timeout: float = 20.0) -> None:
        self._cookie = cookie.strip()
        # trust_env=False：代理只认显式配置，避免宿主 NO_PROXY 的畸形端口把请求打成
        # httpx.InvalidURL（本仓实测过 ':1]' 一类环境噪声）。
        self._client = httpx.Client(
            proxy=proxy.strip() or None,
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._users: dict[str, tuple[float, dict[str, Any]]] = {}

    def __enter__(self) -> "DouyinDmClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # 统一 IM POST：Content-Type/Accept 都是 protobuf，Cookie 放请求头。
    def _post_im(self, path: str, command: int, body: bytes) -> bytes:
        response = self._client.post(
            IM_BASE + path,
            content=build_im_envelope(command, body),
            headers={
                "Content-Type": "application/x-protobuf",
                "Accept": "application/x-protobuf",
                "Origin": "https://www.douyin.com",
                "Referer": CHAT_REFERER,
                "Cookie": self._cookie,
            },
        )
        if response.status_code != 200:
            raise DmApiError(f"http_{response.status_code}")
        return response.content

    # 会话列表：limit=50，按活跃时间倒序。
    # f1=sort_type(1=活跃时间)、f2=cursor(0 首页)、f3=con_type(1=单聊)、f4=limit。
    # 该接口返回的 last_activity 可能滞后于会话内最新消息，故调用方不能只按它增量。
    def list_conversations(self) -> list[Conversation]:
        body = _f_varint(1, 1) + _f_varint(2, 0) + _f_varint(3, 1) + _f_varint(4, 50)
        return parse_conversations(self._post_im("/v1/conversation/get_list", 2006, body))

    # 最近消息：从 future 哨兵值往旧翻一页，响应内按时间正序返回。
    # f1=conversation_id、f2=1、f3=short_id、f4=1、f5=timestamp、f6=每页条数。
    def get_messages(self, conversation: Conversation) -> list[DmMessage]:
        body = (
            _f_text(1, conversation.conversation_id)
            + _f_varint(2, 1)
            + _f_varint(3, conversation.short_id)
            + _f_varint(4, 1)
            + _f_varint(5, LATEST_TIMESTAMP)
            + _f_varint(6, MESSAGE_PAGE_SIZE)
        )
        return parse_messages(self._post_im("/v1/message/get_by_conversation", 301, body))

    # 批量用户信息：把 sec_uid 换成 unique_id/昵称；30 分钟内复用。
    # 分享消息只有 sec_uid，没有 web_rid；unique_id（抖音号）才是拼 live.douyin.com 的键。
    # 缓存按进程内 monotonic 计时，避免同一主播连续分享时重复打用户接口。
    def get_user_info(self, sec_user_ids: list[str]) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        missing = [
            sec
            for sec in sec_user_ids
            if sec and (sec not in self._users or now - self._users[sec][0] > USER_CACHE_TTL_SECONDS)
        ]
        if missing:
            response = self._client.post(
                USER_INFO_URL,
                data={"sec_user_ids": json.dumps(missing)},
                headers={
                    "Referer": CHAT_REFERER,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": self._cookie,
                },
            )
            if response.status_code != 200:
                raise DmApiError(f"user_http_{response.status_code}")
            try:
                payload = response.json()
            except ValueError as error:
                raise DmApiError("user_invalid_response") from error
            if not isinstance(payload, dict):
                raise DmApiError("user_invalid_response")
            for user in payload.get("data") or []:
                if isinstance(user, dict) and isinstance(user.get("sec_uid"), str):
                    self._users[user["sec_uid"]] = (now, user)
        return {sec: self._users[sec][1] for sec in sec_user_ids if sec in self._users}

    # 校验直播间：web_rid 可以是数字 web_rid，也可以是抖音号（实测两者都接受）。
    # 返回 None=接口拒绝/无房间；否则含 is_live、room_id、anchor、title。
    # room_id 回比分享里的 room_id 可识别「同名主播换了直播间/旧分享」的错配。
    # 参数取自网页 web/enter 的固定集合；msToken 留空，该只读接口不强制。
    def get_live_room(self, web_rid: str) -> Optional[dict[str, Any]]:
        response = self._client.get(
            LIVE_ENTER_URL,
            params={
                "aid": "6383",
                "app_name": "douyin_web",
                "live_id": "1",
                "device_platform": "web",
                "language": "zh-CN",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name": "Chrome",
                "browser_version": "141.0.0.0",
                "web_rid": web_rid,
                "msToken": "",
            },
            headers={"Referer": "https://live.douyin.com/", "Cookie": self._cookie},
        )
        if response.status_code != 200:
            raise DmApiError(f"live_http_{response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise DmApiError("live_invalid_response") from error
        if not isinstance(payload, dict) or payload.get("status_code") != 0:
            return None
        data = payload.get("data") or {}
        rooms = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rooms, list) or not rooms or not isinstance(rooms[0], dict):
            return None
        room = rooms[0]
        raw_owner = room.get("owner")
        owner: dict[str, Any] = raw_owner if isinstance(raw_owner, dict) else {}
        return {
            "is_live": room.get("status") == 2,
            "room_id": str(room.get("id_str") or ""),
            "anchor": str(owner.get("nickname") or ""),
            "title": str(room.get("title") or ""),
        }


# 一次轮询使用的不可变配置快照。主循环每轮重读 config.ini，轮询线程只读快照，
# 避免一次 poll 中途配置被改写导致「前半轮用旧 Cookie、后半轮用新代理」。
@dataclass(frozen=True)
class _DmSettings:
    enabled: bool = False
    cookie: str = ""
    proxy: str = ""
    interval: int = 60
    daily_limit: int = 20
    cooldown: int = 60
    quality: str = "原画"
    require_live: bool = False
    lookback_minutes: int = 30


# 私信直播分享监控：独立守护线程轮询，同一进程只跑一个。
# add_room 回调签名为 (url, quality, name) -> 状态码（见 ADDED 等常量）。
class DouyinDmMonitor:
    def __init__(
        self,
        stop_event: threading.Event,
        add_room: Callable[[str, str, str], str],
        clock: Callable[[], float] = time.monotonic,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        today: Callable[[], str] = lambda: time.strftime("%Y-%m-%d"),
        client_factory: Callable[[str, str], DouyinDmClient] = DouyinDmClient,
    ) -> None:
        self._stop = stop_event
        self._add_room = add_room
        self._clock = clock
        self._now_ms = now_ms
        self._today = today
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._settings = _DmSettings()
        self._thread: Optional[threading.Thread] = None
        # 每个会话的 server_id 游标：只处理比它新的消息，重启后归零（由回溯窗口兜底）。
        self._last_server_ids: dict[str, int] = {}
        # room_id -> 最近一次尝试时间；同一房间 24h 内不重复尝试，避免刷配置/接口。
        self._seen_rooms: dict[str, float] = {}
        # 每日新增计数按本地日期切换，不持久化；重启后由 URL_config 查重继续兜底。
        self._daily_date = ""
        self._daily_count = 0
        # 全局新增冷却：一波分享同时出现时只放行一个，避免瞬间拉满录制并发。
        self._last_add_at = 0.0
        self._enabled_logged = False

    # 热更新配置；Cookie/开关变化时清空游标，让下一轮重新看最近消息。
    def configure(
        self,
        enabled: bool,
        cookie: str,
        proxy: str = "",
        interval: int = 60,
        daily_limit: int = 20,
        cooldown: int = 60,
        quality: str = "原画",
        require_live: bool = False,
        lookback_minutes: int = 30,
    ) -> None:
        settings = _DmSettings(
            enabled=bool(enabled),
            cookie=cookie.strip(),
            proxy=proxy.strip(),
            interval=max(15, int(interval)),
            daily_limit=max(0, int(daily_limit)),
            cooldown=max(0, int(cooldown)),
            quality=(quality or "原画").strip() or "原画",
            require_live=bool(require_live),
            lookback_minutes=max(0, int(lookback_minutes)),
        )
        with self._lock:
            previous = self._settings
            self._settings = settings
            # Cookie 换号或开关切换时必须清游标：旧游标属于另一个账号/另一段监听，
            # 不清会让新 Cookie 永远从「比旧 server_id 更新」开始看，漏掉当前分享。
            if previous.cookie != settings.cookie or previous.enabled != settings.enabled:
                self._last_server_ids.clear()
                self._seen_rooms.clear()
                self._daily_date = ""
                self._daily_count = 0
        # 只在「关闭 -> 开启」时打一次启动日志；configure 每轮都会调用，否则会刷屏。
        if settings.enabled and not self._enabled_logged:
            logger.info(
                i18n.tr(
                    "[私信自动录制]已开始监听抖音私信，间隔 {interval} 秒",
                    interval=settings.interval,
                )
            )
            self._enabled_logged = True
        elif not settings.enabled:
            self._enabled_logged = False

    # 启动守护线程；重复调用无副作用。
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="douyin-dm-monitor", daemon=True)
        self._thread.start()

    # 线程主体：轮询失败只记日志，下一轮继续；停止信号用共享 Event，便于随进程退出。
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as error:
                logger.warning(
                    i18n.tr(
                        "[私信自动录制]本轮失败: {type_name}: {e}",
                        type_name=type(error).__name__,
                        e=error,
                    )
                )
            with self._lock:
                interval = self._settings.interval
            self._stop.wait(interval)

    # 单轮：拉会话 -> 拉每个会话最近消息 -> 提取分享 -> 解析并添加。
    # 不按会话列表的 last_activity 做增量：实测该字段会停留在一个旧值，而会话内已有
    # 更新的直播分享；只按 last_activity 跳过会永久漏掉这类会话。会话数由上限约束。
    def poll_once(self) -> None:
        with self._lock:
            settings = self._settings
        if not settings.enabled or not settings.cookie:
            return
        today = self._today()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0
        with self._client_factory(settings.cookie, settings.proxy) as client:
            conversations = client.list_conversations()[:MAX_CONVERSATIONS_PER_POLL]
            for conversation in conversations:
                if self._stop.is_set():
                    return
                try:
                    messages = client.get_messages(conversation)
                except DmApiError as error:
                    logger.debug(
                        i18n.tr(
                            "[私信自动录制]读取会话失败: {type_name}: {e}",
                            type_name=type(error).__name__,
                            e=error,
                        )
                    )
                    continue
                self._process_messages(client, conversation, messages, settings)

    # 处理单个会话的消息；游标按 server_id 单调推进，启动时只看最近 lookback 分钟。
    def _process_messages(
        self,
        client: DouyinDmClient,
        conversation: Conversation,
        messages: list[DmMessage],
        settings: _DmSettings,
    ) -> None:
        if not messages:
            return
        ordered = sorted(messages, key=lambda item: int(item.server_id or 0))
        last_server_id = self._last_server_ids.get(conversation.conversation_id, 0)
        latest_server_id = last_server_id
        cutoff_ms = self._now_ms() - settings.lookback_minutes * 60 * 1000
        added_this_poll = 0
        for message in ordered:
            server_id = int(message.server_id or 0)
            # 游标先判、再处理：重复拉到同一批消息时不会重复添加/重复打接口。
            # 注意即使消息早于回溯窗口也要推进 latest_server_id，否则下一轮还会反复读它。
            if server_id <= last_server_id:
                continue
            latest_server_id = max(latest_server_id, server_id)
            if message.created_ms and message.created_ms < cutoff_ms:
                continue
            share = extract_live_share(message)
            if share is None:
                continue
            # 每日上限 0 表示不限制（与 config.ini 其它「0为不限制」键同口径）。
            if settings.daily_limit > 0 and self._daily_count >= settings.daily_limit:
                logger.info(
                    i18n.tr(
                        "[私信自动录制]达到每日添加上限 {limit}，跳过 {anchor}",
                        limit=settings.daily_limit,
                        anchor=share.owner_name or share.room_id,
                    )
                )
                continue
            now = self._clock()
            # 冷却与 seen 都必须用「存在且未过期」判断；空字典默认 0 + TTL 在 monotonic
            # 较小的机器（刚开机）会误判成「刚看过」而永久跳过。
            if now - self._last_add_at < settings.cooldown:
                continue
            seen_at = self._seen_rooms.get(share.room_id)
            if seen_at is not None and now - seen_at < SHARE_TTL_SECONDS:
                continue
            result = self._resolve_and_add(client, share, settings)
            # 无论成功/跳过都记 seen：否则离线房间或已存在房间会在每个轮询周期重复打接口。
            if result == ADDED:
                self._daily_count += 1
                self._last_add_at = now
                added_this_poll += 1
            self._seen_rooms[share.room_id] = now
            self._prune_seen(now)
            if added_this_poll >= MAX_ADDS_PER_POLL:
                break
        # 游标推进到本批最大 server_id；即使一批里没有分享，也不会重复解析旧消息。
        self._last_server_ids[conversation.conversation_id] = latest_server_id

    # 把分享解析成直播间地址并写配置；返回 ADDED 或跳过原因。
    # 分享 content 只有 room_owner_sec_id 与内部 room_id，没有 web_rid；必须经 IM 用户接口
    # 换成 unique_id 才能拼出 live.douyin.com/<抖音号>，这是整条链路的关键映射。
    def _resolve_and_add(self, client: DouyinDmClient, share: LiveShare, settings: _DmSettings) -> str:
        try:
            users = client.get_user_info([share.owner_sec_uid])
        except DmApiError as error:
            self._log_skip(share, f"user_{error}")
            return WRITE_FAILED
        user = users.get(share.owner_sec_uid) or {}
        unique_id = str(user.get("unique_id") or user.get("short_id") or "").strip()
        url = live_url_for(unique_id)
        if url is None:
            self._log_skip(share, "no_unique_id")
            return INVALID
        anchor = str(user.get("nickname") or share.owner_name or unique_id).strip()
        if settings.require_live:
            try:
                room = client.get_live_room(unique_id)
            except DmApiError as error:
                self._log_skip(share, f"live_{error}")
                return WRITE_FAILED
            # 仅在「仅在播才添加」开启时校验：默认关闭是为了让分享后即使主播已下播，
            # 也能先关注、等下次开播再录；开启则避免配置里多出每轮空转的离线房间。
            # room_id 不一致说明该抖音号已换到另一个直播间，宁可跳过也不录错房间。
            if room is None or not room.get("is_live"):
                self._log_skip(share, "offline", url)
                return EXISTS_DISABLED
            if room.get("room_id") and share.room_id and room["room_id"] != share.room_id:
                self._log_skip(share, "room_changed", url)
                return INVALID
            anchor = str(room.get("anchor") or anchor).strip()
        try:
            result = self._add_room(url, settings.quality, anchor)
        except Exception as error:
            self._log_skip(share, f"add_{type(error).__name__}", url)
            return WRITE_FAILED
        if result == ADDED:
            logger.info(i18n.tr("[私信自动录制]已添加直播间: {url}", url=url))
        else:
            self._log_skip(share, result, url)
        return result

    # 统一跳过日志：只带主播名/地址/机器码，绝不带消息正文或 Cookie。
    def _log_skip(self, share: LiveShare, reason: str, url: str = "") -> None:
        logger.info(
            i18n.tr(
                "[私信自动录制]跳过 {anchor}（{reason}）{url}",
                anchor=share.owner_name or share.room_id,
                reason=reason,
                url=url,
            )
        )

    # 清理过期去重项，避免长期运行时字典无限增长。
    def _prune_seen(self, now: float) -> None:
        expired = [room for room, seen_at in self._seen_rooms.items() if now - seen_at > SHARE_TTL_SECONDS]
        for room in expired:
            self._seen_rooms.pop(room, None)
