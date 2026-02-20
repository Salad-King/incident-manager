import random
import math
from datetime import datetime, timedelta
from models import MetricAnomaly


def generate_mock_timeseries(metric_name: str, n: int = 100) -> list[tuple[datetime, float]]:
    """Generate mock timeseries with an injected spike near the end."""
    now = datetime.utcnow()
    base = {
        "cpu_usage": 40.0,
        "error_rate": 0.5,
        "latency_p99": 120.0,
        "checkout_latency_p99": 210.0,  # normal ~210ms
        "heap_usage_mb": 512.0,         # normal heap ~512 MB
    }.get(metric_name, 50.0)
    noise_scale = base * 0.05

    series = []
    for i in range(n):
        ts = now - timedelta(seconds=(n - i) * 60)
        noise = random.gauss(0, noise_scale)
        if metric_name == "checkout_latency_p99":
            # sudden spike to ~2000ms
            spike = 1800.0 if i >= int(n * 0.9) else 0.0
        elif metric_name == "heap_usage_mb":
            # gradual linear ramp — classic memory leak signature
            # climbs from base (512 MB) to ~1800 MB over the full window
            spike = (i / n) * 1300.0
        else:
            spike = base * 3 if i >= int(n * 0.9) else 0.0
        series.append((ts, base + noise + spike))

    return series


class WindowTrigger:
    def __init__(self, window_seconds: int = 300, threshold_multiplier: float = 2.5):
        self.window_seconds = window_seconds
        self.threshold_multiplier = threshold_multiplier

    def check(self, metric_name: str, series: list[tuple[datetime, float]]) -> list[MetricAnomaly]:
        if len(series) < 2:
            return []

        values = [v for _, v in series]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)
        threshold = mean + self.threshold_multiplier * std

        anomalies = []
        for ts, val in series:
            if val > threshold:
                anomalies.append(MetricAnomaly(
                    metric_name=metric_name,
                    value=round(val, 4),
                    threshold=round(threshold, 4),
                    timestamp=ts,
                    window_seconds=self.window_seconds,
                ))
        return anomalies


def detect_anomalies() -> list[MetricAnomaly]:
    trigger = WindowTrigger(window_seconds=300, threshold_multiplier=2.5)
    metrics = ["cpu_usage", "error_rate", "latency_p99", "checkout_latency_p99", "heap_usage_mb"]
    all_anomalies: list[MetricAnomaly] = []
    for metric in metrics:
        series = generate_mock_timeseries(metric)
        all_anomalies.extend(trigger.check(metric, series))
    return all_anomalies
