import os
import subprocess
from pathlib import Path
from src.engine.tools.docker_runner import create_foundry_config


def create_simple_test(contract_name: str, import_path: str, iteration: int) -> str:
    # 简单的 Fuzz 模板，尝试存取款、溢出等
    return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import "{import_path}";

contract FuzzTest{iteration} is Test {{
    {contract_name} public target;

    function setUp() public {{
        target = new {contract_name}();
    }}

    // 通用 Fuzz 测试：尝试各种随机金额存款和取款，寻找重入或逻辑错误
    function testFuzz_DepositWithdraw(address user, uint256 amount) public {{
        vm.assume(user != address(0));
        vm.assume(amount > 0 && amount < 100 ether);
        vm.deal(user, amount);

        vm.prank(user);
        (bool success, ) = address(target).call{{value: amount}}("");

        if (success) {{
            vm.prank(user);
            // 尝试重入或异常提款
            (bool wSuccess, ) = address(target).call(abi.encodeWithSignature("withdraw()"));
            // 如果提款成功，余额应该由逻辑保证，这里只是简单探测崩溃
        }}
    }}
}}
"""


def run_fuzz_test(task_dir: Path, contract_path: Path, iteration: int):
    """
    运行 Fuzzer
    返回: (status, message, test_file_path)
    """
    create_foundry_config(task_dir)

    # 1. 准备测试文件路径
    artifacts_dir = task_dir / "artifacts"
    if not artifacts_dir.exists():
        artifacts_dir.mkdir()

    # 计算相对引用路径
    try:
        rel_path = os.path.relpath(contract_path, artifacts_dir)
    except:
        # 如果不在同一盘符等极端情况，回退
        rel_path = f"../{contract_path.name}"

    import_path = rel_path.replace("\\", "/")
    if not import_path.startswith("."):
        import_path = "./" + import_path

    # 假设合约名固定为 Target (后续可优化为解析 AST 获取)
    contract_name = "Target"

    test_code = create_simple_test(contract_name, import_path, iteration)
    test_filename = f"FuzzTest_Round{iteration}.t.sol"
    test_file_path = artifacts_dir / test_filename

    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(test_code)

    # 2. 运行 Docker Foundry
    # 增加 runs 次数提高强度
    fuzz_runs = 500

    # 容器路径映射
    container_test_path = f"/app/artifacts/{test_filename}"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{task_dir.absolute()}:/app",
        "ghcr.io/foundry-rs/foundry:latest",
        "/bin/sh", "-c",
        f"forge test --json --fuzz-runs {fuzz_runs} --match-path {container_test_path} --remappings forge-std/=/opt/foundry/lib/forge-std/src/"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        # 👇 返回文件路径，方便 workflow 读取代码入库
        if result.returncode == 0:
            return "success", "Fuzz Passed", test_file_path
        else:
            return "failed", result.stdout, test_file_path

    except Exception as e:
        return "error", str(e), None