import json
import shutil
from src.engine.tools.docker_runner import run_docker_command


def run_slither_scan(file_manager, version: str) -> str:
    """
    运行 Slither 静态扫描
    参数:
      version: 当前版本号 (e.g., "v1", "v2")

    保存路径: storage/tasks/{id}/artifacts/slither_report_{version}.json
    返回: 格式化后的 Markdown 报告字符串
    """
    # 1. 准备工作目录：统一使用 artifacts 目录
    artifacts_dir = file_manager.task_dir / "artifacts"
    if not artifacts_dir.exists():
        artifacts_dir.mkdir(parents=True, exist_ok=True)

    contract_name = file_manager.task.contract_name

    # 自动同步逻辑：确保 artifacts 目录下有合约文件
    root_contract_path = file_manager.task_dir / contract_name
    artifact_contract_path = artifacts_dir / contract_name

    if root_contract_path.exists() and not artifact_contract_path.exists():
        shutil.copy(root_contract_path, artifact_contract_path)
        print(f"DEBUG: Copied contract to artifacts: {artifact_contract_path}")

    # 2. 确定 Solidity 版本
    solc_version = "0.8.20"  # 默认
    try:
        if artifact_contract_path.exists():
            with open(artifact_contract_path, "r", encoding="utf-8") as f:
                content = f.read()
                import re
                match = re.search(r'pragma solidity\s+([^;]+);', content)
                if match:
                    ver_str = match.group(1)
                    nums = re.findall(r'(\d+\.\d+\.\d+)', ver_str)
                    if nums:
                        solc_version = nums[0]
    except Exception as e:
        print(f"Version detection failed: {e}")

    # 3. 构造带版本号的文件名
    report_filename = f"slither_report_{version}.json"

    # 4. 构造命令
    cmd = (
        f"solc-select install {solc_version} && "
        f"solc-select use {solc_version} && "
        f"slither {contract_name} "
        "--exclude-informational --exclude-optimization --exclude-low "
        f"--json {report_filename}"
    )

    print(f"DEBUG: Running Slither ({version}) in artifacts dir: {cmd}")

    # 5. 执行 Docker 命令
    stdout, stderr = run_docker_command(artifacts_dir, cmd)

    # 6. 读取生成的 JSON 报告
    report_path = artifacts_dir / report_filename

    if not report_path.exists():
        return f"Slither failed to generate report ({version}).\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        results = data.get("results", {}).get("detectors", [])

        if not results:
            return f"✅ [Slither {version}] No high/medium severity vulnerabilities found."

        formatted_report = f"### Slither Report ({version})\n\n"

        for idx, item in enumerate(results):
            check = item.get("check", "Unknown")
            impact = item.get("impact", "Unknown")
            description = item.get("description", "No description")

            lines = []
            if "elements" in item:
                for elem in item["elements"]:
                    if "source_mapping" in elem:
                        lines.append(str(elem["source_mapping"].get("lines", [])))

            formatted_report += f"**{idx + 1}. {check}** [{impact}]\n"
            formatted_report += f"- **Description**: {description}\n\n"

        return formatted_report

    except Exception as e:
        return f"Error parsing Slither JSON: {str(e)}"