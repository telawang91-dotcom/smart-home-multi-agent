"""
4 个 Worker Agent 实现
- 感知Agent (Perception): 分析传感器，判断占用
- 预测Agent (Prediction): 温度融合+趋势预测+体感
- 决策Agent (Decision): 制定调控方案
- 验证Agent (Verification): 验证方案合理性

每个 Agent 遵循: 接收任务 → LLM推理 → 调用Tool → 返回结果
"""
import time
from models.schemas import (
    PipelineState, AgentThought, AgentMessage, AgentRole,
    SensorData, PerceptionResult, PredictionResult,
    DecisionResult, VerificationResult, OccupancyStatus
)
from tools.smart_home_tools import (
    analyze_sensors, predict_trend, make_decision, validate_plan,
    TOOL_DEFINITIONS
)
from agents.mock_llm import get_llm


llm = get_llm()


# ========== 感知 Agent ==========

async def perception_agent(state: dict) -> dict:
    """
    Step 1: 感知Agent
    输入: 原始传感器数据
    输出: 占用判断、传感器可靠性、异常检测
    """
    sensor_data = SensorData(**state["sensor_data"]) if isinstance(state["sensor_data"], dict) else state["sensor_data"]

    # 1. LLM 推理（决定调用哪个工具及参数）
    prompt = (
        f"你是智能家居感知Agent。请分析以下传感器数据，判断房间是否有人：\n"
        f"- 房间: {sensor_data.room.value}\n"
        f"- 毫米波雷达: {sensor_data.mmwave_radar}\n"
        f"- PIR红外: {'有运动' if sensor_data.pir else '无运动'}\n"
        f"- 门磁: {'开' if sensor_data.door_contact else '关'}\n"
        f"- 跌倒风险: {'是' if sensor_data.fall_risk else '否'}\n"
        f"请调用 analyze_sensors 工具进行分析。"
    )

    llm_response = await llm.ainvoke(prompt, tools=[TOOL_DEFINITIONS[0]])

    # 2. 执行工具调用
    result = analyze_sensors(sensor_data, detect_anomalies=True)

    # 3. 生成思考过程
    thought = AgentThought(
        agent=AgentRole.PERCEPTION,
        step=1,
        title="占用感知分析",
        reasoning=f"LLM指令: {llm_response.get('content', '')}",
        conclusion=result.reasoning,
        confidence=result.occupancy_probability,
        data={
            "occupancy": result.occupancy_status.value,
            "probability": result.occupancy_probability,
            "anomalies": result.anomalies,
            "reliability": result.sensor_reliability,
        }
    )

    # 4. 生成消息
    message = AgentMessage(
        from_agent=AgentRole.PERCEPTION,
        to_agent=AgentRole.SUPERVISOR,
        content=f"占用判断完成: {result.occupancy_status.value} (概率={result.occupancy_probability:.1%})",
        data=result.model_dump()
    )

    # 5. 更新状态
    thoughts = state.get("thoughts", []) + [thought.model_dump()]
    messages = state.get("messages", []) + [message.model_dump()]

    return {
        **state,
        "perception": result.model_dump(),
        "current_agent": AgentRole.PERCEPTION.value,
        "thoughts": thoughts,
        "messages": messages,
    }


# ========== 预测 Agent ==========

async def prediction_agent(state: dict) -> dict:
    """
    Step 2-3: 预测Agent
    输入: 传感器数据 + 感知结果
    输出: 融合温度、体感指数、趋势预测
    """
    sensor_data = SensorData(**state["sensor_data"]) if isinstance(state["sensor_data"], dict) else state["sensor_data"]
    perception = PerceptionResult(**state["perception"]) if isinstance(state["perception"], dict) else state["perception"]

    # 1. LLM 推理
    prompt = (
        f"你是智能家居预测Agent。基于感知结果和传感器数据，进行温度融合和趋势预测：\n"
        f"- 占用状态: {perception.occupancy_status.value}\n"
        f"- 房间: {sensor_data.room.value}\n"
        f"- 温度: {sensor_data.temperature}\n"
        f"- 湿度: {sensor_data.humidity}%\n"
        f"- 预测功能: {'开启' if sensor_data.prediction_enabled else '关闭'}\n"
        f"请调用 predict_trend 工具进行预测分析。"
    )

    llm_response = await llm.ainvoke(prompt, tools=[TOOL_DEFINITIONS[1]])

    # 2. 执行工具（从 state 传入历史数据供 Holt-Winters 预测）
    history = state.get("history") if state else None
    result = predict_trend(sensor_data, perception, history=history)

    # 3. 思考过程
    thought = AgentThought(
        agent=AgentRole.PREDICTION,
        step=2,
        title="温度融合与趋势预测",
        reasoning=f"LLM指令: {llm_response.get('content', '')}",
        conclusion=result.reasoning,
        confidence=0.85,
        data={
            "fused_temp": result.fused_temp,
            "humidex": result.current_humidex,
            "comfort": result.comfort_level.value,
            "trend": result.trend,
            "predicted_temp": result.predicted_temp_30min,
            "fusion_weights": result.fusion_weights,
        }
    )

    message = AgentMessage(
        from_agent=AgentRole.PREDICTION,
        to_agent=AgentRole.SUPERVISOR,
        content=(
            f"预测完成: 融合温度={result.fused_temp}°C, "
            f"Humidex={result.current_humidex}, "
            f"舒适度={result.comfort_level.value}, "
            f"趋势={result.trend}"
        ),
        data=result.model_dump()
    )

    thoughts = state.get("thoughts", []) + [thought.model_dump()]
    messages = state.get("messages", []) + [message.model_dump()]

    return {
        **state,
        "prediction": result.model_dump(),
        "current_agent": AgentRole.PREDICTION.value,
        "thoughts": thoughts,
        "messages": messages,
    }


