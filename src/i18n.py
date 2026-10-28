# -*- coding: utf-8 -*-
# 国际化：直接读取 i18n/<lang>.json 中的「原文 → 译文」映射，无需编译。
#
# 运行时切换：set_language(lang) 热替换翻译函数（_tr），后续 print/logger 输出
# 即时使用新语言，无需重启进程（Web 面板 的即时切换语言功能依赖此入口）。
#
# 语言解析：resolve_language(value) 为配置键 language 的统一解析入口——
# 空 → 系统语言（detect_system_language）；不可识别或语言目录文件缺失 → en_US 回退。

import builtins
import inspect
import json
import locale
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

# 支持的语言（有序；键为规范语言码，值为界面显示名，供 Web 语言选择器使用）
SUPPORTED_LANGUAGES: dict[str, str] = {
    "zh_CN": "简体中文 (Simplified Chinese)",
    "en_US": "English (US)",
    "en_GB": "English (UK)",
    "zh_TW": "繁體中文 (Traditional Chinese)",
}

# 默认语言（源码输出以中文为主、部分英文常量串，缺失翻译时回退恒等映射）
DEFAULT_LANGUAGE = "zh_CN"

# 回退语言：配置键值不可识别、或对应语言的目录文件缺失时，统一回退显示该语言
FALLBACK_LANGUAGE = "en_US"

# 语言别名归一化表：配置文件/浏览器/历史键值里的各种写法 → 规范语言码。
# 键统一为「小写 + 连字符」形态（normalize_language 会把下划线归一为连字符后查表）
_LANGUAGE_ALIASES: dict[str, str] = {
    "zh-cn": "zh_CN",
    "zh": "zh_CN",
    "zh-hans": "zh_CN",
    "zh-sg": "zh_CN",
    "en": "en_US",
    "en-us": "en_US",
    "en-gb": "en_GB",
    "zh-tw": "zh_TW",
    "zh-hant": "zh_TW",
    "zh-hk": "zh_TW",
    "zh-mo": "zh_TW",
}


