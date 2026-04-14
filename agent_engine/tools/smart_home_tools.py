"""
Smart Home Tools - Agent 可调用的工具函数
对应简历中的 Function Calling / Tool Use
每个工具模拟真实智能家居设备控制能力

公式说明：
- Humidex: 加拿大标准 (Masterson & Richardson, 1979)
  Hx = T + 5/9 * (e - 10)
  其中 e 由露点温度 Td 通过 Clausius-Clapeyron 方程得出
  Td 由 Magnus-Tetens 公式从 T, RH 反推
- Holt 指数平滑: 双参数(level + trend)时序预测
- PMV 简化版: 综合 Humidex + 活动量 met 的舒适度分级
"""
import math
from models.schemas import (
    SensorData, PerceptionResult, PredictionResult,
    DecisionResult, VerificationResult,
    OccupancyStatus, ComfortLevel, RoomType
)
from config import (
    SENSOR_WEIGHTS, TEMPERATURE_SOURCE_WEIGHTS,
    COMFORT_THRESHOLDS, AGENT_CONFIG
)


# ========== Tool 定义（供 Function Calling 注册）==========

TOOL_DEFINITIONS = [
    {
        "name": "analyze_sensors",
        "description": "分析所有传感器数据，判断房间占用状态和传感器可靠性",
        "parameters": {
            "type": "object",
            "properties": {
                "check_radar": {"type": "boolean", "description": "是否检查毫米波雷达"},
                "check_pir": {"type": "boolean", "description": "是否检查PIR红外"},
                "check_door": {"type": "boolean", "description": "是否检查门磁"},
                "detect_anomalies": {"type": "boolean", "description": "是否检测传感器异常"}
            }
        }
    },
    {
        "name": "predict_trend",
        "description": "基于历史数据预测温度趋势，计算融合温度和体感指数",
        "parameters": {
            "type": "object",
            "properties": {
                "horizon_minutes": {"type": "integer", "description": "预测时间范围(分钟)"},
                "use_holt": {"type": "boolean", "description": "是否使用Holt指数平滑"},
                "compute_humidex": {"type": "boolean", "description": "是否计算Humidex指数"}
            }
        }
    },
    {
        "name": "make_decision",
        "description": "根据感知和预测结果，制定温度调控方案",
        "parameters": {
            "type": "object",
            "properties": {
                "optimize_comfort": {"type": "boolean", "description": "优先优化舒适度"},
                "energy_saving": {"type": "boolean", "description": "是否考虑节能"},
                "consider_prediction": {"type": "boolean", "description": "是否考虑预测趋势"}
            }
        }
    },
    {
        "name": "validate_plan",
        "description": "验证调控方案的安全性和合理性",
        "parameters": {
            "type": "object",
            "properties": {
                "check_safety": {"type": "boolean", "description": "检查安全约束"},
                "check_conflict": {"type": "boolean", "description": "检查传感器冲突"},
                "check_energy": {"type": "boolean", "description": "检查能耗合理性"}
            }
        }
    },
    {
        "name": "set_temperature",
        "description": "设置目标房间的空调温度",
        "parameters": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "description": "房间名称"},
                "target_temp": {"type": "number", "description": "目标温度(°C)"},
                "mode": {"type": "string", "description": "模式: cool/heat/auto"}
            },
            "required": ["room", "target_temp"]
        }
    },
    {
        "name": "set_humidity",
        "description": "设置目标房间的除湿/加湿",
        "parameters": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "description": "房间名称"},
                "target_humidity": {"type": "number", "description": "目标湿度(%)"}
            },
            "required": ["room", "target_humidity"]
        }
    }
]


# ========== 核心物理公式 ==========

