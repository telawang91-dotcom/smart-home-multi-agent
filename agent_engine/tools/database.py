"""
数据持久化层 - SQLite
存储:
  1. 传感器历史数据（供 Holt-Winters 训练）
  2. Agent 决策日志（可追溯）
  3. 用户偏好（学习舒适温度习惯）
"""
import os
import json
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

from config import DB_PATH, HISTORY_RETENTION_DAYS

logger = logging.getLogger("database")


def _ensure_dir():
    """确保数据库目录存在"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)


@contextmanager
def get_db():
    """获取数据库连接（上下文管理器）"""
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """初始化数据库表"""
    with get_db() as db:
        # 传感器历史数据
        db.execute("""
            CREATE TABLE IF NOT EXISTS sensor_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                temperature REAL,
                humidity REAL,
                humidex REAL,
                occupancy TEXT,
                occupancy_prob REAL,
                source TEXT DEFAULT 'mock',
                timestamp REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sensor_room_time
            ON sensor_history(room, timestamp)
        """)

        # Agent 决策日志
        db.execute("""
            CREATE TABLE IF NOT EXISTS decision_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_id TEXT,
                room TEXT,
                action TEXT,
                target_temp REAL,
                intensity TEXT,
                comfort_level TEXT,
                humidex REAL,
                fused_temp REAL,
                prediction_temp REAL,
                prediction_method TEXT,
                verified INTEGER DEFAULT 0,
                conflict_detected INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                control_executed INTEGER DEFAULT 0,
                duration_ms REAL,
                thoughts_json TEXT,
                timestamp REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_decision_room_time
            ON decision_log(room, timestamp)
        """)

        # 用户偏好
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                hour_slot INTEGER,
                preferred_temp REAL,
                preferred_humidity REAL,
                activity TEXT,
                feedback TEXT,
                sample_count INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pref_room_hour_activity
            ON user_preferences(room, hour_slot, activity)
        """)

    logger.info(f"数据库初始化完成: {DB_PATH}")


# ========== 传感器历史 ==========

def save_sensor_data(
    room: str, temperature: float, humidity: float,
    humidex: float = None, occupancy: str = "unknown",
    occupancy_prob: float = 0.0, source: str = "mock"
):
    """保存一条传感器数据"""
    with get_db() as db:
        db.execute(
            """INSERT INTO sensor_history
               (room, temperature, humidity, humidex, occupancy, occupancy_prob, source, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (room, temperature, humidity, humidex, occupancy, occupancy_prob, source, time.time())
        )


def get_temperature_history(
    room: str, minutes: int = 120, limit: int = 200
) -> list[float]:
    """
    获取指定房间的温度历史（供 Holt-Winters 预测）
    默认取最近 2 小时的数据
    """
    cutoff = time.time() - minutes * 60
    with get_db() as db:
        rows = db.execute(
            """SELECT temperature FROM sensor_history
               WHERE room = ? AND timestamp > ? AND temperature IS NOT NULL
               ORDER BY timestamp ASC LIMIT ?""",
            (room, cutoff, limit)
        ).fetchall()
    return [r["temperature"] for r in rows]


def get_sensor_stats(room: str, hours: int = 24) -> dict:
    """获取传感器统计信息"""
    cutoff = time.time() - hours * 3600
    with get_db() as db:
        row = db.execute(
            """SELECT
                 COUNT(*) as count,
                 AVG(temperature) as avg_temp,
                 MIN(temperature) as min_temp,
                 MAX(temperature) as max_temp,
                 AVG(humidity) as avg_humidity,
                 AVG(humidex) as avg_humidex
               FROM sensor_history
               WHERE room = ? AND timestamp > ?""",
            (room, cutoff)
        ).fetchone()
    if row and row["count"] > 0:
        return dict(row)
    return {"count": 0}


# ========== 决策日志 ==========

def save_decision_log(
    pipeline_id: str, room: str, result: dict, duration_ms: float = 0,
    control_executed: bool = False
):
    """保存一次完整的 Agent 决策日志"""
    decision = result.get("decision", {})
    prediction = result.get("prediction", {})
    verification = result.get("verification", {})

    with get_db() as db:
        db.execute(
            """INSERT INTO decision_log
               (pipeline_id, room, action, target_temp, intensity,
                comfort_level, humidex, fused_temp,
                prediction_temp, prediction_method,
                verified, conflict_detected, retry_count,
                control_executed, duration_ms, thoughts_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pipeline_id, room,
                decision.get("action"),
                decision.get("target_temp"),
                decision.get("intensity"),
                prediction.get("comfort_level"),
                prediction.get("current_humidex"),
                prediction.get("fused_temp"),
                prediction.get("predicted_temp_30min"),
                "hybrid",
                1 if verification.get("plan_approved") else 0,
                1 if verification.get("conflict_detected") else 0,
                result.get("retry_count", 0),
                1 if control_executed else 0,
                duration_ms,
                json.dumps(result.get("thoughts", []), ensure_ascii=False, default=str),
                time.time(),
            )
        )


