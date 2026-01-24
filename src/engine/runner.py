import os
from sqlalchemy.orm import Session
from src.db.session import SessionLocal
from src.db.models import Task, StreamLog, TaskArtifact
from src.engine.tools.file_manager import FileManager
from src.engine.graph.workflow import create_graph
import threading


MAX_CONCURRENT_TASKS = 3
task_semaphore = threading.Semaphore(MAX_CONCURRENT_TASKS)


# === 辅助函数：更新阶段 ===
def update_task_phase(task_id: str, phase_name: str):
    """供 Workflow 节点调用，实时更新数据库状态"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.current_phase = phase_name
            db.commit()
    except Exception as e:
        print(f"Error updating phase: {e}")
    finally:
        db.close()


# === 辅助函数：日志归档 ===
def archive_logs_to_file(task_id: str):
    """任务结束时，将 DB 日志转存为文件"""
    db = SessionLocal()
    try:
        # 1. 查询所有日志
        logs = db.query(StreamLog).filter(StreamLog.task_id == task_id).order_by(StreamLog.timestamp).all()
        if not logs:
            return

        # 2. 拼接内容
        full_log_content = "\n".join([f"[{log.timestamp}] [{log.level}] {log.content}" for log in logs])

        # 3. 写入文件
        fm = FileManager(db, task_id)
        log_filename = f"execution_{task_id}.log"
        log_path = fm.task_dir / log_filename

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full_log_content)

        # 4. 记录 Artifact (用于历史查询下载)
        artifact = TaskArtifact(
            task_id=task_id,
            artifact_type="log_file",
            filename=log_filename,
            file_path=str(log_path.relative_to(fm.settings.BASE_DIR)),
            phase="archive"
        )
        db.add(artifact)
        db.commit()
        print(f"✅ Logs archived to {log_filename}")

    except Exception as e:
        print(f"❌ Log archive failed: {e}")
    finally:
        db.close()


# === 主运行逻辑 ===
def run_agent_task(task_id: str):
    # 1. 尝试获取执行令牌
    print(f"Task {task_id} is waiting for execution slot...")

    # 这行代码会阻塞，直到有空闲名额
    with task_semaphore:
        print(f"🚀 Task {task_id} acquired slot! Starting execution...")
        """
        后台 Worker 执行 LangGraph 工作流
        """
        db = SessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()

        if not task:
            print(f"Task {task_id} not found.")
            db.close()
            return

        # Update status to running
        task.status = "running"
        task.current_phase = "Initializing"
        db.commit()

        # Initialize State
        fm = FileManager(db, task_id)
        try:
            original_contract_path = fm.original_dir / task.contract_name
            with open(original_contract_path, "r", encoding="utf-8") as f:
                original_code = f.read()
        except Exception as e:
            task.status = "failed"
            task.result_summary = f"Could not read original file: {str(e)}"
            db.commit()
            db.close()
            return

        # Initial Agent State
        initial_state = {
            "task_id": task_id,
            "original_source": original_code,
            "current_source": original_code,
            "current_phase": "static_scan",
            "round_count": 0,
            "consecutive_success": 0,
            "max_retries": 5,
            "max_rounds": 5,
            "slither_report": "",
            "fuzz_logs": "",
            "exploit_code": "",
            "judge_result": "",
            "fix_history": [],
            "execution_status": "running"
        }

        # Execute Graph
        app = create_graph()

        try:
            # Invoke the graph
            final_state = app.invoke(initial_state)

            # 重新拉取最新状态
            db.expire_all()
            task = db.query(Task).filter(Task.id == task_id).first()

            if task.status == "stopped":
                print(f"Task {task_id} was stopped by user (DB check).")
            else:
                status = final_state.get("execution_status", "unknown")

                if status == "stopped":
                    task.status = "stopped"
                    task.result_summary = "Task stopped during execution."

                # 兼容 workflow.py 返回的 "secure" 状态
                elif status == "secure" or status == "pass":
                    task.status = "completed"
                    task.result_summary = "All threats mitigated. Contract is secure."

                # 处理未通过的情况
                elif status == "needs_fix":
                    task.status = "failed"
                    task.result_summary = "Vulnerabilities persist after repair attempts."

                elif status == "fail_timeout":
                    task.status = "failed"
                    task.result_summary = "Max retries reached."
                elif status == "fail_error":
                    task.status = "failed"
                    task.result_summary = "System error during execution."
                else:
                    # 兜底
                    task.status = "completed" if status == "secure" else "failed"

                task.current_phase = "Finished"

        except Exception as e:
            print(f"Runner Exception: {e}")
            db.expire_all()
            task = db.query(Task).filter(Task.id == task_id).first()
            if task.status != "stopped":
                task.status = "failed"
                task.result_summary = f"System Error: {str(e)}"
        finally:
            # === 核心：归档日志 ===
            archive_logs_to_file(task_id)

            from sqlalchemy import func
            task.finished_at = func.now()
            db.commit()
            db.close()