def dew_point(T: float, RH: float) -> float:
    """
    露点温度计算 - Magnus-Tetens 公式
    参考: Alduchov & Eskridge (1996), Journal of Applied Meteorology

    Td = b * gamma / (a - gamma)
    gamma = a*T/(b+T) + ln(RH/100)

    参数: a=17.625, b=243.04°C
    适用范围: -45°C < T < 60°C, 1% < RH < 100%
    精度: ±0.4°C
    """
    a = 17.625
    b = 243.04
    RH = max(1.0, min(100.0, RH))
    gamma = (a * T) / (b + T) + math.log(RH / 100.0)
    Td = (b * gamma) / (a - gamma)
    return Td


def vapor_pressure_from_dew_point(Td: float) -> float:
    """
    从露点温度计算水汽压 (hPa)
    Clausius-Clapeyron 近似:
    e = 6.11 * exp(5417.7530 * (1/273.16 - 1/(Td+273.15)))

    参考: Environment Canada Humidex 技术文档
    """
    return 6.11 * math.exp(5417.7530 * (1.0 / 273.16 - 1.0 / (Td + 273.15)))


def compute_humidex(temp: float, humidity: float) -> float:
    """
    Humidex 体感温度计算 - 加拿大标准
    参考: Masterson & Richardson (1979), Atmosphere-Ocean

    公式: Hx = T + 5/9 * (e - 10)
    其中 e 从露点 Td 经 Clausius-Clapeyron 方程得到

    与前端 gai.html 中的 humidex() 函数完全一致:
      1. 先用 Magnus 公式从 (T, RH) 算露点 Td
      2. 再用 Clausius-Clapeyron 从 Td 算水汽压 e
      3. 最后 Hx = T + 5/9*(e-10)

    Humidex 分级 (加拿大标准):
      < 29: 舒适 (comfortable)
      29-34: 略有不适 (slightly warm)
      35-39: 不舒适 (uncomfortable)
      40-45: 很不舒适 (very uncomfortable)
      ≥ 46: 危险 (dangerous)
    """
    try:
        Td = dew_point(temp, humidity)
        e = vapor_pressure_from_dew_point(Td)
        hx = temp + (5.0 / 9.0) * (e - 10.0)
        return round(hx, 1)
    except Exception:
        return round(temp, 1)


def classify_comfort(
    humidex: float, temp: float,
    met: float = 1.2, age_factor: float = 1.1
) -> ComfortLevel:
    """
    综合舒适度分级 - 改进版（融合 Humidex + PMV 简化）

    传统 Humidex 只考虑温湿度。本方法额外纳入:
    1. met (代谢率): 睡眠≈0.8, 静坐≈1.2, 走动≈2.0
       高活动量降低热舒适阈值
    2. age_factor: 老年人(>65岁) ≈ 1.1，对极端温度更敏感

    等效 Humidex = Humidex + (met - 1.0) * 3.0
    - met=1.2(静坐): +0.6°C 修正
    - met=2.0(走动): +3.0°C 修正
    - met=0.8(睡眠): -0.6°C 修正

    老年人修正: 阈值收紧 10% (age_factor=1.1)
    """
    # 活动量修正
    met_correction = (met - 1.0) * 3.0
    effective_hx = humidex + met_correction

    # 老年人阈值收紧
    thresholds = COMFORT_THRESHOLDS["humidex"]
    dangerous_th = thresholds["dangerous"] / age_factor
    uncomfortable_th = thresholds["uncomfortable"] / age_factor
    slightly_warm_th = thresholds["slightly_warm"] / age_factor

    if effective_hx >= dangerous_th:
        return ComfortLevel.DANGEROUS
    if effective_hx >= uncomfortable_th:
        return ComfortLevel.UNCOMFORTABLE
    if effective_hx >= slightly_warm_th:
        return ComfortLevel.SLIGHTLY_WARM
    if temp < COMFORT_THRESHOLDS["temperature"]["cold"]:
        return ComfortLevel.COLD
    return ComfortLevel.COMFORTABLE


# ========== 活动量映射 ==========

