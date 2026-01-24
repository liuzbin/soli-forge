import re
from src.engine.tools.docker_runner import run_docker_command


def detect_solc_version(content: str) -> str:
    """尝试从合约源码中提取 solidity 版本号"""
    match = re.search(r'pragma\s+solidity\s+[\^><=]*(\d+\.\d+\.\d+)', content)
    if match:
        return match.group(1)
    return "0.8.20"


def run_slither_scan(fm):
    """
    运行 Slither 静态扫描 (只关注中高风险)
    """
    try:
        contract_path = fm.task_dir / fm.task.contract_name
        if contract_path.exists():
            content = contract_path.read_text(encoding='utf-8')
            version = detect_solc_version(content)
        else:
            version = "0.8.20"
    except:
        version = "0.8.20"

    print(f"DEBUG: Detected Solidity version: {version}")

    # ✅ 核心修改在这里：
    # 添加 --exclude-informational --exclude-optimization --exclude-low
    # 策略：
    # 1. 绝对排除: optimization (Gas优化), informational (代码风格)
    # 2. 视情况排除: low (低危)。通常建议保留 Low 给 LLM 看一眼，但这里为了你的需求，我们先把 Low 也排除，只看 Medium/High。
    # 如果你想保留 Low，把 --exclude-low 去掉即可。

    cmd = (
        f"solc-select install {version} && "
        f"solc-select use {version} && "
        f"slither {fm.task.contract_name} "
        "--exclude-informational --exclude-optimization --exclude-low"
    )

    print(f"DEBUG: Running Slither command: {cmd}")

    stdout, stderr = run_docker_command(fm.task_dir, cmd)

    report = (stdout or "") + "\n" + (stderr or "")

    # 简单清洗：如果没有任何输出，说明没有中高危漏洞
    if not report.strip():
        return ""  # 返回空字符串，代表无风险

    return report
