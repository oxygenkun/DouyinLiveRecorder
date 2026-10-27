# AGENTS.md「已知坑（避免回归）」索引 ↔ docs/agent-reference/known-pitfalls.md 标题的一致性锁。
#
# 治理症状：细则外迁后，根文件索引与明细标题变成两份手工维护的清单；任一侧改名、增删而另一侧
# 不动，根文件就会指向不存在的主题（或新主题在根文件里根本看不见），且没有任何检查会红。
# 判据：索引条目顺序与明细 `##` 标题逐字相等；任一侧为空即失败（防「两边都空」的假绿）。

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
DETAIL = ROOT / "docs" / "agent-reference" / "known-pitfalls.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少文档：{path}"
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _index_topics() -> list[str]:
    # 索引节是根文件里以 `## 已知坑（避免回归）` 开头、到下一个 `## ` 或文末为止的 `- ` 条目
    text = _read(AGENTS)
    marker = "## 已知坑（避免回归）"
    assert marker in text, f"根文件缺少「{marker[3:]}」索引节"
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    return [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]


def _detail_headings() -> list[str]:
    return [line[3:].strip() for line in _read(DETAIL).splitlines() if line.startswith("## ")]


def test_pitfall_index_matches_detail_headings() -> None:
    topics = _index_topics()
    headings = _detail_headings()
    assert topics, "根文件「已知坑」索引不得为空"
    assert headings, "明细文档不得没有主题小节"
    assert topics == headings, (
        "AGENTS.md「已知坑」索引与 known-pitfalls.md 标题不一致；新增、改名或删除主题须同批同步两侧"
    )
