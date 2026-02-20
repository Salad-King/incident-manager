import random
from datetime import datetime, timedelta
from pydantic_ai import Agent
from models import LogSummary

MOCK_LOG_TEMPLATES = [
    "[ERROR] {service}: Connection timeout after 30s",
    "[WARN]  {service}: High memory usage detected (>85%)",
    "[ERROR] {service}: Database query failed: deadlock detected",
    "[INFO]  {service}: Request rate spike detected",
    "[ERROR] {service}: Upstream dependency {dep} returned 503",
    "[ERROR] {service}: Circuit breaker OPEN for {dep}",
    "[WARN]  {service}: Response time exceeded SLA threshold",
    "[ERROR] {service}: OOM killed, restarting container",
    "[INFO]  {service}: Deployment completed for version {version}",
    "[ERROR] {service}: Health check failed, removing from load balancer",
]

# Injected logs specific to the latent config bug scenario
CHECKOUT_BUG_LOGS = [
    "[ERROR] checkout-service: DB connection timeout after 5000ms (pool exhausted)",
    "[ERROR] checkout-service: Failed to acquire DB connection from pool: timeout=5s exceeded",
    "[ERROR] checkout-service: JDBC pool wait time 4987ms — pool_size=2, active=2, idle=0",
    "[ERROR] checkout-service: Transaction rolled back — upstream postgres unreachable after 5s",
    "[WARN]  checkout-service: DB connection pool nearly exhausted (2/2 connections in use)",
    "[ERROR] checkout-service: p99 latency 2143ms breached SLA threshold of 500ms",
    "[ERROR] checkout-service: Checkout request failed — DB pool timeout, returning 503 to client",
    "[INFO]  checkout-service: Config reload triggered — db.pool.max_connections changed 20 → 2",
    "[INFO]  checkout-service: Config reload triggered — db.connection.timeout changed 30000ms → 5000ms",
    "[WARN]  checkout-service: Applied new config from checkout-service-config v2.4.1",
]

MEMORY_LEAK_LOGS = [
    "[WARN]  worker-service: Heap usage 650 MB — GC pressure increasing",
    "[WARN]  worker-service: Heap usage 820 MB — GC pause 340ms",
    "[WARN]  worker-service: Heap usage 1050 MB — GC pause 890ms, throughput degraded",
    "[ERROR] worker-service: Heap usage 1380 MB — Full GC triggered, STW pause 2.1s",
    "[ERROR] worker-service: Heap usage 1620 MB — GC overhead limit exceeded",
    "[ERROR] worker-service: java.lang.OutOfMemoryError: Java heap space",
    "[ERROR] worker-service: EventListenerRegistry: 48203 listeners registered, 0 removed (likely leak)",
    "[WARN]  worker-service: Cache eviction disabled — CacheManager holding 312k entries (no TTL set)",
    "[ERROR] worker-service: Thread pool queue depth 9842 — tasks accumulating faster than processing",
    "[ERROR] worker-service: Pod OOMKilled by kubelet — restarting (restart #4 in 2h)",
]

SERVICES = ["api-gateway", "auth-service", "payment-service", "checkout-service", "worker-service"]
DEPS = ["postgres", "redis", "kafka", "elasticsearch"]


def _gen_logs(service: str, start_dt: datetime, end_dt: datetime, count: int = 20) -> list[str]:
    logs = []
    # Inject scenario-specific logs for known services
    if service == "worker-service":
        for i, entry in enumerate(MEMORY_LEAK_LOGS):
            # spread evenly across the full window — leak is gradual
            offset = (end_dt - start_dt).total_seconds() * (i / len(MEMORY_LEAK_LOGS))
            ts = start_dt + timedelta(seconds=offset)
            logs.append(f"{ts.strftime('%Y-%m-%dT%H:%M:%SZ')} {entry}")
    if service == "checkout-service":
        for i, entry in enumerate(CHECKOUT_BUG_LOGS):
            # spread them across the second half of the window (after config deploy)
            offset = (end_dt - start_dt).total_seconds() * (0.5 + i * 0.04)
            ts = start_dt + timedelta(seconds=min(offset, (end_dt - start_dt).total_seconds() - 1))
            logs.append(f"{ts.strftime('%Y-%m-%dT%H:%M:%SZ')} {entry}")
    for _ in range(count):
        offset = random.random() * (end_dt - start_dt).total_seconds()
        ts = start_dt + timedelta(seconds=offset)
        template = random.choice(MOCK_LOG_TEMPLATES)
        line = template.format(
            service=service,
            dep=random.choice(DEPS),
            version=f"v1.{random.randint(0, 9)}.{random.randint(0, 99)}",
        )
        logs.append(f"{ts.strftime('%Y-%m-%dT%H:%M:%SZ')} {line}")
    return sorted(logs)


def _make_agent() -> Agent:
    agent = Agent(
        model="openrouter:anthropic/claude-sonnet-4-5",
        output_type=LogSummary,
        system_prompt=(
            "You are a log analysis specialist. Fetch and analyze logs around the anomaly "
            "timeframe using available tools. Identify error patterns, exceptions, and "
            "service degradation signals. Produce a structured log summary."
        ),
    )

    @agent.tool_plain
    def fetch_logs(service: str, start_iso: str, end_iso: str, level: str = "ERROR") -> list[str]:
        """Fetch mock log lines for a service between start_iso and end_iso filtered by level."""
        start_dt = datetime.fromisoformat(start_iso.replace("Z", ""))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", ""))
        all_logs = _gen_logs(service, start_dt, end_dt, count=30)
        if level:
            return [l for l in all_logs if f"[{level}]" in l]
        return all_logs

    @agent.tool_plain
    def search_logs(pattern: str, start_iso: str, end_iso: str) -> list[str]:
        """Search all services for log lines matching pattern between start_iso and end_iso."""
        start_dt = datetime.fromisoformat(start_iso.replace("Z", ""))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", ""))
        matches = []
        for svc in SERVICES:
            logs = _gen_logs(svc, start_dt, end_dt, count=15)
            matches.extend([l for l in logs if pattern.lower() in l.lower()])
        return matches[:20]

    return agent


async def run_logs_agent(start_iso: str, end_iso: str) -> LogSummary:
    agent = _make_agent()
    prompt = (
        f"Analyze logs between {start_iso} and {end_iso}. "
        "Fetch logs for key services and search for errors and timeouts. "
        "Produce a structured LogSummary with the most important entries and an overall summary."
    )
    result = await agent.run(prompt)
    return result.output
