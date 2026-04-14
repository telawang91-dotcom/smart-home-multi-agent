"""
后台定时任务调度器
负责:
  1. 定时拉取 HA 传感器数据 → 存入 SQLite
  2. 定时运行 Agent Pipeline → 自动决策
  3. 紧急事件优先处理（跌倒/极端温度）
  4. 定期清理过期数据
"""
import asyncio
import logging
import time
from datetime import datetime

from config import (
    AUTO_ANALYZE_INTERVAL, EMERGENCY_COOLDOWN,
    HA_ENTITY_MAP,
)
from tools.ha_bridge import (
    fetch_sensor_data, fetch_all_sensor_data,
    execute_climate_control, execute_alarm, get_ha_client,
)
from tools.database import (
    init_database, save_sensor_data, save_decision_log,
    get_temperature_history, cleanup_old_data, update_preference,
    get_sensor_stats, get_recent_decisions,
)
from tools.smart_home_tools import compute_humidex
from tools.notification import get_notification_manager
from agents.supervisor import run_pipeline

logger = logging.getLogger("scheduler")

# 全局状态
_scheduler_running = False
_last_analyze_time = {}    # {room: timestamp}
_last_emergency_time = 0
_analyze_count = 0


async def collect_and_store_sensors():
    """
    任务1: 采集所有房间传感器数据并存入数据库
    """
    all_data = await fetch_all_sensor_data()
    stored_count = 0
    for room, data in all_data.items():
        temp = data.get("temperature")
        humidity = data.get("humidity")
        if temp is not None:
            humidex = compute_humidex(temp, humidity or 55)
            occupancy = "occupied" if data.get("mmwave_radar") == "active" else "empty"
            source = data.get("_source", "mock")
            save_sensor_data(
                room=room, temperature=temp, humidity=humidity or 55,
                humidex=humidex, occupancy=occupancy, source=source,
            )
            stored_count += 1
    return stored_count


async def auto_analyze_room(room: str, auto_execute: bool = False) -> dict | None:
    """
    任务2: 对指定房间运行完整 Agent Pipeline
    """
    global _analyze_count

    # 获取传感器数据
    sensor_raw = await fetch_sensor_data(room)
    if not sensor_raw or sensor_raw.get("temperature") is None:
        logger.warning(f"[{room}] 无法获取传感器数据，跳过分析")
        return None

    # 获取所有房间温度
    all_data = await fetch_all_sensor_data()
    temperature_map = {r: d.get("temperature", 25.0) for r, d in all_data.items()}

    # 获取历史温度（供预测模型）
    history = get_temperature_history(room, minutes=120)

    sensor_payload = {
        "room": room,
        "temperature": temperature_map,
        "humidity": sensor_raw.get("humidity", 55),
        "hour": sensor_raw.get("hour", datetime.now().hour),
        "mmwave_radar": sensor_raw.get("mmwave_radar", "idle"),
        "pir": sensor_raw.get("pir", False),
        "door_contact": sensor_raw.get("door_contact", False),
        "fall_risk": sensor_raw.get("fall_risk", False),
        "activity": sensor_raw.get("activity", "sitting"),
        "prediction_enabled": True,
    }

    start = time.time()
    result = await run_pipeline(
        sensor_data=sensor_payload,
        scene_id=None,
        history=history,
    )
    duration_ms = round((time.time() - start) * 1000, 1)

    # 存储决策日志
    pipeline_id = result.get("pipeline_id", "")
    control_executed = False

    # 自动执行控制
    if auto_execute and result.get("decision"):
        decision = result["decision"]
        if decision.get("action") not in (None, "none"):
            target = decision.get("target_temp")
            mode_map = {"cool": "cool", "heat": "heat", "emergency_cool": "cool"}
            hvac_mode = mode_map.get(decision["action"], "cool")
            if target:
                control_executed = await execute_climate_control(room, target, hvac_mode)

    save_decision_log(pipeline_id, room, result, duration_ms, control_executed)

    # 更新用户偏好（当 Agent 决策舒适时记录当前温度作为偏好）
    prediction = result.get("prediction", {})
    if prediction.get("comfort_level") == "comfortable":
        update_preference(
            room=room,
            hour=datetime.now().hour,
            activity=sensor_raw.get("activity", "sitting"),
            actual_temp=prediction.get("fused_temp", 25.0),
            humidity=sensor_raw.get("humidity"),
        )

    _analyze_count += 1
    _last_analyze_time[room] = time.time()

    logger.info(
        f"[{room}] 分析完成 #{_analyze_count} | "
        f"{prediction.get('comfort_level','?')} | "
        f"动作={result.get('decision', {}).get('action', 'none')} | "
        f"控制={'✓' if control_executed else '✗'} | "
        f"{duration_ms}ms"
    )

    return result


