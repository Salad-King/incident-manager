import random
from datetime import datetime, timedelta
from pydantic_ai import Agent
from models import MetricAnomaly


def _make_agent() -> Agent:
    agent = Agent(
        model="openrouter:anthropic/claude-sonnet-4-5",
        output_type=str,
        system_prompt=(
            "You are a metrics triage specialist. Analyze the provided metric anomalies, "
            "query additional details using available tools, and produce a concise summary "
            "of what the metrics indicate about system health. Focus on severity, affected "
            "components, and potential causes suggested by the metric patterns."
        ),
    )

    @agent.tool_plain
    def query_metric(metric_name: str, start: str, end: str) -> dict:
        """Query mock timeseries data for a metric between start and end ISO timestamps."""
        start_dt = datetime.fromisoformat(start.replace("Z", ""))
        end_dt = datetime.fromisoformat(end.replace("Z", ""))
        base = {"cpu_usage": 40.0, "error_rate": 0.5, "latency_p99": 120.0}.get(metric_name, 50.0)
        points = {}
        current = start_dt
        while current <= end_dt:
            spike = base * 3 if current >= end_dt - timedelta(minutes=10) else 0.0
            points[current.isoformat()] = round(base + random.gauss(0, base * 0.05) + spike, 4)
            current += timedelta(minutes=1)
        return {"metric": metric_name, "points": points}

    @agent.tool_plain
    def compare_baseline(metric_name: str, current_window: str, baseline_window: str) -> dict:
        """Compare current window metric values against a baseline window."""
        base = {"cpu_usage": 40.0, "error_rate": 0.5, "latency_p99": 120.0}.get(metric_name, 50.0)
        current_mean = base * 3.2 + random.gauss(0, base * 0.1)
        baseline_mean = base + random.gauss(0, base * 0.05)
        deviation_pct = round(((current_mean - baseline_mean) / baseline_mean) * 100, 2)
        return {
            "metric": metric_name,
            "current_mean": round(current_mean, 4),
            "baseline_mean": round(baseline_mean, 4),
            "deviation_percent": deviation_pct,
            "current_window": current_window,
            "baseline_window": baseline_window,
        }

    return agent


async def run_metrics_agent(anomalies: list[MetricAnomaly]) -> str:
    agent = _make_agent()
    anomaly_text = "\n".join(
        f"- {a.metric_name}: value={a.value}, threshold={a.threshold}, at={a.timestamp.isoformat()}"
        for a in anomalies
    )
    prompt = f"Analyze the following metric anomalies and investigate using available tools:\n\n{anomaly_text}"
    result = await agent.run(prompt)
    return result.output