# ========== 决策 Agent ==========

async def decision_agent(state: dict) -> dict:
    """
    Step 4-5: 决策Agent
    输入: 感知 + 预测结果
    输出: 调控方案（降温/升温/无动作）
    """
    sensor_data = SensorData(**state["sensor_data"]) if isinstance(state["sensor_data"], dict) else state["sensor_data"]
    perception = PerceptionResult(**state["perception"]) if isinstance(state["perception"], dict) else state["perception"]
    prediction = PredictionResult(**state["prediction"]) if isinstance(state["prediction"], dict) else state["prediction"]

    # 1. LLM 推理
    prompt = (
        f"你是智能家居决策Agent。根据以下分析结果制定温度调控方案：\n"
        f"- 占用: {perception.occupancy_status.value}\n"
        f"- 融合温度: {prediction.fused_temp}°C\n"
        f"- Humidex: {prediction.current_humidex}\n"
        f"- 舒适度: {prediction.comfort_level.value}\n"
        f"- 趋势: {prediction.trend}\n"
        f"- 活动: {sensor_data.activity}\n"
        f"请调用 make_decision 工具制定方案。"
    )

    llm_response = await llm.ainvoke(prompt, tools=[TOOL_DEFINITIONS[2]])

    # 2. 执行工具
    result = make_decision(sensor_data, perception, prediction, consider_prediction=True)

    # 3. 思考过程
    thought = AgentThought(
        agent=AgentRole.DECISION,
        step=4,
        title="调控方案制定",
        reasoning=f"LLM指令: {llm_response.get('content', '')}",
        conclusion=result.reasoning,
        confidence=0.9,
        data={
            "action": result.action,
            "target_temp": result.target_temp,
            "intensity": result.intensity,
            "rooms": result.affected_rooms,
            "estimated_time": result.estimated_time_min,
        }
    )

    message = AgentMessage(
        from_agent=AgentRole.DECISION,
        to_agent=AgentRole.SUPERVISOR,
        content=f"决策完成: {result.action} → 目标{result.target_temp}°C, 强度={result.intensity}",
        data=result.model_dump()
    )

    thoughts = state.get("thoughts", []) + [thought.model_dump()]
    messages = state.get("messages", []) + [message.model_dump()]

    return {
        **state,
        "decision": result.model_dump(),
        "current_agent": AgentRole.DECISION.value,
        "thoughts": thoughts,
        "messages": messages,
    }


# ========== 验证 Agent ==========

async def verification_agent(state: dict) -> dict:
    """
    Step 6: 验证Agent
    输入: 全部结果
    输出: 方案验证、冲突检测、是否需要重规划
    """
    sensor_data = SensorData(**state["sensor_data"]) if isinstance(state["sensor_data"], dict) else state["sensor_data"]
    perception = PerceptionResult(**state["perception"]) if isinstance(state["perception"], dict) else state["perception"]
    prediction = PredictionResult(**state["prediction"]) if isinstance(state["prediction"], dict) else state["prediction"]
    decision = DecisionResult(**state["decision"]) if isinstance(state["decision"], dict) else state["decision"]

    # 1. LLM 推理
    prompt = (
        f"你是智能家居验证Agent。请验证以下调控方案的安全性和合理性：\n"
        f"- 调控动作: {decision.action}\n"
        f"- 目标温度: {decision.target_temp}°C\n"
        f"- 强度: {decision.intensity}\n"
        f"- 当前舒适度: {prediction.comfort_level.value}\n"
        f"- 传感器异常: {perception.anomalies}\n"
        f"- 跌倒风险: {'是' if sensor_data.fall_risk else '否'}\n"
        f"请调用 validate_plan 工具进行验证。"
    )

    llm_response = await llm.ainvoke(prompt, tools=[TOOL_DEFINITIONS[3]])

    # 2. 执行工具
    result = validate_plan(sensor_data, perception, prediction, decision)

    # 3. 思考过程
    thought = AgentThought(
        agent=AgentRole.VERIFICATION,
        step=6,
        title="方案验证与安全检查",
        reasoning=f"LLM指令: {llm_response.get('content', '')}",
        conclusion=result.reasoning,
        confidence=1.0 if result.plan_approved else 0.5,
        data={
            "approved": result.plan_approved,
            "issues": result.issues,
            "suggestions": result.suggestions,
            "conflict": result.conflict_detected,
            "requires_replanning": result.requires_replanning,
        }
    )

    message = AgentMessage(
        from_agent=AgentRole.VERIFICATION,
        to_agent=AgentRole.SUPERVISOR,
        content=(
            f"验证{'✅通过' if result.plan_approved else '❌未通过'}"
            f"{' | 需要重规划' if result.requires_replanning else ''}"
            f"{' | 冲突: ' + result.conflict_details if result.conflict_detected else ''}"
        ),
        data=result.model_dump()
    )

    thoughts = state.get("thoughts", []) + [thought.model_dump()]
    messages = state.get("messages", []) + [message.model_dump()]

    return {
        **state,
        "verification": result.model_dump(),
        "current_agent": AgentRole.VERIFICATION.value,
        "thoughts": thoughts,
        "messages": messages,
    }