# scripts/run_gates.py 的回归用例。
#
# 覆盖三类必须锁住的行为：
#   1) 检查清单从 AGENTS.md「格式化命令」章节解析（唯一事实源，不得漂移成脚本内清单）；
#   2) 写型 black/isort 的兜底拦截判定（含 `--profile black` 参数值误报的历史坑）；
#   3) 回路安全不变量：--list 输出的每一条命令都必须是 --check 型，且清单缺失/被污染时
#      以非 0 退出——「检查没跑」绝不能被报告成「检查通过」。
#
# 全部离线：不执行任何真实检查命令。
# 现被测面 = 解析/判定纯函数 + run_command 的真实子进程行为 + ensure_utf8_streams 的崩溃恢复
# 分支。仍不跑任何**仓库检查**：run_command 的用例一律驱动 tmp_path 下的一次性子进程脚本，
# 既快又与本机装了什么工具无关。
# [2026-09-23 扩展] gate_pytest_warnings_summary 的 fail-closed 判据（SEV-2219）：
#   - gate_pytest_warnings_summary 的三条 fail-closed 判据（rc 参与判定、stderr 参与回路、
#     「N passed > 0」见证）——桩化 subprocess.run，绝不真跑 pytest；

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import types
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_run_gates() -> ModuleType:
    # 沿用仓内惯例（test_i18n.py / test_i18n_migration.py）：scripts/ 不在包路径内，
    # 用 spec_from_file_location 显式加载，避免 sys.path 注入被 isort 重排到 import 之前
    spec = importlib.util.spec_from_file_location("_run_gates_probe", ROOT / "scripts" / "run_gates.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_gates = _load_run_gates()

# 「格式化命令」章节 bash 代码块声明的检查条数：Ruff 两项与仓库脚本四项。
# 断言用 >=：章节将来增行属放宽，不应弄红本用例；减少则说明清单被截断，必须拦。
MIN_GATE_COMMANDS = 6


def test_extract_matches_agents_section() -> None:
    # 真源解析：首条必须是 black 的逐字检查命令（含显式传参），证明清单来自章节而非脚本内复制
    cmds = run_gates.extract_gate_commands(ROOT / "AGENTS.md")
    assert len(cmds) >= MIN_GATE_COMMANDS
    assert cmds[0] == "python -m ruff format --check --diff ."
    assert any(c.startswith("python -m ruff check --no-fix") for c in cmds)
    # 行尾注释必须被剥掉（cmd.exe 不识别 #，保留会把注释当参数）
    assert all("#" not in c for c in cmds)
    # 围栏外的行绝不许混入：「isort 收尾清理」小节的 Remove-Item 也是 bash 围栏之外的内容
    assert not any("Remove-Item" in c or "find ." in c for c in cmds)


def test_extract_missing_section_returns_empty() -> None:
    # 章节缺失 → 空清单，由 main() 判 rc=2；解析函数自身不得伪造命令
    missing = Path("AGENTS-no-such-file.md")
    assert run_gates.extract_gate_commands(missing) == []


def test_extract_empty_when_section_renamed(tmp_path: Path) -> None:
    # 标题被改名（如重构文档时手滑）视同缺源：宁可报「没跑」也不返回旧清单
    fake = tmp_path / "AGENTS.md"
    fake.write_text("## 别的章节\n\n```bash\npython -m ruff format --check .\n```\n", encoding="utf-8")
    assert run_gates.extract_gate_commands(fake) == []


@pytest.mark.parametrize(
    ("cmd", "expect_write"),
    [
        ("python -m ruff format --check .", False),
        ("ruff format --diff .", False),
        ("ruff format .", True),
        ("python -m ruff check --no-fix .", False),
        ("ruff check --fix .", True),
        ("ruff check --fix-only .", True),
        ("python scripts/check_version.py", False),
    ],
)
def test_is_write_command(cmd: str, expect_write: bool) -> None:
    assert run_gates.is_write_command(cmd) is expect_write


def test_list_mode_emits_only_check_commands() -> None:
    # 回路安全不变量：--list 打印的每条命令都不许是写型格式化命令（不得顺带改写工作区）
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_gates.py"), "--list"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) >= MIN_GATE_COMMANDS
    assert all(not run_gates.is_write_command(ln) for ln in lines)


def test_missing_source_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 清单来源缺失时 main() 必须 rc=2：检查「没跑」不得被当成「跑过且通过」
    monkeypatch.setattr(run_gates, "ROOT", tmp_path)
    assert run_gates.main(["--list"]) == 2


# ---------------------------------------------------------------------------
# run_command / ensure_utf8_streams 用例（2026-09-22 补，AGENTS.md「完成定义」点名的缺口）
#
# 一律驱动 tmp_path 下的一次性子进程脚本，不跑任何真实检查命令；被测面按 run_gates 的
# 四条回路语义组织：① 退出码原样透传（异常退出/信号死亡都必须非 0，绝不判 PASS）；
# ② stderr 边转发边扫描（不得吞输出，也不得只看 rc —— MID-63 的假绿形态）；
# ③ 子进程环境分层（GATE_CHILD_ENV 硬默认 > 父环境，extra_env 再覆盖）与解释器换绑；
# ④ 本进程输出流必须先 UTF-8 化，否则检查会因转发 emoji 而自炸（2026-09-21 实测）。
# ---------------------------------------------------------------------------


def _child_script(tmp_path: Path, name: str, body: str) -> Path:
    # 命令串刻意以 `python ` 开头，才会命中 _rebind_interpreter 的换绑分支
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return script


def _gate_cmd(script: Path) -> str:
    return f'python "{script}"'


def test_run_command_zero_rc_forwards_stderr_verbatim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _child_script(tmp_path, "ok.py", "import sys\nsys.stderr.write('gate line\\n')\n")
    rc, dur, hits = run_gates.run_command(_gate_cmd(script))
    assert rc == 0, f"干净退出被判成 {rc}"
    assert hits == []
    assert dur >= 0.0
    # 「边转发边扫描」的另一半：输出必须原样可见，不能只用于判定就吞掉
    assert "gate line" in capsys.readouterr().out


def test_run_command_propagates_nonzero_rc(tmp_path: Path) -> None:
    script = _child_script(tmp_path, "fail.py", "import sys\nsys.exit(3)\n")
    rc, _dur, hits = run_gates.run_command(_gate_cmd(script))
    assert rc != 0, f"子进程 sys.exit(3) 被吞成 rc={rc}，检查会把它报成 PASS"
    assert hits == []


def test_run_command_flags_fatal_pattern_despite_zero_rc(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # MID-63 核心不变量：isort 静默跳文件时 rc 恒 0，只认退出码即检查假绿。
    # 故意打两次同一告警，顺带锁定 hits 去重（否则一条告警会被汇总成两条失败）。
    body = (
        "import sys\n"
        "for _ in range(2):\n"
        "    sys.stderr.write('WARNING: Unable to parse file: main.py\\n')\n"
        "    sys.stderr.flush()\n"
    )
    rc, _dur, hits = run_gates.run_command(_gate_cmd(_child_script(tmp_path, "skip.py", body)))
    assert rc == 0, "用例前提失效：这条子进程应当成功退出"
    assert hits == ["Unable to parse file"], hits
    assert capsys.readouterr().out.count("Unable to parse file") == 2, "转发必须逐行原样，不得折叠/丢失"


def test_run_command_child_env_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # GATE_CHILD_ENV 是兜底硬默认：父环境把 PYTHONUTF8 设成 0 也必须被覆盖成 1（MID-63），
    # extra_env（来自检查块行首前缀）则叠加自己的键。
    monkeypatch.setenv("PYTHONUTF8", "0")
    probe = tmp_path / "env.txt"
    body = (
        "import os\n"
        f"open(r'{probe}', 'w', encoding='utf-8').write("
        "os.environ.get('PYTHONUTF8', '<unset>') + '|' + os.environ.get('DLR_GATE_PROBE', '<unset>') + '|'\n"
        "    + os.environ.get('PATH', '<unset>')[:1])\n"
    )
    rc, _dur, _hits = run_gates.run_command(_gate_cmd(_child_script(tmp_path, "env.py", body)), {"DLR_GATE_PROBE": "x"})
    assert rc == 0
    utf8, extra, path_head = probe.read_text(encoding="utf-8").split("|")
    assert utf8 == "1", "GATE_CHILD_ENV 的硬默认被父环境覆盖：isort 静默跳文件的形态回来了"
    assert extra == "x"
    assert path_head != "<", "父环境其余变量必须继续可见，不得整体替换子进程环境"


def test_run_command_rebinds_interpreter_to_current_python(tmp_path: Path) -> None:
    # 清单里的 `python` 必须换绑到**当前解释器**：否则本地检查可能跑在与 pytest 不同的
    # 解释器上（venv/py 启动器混用），正是「本地绿、CI 红」的成因之一。
    probe = tmp_path / "exe.txt"
    body = f"import sys\nopen(r'{probe}', 'w', encoding='utf-8').write(sys.executable)\n"
    rc, _dur, _hits = run_gates.run_command(_gate_cmd(_child_script(tmp_path, "exe.py", body)))
    assert rc == 0
    assert Path(probe.read_text(encoding="utf-8")).samefile(sys.executable)


def test_run_command_does_not_inherit_stdin(tmp_path: Path) -> None:
    # stdin=DEVNULL：管道/IDE harness 下父 stdin 可能已关闭，子进程 site 初始化碰它即
    # OSError WinError 6；读它也只能拿到 EOF，绝不能挂住检查。
    probe = tmp_path / "stdin.txt"
    body = f"import sys\nopen(r'{probe}', 'w', encoding='utf-8').write(repr(sys.stdin.read()))\n"
    rc, _dur, _hits = run_gates.run_command(_gate_cmd(_child_script(tmp_path, "stdin.py", body)))
    assert rc == 0, "子进程继承了父 stdin 或读它即失败"
    assert probe.read_text(encoding="utf-8") == "''"


@pytest.mark.skipif(sys.platform == "win32", reason="信号杀死自身（SIGKILL）是 POSIX 形态，Windows 走终止码 1")
def test_run_command_reports_signal_death_and_keeps_forwarded_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 崩溃恢复分支：子进程在读端尚未 drain 完就被信号打死，run_command 既不能抛错也不能判 0。
    body = (
        "import os, signal, sys\n"
        "sys.stderr.write('before death\\n')\n"
        "sys.stderr.flush()\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    rc, _dur, _hits = run_gates.run_command(_gate_cmd(_child_script(tmp_path, "sig.py", body)))
    assert rc != 0, f"信号死亡的退出码被吞成 {rc}（shell 通常报 137，run_gates 须原样透传）"
    assert "before death" in capsys.readouterr().out, "进程异常死亡前写出的 stderr 不得随管道一起丢"


def test_run_command_tolerates_absent_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # proc.stderr is None 的分支（stderr 未被接管时）必须正常走完并给出结论，而不是 TypeError。
    # 只替换 run_gates 模块全局名 subprocess（SimpleNamespace 副本），不改 stdlib 模块本体。
    class _NoStderrPopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.stderr = None
            self.returncode: int | None = 0

        def wait(self, *args: Any, **kwargs: Any) -> int:
            return 0

    shim = types.SimpleNamespace(**vars(subprocess))
    shim.Popen = _NoStderrPopen
    monkeypatch.setattr(run_gates, "subprocess", shim)
    rc, dur, hits = run_gates.run_command("python -c pass")
    assert (rc, hits) == (0, [])
    assert dur >= 0.0


def test_run_command_missing_gate_cwd_raises_not_silently_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # cwd 不存在属「检查工具本身坏了」，必须显式失败而不是返回 rc=0 让人以为检查过了。
    # 刻意取 tmp_path 下一个**没建过**的子目录：跨平台都保证不存在，且不会碰到只读根目录。
    monkeypatch.setattr(run_gates, "ROOT", tmp_path / "no-such-gate-cwd-dir")
    with pytest.raises(OSError):
        run_gates.run_command("python -c pass")


# ---------------------------------------------------------------------------
# L-30（2026-10-18 审查）：检查命令的执行形态
#
# 缺陷面：shell=True 时 Python 把整条命令再包一层引号并把内层引号转义成 `\"`，交 cmd.exe /c；
# cmd 对 /c 后面那段有「首尾引号成对就剥一层」的规则，于是 _rebind_interpreter 拼出的
# `"C:\My Tools\venv\Scripts\python.exe" -m black ...` 能否解析，取决于引号层数与 cmd 的
# 实现细节（venv 装在带空格路径上是常态，本机实测虽未复现失败，但这条依赖本身不可复核）。
# 修法是能按参数列表表达的命令一律 shell=False；拆不了的（含 shell 结构符、行首是 .bat/.cmd）
# 退回原形态，判定口径逐字不变。两侧用例都不靠 skipif：Linux CI 与 Windows 本机跑同一份断言。
# ---------------------------------------------------------------------------


def _stub_popen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, dict[str, Any]]]:
    # 记录 run_command 真正交给子进程 API 的东西；不真起进程，故与本机装了什么工具无关。
    # Popen 替身必须是**类**：run_command 之后还要读 .stderr / 调 .wait() / 取 .returncode；
    # 同时给 __class_getitem__，免掉「按 Popen[bytes] 形态注解」时的求值崩口（AGENTS.md 约定）。
    calls: list[tuple[Any, dict[str, Any]]] = []

    class _CapturingPopen:
        def __class_getitem__(cls, item: object) -> type:
            return cls

        def __init__(self, args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))
            self.stderr = io.StringIO("")
            self.returncode: int | None = 0

        def wait(self, *args: Any, **kwargs: Any) -> int:
            return 0

    shim = types.SimpleNamespace(**vars(subprocess))
    shim.Popen = _CapturingPopen
    monkeypatch.setattr(run_gates, "subprocess", shim)
    return calls


