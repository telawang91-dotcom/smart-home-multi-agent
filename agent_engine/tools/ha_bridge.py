"""
Home Assistant 桥接层
负责与 HA 的双向通信：
  1. 读取传感器数据 (REST API)
  2. 控制设备 (Service Call)
  3. WebSocket 实时事件订阅

支持两种模式:
  - HA_ENABLED=true  → 真实连接 Home Assistant
  - HA_ENABLED=false → 返回虚拟数据（仿真模式，兼容无 HA 环境）

参考:
  - HA REST API: https://developers.home-assistant.io/docs/api/rest
  - HA WebSocket: https://developers.home-assistant.io/docs/api/websocket
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

import httpx

from config import (
    HA_URL, HA_TOKEN, HA_ENABLED, HA_WS_URL,
    HA_ENTITY_MAP, HA_GLOBAL_ENTITIES,
)

logger = logging.getLogger("ha_bridge")


# ========== HA REST API 客户端 ==========

class HAClient:
    """Home Assistant REST API 客户端"""

    def __init__(self):
        self.base_url = HA_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=10.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------- 读取状态 ----------

    async def get_state(self, entity_id: str) -> dict | None:
        """
        获取单个 entity 的状态
        GET /api/states/<entity_id>
        返回: {"entity_id": "...", "state": "25.3", "attributes": {...}}
        """
        if not entity_id:
            return None
        try:
            client = await self._get_client()
            resp = await client.get(f"/api/states/{entity_id}")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"HA get_state {entity_id} → HTTP {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"HA get_state {entity_id} 失败: {e}")
            return None

    async def get_states_batch(self, entity_ids: list[str]) -> dict[str, Any]:
        """批量获取多个 entity 状态"""
        results = {}
        tasks = [self.get_state(eid) for eid in entity_ids if eid]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for eid, resp in zip([e for e in entity_ids if e], responses):
            if isinstance(resp, dict):
                results[eid] = resp
            else:
                results[eid] = None
        return results

    # ---------- 传感器数据聚合 ----------

    async def get_room_sensor_data(self, room: str) -> dict:
        """
        获取指定房间的全部传感器数据
        返回格式与 SensorData 兼容
        """
        entity_map = HA_ENTITY_MAP.get(room, {})
        if not entity_map:
            logger.warning(f"房间 {room} 未配置 HA Entity 映射")
            return {}

        # 收集需要查询的 entity_id
        entities_to_query = {
            k: v for k, v in entity_map.items() if v
        }

        states = await self.get_states_batch(list(entities_to_query.values()))

        # 解析传感器值
        data = {"room": room}

        # 温度
        temp_eid = entities_to_query.get("temperature")
        if temp_eid and states.get(temp_eid):
            try:
                data["temperature"] = float(states[temp_eid]["state"])
            except (ValueError, KeyError):
                pass

        # 湿度
        hum_eid = entities_to_query.get("humidity")
        if hum_eid and states.get(hum_eid):
            try:
                data["humidity"] = float(states[hum_eid]["state"])
            except (ValueError, KeyError):
                pass

        # 人体存在（毫米波雷达 / Aqara FP2 等）
        pres_eid = entities_to_query.get("presence")
        if pres_eid and states.get(pres_eid):
            state_val = states[pres_eid]["state"]
            data["mmwave_radar"] = "active" if state_val == "on" else "idle"

        # PIR 运动传感器
        pir_eid = entities_to_query.get("pir")
        if pir_eid and states.get(pir_eid):
            data["pir"] = states[pir_eid]["state"] == "on"

        # 门磁
        door_eid = entities_to_query.get("door")
        if door_eid and states.get(door_eid):
            data["door_contact"] = states[door_eid]["state"] == "on"

        # 跌倒检测
        fall_eid = entities_to_query.get("fall_sensor")
        if fall_eid and states.get(fall_eid):
            data["fall_risk"] = states[fall_eid]["state"] == "on"

        # 时间
        data["hour"] = datetime.now().hour

        return data

    async def get_all_rooms_data(self) -> dict:
        """获取所有房间的传感器数据"""
        all_data = {}
        for room in HA_ENTITY_MAP:
            room_data = await self.get_room_sensor_data(room)
            if room_data:
                all_data[room] = room_data
        return all_data

    # ---------- 设备控制 ----------

    async def call_service(
        self, domain: str, service: str, data:dict
    ) -> bool:
        """
        调用 HA Service
        POST /api/services/<domain>/<service>
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                f"/api/services/{domain}/{service}",
                json=data,
            )
            if resp.status_code in (200, 201):
                logger.info(f"HA service {domain}.{service} 调用成功: {data}")
                return True
            logger.warning(f"HA service 调用失败: HTTP {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"HA service 调用异常: {e}")
            return False

    async def set_temperature(
        self, room: str, target_temp: float, hvac_mode: str = "cool"
    ) -> bool:
        """设置空调温度"""
        entity_map = HA_ENTITY_MAP.get(room, {})
        climate_id = entity_map.get("climate")
        if not climate_id:
            logger.warning(f"房间 {room} 未配置 climate entity")
            return False

        return await self.call_service("climate", "set_temperature", {
            "entity_id": climate_id,
            "temperature": target_temp,
            "hvac_mode": hvac_mode,
        })

    async def set_hvac_mode(self, room: str, mode: str) -> bool:
        """设置空调模式 (cool/heat/auto/off)"""
        entity_map = HA_ENTITY_MAP.get(room, {})
        climate_id = entity_map.get("climate")
        if not climate_id:
            return False

        return await self.call_service("climate", "set_hvac_mode", {
            "entity_id": climate_id,
            "hvac_mode": mode,
        })

    async def turn_off_climate(self, room: str) -> bool:
        """关闭空调"""
        return await self.set_hvac_mode(room, "off")

    async def set_humidity(self, target_humidity: float) -> bool:
        """控制除湿机"""
        dehumidifier_id = HA_GLOBAL_ENTITIES.get("dehumidifier")
        if not dehumidifier_id:
            logger.warning("未配置除湿机 entity")
            return False

        return await self.call_service("humidifier", "set_humidity", {
            "entity_id": dehumidifier_id,
            "humidity": target_humidity,
        })

    async def trigger_alarm(self, message: str = "") -> bool:
        """触发告警"""
        alarm_id = HA_GLOBAL_ENTITIES.get("alarm")
        if not alarm_id:
            logger.warning("未配置告警 entity")
            return False

        return await self.call_service("notify", "notify", {
            "message": message or "智能家居系统告警",
            "title": "全屋智能告警",
        })

    # ---------- 连接检测 ----------

    async def check_connection(self) -> dict:
        """检测 HA 连接状态"""
        try:
            client = await self._get_client()
            resp = await client.get("/api/")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "connected": True,
                    "version": data.get("version", "unknown"),
                    "message": data.get("message", ""),
                }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e),
            }
        return {"connected": False, "error": "Unknown"}


