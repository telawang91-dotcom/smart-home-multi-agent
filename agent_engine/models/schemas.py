"""
数据模型定义 - 传感器数据、Agent 状态、决策结果
"""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field
from enum import Enum
import time


# ========== 枚举类型 ==========

class RoomType(str, Enum):
    BEDROOM = "bedroom"
    LIVING = "living"
    BATHROOM = "bathroom"


class OccupancyStatus(str, Enum):
    OCCUPIED = "occupied"
    EMPTY = "empty"
    UNCERTAIN = "uncertain"


class ComfortLevel(str, Enum):
    COMFORTABLE = "comfortable"
    SLIGHTLY_WARM = "slightly_warm"
    UNCOMFORTABLE = "uncomfortable"
    COLD = "cold"
    DANGEROUS = "dangerous"


class AgentRole(str, Enum):
    SUPERVISOR = "supervisor"
    PERCEPTION = "perception"
    PREDICTION = "prediction"
    DECISION = "decision"
    VERIFICATION = "verification"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNING = "replanning"


# ========== 传感器数据 ==========

class SensorData(BaseModel):
    """原始传感器输入"""
    room: RoomType = RoomType.BEDROOM
    temperature: dict = Field(
        default_factory=lambda: {"bedroom": 25.0, "living": 25.0, "bathroom": 25.0}
    )
    humidity: float = 55.0
    hour: int = 15
    mmwave_radar: str = "active"       # active / idle / sleep
    pir: bool = True
    door_contact: bool = False          # True = 门开
    fall_risk: bool = False
    activity: str = "sitting"           # sitting / sleeping / walking
    manual_override: bool = False
    prediction_enabled: bool = True
    scene_id: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


# ========== Agent 消息 ==========

class AgentMessage(BaseModel):
    """Agent 之间传递的消息"""
    from_agent: AgentRole
    to_agent: AgentRole
    content: str
    data:dict = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class AgentThought(BaseModel):
    """Agent 的思考过程（用于可解释性展示）"""
    agent: AgentRole
    step: int
    title: str
    reasoning: str
    conclusion: str
    confidence: float = 1.0
    data: dict = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


# ========== 各 Agent 输出结果 ==========

class PerceptionResult(BaseModel):
    """感知Agent输出"""
    occupancy_status: OccupancyStatus
    occupancy_probability: float = 0.0
    occupied_rooms: list[str] = Field(default_factory=list)
    sensor_reliability: dict = Field(default_factory=dict)
    anomalies: list[str] = Field(default_factory=list)
    reasoning: str = ""


class PredictionResult(BaseModel):
    """预测Agent输出"""
    current_temp: float = 25.0
    fused_temp: float = 25.0
    current_humidex: float = 29.0
    comfort_level: ComfortLevel = ComfortLevel.COMFORTABLE
    predicted_temp_30min: Optional[float] = None
    predicted_humidex_30min: Optional[float] = None
    trend: str = "stable"              # rising / falling / stable
    fusion_weights: dict = Field(default_factory=dict)
    reasoning: str = ""


class DecisionResult(BaseModel):
    """决策Agent输出"""
    action: str = "none"               # cool / heat / none / emergency_cool
    target_temp: Optional[float] = None
    target_humidity: Optional[float] = None
    intensity: str = "normal"          # low / normal / high / emergency
    affected_rooms: list[str] = Field(default_factory=list)
    reasoning: str = ""
    estimated_time_min: int = 0


class VerificationResult(BaseModel):
    """验证Agent输出"""
    plan_approved: bool = True
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    conflict_detected: bool = False
    conflict_details: str = ""
    requires_replanning: bool = False
    reasoning: str = ""


# ========== Pipeline 状态 ==========

class PipelineState(BaseModel):
    """整个决策Pipeline的状态（LangGraph State）"""
    # 输入
    sensor_data: SensorData = Field(default_factory=SensorData)
    scene_id: Optional[str] = None

    # 各Agent结果
    perception: Optional[PerceptionResult] = None
    prediction: Optional[PredictionResult] = None
    decision: Optional[DecisionResult] = None
    verification: Optional[VerificationResult] = None

    # 流程控制
    current_agent: AgentRole = AgentRole.SUPERVISOR
    task_status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    error_message: str = ""

    # 可解释性
    thoughts: list[AgentThought] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)

    # 元信息
    pipeline_id: str = ""
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None


# ========== API 请求/响应 ==========

class AnalyzeRequest(BaseModel):
    """前端发送的分析请求"""
    sensor_data: SensorData
    scene_id: Optional[str] = None


class AgentEvent(BaseModel):
    """实时推送给前端的 Agent 事件"""
    event_type: str                    # agent_start / agent_complete / thought / error / pipeline_complete
    agent: Optional[AgentRole] = None
    step: int = 0
    data: dict = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class AnalyzeResponse(BaseModel):
    """完整分析结果响应"""
    pipeline_id: str
    status: TaskStatus
    perception: Optional[PerceptionResult] = None
    prediction: Optional[PredictionResult] = None
    decision: Optional[DecisionResult] = None
    verification: Optional[VerificationResult] = None
    thoughts: list[AgentThought] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    duration_ms: float = 0