def test_run_command_uses_argv_list_for_plain_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_popen(monkeypatch)
    run_gates.run_command("python -m ruff format --check --line-length 120 .")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs.get("shell") is False, "普通检查命令仍经 cmd.exe/sh 二次解释 = L-30 的引号层还在"
    assert isinstance(args, list), f"应传参数列表，实际 {type(args).__name__}: {args!r}"
    assert args[0] == sys.executable, "行首解释器必须仍是当前解释器（换绑语义不得随形态改动丢失）"
    assert all('"' not in token for token in args), f"argv 里不该再有任何引号层：{args}"
    assert args[1:] == ["-m", "ruff", "format", "--check", "--line-length", "120", "."]


def test_run_command_falls_back_to_shell_only_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    # 退回 shell 的两类合法形态必须退回（否则等于把 shell 语义悄悄删掉）：
    #   ① 含 shell 结构符：参数列表无法等价表达，硬拆会把 `>` 当字面参数传给工具；
    #   ② 行首解析到 .bat/.cmd：Windows 的 CreateProcess 不执行批处理。
    calls = _stub_popen(monkeypatch)
    run_gates.run_command("python -m ruff format --check . > out.txt")
    args, kwargs = calls[0]
    assert kwargs.get("shell") is True and isinstance(args, str), "含重定向的命令必须仍走 shell"
    assert ">" in args, f"shell 形态应把原命令串交给 shell：{args!r}"

    shutil_shim = types.SimpleNamespace(**vars(run_gates.shutil))
    shutil_shim.which = lambda name: "/fake/Scripts/ruff.cmd"
    monkeypatch.setattr(run_gates, "shutil", shutil_shim)
    assert run_gates.build_gate_argv("ruff format --check .") is None, "批处理入口被拆成 argv = Windows 上必起不来"