async def check_emergencies():
    """
    任务3: 紧急事件检测
    - 跌倒检测 → 立即告警
    - 极端温度 → 紧急降温/升温
    """
    global _last_emergency_time

    now = time.time()
    if now - _last_emergency_time < EMERGENCY_COOLDOWN:
        return  # 冷却期内不重复处理

    nm = get_notification_manager()
    all_data = await fetch_all_sensor_data()
    for room, data in all_data.items():
        # 跌倒检测
        if data.get("fall_risk"):
            _last_emergency_time = now
            logger.critical(f"[紧急] {room} 检测到跌倒风险！")
            await nm.notify_fall_detected(room)
            await execute_alarm(f"紧急告警：{room} 检测到疑似跌倒事件，请立即确认！")
            await auto_analyze_room(room, auto_execute=True)
            return

        # 极端温度
        temp = data.get("temperature")
        if temp and temp > 35:
            _last_emergency_time = now
            logger.critical(f"[紧急] {room} 温度过高: {temp}°C")
            await nm.notify_extreme_temp(room, temp, is_high=True)
            await execute_climate_control(room, 24.0, "cool")
            return

        if temp and temp < 12:
            _last_emergency_time = now
            logger.critical(f"[紧急] {room} 温度过低: {temp}°C")
            await nm.notify_extreme_temp(room, temp, is_high=False)
            await execute_climate_control(room, 22.0, "heat")
            return


# ========== 主调度循环 ==========

async def scheduler_loop():
    """
    主调度循环 — 在 FastAPI 启动时作为后台任务运行

    每个周期:
      1. 采集传感器数据 → 存储
      2. 检查紧急事件
      3. 每 AUTO_ANALYZE_INTERVAL 秒对所有房间运行 Agent 分析
      4. 每天凌晨 3 点清理过期数据
    """
    global _scheduler_running
    _scheduler_running = True

    logger.info(f"调度器启动 | 采集间隔=60s | 分析间隔={AUTO_ANALYZE_INTERVAL}s")

    # 初始化数据库
    init_database()

    last_cleanup = 0
    last_daily_report = 0
    cycle = 0

    while _scheduler_running:
        try:
            cycle += 1
            now = time.time()
            current_hour = datetime.now().hour

            # 1. 每 60 秒采集一次传感器数据
            stored = await collect_and_store_sensors()
            if cycle % 10 == 0:
                logger.info(f"采集周期 #{cycle} | 存储 {stored} 条传感器数据")

            # 2. 紧急事件检测（每次都检查）
            await check_emergencies()

            # 3. 定时分析（每 AUTO_ANALYZE_INTERVAL 秒）
            for room in HA_ENTITY_MAP:
                last_time = _last_analyze_time.get(room, 0)
                if now - last_time >= AUTO_ANALYZE_INTERVAL:
                    await auto_analyze_room(room, auto_execute=True)

            # 4. 每天清理一次过期数据
            if now - last_cleanup > 86400:
                cleanup_old_data()
                last_cleanup = now

            # 5. 每日报告（21:00 推送）
            if current_hour == 21 and now - last_daily_report > 72000:
                last_daily_report = now
                await send_daily_report()

        except Exception as e:
            logger.error(f"调度器异常: {e}", exc_info=True)

        # 等待下一个周期
        await asyncio.sleep(60)


async def send_daily_report():
    """
    每日报告 — 汇总当天的传感器数据和决策记录
    """
    nm = get_notification_manager()
    rooms_summary = {}

    for room in HA_ENTITY_MAP:
        stats = get_sensor_stats(room, hours=24)
        decisions = get_recent_decisions(room=room, limit=100)
        today_decisions = [
            d for d in decisions
            if time.time() - d.get("timestamp", 0) < 86400
        ]

        comfort_count = sum(
            1 for d in today_decisions
            if d.get("comfort_level") == "comfortable"
        )
        total = len(today_decisions) or 1

        rooms_summary[room] = {
            "avg_temp": round(stats.get("avg_temp", 0), 1) if stats.get("count", 0) > 0 else "N/A",
            "avg_humidity": round(stats.get("avg_humidity", 0), 0) if stats.get("count", 0) > 0 else "N/A",
            "decision_count": len(today_decisions),
            "comfort_rate": round(comfort_count / total * 100),
            "emergency_count": sum(
                1 for d in today_decisions
                if d.get("action") == "emergency_cool"
            ),
        }

    total_decisions = sum(r["decision_count"] for r in rooms_summary.values())
    total_emergencies = sum(r["emergency_count"] for r in rooms_summary.values())
    controls = sum(
        1 for d in get_recent_decisions(limit=200)
        if d.get("control_executed") and time.time() - d.get("timestamp", 0) < 86400
    )

    await nm.notify_daily_summary({
        "total_analyses": total_decisions,
        "controls_executed": controls,
        "emergencies": total_emergencies,
        "rooms": rooms_summary,
    })
    logger.info(f"每日报告已推送 | 分析={total_decisions} 控制={controls} 紧急={total_emergencies}")


def stop_scheduler():
    """停止调度器"""
    global _scheduler_running
    _scheduler_running = False
    logger.info("调度器已停止")


def get_scheduler_status() -> dict:
    """获取调度器状态"""
    return {
        "running": _scheduler_running,
        "analyze_count": _analyze_count,
        "last_analyze": _last_analyze_time,
        "interval_seconds": AUTO_ANALYZE_INTERVAL,
    }