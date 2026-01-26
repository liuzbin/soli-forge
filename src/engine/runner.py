import os
import traceback
import threading
from datetime import datetime
from sqlalchemy.orm import Session
from src.db.session import SessionLocal
from src.db.models import Task, StreamLog, TaskArtifact
from src.engine.tools.file_manager import FileManager
from src.engine.graph.workflow import create_graph
# 👇 引入日志工具
from src.core.logger import log_to_db

MAX_CONCURRENT_TASKS = 3
task_semaphore = threading.Semaphore(MAX_CONCURRENT_TASKS)


def update_task_phase(task_id: str, phase_name: str):
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


def archive_logs_to_file(task_id: str):
    db = SessionLocal()
    try:
        logs = db.query(StreamLog).filter(StreamLog.task_id == task_id).order_by(StreamLog.timestamp).all()
        if not logs:
            return

        full_log_content = "\n".join([f"[{log.timestamp}] [{log.level}] {log.content}" for log in logs])

        fm = FileManager(db, task_id)
        log_filename = f"execution_{task_id}.log"
        log_path = fm.task_dir / log_filename

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full_log_content)

        artifact = TaskArtifact(
            task_id=task_id,
            artifact_type="log_file",
            filename=log_filename,
            file_path=str(log_path.relative_to(fm.settings.BASE_DIR)),
            phase="archive"
        )
        db.add(artifact)
        db.commit()
        # 将归档动作也记录到日志
        log_to_db(task_id, f"✅ Logs archived to {log_filename}")

    except Exception as e:
        print(f"❌ Log archive failed: {e}")
    finally:
        db.close()


def run_agent_task(task_id: str):
    print(f"Task {task_id} is waiting for execution slot...")

    # 这一句在获取锁之前，先别写数据库，防止阻塞

    with task_semaphore:
        print(f"🚀 Task {task_id} acquired slot!")
        # 👇 写入数据库，前端可见
        log_to_db(task_id, "🚀 Task acquired execution slot. Initializing environment...", "INFO")

        db = SessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()

        if not task:
            print(f"Task {task_id} not found.")
            db.close()
            return

        task.status = "running"
        task.current_phase = "Initializing"
        db.commit()

        # Initialize State
        fm = FileManager(db, task_id)
        try:
            original_contract_path = fm.original_dir / task.contract_name

            # 👇 打印调试信息到前端
            log_to_db(task_id, f"📂 Reading contract from: {original_contract_path}", "DEBUG")

            with open(original_contract_path, "r", encoding="utf-8") as f:
                original_code = f.read()

        except Exception as e:
            error_msg = f"Could not read original file: {str(e)}"
            print(f"❌ {error_msg}")
            traceback.print_exc()

            # 👇 将错误写入数据库，让用户知道为什么失败
            log_to_db(task_id, f"❌ Critical Error: {error_msg}", "ERROR")

            task.status = "failed"
            task.result_summary = error_msg
            db.commit()
            db.close()
            return

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

        app = create_graph()

        try:
            log_to_db(task_id, "🤖 AI Agents workflow started.", "INFO")

            final_state = app.invoke(initial_state)

            db.expire_all()
            task = db.query(Task).filter(Task.id == task_id).first()

            if task.status == "stopped":
                log_to_db(task_id, "🛑 Task was stopped by user.", "WARNING")
            else:
                status = final_state.get("execution_status", "unknown")

                if status == "stopped":
                    task.status = "stopped"
                    task.result_summary = "Task stopped during execution."
                elif status == "secure" or status == "pass":
                    task.status = "completed"
                    task.result_summary = "All threats mitigated. Contract is secure."
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
                    task.status = "completed" if status == "secure" else "failed"

                task.current_phase = "Finished"
                log_to_db(task_id, f"🏁 Workflow finished with status: {task.status}", "INFO")

        except Exception as e:
            error_msg = f"System Error: {str(e)}"
            print(f"❌ Runner Execution Exception: {e}")
            traceback.print_exc()

            # 👇 异常上报到前端
            log_to_db(task_id, f"❌ Workflow Crash: {error_msg}", "ERROR")

            db.expire_all()
            task = db.query(Task).filter(Task.id == task_id).first()
            if task.status != "stopped":
                task.status = "failed"
                task.result_summary = error_msg
        finally:
            archive_logs_to_file(task_id)

            from sqlalchemy import func
            now = datetime.now()

            task = db.query(Task).filter(Task.id == task_id).first()
            task.finished_at = now

            if task.started_at:
                start_time = task.started_at
                if isinstance(start_time, str):
                    try:
                        start_time = datetime.fromisoformat(str(start_time))
                    except:
                        pass

                if isinstance(start_time, datetime):
                    delta = now - start_time
                    task.duration = int(delta.total_seconds())

            db.commit()
            db.close()