def test_build_gate_argv_keeps_backslashes_and_quotes_stripped() -> None:
    # posix=False 的意义就在这里：默认模式会把反斜杠当转义吃掉，Windows 绝对路径被切成
    # `C:venvScriptspython.exe`（argv[0] 不存在，检查会「跑不起来却报成通过」）。
    argv = run_gates.build_gate_argv(r'python -m ruff format --exclude "C:\path\to\x" .')
    assert argv is not None
    assert r"C:\path\to\x" in argv, f"反斜杠被吃掉或引号没剥净：{argv}"
    assert all('"' not in token for token in argv)


@pytest.mark.parametrize(
    "cmd",
    [
        "python scripts/check_version.py",
        "ruff",
        "python -m ruff check --no-fix .",
    ],
)
def test_every_gate_command_shape_is_argv_executable(cmd: str) -> None:
    # 回路锁：AGENTS.md 检查块里的每一条形态都必须能按参数列表跑起来（拆不出来即说明改动
    # 让某条检查静默退回旧形态，本机看不出来、换机器才发现）。
    assert run_gates.build_gate_argv(cmd) is not None, f"{cmd!r} 拆不出 argv：检查仍要走 cmd.exe 引号层"


def test_run_command_drives_spaced_script_path_without_shell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 端到端形态：脚本路径本身带空格（venv 装在带空格目录属同一机制）。改前这条要靠
    # cmd.exe 的引号剥层规则解析，改后是纯 argv——空格不得再是判定面的一部分。
    spaced_dir = tmp_path / "gate space dir"
    spaced_dir.mkdir()
    script = _child_script(spaced_dir, "ok.py", "import sys\nsys.stderr.write('spaced line\\n')\n")
    rc, _dur, hits = run_gates.run_command(_gate_cmd(script))
    assert rc == 0, f"带空格路径的检查命令被判成 {rc}（cmd.exe 引号剥层形态回归）"
    assert hits == []
    assert "spaced line" in capsys.readouterr().out, "argv 形态下 stderr 仍须逐行原样转发（MID-63）"


