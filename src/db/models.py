from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True)
    hashed_password = Column(String(255))
    username = Column(String(255), nullable=True)  # 确保有这个字段
    is_active = Column(Boolean, default=True)
    role = Column(String(50), default='user')
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tasks = relationship("Task", back_populates="owner")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(100))
    status = Column(String(20), default="created")

    contract_name = Column(String(100), nullable=True)

    # 存储代码资产
    source_code = Column(Text, nullable=True)
    exploit_code = Column(Text, nullable=True)
    fixed_code = Column(Text, nullable=True)

    # 报告存储
    slither_report = Column(Text, nullable=True)

    # 流程控制
    current_phase = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)  # 之前加的字段

    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="tasks")

    # 👇👇👇 关键修复点：这几行必须存在！否则报 500 错误 👇👇👇
    test_cases = relationship("TestCase", back_populates="task", cascade="all, delete-orphan")
    logs = relationship("StreamLog", back_populates="task", cascade="all, delete-orphan")
    artifacts = relationship("TaskArtifact", back_populates="task", cascade="all, delete-orphan")


class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(String(36), primary_key=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"))

    source = Column(String(50))
    name = Column(String(200))
    description = Column(Text, nullable=True)
    code = Column(Text, nullable=True)
    status = Column(String(20), default="PENDING")
    version_added = Column(String(10), default="v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 这里的 back_populates="test_cases" 必须对应 Task 类里的属性名
    task = relationship("Task", back_populates="test_cases")


class StreamLog(Base):
    __tablename__ = "stream_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"))
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    level = Column(String(10), default="INFO")
    content = Column(Text)

    # 这里的 back_populates="logs" 必须对应 Task 类里的属性名
    task = relationship("Task", back_populates="logs")


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"))
    artifact_type = Column(String(50))
    filename = Column(String(255))
    file_path = Column(Text)
    phase = Column(String(50))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 新增关联
    task = relationship("Task", back_populates="artifacts")