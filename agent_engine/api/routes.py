"""
FastAPI 路由 - Agent Engine API
提供 HTTP 接口供 Node.js 中间层调用
支持 SSE 流式推送 Agent 流转事件
"""
import time
import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from models.schemas import (
    SensorData, AnalyzeRequest, AnalyzeResponse,
    AgentEvent, TaskStatus, AgentRole
)
from agents.supervisor import run_pipeline, run_pipeline_streaming
from tools.ha_bridge import (
    fetch_sensor_data, fetch_all_sensor_data,
    execute_climate_control, check_ha_status, get_ha_client,
)
from config import HA_ENABLED, HA_ENTITY_MAP

router = APIRouter()


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "agent-engine", "timestamp": time.time()}


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    同步分析接口 - 一次性返回完整结果
    POST /api/analyze
    """
    start = time.time()

    result = await run_pipeline(
        sensor_data=request.sensor_data.model_dump(),
        scene_id=request.scene_id
    )

    duration_ms = round((time.time() - start) * 1000, 1)

    return AnalyzeResponse(
        pipeline_id=result.get("pipeline_id", ""),
        status=TaskStatus(result.get("task_status", "completed")),
        perception=result.get("perception"),
        prediction=result.get("prediction"),
        decision=result.get("decision"),
        verification=result.get("verification"),
        thoughts=result.get("thoughts", []),
        messages=result.get("messages", []),
        duration_ms=duration_ms,
    )


@router.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """
    SSE 流式分析接口 - 逐步推送每个 Agent 的执行结果
    POST /api/analyze/stream
    返回 Server-Sent Events
    """
    async def event_generator():
        async for event in run_pipeline_streaming(
            sensor_data=request.sensor_data.model_dump(),
            scene_id=request.scene_id
        ):
            # SSE 格式
            data = json.dumps(event, ensure_ascii=False, default=str)
            yield f"data:{data}\n\n"
            # 每个事件之间加小延迟，让前端动画更流畅
            await asyncio.sleep(0.3)

        # 结束信号
        yield f"data: {json.dumps({'event_type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.websocket("/ws/analyze")
async def ws_analyze(websocket: WebSocket):
    """
    WebSocket 分析接口 - 实时双向通信
    前端发送传感器数据，后端逐步推送 Agent 状态
    """
    await websocket.accept()

    try:
        while True:
            # 接收前端发来的传感器数据
            raw = await websocket.receive_text()
            data = json.loads(raw)

            sensor_data = data.get("sensor_data", {})
            scene_id = data.get("scene_id")

            # 发送开始事件
            await websocket.send_json({
                "event_type": "pipeline_start",
                "timestamp": time.time()
            })

            # 流式执行并推送
            async for event in run_pipeline_streaming(
                sensor_data=sensor_data,
                scene_id=scene_id
            ):
                await websocket.send_json(event)
                await asyncio.sleep(0.2)

            # 发送完成事件
            await websocket.send_json({
                "event_type": "pipeline_complete",
                "timestamp": time.time()
            })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "event_type": "error",
                "error": str(e),
                "timestamp": time.time()
            })
        except Exception:
            pass


# ========== Home Assistant 桥接接口 ==========

@router.get("/ha/status")
async def ha_status():
    """检查 Home Assistant 连接状态"""
    status = await check_ha_status()
    return {
        "ha_enabled": HA_ENABLED,
        "connection": status,
        "entity_map": HA_ENTITY_MAP,
    }


@router.get("/ha/sensors/{room}")
async def ha_room_sensors(room: str):
    """从 HA (或仿真) 获取指定房间的传感器数据"""
    if room not in HA_ENTITY_MAP:
        return {"error": f"未知房间: {room}", "available": list(HA_ENTITY_MAP.keys())}
    data = await fetch_sensor_data(room)
    return {"room": room, "sensor_data": data, "source": "ha" if HA_ENABLED else "mock"}


@router.get("/ha/sensors")
async def ha_all_sensors():
    """从 HA (或仿真) 获取所有房间的传感器数据"""
    data = await fetch_all_sensor_data()
    return {"rooms": data, "source": "ha" if HA_ENABLED else "mock"}


@router.post("/ha/control")
async def ha_control(request: dict):
    """
    执行设备控制
    Body: {"room": "bedroom", "target_temp": 25.0, "mode": "cool"}
    """
    room = request.get("room", "bedroom")
    target_temp = request.get("target_temp")
    mode = request.get("mode", "cool")
    if target_temp is None:
        return {"error": "缺少 target_temp"}
    success = await execute_climate_control(room, target_temp, mode)
    return {"success": success, "room": room, "target_temp": target_temp, "mode": mode}


@router.post("/ha/analyze-live")
async def ha_analyze_live(request: dict = None):
    """
    从 HA 拉取实时传感器数据 → 直接运行 Agent Pipeline
    这是产品核心流程: 真实传感器 → Agent 决策 → 设备控制
    """
    room = (request or {}).get("room", "bedroom")
    auto_execute = (request or {}).get("auto_execute", False)

    start = time.time()

    # 1. 从 HA 拉取传感器数据
    sensor_raw = await fetch_sensor_data(room)
    if not sensor_raw:
        return {"error": f"无法获取 {room} 传感器数据"}

    # 2. 组装完整的传感器数据（所有房间温度）
    all_rooms = await fetch_all_sensor_data()
    temperature_map = {}
    for r, rd in all_rooms.items():
        temperature_map[r] = rd.get("temperature", 25.0)

    sensor_payload = {
        "room": room,
        "temperature": temperature_map,
        "humidity": sensor_raw.get("humidity", 55),
        "hour": sensor_raw.get("hour", 15),
        "mmwave_radar": sensor_raw.get("mmwave_radar", "active"),
        "pir": sensor_raw.get("pir", True),
        "door_contact": sensor_raw.get("door_contact", False),
        "fall_risk": sensor_raw.get("fall_risk", False),
        "activity": sensor_raw.get("activity", "sitting"),
        "prediction_enabled": True,
    }

    # 3. 运行 Agent Pipeline
    result = await run_pipeline(sensor_data=sensor_payload, scene_id=None)
    duration_ms = round((time.time() - start) * 1000, 1)

    # 4. 自动执行控制（如果开启）
    control_executed = False
    if auto_execute and result.get("decision"):
        decision = result["decision"]
        if decision.get("action") not in (None, "none"):
            target = decision.get("target_temp")
            mode_map = {"cool": "cool", "heat": "heat", "emergency_cool": "cool"}
            hvac_mode = mode_map.get(decision["action"], "cool")
            if target:
                control_executed = await execute_climate_control(room, target, hvac_mode)

    return {
        "pipeline_id": result.get("pipeline_id", ""),
        "status": result.get("task_status", "completed"),
        "sensor_source": sensor_raw.get("_source", "ha" if HA_ENABLED else "mock"),
        "sensor_data": sensor_payload,
        "perception": result.get("perception"),
        "prediction": result.get("prediction"),
        "decision": result.get("decision"),
        "verification": result.get("verification"),
        "control_executed": control_executed,
        "thoughts": result.get("thoughts", []),
        "duration_ms": duration_ms,
    }


# ========== 预设异常场景 ==========

PRESET_SCENARIOS = {
    "sensor_conflict": {
        "name": "传感器冲突",
        "description": "毫米波雷达显示有人，但PIR未检测到运动 → 验证Agent发现冲突 → Supervisor 重规划",
        "sensor_data": {
            "room": "bedroom",
            "temperature": {"bedroom": 28.5, "living": 25.0, "bathroom": 24.0},
            "humidity": 70,
            "hour": 14,
            "mmwave_radar": "active",
            "pir": False,
            "door_contact": False,
            "fall_risk": False,
            "activity": "sitting",
            "prediction_enabled": True,
        }
    },
    "fall_risk": {
        "name": "跌倒风险",
        "description": "检测到跌倒风险 → 验证Agent触发安全告警 → 紧急处理",
        "sensor_data": {
            "room": "bathroom",
            "temperature": {"bedroom": 25.0, "living": 25.0, "bathroom": 26.0},
            "humidity": 75,
            "hour": 22,
            "mmwave_radar": "active",
            "pir": True,
            "door_contact": True,
            "fall_risk": True,
            "activity": "walking",
            "prediction_enabled": True,
        }
    },
    "empty_room": {
        "name": "无人节能",
        "description": "全屋无人 → 感知Agent判定无人 → 跳过预测直接进入节能决策",
        "sensor_data": {
            "room": "bedroom",
            "temperature": {"bedroom": 26.0, "living": 26.0, "bathroom": 25.0},
            "humidity": 55,
            "hour": 11,
            "mmwave_radar": "idle",
            "pir": False,
            "door_contact": False,
            "fall_risk": False,
            "activity": "sitting",
            "prediction_enabled": True,
        }
    },
    "extreme_heat": {
        "name": "极端高温",
        "description": "室温超过30°C + 高湿度 → 紧急降温模式 → 验证通过紧急方案",
        "sensor_data": {
            "room": "bedroom",
            "temperature": {"bedroom": 32.0, "living": 30.0, "bathroom": 28.0},
            "humidity": 80,
            "hour": 15,
            "mmwave_radar": "active",
            "pir": True,
            "door_contact": False,
            "fall_risk": False,
            "activity": "sitting",
            "prediction_enabled": True,
        }
    },
}


@router.get("/scenarios")
async def list_scenarios():
    """列出所有预设异常场景"""
    return {
        name: {"name": s["name"], "description": s["description"]}
        for name, s in PRESET_SCENARIOS.items()
    }


@router.post("/scenarios/{scenario_id}")
async def run_scenario(scenario_id: str):
    """
    运行预设异常场景
    POST /api/scenarios/{scenario_id}
    """
    if scenario_id not in PRESET_SCENARIOS:
        return {"error": f"未知场景: {scenario_id}", "available": list(PRESET_SCENARIOS.keys())}

    scenario = PRESET_SCENARIOS[scenario_id]
    start = time.time()

    result = await run_pipeline(
        sensor_data=scenario["sensor_data"],
        scene_id=scenario_id
    )

    duration_ms = round((time.time() - start) * 1000, 1)

    return {
        "scenario": scenario["name"],
        "description": scenario["description"],
        "pipeline_id": result.get("pipeline_id", ""),
        "status": result.get("task_status", "completed"),
        "retry_count": result.get("retry_count", 0),
        "perception": result.get("perception"),
        "prediction": result.get("prediction"),
        "decision": result.get("decision"),
        "verification": result.get("verification"),
        "thoughts": result.get("thoughts", []),
        "messages": result.get("messages", []),
        "duration_ms": duration_ms,
    }