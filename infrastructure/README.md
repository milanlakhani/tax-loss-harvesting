# AWS CDK deployment (Chapter 6)

Minimal CDK v2 Python infrastructure for the existing tax-loss harvesting demo. This is **not** a
production topology. The Fargate task runs in **public subnets with `assign_public_ip=True`** so the
demo can reach OpenAI, Alpaca, Alpha Vantage, CoinGecko, Frankfurter, ECR, and AWS APIs **without a
NAT Gateway**. That public task IP is a cost-saving demo choice, not a production recommendation.

Local `APP_ENV=local` and Docker Compose do not require AWS credentials or `ALLOWED_IPV4_CIDR`.
Compose runs FastAPI, FastMCP, Streamlit, and PostgreSQL as **four** services. MCP is not published
to the host unless `docker-compose.debug-mcp.yml` is explicitly enabled.

`ALLOWED_IPV4_CIDR` is AWS/CDK-only.

## What is provisioned

- VPC across two AZs: public subnets (ALB + one Fargate task) and isolated private subnets (RDS)
- No NAT Gateway
- Free gateway VPC endpoints for S3 and DynamoDB
- Single-AZ RDS PostgreSQL `db.t3.micro`, encrypted, not publicly accessible
- Encrypted S3 statement bucket with all public access blocked
- DynamoDB on-demand table (`pk`/`sk`, TTL attribute `ttl`) for rolling windows
- Secrets Manager application secret (JSON keys only) plus the RDS-generated secret
- ECS cluster, one Fargate service, desired count 1
- Backend (FastAPI `:8000`), FastMCP sidecar (`:8001`), and Streamlit (`:8501`) in the **same** task definition
- Public ALB whose inbound HTTP rule is restricted to `ALLOWED_IPV4_CIDR` (never `0.0.0.0/0` by default)
- Target groups: `/api/*`, `/health`, `/health/*` → FastAPI; default → Streamlit
- MCP is **not** on the ALB: no listener, target group, or public security-group ingress for port 8001
- Backend reaches MCP at `MCP_SERVER_URL=http://127.0.0.1:8001/mcp` (task-local sidecar)
- Streamlit target-group stickiness and a 400s ALB idle timeout for WebSocket traffic
- CloudWatch log groups with 14-day retention
- Least-privilege task IAM for this bucket, table, and the two secrets
- Docker image as a CDK asset (ECR)
- One-off migration task definition (`alembic upgrade head`) using the same image, secrets, and network

The task security group allows:

- inbound `8000` and `8501` only from the ALB security group
- outbound PostgreSQL `5432` only to the RDS security group
- outbound HTTPS `443` for providers, AWS APIs, and image pulls
- outbound DNS `53` for the VPC resolver

Port 8001 is reachable only inside the task network namespace (`127.0.0.1`). Do not add ALB or
security-group ingress for MCP.

Do not describe this as “ECS-to-RDS only”. Provider HTTPS and AWS control-plane access are required.

## Intentionally not included

EventBridge, SQS, DLQ, Lambda, background workers, schedulers, RAG, embeddings, ARIMA, forecasting,
authentication, live (non-paper) trading, and a public WhatsApp webhook.

## WhatsApp limitation on AWS

The WhatsApp channel stays **read-only** in the application. Inbound Cloud API webhooks need a
public HTTPS URL. Opening the ALB to `0.0.0.0/0` would violate the demo security model (there is
still **no user authentication**). Inbound WhatsApp is therefore **disabled on AWS**. Use Docker
Compose plus the optional `whatsapp` tunnel profile locally if you need that demonstration.

## Quote sources

Live routing is intentional:

- Alpaca Market Data supplies current EQUITY/ETF quotes (feed `iex` unless `ALPACA_MARKET_DATA_FEED` is set).
- Alpha Vantage supplies historical EQUITY/ETF windows.
- CoinGecko supplies current and historical CRYPTO prices.
- Frankfurter supplies FX.
- Alpaca Trading supplies positions, quantities, orders, and fills.

The Alpaca market quote and the Alpaca fill remain separate records. CDK does not change that
application behavior.

## Secrets

Mandatory non-blank JSON keys in the application secret:

- `OPENAI_API_KEY`
- `ALPHA_VANTAGE_API_KEY`
- `COINGECKO_API_KEY` (mandatory, not optional)
- `ALPACA_ACCOUNT_1_KEY` / `ALPACA_ACCOUNT_1_SECRET`
- `ALPACA_ACCOUNT_2_KEY` / `ALPACA_ACCOUNT_2_SECRET`
- `DEMO_SESSION_SIGNING_SECRET`

Frankfurter needs no secret. Database username/password come from the **RDS-generated** secret.
Never put values in CDK code, `cdk.json`, context, CloudFormation outputs, images, logs, or git.
Copy `app-secret.example.json` to `app-secret.json` (gitignored) and edit locally.

Provider keys needed by approved MCP tools (quotes, analysis, statement parse) are injected into the
backend **and** MCP containers. OpenAI and demo-session signing secrets stay on the backend only.
Streamlit receives `BACKEND_URL=http://127.0.0.1:8000` only. MCP receives no `MCP_SERVER_URL` (it is
the server). The backend receives `MCP_SERVER_URL=http://127.0.0.1:8001/mcp`.

## Charges while deployed

RDS `db.t3.micro`, the ALB, one Fargate task (1 vCPU / 2 GB), Secrets Manager, CloudWatch Logs,
S3 storage, and DynamoDB on-demand all incur charges until you destroy the stack. There is **no**
NAT Gateway charge. Public IPv4 addresses attached to the ALB and the task also incur AWS public
IPv4 charges.

## Demo destroy vs production-named environments

