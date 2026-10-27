#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# DouyinLiveRecorder 核心包：初始化运行时环境（PATH / Node.js）并导出弹幕录制公共 API。

# Author: Hmily
# GitHub: https://github.com/ihmily
# Date: 2023-2025

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

JS_SCRIPT_PATH = Path(__file__).resolve().parent / "javascript"


def initialize_runtime() -> None:
    # 入口完成编码和日志模式设置后再检查运行时，包导入本身不启动子进程。
    from .logger import script_path
    from .node_install import check_node

    node_dir = Path(script_path) / "node"
    env_path = os.environ.get("PATH", "")
    if node_dir.is_dir() and str(node_dir) not in env_path.split(os.pathsep):
        os.environ["PATH"] = str(node_dir) + os.pathsep + env_path
    if os.environ.get("DOUYIN_SKIP_RUNTIME_CHECK", "").strip().lower() not in ("1", "true", "yes"):
        check_node()


# ---------------------------------------------------------------------------
# 弹幕录制公共 API：平台名 → 弹幕类注册表 + 两个工厂函数
# ---------------------------------------------------------------------------
# 移植自 dart_simple_live，为录制工具提供与录像同步、按半小时分片的 SRT 弹幕录制能力。

if TYPE_CHECKING:
    from src.base import DanmakuBase
    from src.collector import DanmakuCollector


# 平台名与 main.py 中的 platform 标识一致。注册表在函数内惰性构建，避免 `import src`
# 即拉起 websockets / protobuf 等重依赖。
def get_danmaku_class(platform: str) -> Optional[type[DanmakuBase]]:
    from src.platforms.bilibili import BilibiliDanmaku
    from src.platforms.douyin import DouyinDanmaku
    from src.platforms.douyu import DouyuDanmaku
    from src.platforms.huya import HuyaDanmaku
    from src.platforms.twitch import TwitchDanmaku

    _DANMAKU_REGISTRY = {
        "斗鱼直播": DouyuDanmaku,
        "B站直播": BilibiliDanmaku,
        "虎牙直播": HuyaDanmaku,
        "抖音直播": DouyinDanmaku,
        "TwitchTV": TwitchDanmaku,
    }
    return _DANMAKU_REGISTRY.get(platform)


# 工厂函数：平台不支持或 danmaku_args 为空时返回 None（导入放在函数内，避免循环依赖）。
def get_danmaku_collector(
    platform: str,
    danmaku_args: Any,
    base_filename: str,
    segment_seconds: Optional[float] = 1800.0,
    # CR-04：默认 None 而非 True——True 会顶掉平台类自己刻意设的默认值（斗鱼为 False），
    # 让该平台的普通弹幕被静默丢弃且无日志；None 表示调用方未指定、不覆盖平台默认。
    only_fans: Optional[bool] = None,
    room_name: Optional[str] = None,
    write_srt: bool = True,
) -> Optional["DanmakuCollector"]:
    from src.collector import DanmakuCollector

    cls = get_danmaku_class(platform)
    if cls is None:
        return None
    if not danmaku_args:
        return None
    return DanmakuCollector(
        danmaku_cls=cls,
        danmaku_args=danmaku_args,
        base_filename=base_filename,
        segment_seconds=segment_seconds,
        only_fans=only_fans,
        room_name=room_name,
        platform_name=platform,
        write_srt=write_srt,
    )
