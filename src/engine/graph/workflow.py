import uuid
import re
import os
from langgraph.graph import StateGraph, END
from src.engine.graph.state import AgentState
from src.engine.agents.red_agent import RedAgent
from src.engine.agents.blue_agent import BlueAgent
from src.engine.tools.file_manager import FileManager
from src.engine.tools.slither_runner import run_slither_scan
from src.engine.tools.docker_runner import run_forge_test_json, run_docker_command
# 👇 引入刚才修改的 Fuzzer
from src.engine.tools.fuzzer import run_fuzz_test
from src.db.session import SessionLocal
from src.db.models import Task, TestCase
from src.core.logger import log_to_db


def update_phase(task_id, phase):
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.current_phase = phase
        db.commit()
    db.close()


def get_ver_tag(state: AgentState):
    return f"v{state.get('round_count', 0) + 1}"


# =========================================
# 节点 1: 侦查 (Discovery) - 集成真 Fuzzer
# =========================================
def node_discovery(state: AgentState):
    task_id = state["task_id"]
    ver = get_ver_tag(state)
    round_idx = state.get("round_count", 0)

    update_phase(task_id, f"Discovery ({ver})")
    log_to_db(task_id, f"🔍 [Discovery - {ver}] Starting Slither & Fuzzer analysis...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 1. 静态扫描
    report = run_slither_scan(fm)
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.slither_report = report
        db.commit()

    # 2. 动态模糊测试 (Real Fuzzer)
    log_to_db(task_id, f"🌪️ [Fuzzer - {ver}] Launching Foundry Fuzzing...")

    # 目标合约路径 (Runner 保证了 fm.task.contract_name 是当前最新代码)
    contract_path = fm.task_dir / fm.task.contract_name

    # 运行 Fuzzer
    status, output, test_file_path = run_fuzz_test(fm.task_dir, contract_path, round_idx)

    new_threats_count = 0

    # 如果 Fuzzer 失败，说明发现了漏洞 (Foundry: Fail = Vulnerability Found)
    if status == "failed" and test_file_path and test_file_path.exists():
        # 读取生成的 Fuzz 测试代码
        with open(test_file_path, "r", encoding="utf-8") as f:
            fuzz_code = f.read()

        fuzz_name = f"Fuzz_Crash_{ver}"

        # 查重
        exists = db.query(TestCase).filter_by(task_id=task_id, name=fuzz_name).first()
        if not exists:
            tc = TestCase(
                id=str(uuid.uuid4()), task_id=task_id,
                source="FUZZER",
                name=fuzz_name,
                description=f"Automated Fuzzing Crash in {ver}",
                code=fuzz_code,
                status="FAILING",  # 实锤漏洞，红色
                version_added=ver
            )
            db.add(tc)
            new_threats_count += 1
            log_to_db(task_id, f"🔴 [Matrix] Fuzzer found a crash! Injected: {fuzz_name}")
    elif status == "success":
        log_to_db(task_id, f"🟢 [Fuzzer] No crashes found in {ver}.")

    db.commit()
    db.close()

    return {
        "slither_report": report,
        "new_threats_count": new_threats_count
    }


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

    exploit_code = agent.generate_exploit(state["current_source"], state["slither_report"])

    # 临时文件预检
    temp_filename = f"Temp_Red_{ver}.t.sol"
    fm.save_artifact(temp_filename, exploit_code, "temp")

    log_to_db(task_id, f"⚡ [Red Team] Pre-validating exploit...")

    container_path = f"/app/{temp_filename}"
    cmd = f"forge test --json --match-path {container_path} --remappings forge-std/=/opt/foundry/lib/forge-std/src/"
    stdout, stderr = run_docker_command(fm.task_dir, cmd)
    full_output = (stdout or "") + (stderr or "")

    matches = re.findall(r'\[(PASS|FAIL).*?\]\s+(testExploit_\w+)\(\)', full_output)

    valid_exploits_count = 0
    for status, func_name in matches:
        if status == "PASS":  # 攻击成功 -> 入库
            exists = db.query(TestCase).filter_by(task_id=task_id, name=func_name).first()
            if not exists:
                tc = TestCase(
                    id=str(uuid.uuid4()), task_id=task_id,
                    source="RED_TEAM",
                    name=func_name,
                    description=f"Verified Exploit from {ver}",
                    code=exploit_code,
                    status="FAILING",
                    version_added=ver
                )
                db.add(tc)
                valid_exploits_count += 1
                log_to_db(task_id, f"🔴 [Matrix] Red Team Exploit Verified: {func_name}")

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
    # 检查是否还有任何 FAILING (红色) 的格子
    active_reds = db.query(TestCase).filter(TestCase.task_id == task_id, TestCase.status == "FAILING").count()
    db.close()

    log_to_db(task_id, f"🧐 [Gatekeeper] New Threats: {new_threats} | Total Active Reds: {active_reds}")

    # Condition A: new_threats == 0
    # Condition B: active_reds == 0
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
    next_round = state.get("round_count", 0) + 1
    next_ver = f"v{next_round + 1}"

    update_phase(task_id, f"Fixing ({current_ver} -> {next_ver})")
    log_to_db(task_id, f"🛡️ [Blue Team] Fixing active threats...")

    db = SessionLocal()
    failed_cases = db.query(TestCase).filter(TestCase.task_id == task_id, TestCase.status == "FAILING").all()
    failed_snippets = "\n".join([f"// Exploit {c.name}\n{c.code}" for c in failed_cases[:5]])
    db.close()

    agent = BlueAgent()
    fixed_code = agent.fix_vulnerability(state["current_source"], state["slither_report"], failed_snippets)

    # 覆盖主文件，供下一轮使用
    fm = FileManager(db, task_id)
    fm.save_artifact(fm.task.contract_name, fixed_code)
    fm.save_artifact(f"Backup_{next_ver}.sol", fixed_code)

    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.fixed_code = fixed_code
        db.commit()

    return {"current_source": fixed_code, "round_count": next_round}


# =========================================
# 节点 5: 全量回归 (Regression)
# =========================================
def node_validate_matrix(state: AgentState):
    task_id = state["task_id"]
    current_ver = get_ver_tag(state)

    update_phase(task_id, f"Regression ({current_ver})")
    log_to_db(task_id, f"🧪 [Validation - {current_ver}] Regression testing ALL Matrix cases...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 运行所有 .t.sol (包括红队生成的 和 Fuzzer 生成的)
    results, raw_output = run_forge_test_json(fm.task_dir)

    passed_cnt = 0
    failed_cnt = 0

    all_cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()

    for tc in all_cases:
        # Fuzzer 生成的测试如果 PASS 意味着 Crash 没复现 -> Green
        # RedTeam 生成的测试如果 FAIL 意味着攻击没成功 -> Green

        # 统一逻辑：我们生成的 Test 都是 "攻击脚本"
        # 攻击脚本 PASS = 攻击成功 = 漏洞存在 = RED
        # 攻击脚本 FAIL = 攻击失败 = 防御成功 = GREEN

        if tc.name in results:
            res = results[tc.name]
            if res == "PASS":
                tc.status = "FAILING"
                failed_cnt += 1
            else:
                tc.status = "PASSING"
                passed_cnt += 1

    db.commit()
    db.close()

    log_to_db(task_id, f"📊 [Regression] {passed_cnt} Secure (Green) | {failed_cnt} Vulnerable (Red)")
    return {}


# =========================================
# Graph
# =========================================
def router_decision(state: AgentState):
    status = state.get("execution_status")
    round_count = state.get("round_count", 0)

    if status == "secure":
        log_to_db(state["task_id"], "🏆 [Success] System Secure.")
        return END
    if round_count >= 10:
        log_to_db(state["task_id"], "🚫 [Failure] Max rounds reached.")
        return END
    return "fix"


def create_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("discovery", node_discovery)
    workflow.add_node("weaponize", node_red_weaponize)
    workflow.add_node("check", node_check_termination)
    workflow.add_node("fix", node_blue_fix)
    workflow.add_node("validate", node_validate_matrix)

    workflow.set_entry_point("discovery")
    workflow.add_edge("discovery", "weaponize")
    workflow.add_edge("weaponize", "check")

    workflow.add_conditional_edges("check", router_decision, {"fix": "fix", END: END})

    workflow.add_edge("fix", "validate")
    workflow.add_edge("validate", "discovery")  # 闭环

    return workflow.compile()