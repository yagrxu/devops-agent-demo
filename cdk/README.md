# DevOps Agent Demo - Infrastructure & Scripts

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     ap-southeast-1                           │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │ ElastiCache      │  │ Aurora Serverless │               │
│  │ Redis Serverless │  │ v2 (PostgreSQL)   │               │
│  │ 5GB / 10K ECPU   │  │ 0.5-4 ACU        │               │
│  └──────────────────┘  └──────────────────┘               │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │ MSK Serverless   │  │ CloudWatch Alarms │               │
│  │ (IAM auth)       │  │ × 6 (Redis, RDS,  │               │
│  └──────────────────┘  │  checkout, MSK)   │               │
│                         └────────┬─────────┘               │
│                                  │                          │
│  ┌──────────────────┐           │ SNS                      │
│  │ Bastion (t3.micro)│           ▼                          │
│  │ for injection     │  ┌──────────────────┐               │
│  └──────────────────┘  │ devops-agent-demo │               │
│                         │ -alarms (Topic)   │               │
│                         └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Deploy (via GitHub Actions)

Push to `main` or trigger manually:
```bash
gh workflow run deploy.yml -f action=deploy
```

### 2. Register Skills

```bash
cd scripts/
python register_skills.py --profile cloudops-demo --create-space --create-task
```

### 3. Inject Errors

```bash
# Quick mode (instant alarm, good for booth demo):
python inject_cascade.py --mode quick --profile cloudops-demo

# Full cascade (8 min realistic timeline):
python inject_cascade.py --mode full --profile cloudops-demo

# Reset between demos:
python inject_cascade.py --mode reset --profile cloudops-demo
```

### 4. Observe

- DevOps Agent investigates using registered skills
- Agent queries CloudWatch metrics from the AWS source account
- Skills provide business context for root-cause reasoning

## CloudWatch Alarms

| Alarm | Trigger | Skill |
|-------|---------|-------|
| `quickmart-demo-redis-memory-high` | Memory > 80% | redis-keyspace-business-map |
| `quickmart-demo-redis-evictions-high` | Evictions > 100/min | redis-keyspace-business-map |
| `quickmart-demo-rds-connections-high` | Connections > 50 | rds-business-critical-slowdown |
| `quickmart-demo-rds-latency-high` | Read latency > 20ms | rds-business-critical-slowdown |
| `quickmart-demo-checkout-p99-high` | Custom metric > 200ms | rds-business-critical-slowdown |
| `quickmart-demo-msk-consumer-lag-high` | Custom metric > 30s | msk-business-topic-lag |

## Custom Metrics (pushed by injection script)

| Namespace | Metric | Dimensions |
|-----------|--------|------------|
| `QuickMart/Application` | `CheckoutP99Latency` | Service=checkout-svc, Environment=demo |
| `QuickMart/Messaging` | `ConsumerLagSeconds` | Topic=order.placed, ConsumerGroup=reconciler-cg |
| `QuickMart/Messaging` | `ConsumerLagSeconds` | Topic=payment.proc, ConsumerGroup=notifier-cg |
| `QuickMart/Redis` | `RedisLockKeyCount` | Keyspace=lock:inv:*, Cluster=quickmart-demo-redis |

## Cost (~$0.25/hr when running)

| Resource | Approx Cost |
|----------|-------------|
| ElastiCache Serverless (idle) | $0.10/hr |
| Aurora Serverless v2 (0.5 ACU) | $0.06/hr |
| MSK Serverless (idle) | $0.07/hr |
| Bastion (t3.micro) | $0.01/hr |
| **Total** | **~$0.25/hr** |

**Destroy after demo:** `gh workflow run deploy.yml -f action=destroy`

## File Structure

```
cdk/
├── bin/app.ts                    # CDK app entry point
├── lib/demo-infra-stack.ts       # All infrastructure
├── scripts/
│   ├── inject_cascade.py         # Error injection (quick/full/reset)
│   └── register_skills.py        # Skill registration with DevOps Agent
├── .github/workflows/deploy.yml  # GitHub Actions deploy/destroy
├── package.json
├── tsconfig.json
└── cdk.json
```
