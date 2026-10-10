"""Open already-exported fixed files only after an explicit user action."""
import os
from pathlib import Path
import shutil
import shlex
import subprocess


def find_editor():
    configured = os.environ.get("REVIEW_COMPANION_EDITOR")
    if configured:
        return shutil.which(configured)
    executable = shutil.which("code")
    if executable:
        return executable
    mac_cli = Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
    if mac_cli.is_file() and os.access(mac_cli, os.X_OK):
        return str(mac_cli)
    return None


def launch_editor(prepared, mode, repository=None):
    """An exit-zero CLI handoff is a request, not a claim of visible UI success."""
    result = {**prepared, "launch_requested": False}
    if mode not in ("source", "diff", "project"):
        raise ValueError("unsupported editor mode")
    if repository is not None or mode == "project":
        project = Path(repository or "")
        if not project.is_absolute() or not project.is_dir():
            return {**result, "command": "", "message": "项目目录已不可用，请检查本次回顾对应的本机项目路径。"}
        repository = str(project)
        result["repository"] = repository
    executable = find_editor()
    argv = [executable or "code", repository] if repository else [executable or "code", "--reuse-window"]
    if mode == "source":
        path = Path(prepared["path"])
        line = prepared["line"]
        if not path.is_absolute() or not path.is_file() or type(line) is not int or line < 1:
            return {**result, "message": "固定版本文件或行号无效，请重新准备后再打开。"}
        argv += ["--goto", f"{path}:{line}:1"]
    elif mode == "diff":
        paths = [Path(prepared[key]) for key in ("left_path", "right_path")]
        if any(not path.is_absolute() or not path.is_file() for path in paths):
            return {**result, "message": "固定比较文件已不可用，请重新准备后再打开。"}
        argv += ["--diff", *(str(path) for path in paths)]
    if repository:
        result["command"] = shlex.join(argv)
    if not executable:
        return {**result, "message": "未找到 VS Code 命令。可复制下方命令手工打开。"}
    try:
        process = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False, shell=False)
    except subprocess.TimeoutExpired:
        return {**result, "message": "启动 VS Code 超时。可复制下方命令手工打开。"}
    except OSError:
        return {**result, "message": "无法启动 VS Code。可复制下方命令手工打开。"}
    if process.returncode != 0:
        return {**result, "message": f"VS Code 启动未成功（退出码 {process.returncode}）。可复制下方命令手工打开。"}
    return {**result, "launch_requested": True, "message": "已请求 VS Code 打开项目。" if mode == "project" else "已请求 VS Code 在项目中打开固定版本。"}
