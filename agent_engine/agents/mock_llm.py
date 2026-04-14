"""
Mock LLM - 模拟大模型推理响应
后续替换为真实大模型API时，只需修改 get_llm() 函数

支持的 Provider:
- mock: 基于规则的模拟响应（默认）
- openai: OpenAI GPT 系列
- deepseek: DeepSeek
- qwen: 通义千问
"""
import json
import time
from typing import Any
from config import LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class MockLLM:
    """基于规则的模拟LLM，输出格式与真实LLM一致"""

    def __init__(self):
        self.call_count = 0

    async def ainvoke(self, prompt: str, tools: list[dict] | None = None) -> dict:
        """
        模拟异步LLM调用
        返回格式: {"content": str, "tool_calls": list[dict]}
        """
        self.call_count += 1
        await self._simulate_latency()

        if tools:
            return self._handle_tool_call(prompt, tools)
        return self._handle_chat(prompt)

    def invoke(self, prompt: str, tools: list[dict] | None = None) -> dict:
        """同步版本"""
        self.call_count += 1
        if tools:
            return self._handle_tool_call(prompt, tools)
        return self._handle_chat(prompt)

    async def _simulate_latency(self):
        """模拟网络延迟"""
        import asyncio
        await asyncio.sleep(0.1)

    def _handle_chat(self, prompt: str) -> dict:
        """处理普通对话"""
        return {
            "content": self._generate_response(prompt),
            "tool_calls": []
        }

    def _handle_tool_call(self, prompt: str, tools: list[dict]) -> dict:
        """处理带工具调用的请求（模拟 Function Calling）"""
        prompt_lower = prompt.lower()

        # 根据 prompt 内容判断应调用哪个工具
        for tool in tools:
            tool_name = tool.get("name", "")

            if "感知" in prompt or "occupancy" in prompt_lower or "sensor" in prompt_lower:
                if tool_name == "analyze_sensors":
                    return self._mock_sensor_analysis(prompt)

            if "预测" in prompt or "predict" in prompt_lower or "forecast" in prompt_lower:
                if tool_name == "predict_trend":
                    return self._mock_prediction(prompt)

            if "决策" in prompt or "decision" in prompt_lower or "control" in prompt_lower:
                if tool_name == "make_decision":
                    return self._mock_decision(prompt)

            if "验证" in prompt or "verify" in prompt_lower or "validate" in prompt_lower:
                if tool_name == "validate_plan":
                    return self._mock_validation(prompt)

        # 默认返回普通对话
        return self._handle_chat(prompt)

    def _generate_response(self, prompt: str) -> str:
        """生成通用响应"""
        if "分析" in prompt or "analyze" in prompt.lower():
            return "我已经分析了传感器数据，需要调用相应的工具来获取详细结果。"
        if "总结" in prompt or "summary" in prompt.lower():
            return "分析完成。系统已根据多维传感器数据做出智能决策，确保居住者舒适度。"
        return "收到指令，正在处理中。"

    def _mock_sensor_analysis(self, prompt: str) -> dict:
        """模拟传感器分析（Function Calling 格式）"""
        return {
            "content": "正在分析传感器数据，调用 analyze_sensors 工具...",
            "tool_calls": [{
                "name": "analyze_sensors",
                "arguments": {
                    "check_radar": True,
                    "check_pir": True,
                    "check_door": True,
                    "detect_anomalies": True
                }
            }]
        }

    def _mock_prediction(self, prompt: str) -> dict:
        """模拟预测分析"""
        return {
            "content": "正在进行温度趋势预测，调用 predict_trend 工具...",
            "tool_calls": [{
                "name": "predict_trend",
                "arguments": {
                    "horizon_minutes": 30,
                    "use_holt": True,
                    "compute_humidex": True
                }
            }]
        }

    def _mock_decision(self, prompt: str) -> dict:
        """模拟控制决策"""
        return {
            "content": "根据分析结果制定调控方案，调用 make_decision 工具...",
            "tool_calls": [{
                "name": "make_decision",
                "arguments": {
                    "optimize_comfort": True,
                    "energy_saving": True,
                    "consider_prediction": True
                }
            }]
        }

    def _mock_validation(self, prompt: str) -> dict:
        """模拟方案验证"""
        return {
            "content": "正在验证调控方案的合理性，调用 validate_plan 工具...",
            "tool_calls": [{
                "name": "validate_plan",
                "arguments": {
                    "check_safety": True,
                    "check_conflict": True,
                    "check_energy": True
                }
            }]
        }


def get_llm():
    """
    获取 LLM 实例
    根据 LLM_PROVIDER 配置返回对应的 LLM

    后续接入真实模型时，只需在此函数中添加分支:
    if LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY)
    """
    if LLM_PROVIDER == "mock":
        return MockLLM()

    # 预留真实模型接口
    if LLM_PROVIDER == "openai":
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=LLM_MODEL,
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL or None,
            )
        except ImportError:
            print("[WARN] langchain_openai 未安装，回退到 MockLLM")
            return MockLLM()

    if LLM_PROVIDER == "deepseek":
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=LLM_MODEL or "deepseek-chat",
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL or "https://api.deepseek.com/v1",
            )
        except ImportError:
            print("[WARN] langchain_openai 未安装，回退到 MockLLM")
            return MockLLM()

    print(f"[WARN] 未知 LLM_PROVIDER: {LLM_PROVIDER}，使用 MockLLM")
    return MockLLM()
