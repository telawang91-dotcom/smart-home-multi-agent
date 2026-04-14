"""
全屋智能 Multi-Agent 系统配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ========== LLM 配置 ==========
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock / openai / deepseek / qwen
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# ========== 服务配置 ==========
AGENT_ENGINE_HOST = os.getenv("AGENT_ENGINE_HOST", "0.0.0.0")
AGENT_ENGINE_PORT = int(os.getenv("AGENT_ENGINE_PORT", "8081"))
NODE_SERVER_URL = os.getenv("NODE_SERVER_URL", "http://localhost:8080")

# ========== Home Assistant 配置 ==========
HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")  # Long-Lived Access Token
HA_ENABLED = os.getenv("HA_ENABLED", "false").lower() == "true"
HA_WS_URL = os.getenv("HA_WS_URL", "")  # 自动从 HA_URL 推导，也可手动覆盖

# 如果未手动设置 WS URL，则自动推导
if not HA_WS_URL and HA_URL:
    _ha_ws = HA_URL.replace("https://", "wss://").replace("http://", "ws://")
    HA_WS_URL = f"{_ha_ws}/api/websocket"

# HA Entity ID 映射（用户在 .env 或 UI 中配置具体的 entity_id）
HA_ENTITY_MAP = {
    "bedroom": {
        "temperature": os.getenv("HA_ENTITY_BEDROOM_TEMP", "sensor.bedroom_temperature"),
        "humidity": os.getenv("HA_ENTITY_BEDROOM_HUMIDITY", "sensor.bedroom_humidity"),
        "presence": os.getenv("HA_ENTITY_BEDROOM_PRESENCE", "binary_sensor.bedroom_presence"),
        "pir": os.getenv("HA_ENTITY_BEDROOM_PIR", "binary_sensor.bedroom_motion"),
        "door": os.getenv("HA_ENTITY_BEDROOM_DOOR", "binary_sensor.bedroom_door"),
        "climate": os.getenv("HA_ENTITY_BEDROOM_AC", "climate.bedroom_ac"),
        "fall_sensor": os.getenv("HA_ENTITY_BEDROOM_FALL", ""),
    },
    "living": {
        "temperature": os.getenv("HA_ENTITY_LIVING_TEMP", "sensor.living_room_temperature"),
        "humidity": os.getenv("HA_ENTITY_LIVING_HUMIDITY", "sensor.living_room_humidity"),
        "presence": os.getenv("HA_ENTITY_LIVING_PRESENCE", "binary_sensor.living_room_presence"),
        "pir": os.getenv("HA_ENTITY_LIVING_PIR", "binary_sensor.living_room_motion"),
        "door": os.getenv("HA_ENTITY_LIVING_DOOR", ""),
        "climate": os.getenv("HA_ENTITY_LIVING_AC", "climate.living_room_ac"),
        "fall_sensor": os.getenv("HA_ENTITY_LIVING_FALL", ""),
    },
    "bathroom": {
        "temperature": os.getenv("HA_ENTITY_BATH_TEMP", "sensor.bathroom_temperature"),
        "humidity": os.getenv("HA_ENTITY_BATH_HUMIDITY", "sensor.bathroom_humidity"),
        "presence": os.getenv("HA_ENTITY_BATH_PRESENCE", "binary_sensor.bathroom_presence"),
        "pir": os.getenv("HA_ENTITY_BATH_PIR", "binary_sensor.bathroom_motion"),
        "door": os.getenv("HA_ENTITY_BATH_DOOR", "binary_sensor.bathroom_door"),
        "climate": os.getenv("HA_ENTITY_BATH_AC", ""),
        "fall_sensor": os.getenv("HA_ENTITY_BATH_FALL", "binary_sensor.bathroom_fall"),
    },
}

# HA 全局设备
HA_GLOBAL_ENTITIES = {
    "dehumidifier": os.getenv("HA_ENTITY_DEHUMIDIFIER", ""),
    "ventilation": os.getenv("HA_ENTITY_VENTILATION", ""),
    "alarm": os.getenv("HA_ENTITY_ALARM", ""),
}

# ========== 数据存储配置 ==========
DB_PATH = os.getenv("DB_PATH", "data/smart_home.db")
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))

# ========== 定时任务配置 ==========
AUTO_ANALYZE_INTERVAL = int(os.getenv("AUTO_ANALYZE_INTERVAL", "300"))  # 秒，默认5分钟
EMERGENCY_COOLDOWN = int(os.getenv("EMERGENCY_COOLDOWN", "60"))  # 紧急事件冷却时间(秒)

# ========== 舒适度阈值 ==========
COMFORT_THRESHOLDS = {
    "humidex": {
        "comfortable": 29,
        "slightly_warm": 32,
        "uncomfortable": 35,
        "dangerous": 40,
    },
    "temperature": {
        "cold": 18,
        "cool": 20,
        "comfortable_low": 22,
        "comfortable_high": 26,
        "warm": 28,
        "hot": 30,
    },
    "humidity": {
        "dry": 30,
        "comfortable_low": 40,
        "comfortable_high": 60,
        "humid": 70,
    }
}

# ========== Agent 配置 ==========
AGENT_CONFIG = {
    "max_retries": 3,
    "step_timeout": 10,
    "enable_prediction": True,
    "prediction_horizon": 30,
}

# ========== 传感器权重 ==========
SENSOR_WEIGHTS = {
    "mmwave_radar": 0.5,
    "pir": 0.3,
    "door_contact": 0.2,
}

TEMPERATURE_SOURCE_WEIGHTS = {
    "bedside": {"base_weight": 0.5, "description": "床头传感器"},
    "window": {"base_weight": 0.3, "description": "窗边传感器"},
    "outlet": {"base_weight": 0.2, "description": "出风口传感器"},
}