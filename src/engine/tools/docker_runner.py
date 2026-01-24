import subprocess
from pathlib import Path
import re


def run_docker_command(work_dir: Path, command: str):
    """
    通用 Docker 执行器
    """
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{work_dir.absolute()}:/app",
        "-w", "/app",
        "ghcr.io/foundry-rs/foundry:latest",
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
    # 运行 foundry 测试，开启详细模式 -vv
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
    stdout, stderr = run_docker_command(work_dir, "forge build")
    if "Compiler run successful" in stdout or "No files changed" in stdout:
        return True
    return False