MET_TABLE = {
    "sleeping": 0.8,
    "sitting": 1.2,
    "standing": 1.5,
    "walking": 2.0,
    "exercising": 3.0,
}


def get_met(activity: str) -> float:
    """从活动描述获取代谢率 met"""
    return MET_TABLE.get(activity, 1.2)


# ========== 预测模型组合 ==========

def holt_winters(
    history: list[float],
    alpha: float = 0.35,
    beta: float = 0.15,
    gamma: float = 0.1,
    season_length: int = 12,
    horizon_steps: int = 6,
) -> dict:
    """
    Holt-Winters 三参数指数平滑（加法季节性）

    参考:
      Winters (1960), "Forecasting sales by exponentially weighted
      moving averages", Management Science 6(3), 324-342

    模型:
      Level:    L_t = α·(y_t - S_{t-m}) + (1-α)·(L_{t-1} + T_{t-1})
      Trend:    T_t = β·(L_t - L_{t-1}) + (1-β)·T_{t-1}
      Season:   S_t = γ·(y_t - L_t) + (1-γ)·S_{t-m}
      预测:     ŷ_{t+h} = L_t + h·T_t + S_{t-m+h mod m}

    参数选择:
      α=0.35: level 跟随速度
      β=0.15: trend 跟随速度
      γ=0.10: 季节分量跟随速度（室内温度日周期弱，取小值）
      season_length=12: 每步5min，12步=1小时为一个季节周期

    与纯 Holt 的区别:
      - 室内温度受日照/空调开关有周期性波动
      - γ 分量捕捉"每天这个时段温度倾向偏高/偏低"
      - 数据不足 season_length 时，自动退化为 Holt 双参数

    置信区间 (Chatfield & Yar, 1988):
      σ_h = σ · √(Σ_{j=0}^{h-1} c_j²)
      其中 c_j = α·(1 + j·β) (简化)
    """
    n = len(history)

    # 数据不足一个季节周期 → 退化为 Holt 双参数
    if n < season_length + 2:
        return _holt_two_param(history, alpha, beta, horizon_steps)

    # === Holt-Winters 初始化 ===
    # Level: 第一个周期的均值
    level = sum(history[:season_length]) / season_length
    # Trend: 前两个周期的平均斜率
    if n >= 2 * season_length:
        trend = sum(
            (history[season_length + i] - history[i]) / season_length
            for i in range(season_length)
        ) / season_length
    else:
        trend = (history[-1] - history[0]) / max(n - 1, 1)
    # Season: 第一个周期内各点与均值的偏差
    season = [history[i] - level for i in range(season_length)]

    residuals = []

    for i in range(season_length, n):
        s_idx = i % season_length
        y = history[i]
        predicted = level + trend + season[s_idx]
        residuals.append(y - predicted)

        new_level = alpha * (y - season[s_idx]) + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        season[s_idx] = gamma * (y - new_level) + (1 - gamma) * season[s_idx]
        level = new_level
        trend = new_trend

    # 预测
    predictions = []
    for h in range(1, horizon_steps + 1):
        s_idx = (n + h - 1) % season_length
        predictions.append(level + h * trend + season[s_idx])
    predicted = predictions[-1] if predictions else level

    # 残差标准差
    std_r = _residual_std(residuals)

    # 置信度
    data_confidence = min(1.0, n / 30.0)
    trend_confidence = max(0.0, 1.0 - std_r / 2.0)
    confidence = round(0.5 * data_confidence + 0.3 * trend_confidence + 0.2, 2)

    return {
        "predicted": round(predicted, 2),
        "trend_per_step": round(trend, 4),
        "confidence": min(1.0, confidence),
        "residual_std": round(std_r, 3),
        "method": "holt_winters",
    }