def get_recent_decisions(room: str = None, limit: int = 20) -> list[dict]:
    """获取最近的决策记录"""
    with get_db() as db:
        if room:
            rows = db.execute(
                """SELECT * FROM decision_log WHERE room = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (room, limit)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM decision_log
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ========== 用户偏好学习 ==========

def update_preference(
    room: str, hour: int, activity: str,
    actual_temp: float, humidity: float = None
):
    """
    更新用户偏好（增量学习）
    使用指数移动平均: new = α·actual + (1-α)·old
    """
    alpha = 0.2  # 学习率
    hour_slot = hour // 3  # 每 3 小时一个时段 (0-7)

    with get_db() as db:
        existing = db.execute(
            """SELECT * FROM user_preferences
               WHERE room = ? AND hour_slot = ? AND activity = ?""",
            (room, hour_slot, activity)
        ).fetchone()

        if existing:
            old_temp = existing["preferred_temp"]
            new_temp = round(alpha * actual_temp + (1 - alpha) * old_temp, 1)
            new_count = existing["sample_count"] + 1

            new_humidity = humidity
            if existing["preferred_humidity"] and humidity:
                new_humidity = round(alpha * humidity + (1 - alpha) * existing["preferred_humidity"], 0)

            db.execute(
                """UPDATE user_preferences
                   SET preferred_temp = ?, preferred_humidity = ?,
                       sample_count = ?, updated_at = datetime('now', 'localtime')
                   WHERE room = ? AND hour_slot = ? AND activity = ?""",
                (new_temp, new_humidity, new_count, room, hour_slot, activity)
            )
        else:
            db.execute(
                """INSERT INTO user_preferences
                   (room, hour_slot, preferred_temp, preferred_humidity, activity, sample_count)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (room, hour_slot, actual_temp, humidity, activity)
            )


def get_preference(room: str, hour: int, activity: str = "sitting") -> dict | None:
    """获取用户偏好"""
    hour_slot = hour // 3
    with get_db() as db:
        row = db.execute(
            """SELECT * FROM user_preferences
               WHERE room = ? AND hour_slot = ? AND activity = ?""",
            (room, hour_slot, activity)
        ).fetchone()
    return dict(row) if row else None


# ========== 数据清理 ==========

def cleanup_old_data():
    """清理过期数据"""
    cutoff = time.time() - HISTORY_RETENTION_DAYS * 86400
    with get_db() as db:
        deleted_sensors = db.execute(
            "DELETE FROM sensor_history WHERE timestamp < ?", (cutoff,)
        ).rowcount
        deleted_decisions = db.execute(
            "DELETE FROM decision_log WHERE timestamp < ?", (cutoff,)
        ).rowcount
    logger.info(f"清理数据: 传感器={deleted_sensors}条, 决策={deleted_decisions}条")
    return {"sensors_deleted": deleted_sensors, "decisions_deleted": deleted_decisions}