# ========== 仿真模式客户端 ==========

class MockHAClient:
    """
    虚拟 HA 客户端 — 不连接真实 HA，返回模拟数据
    用于开发/演示/无 HA 环境
    """

    def __init__(self):
        self._mock_temps = {"bedroom": 26.5, "living": 25.0, "bathroom": 24.5}
        self._mock_humidity = {"bedroom": 58, "living": 55, "bathroom": 70}
        self._ac_setpoints = {"bedroom": 25.0, "living": 25.0, "bathroom": None}
        self._ac_modes = {"bedroom": "cool", "living": "auto", "bathroom": "off"}

    async def close(self):
        pass

    async def get_room_sensor_data(self, room: str) -> dict:
        """返回模拟传感器数据（基于物理模型，不会漂移）"""
        import math
        import random
        hour = datetime.now().hour

        # 基准温度（不累积，每次从基准计算）
        base_temps = {"bedroom": 26.5, "living": 25.0, "bathroom": 24.5}
        base_temp = base_temps.get(room, 25.0)

        # 日温变化（正弦模型）
        if 6 <= hour <= 18:
            daily_variation = 2.0 * math.sin(math.pi * (hour - 6) / 12)
        else:
            daily_variation = -1.0

        # 小噪声（不累积）
        noise = random.uniform(-0.3, 0.3)
        temp = round(base_temp + daily_variation + noise, 1)

        # 湿度（从基准计算，不累积）
        base_humidities = {"bedroom": 58, "living": 55, "bathroom": 70}
        base_hum = base_humidities.get(room, 55)
        humidity = round(base_hum + random.uniform(-3, 3))
        humidity = max(35, min(85, humidity))

        return {
            "room": room,
            "temperature": temp,
            "humidity": humidity,
            "mmwave_radar": "active" if hour >= 7 and hour <= 22 else "sleep",
            "pir": hour >= 7 and hour <= 22,
            "door_contact": False,
            "fall_risk": False,
            "hour": hour,
            "activity": "sleeping" if hour < 7 or hour > 22 else "sitting",
            "prediction_enabled": True,
            "_source": "mock",
        }

    async def get_all_rooms_data(self) -> dict:
        all_data = {}
        for room in ["bedroom", "living", "bathroom"]:
            all_data[room] = await self.get_room_sensor_data(room)
        return all_data

    async def set_temperature(
        self, room: str, target_temp: float, hvac_mode: str = "cool"
    ) -> bool:
        self._ac_setpoints[room] = target_temp
        self._ac_modes[room] = hvac_mode
        logger.info(f"[Mock] 设置 {room} 空调: {target_temp}°C, 模式={hvac_mode}")
        return True

    async def set_hvac_mode(self, room: str, mode: str) -> bool:
        self._ac_modes[room] = mode
        logger.info(f"[Mock] 设置 {room} 空调模式: {mode}")
        return True

    async def turn_off_climate(self, room: str) -> bool:
        return await self.set_hvac_mode(room, "off")

    async def set_humidity(self, target_humidity: float) -> bool:
        logger.info(f"[Mock] 设置除湿机: {target_humidity}%")
        return True

    async def trigger_alarm(self, message: str = "") -> bool:
        logger.info(f"[Mock] 触发告警: {message}")
        return True

    async def check_connection(self) -> dict:
        return {
            "connected": True,
            "version": "mock-2024.1",
            "message": "Mock HA (仿真模式)",
            "_mock": True,
        }