def _holt_two_param(
    history: list[float],
    alpha: float = 0.35,
    beta: float = 0.15,
    horizon_steps: int = 6,
) -> dict:
    """
    Holt 双参数指数平滑（无季节性退化版）

    参考: Holt (1957), "Forecasting seasonals and trends by
          exponentially weighted moving averages"
    """
    if len(history) < 2:
        return {
            "predicted": history[-1] if history else 25.0,
            "trend_per_step": 0.0,
            "confidence": 0.3,
            "residual_std": 0.0,
            "method": "holt",
        }

    level = history[0]
    trend = history[1] - history[0]
    residuals = []

    for i, y in enumerate(history):
        if i == 0:
            continue
        predicted = level + trend
        residuals.append(y - predicted)
        new_level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (new_level - level) + (1 - beta) * trend
        level = new_level

    predicted = level + horizon_steps * trend
    std_r = _residual_std(residuals)

    data_confidence = min(1.0, len(history) / 20.0)
    trend_confidence = max(0.0, 1.0 - std_r / 2.0)
    confidence = round(0.6 * data_confidence + 0.4 * trend_confidence, 2)

    return {
        "predicted": round(predicted, 2),
        "trend_per_step": round(trend, 4),
        "confidence": confidence,
        "residual_std": round(std_r, 3),
        "method": "holt",
    }


def newton_cooling_predict(
    current_temp: float,
    hour: int,
    horizon_min: int = 30,
    ac_running: bool = False,
    ac_setpoint: float = 25.0,
) -> dict:
    """
    Newton 冷却定律 + 日照模型 — 无历史数据时的物理 fallback

    参考: Newton's Law of Cooling (1701)
      dT/dt = -k · (T_in - T_env)

    改进:
    1. 室外温度用 正弦日照模型 估算:
       T_out(h) = T_mean + A·sin(π·(h-6)/12)   (6:00-18:00)
       T_out(h) = T_mean - A_night              (夜间)
       参数来自中国气象局华南典型城市统计

    2. 围护结构热惰性 (thermal mass):
       室内温度变化会受热容量 C 阻尼
       有效 k = k_wall / (1 + C/k_wall)

    3. 空调运行时加入制冷/制热驱动力:
       dT/dt += k_ac · (T_setpoint - T_in)

    适用场景: 系统冷启动 / 历史数据不足 / 传感器掉线
    """
    # 室外温度估算（正弦日照模型）
    T_mean = 28.0   # 华南夏季日均温
    A_day = 7.0     # 日温差振幅
    A_night = 3.0   # 夜间偏低量

    if 6 <= hour <= 18:
        t_out = T_mean + A_day * math.sin(math.pi * (hour - 6) / 12)
    else:
        t_out = T_mean - A_night

    # 围护结构传热系数 (W/(m²·K) 等效)
    k_wall = 0.008   # 较好隔热的住宅，约 0.005-0.015 /min
    C_ratio = 2.0    # 热容量/传热系数比，越大响应越慢
    k_eff = k_wall / (1 + C_ratio * k_wall)

    # 温度漂移
    drift = k_eff * (t_out - current_temp) * horizon_min

    # 空调运行修正
    if ac_running:
        k_ac = 0.02   # 空调调节速率 /min
        drift += k_ac * (ac_setpoint - current_temp) * horizon_min

    predicted = current_temp + drift
    predicted = round(max(10.0, min(45.0, predicted)), 1)

    return {
        "predicted": predicted,
        "trend_per_step": round(drift / max(horizon_min / 5, 1), 4),
        "confidence": 0.4,
        "residual_std": 1.0,
        "method": "newton_cooling",
        "outdoor_estimate": round(t_out, 1),
    }


def _residual_std(residuals: list[float]) -> float:
    """计算残差标准差"""
    if len(residuals) < 2:
        return 0.5
    mean_r = sum(residuals) / len(residuals)
    var_r = sum((r - mean_r) ** 2 for r in residuals) / (len(residuals) - 1)
    return math.sqrt(var_r)


