import uuid
import re
from langgraph.graph import StateGraph, END
from src.engine.graph.state import AgentState
from src.engine.agents.red_agent import RedAgent
from src.engine.agents.blue_agent import BlueAgent
from src.engine.tools.file_manager import FileManager
from src.engine.tools.slither_runner import run_slither_scan
from src.engine.tools.docker_runner import run_forge_test_json
from src.db.session import SessionLocal
from src.db.models import Task, TestCase
from src.core.logger import log_to_db


def update_phase(task_id, phase):
    """更新任务阶段显示"""
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.current_phase = phase
        db.commit()
    db.close()


# 1. 静态扫描 + 占位
def node_static_scan(state: AgentState):
    task_id = state["task_id"]
    update_phase(task_id, "Static Scan")
    log_to_db(task_id, "🔍 [Static] Starting Slither analysis...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 运行 Slither
    report = run_slither_scan(fm)

    # 如果有报告，生成一个 "PENDING" 的静态发现记录
    if report and report.strip():
        tc = TestCase(
            id=str(uuid.uuid4()), task_id=task_id,
            source="SLITHER", name="Static Analysis Findings",
            description="High/Medium issues detected by Slither",
            status="PENDING", version_added="v1"
        )
        db.add(tc)
        db.commit()

    db.close()
    return {"slither_report": report}


# 2. 红方：生成攻击矩阵
def node_red_attack(state: AgentState):
    task_id = state["task_id"]
    update_phase(task_id, "Red Team Attack")
    log_to_db(task_id, "⚔️ [Red] Generating Attack Matrix...")

    agent = RedAgent()
    exploit_code = agent.generate_exploit(state["current_source"], state["slither_report"])

    db = SessionLocal()
    fm = FileManager(db, task_id)
    fm.save_artifact("ExploitTest.t.sol", exploit_code, "exploit")

    # 识别代码中的测试函数，注册到数据库
    test_funcs = re.findall(r'function\s+(testExploit_\w+)', exploit_code)

    new_count = 0
    for func_name in test_funcs:
        # 查重
        exists = db.query(TestCase).filter_by(task_id=task_id, name=func_name).first()
        if not exists:
            tc = TestCase(
                id=str(uuid.uuid4()), task_id=task_id,
                source="RED_TEAM", name=func_name,
                description="Red Team generated exploit PoC",
                code=exploit_code,  # 这里暂存整个文件，前端展示时可优化
                status="FAILING",  # 默认假设是有效的威胁
                version_added="v1"
            )
            db.add(tc)
            new_count += 1

    db.commit()
    log_to_db(task_id, f"⚔️ [Red] Registered {new_count} new test cases in Matrix.")
    db.close()

    return {"exploit_code": exploit_code}


# 3. 验证矩阵：运行所有用例
def node_validate_matrix(state: AgentState):
    task_id = state["task_id"]
    update_phase(task_id, "Validating Matrix")
    log_to_db(task_id, "🧪 [Matrix] Running all test cases against current contract...")

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 运行 forge test
    results, raw_output = run_forge_test_json(fm.task_dir)

    failed_count = 0  # 红色威胁计数

    for test_name, result in results.items():
        tc = db.query(TestCase).filter_by(task_id=task_id, name=test_name).first()
        if tc:
            # 逻辑定义:
            # Red Team PoC: PASS意味着攻击成功(漏洞存在) -> FAILING(红色)
            # Red Team PoC: FAIL意味着攻击失败(被防御) -> PASSING(绿色)
            if result == "PASS":
                tc.status = "FAILING"  # 威胁生效
                failed_count += 1
            else:
                tc.status = "PASSING"  # 威胁解除

    db.commit()
    db.close()

    log_to_db(task_id, f"📊 [Matrix] Active Threats: {failed_count}.")

    if failed_count > 0:
        return {"execution_status": "needs_fix"}
    else:
        return {"execution_status": "secure"}


# 4. 蓝方：修复
def node_blue_fix(state: AgentState):
    task_id = state["task_id"]
    update_phase(task_id, "Blue Team Fix")
    log_to_db(task_id, "🛡️ [Blue] Patching vulnerabilities based on Matrix...")

    agent = BlueAgent()
    fixed_code = agent.fix_vulnerability(state["current_source"], state["slither_report"], state["exploit_code"])

    db = SessionLocal()
    fm = FileManager(db, task_id)

    # 备份并覆盖
    # fm.save_artifact("Target_v1.sol", state["current_source"])
    fm.save_artifact(fm.task.contract_name, fixed_code)

    db.close()
    return {"current_source": fixed_code}


# --- Router ---
def router_check(state: AgentState):
    status = state.get("execution_status")
    if status == "needs_fix":
        return "blue_fix"
    else:
        log_to_db(state["task_id"], "✅ [Success] All threats mitigated. Contract Secure.")
        return END


# --- Graph ---
def create_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("static_scan", node_static_scan)
    workflow.add_node("red_attack", node_red_attack)
    workflow.add_node("validate_matrix_v1", node_validate_matrix)
    workflow.add_node("blue_fix", node_blue_fix)
    workflow.add_node("validate_matrix_v2", node_validate_matrix)

    workflow.set_entry_point("static_scan")

    workflow.add_edge("static_scan", "red_attack")
    workflow.add_edge("red_attack", "validate_matrix_v1")

    workflow.add_conditional_edges(
        "validate_matrix_v1",
        router_check,
        {
            "blue_fix": "blue_fix",
            END: END
        }
    )

    workflow.add_edge("blue_fix", "validate_matrix_v2")
    workflow.add_edge("validate_matrix_v2", END)  # 暂只修一轮

    return workflow.compile()