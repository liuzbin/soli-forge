from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    # --- 基础上下文 ---
    task_id: str
    original_source: str  # 原始代码 (只读)

    # --- 动态上下文 ---
    current_source: str  # 当前最新版本的合约代码
    current_phase: str  # 'static_scan' (Slither) 或 'fuzz_test' (Foundry)

    # --- 计数器与熔断 ---
    round_count: int  # 总轮次
    consecutive_success: int  # 动态扫描连续通过次数 (用于 fuzz 阶段)
    max_rounds: int  # 最大允许轮次 (防止死循环)

    # --- 报告与日志 ---
    slither_report: str  # 最新 Slither 报告
    fuzz_logs: str  # 最新 Foundry 运行日志 (包含失败详情)
    compiler_error: str  # 如果编译失败，存报错信息

    # --- 攻防中间产物 ---
    exploit_code: str  # 红方生成的攻击代码
    judge_result: str  # 红方判别结果: 'VALID', 'FALSE_POSITIVE', 'SKIP'

    # --- 历史记忆 (核心防死循环机制) ---
    fix_history: List[str]  # 记录过去几轮的修复思路摘要

    # --- 最终状态 ---
    execution_status: str  # 'running', 'pass', 'fail_timeout', 'fail_error'