def hybrid_predict(
    history: list[float] | None,
    current_temp: float,
    hour: int,
    horizon_min: int = 30,
) -> dict:
    """
    混合预测引擎 — 自动选择最优模型

    策略:
      数据量 ≥ 24 点 (2h)  → Holt-Winters (有季节性分量)
      数据量 ≥ 3 点 (15min) → Holt 双参数
      数据量 < 3 点          → Newton 冷却定律(纯物理)

    多模型融合 (当 Holt 可用时):
      若 Holt 置信度 < 0.5，与 Newton 做加权混合:
      ŷ = w_holt·ŷ_holt + (1-w_holt)·ŷ_newton
      w_holt = confidence_holt

    参考:
      Makridakis et al. (2018), "Statistical and Machine Learning
      forecasting methods: Concerns and ways forward", PLOS ONE
      — 证明模型组合(combination)通常优于单一模型
    """
    newton = newton_cooling_predict(current_temp, hour, horizon_min)

    if not history or len(history) < 3:
        return newton

    # 统计模型预测
    hw_result = holt_winters(
        history,
        alpha=0.35, beta=0.15, gamma=0.1,
        season_length=12,
        horizon_steps=horizon_min // 5,
    )

    # 置信度加权融合
    w = hw_result["confidence"]
    if w >= 0.7:
        # 统计模型足够可靠，直接使用
        return hw_result
    else:
        # 混合: 统计 + 物理
        blended_pred = w * hw_result["predicted"] + (1 - w) * newton["predicted"]
        blended_conf = max(hw_result["confidence"], newton["confidence"])
        return {
            "predicted": round(blended_pred, 2),
            "trend_per_step": round(
                w * hw_result["trend_per_step"] + (1 - w) * newton["trend_per_step"], 4
            ),
            "confidence": round(blended_conf, 2),
            "residual_std": round(hw_result["residual_std"], 3),
            "method": f"hybrid({hw_result['method']}×{w:.0%}+newton×{1-w:.0%})",
        }


# ========== 工具实现 ==========

def analyze_sensors(sensor_data:SensorData, **kwargs) -> PerceptionResult:
    """
    Tool: analyze_sensors
    分析传感器数据，判断占用状态
    """
    anomalies = []
    reliability = {}

    # 毫米波雷达分析
    radar_score = 0.0
    if sensor_data.mmwave_radar == "active":
        radar_score = 0.95
    elif sensor_data.mmwave_radar == "sleep":
        radar_score = 0.7
    else:
        radar_score = 0.05
    reliability["mmwave_radar"] = round(radar_score, 2)

    # PIR 分析
    pir_score = 0.85 if sensor_data.pir else 0.1
    reliability["pir"] = round(pir_score, 2)

    # 门磁分析
    door_score = 0.6 if sensor_data.door_contact else 0.4
    reliability["door_contact"] = round(door_score, 2)

    # 加权占用概率
    weights = SENSOR_WEIGHTS
    occupancy_prob = (
        radar_score * weights["mmwave_radar"] +
        pir_score * weights["pir"] +
        door_score * weights["door_contact"]
    )
    occupancy_prob = round(min(max(occupancy_prob, 0), 1), 3)

    # 异常检测：传感器冲突
    detect_anomalies = kwargs.get("detect_anomalies", True)
    if detect_anomalies:
        if radar_score > 0.8 and pir_score < 0.2:
            anomalies.append("传感器冲突：毫米波显示有人，PIR未检测到运动")
        if radar_score < 0.2 and pir_score > 0.8:
            anomalies.append("传感器冲突：PIR检测到运动，毫米波未确认")
        if sensor_data.fall_risk:
            anomalies.append("跌倒风险告警：检测到可能的跌倒事件")

    # 判定占用状态
    if occupancy_prob > 0.6:
        status = OccupancyStatus.OCCUPIED
    elif occupancy_prob > 0.3:
        status = OccupancyStatus.UNCERTAIN
    else:
        status = OccupancyStatus.EMPTY

    # 确定有人的房间
    occupied_rooms = []
    if status != OccupancyStatus.EMPTY:
        occupied_rooms.append(sensor_data.room.value)

    reasoning = (
        f"毫米波雷达({sensor_data.mmwave_radar})得分{radar_score:.2f}, "
        f"PIR({'有' if sensor_data.pir else '无'}运动)得分{pir_score:.2f}, "
        f"门磁({'开' if sensor_data.door_contact else '关'})得分{door_score:.2f} → "
        f"加权占用概率={occupancy_prob:.3f} → {status.value}"
    )

    return PerceptionResult(
        occupancy_status=status,
        occupancy_probability=occupancy_prob,
        occupied_rooms=occupied_rooms,
        sensor_reliability=reliability,
        anomalies=anomalies,
        reasoning=reasoning,
    )


