# 🏠 Smart Home Multi-Agent System

> **AI-powered elderly care smart home platform** — Using Multi-Agent collaboration to replace traditional rule engines, enabling smart homes that truly "understand" elderly needs.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-orange.svg)](https://langchain-ai.github.io/langgraph/)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-Compatible-41BDF5.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<p align="center">
  <b>English</b> | <a href="#中文说明">中文说明</a>
</p>

---

## ✨ Key Differentiators

| Feature | Traditional (Mi Home / Google Home) | This System |
|---------|-------------------------------------|-------------|
| Decision Engine | Fixed if-then rules | **Multi-Agent collaborative reasoning** |
| Comfort Control | Manual temperature setting | **AI auto-calculates optimal comfort** |
| Safety Monitoring | Single-sensor alarm | **Multi-sensor fusion + conflict detection + auto-verification** |
| Prediction | None | **30-min trend prediction with Holt-Winters + Newton Cooling** |
| Explainability | Black box | **Full reasoning chain for every decision** |
| Elderly Care | Generic | **Age-adapted comfort model + fall detection** |

---

## 🏗 Architecture

<p align="center">
    <img src="docs/architecture.svg" alt="System Architecture" width="100%">
    </p>

    ### Agent Pipeline Flow

    <p align="center">
        <img src="docs/agent-pipeline.svg" alt="Agent Pipeline" width="100%">
        </p>

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+**

### 1. Clone & Setup

```bash
git clone https://github.com/your-username/smart-home-multi-agent.git
cd smart-home-multi-agent

# Setup Python environment
cd agent_engine
cp .env.example .env       # Edit .env to configure
pip install -r requirements.txt
cd ..

# Setup Node.js
npm install ws
```

### 2. Start Services

```bash
# Terminal 1: Agent Engine
cd agent_engine && python main.py

# Terminal 2: Node.js Gateway
node sync-server.js
```

### 3. Open Browser

```
http://localhost:8080
```

**API Documentation:** http://localhost:8081/docs (Swagger UI)

---

## 🔌 Connect to Home Assistant

Switch from simulation to real devices in **3 steps**:

```bash
# 1. Edit agent_engine/.env
HA_ENABLED=true
HA_URL=http://your-ha-ip:8123
HA_TOKEN=your-long-lived-access-token

# 2. Map your entities
HA_ENTITY_BEDROOM_TEMP=sensor.bedroom_temperature
HA_ENTITY_BEDROOM_HUMIDITY=sensor.bedroom_humidity
HA_ENTITY_BEDROOM_PRESENCE=binary_sensor.bedroom_fp2_presence
HA_ENTITY_BEDROOM_AC=climate.bedroom_ac

# 3. Restart Agent Engine
cd agent_engine && python main.py
```

See [`.env.example`](agent_engine/.env.example) for all available entity mappings.

---

## 📐 Scientific Models

### Humidex (Body-Felt Temperature)

Standard dew-point method — **Masterson & Richardson (1979)**

```
Td = 243.04 × γ / (17.625 - γ)          # Magnus-Tetens dew point
e  = 6.11 × exp(5417.753 × (1/273.16 - 1/(Td+273.15)))  # Clausius-Clapeyron
Hx = T + 5/9 × (e - 10)                  # Humidex
```

### Hybrid Prediction Engine

| Data Available | Model | Reference |
|----------------|-------|-----------|
| ≥ 24 points (2h) | **Holt-Winters** (triple exponential smoothing) | Winters (1960) |
| ≥ 3 points (15min) | **Holt** (double exponential smoothing) | Holt (1957) |
| < 3 points | **Newton Cooling Law** + sinusoidal solar model | Newton (1701) |
| Mixed | **Weighted hybrid** (statistical × confidence + physical × (1-confidence)) | Makridakis et al. (2018) |

### Comfort Classification

Extended Humidex with:
- **Metabolic rate (met)** correction: sleep=0.8, sitting=1.2, walking=2.0
- **Elderly adaptation**: 10% tighter thresholds (age_factor=1.1)

---

## 📁 Project Structure

```
smart-home-multi-agent/
├── gai.html                    # Frontend (5000+ lines, single-file SPA)
├── sync-server.js              # Node.js Gateway + WebSocket relay
├── mobile-control.html         # Mobile control panel
├── Dockerfile                  # Docker image
├── docker-compose.yml          # One-click deployment
├── docker-start.sh             # Container startup script
├── LICENSE                     # MIT License
├── README.md                   # This file
│
└── agent_engine/               # Python Multi-Agent Engine v3.0
    ├── main.py                 # FastAPI entry + lifespan + scheduler
    ├── config.py               # Configuration (LLM/HA/DB/Scheduler)
    ├── .env.example            # Environment template (safe to commit)
    ├── requirements.txt        # Python dependencies
    │
    ├── agents/
    │   ├── supervisor.py       # Supervisor Agent + LangGraph orchestration
    │   ├── workers.py          # 4 Worker Agents
    │   └── mock_llm.py         # Mock LLM (→ real LLM one-line switch)
    │
    ├── models/
    │   └── schemas.py          # Pydantic data models
    │
    ├── tools/
    │   ├── smart_home_tools.py # Humidex + Holt-Winters + Newton Cooling
    │   ├── ha_bridge.py        # HA REST API bridge (real/mock dual-mode)
    │   ├── ha_websocket.py     # HA WebSocket real-time events
    │   ├── database.py         # SQLite persistence + preference learning
    │   ├── scheduler.py        # Background scheduler + daily report
    │   └── notification.py     # Multi-channel alerts
    │
    ├── api/
    │   └── routes.py           # 30+ API endpoints
    │
    └── data/                   # Auto-created at runtime
        └── smart_home.db       # SQLite database
```

---

## 🔧 API Overview (30+ Endpoints)

| Category | Endpoint | Method | Description |
|----------|----------|--------|-------------|
| **Agent** | `/api/analyze` | POST | Run full Agent pipeline |
| | `/api/analyze/stream` | POST | SSE streaming pipeline |
| | `/api/ws/analyze` | WS | WebSocket real-time |
| | `/api/health` | GET | Health check |
| **HA Bridge** | `/api/ha/status` | GET | HA connection status |
| | `/api/ha/sensors` | GET | All room sensor data |
| | `/api/ha/sensors/{room}` | GET | Room sensor data |
| | `/api/ha/control` | POST | Device control |
| | `/api/ha/analyze-live` | POST | Live sensor → Agent → control |
| **Scenarios** | `/api/scenarios` | GET | List preset scenarios |
| | `/api/scenarios/{id}` | POST | Run scenario |
| **Data** | `/api/data/decisions` | GET | Decision history |
| | `/api/data/sensors/stats/{room}` | GET | Sensor statistics |
| **Scheduler** | `/api/scheduler/status` | GET | Scheduler status |
| **Notifications** | `/api/notifications` | GET | Notification history |
| | `/api/notifications/webhook` | POST | Add webhook channel |
| | `/api/notifications/test` | POST | Test notification |
| | `/api/daily-report` | POST | Trigger daily report |
| **WebSocket** | `/api/ha/websocket/status` | GET | HA WS connection status |

Full interactive docs: **http://localhost:8081/docs**

---

## 🐳 Docker Deployment

```bash
docker-compose up -d
```

Or build manually:

```bash
docker build -t smart-home-agent .
docker run -p 8080:8080 -p 8081:8081 smart-home-agent
```

---

## 🧪 Demo Scenarios

Test abnormal scenarios to see the Agent's self-correction:

```bash
# Sensor conflict → Verification fails → Supervisor re-plans 3x
curl -X POST http://localhost:8081/api/scenarios/sensor_conflict

# Fall detection → Emergency alert + immediate analysis
curl -X POST http://localhost:8081/api/scenarios/fall_detected

# Empty room → Energy saving mode
curl -X POST http://localhost:8081/api/scenarios/empty_room

# Extreme heat → Emergency cooling
curl -X POST http://localhost:8081/api/scenarios/extreme_heat
```

---

## 🗺️ Roadmap

- [x] Multi-Agent Engine (LangGraph)
- [x] Home Assistant Bridge (REST + WebSocket)
- [x] SQLite Persistence + Preference Learning
- [x] Background Scheduler + Auto-Decision
- [x] Multi-Channel Notifications
- [x] Hybrid Prediction (Holt-Winters + Newton)
- [x] 4 Abnormal Scenarios with Auto-Recovery
- [ ] Real LLM Integration Testing (DeepSeek/GPT-4)
- [ ] Family Dashboard (remote monitoring)
- [ ] Voice Control Integration
- [ ] Energy Consumption Analytics
- [ ] Mobile PWA App

---

## 📄 License

[MIT License](LICENSE)

---

<a id="中文说明"></a>

## 🇨🇳 中文说明

### 项目简介

这是一个面向**老年人居家养护**的全屋智能系统，使用 Multi-Agent 架构替代传统规则引擎：

- **Supervisor Agent** 统筹调度，失败自动重规划
- **感知 Agent** 多传感器融合，判断人员占用状态
- **预测 Agent** Holt-Winters + Newton 冷却定律，30分钟趋势预测
- **决策 Agent** 基于 Humidex 偏差的精确温度调控
- **验证 Agent** 安全检查 + 冲突检测，不合格则回退

### 核心技术栈

| 组件 | 技术 |
|------|------|
| Agent 编排 | LangGraph (状态图) |
| 后端 | FastAPI + Uvicorn |
| 前端 | 原生 HTML/CSS/JS (5000+ 行) |
| 网关 | Node.js + WebSocket |
| 智能家居 | Home Assistant REST API + WebSocket |
| 数据存储 | SQLite |
| 部署 | Docker Compose |

### 快速启动

```bash
# 克隆项目
git clone https://github.com/your-username/smart-home-multi-agent.git
cd smart-home-multi-agent

# 配置环境
cd agent_engine
cp .env.example .env
pip install -r requirements.txt

# 启动（两个终端）
python main.py          # 终端1: Agent 引擎
cd .. && node sync-server.js  # 终端2: Node 网关

# 打开浏览器
# http://localhost:8080
```

### 对接 Home Assistant

1. 在 HA 中创建**长期访问令牌**
2. 修改 `.env`：`HA_ENABLED=true`，填入 Token 和 Entity ID
3. 重启 Agent Engine

详细配置见 [`.env.example`](agent_engine/.env.example)