@pytest.mark.parametrize("stream_factory", ["no_reconfigure", "value_error", "os_error"], ids=lambda v: v)
def test_ensure_utf8_streams_never_raises(monkeypatch: pytest.MonkeyPatch, stream_factory: str) -> None:
    # 崩溃恢复分支：本函数只改善输出、不参与判定。三种「改不动」的形态都必须静默放过，
    # 否则「输出流被重定向到已关闭句柄」会把检查进程炸在还没有任何结论之前。
    class _NoReconfigure:
        pass

    class _BadReconfigure:
        def __init__(self, exc: type[Exception]) -> None:
            self._exc = exc

        def reconfigure(self, **kwargs: Any) -> None:
            raise self._exc("boom")

    streams: dict[str, Any] = {
        "no_reconfigure": _NoReconfigure(),
        "value_error": _BadReconfigure(ValueError),
        "os_error": _BadReconfigure(OSError),
    }
    fake = streams[stream_factory]
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setattr(sys, "stderr", fake)
    run_gates.ensure_utf8_streams()


def test_ensure_utf8_streams_makes_unencodable_emoji_survivable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2026-09-21 实测崩溃：中文 Windows（cp936）下 black **通过**时那行 `All done! ✨ 🍰 ✨`
    # 让转发语句抛 UnicodeEncodeError，rc=1 且只剩一段 Traceback——比不跑检查更糟。
    out = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    err = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    run_gates.ensure_utf8_streams()
    out.write("All done! ✨ 🍰 ✨")  # 未重配置成功即抛，本行就是回归点
    out.flush()
    assert "\u2728" in out.buffer.getvalue().decode("utf-8", errors="replace")