def predict_trend(
    sensor_data: SensorData,
    perception: PerceptionResult,
    history: list[float] | None = None,
    **kwargs
) -> PredictionResult:
    """
    Tool: predict_trend
    温度融合 + Holt趋势预测 + Humidex体感 + 活动量修正
    """
    room = sensor_data.room.value
    current_temp = sensor_data.temperature.get(room, 25.0)

    # === 多源温度融合（模拟 3 个传感器位置）===
    # 真实场景中这些来自不同 HA entity
    noise_bedside = 0.0
    noise_window = 0.8
    noise_outlet = -0.5
    sources = {
        "bedside": current_temp + noise_bedside,
        "window": current_temp + noise_window,
        "outlet": current_temp + noise_outlet,
    }
    tw = TEMPERATURE_SOURCE_WEIGHTS
    total_weight = 0
    fused_temp = 0
    fusion_weights = {}
    for src, temp_val in sources.items():
        w = tw[src]["base_weight"]
        fused_temp += temp_val * w
        total_weight += w
        fusion_weights[src] = {"temp": round(temp_val, 1), "weight": w}
    fused_temp = round(fused_temp / total_weight if total_weight > 0 else current_temp, 1)

    # === Humidex 计算（标准露点法，与前端一致）===
    humidity = sensor_data.humidity
    current_humidex = compute_humidex(fused_temp, humidity)

    # === 活动量 met 修正 ===
    met = get_met(sensor_data.activity)
    comfort = classify_comfort(current_humidex, fused_temp, met=met)

    # === 混合预测引擎 (Holt-Winters + Newton 冷却定律) ===
    predicted_temp = None
    predicted_humidex = None
    trend = "stable"
    prediction_confidence = 0.5
    prediction_method = "none"

    use_prediction = kwargs.get("use_holt", True)
    prediction_enabled = sensor_data.prediction_enabled
    horizon = kwargs.get("horizon_minutes", 30)

    if use_prediction and prediction_enabled:
        pred_result = hybrid_predict(
            history=history,
            current_temp=fused_temp,
            hour=sensor_data.hour,
            horizon_min=horizon,
        )
        predicted_temp = round(pred_result["predicted"], 1)
        prediction_confidence = pred_result["confidence"]
        prediction_method = pred_result.get("method", "unknown")

        if predicted_temp is not None:
            predicted_humidex = compute_humidex(predicted_temp, humidity)
            if predicted_temp > fused_temp + 0.3:
                trend = "rising"
            elif predicted_temp < fused_temp - 0.3:
                trend = "falling"

    reasoning = (
        f"融合温度: {fused_temp}°C (床头{sources['bedside']:.1f}×{tw['bedside']['base_weight']}, "
        f"窗边{sources['window']:.1f}×{tw['window']['base_weight']}, "
        f"出风口{sources['outlet']:.1f}×{tw['outlet']['base_weight']}) | "
        f"Humidex={current_humidex}(露点法) | met={met}({sensor_data.activity}) → {comfort.value} | "
        f"趋势={trend}"
    )
    if predicted_temp:
        reasoning += f" | {horizon}min预测: {predicted_temp}°C (Hx={predicted_humidex}, 置信={prediction_confidence}, 方法={prediction_method})"

    return PredictionResult(
        current_temp=current_temp,
        fused_temp=fused_temp,
        current_humidex=current_humidex,
        comfort_level=comfort,
        predicted_temp_30min=predicted_temp,
        predicted_humidex_30min=predicted_humidex,
        trend=trend,
        fusion_weights=fusion_weights,
        reasoning=reasoning,
    )


