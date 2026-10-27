# 执行 pytest 的 CI job 必须通过 uv 锁定安装基础依赖和开发工具。

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_FILE = ROOT / ".github" / "workflows" / "ci.yml"

# 独立 token 形态的 pytest：既排除 `pytest_version=9.1.1`（setup job 的 echo，后随 `_`），
# 也排除 `pytest-cov` / `pytest-asyncio`（后随 `-`）——两者都不是「跑/装 pytest」本身。
# 装上 `pytest==9.1.1` 的 typecheck job 会被认出（后随 `=` 不在排除集内），这是有意的：
# 那条 job 也是靠 requirements.txt 兜底的，纳入判定面只会让锁更紧而非更松。
_PYTEST_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.\-])pytest(?![A-Za-z0-9_\-])")

# 运行时依赖安装锚点：uv sync --locked 必须安装基础依赖，不能只装工具组。
# [历史注 2026-10-23] 从 pip requirements 迁移；extra 同步会连同基础依赖一起安装。
_REQUIREMENTS_INSTALL_RE = re.compile(r"uv sync --locked\b(?![^\n|&;]*--(?:only|no-install)-)")

# 已知「真跑 pytest」的 job。用「必须是检测结果的子集」而不是相等：
# 新增 pytest job 属放宽，不该弄红；**减少**（把某条 job 改成不再跑 pytest，或检测器退化）
# 会让覆盖面悄悄缩水，必须拦。
_KNOWN_PYTEST_JOBS = ("test", "concurrency-test")

# 检测器精度守卫：这些 job 不得被认成 pytest job。`setup` 是最关键的一格——它只 echo 版本常量
# （`pytest_version=9.1.1`），裸子串匹配会命中它，于是正向判据会被一个从不跑 pytest 的 job 稀释。
_KNOWN_NON_PYTEST_JOBS = ("setup", "static", "deps-audit", "integration-verify", "build-verify")


def _ci_doc() -> dict[str, Any]:
    assert CI_FILE.is_file(), f"缺少 CI 工作流定义：{CI_FILE}"
    doc = yaml.safe_load(CI_FILE.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "ci.yml 必须解析为映射"
    return cast("dict[str, Any]", doc)


def _jobs() -> dict[str, dict[str, Any]]:
    # 按结构取 jobs。空 jobs 一律显式失败：扫不到东西的绿灯等于锁被掏空还显示通过。
    doc = _ci_doc()
    jobs = doc.get("jobs") or {}
    assert isinstance(jobs, dict), "ci.yml 的 jobs 必须是映射"
    typed = cast("dict[str, dict[str, Any]]", jobs)
    assert typed, "ci.yml 的 jobs 不得为空（解析失败或结构漂移）"
    return typed


def _strip_comment_lines(text: str) -> str:
    # 只保留**会被执行**的行：bash 块里的 `#` 注释同样会出现 pytest 字样（setup job 的常量
    # 步骤就有一整段 `# pytest 同属此类…`），带注释一起判等于把「提到」当成「执行」——
    # 判据随即被一个从不跑 pytest 的 job 稀释。行首（去空白后）为 # 的行一律丢掉。
    kept = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(kept)


def _step_texts(job: dict[str, Any]) -> list[str]:
    # 一条 job 的「可判定文本」= 每个 step 的 run 脚本体 + retry 复合动作的 with.command。
    # 必须同时看两处：网络安装统一走 ./.github/actions/retry（AGENTS「CI / workflow 约定」），
    # 只看 run 会漏掉全部经该动作执行的 pip install。
    # 刻意**不**收 step.name / with.label：它们是给人看的文案，写「requirements + pytest」
    # 只是描述，不代表真跑 pytest。
    steps = job.get("steps") or []
    if not isinstance(steps, list):
        # 调用他人 workflow 的 job 没有 steps，跳过而不是抛 AttributeError 掩盖真判据。
        return []
    texts: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run_body = step.get("run")
        if isinstance(run_body, str):
            texts.append(_strip_comment_lines(run_body))
        with_map = step.get("with")
        if isinstance(with_map, dict):
            command = with_map.get("command")
            if isinstance(command, str):
                texts.append(_strip_comment_lines(command))
    return texts


def _pytest_jobs() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, job in _jobs().items():
        texts = _step_texts(job)
        if any(_PYTEST_TOKEN_RE.search(text) for text in texts):
            found[name] = texts
    return found


def test_every_pytest_job_installs_runtime_requirements() -> None:
    found = _pytest_jobs()
    assert found, "未检测到任何 pytest job（ci.yml 结构漂移或检测器失效，不得静默放行）"
    offenders = [name for name, texts in found.items() if not any(_REQUIREMENTS_INSTALL_RE.search(t) for t in texts)]
    assert not offenders, (
        "以下 job 会执行/安装 pytest，却没通过 uv 锁定安装基础依赖："
        f"{sorted(offenders)}。tests/conftest.py 的 autouse fixture 会 import src 与 i18n"
        "（分别拉起 distro / loguru 等运行时依赖），只装 pytest 会让全部用例在 setup 阶段"
        " ModuleNotFoundError（缺少运行时依赖的实测形态）"
    )


def test_known_pytest_jobs_are_detected() -> None:
    found = _pytest_jobs()
    missing = [name for name in _KNOWN_PYTEST_JOBS if name not in found]
    assert not missing, f"检测器漏掉了已知 pytest job {missing}（正则或结构解析失效，正向判据随之失真）"


@pytest.mark.parametrize("job_name", _KNOWN_NON_PYTEST_JOBS)
def test_setup_job_is_not_mistaken_for_a_pytest_job(job_name: str) -> None:
    # 反向钉：这些 job 不该被检测器认成 pytest job。setup 是最关键的一格——它只有
    # `echo "pytest_version=9.1.1"` 这类版本常量字符串，裸子串匹配会命中它，
    # 于是「pytest job 必须装 requirements」这条判据会被一个从不跑 pytest 的 job 稀释。
    assert job_name not in _pytest_jobs(), f"{job_name} 不含 pytest 执行/安装，检测器不应命中它"
