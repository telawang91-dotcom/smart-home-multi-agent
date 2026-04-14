"""
Supervisor Agent + LangGraph 编排

Supervisor 职责:
1. 接收任务，动态分派给 Worker Agent
2. 监控 Agent 执行状态
3. 异常回退与自主重规划
4. 汇总结果，形成最终决策

LangGraph 图结构:
  Supervisor → Perception → Prediction → Decision → Verification
                                                         ↓
                                              (若需重规划) → Supervisor → ...
"""
import time
import uuid
import asyncio
from typing import TypedDict, Annotated, Any, Optional

from langgraph.graph import StateGraph, END

from models.schemas import (
    AgentRole, TaskStatus, AgentThought, AgentMessage,
    OccupancyStatus, SensorData
)
from agents.workers import (
    perception_agent, prediction_agent,
    decision_agent, verification_agent
)
from config import AGENT_CONFIG


# ========== LangGraph State 定义 ==========

class GraphState(TypedDict):
    """LangGraph 状态类型"""
    # 输入
    sensor_data:dict
    scene_id: Optional[str]

    # Agent 输出
    perception: Optional[dict]
    prediction: Optional[dict]
    decision: Optional[dict]
    verification: Optional[dict]

    # 流程控制
    current_agent: str
    task_status: str
    retry_count: int
    error_message: str

    # 可解释性
    thoughts: list[dict]
    messages: list[dict]

    # 历史数据（供预测模型）
    history: Optional[list]

    # 元信息
    pipeline_id: str
    start_time: float
    end_time: Optional[float]


# ========== Supervisor 节点 ==========

async def supervisor_start(state: GraphState) -> GraphState:
    """Supervisor: 任务初始化与分派"""
    thought = AgentThought(
        agent=AgentRole.SUPERVISOR,
        step=0,
        title="任务接收与初始化",
        reasoning="收到传感器数据分析请求，开始分派任务给 Worker Agent",
        conclusion="任务分派: Perception → Prediction → Decision → Verification",
        confidence=1.0,
        data={"pipeline_id": state.get("pipeline_id", "")}
    )

    message = AgentMessage(
        from_agent=AgentRole.SUPERVISOR,
        to_agent=AgentRole.PERCEPTION,
        content="请分析传感器数据，判断房间占用状态",
        data={}
    )

    return {
        **state,
        "current_agent": AgentRole.SUPERVISOR.value,
        "task_status": TaskStatus.RUNNING.value,
        "thoughts": state.get("thoughts", []) + [thought.model_dump()],
        "messages": state.get("messages", []) + [message.model_dump()],
    }


async def supervisor_check(state: GraphState) -> GraphState:
    """Supervisor: 验证后检查，决定是否完成或重规划"""
    verification = state.get("verification", {})
    retry_count = state.get("retry_count", 0)
    max_retries = AGENT_CONFIG["max_retries"]

    requires_replanning = verification.get("requires_replanning", False)
    plan_approved = verification.get("plan_approved", True)

    if requires_replanning and retry_count < max_retries:
        # 需要重规划
        thought = AgentThought(
            agent=AgentRole.SUPERVISOR,
            step=0,
            title="异常回退 - 自主重规划",
            reasoning=(
                f"验证Agent报告方案未通过: {verification.get('issues', [])} | "
                f"冲突检测: {verification.get('conflict_detected', False)} | "
                f"当前重试次数: {retry_count}/{max_retries}"
            ),
            conclusion=f"启动重规划 (第{retry_count + 1}次), 重新从感知Agent开始",
            confidence=0.7,
            data={"retry_count": retry_count + 1, "issues": verification.get("issues", [])}
        )

        message = AgentMessage(
            from_agent=AgentRole.SUPERVISOR,
            to_agent=AgentRole.PERCEPTION,
            content=f"方案验证未通过，启动重规划(第{retry_count + 1}次)，请重新分析",
            data={"reason": "replanning", "issues": verification.get("issues", [])}
        )

        return {
            **state,
            "current_agent": AgentRole.SUPERVISOR.value,
            "task_status": TaskStatus.REPLANNING.value,
            "retry_count": retry_count + 1,
            "perception": None,
            "prediction": None,
            "decision": None,
            "verification": None,
            "thoughts": state.get("thoughts", []) + [thought.model_dump()],
            "messages": state.get("messages", []) + [message.model_dump()],
        }
    else:
        # 完成（无论通过还是超过重试次数）
        status = "approved" if plan_approved else f"completed_with_issues(retries={retry_count})"
        thought = AgentThought(
            agent=AgentRole.SUPERVISOR,
            step=0,
            title="Pipeline 完成",
            reasoning=f"方案状态: {'✅通过' if plan_approved else '⚠️存在问题但已达最大重试次数'}",
            conclusion=f"决策Pipeline完成: {status}",
            confidence=1.0 if plan_approved else 0.6,
            data={"final_status": status}
        )

        return {
            **state,
            "current_agent": AgentRole.SUPERVISOR.value,
            "task_status": TaskStatus.COMPLETED.value,
            "end_time": time.time(),
            "thoughts": state.get("thoughts", []) + [thought.model_dump()],
        }


