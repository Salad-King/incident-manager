import asyncio
import os
import subprocess
from datetime import datetime, timedelta

from triggers import detect_anomalies
from agents.metrics_agent import run_metrics_agent
from agents.logs_agent import run_logs_agent
from agents.commander_agent import run_commander_agent
from models import IncidentContext

ARTIFACTS_GCS_DIR = os.environ.get("ARTIFACTS_GCS_DIR", "")


def upload_to_gcs(local_path: str) -> None:
    if not ARTIFACTS_GCS_DIR:
        print("      ARTIFACTS_GCS_DIR not set — skipping GCS upload.")
        return
    dest = f"{ARTIFACTS_GCS_DIR.rstrip('/')}/{os.path.basename(local_path)}"
    print(f"      Uploading {local_path} → {dest}")
    result = subprocess.run(
        ["gsutil", "cp", local_path, dest],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"      GCS upload failed: {result.stderr.strip()}")
    else:
        print(f"      Uploaded: {dest}")


async def main():
    print("=== Incident Commander ===\n")

    print("[1/4] Detecting anomalies...")
    anomalies = detect_anomalies()
    if not anomalies:
        print("No anomalies detected. System healthy.")
        return

    print(f"      Detected {len(anomalies)} anomaly/anomalies:")
    for a in anomalies:
        print(f"      - {a.metric_name}: {a.value:.2f} (threshold: {a.threshold:.2f})")

    triggered_at = max(a.timestamp for a in anomalies)
    window_start = (triggered_at - timedelta(minutes=30)).isoformat()
    window_end = triggered_at.isoformat()

    print("\n[2/4] Running MetricsAgent...")
    metrics_summary = await run_metrics_agent(anomalies)
    print(f"      MetricsAgent summary: {metrics_summary[:120]}...")

    print("\n[3/4] Running LogsAgent...")
    log_summary = await run_logs_agent(window_start, window_end)
    print(f"      LogsAgent summary: {log_summary.summary[:120]}...")

    print("\n[4/4] Running CommanderAgent (INVESTIGATE → DECIDE → ACT → REPORT)...")
    context = IncidentContext(
        anomalies=anomalies,
        log_summary=log_summary,
        triggered_at=triggered_at,
    )
    rca = await run_commander_agent(context)

    local_rca_path = f"rca_reports/incident_{rca.incident_id}.md"
    print(f"\n=== Incident Complete ===")
    print(f"RCA written: {local_rca_path}")
    print(f"Root cause: {rca.root_cause[:160]}")
    print(f"Confidence: {rca.confidence * 100:.0f}%")
    print(f"Affected services: {', '.join(rca.affected_services)}")

    upload_to_gcs(local_rca_path)


if __name__ == "__main__":
    asyncio.run(main())
