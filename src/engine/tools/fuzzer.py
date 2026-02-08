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

    if test_sol_path.exists(): return
    if forge_std_dir.exists():
        try:
            shutil.rmtree(forge_std_dir)
        except:
            pass

    cmd = [
        "docker", "run", "--rm", "--entrypoint", "",
        "-v", f"{task_dir.absolute()}:/app", "-w", "/app",
        "ghcr.io/foundry-rs/foundry:latest",
        "/bin/sh", "-c",
        "mkdir -p lib && git clone --depth 1 https://github.com/foundry-rs/forge-std lib/forge-std"
    ]
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

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


# Template 1: 随机 Fuzz 模板 (用于探索)
def create_fuzz_template(contract_name: str, import_path: str, iteration: int) -> str:
    return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import "{import_path}";

contract FuzzTest{iteration} is Test {{
    {contract_name} public target;
    function setUp() public {{ target = new {contract_name}(); }}

    function testFuzz_Exploration(uint256 amount) public {{
        // 限制范围，避免无意义的溢出测试干扰逻辑测试
        amount = bound(amount, 1, 100 ether);

        vm.deal(address(this), amount);
        try target.deposit{{value: amount}}() {{
            // 如果存款成功，尝试取款
            try target.withdraw() {{
                // Check Invariant: Contract balance should be 0 after full withdrawal
                // 如果 withdraw 有逻辑漏洞（比如没扣余额），这里虽然跑通了，但状态可能不对
                // 但对于 Reentrancy，普通 Fuzz 很难直接 panic，除非我们检查不变量
            }} catch {{}}
        }} catch {{}}
    }}
}}
"""


# Template 2: 固化复现模板 (用于生成“不安全的证据”)
def create_reproduction_test(contract_name: str, import_path: str, iteration: int, args: list) -> str:
    """
    将 Fuzzer 发现的参数 'args' 硬编码生成一个具体的 Solidity 测试文件。
    """
    # 构造参数字符串，例如: uint256 amount = 123456;
    # 简单起见，我们假设只有一个 uint 参数，实际需根据 JSON 类型解析
    # 这里做个简单适配：取第一个参数作为 amount
    fixed_val = args[0] if args else "1 ether"

    return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import "{import_path}";

// 🔴 这是由 Fuzzer 自动生成的攻击复现代码
// 参数已固化，用于确凿地证明漏洞存在
contract Reproduce_Fuzz_Crash_{iteration} is Test {{
    {contract_name} public target;

    function setUp() public {{ 
        target = new {contract_name}(); 
    }}

    function testExploit_Fuzz_Reproduction() public {{
        uint256 amount = {fixed_val};

        vm.deal(address(this), amount);

        console.log("Replaying Fuzz Crash with amount:", amount);

        // 我们期望这里会发生 Revert 或者 违反断言
        target.deposit{{value: amount}}();
        target.withdraw();
    }}
}}
"""


def run_fuzz_test(task_dir: Path, contract_path: Path, iteration: int):
    """
    运行 Fuzzer -> 解析结果 -> 如果失败，生成固化代码 -> 返回固化代码路径
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

    # 1. 生成并运行随机 Fuzz 测试
    fuzz_filename = f"FuzzTest_Round{iteration}.t.sol"
    fuzz_path = artifacts_dir / fuzz_filename
    with open(fuzz_path, "w", encoding="utf-8") as f:
        f.write(create_fuzz_template(contract_name, import_path, iteration))

    fuzz_runs = 1000  # 提高到 1000 轮
    container_test_path = f"/app/artifacts/{fuzz_filename}"

    cmd = [
        "docker", "run", "--rm", "--entrypoint", "",
        "-v", f"{task_dir.absolute()}:/app", "-w", "/app",
        "ghcr.io/foundry-rs/foundry:latest",
        "/bin/sh", "-c",
        f"forge test --json --fuzz-runs {fuzz_runs} --match-path {container_test_path}"
    ]

    stats = {"runs": fuzz_runs, "failures": 0}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        if result.stdout and "{" in result.stdout:
            try:
                json_str = result.stdout[result.stdout.find('{'):result.stdout.rfind('}') + 1]
                data = json.loads(json_str)

                counterexample_args = []
                found_failure = False

                for contract_key, contract_val in data.items():
                    test_results = contract_val.get("test_results", {})
                    for test_name, test_data in test_results.items():
                        kind = test_data.get("kind", {})
                        if "Fuzz" in kind:
                            stats["runs"] = kind["Fuzz"].get("runs", fuzz_runs)

                        if test_data.get("status") != "Success":
                            stats["failures"] = 1
                            found_failure = True
                            # 提取反例参数
                            # counterexample 格式通常是: [ "0x...", "123" ]
                            cex = test_data.get("counterexample")
                            if cex and isinstance(cex, list):  # 新版 Foundry
                                # 处理一下参数，把 16进制转十进制字符串，或者直接用
                                counterexample_args = cex
                            elif test_data.get("reason"):  # 有时候直接给 reason
                                pass

                # 🌟 关键逻辑：如果发现失败，生成“复现脚本”
                if found_failure:
                    print(f"DEBUG: Fuzzer found failure! Args: {counterexample_args}")

                    # 生成固化的 .t.sol
                    repro_code = create_reproduction_test(contract_name, import_path, iteration, counterexample_args)
                    repro_filename = f"Exploit_Fuzzer_Repro_{iteration}.t.sol"  # 命名统一为 Exploit_
                    repro_path = artifacts_dir / repro_filename

                    with open(repro_path, "w", encoding="utf-8") as f:
                        f.write(repro_code)

                    # 返回新生成的固化文件路径，而不是随机 Fuzz 文件路径
                    return "success", stats, repro_path

            except Exception as e:
                print(f"JSON Parse Error: {e}")

            # 如果全是 Success，还是返回 Fuzz 文件
            return "success", stats, fuzz_path
        else:
            return "failed", stats, fuzz_path

    except Exception as e:
        return "error", stats, None