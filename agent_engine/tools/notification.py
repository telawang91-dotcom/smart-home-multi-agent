"""
通知 & 告警系统
支持多通道推送:
  1. HA 通知服务（手机 App 推送）
  2. Webhook（飞书/钉钉/企业微信）
  3. 邮件（SMTP）
  4. 系统日志（兜底）

架构:
  NotificationManager → Channel 1 (HA Notify)
                      → Channel 2 (Webhook)
                      → Channel 3 (Email)
                      → Channel 4 (Log)
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from enum import Enum

import httpx

from config import HA_URL, HA_TOKEN, HA_ENABLED

logger = logging.getLogger("notification")


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class NotificationChannel(str, Enum):
    HA_NOTIFY = "ha_notify"
    WEBHOOK = "webhook"
    LOG = "log"


# ========== 通道实现 ==========

async def send_ha_notification(title: str, message: str, data:dict = None) -> bool:
    """
    通过 HA 通知服务推送（到手机 HA App）
    POST /api/services/notify/notify
    """
    if not HA_ENABLED or not HA_TOKEN:
        logger.debug("HA 未启用，跳过 HA 通知")
        return False

    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "message": message,
                "title": title,
            }
            if data:
                payload["data"] = data

            resp = await client.post(
                f"{HA_URL}/api/services/notify/notify",
                headers={
                    "Authorization": f"Bearer {HA_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                logger.info(f"HA 通知已发送: {title}")
                return True
            logger.warning(f"HA 通知发送失败: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"HA 通知异常: {e}")
    return False


async def send_webhook(
    url: str, title: str, message: str,
    level: AlertLevel = AlertLevel.INFO,
    data: dict = None,
) -> bool:
    """
    通过 Webhook 推送（支持飞书/钉钉/企业微信/自定义）

    飞书格式:
      POST url
      {"msg_type": "text", "content": {"text": "..."}}

    钉钉格式:
      POST url
      {"msgtype": "text", "text": {"content": "..."}}
    """
    if not url:
        return False

    emoji_map = {
        AlertLevel.INFO: "ℹ️",
        AlertLevel.WARNING: "⚠️",
        AlertLevel.CRITICAL: "🚨",
        AlertLevel.EMERGENCY: "🆘",
    }
    emoji = emoji_map.get(level, "ℹ️")
    full_text = f"{emoji} [{level.value.upper()}] {title}\n{message}"
    if data:
        full_text += f"\n📊 {json.dumps(data, ensure_ascii=False, default=str)[:200]}"
    full_text += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    try:
        async with httpx.AsyncClient() as client:
            # 自动检测 Webhook 类型
            if "feishu" in url or "lark" in url:
                payload = {"msg_type": "text", "content": {"text": full_text}}
            elif "dingtalk" in url or "oapi.dingtalk" in url:
                payload = {"msgtype": "text", "text": {"content": full_text}}
            elif "qyapi.weixin" in url:
                payload = {"msgtype": "text", "text": {"content": full_text}}
            else:
                payload = {
                    "title": title, "message": message,
                    "level": level.value, "data": data,
                    "timestamp": datetime.now().isoformat(),
                }

            resp = await client.post(url, json=payload, timeout=10.0)
            if resp.status_code in (200, 201):
                logger.info(f"Webhook 通知已发送: {title}")
                return True
            logger.warning(f"Webhook 通知失败: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Webhook 通知异常: {e}")
    return False


# ========== 通知管理器 ==========

class NotificationManager:
    """
    统一通知管理器
    根据告警级别和配置，自动选择推送通道
    """

    def __init__(self):
        self.webhook_urls: list[str] = []
        self._history: list[dict] = []
        self._cooldown: dict[str, float] = {}  # {key: last_time}
        self.cooldown_seconds = 60  # 同一告警冷却时间

    def add_webhook(self, url: str):
        """添加 Webhook 通道"""
        if url and url not in self.webhook_urls:
            self.webhook_urls.append(url)
            logger.info(f"添加 Webhook 通道: {url[:50]}...")

    def _should_send(self, key: str) -> bool:
        """检查冷却期"""
        now = time.time()
        last = self._cooldown.get(key, 0)
        if now - last < self.cooldown_seconds:
            return False
        self._cooldown[key] = now
        return True

    async def notify(
        self, title: str, message: str,
        level: AlertLevel = AlertLevel.INFO,
        data: dict = None,
        dedupe_key: str = None,
    ):
        """
        发送通知（自动路由到所有可用通道）

        Args:
            title: 通知标题
            message: 通知内容
            level: 告警级别
            data: 附加数据
            dedupe_key: 去重键（相同 key 在冷却期内不重复发送）
        """
        # 去重检查
        if dedupe_key and not self._should_send(dedupe_key):
            logger.debug(f"通知冷却中，跳过: {dedupe_key}")
            return

        # 记录历史
        record = {
            "title": title, "message": message, "level": level.value,
            "data": data, "timestamp": time.time(),
            "channels_sent": [],
        }

        # 日志（始终记录）
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
            AlertLevel.EMERGENCY: logger.critical,
        }.get(level, logger.info)
        log_fn(f"[通知] [{level.value}] {title}: {message}")
        record["channels_sent"].append("log")

        # HA 推送（warning 及以上）
        if level in (AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.EMERGENCY):
            if await send_ha_notification(title, message, data):
                record["channels_sent"].append("ha_notify")

        # Webhook 推送（所有已配置的通道）
        for url in self.webhook_urls:
            if await send_webhook(url, title, message, level, data):
                record["channels_sent"].append(f"webhook:{url[:30]}")

        self._history.append(record)
        # 只保留最近 100 条
        if len(self._history) > 100:
            self._history = self._history[-100:]

    async def notify_fall_detected(self, room: str, details: str = ""):
        """跌倒检测通知"""
        await self.notify(
            title=f"🆘 紧急：{room}检测到疑似跌倒",
            message=f"智能传感器在{room}检测到疑似跌倒事件，请立即确认老人安全！\n{details}",
            level=AlertLevel.EMERGENCY,
            data={"room": room, "event": "fall_detected"},
            dedupe_key=f"fall_{room}",
        )

    async def notify_extreme_temp(self, room: str, temp: float, is_high: bool = True):
        """极端温度通知"""
        direction = "过高" if is_high else "过低"
        await self.notify(
            title=f"🚨 温度告警：{room}温度{direction}",
            message=f"{room}当前温度 {temp}°C {'超过安全阈值' if is_high else '低于安全阈值'}，系统已启动紧急{'降温' if is_high else '升温'}。",
            level=AlertLevel.CRITICAL,
            data={"room": room, "temperature": temp, "type": "high" if is_high else "low"},
            dedupe_key=f"temp_{room}_{direction}",
        )

    async def notify_sensor_conflict(self, room: str, details: str):
        """传感器冲突通知"""
        await self.notify(
            title=f"⚠️ 传感器冲突：{room}",
            message=f"{room}传感器数据存在矛盾，Agent 已启动重规划。\n{details}",
            level=AlertLevel.WARNING,
            data={"room": room, "event": "sensor_conflict"},
            dedupe_key=f"conflict_{room}",
        )

    async def notify_daily_summary(self, summary: dict):
        """每日决策摘要"""
        rooms_info = []
        for room, info in summary.get("rooms", {}).items():
            rooms_info.append(
                f"  {room}: 平均{info.get('avg_temp', '?')}°C, "
                f"决策{info.get('decision_count', 0)}次, "
                f"舒适率{info.get('comfort_rate', '?')}%"
            )
        rooms_text = "\n".join(rooms_info) if rooms_info else "  无数据"

        await self.notify(
            title="📊 每日智能家居报告",
            message=(
                f"日期: {datetime.now().strftime('%Y-%m-%d')}\n"
                f"分析次数: {summary.get('total_analyses', 0)}\n"
                f"控制执行: {summary.get('controls_executed', 0)}次\n"
                f"紧急事件: {summary.get('emergencies', 0)}次\n"
                f"各房间:\n{rooms_text}"
            ),
            level=AlertLevel.INFO,
            data=summary,
        )

    def get_history(self, limit: int = 20) -> list[dict]:
        """获取通知历史"""
        return self._history[-limit:]


# ========== 单例 ==========

_manager: NotificationManager | None = None


def get_notification_manager() -> NotificationManager:
    """获取通知管理器（单例）"""
    global _manager
    if _manager is None:
        _manager = NotificationManager()
    return _manager
