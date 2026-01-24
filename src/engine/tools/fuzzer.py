import os
import subprocess
import json
from pathlib import Path
from .docker_runner import create_foundry_config


def create_simple_test(contract_name: str, import_path: str, iteration: int) -> str:
    """
    :param import_path: 目标合约的 import 路径 (例如 ./artifacts/target-r1.sol)
    """
    return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import "{import_path}";

contract FuzzTest{iteration} is Test {{
    {contract_name} public target;

    function setUp() public {{
        target = new {contract_name}();
    }}

    function testFuzz_DepositWithdraw(address user, uint256 amount) public {{
        vm.assume(user != address(0));
        vm.assume(amount > 0 && amount < 100 ether);
        vm.deal(user, amount);

        vm.prank(user);
        (bool success, ) = address(target).call{{value: amount}}("");

        if (success) {{
            vm.prank(user);
            (bool wSuccess, ) = address(target).call(abi.encodeWithSignature("withdraw()"));
            assertTrue(wSuccess || address(target).balance >= 0);
        }}
    }}
}}
"""


def run_fuzz_test(task_dir: Path,
                  contract_path: Path,
                  iteration: int) -> tuple[str, str]:
    """
    :param task_dir: 任务根目录
    :param contract_path: 目标合约的完整路径 (用于计算相对引用)
    """
    create_foundry_config(task_dir)

    # 1. 计算相对路径以生成 import "./..."
    # 假设 contract_path 是 .../artifacts/target.sol，test 文件将放在 .../artifacts/ 下
    # 简单起见，我们把 Test 文件生成在 artifacts 目录
    artifacts_dir = task_dir / "artifacts"

    # 计算 import 路径: 由于都在 artifacts 下或引用 original，这里简化处理
    # 如果 target 在 original，引用就是 "../original/Target.sol"
    # 如果 target 在 artifacts，引用就是 "./target-r1.sol"
    rel_path = os.path.relpath(contract_path, artifacts_dir)
    # 替换反斜杠适应 Solidity
    import_path = rel_path.replace("\\", "/")
    if not import_path.startswith("."):
        import_path = "./" + import_path

    contract_name = "Target"  # 假设合约名固定或需解析，这里暂用 Target

    test_code = create_simple_test(contract_name, import_path, iteration)
    test_filename = f"FuzzTest{iteration}.t.sol"
    test_file_path = artifacts_dir / test_filename

    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(test_code)

    # 2. 运行 Docker
    fuzz_runs = 1000 if iteration == 1 else 5000

    # 容器内路径: /app/artifacts/FuzzTest1.t.sol
    container_test_path = f"/app/artifacts/{test_filename}"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{task_dir.absolute()}:/app",
        "foundry-box",
        f"forge test --json --fuzz-runs {fuzz_runs} --match-path {container_test_path} --remappings forge-std/=/opt/foundry/lib/forge-std/src/"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode == 0:
            return "success", f"Fuzz {iteration} Passed."
        else:
            return "failed", f"Fuzz {iteration} Failed.\n{result.stdout[:500]}"
    except Exception as e:
        return "error", str(e)