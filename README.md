# Incident Commander

A multi-agent incident response system built with [`pydantic-ai`](https://ai.pydantic.dev/) and OpenRouter. When metric anomalies are detected, three specialist AI agents collaborate to investigate the incident and produce a root cause analysis (RCA) report.

---

## How It Works

```
Mock Timeseries (5 metrics)
        │
        ▼
  WindowTrigger (sliding window anomaly detection)
        │
        ▼
  MetricsAgent ──────────────────────────────┐
                                             ▼
  LogsAgent ─────────────────────────► CommanderAgent ──► rca_reports/incident_<id>.md
```

### Pipeline

1. **Anomaly Detection** (`triggers.py`)
   Generates mock timeseries for five metrics and runs each through a `WindowTrigger`. Any point exceeding mean + N×σ fires a `MetricAnomaly`.

   | Metric | Normal | Anomaly Pattern | Scenario |
   |--------|--------|-----------------|----------|
   | `cpu_usage` | 40% | spike (3×) | General overload |
   | `error_rate` | 0.5/s | spike (3×) | Service errors |
   | `latency_p99` | 120ms | spike (3×) | General slowness |
   | `checkout_latency_p99` | 210ms | spike to ~2000ms | Latent config bug |
   | `heap_usage_mb` | 512 MB | gradual linear ramp to ~1800 MB | Memory leak |

2. **MetricsAgent** (`agents/metrics_agent.py`)
   A pydantic-ai agent that receives the list of anomalies and investigates further using two tools:
   - `query_metric` — fetches timeseries points for a metric in a given window
   - `compare_baseline` — computes percent deviation vs. a baseline window
   Returns a plain-text triage summary.

3. **LogsAgent** (`agents/logs_agent.py`)
   Receives the anomaly timeframe and analyzes logs using:
   - `fetch_logs` — retrieves mock log lines for a service filtered by level
   - `search_logs` — searches across all services for a pattern
   Returns a structured `LogSummary` (Pydantic model).

4. **CommanderAgent** (`agents/commander_agent.py`)
   The orchestrator. Receives both the metric anomalies and log summary as an `IncidentContext` and runs the full **INVESTIGATE → DECIDE → ACT → REPORT** loop using five tools:
   - `get_metric_details` — deeper per-metric analysis (includes `trend` field: `sudden_spike` vs `gradual_ramp`)
   - `get_log_details` — per-service log error summary with scenario-specific detail
   - `list_recent_deploys` — mock deployment events anchored to the incident window
   - `get_code_diff` — mock diff for a service commit
   - `write_rca` — writes the final RCA markdown file and signals completion
   Returns a structured `RCAReport` (Pydantic model).

   The agent is prompted to recognise three investigation patterns:
   - **Deploy correlation** — a deployment within 30 minutes of symptom onset is a strong root cause signal → recommend rollback
   - **Latency + DB pool exhaustion** — check connection pool settings in code diffs
   - **Heap gradual ramp** — monotonically growing heap with no sawtooth = memory leak → recommend pod restart + heap dump

---

## Project Structure

```
incident_commander/
├── main.py                  # Entrypoint — wires the full pipeline
├── models.py                # Shared Pydantic models
├── triggers.py              # Timeseries generation + WindowTrigger
├── agents/
│   ├── metrics_agent.py     # MetricsAgent
│   ├── logs_agent.py        # LogsAgent
│   └── commander_agent.py   # CommanderAgent + RCA writer
└── rca_reports/             # Output directory (created at runtime)
```

### Shared Models (`models.py`)

| Model | Description |
|-------|-------------|
| `MetricAnomaly` | A single anomalous data point with metric name, value, threshold, timestamp |
| `LogSummary` | Structured log analysis result with key entries and summary text |
| `IncidentContext` | Combined input to CommanderAgent: anomalies + log summary + trigger time |
| `RCAReport` | Final output: root cause, timeline, affected services, remediation steps, confidence |

---

## Setup

### Local

**Requirements:** Python 3.14+, [`uv`](https://docs.astral.sh/uv/)

```bash
# Install dependencies
uv sync

# Configure environment (.envrc is loaded automatically with direnv)
export OPENROUTER_API_KEY=sk-or-...
export ARTIFACTS_GCS_DIR=gs://your-bucket/rca-reports   # optional; skipped if unset

# Run
uv run python main.py
```

The RCA report is written to `rca_reports/incident_<id>.md` and uploaded to GCS if `ARTIFACTS_GCS_DIR` is set.

---

### Cloud Run (via GitHub Actions)

The pipeline runs as a **Cloud Run Job** — a single dedicated execution with no concurrency or resource sharing.

#### GCP prerequisites

```bash
PROJECT_ID=YOUR_PROJECT_ID
REGION=YOUR_REGION
REPO=YOUR_REPO

# Enable APIs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  --project "$PROJECT_ID"

# Create Artifact Registry repo
gcloud artifacts repositories create "$REPO" \
  --repository-format docker \
  --location "$REGION" \
  --project "$PROJECT_ID"

# Store OpenRouter key in Secret Manager
echo -n "sk-or-..." | gcloud secrets create OPENROUTER_API_KEY \
  --data-file=- --project "$PROJECT_ID"

# Create a dedicated service account for the Cloud Run Job
gcloud iam service-accounts create incident-commander-runner \
  --project "$PROJECT_ID"

SA="incident-commander-runner@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant it access to the secret and the GCS bucket
gcloud secrets add-iam-policy-binding OPENROUTER_API_KEY \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor \
  --project "$PROJECT_ID"

gsutil iam ch "serviceAccount:${SA}:roles/storage.objectCreator" \
  gs://incident-commander-artifacts
```

#### Workload Identity Federation (keyless auth for GitHub Actions)

```bash
# Create WIF pool and provider
gcloud iam workload-identity-pools create github-pool \
  --location global --project "$PROJECT_ID"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location global \
  --workload-identity-pool github-pool \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --project "$PROJECT_ID"

# Bind to the service account (replace ORG/REPO with your GitHub org and repo)
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')/locations/global/workloadIdentityPools/github-pool/attribute.repository/ORG/REPO" \
  --project "$PROJECT_ID"
```

#### GitHub Actions secrets

Set these in **Settings → Secrets → Actions**:

| Secret | Value |
|--------|-------|
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | `incident-commander-runner@PROJECT_ID.iam.gserviceaccount.com` |
| `CLOUD_RUN_SA` | same as above |
| `ARTIFACTS_GCS_DIR` | `gs://incident-commander-artifacts/rca-reports` |

#### Workflow

Every push to `main`:
1. Builds the Docker image
2. Pushes `:sha` and `:latest` tags to Artifact Registry
3. Creates or updates the Cloud Run Job with the new image
4. Executes the job immediately (`--execute-now`)
5. Waits for completion and surfaces exit code back to the workflow

Update `PROJECT_ID`, `REGION`, and `REPO` at the top of `.github/workflows/deploy.yml` before first use.

---

## Simulated Scenarios

### Latent Configuration Bug — `checkout-service`
- **Trigger:** `checkout_latency_p99` spikes to ~2000ms
- **Root cause:** `checkout-service-config v2.4.1` deployed 15 minutes prior reduced DB pool from 20→2 connections and tightened timeout from 30s→5s
- **Evidence chain:** deploy list → code diff → DB pool exhaustion errors in logs → zero errors before config reload
- **Expected remediation:** immediate rollback of `checkout-service-config` to previous version

### Memory Leak — `worker-service`
- **Trigger:** `heap_usage_mb` ramps linearly from 512 MB → ~1800 MB (gradual ramp, not a spike)
- **Root cause:** `EventListenerRegistry` accumulating listeners with no cleanup; `CacheManager` holding 312k entries with no TTL
- **Evidence chain:** monotonic heap growth + no sawtooth → GC pauses escalating → OOMKilled pod restarts
- **Expected remediation:** immediate pod restart to restore service; heap dump capture + code review for listener/cache leak

---

## Configuration

**Anomaly sensitivity** — edit `WindowTrigger` defaults in `triggers.py`:

```python
WindowTrigger(window_seconds=300, threshold_multiplier=2.5)
```

- Lower `threshold_multiplier` → more sensitive (fires earlier)
- Higher `threshold_multiplier` → less sensitive (only fires on extreme spikes)

**Model** — all agents use `openrouter:anthropic/claude-sonnet-4-5`. Change in each agent file's `_make_agent()` function.

---

## Example Output

```
=== Incident Commander ===

[1/4] Detecting anomalies...
      Detected 40 anomaly/anomalies:
      - cpu_usage: 161.53 (threshold: 142.06)
      - error_rate: 2.04 (threshold: 1.77)
      - latency_p99: 484.10 (threshold: 424.85)
      - checkout_latency_p99: 2143.77 (threshold: 892.14)
      - heap_usage_mb: 1682.34 (threshold: 1401.22)
      ...

[2/4] Running MetricsAgent...
[3/4] Running LogsAgent...
[4/4] Running CommanderAgent (INVESTIGATE → DECIDE → ACT → REPORT)...

=== Incident Complete ===
RCA written: rca_reports/incident_INC-20240315-001.md
Root cause: Two concurrent incidents: (1) checkout-service DB pool exhausted due to
config change 15min prior; (2) worker-service memory leak from unregistered listeners.
Confidence: 91%
Affected services: checkout-service, worker-service, postgres
```
