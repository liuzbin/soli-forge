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
    log_to_db(task_id, f"🔍 [Discovery - {ver}] Starting fresh scan on {ver}...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 1. 静态扫描 (Slither)
    try:
        report = run_slither_scan(fm, ver)
    except TypeError as e:
        error_msg = f"Slither Call Error: {str(e)}"
        log_to_db(task_id, f"❌ {error_msg}", "ERROR")
        raise e

    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.slither_report = report
        db.commit()

    # 2. 动态模糊测试 (Fuzzer)
    log_to_db(task_id, f"🌪️ [Fuzzer - {ver}] Running fuzzing campaign...")

    contract_path = fm.task_dir / fm.task.contract_name

    # 👇👇👇 修改点：接收 stats 👇👇👇
    status, stats, test_file_path = run_fuzz_test(fm.task_dir, contract_path, round_idx)

    new_threats_count = 0

    # 👇👇👇 修改点：将统计数据写入数据库供前端显示 👇👇👇
    if task:
        # 构造前端需要的 JSON 格式，例如 {"total": 500, "failures": 0}
        import json
        fuzzer_data = {
            "total": stats.get("runs", 0),
            "failures": stats.get("failures", 0),
            "status": "Secure" if stats.get("failures", 0) == 0 else "Vulnerable"
        }
        # 将其存入 task.fuzzer_report (假设前端读这个)
        # 或者如果你有专门的字段，请存入专门字段
        # 这里我们存入 fuzzer_report，覆盖之前的文本
        task.fuzzer_report = json.dumps(fuzzer_data)
        db.commit()

    if status == "success" and stats.get("failures", 0) > 0:
        # 这种情况通常是因为 Fuzzer 跑通了，但是发现了 Bug (status=success指执行成功)
        # 我们需要在 Fuzzer 代码里把 status 标为 success，但 workflow 里判断 failures > 0
        pass

        # 逻辑修正：如果 Fuzzer 发现了漏洞，Foundry 的 status 通常也是 Success (指测试运行完成)，
    # 但具体的 test case status 是 Failed。
    # 我们在 fuzzer.py 里已经处理了：如果 test_data status != Success -> stats['failures'] = 1

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
        log_to_db(task_id, f"🟢 [Fuzzer] No crashes found in {ver}. Runs: {stats.get('runs')}")

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

    # 1. 生成攻击代码
    exploit_code = agent.generate_exploit(state["current_source"], state["slither_report"])

    # 2. 临时保存用于预检
    temp_filename = f"Temp_Red_{ver}.t.sol"
    fm.save_artifact(temp_filename, exploit_code, "temp")

    # 3. 预检 (Pre-Check): 攻击是否奏效？
    log_to_db(task_id, f"⚡ [Red Team] Pre-validating exploit effectiveness...")

    # 仅运行这个临时测试文件
    container_path = f"/app/{temp_filename}"
    cmd = f"forge test --json --match-path {container_path} --remappings forge-std/=/opt/foundry/lib/forge-std/src/"

    stdout, stderr = run_docker_command(fm.task_dir, cmd)
    full_output = (stdout or "") + (stderr or "")

    # 解析结果
    matches = re.findall(r'\[(PASS|FAIL).*?\]\s+(testExploit_\w+)\(\)', full_output)

    valid_exploits_count = 0

    for status, func_name in matches:
        if status == "PASS":
            # 🎯 攻击成功 (PASS) -> 漏洞存在 -> 注入矩阵 (标红)
            exists = db.query(TestCase).filter_by(task_id=task_id, name=func_name).first()
            if not exists:
                tc = TestCase(
                    id=str(uuid.uuid4()), task_id=task_id,
                    source="RED_TEAM",
                    name=func_name,
                    description=f"Verified Exploit from {ver}",
                    code=exploit_code,
                    status="FAILING",  # 直接标红
                    version_added=ver
                )
                db.add(tc)
                valid_exploits_count += 1
                log_to_db(task_id, f"🔴 [Matrix] Red Team Exploit Verified & Injected: {func_name}")
        else:
            # 攻击失败 -> 误报或无效 -> 丢弃
            log_to_db(task_id, f"🗑️ [Red Team] Discarding ineffective exploit: {func_name}")

    # 如果有有效攻击，保存为正式文件供后续回归测试
    if valid_exploits_count > 0:
        perm_filename = f"Red_Exploit_{ver}_{uuid.uuid4().hex[:6]}.t.sol"
        fm.save_artifact(perm_filename, exploit_code, "exploit")

    try:
        os.remove(fm.task_dir / temp_filename)
    except:
        pass

    db.commit()
    db.close()

    # 更新本轮新增威胁计数 (Fuzzer + RedTeam)
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
    # 此时代码已经是 VN+1 了
    current_ver = get_ver_tag(state)

    update_phase(task_id, f"Regression ({current_ver})")
    log_to_db(task_id, f"🧪 [Validation - {current_ver}] Running regression test on ALL Matrix cases...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 运行目录下所有的 .t.sol (包括历史累积的所有攻击脚本)
    results, raw_output = run_forge_test_json(fm.task_dir)

    passed_cnt = 0  # 绿
    failed_cnt = 0  # 红

    all_cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()

    for tc in all_cases:
        if tc.name in results:
            res = results[tc.name]
            # Foundry 逻辑:
            # PASS = 断言成立 = 攻击成功 = 漏洞存在 = RED
            # FAIL = 断言失败 = 攻击被阻 = 防御成功 = GREEN

            if res == "PASS":
                tc.status = "FAILING"
                failed_cnt += 1
            else:
                tc.status = "PASSING"
                passed_cnt += 1

    db.commit()
    db.close()

    log_to_db(task_id, f"📊 [Regression Result] {passed_cnt} Green (Secure) | {failed_cnt} Red (Vulnerable)")

    return {}


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
    workflow.set_entry_point("discovery")

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