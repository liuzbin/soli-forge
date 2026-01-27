import threading
from sqlalchemy.orm import Session
from sqlalchemy.sql import func  # 👈 必须引入 func
from src.db.models import Task
from src.engine.tools.file_manager import FileManager
from src.engine.runner import run_agent_task


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

            # 👇👇👇 核心修复：记录开始时间，前端计时器才能走动 👇👇👇
            if not task.started_at:
                task.started_at = func.now()

            self.db.commit()

        # 2. 启动线程运行
        thread = threading.Thread(target=run_agent_task, args=(self.task_id,))
        thread.start()