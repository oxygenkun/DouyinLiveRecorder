"""源码目录迁移后的入口顺序和运行时资源定位。"""

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import src
from src import logger as logger_module
from src import node_install

ROOT = Path(__file__).resolve().parent.parent


def test_package_import_leaves_entry_initialization_to_entry(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import src; "
            "assert 'src.logger' not in sys.modules; assert 'src.node_install' not in sys.modules",
            str(ROOT),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("frozen", [False, True])
def test_source_and_frozen_entries_keep_resources_at_app_root(monkeypatch: pytest.MonkeyPatch, frozen: bool) -> None:
    process = types.SimpleNamespace(**vars(sys))
    process.argv = [str(ROOT / "src" / "main.py")]
    process.executable = str(ROOT / "DouyinLiveRecorder")
    process.frozen = frozen
    monkeypatch.setattr(logger_module, "sys", process)
    assert Path(logger_module._app_root()) == ROOT


@pytest.mark.parametrize("skip, expected_calls", [("1", []), ("", [True])])
def test_runtime_initialization_respects_skip_and_bundled_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skip: str, expected_calls: list[bool]
) -> None:
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    monkeypatch.setattr(logger_module, "script_path", str(tmp_path))
    calls: list[bool] = []
    monkeypatch.setattr(node_install, "check_node", lambda: calls.append(True))
    monkeypatch.setenv("DOUYIN_SKIP_RUNTIME_CHECK", skip)
    monkeypatch.setenv("PATH", str(node_dir))
    src.initialize_runtime()
    assert calls == expected_calls
    assert os.environ["PATH"] == str(node_dir)
