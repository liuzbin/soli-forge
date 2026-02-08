import uuid
from langgraph.graph import StateGraph, END, START
import os
import json
from langgraph.graph import StateGraph, END
from src.engine.graph.state import AgentState
from src.engine.agents.red_agent import RedAgent
from src.engine.agents.blue_agent import BlueAgent
from src.engine.tools.file_manager import FileManager
from src.engine.tools.slither_runner import run_slither_scan
from src.engine.tools.docker_runner import run_forge_test_json, run_docker_command
from src.engine.tools.fuzzer import run_fuzz_test
from src.db.session import SessionLocal
from src.db.models import Task, TestCase
from src.core.logger import log_to_db


# === 辅助工具 ===
def update_phase(task_id, phase):
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.current_phase = phase
        db.commit()
    db.close()


def get_ver_tag(state: AgentState):
    """
    版本号逻辑：
    - 初始 round_count=0 -> v1
    - 经过 Fix 后 round_count+1 -> v2
    """
    return f"v{state.get('round_count', 0) + 1}"


# =========================================
# 节点 1: 侦查 (Discovery)
# =========================================
def node_discovery(state: AgentState):
    task_id = state["task_id"]
    ver = get_ver_tag(state)
    round_idx = state.get("round_count", 0)

    update_phase(task_id, f"Discovery ({ver})")
    log_to_db(task_id, f"🔍 [Discovery - {ver}] Starting scan...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 1. 静态扫描
    try:
        report = run_slither_scan(fm, ver)
    except Exception as e:
        log_to_db(task_id, f"❌ Slither Error: {str(e)}", "ERROR")
        raise e

    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.slither_report = report
        db.commit()

    # 2. 动态模糊测试
    log_to_db(task_id, f"🌪️ [Fuzzer - {ver}] Running fuzzing...")
    contract_path = fm.task_dir / fm.task.contract_name

    # 运行 Fuzzer
    status, stats, test_file_path = run_fuzz_test(fm.task_dir, contract_path, round_idx)

    # 👇👇👇 关键修复：逻辑漏洞修补 👇👇👇
    # 如果 Fuzzer 连编译都过不去，不能当做 Safe，必须报错！
    if status == "failed" and isinstance(stats, str):
        # run_fuzz_test 在严重错误时 stats 可能是错误信息字符串
        error_msg = f"Fuzzer Critical Failure: {stats}"
        log_to_db(task_id, f"❌ {error_msg}", "ERROR")
        raise Exception(error_msg)

    if status == "failed" and stats.get("runs") == 0:
        error_msg = "Fuzzer failed to run (Compilation Error likely)."
        log_to_db(task_id, f"❌ {error_msg}", "ERROR")
        raise Exception(error_msg)

    # 保存统计数据
    if task:
        fuzzer_data = {
            "total": stats.get("runs", 0),
            "failures": stats.get("failures", 0),
            "status": "Secure" if stats.get("failures", 0) == 0 else "Vulnerable"
        }
        task.fuzzer_report = json.dumps(fuzzer_data)
        db.commit()

    new_threats_count = 0

    # 处理 Fuzzer 结果
    if stats.get("failures", 0) > 0 and test_file_path and test_file_path.exists():
        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                fuzz_code = f.read()

            fuzz_name = f"Fuzz_Crash_{ver}"
            exists = db.query(TestCase).filter_by(task_id=task_id, name=fuzz_name).first()
            if not exists:
                tc = TestCase(
                    id=str(uuid.uuid4()), task_id=task_id,
                    source="FUZZER",
                    name=fuzz_name,
                    description=f"Automated Fuzzing Crash in {ver}",
                    code=fuzz_code,
                    status="FAILING",
                    version_added=ver
                )
                db.add(tc)
                new_threats_count += 1
                log_to_db(task_id, f"🔴 [Matrix] New Fuzzer Exploit Injected: {fuzz_name}")
        except Exception as e:
            log_to_db(task_id, f"⚠️ Failed to read fuzz file: {e}", "WARNING")
    else:
        log_to_db(task_id, f"🟢 [Fuzzer] No crashes found in {ver}. Runs: {stats.get('runs', 0)}")

    db.commit()
    db.close()

    return {"slither_report": report, "new_threats_count": new_threats_count}


# =========================================
# 节点 2: 武器化 (Weaponization)
# =========================================
def node_red_weaponize(state: AgentState):
    task_id = state["task_id"]
    ver = get_ver_tag(state)
    current_new_threats = state.get("new_threats_count", 0)

    update_phase(task_id, f"Red Team ({ver})")
    log_to_db(task_id, f"⚔️ [Red Team - {ver}] Weaponizing static report...")

    agent = RedAgent()
    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 1. 生成攻击代码
    exploit_code = agent.generate_exploit(state["current_source"], state["slither_report"])

    # 👇👇👇 改动 1: 创建标准的 src 和 test 目录 👇👇👇
    src_dir = fm.task_dir / "src"
    test_dir = fm.task_dir / "test"
    src_dir.mkdir(exist_ok=True)
    test_dir.mkdir(exist_ok=True)

    # 👇👇👇 改动 2: 将目标合约写入 src/Target.sol 👇👇👇
    target_sol_path = src_dir / "Target.sol"
    with open(target_sol_path, "w", encoding="utf-8") as f:
        f.write(state["current_source"])

    # 👇👇👇 改动 3: 将攻击脚本写入 test/ 目录 👇👇👇
    temp_filename = f"Red_Exploit_{ver}.t.sol"
    temp_file_path = test_dir / temp_filename

    with open(temp_file_path, "w", encoding="utf-8") as f:
        f.write(exploit_code)

    log_to_db(task_id, f"⚡ [Red Team] Pre-validating exploit...")

    # 👇👇👇 改动 4: 运行命令指向 test/ 目录 👇👇👇
    # 注意：在容器内，fm.task_dir 挂载为 /app
    container_test_path = f"test/{temp_filename}"
    cmd = f"forge test --json --match-path {container_test_path}"

    stdout, stderr = run_docker_command(fm.task_dir, cmd)
    full_output = (stdout or "") + (stderr or "")

    # 3. 编译检查 (保留这个守门员)
    if "Compilation failed" in full_output or "Error:" in full_output or "ParserError" in full_output:
        # 有时候 JSON 混在报错里，我们需要更智能的判断
        # 如果 output 里没有 '{'，那肯定是挂了
        if "{" not in stdout:
            error_msg = f"Red Team Exploit Compilation Failed!\nOutput: {full_output}"
            log_to_db(task_id, f"❌ {error_msg}", "ERROR")
            raise Exception("Red Team Code Compilation Failed. Workflow Halted.")

    valid_exploits_count = 0

    # 4. JSON 解析 (替代 Regex)
    try:
        # 提取 JSON 部分 (防止有其他日志干扰)
        if "{" in stdout:
            json_str = stdout[stdout.find('{'):stdout.rfind('}') + 1]
            data = json.loads(json_str)

            # 遍历 Forge JSON 结构
            # 结构通常是: { "tests/Temp_Red_v1.t.sol": { "test_results": { "testExploit_01": { "status": "Success" } } } }
            for file_path, file_data in data.items():
                test_results = file_data.get("test_results", {})

                for test_name, result in test_results.items():
                    status = result.get("status")

                    # Foundry JSON 中: "Success" = PASS, "Failure" = FAIL
                    if status == "Success":
                        # 🎯 攻击成功！
                        exists = db.query(TestCase).filter_by(task_id=task_id, name=test_name).first()
                        if not exists:
                            tc = TestCase(
                                id=str(uuid.uuid4()), task_id=task_id,
                                source="RED_TEAM",
                                name=test_name,
                                description=f"Verified Exploit from {ver}",
                                code=exploit_code,
                                status="FAILING",
                                version_added=ver
                            )
                            db.add(tc)
                            valid_exploits_count += 1
                            log_to_db(task_id, f"🔴 [Matrix] Verified & Injected: {test_name}")
                    else:
                        # 攻击失败
                        reason = result.get("reason", "Unknown")
                        log_to_db(task_id, f"🗑️ [Red Team] Discarding failed exploit: {test_name} (Reason: {reason})")
        else:
            log_to_db(task_id, f"⚠️ Warning: No JSON output from Forge. Full Output: {full_output}", "WARNING")

    except Exception as e:
        log_to_db(task_id, f"❌ JSON Parse Error in Red Team: {str(e)}", "ERROR")
        # 这里可以选择抛出异常，或者忽略当前轮次

    # 5. 保存有效攻击文件
    if valid_exploits_count > 0:
        perm_filename = f"Red_Exploit_{ver}_{uuid.uuid4().hex[:6]}.t.sol"
        fm.save_artifact(perm_filename, exploit_code, "exploit")

    try:
        os.remove(fm.task_dir / temp_filename)
    except:
        pass

    db.commit()
    db.close()

    total_new_threats = current_new_threats + valid_exploits_count
    return {"new_threats_count": total_new_threats}


# =========================================
# 节点 3: 终止判定 (Gatekeeper)
# =========================================
def node_check_termination(state: AgentState):
    task_id = state["task_id"]
    new_threats = state.get("new_threats_count", 0)

    db = SessionLocal()
    # 查询矩阵中当前还是红色的用例总数
    active_reds = db.query(TestCase).filter(
        TestCase.task_id == task_id,
        TestCase.status == "FAILING"
    ).count()
    db.close()

    log_to_db(task_id, f"🧐 [Gatekeeper] New Threats: {new_threats} | Total Active Reds: {active_reds}")

    if active_reds == 0 and new_threats == 0:
        return {"execution_status": "secure"}
    else:
        return {"execution_status": "needs_fix"}


# =========================================
# 节点 4: 蓝队修复 (Fix)
# =========================================
def node_blue_fix(state: AgentState):
    task_id = state["task_id"]
    current_ver = get_ver_tag(state)

    # 准备生成下一版本
    next_round = state.get("round_count", 0) + 1
    next_ver = f"v{next_round + 1}"

    update_phase(task_id, f"Fixing ({current_ver} -> {next_ver})")
    log_to_db(task_id, f"🛡️ [Blue Team] Fixing all active threats to generate {next_ver}...")

    db = SessionLocal()
    # 提取所有红色用例 (FAILING)
    failed_cases = db.query(TestCase).filter(TestCase.task_id == task_id, TestCase.status == "FAILING").all()

    # 拼接 Prompt
    failed_snippets = "\n".join([f"// Exploit {c.name}\n{c.code}" for c in failed_cases[:3]])
    db.close()

    agent = BlueAgent()
    fixed_code = agent.fix_vulnerability(state["current_source"], state["slither_report"], failed_snippets)

    # ⚠️ 关键操作：覆盖主文件
    fm = FileManager(db, task_id)
    fm.save_artifact(fm.task.contract_name, fixed_code)

    # 备份
    fm.save_artifact(f"Backup_{next_ver}.sol", fixed_code)

    # 更新 DB 供前端 Diff
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.fixed_code = fixed_code
        db.commit()

    return {
        "current_source": fixed_code,
        "round_count": next_round
    }


# =========================================
# 节点 5: 全量验证 (Regression Validation)
# =========================================
def node_validate_matrix(state: AgentState):
    task_id = state["task_id"]
    current_ver = get_ver_tag(state)
    update_phase(task_id, f"Regression ({current_ver})")
    log_to_db(task_id, f"🧪 [Validation - {current_ver}] Regression testing...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 👇👇👇 改动 1: 覆盖 src/Target.sol 为最新代码 (v2/v3) 👇👇👇
    src_dir = fm.task_dir / "src"
    src_dir.mkdir(exist_ok=True)
    target_sol_path = src_dir / "Target.sol"

    with open(target_sol_path, "w", encoding="utf-8") as f:
        f.write(state["current_source"])

    # 👇👇👇 改动 2: 扫描 test/ 目录下的所有测试文件 👇👇👇
    # 这样旧的 Red_Exploit_v1.t.sol (在 test/ 里) 会引用新的 src/Target.sol
    container_pattern = "test/*.t.sol"
    cmd = f"forge test --json {container_pattern}"

    stdout, stderr = run_docker_command(fm.task_dir, cmd)
    full_output = (stdout or "") + (stderr or "")

    # 2. 编译检查 (防止蓝队改坏了代码导致编译不过)
    if "Compilation failed" in full_output or "Error:" in full_output:
        if "{" not in stdout:
            log_to_db(task_id, f"❌ Regression Compilation Failed! Blue Team broke the build.", "ERROR")
            # 这里可以选择抛异常，或者让它进入下一轮修复
            # 为了防止死循环，我们抛出异常让蓝队知道出事了
            raise Exception(f"Regression Compilation Failed: {full_output}")

    passed_cnt = 0
    failed_cnt = 0

    # 3. 解析结果并更新数据库
    try:
        results_map = {}
        if "{" in stdout:
            json_str = stdout[stdout.find('{'):stdout.rfind('}') + 1]
            data = json.loads(json_str)

            # 展平结果：文件名 -> 测试函数 -> 结果
            for file_path, file_data in data.items():
                test_results = file_data.get("test_results", {})
                for test_name, result in test_results.items():
                    results_map[test_name] = result.get("status")  # "Success" or "Failure"

        # 4. 对比数据库中的已知威胁
        all_cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()

        for tc in all_cases:
            # 只关心由于 Red Team 生成的测试用例 (Fuzzer的也可以，但主要是 Red)
            if tc.name in results_map:
                forge_status = results_map[tc.name]

                # 👇👇👇 关键逻辑反转 (Logic Inversion) 👇👇👇
                # 在回归测试中：
                # 如果攻击代码执行 Success -> 说明攻击成功 -> 漏洞依然存在 -> FAILING
                # 如果攻击代码执行 Failure -> 说明攻击失败 (被防住了) -> 漏洞已修复 -> PASSING

                if forge_status == "Success":
                    tc.status = "FAILING"  # 哎呀，还是被攻破了
                    failed_cnt += 1
                    log_to_db(task_id, f"🔴 Vulnerability '{tc.name}' is still active!")
                else:
                    tc.status = "PASSING"  # 好耶，攻击被拦截了
                    passed_cnt += 1
                    log_to_db(task_id, f"🟢 Vulnerability '{tc.name}' mitigated.")
            else:
                # 如果没在结果里找到，可能被过滤了，或者文件丢失
                # 保持原状态，或者标记为 WARNING
                pass

        db.commit()

    except Exception as e:
        log_to_db(task_id, f"❌ Validation Logic Error: {str(e)}", "ERROR")

    db.close()

    log_to_db(task_id, f"📊 [Regression] {passed_cnt} Green (Fixed) | {failed_cnt} Red (Active)")

    # 返回剩余的威胁数量，如果没有威胁了，Router 就会结束任务
    return {"new_threats_count": failed_cnt}


# =========================================
# 路由逻辑 (Check 节点的出口)
# =========================================
def router_decision(state: AgentState):
    status = state.get("execution_status")
    round_count = state.get("round_count", 0)
    max_rounds = 10

    if status == "secure":
        log_to_db(state["task_id"], "🏆 [Success] System Secure. No new threats & All matrix cases passed.")
        return END

    if round_count >= max_rounds:
        log_to_db(state["task_id"], "🚫 [Failure] Max iterations reached. Vulnerabilities persist.")
        return END

    return "fix"


# =========================================
# 图构建 (Graph)
# 👇👇👇 请确保这段代码在文件末尾 👇👇👇
# =========================================
def create_graph():
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("discovery", node_discovery)
    workflow.add_node("weaponize", node_red_weaponize)
    workflow.add_node("check", node_check_termination)
    workflow.add_node("fix", node_blue_fix)
    workflow.add_node("validate", node_validate_matrix)

    # 流程编排 (闭环结构)
    workflow.add_edge(START, "discovery")
    # 1. 侦查 -> 2. 武器化 -> 3. 判定
    workflow.add_edge("discovery", "weaponize")
    workflow.add_edge("weaponize", "check")

    # 3. 判定 -> (Secure/End) OR (Fix)
    workflow.add_conditional_edges(
        "check",
        router_decision,
        {
            "fix": "fix",  # 有红 -> 去修复
            END: END  # 全绿 -> 结束
        }
    )

    # 4. 修复 -> 5. 回归验证 -> 1. 下一轮侦查 (Loop)
    workflow.add_edge("fix", "validate")
    workflow.add_edge("validate", "discovery")  # 强制闭环

    return workflow.compile()