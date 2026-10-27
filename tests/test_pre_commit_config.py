# pre-commit 配置中的 Ruff 与仓库检查入口。

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent

# 提交时使用的仓库级检查脚本（匹配口径取脚本相对路径子串：
# entry 可再携带参数，只要脚本本身被调用即视为覆盖，不钉参数顺序与字符串完全相等）。
REPO_LEVEL_GATE_SCRIPTS: tuple[str, ...] = (
    "scripts/compile_po.py",
    "scripts/check_version.py",
    "scripts/check_skill_agents_consistency.py",
)


def _load_pre_commit_config() -> dict[str, Any]:
    cfg_path = ROOT / ".pre-commit-config.yaml"
    assert cfg_path.exists(), f"缺少 {cfg_path}"
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), "pre-commit 配置文件顶层必须是 mapping"
    return cast("dict[str, Any]", loaded)


def _iter_hooks(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    # 摊平所有 repo 下的 hooks；调用方按 repo/entry/id 自行筛选。
    hooks: list[dict[str, Any]] = []
    for repo in cfg.get("repos", []):
        for hook in repo.get("hooks", []) or []:
            hook_with_repo = dict(hook)
            hook_with_repo["__repo__"] = repo.get("repo")
            hooks.append(hook_with_repo)
    return hooks


def _local_entries(cfg: dict[str, Any]) -> list[str]:
    # 返回 repo: local 下所有 hook 的 entry 字符串。
    out: list[str] = []
    for hook in _iter_hooks(cfg):
        if hook.get("__repo__") != "local":
            continue
        entry = hook.get("entry")
        if isinstance(entry, str):
            out.append(entry)
    return out


# ───────────────────────── 核心：pre-commit 必须覆盖上述每一条 ─────────────────────────


def test_pre_commit_local_hooks_cover_repo_level_gates() -> None:
    # 核心回归锁：三份仓库级检查脚本必须在 local hook 的 entry 里出现（子串匹配，允许额外参数）。
    # 缺失任一条 = 本次修复回归，CI 与本地检查再次脱钩。
    entries = _local_entries(_load_pre_commit_config())
    missing = [s for s in REPO_LEVEL_GATE_SCRIPTS if not any(s in e for e in entries)]
    assert not missing, f"pre-commit 未覆盖仓库级检查脚本：{missing}"


def test_pre_commit_local_hooks_cover_ruff_tools() -> None:
    # 本地 hook 消费 uv.lock，不再使用独立 mirror 环境的版本。
    hooks = {hook["id"]: hook for hook in _iter_hooks(_load_pre_commit_config())}
    assert not hooks.keys() & {"black", "isort", "mypy", "basedpyright", "pyright"}
    for name, tool in (("ruff-check", "ruff check --fix"), ("ruff-format", "ruff format")):
        assert hooks[name]["__repo__"] == "local"
        assert hooks[name]["entry"] == f"uv run --locked --extra dev {tool}"


# ───────────────────────── 结构不变量：三个 hook 的控制位必须一致 ─────────────────────────


def test_repo_level_gate_hooks_share_system_pass_filenames_false_always_run() -> None:
    # 结构不变量：三份仓库级检查 hook 必须 language=system + pass_filenames=false + always_run=true。
    # 三个脚本按仓库根遍历做跨文件比对（翻译、版本号和技能目录表），
    # 若允许 pass_filenames 会把 staged 文件当参数传入而破坏自身遍历；若非 always_run 则改 .po /
    # build_exe.py / pyproject.toml 时不触发，等于回到本次修复前的症状（语义细则见配置文件内注释）。
    cfg = _load_pre_commit_config()
    matched = 0
    for hook in _iter_hooks(cfg):
        if hook.get("__repo__") != "local":
            continue
        entry = hook.get("entry", "")
        if not isinstance(entry, str):
            continue
        if not any(s in entry for s in REPO_LEVEL_GATE_SCRIPTS):
            continue
        matched += 1
        hook_id = hook.get("id")
        assert hook.get("language") == "system", f"hook {hook_id!r} 应为 language=system（消费 venv 已装包）"
        assert hook.get("pass_filenames") is False, f"hook {hook_id!r} 必须 pass_filenames: false"
        assert hook.get("always_run") is True, (
            f"hook {hook_id!r} 必须 always_run: true（跨文件比对不能被 staged 类型漏掉）"
        )
    assert matched == len(REPO_LEVEL_GATE_SCRIPTS), (
        f"pre-commit 应命中 {len(REPO_LEVEL_GATE_SCRIPTS)} 条仓库级检查 hook，实际 {matched} 条"
    )
