# Docker 只承载 CLI / Web，桌面窗口属于宿主机可选功能。
# 基础依赖和镜像构建都不得隐式选择 gui extra。
# .dockerignore 同时排除桌面入口与窗口层，防止依赖精简后仍发布不可用的桌面入口。
# compose 必须只有 recorder / web，防止把 X11 挂载和 GUI 构建重新引入部署流程。
# uv.lock 必须进入构建上下文，才能保证镜像消费的是提交过的解析结果。
# [历史注 2026-10-23] INSTALL_GUI 与 gui profile 已按用户要求移除，文件名沿用旧回归锁入口。

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"


def test_image_installs_locked_base_dependencies_only() -> None:
    # 只看可执行指令，历史说明里的包名不应影响安装契约。
    text = DOCKERFILE.read_text(encoding="utf-8")
    instructions = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "uv sync --locked --no-dev --no-install-project" in instructions
    assert "COPY pyproject.toml uv.lock ./" in instructions
    assert "--extra" not in instructions
    assert "INSTALL_GUI" not in instructions
    assert "PySide6" not in instructions
    assert "libxcb" not in instructions
    assert "libegl1" not in instructions


def test_gui_package_is_optional_for_host_installation() -> None:
    # 桌面可安装，与容器永远无 GUI 是两个独立契约。
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    base = {re.split(r"[<>=!~;\[]", spec)[0].lower() for spec in project["dependencies"]}
    gui = {re.split(r"[<>=!~;\[]", spec)[0].lower() for spec in project["optional-dependencies"]["gui"]}
    assert "pyside6" not in base
    assert "pyside6" in gui


def test_compose_only_defines_cli_and_web_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"recorder", "web"}
    for service in compose["services"].values():
        assert "INSTALL_GUI" not in service["build"].get("args", {})


def test_docker_context_contains_lock_but_excludes_desktop_code() -> None:
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "uv.lock" not in patterns
    assert "src/gui.py" in patterns
    assert "src/gui_qt/" in patterns


def test_published_image_workflow_has_no_gui_option() -> None:
    text = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    assert "INSTALL_GUI" not in text
    assert "--extra gui" not in text