# ========== 工厂函数 ==========

_ha_client_instance: HAClient | MockHAClient | None = None


def get_ha_client() -> HAClient | MockHAClient:
    """
    获取 HA 客户端（单例）
    根据 HA_ENABLED 配置自动选择真实/模拟客户端
    """
    global _ha_client_instance
    if _ha_client_instance is None:
        if HA_ENABLED and HA_TOKEN:
            logger.info(f"初始化 HA 真实客户端: {HA_URL}")
            _ha_client_instance = HAClient()
        else:
            logger.info("初始化 HA 模拟客户端 (仿真模式)")
            _ha_client_instance = MockHAClient()
    return _ha_client_instance


# ========== 便捷函数（供 Agent Tools 调用）==========

async def fetch_sensor_data(room: str) -> dict:
    """获取指定房间的传感器数据（自动选择真实/模拟）"""
    client = get_ha_client()
    return await client.get_room_sensor_data(room)


async def fetch_all_sensor_data() -> dict:
    """获取所有房间的传感器数据"""
    client = get_ha_client()
    return await client.get_all_rooms_data()


async def execute_climate_control(
    room: str, target_temp: float, mode: str = "cool"
) -> bool:
    """执行空调控制"""
    client = get_ha_client()
    return await client.set_temperature(room, target_temp, mode)


async def execute_alarm(message: str) -> bool:
    """执行告警"""
    client = get_ha_client()
    return await client.trigger_alarm(message)


async def check_ha_status() -> dict:
    """检查 HA 连接状态"""
    client = get_ha_client()
    return await client.check_connection()