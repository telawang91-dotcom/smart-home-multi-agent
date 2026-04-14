"""
Home Assistant WebSocket 实时事件订阅
当传感器状态变化时，即时触发 Agent 分析

HA WebSocket API 协议:
  1. 连接 ws://ha:8123/api/websocket
  2. 收到 auth_required → 发送 {"type":"auth","access_token":"..."}
  3. 收到 auth_ok → 订阅状态变化事件
  4. 发送 {"id":1,"type":"subscribe_events","event_type":"state_changed"}
  5. 持续接收 state_changed 事件

参考: https://developers.home-assistant.io/docs/api/websocket
"""
import asyncio
import json
import logging
import time
from datetime import datetime

import httpx

from config import HA_ENABLED, HA_TOKEN, HA_WS_URL, HA_ENTITY_MAP

logger = logging.getLogger("ha_websocket")

# 全局状态
_ws_connected = False
_ws_task: asyncio.Task | None = None
_event_count = 0
_last_event_time = 0

# 需要监听的 entity_id 集合
_watched_entities: set[str] = set()

# 回调函数
_on_sensor_change_callback = None


def _build_watched_entities():
    """从配置中收集需要监听的 entity_id"""
    entities = set()
    for room, mapping in HA_ENTITY_MAP.items():
        for sensor_type, entity_id in mapping.items():
            if entity_id:
                entities.add(entity_id)
    return entities


def _entity_to_room(entity_id: str) -> str | None:
    """从 entity_id 反查房间名"""
    for room, mapping in HA_ENTITY_MAP.items():
        for sensor_type, eid in mapping.items():
            if eid == entity_id:
                return room
    return None


def _entity_to_sensor_type(entity_id: str) -> str | None:
    """从 entity_id 反查传感器类型"""
    for room, mapping in HA_ENTITY_MAP.items():
        for sensor_type, eid in mapping.items():
            if eid == entity_id:
                return sensor_type
    return None


async def _ha_websocket_loop():
    """
    HA WebSocket 主循环
    持续连接并监听状态变化事件
    """
    global _ws_connected, _event_count, _last_event_time

    if not HA_ENABLED or not HA_TOKEN or not HA_WS_URL:
        logger.info("HA WebSocket 未启用（HA_ENABLED=false 或缺少配置）")
        return

    _watched_entities.update(_build_watched_entities())
    logger.info(f"HA WebSocket 监听 {len(_watched_entities)} 个实体")

    msg_id = 1
    retry_delay = 5

    while True:
        try:
            # 使用 httpx 的 WebSocket（需要 websockets 库）
            # 这里用原生 asyncio 实现简单的 WebSocket 客户端
            import websockets

            logger.info(f"连接 HA WebSocket: {HA_WS_URL}")
            async with websockets.connect(HA_WS_URL) as ws:
                # Step 1: 等待 auth_required
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("type") != "auth_required":
                    logger.error(f"意外消息: {msg}")
                    continue

                # Step 2: 发送认证
                await ws.send(json.dumps({
                    "type": "auth",
                    "access_token": HA_TOKEN,
                }))

                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("type") != "auth_ok":
                    logger.error(f"认证失败: {msg}")
                    await asyncio.sleep(retry_delay)
                    continue

                logger.info(f"HA WebSocket 认证成功 (HA {msg.get('ha_version', '?')})")
                _ws_connected = True
                retry_delay = 5  # 重置重连延迟

                # Step 3: 订阅 state_changed 事件
                await ws.send(json.dumps({
                    "id": msg_id,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }))
                msg_id += 1

                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                result = json.loads(raw)
                if result.get("success"):
                    logger.info("已订阅 state_changed 事件")
                else:
                    logger.warning(f"订阅失败: {result}")

                # Step 4: 持续接收事件
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") != "event":
                            continue

                        event_data = msg.get("event", {})
                        if event_data.get("event_type") != "state_changed":
                            continue

                        data = event_data.get("data", {})
                        entity_id = data.get("entity_id", "")

                        # 只处理我们关注的实体
                        if entity_id not in _watched_entities:
                            continue

                        new_state = data.get("new_state", {})
                        old_state = data.get("old_state", {})

                        # 状态没变就跳过
                        if new_state.get("state") == old_state.get("state"):
                            continue

                        _event_count += 1
                        _last_event_time = time.time()

                        room = _entity_to_room(entity_id)
                        sensor_type = _entity_to_sensor_type(entity_id)
                        new_val = new_state.get("state")
                        old_val = old_state.get("state")

                        logger.info(
                            f"[HA事件 #{_event_count}] {entity_id} "
                            f"({room}/{sensor_type}): {old_val} → {new_val}"
                        )

                        # 紧急事件立即处理
                        is_emergency = False
                        if sensor_type == "fall_sensor" and new_val == "on":
                            is_emergency = True
                            logger.critical(f"[紧急] {room} 跌倒检测触发！")
                        elif sensor_type == "temperature":
                            try:
                                temp_val = float(new_val)
                                if temp_val > 35 or temp_val < 12:
                                    is_emergency = True
                                    logger.critical(f"[紧急] {room} 极端温度: {temp_val}°C")
                            except ValueError:
                                pass

                        # 触发回调
                        if _on_sensor_change_callback:
                            asyncio.create_task(
                                _on_sensor_change_callback(
                                    room=room,
                                    sensor_type=sensor_type,
                                    entity_id=entity_id,
                                    old_value=old_val,
                                    new_value=new_val,
                                    is_emergency=is_emergency,
                                )
                            )

                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        logger.error(f"处理事件异常: {e}")

        except ImportError:
            logger.warning("websockets 库未安装，HA WebSocket 功能不可用。安装: pip install websockets")
            return
        except asyncio.CancelledError:
            logger.info("HA WebSocket 任务已取消")
            break
        except Exception as e:
            _ws_connected = False
            logger.warning(f"HA WebSocket 断开: {e}，{retry_delay}s 后重连...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # 指数退避，最长60s

    _ws_connected = False


def set_on_sensor_change(callback):
    """
    注册传感器变化回调
    callback(room, sensor_type, entity_id, old_value, new_value, is_emergency)
    """
    global _on_sensor_change_callback
    _on_sensor_change_callback = callback
    logger.info("已注册传感器变化回调")


async def start_ha_websocket():
    """启动 HA WebSocket 监听（作为后台任务）"""
    global _ws_task
    if _ws_task and not _ws_task.done():
        logger.warning("HA WebSocket 已在运行")
        return

    _ws_task = asyncio.create_task(_ha_websocket_loop())
    logger.info("HA WebSocket 监听任务已启动")


def stop_ha_websocket():
    """停止 HA WebSocket"""
    global _ws_task, _ws_connected
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
    _ws_connected = False
    logger.info("HA WebSocket 已停止")


def get_ws_status() -> dict:
    """获取 WebSocket 连接状态"""
    return {
        "connected": _ws_connected,
        "event_count": _event_count,
        "last_event_time": _last_event_time,
        "watched_entities": len(_watched_entities),
    }
