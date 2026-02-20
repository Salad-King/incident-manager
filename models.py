from datetime import datetime
from pydantic import BaseModel


class MetricAnomaly(BaseModel):
    metric_name: str
    value: float
    threshold: float
    timestamp: datetime
    window_seconds: int


class LogSummary(BaseModel):
    timeframe_start: str
    timeframe_end: str
    entries: list[str]
    summary: str


class IncidentContext(BaseModel):
    anomalies: list[MetricAnomaly]
    log_summary: LogSummary
    triggered_at: datetime


class RCAReport(BaseModel):
    incident_id: str
    root_cause: str
    timeline: str
    affected_services: list[str]
    remediation_steps: list[str]
    confidence: float