def _fake_agents(tmp_path: Path) -> Path:
    # 最小「格式化命令」章节：两条 --check 型命令 + 行首环境变量前缀，供 main() 回路用例使用
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "## 格式化命令（用例用）\n\n"
        "```bash\n"
        "PYTHONUTF8=1 python -m ruff format --check .\n"
        "python scripts/check_version.py   # 条件增跑\n"
        "```\n",
        encoding="utf-8",
    )
    return agents


def test_main_fails_on_fatal_pattern_even_with_zero_rc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 回路级闭环：rc=0 但 stderr 命中致命告警 → 该条判 FAIL → 整体 rc=1；
    # 且默认首个失败即停（--keep-going 才跑完），检查块行首前缀必须作为子进程环境透下去。
    monkeypatch.setattr(run_gates, "ROOT", tmp_path)
    _fake_agents(tmp_path)
    calls: list[tuple[str, dict[str, str] | None]] = []

    def fake_run_command(cmd: str, extra_env: dict[str, str] | None = None) -> tuple[int, float, list[str]]:
        calls.append((cmd, extra_env))
        return 0, 0.1, ["Unable to parse file"]

    monkeypatch.setattr(run_gates, "run_command", fake_run_command)
    monkeypatch.setattr(run_gates, "check_executables", lambda cmds: [])
    assert run_gates.main([]) == 1, "rc=0 + 致命告警被判成通过 = MID-63 假绿"
    assert len(calls) == 1, f"默认应首个失败即停，实际跑了 {len(calls)} 条"
    assert calls[0] == ("python -m ruff format --check .", {"PYTHONUTF8": "1"}), calls[0]

    calls.clear()  # 第二次跑只看本轮次数，不与上一轮的 fail-fast 计数混在一起
    assert run_gates.main(["--keep-going"]) == 1
    assert len(calls) == 2, "--keep-going 必须跑完剩余检查再汇总"


