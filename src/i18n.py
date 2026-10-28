# -*- coding: utf-8 -*-
# 国际化：中文直接使用源码原文，英文读取 i18n/en_US.json，无需编译。
#
# 运行时切换：set_language(lang) 热替换翻译函数（_tr），后续 print/logger 输出
# 即时使用新语言，无需重启进程（Web 面板 的即时切换语言功能依赖此入口）。
#
# 语言解析：resolve_language(value) 为配置键 language 的统一解析入口——
# 未配置、不可识别或英文目录缺失时使用中文。

import builtins
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

# 支持的语言（有序；键为规范语言码，值为界面显示名，供 Web 语言选择器使用）
SUPPORTED_LANGUAGES: dict[str, str] = {
    "zh_CN": "中文",
    "en_US": "English",
}

# 默认语言（源码输出以中文为主、部分英文常量串，缺失翻译时回退恒等映射）
DEFAULT_LANGUAGE = "zh_CN"

# 配置无效或英文目录不可用时，回退到源码原文。
FALLBACK_LANGUAGE = DEFAULT_LANGUAGE

# 语言别名归一化表：配置文件/浏览器/历史键值里的各种写法 → 规范语言码。
# 键统一为「小写 + 连字符」形态（normalize_language 会把下划线归一为连字符后查表）
_LANGUAGE_ALIASES: dict[str, str] = {
    "zh-cn": "zh_CN",
    "zh": "zh_CN",
    "zh-hans": "zh_CN",
    "zh-sg": "zh_CN",
    "en": "en_US",
    "en-us": "en_US",
    "en-gb": "en_US",
    "zh-tw": "zh_CN",
    "zh-hant": "zh_CN",
    "zh-hk": "zh_CN",
    "zh-mo": "zh_CN",
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


# 中文无需目录；英文目录缺失或损坏时不得回报虚假的切换成功。
def has_catalog(lang: str) -> bool:
    return lang == DEFAULT_LANGUAGE or bool(_load_translations(locale_path, lang))


def resolve_language(value: str | None) -> str:
    language = normalize_language(value)
    return language if has_catalog(language) else DEFAULT_LANGUAGE


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


# 中文原文无需加载翻译文件。
def _load_translations(locale_dir: str | Path, lang: str) -> dict[str, str] | None:
    if lang == DEFAULT_LANGUAGE:
        return None
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


# 返回实际生效的语言，供调用方写回配置。
def set_language(lang: str | None) -> str:
    global _current_language, _tr
    effective = resolve_language(lang)
    _current_language = effective
    _tr = _build_translator(locale_path, effective)
    return effective


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
