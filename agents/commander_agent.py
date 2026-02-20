import random
from datetime import datetime, timedelta
from pathlib import Path
from pydantic_ai import Agent, RunContext
from models import IncidentContext, RCAReport


def _make_agent() -> Agent:
    agent = Agent(
        model="openrouter:anthropic/claude-sonnet-4-5",
        output_type=RCAReport,
        deps_type=IncidentContext,
        system_prompt=(
            "You are an incident commander. Given metrics anomalies and log summaries, "
            "investigate the incident using available tools to gather additional context "
            "(deploys, code diffs, metric details, log details). "
            "Follow the loop: INVESTIGATE → DECIDE → ACT → REPORT. "
            "Key investigation patterns to look for: "
            "(1) Correlate anomaly onset time with recent deployments — a deploy within 30 minutes before "
            "symptoms begin is a strong root cause signal. "
            "(2) For latency spikes, check DB connection pool settings and timeouts in code diffs. "
            "(3) For heap_usage_mb showing a gradual_ramp trend (not a spike), treat this as a memory leak — "
            "check for listener/cache accumulation, missing TTLs, or unbounded queues in logs. "
            "(4) If a config or infrastructure change is the root cause, include an immediate rollback "
            "as the first remediation step. "
            "(5) If a memory leak is confirmed, recommend pod restart as immediate mitigation and a "
            "heap dump + code review as the follow-up action. "
            "When you have enough information, call write_rca with your findings."
        ),
    )

    @agent.tool
    def get_metric_details(ctx: RunContext[IncidentContext], metric_name: str, start: str, end: str) -> dict:
        """Get detailed metric data for a specific metric in the given time window."""
        base = {
            "cpu_usage": 40.0,
            "error_rate": 0.5,
            "latency_p99": 120.0,
            "checkout_latency_p99": 210.0,
            "heap_usage_mb": 512.0,
        }.get(metric_name, 50.0)
        if metric_name == "checkout_latency_p99":
            current_mean = 2050.0
        elif metric_name == "heap_usage_mb":
            current_mean = 1680.0  # near OOM territory
        else:
            current_mean = base * 3.1
        baseline_mean = base
        return {
            "metric": metric_name,
            "window": {"start": start, "end": end},
            "current_mean": round(current_mean, 4),
            "baseline_mean": round(baseline_mean, 4),
            "deviation_percent": round(((current_mean - baseline_mean) / baseline_mean) * 100, 2),
            "peak_value": round(current_mean * 1.2, 4),
            "anomalous_points": random.randint(5, 15),
            "trend": "gradual_ramp" if metric_name == "heap_usage_mb" else "sudden_spike",
        }

    @agent.tool
    def get_log_details(ctx: RunContext[IncidentContext], service: str, start: str, end: str) -> dict:
        """Get log summary details for a service in the given time window."""
        if service == "worker-service":
            return {
                "service": service,
                "window": {"start": start, "end": end},
                "error_count": 89,
                "warn_count": 143,
                "top_errors": [
                    "java.lang.OutOfMemoryError: Java heap space — 4 occurrences",
                    "GC overhead limit exceeded — 11 occurrences",
                    "Pod OOMKilled by kubelet — 4 restarts in window",
                ],
                "error_rate_per_minute": 3.2,
                "heap_trend": "512 MB → 1680 MB over 100 minutes (linear ramp, no sawtooth)",
                "gc_pauses_ms": [12, 45, 340, 890, 2100],
                "note": "Heap grows monotonically — no release between GC cycles. Classic leak signature.",
            }
        if service == "checkout-service":
            return {
                "service": service,
                "window": {"start": start, "end": end},
                "error_count": 312,
                "warn_count": 47,
                "top_errors": [
                    "DB connection timeout after 5000ms (pool exhausted) — 187 occurrences",
                    "Failed to acquire DB connection: pool_size=2, active=2, idle=0 — 98 occurrences",
                    "p99 latency 2143ms breached SLA threshold of 500ms — 27 occurrences",
                ],
                "error_rate_per_minute": 24.7,
                "note": "Errors begin sharply at config reload event. Zero errors in preceding 24h window.",
            }
        errors = [
            f"Connection pool exhausted for {service}",
            f"Upstream timeout from {service} to postgres",
            f"Circuit breaker opened for {service}",
            f"OOM event in {service} pod, restarted",
        ]
        return {
            "service": service,
            "window": {"start": start, "end": end},
            "error_count": random.randint(50, 300),
            "warn_count": random.randint(10, 80),
            "top_errors": random.sample(errors, k=min(3, len(errors))),
            "error_rate_per_minute": round(random.uniform(5.0, 25.0), 2),
        }

    @agent.tool
    def list_recent_deploys(ctx: RunContext[IncidentContext], start: str, end: str) -> list[dict]:
        """List recent deployment events between start and end ISO timestamps."""
        # The latent config bug: checkout-service-config deployed 15 minutes before the anomaly
        config_deploy_time = ctx.deps.triggered_at - timedelta(minutes=15)
        deploys = [
            {
                "service": "checkout-service",
                "deploy_type": "config",
                "artifact": "checkout-service-config",
                "commit_sha": "cf9a12d",
                "version": "v2.4.1",
                "deployed_at": config_deploy_time.isoformat(),
                "deployed_by": "ci-pipeline",
                "status": "success",
                "change_summary": "Tuned DB pool settings for 'cost optimisation' initiative",
            },
            {
                "service": "api-gateway",
                "deploy_type": "service",
                "commit_sha": f"a{random.randint(100000, 999999)}b",
                "version": f"v3.{random.randint(1, 4)}.{random.randint(0, 10)}",
                "deployed_at": (ctx.deps.triggered_at - timedelta(hours=4)).isoformat(),
                "deployed_by": "ci-pipeline",
                "status": "success",
                "change_summary": "Bumped rate limiter defaults",
            },
        ]
        return deploys

    @agent.tool
    def get_code_diff(ctx: RunContext[IncidentContext], service: str, commit_sha: str) -> dict:
        """Get a mock code diff snippet for a service at a given commit SHA."""
        diffs = {
            "checkout-service": (
                "diff --git a/config/db.yaml b/config/db.yaml\n"
                "--- a/config/db.yaml\n"
                "+++ b/config/db.yaml\n"
                "@@ -3,8 +3,8 @@ database:\n"
                "   host: postgres-primary.internal\n"
                "   port: 5432\n"
                "   pool:\n"
                "-    max_connections: 20\n"
                "+    max_connections: 2        # COST-OPT: reduced pool size\n"
                "-    connection_timeout_ms: 30000\n"
                "+    connection_timeout_ms: 5000  # COST-OPT: tighter timeout\n"
                "   query_timeout_ms: 10000\n"
            ),
            "api-gateway": (
                "diff --git a/src/pool.js\n"
                "-  maxConnections: 50\n"
                "+  maxConnections: 10  // reduced for cost savings\n"
            ),
        }
        diff = diffs.get(service, "diff --git a/src/main\n- // no significant changes\n+ // minor refactor\n")
        return {
            "service": service,
            "commit_sha": commit_sha,
            "diff": diff,
            "files_changed": random.randint(1, 5),
            "lines_added": random.randint(5, 50),
            "lines_removed": random.randint(5, 30),
        }

    @agent.tool
    def write_rca(ctx: RunContext[IncidentContext], report: RCAReport) -> str:
        """Write the RCA report to a markdown file and return the file path."""
        rca_dir = Path("rca_reports")
        rca_dir.mkdir(exist_ok=True)

        filename = rca_dir / f"incident_{report.incident_id}.md"
        triggered_at = ctx.deps.triggered_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        md = f"""# Incident RCA: {report.incident_id}

**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
**Triggered:** {triggered_at}
**Confidence:** {report.confidence * 100:.0f}%

---

## Root Cause

{report.root_cause}

---

## Timeline

{report.timeline}

---

## Affected Services

{chr(10).join(f'- {svc}' for svc in report.affected_services)}

---

## Remediation Steps

{chr(10).join(f'{i+1}. {step}' for i, step in enumerate(report.remediation_steps))}

---

*RCA generated by Incident Commander multi-agent system*
"""
        filename.write_text(md)
        return str(filename)

    return agent


async def run_commander_agent(context: IncidentContext) -> RCAReport:
    agent = _make_agent()
    anomaly_text = "\n".join(
        f"- {a.metric_name}: value={a.value}, threshold={a.threshold}, at={a.timestamp.isoformat()}"
        for a in context.anomalies
    )
    log_text = (
        f"Log summary ({context.log_summary.timeframe_start} to {context.log_summary.timeframe_end}):\n"
        f"{context.log_summary.summary}\n\n"
        f"Key log entries:\n" + "\n".join(f"  {e}" for e in context.log_summary.entries[:10])
    )
    prompt = (
        f"An incident has been detected at {context.triggered_at.isoformat()}.\n\n"
        f"## Metric Anomalies\n{anomaly_text}\n\n"
        f"## Log Context\n{log_text}\n\n"
        "Investigate this incident thoroughly using the available tools, "
        "then call write_rca with your findings."
    )
    result = await agent.run(prompt, deps=context)
    return result.output
