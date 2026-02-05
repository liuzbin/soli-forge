import subprocess
from pathlib import Path
import re
import os


def create_foundry_config(work_dir: Path):
    """
    自动创建 foundry.toml 配置文件。
    这是为了确保 forge test 能在我们的临时目录结构中正确找到 .sol 和 .t.sol 文件。
    """
    config_path = work_dir / "foundry.toml"

    # 只有当配置文件不存在时才创建
    if not config_path.exists():
        # 配置说明：
        # src = '.' 和 test = '.' 表示直接在当前根目录及其子目录查找源文件和测试文件
        # 这样无论我们把文件放在 root 还是 artifacts 目录，forge 都能递归扫描到
        config_content = """[profile.default]
src = '.'
test = '.'
out = 'out'
libs = ['lib']
"""
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)
        except Exception as e:
            print(f"⚠️ Warning: Failed to create foundry.toml: {e}")


def run_docker_command(work_dir: Path, command: str):
    """
    通用 Docker 执行器
    """
    # 确保路径是绝对路径
    abs_work_dir = work_dir.absolute()

    docker_cmd = [
        "docker", "run", "--rm",
        "--entrypoint", "",
        "-v", f"{abs_work_dir}:/app",
        "-w", "/app",
        "soliforge-worker",
        "/bin/sh", "-c",
        command
    ]

    print(f"DEBUG: Docker Exec: {command}")

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'  # 防止特殊字符报错
        )
        return result.stdout, result.stderr
    except Exception as e:
        return "", str(e)


def run_forge_test_json(work_dir: Path):
    """
    运行测试并解析每个 Test Case 的结果
    返回:
      results: { "testExploit_XXX": "PASS" | "FAIL" }
      raw_output: str
    """
    # 1. 确保有配置文件
    create_foundry_config(work_dir)

    # 2. 运行 foundry 测试，开启详细模式 -vv
    # --json 参数在某些版本的 forge 中行为不一致，这里我们主要解析标准输出
    cmd = "forge test -vv"
    stdout, stderr = run_docker_command(work_dir, cmd)

    results = {}
    full_output = (stdout or "") + "\n" + (stderr or "")

    # 解析 Foundry 输出
    # 典型输出格式:
    # [PASS] testExploit_Reentrancy_01() (gas: 1234)
    # [FAIL. Reason: Assertion failed] testExploit_Overflow_02() (gas: 5678)

    # 正则匹配
    matches = re.findall(r'\[(PASS|FAIL).*?\]\s+(testExploit_\w+)\(\)', full_output)

    for status, name in matches:
        # status 捕获到的可能是 "PASS" 或 "FAIL"
        results[name] = status

    return results, full_output


def check_compilation(work_dir: Path):
    """简单检查编译是否通过"""
    create_foundry_config(work_dir)
    stdout, stderr = run_docker_command(work_dir, "forge build")

    # 简单的成功判定
    if "Compiler run successful" in stdout or "No files changed" in stdout:
        return True
    return False