def test_main_exits_3_when_gate_tool_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 「工具没装」与「代码不合格」必须用不同退出码区分开，否则环境噪声会被读成回归
    monkeypatch.setattr(run_gates, "ROOT", tmp_path)
    _fake_agents(tmp_path)
    monkeypatch.setattr(run_gates, "check_executables", lambda cmds: ["ruff"])
    monkeypatch.setattr(run_gates, "run_command", lambda *a: (0, 0.0, []))
    assert run_gates.main([]) == 3


def test_check_executables_accepts_module_fallback_without_path_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    # 裸 console-script（mypy）不在 PATH、但当前解释器装了该包时不得误报 rc=3。
    # 只替换 run_gates 模块全局名 shutil（SimpleNamespace 副本），不改 stdlib 模块本体：
    # shutil.which 被同进程其他持有者（含 harness 线程）共用，整体替换会外溢。
    shim = types.SimpleNamespace(**vars(run_gates.shutil))
    shim.which = lambda name: None
    monkeypatch.setattr(run_gates, "shutil", shim)
    assert run_gates.check_executables(["python -m ruff format --check ."]) == []
    assert run_gates.check_executables(["ruff"]) == []
    assert run_gates._module_unavailable("definitely_not_a_real_module_xyz") is True
    assert run_gates._module_unavailable("json") is False


# ---------------------------------------------------------------------------
# gate_pytest_warnings_summary 用例（SEV-2219，2026-09-23）
#
# 该兜底原形态：`_ = proc.wait()` 丢弃退出码 + `stderr=DEVNULL` 吞 stderr，判定只看
# stdout 里有没有 `= warnings summary =`。于是 pytest 以 rc=2/3/4/5 崩溃、收集期
# ImportError、甚至**一条用例都没跑到**时，它照样返回 True 并打印「为空 → PASS」，
# DoD 第 1 步的测试面自我证明整个失效。下面逐条锁住三条 fail-closed 判据：
#   ① rc != 0 一律失败且带上 rc 语义；② stderr 纳入 FATAL_STDERR_PATTERNS 同一回路；
#   ③ 必须看到「N passed」且 N > 0。
# 全部离线：subprocess 只替换 run_gates 模块全局引用（SimpleNamespace 副本），
# 不改 stdlib 本体（AGENTS.md「patch main.py 的 subprocess 必须替换全局引用」同源约束，
# 本仓 tests/ 卫生检查 R1 也会把改本体的写法判红）。
# ---------------------------------------------------------------------------