# 判断语言标识是否可识别（受支持码/已知别名/带编码后缀变体）；不回退默认值
def is_recognized_language(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip()
    if v in SUPPORTED_LANGUAGES:
        return True
    low = v.lower().replace("_", "-")
    if low in _LANGUAGE_ALIASES:
        return True
    prefix = low.split(".")[0].split("@")[0]
    if prefix in _LANGUAGE_ALIASES:
        return True
    return prefix.replace("-", "_") in SUPPORTED_LANGUAGES


# 把任意语言标识归一化为受支持的规范语言码：精确匹配 → 别名表（小写 + 连字符）→
# 前缀匹配（如 zh_CN.UTF-8）→ 无法识别时回退默认语言
def normalize_language(value: str | None) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    v = value.strip()
    if v in SUPPORTED_LANGUAGES:
        return v
    low = v.lower().replace("_", "-")
    if low in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[low]
    # 前缀匹配：zh_CN.UTF-8 / en_US.UTF-8 等带编码后缀的写法
    prefix = low.split(".")[0].split("@")[0]
    if prefix in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[prefix]
    if prefix.replace("-", "_") in SUPPORTED_LANGUAGES:
        return prefix.replace("-", "_")
    return DEFAULT_LANGUAGE


# 检测系统语言，失败返回 None（不抛异常）：
#   1) 环境变量 LANGUAGE / LC_ALL / LC_MESSAGES / LANG（LANGUAGE 为冒号分隔列表，
#      取首项；C / POSIX 视为未设置）
#   2) Windows：用户默认 UI 语言（LANGID 经 locale.windows_locale 映射为 zh_CN 等代码）
#   3) POSIX：locale.getlocale()（进程已 setlocale 时有效；C / POSIX 结果同样视为未设置，
#      实测 Linux CI 的 LANG=C 进程里 getlocale() 返回 ('C', None)，不拦会原样泄漏 "C"）
def detect_system_language() -> str | None:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").strip()
        if value and value.upper() not in ("C", "POSIX"):
            return value.split(":")[0].strip() or None
    if sys.platform == "win32":
        win_lang = _windows_ui_language()
        if win_lang:
            return win_lang
    try:
        current = locale.getlocale()[0]
    except ValueError:
        current = None
    if current and current.upper() not in ("C", "POSIX"):
        return current
    return None


# Windows 用户默认 UI 语言代码（如 zh_CN / en_US）；非 Windows 或调用失败返回 None
def _windows_ui_language() -> str | None:
    # 平台门控放函数体首行：WinDLL 仅存在于 Windows typeshed，裸引用会让 mypy 在
    # 非 win32 平台（CI 的 linux runner）报 attr-defined（通过平台判断门控）
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        # WinDLL 而非 windll：与 web.py 的 Windows API 调用惯例一致（显式声明返回类型）
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetUserDefaultUILanguage.restype = ctypes.c_uint16
        lang_id = int(kernel32.GetUserDefaultUILanguage())
    except Exception:
        return None
    return locale.windows_locale.get(lang_id)


# 目录必须能加载出非空映射，避免损坏或漏装时回报虚假的语言切换成功。
def has_catalog(lang: str) -> bool:
    return bool(_load_translations(locale_path, lang))


# 把配置语言键值解析为最终显示语言（CLI/Web 启动初始化与热切换的统一入口）：
#   空 / 缺失         → 系统语言（检测结果不可用或无对应目录时回退 FALLBACK_LANGUAGE）
#   可识别且有目录    → 该语言（别名归一化，如 zh-cn → zh_CN）
#   不可识别 / 无目录 → FALLBACK_LANGUAGE（en_US）
def resolve_language(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        system_lang = detect_system_language()
        if system_lang and is_recognized_language(system_lang):
            normalized = normalize_language(system_lang)
            if has_catalog(normalized):
                return normalized
        return FALLBACK_LANGUAGE
    if not is_recognized_language(raw):
        return FALLBACK_LANGUAGE
    normalized = normalize_language(raw)
    return normalized if has_catalog(normalized) else FALLBACK_LANGUAGE


# 检测执行目录，支持打包后 (_internal) 和源码两种运行方式
# 优先基于本模块文件所在目录定位，避免 sys.argv[0] 在打包/-m 运行时被误解析；
# 冻结运行时模块可能位于 PYZ 中，__file__ 指向 PYZ 目录，需回退到 sys._MEIPASS。
module_dir = Path(__file__).resolve().parent.parent
_meipass = getattr(sys, "_MEIPASS", None)
if os.path.exists(module_dir / "_internal/i18n"):
    locale_path = module_dir / "_internal/i18n"  # PyInstaller 打包版位置
elif _meipass and os.path.exists(Path(_meipass) / "i18n"):
    locale_path = Path(_meipass) / "i18n"  # 冻结运行 data 目录
else:
    locale_path = module_dir / "i18n"  # 源码运行位置

# 需要翻译的源码目录：src/ 包以及项目根（main.py 等顶层脚本）
# 统一规范化路径分隔符，兼容 Windows 下 sys._getframe 返回 / 而 os.path.realpath 返回 \ 的情况
_project_root = os.path.normpath(str(module_dir))


# 判断调用者文件是否位于需要翻译的项目源码目录下。
def _should_translate(caller_file: str) -> bool:
    caller_norm = os.path.normpath(caller_file)
    # 在项目根目录下即为项目源码（含 src/ 及 main.py/web.py 等）。
    # L-03（2026-10-18 审查）：前缀比对必须带路径分隔符边界——裸 startswith 会把「与项目根同名前缀的
    # 兄弟目录」（根为 /proj 时的 /proj-evil/xxx.py）判成本项目源码，第三方目录里的 print 也被翻译；
    # 判定语义是「谁在本项目内」，不是「谁像本项目」。归一后完全相等（把项目根当文件传进来的调用方）
    # 仍算在内，不得收紧成「只认严格子路径」而漏掉相等情形。
    return caller_norm == _project_root or caller_norm.startswith(_project_root + os.sep)


# 从 JSON 文件加载「原文 → 译文」映射；文件不存在或解析失败时返回 None
def _load_json_catalog(path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    # 仅保留 str→str 条目，其余类型跳过（容忍手工编辑出的杂项值）
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


# 所有语言统一使用 JSON 目录。
def _load_translations(locale_dir: str | Path, lang: str) -> dict[str, str] | None:
    return _load_json_catalog(Path(locale_dir) / f"{lang}.json")


# 构建某语言的翻译函数：加载翻译目录，命中返回译文、未命中回退原文
def _build_translator(locale_dir: str | Path, lang: str) -> Callable[[str], str]:
    catalog = _load_translations(locale_dir, lang)
    if not catalog:
        return lambda text: text
    return lambda text: catalog.get(text, text)


# 当前语言（模块级状态；set_language 热切换）
_current_language: str = DEFAULT_LANGUAGE

# 当前翻译函数：所有输出翻译统一经此引用（translated_print 与外部按需取用）
_tr: Callable[[str], str] = _build_translator(locale_path, DEFAULT_LANGUAGE)
original_print = builtins.print  # 保存原始 print 函数


# 切换当前语言：归一化 → 目录可用性判定 → 加载翻译目录 → 热替换 _tr。
# 返回**实际生效**的语言码（调用方据此写配置 / 提示用户），而不是布尔「请求是否合法」。
# MID-52：目录缺失或损坏时（如发行包漏装 en_GB.json）按 resolve_language
# 的口径回退 FALLBACK_LANGUAGE，保证 _current_language 与实际装载的目录**永远一致**——旧实现回报
# 请求码，「语言已切换」是假的，且该码写回 config.ini 后录制子进程经 resolve_language 落到 en_US：
# 同一部署两种语言。注意：本函数刻意不做系统语言探测（那是 resolve_language 的职责，CLI/Web 入口
# 已先调用），否则「set_language('') 应为默认语言」的既有语义会被宿主环境改掉。
def set_language(lang: str | None) -> str:
    global _current_language, _tr
    effective = _effective_language(lang)
    _current_language = effective
    _tr = _build_translator(locale_path, effective)
    return effective


# 把「请求的语言」折算为「有目录可加载的生效语言」；候选按优先级排列。
# 全部候选都没有目录（理论上只出现在「整个 i18n 目录没装好」的畸形发行包）时返回 FALLBACK_LANGUAGE
# ——与 resolve_language 在同一情形下的答案一致，这样 Web 与录制子进程至少报同一个码（译文都是
# 恒等映射，不再有二义）。「有目录」的判据即 has_catalog（MID-2248 收紧为「真的装载得出非空映射」）。
def _effective_language(lang: str | None) -> str:
    raw = (lang or "").strip()
    if not raw:
        candidates: list[str] = [DEFAULT_LANGUAGE, FALLBACK_LANGUAGE]
    elif not is_recognized_language(raw):
        candidates = [FALLBACK_LANGUAGE, DEFAULT_LANGUAGE]
    else:
        candidates = [normalize_language(raw), FALLBACK_LANGUAGE, DEFAULT_LANGUAGE]
    for code in candidates:
        if has_catalog(code):
            return code
    return FALLBACK_LANGUAGE


# 返回当前语言规范码
def get_language() -> str:
    return _current_language


# 返回受支持的语言映射（键为语言码、值为显示名），供选择器渲染
def available_languages() -> dict[str, str]:
    return dict(SUPPORTED_LANGUAGES)


# 包装 print：对来自源码目录的输出自动翻译后再打印。
def translated_print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    file: TextIO | None = None,
    flush: bool = False,
) -> None:
    try:
        frame = inspect.currentframe()
        caller_file = frame.f_back.f_code.co_filename if frame and frame.f_back else ""
        should_translate = _should_translate(caller_file)
    except ValueError, AttributeError:
        should_translate = False

    translated_args: list[str] = []
    for arg in args:
        text = str(arg)
        if should_translate:
            text = _tr(text)  # 翻译文本
        translated_args.append(text)

    original_print(sep.join(translated_args), sep=sep, end=end, file=file, flush=flush)  # 调用原始 print


# 带占位符的翻译入口（先查表再插值）：
# 调用方传「原文模板（必须是字面量常量串，与目录键严格一致）+ 字段值」，
# 避免 f-string 在查表前先把模板替换为已插值字符串、
# 导致目录里 [{record_name}] 这类 msgid 永远查不到、翻译静默退化为原文。
# 翻译命中时按 .format(**kwargs) 二次插值；未命中则直接对原文格式化为最终输出。
# 表达式类占位符（如 {type(e).__name__}）调用方须提前求值为局部变量后再传入。
def tr(template: str, **kwargs: Any) -> str:
    # MI-23：译文是**外部可编辑数据**（JSON 由译者维护），占位符写错（把 {e} 写成 {err}）或含
    # 未转义的花括号都会抛 KeyError/IndexError/ValueError；而 tr() 的调用点大量位于 except 分支内，
    # 二次异常会顶掉原始异常，把「网络失败」升级成崩溃、掩盖真实故障（zh_CN 常为恒等映射，问题只在
    # 切换语言后暴露）。故本函数永不抛：格式化失败降级为原文模板，再失败返回裸模板。
    # M-19（2026-09-29）：上面的异常清单只覆盖「占位符名写错 / 花括号未转义」，漏掉了**值侧**两类失败
    # ——译文里合法的 `{x.y}` 在 x=None 时抛 AttributeError、`{x:d}` 在 x=None 时抛 TypeError
    # （Python 3.14.7 实测两者均穿透本函数）。触发条件落在既有风险面内：except 分支里 `{e}` 的实参
    # 为空对象是常态。故两层 except 一并扩入 AttributeError/TypeError，降级语义不变（译文失败退原文、
    # 原文也退不动就返回裸模板）；不额外发日志——tr 本就在 except 分支里被调，再打日志会把真实故障
    # 的告警淹成格式化告警。
    translated = _tr(template)
    try:
        return translated.format(**kwargs)
    except KeyError, IndexError, ValueError, AttributeError, TypeError:
        try:
            return template.format(**kwargs)
        except KeyError, IndexError, ValueError, AttributeError, TypeError:
            return template