def make_decision(
    sensor_data: SensorData,
    perception: PerceptionResult,
    prediction: PredictionResult,
    **kwargs
) -> DecisionResult:
    """
    Tool: make_decision
    根据感知和预测结果制定调控方案

    决策逻辑:
    1. 无人 → 节能模式（升高设定温度2°C or关闭）
    2. 有人 + 舒适 → 无动作
    3. 有人 + 不适 → 根据 Humidex 偏差计算目标温度
    4. 趋势补偿 → 预判30min后状态，提前微调
    """
    # 无人 → 不调控
    if perception.occupancy_status == OccupancyStatus.EMPTY:
        return DecisionResult(
            action="none",
            reasoning="房间无人，进入节能模式，不执行温度调控。",
            affected_rooms=[],
        )

    comfort = prediction.comfort_level
    fused_temp = prediction.fused_temp
    humidex = prediction.current_humidex
    room = sensor_data.room.value
    met = get_met(sensor_data.activity)

    action = "none"
    target_temp = None
    intensity = "normal"
    estimated_time = 0

    # === 基于偏差的精确目标温度计算 ===
    # 舒适 Humidex 目标: 26-28（老年人偏暖）
    # 目标 = 当前设定 - (当前Humidex - 目标Humidex) * 调节系数
    target_humidex = 27.0  # 老年人舒适中心
    hx_deviation = humidex - target_humidex

    if comfort == ComfortLevel.DANGEROUS:
        action = "emergency_cool"
        target_temp = round(fused_temp - hx_deviation * 0.6, 1)
        target_temp = max(22.0, min(target_temp, 26.0))
        intensity = "emergency"
        estimated_time = 15
    elif comfort == ComfortLevel.UNCOMFORTABLE:
        action = "cool"
        target_temp = round(fused_temp - hx_deviation * 0.5, 1)
        target_temp = max(23.0, min(target_temp, 26.0))
        intensity = "high"
        estimated_time = 20
    elif comfort == ComfortLevel.SLIGHTLY_WARM:
        action = "cool"
        target_temp = round(fused_temp - hx_deviation * 0.4, 1)
        target_temp = max(24.0, min(target_temp, 26.0))
        intensity = "normal"
        estimated_time = 25
    elif comfort == ComfortLevel.COLD:
        action = "heat"
        target_temp = round(fused_temp + abs(hx_deviation) * 0.4, 1)
        target_temp = max(22.0, min(target_temp, 25.0))
        intensity = "normal"
        estimated_time = 20

    # === 趋势补偿 ===
    consider_prediction = kwargs.get("consider_prediction", True)
    if consider_prediction and sensor_data.prediction_enabled:
        if prediction.trend == "rising" and action == "none":
            if (prediction.predicted_humidex_30min and
                    prediction.predicted_humidex_30min > COMFORT_THRESHOLDS["humidex"]["slightly_warm"]):
                action = "cool"
                target_temp = round(fused_temp - 1.0, 1)
                target_temp = max(24.0, target_temp)
                intensity = "low"
                estimated_time = 30

        if prediction.trend == "falling" and action == "none":
            if (prediction.predicted_temp_30min and
                    prediction.predicted_temp_30min < COMFORT_THRESHOLDS["temperature"]["cool"]):
                action = "heat"
                target_temp = round(fused_temp + 1.0, 1)
                target_temp = min(25.0, target_temp)
                intensity = "low"
                estimated_time = 30

    reasoning = (
        f"当前舒适度: {comfort.value} (Humidex={humidex}, T={fused_temp}°C) → "
        f"决策: {action}"
    )
    if target_temp:
        reasoning += f", 目标温度={target_temp}°C, 强度={intensity}"
    if consider_prediction and prediction.trend != "stable":
        reasoning += f" | 趋势({prediction.trend})已纳入决策"

    return DecisionResult(
        action=action,
        target_temp=target_temp,
        intensity=intensity,
        affected_rooms=[room] if action != "none" else [],
        reasoning=reasoning,
        estimated_time_min=estimated_time,
    )