# ========== 路由函数 ==========

def should_continue_after_perception(state: GraphState) -> str:
    """感知后路由: 有人→预测, 无人→直接决策(节能)"""
    perception = state.get("perception", {})
    occupancy = perception.get("occupancy_status", "empty")

    if occupancy == OccupancyStatus.EMPTY.value:
        # 无人直接跳到决策（节能模式）
        return "decision"
    return "prediction"


def should_continue_after_verification(state: GraphState) -> str:
    """验证后路由: 需重规划→supervisor_check, 否则→supervisor_check"""
    # 统一走 supervisor_check，由 supervisor_check 内部判断
    return "supervisor_check"


def should_replan_or_end(state: GraphState) -> str:
    """Supervisor检查后路由: 重规划→perception, 完成→end"""
    task_status = state.get("task_status", "")
    if task_status == TaskStatus.REPLANNING.value:
        return "perception"
    return END


# ========== 构建 LangGraph ==========

def build_agent_graph() -> StateGraph:
    """
    构建 Multi-Agent 决策图

    流程:
    supervisor_start → perception → [prediction/decision] → decision → verification
                                                                          ↓
                                                              supervisor_check → [replan/end]
    """
    workflow = StateGraph(GraphState)

    # 添加节点
    workflow.add_node("supervisor_start", supervisor_start)
    workflow.add_node("perception", perception_agent)
    workflow.add_node("prediction", prediction_agent)
    workflow.add_node("decision", decision_agent)
    workflow.add_node("verification", verification_agent)
    workflow.add_node("supervisor_check", supervisor_check)

    # 设置入口
    workflow.set_entry_point("supervisor_start")

    # 添加边
    workflow.add_edge("supervisor_start", "perception")

    # 感知后: 有人→预测, 无人→决策
    workflow.add_conditional_edges(
        "perception",
        should_continue_after_perception,
        {
            "prediction": "prediction",
            "decision": "decision",
        }
    )

    workflow.add_edge("prediction", "decision")
    workflow.add_edge("decision", "verification")

    # 验证后统一进入 supervisor_check
    workflow.add_edge("verification", "supervisor_check")

    # supervisor_check: 重规划→perception, 完成→END
    workflow.add_conditional_edges(
        "supervisor_check",
        should_replan_or_end,
        {
            "perception": "perception",
            END: END,
        }
    )

    return workflow.compile()


# ========== 运行入口 ==========

# 全局图实例
agent_graph = build_agent_graph()


async def run_pipeline(
    sensor_data: dict,
    scene_id: str | None = None,
    history: list[float] | None = None,
) -> dict:
    """
    运行完整的 Multi-Agent 决策 Pipeline

    Args:
        sensor_data: 传感器数据字典
        scene_id: 场景ID
        history: 温度历史数据（供 Holt-Winters 预测）

    Returns:
        完整的 Pipeline 状态
    """
    pipeline_id = f"pipeline_{uuid.uuid4().hex[:8]}_{int(time.time())}"

    initial_state: GraphState = {
        "sensor_data": sensor_data,
        "scene_id": scene_id,
        "perception": None,
        "prediction": None,
        "decision": None,
        "verification": None,
        "current_agent": AgentRole.SUPERVISOR.value,
        "task_status": TaskStatus.PENDING.value,
        "retry_count": 0,
        "error_message": "",
        "thoughts": [],
        "messages": [],
        "history": history,
        "pipeline_id": pipeline_id,
        "start_time": time.time(),
        "end_time": None,
    }

    try:
        final_state = await agent_graph.ainvoke(initial_state)
        return final_state
    except Exception as e:
        return {
            **initial_state,
            "task_status": TaskStatus.FAILED.value,
            "error_message": str(e),
            "end_time": time.time(),
        }


async def run_pipeline_streaming(sensor_data: dict, scene_id: str | None = None):
    """
    流式运行 Pipeline，逐步 yield 每个 Agent 的状态更新
    用于 WebSocket 实时推送

    Yields:
        dict: 每步的状态更新事件
    """
    pipeline_id = f"pipeline_{uuid.uuid4().hex[:8]}_{int(time.time())}"

    initial_state: GraphState = {
        "sensor_data": sensor_data,
        "scene_id": scene_id,
        "perception": None,
        "prediction": None,
        "decision": None,
        "verification": None,
        "current_agent": AgentRole.SUPERVISOR.value,
        "task_status": TaskStatus.PENDING.value,
        "retry_count": 0,
        "error_message": "",
        "thoughts": [],
        "messages": [],
        "pipeline_id": pipeline_id,
        "start_time": time.time(),
        "end_time": None,
    }

    try:
        async for event in agent_graph.astream(initial_state):
            for node_name, node_state in event.items():
                yield {
                    "event_type": "agent_update",
                    "node": node_name,
                    "state": node_state,
                    "pipeline_id": pipeline_id,
                    "timestamp": time.time(),
                }
    except Exception as e:
        yield {
            "event_type": "error",
            "error": str(e),
            "pipeline_id": pipeline_id,
            "timestamp": time.time(),
        }