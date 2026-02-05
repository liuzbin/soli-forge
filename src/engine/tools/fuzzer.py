import os
import subprocess
import shutil
import re
import json
from pathlib import Path
from .docker_runner import create_foundry_config


def ensure_forge_std(task_dir: Path):
    """强力安装 forge-std"""
    lib_dir = task_dir / "lib"
    forge_std_dir = lib_dir / "forge-std"
    test_sol_path = forge_std_dir / "src" / "Test.sol"

    if test_sol_path.exists():
        return

    if forge_std_dir.exists():
        try:
            shutil.rmtree(forge_std_dir)
        except:
            pass

    # 使用 git clone
    cmd = [
        "docker", "run", "--rm",
        "--entrypoint", "",
        "-v", f"{task_dir.absolute()}:/app",
        "-w", "/app",
        "ghcr.io/foundry-rs/foundry:latest",
        "/bin/sh", "-c",
        "mkdir -p lib && git clone --depth 1 https://github.com/foundry-rs/forge-std lib/forge-std"
    ]
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

    # 写入 remappings
    remapping_path = task_dir / "remappings.txt"
    with open(remapping_path, "w", encoding="utf-8") as f:
        f.write("forge-std/=lib/forge-std/src/")


def get_contract_name(contract_path: Path) -> str:
    try:
        with open(contract_path, "r", encoding="utf-8") as f:
            match = re.search(r'contract\s+(\w+)', f.read())
            if match: return match.group(1)
    except:
        pass
    return "Target"


def create_simple_test(contract_name: str, import_path: str, iteration: int) -> str:
    return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import "{import_path}";

contract FuzzTest{iteration} is Test {{
    {contract_name} public target;
    function setUp() public {{ target = new {contract_name}(); }}

    function testFuzz_DepositWithdraw(address user, uint256 amount) public {{
        vm.assume(user != address(0));
        vm.assume(amount > 0 && amount < 100 ether);
        vm.deal(user, amount);
        vm.prank(user);
        (bool success, ) = address(target).call{{value: amount}}("");
        if (success) {{
            vm.prank(user);
            (bool wSuccess, ) = address(target).call(abi.encodeWithSignature("withdraw()"));
        }}
    }}
}}
"""


def run_fuzz_test(task_dir: Path, contract_path: Path, iteration: int):
    """
    运行 Fuzzer 并返回详细统计信息
    返回: (status, stats_dict, test_file_path)
    """
    ensure_forge_std(task_dir)
    create_foundry_config(task_dir)

    artifacts_dir = task_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        rel_path = os.path.relpath(contract_path, artifacts_dir)
    except:
        rel_path = f"../{contract_path.name}"
    import_path = rel_path.replace("\\", "/").lstrip("./")
    if not import_path.startswith("../"): import_path = "./" + import_path

    contract_name = get_contract_name(contract_path)
    test_code = create_simple_test(contract_name, import_path, iteration)
    test_filename = f"FuzzTest_Round{iteration}.t.sol"
    test_file_path = artifacts_dir / test_filename

    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(test_code)

    fuzz_runs = 500
    container_test_path = f"/app/artifacts/{test_filename}"

    cmd = [
        "docker", "run", "--rm",
        "--entrypoint", "",
        "-v", f"{task_dir.absolute()}:/app",
        "-w", "/app",
        "ghcr.io/foundry-rs/foundry:latest",
        "/bin/sh", "-c",
        f"forge test --json --fuzz-runs {fuzz_runs} --match-path {container_test_path}"
    ]

    # 默认统计
    stats = {"runs": fuzz_runs, "failures": 0}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        # 解析 JSON 提取真实运行次数
        if result.stdout and "{" in result.stdout:
            try:
                # 提取 JSON 部分 (防止有其他日志干扰)
                json_str = result.stdout[result.stdout.find('{'):result.stdout.rfind('}') + 1]
                data = json.loads(json_str)

                # 遍历结果找到 Fuzz 统计
                for contract_key, contract_val in data.items():
                    test_results = contract_val.get("test_results", {})
                    for test_name, test_data in test_results.items():
                        kind = test_data.get("kind", {})
                        if "Fuzz" in kind:
                            stats["runs"] = kind["Fuzz"].get("runs", fuzz_runs)
                            # 如果状态不是 Success，那就是失败
                            if test_data.get("status") != "Success":
                                stats["failures"] = 1  # 至少失败了1次
            except Exception as e:
                print(f"JSON Parse Error: {e}")

            return "success", stats, test_file_path
        else:
            return "failed", stats, test_file_path

    except Exception as e:
        return "error", stats, None