def validate_plan(
    sensor_data: SensorData,
    perception: PerceptionResult,
    prediction: PredictionResult,
    decision: DecisionResult,
    **kwargs
) -> VerificationResult:
    """
    Tool: validate_plan
    验证调控方案的安全性和合理性
    """
    issues = []
    suggestions = []
    conflict_detected = False
    conflict_details = ""
    requires_replanning = False

    # 安全性检查
    check_safety = kwargs.get("check_safety", True)
    if check_safety:
        if decision.target_temp and decision.target_temp < 16:
            issues.append("目标温度过低(<16°C)，可能导致老人不适")
            requires_replanning = True
        if decision.target_temp and decision.target_temp > 30:
            issues.append("目标温度过高(>30°C)，存在安全风险")
            requires_replanning = True
        if sensor_data.fall_risk:
            issues.append("跌倒风险告警：建议优先处理安全问题")
            suggestions.append("通知紧急联系人或触发报警")

    # 传感器冲突检查
    check_conflict = kwargs.get("check_conflict", True)
    if check_conflict and perception.anomalies:
        for anomaly in perception.anomalies:
            if "冲突" in anomaly:
                conflict_detected = True
                conflict_details = anomaly
                suggestions.append("建议重新采集传感器数据以确认占用状态")
                if perception.occupancy_status == OccupancyStatus.UNCERTAIN:
                    requires_replanning = True
                    suggestions.append("占用状态不确定，建议 Supervisor 重新规划")

    # 能耗合理性检查
    check_energy = kwargs.get("check_energy", True)
    if check_energy:
        if decision.intensity == "emergency" and perception.occupancy_status != OccupancyStatus.OCCUPIED:
            issues.append("紧急调控但占用状态不明确，能耗不合理")
            requires_replanning = True

    # 动作一致性检查
    if decision.action == "cool" and prediction.comfort_level == ComfortLevel.COLD:
        issues.append("当前已偏冷，但决策为降温，逻辑矛盾")
        requires_replanning = True
    if decision.action == "heat" and prediction.comfort_level in [ComfortLevel.UNCOMFORTABLE, ComfortLevel.DANGEROUS]:
        issues.append("当前已过热，但决策为升温，逻辑矛盾")
        requires_replanning = True

    plan_approved = not requires_replanning and len(issues) == 0

    reasoning = f"验证{'通过' if plan_approved else '未通过'}"
    if issues:
        reasoning += f" | 问题: {'; '.join(issues)}"
    if suggestions:
        reasoning += f" | 建议: {'; '.join(suggestions)}"
    if conflict_detected:
        reasoning += f" | 传感器冲突: {conflict_details}"

    return VerificationResult(
        plan_approved=plan_approved,
        issues=issues,
        suggestions=suggestions,
        conflict_detected=conflict_detected,
        conflict_details=conflict_details,
        requires_replanning=requires_replanning,
        reasoning=reasoning,
    )


# ========== 工具注册表 ==========

TOOL_REGISTRY = {
    "analyze_sensors": analyze_sensors,
    "predict_trend": predict_trend,
    "make_decision": make_decision,
    "validate_plan": validate_plan,
}