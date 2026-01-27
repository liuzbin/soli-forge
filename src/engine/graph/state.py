from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    # --- 基础上下文 ---
    task_id: str
    original_source: str  # 原始代码 (只读)

    # --- 动态上下文 ---
    current_source: str  # 当前最新版本的合约代码
    current_phase: str  # 当前阶段描述

    # --- 计数器与熔断 ---
    round_count: int  # 总轮次
    max_rounds: int  # 最大允许轮次

    # 👇👇👇 新增字段：本轮新增威胁数 👇👇👇
    # 用于 Check 节点判定 (Condition A)
    new_threats_count: int

    # --- 报告与日志 ---
    slither_report: str  # 最新 Slither 报告

    # --- 攻防中间产物 ---
    exploit_code: str  # 红方生成的攻击代码 (临时)

    # --- 最终状态 ---
    execution_status: str  # 'secure', 'needs_fix', 'running'等