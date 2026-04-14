"""
全屋智能 Multi-Agent 决策引擎 - 主入口
启动方式: python main.py 或 uvicorn main:app --port 8081
"""
import sys
import os
import asyncio
import logging
from datetime import datetime

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router
from config import AGENT_ENGINE_HOST, AGENT_ENGINE_PORT, HA_ENABLED
from tools.database import init_database, get_recent_decisions, get_sensor_stats
from tools.scheduler import scheduler_loop, stop_scheduler, get_scheduler_status, auto_analyze_room
from tools.notification import get_notification_manager
from tools.ha_websocket import (
  start_ha_websocket, stop_ha_websocket, set_on_sensor_change, get_ws_status,
)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ========== 生命周期管理 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期: 启动时初始化数据库+调度器，关闭时清理"""
    # 启动
    logger.info("初始化数据库...")
    init_database()

    logger.info("启动后台调度器...")
    scheduler_task = asyncio.create_task(scheduler_loop())

    # 注册 HA WebSocket 事件回调
    async def on_sensor_change(room, sensor_type, entity_id, old_value, new_value, is_emergency):
        """传感器变化时触发的回调"""
        nm = get_notification_manager()
        if is_emergency:
            if sensor_type == "fall_sensor":
                await nm.notify_fall_detected(room)
            elif sensor_type == "temperature":
                try:
                    temp = float(new_value)
                    await nm.notify_extreme_temp(room, temp, is_high=temp > 30)
                except ValueError:
                    pass
            # 紧急情况立即分析
            await auto_analyze_room(room, auto_execute=True)
        else:
            # 非紧急：关键传感器变化时触发分析（有冷却期）
            if sensor_type in ("temperature", "presence", "pir"):
                logger.info(f"[事件驱动] {room}/{sensor_type} 变化，触发分析")
                await auto_analyze_room(room, auto_execute=True)

    set_on_sensor_change(on_sensor_change)

    logger.info("启动 HA WebSocket 监听...")
    await start_ha_websocket()

    yield

    # 关闭
    logger.info("停止服务...")
    stop_scheduler()
    stop_ha_websocket()
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    logger.info("Agent Engine 已关闭")


app = FastAPI(
    title="全屋智能 Multi-Agent 决策引擎",
    description=(
        "基于 LangGraph 的分层 Multi-Agent 系统\n\n"
        "架构: Supervisor + 4 Worker Agent（感知、预测、决策、验证）\n"
        "支持动态任务路由、异常回退、自主重规划\n"
        "集成 Home Assistant 桥接 + SQLite 持久化 + 后台定时决策"
    ),
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router, prefix="/api")


# ========== 根路由 + 数据查询接口 ==========

@app.get("/")
async def root():
    return {
        "service": "Smart Home Multi-Agent Engine",
        "version": "3.0.0",
        "architecture": "Supervisor + 4 Workers (Perception, Prediction, Decision, Verification)",
        "framework": "LangGraph + FastAPI + HA Bridge + SQLite",
        "ha_enabled": HA_ENABLED,
        "docs": "/docs",
        "endpoints": {
            "health": "/api/health",
            "analyze": "POST /api/analyze",
            "analyze_stream": "POST /api/analyze/stream",
            "ws_analyze": "WS /api/ws/analyze",
            "ha_status": "/api/ha/status",
            "ha_sensors": "/api/ha/sensors",
            "ha_analyze_live": "POST /api/ha/analyze-live",
            "scenarios": "/api/scenarios",
            "scheduler": "/api/scheduler/status",
            "history_decisions": "/api/data/decisions",
            "history_sensors": "/api/data/sensors/stats/{room}",
        }
    }


@app.get("/api/scheduler/status")
async def api_scheduler_status():
    """获取后台调度器状态"""
    return get_scheduler_status()


@app.get("/api/ha/websocket/status")
async def api_ha_ws_status():
    """获取 HA WebSocket 连接状态"""
    return get_ws_status()


@app.get("/api/data/decisions")
async def api_recent_decisions(room: str = None, limit: int = 20):
    """获取最近的 Agent 决策记录"""
    decisions = get_recent_decisions(room=room, limit=limit)
    return {"count": len(decisions), "decisions": decisions}


@app.get("/api/data/sensors/stats/{room}")
async def api_sensor_stats(room: str, hours: int = 24):
    """获取传感器统计信息"""
    stats = get_sensor_stats(room=room, hours=hours)
    return {"room": room, "hours": hours, "stats": stats}


@app.get("/api/notifications")
async def api_notifications(limit: int = 20):
    """获取通知历史"""
    nm = get_notification_manager()
    return {"notifications": nm.get_history(limit)}


@app.post("/api/notifications/webhook")
async def api_add_webhook(request: dict):
    """添加 Webhook 通知通道（飞书/钉钉/企业微信）"""
    url = request.get("url")
    if not url:
        return {"error": "缺少 url 参数"}
    nm = get_notification_manager()
    nm.add_webhook(url)
    return {"success": True, "webhooks": nm.webhook_urls}


@app.post("/api/daily-report")
async def api_trigger_daily_report():
    """手动触发每日报告"""
    from tools.scheduler import send_daily_report
    await send_daily_report()
    nm = get_notification_manager()
    return {"success": True, "report": nm.get_history(1)}


@app.post("/api/notifications/test")
async def api_test_notification(request: dict = None):
    """发送测试通知"""
    from tools.notification import AlertLevel
    nm = get_notification_manager()
    await nm.notify(
        title="🧪 测试通知",
        message="全屋智能系统通知功能测试成功！",
        level=AlertLevel.INFO,
        data={"test": True, "time": datetime.now().isoformat()},
    )
    return {"success": True, "message": "测试通知已发送"}


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  全屋智能 Multi-Agent 决策引擎 v3.0")
    print("  Supervisor + 4 Workers (LangGraph)")
    print(f"  HTTP:  http://{AGENT_ENGINE_HOST}:{AGENT_ENGINE_PORT}")
    print(f"  Docs:  http://localhost:{AGENT_ENGINE_PORT}/docs")
    print(f"  WS:    ws://localhost:{AGENT_ENGINE_PORT}/api/ws/analyze")
    print(f"  HA:    {'已启用' if HA_ENABLED else '仿真模式'}")
    print("  调度器: 自动采集(60s) + 自动分析(5min)")
    print("=" * 60)

    uvicorn.run(
        "main:app",
        host=AGENT_ENGINE_HOST,
        port=AGENT_ENGINE_PORT,
        reload=True,
        log_level="info",
    )