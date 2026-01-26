import threading
from sqlalchemy.orm import Session
from src.db.models import Task
from src.engine.tools.file_manager import FileManager
# 引入 runner 中的主执行逻辑
from src.engine.runner import run_agent_task
from sqlalchemy import func


class TaskManager:
    def __init__(self, db: Session, task_id: str):
        self.db = db
        self.task_id = task_id
        self.file_manager = FileManager(db, task_id)

    def start_execution(self):
        """
        在后台线程启动工作流，避免阻塞 API
        """
        # 1. 更新数据库状态为 running
        task = self.db.query(Task).filter(Task.id == self.task_id).first()
        if task:
            task.status = "running"
            task.current_phase = "Initializing"
            self.db.commit()

        # 2. 启动线程运行 (复用 runner.py 的逻辑)
        thread = threading.Thread(target=run_agent_task, args=(self.task_id,))
        thread.start()

        def start_execution(self):
            task = self.db.query(Task).filter(Task.id == self.task_id).first()
            if task:
                task.status = "running"
                task.current_phase = "Initializing"
                # 记录开始时间
                if not task.started_at:
                    task.started_at = func.now()
                self.db.commit()

            thread = threading.Thread(target=run_agent_task, args=(self.task_id,))
            thread.start()