`environment=demo` (default) uses destroy-compatible removal policies and empties the generated S3
bucket on `cdk destroy`. **Data will be deleted.**

`environment=production` (also `prod`, `staging`) retains RDS, S3, and DynamoDB. That is not a
silent destroy.

## Commands

All CDK commands run from `infrastructure/` unless noted. Replace region/account placeholders.
These deploy/bootstrap/destroy steps **require the operator's AWS credentials**; they are not run
by the application test suite.

### 1. Install CDK (once per machine)

CDK Python uses the jsii runtime, which requires **Node.js 18+** on PATH in addition to Python 3.12.

```bash
npm install -g aws-cdk@2.178.2
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cdk --version
```

### 2. Configure the operator CIDR

Use a host `/32` such as `203.0.113.10/32`, never `0.0.0.0/0`. The CIDR is shown in `cdk diff`
outputs; it is not a secret.

```bash
# PowerShell
$env:ALLOWED_IPV4_CIDR="203.0.113.10/32"
$env:ENVIRONMENT="demo"
$env:CDK_DEFAULT_ACCOUNT="123456789012"
$env:CDK_DEFAULT_REGION="eu-west-2"
```

```bash
export ALLOWED_IPV4_CIDR=203.0.113.10/32
export ENVIRONMENT=demo
export CDK_DEFAULT_ACCOUNT=123456789012
export CDK_DEFAULT_REGION=eu-west-2
```

Equivalent CDK context: `-c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo`.

### 3. Bootstrap (once per account/region)

Requires AWS credentials.

```bash
cdk bootstrap aws://123456789012/eu-west-2
```

### 4. Synth and diff (CIDR is visible here)

`cdk synth` without `-c container_image=...` builds the Docker image asset and **requires Docker**.
Template-only synth (no image build) is:

```bash
cdk synth -c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo -c container_image=public.ecr.aws/docker/library/python:3.12-slim
```

Without the `cdk` CLI, the same template-only synth is:

```bash
# from infrastructure/ with the venv and Node.js on PATH
export ALLOWED_IPV4_CIDR=198.51.100.10/32
export TLH_CONTAINER_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
python app.py
```

The CIDR appears in `cdk.out` outputs and the ALB security-group ingress rule; it is not a secret.

```bash
cdk synth -c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo
cdk diff -c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo
```

### 5. Deploy

Requires AWS credentials and Docker (image asset push to ECR).

```bash
cdk deploy TaxLossHarvestingDemo -c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo
```

Note the outputs: `LoadBalancerDns`, `AppSecretArn`, `RdsSecretArn`, `ClusterName`, `ServiceName`,
`MigrationTaskDefinitionArn`, `TaskSecurityGroupId`, `PublicSubnetIds`.

### 6. Put application secret values

Requires AWS credentials. Do this before a healthy service start.

```bash
cp app-secret.example.json app-secret.json
# edit app-secret.json locally; do not commit it
aws secretsmanager put-secret-value --secret-id APP_SECRET_ARN --secret-string file://app-secret.json
```

### 7. One-off migration

Requires AWS credentials. Same image, secrets, task role, public subnets, public IP, and task
security group as the backend.

```bash
aws ecs run-task --cluster CLUSTER_NAME --task-definition MIGRATION_TASK_DEFINITION_ARN --launch-type FARGATE --network-configuration "awsvpcConfiguration={subnets=[PUBLIC_SUBNET_ID],securityGroups=[TASK_SECURITY_GROUP_ID],assignPublicIp=ENABLED}"
```

Wait until the task stops with exit code 0:

```bash
aws ecs describe-tasks --cluster CLUSTER_NAME --tasks TASK_ARN --query "tasks[0].{last:lastStatus,exit:containers[0].exitCode}"
```

### 8. Restart the service after secrets or image changes

Requires AWS credentials.

```bash
aws ecs update-service --cluster CLUSTER_NAME --service SERVICE_NAME --force-new-deployment
```

### 9. Health verification

From an IP inside `ALLOWED_IPV4_CIDR`:

```bash
curl http://LOAD_BALANCER_DNS/health
curl http://LOAD_BALANCER_DNS/_stcore/health
curl -I http://LOAD_BALANCER_DNS/api/
```

Open `http://LOAD_BALANCER_DNS/` in a browser. Streamlit WebSockets use the same origin through the
ALB (sticky UI target group, 400s idle timeout). FastAPI remains on `/health` and `/api/*`. There is
no public `/mcp` route; MCP is task-local on port 8001.

### 10. Logs

Requires AWS credentials.

```bash
aws logs tail /aws/ecs/BackendLogGroup --follow
```

Use the log group names from the deployed stack (CloudFormation resources `BackendLogGroup`,
`McpLogGroup`, `StreamlitLogGroup`, `MigrationLogGroup`). Do not expect secrets to appear in logs; do not print them.

### 11. Destroy (demo only — deletes data)

Requires AWS credentials. Warns via the `DestroyWarning` output that RDS, S3 objects, and DynamoDB
items are deleted for `environment=demo`.

```bash
cdk destroy TaxLossHarvestingDemo -c allowed_ipv4_cidr=203.0.113.10/32 -c environment=demo
```

## Local verification that does not need AWS credentials

From the repository root (application tests still need local PostgreSQL):

```bash
python -m pytest -q --tb=line
```

From `infrastructure/` (uses a registry image stub, not a live deploy):

```bash
pip install -r requirements.txt
python -m pytest -q --tb=line
```

Docker image build (local daemon, no AWS account required):

```bash
docker build -t tax-loss-harvesting-demo:local .
```

`cdk synth` with Docker **does** build the asset. `cdk deploy`, `cdk bootstrap`, secret updates,
`ecs run-task`, service restart, log tailing, and destroy **require AWS credentials** and are not
claimed as tested by the application suite.