class _StubCompleted:
    # subprocess.run 的返回值替身：只暴露被测面消费的三个字段。
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_pytest_run(
    monkeypatch: pytest.MonkeyPatch, *, rc: int, stdout: str = "", stderr: str = ""
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake_run(cmd: Any, **kwargs: Any) -> _StubCompleted:
        calls.append((cmd, kwargs))
        return _StubCompleted(rc, stdout, stderr)

    shim = types.SimpleNamespace(**vars(subprocess))
    shim.run = fake_run
    monkeypatch.setattr(run_gates, "subprocess", shim)
    return calls


def test_warnings_gate_passes_on_clean_full_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_pytest_run(
        monkeypatch,
        rc=0,
        stdout="................................................................ [100%]\n422 passed in 11.68s\n",
    )
    ok, msg = run_gates.gate_pytest_warnings_summary()
    assert ok is True, msg
    assert "422 passed" in msg, f"通过信息须带用例总数，供人核对判定面没缩水: {msg}"
    # 调用形态本身也是被锁的一面：必须接管 stdout+stderr 且 check=False（自己判 rc）
    cmd, kwargs = calls[0]
    assert kwargs["stdout"] is subprocess.PIPE and kwargs["stderr"] is subprocess.PIPE, kwargs
    assert kwargs.get("check") is False, "用了 check=True 就等于把 rc 交给异常路径，兜底会先崩"


@pytest.mark.parametrize(
    ("rc", "expect_fragment"),
    [
        (2, "中断"),
        (3, "内部错误"),
        (4, "用法"),
        (5, "未收集"),
    ],
    ids=["rc2-interrupted", "rc3-internal", "rc4-usage", "rc5-notcollected"],
)
def test_warnings_gate_fails_on_pytest_crash_rc(monkeypatch: pytest.MonkeyPatch, rc: int, expect_fragment: str) -> None:
    # SEV-2219 的主形态：rc 被丢弃时，这四类「根本没跑完」的输出里都没有
    # `= warnings summary =`，旧实现会打印「为空 → PASS」。
    _stub_pytest_run(monkeypatch, rc=rc, stdout="no tests ran in 0.01s\n")
    ok, msg = run_gates.gate_pytest_warnings_summary()
    assert ok is False, f"rc={rc} 被判成通过 = 丢弃退出码的形态回来了"
    assert f"rc={rc}" in msg, msg
    assert expect_fragment in msg, f"诊断里必须给出 rc 的语义，实际: {msg}"


def test_warnings_gate_fails_when_no_test_count_in_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    # rc=0 但 stdout 里既无 warnings summary 也无「N passed」汇总行
    # （报告 §6.3 第 17 条点名的形态：一条用例都没跑到却判 PASS）。
    _stub_pytest_run(monkeypatch, rc=0, stdout="no tests ran in 0.05s\n")
    ok, msg = run_gates.gate_pytest_warnings_summary()
    assert ok is False, "检查没跑到用例却判通过"
    assert "没跑到用例" in msg or "0）" in msg, msg


def test_warnings_gate_fails_on_zero_passed_summary_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # 全部跳过（0 passed）同样属「没跑到用例」：不得靠 rc=0 蒙过去。
    _stub_pytest_run(monkeypatch, rc=0, stdout="18 skipped in 0.30s\n")
    ok, _msg = run_gates.gate_pytest_warnings_summary()
    assert ok is False, "整会话零执行却被判通过（skip 风暴 / testpaths 失配的形态）"


def test_warnings_gate_fails_on_fatal_stderr_pattern_despite_zero_rc(monkeypatch: pytest.MonkeyPatch) -> None:
    # ② stderr 纳入同一回路：rc=0 + 正常汇总行，但 stderr 命中「Unable to parse file」
    # （插件/编码降级只发告警的形态）必须失败，而不是继续吞掉。
    _stub_pytest_run(
        monkeypatch,
        rc=0,
        stdout="10 passed in 1.00s\n",
        stderr="WARNING: Unable to parse file: pyproject.toml\n",
    )
    ok, msg = run_gates.gate_pytest_warnings_summary()
    assert ok is False, "stderr 被吞回旧形态：致命告警不再参与判定"
    assert "Unable to parse file" in msg, msg


def test_warnings_gate_still_flags_nonempty_warnings_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # 原有语义不得丢：有汇总行 = 有告警 = 失败。
    _stub_pytest_run(
        monkeypatch,
        rc=0,
        stdout="5 passed, 3 warnings in 2.00s\n=============================== warnings summary ===============================\n",
    )
    ok, msg = run_gates.gate_pytest_warnings_summary()
    assert ok is False
    assert "warnings summary 非空" in msg, msg


def test_warnings_gate_source_takes_rc_and_stderr() -> None:
    # 源码形态锁：把 fail-closed 三条改回「丢弃 rc / 吞 stderr」是同一族回归，
    # 而桩化用例在「改用 Popen 但不读 rc」时未必能抓到——这里直接盯字面形态。
    source = (ROOT / "scripts" / "run_gates.py").read_text(encoding="utf-8")
    start = source.index("def gate_pytest_warnings_summary(")
    end = source.index("\ndef main(", start)
    # 先剥注释行：本仓注释里会引用「原实现长什么样」（SEV-2219 的旧形态就写在注释里），
    # 不剥离就会被自己写的历史注误判成「旧代码还在」。等价性校验用 AST、这条用有效代码行。
    seg = "\n".join(line for line in source[start:end].splitlines() if not line.strip().startswith("#"))
    assert "subprocess.run(" in seg, "必须一次性接管两路输出（communicate），避免 stderr 管道塞满死锁"
    assert "stderr=subprocess.DEVNULL" not in seg, "SEV-2219：stderr 又被吞回 DEVNULL"
    assert "proc.returncode" in seg, "SEV-2219：退出码必须参与判定"
    assert "_ = proc.wait()" not in seg, "SEV-2219：wait() 的返回值被丢弃 = rc 没人看"


def test_pytest_rc_semantics_table_covers_documented_codes() -> None:
    # 语义表缺项会让诊断退化成「未登记的退出码 N」，运维看不出下一步该查什么。
    for rc in (0, 1, 2, 3, 4, 5):
        assert rc in run_gates.PYTEST_RC_SEMANTICS, f"缺少 rc={rc} 的语义说明"


def test_ruff_discovery_keeps_product_and_test_files() -> None:
    # 直接问 Ruff 扫了哪些文件，避免再实现一份目录匹配器而得到假绿。
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-files", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    scanned = {Path(name).relative_to(ROOT).as_posix() for name in proc.stdout.splitlines()}
    assert len(scanned) >= 200
    assert {"main.py", "gui.py", "web.py", "src/ffmpeg_install.py", "tests/test_node_install.py"} <= scanned


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("def caller():\n    return missing_symbol()\n", "F821"),
        ('__all__ = ["missing_symbol"]\n', "F822"),
        ("value = 1\ndef caller():\n    print(value)\n    value = 2\n", "F823"),
    ],
)
def test_ruff_reports_undefined_names(source: str, rule: str) -> None:
    # 驱动真实 Ruff 与仓库配置，确保不同作用域的悬空名字仍被检查。
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-fix",
            "--output-format",
            "concise",
            "--stdin-filename",
            "src/_lint_probe.py",
            "-",
        ],
        cwd=ROOT,
        input=source,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f" {rule} " in proc.stdout, proc.stdout + proc.stderr
