# Product: DevOps Agent Skills Demo (HK Summit 2026)

## What this is

A **live booth demo** for the AWS Hong Kong Summit 2026 that shows how the
**AWS DevOps Agent** uses **business-context skills** to root-cause a cascading
production incident faster and more accurately than a generic agent.

This is NOT a slide deck or a mock simulation. It runs on **real, deployed AWS
infrastructure** ("QuickMart" e-commerce platform) and a real DevOps Agent space.
An operator triggers a real failure cascade, and the agent investigates live.

## The narrative: "Flash sale cascade"

One triggering event (a flash-sale traffic spike) creates correlated symptoms
across three services, demonstrating cross-boundary reasoning:

```
Flash sale (10x traffic)
  └─ Redis: lock:inv:* keys accumulate (LOCK_TTL too long: 30s instead of 5s)
       └─ memory pressure → cache:product:* evictions
            └─ cache-miss storm → direct DB queries
                 └─ Aurora connection pool exhausted
                      └─ checkout p99 breaches 200ms SLA
                           └─ order.placed producer slows → MSK consumer lag > 30s
```

The **root cause is a single misconfiguration**: `LOCK_TTL_SECONDS=30` (the bug),
which should be `5`. The agent, armed with skills, identifies this upstream cause
rather than chasing the downstream symptoms.

## The three skills (the differentiator)

| Skill | Service | Business context it adds |
|-------|---------|--------------------------|
| `redis-keyspace-business-map` | ElastiCache Redis | per-keyspace purpose, eviction acceptability, cold-start cost, forbidden mitigations, TTL audit |
| `rds-business-critical-slowdown` | Aurora PostgreSQL | critical transactions, SLAs, business windows, amplifiers, forbidden failover |
| `msk-business-topic-lag` | MSK | topic→business impact, rebalance blackouts, known burst patterns |

The "money shot": **without skills** the agent says "Redis evictions high, CPU
fine" (generic); **with skills** it says "lock:inv:* is accumulating (TTL audit
failed), pushing evictions onto cache:product:* whose cold_start_cost is 10x DB
load — this is the upstream cause of your checkout SLA breach."

## Demo flow (operator script)

1. **Act 1 — Redis alert** arrives → agent uses redis skill, finds lock TTL bug
2. **Act 2 — Checkout SLA breach** → agent uses RDS skill, ties it to cache-miss storm
3. **Act 3 — Payment lag** → agent uses MSK skill, ranks RDS as root cause, predicts self-heal
4. **Act 4 — Resolution** → reduce lock TTL 30s→5s, cascade resolves in ~5 min

The agent's reasoning output IS the narrative — the audience reads it.

## Success criteria

- Operator can trigger the cascade and reset between demos in ~30s (see `OPERATOR_RESET_CHECKLIST.md`).
- DevOps Agent receives the alarm (via webhook), investigates, and surfaces the
  lock-TTL root cause using the registered skills.
- Operator can also drive the agent interactively from Slack.
- Two run modes: `quick` (instant alarms for short booth slots) and `full`
  (8-minute realistic cascade).
- Cost stays low (~$0.25/hr when idle) and the stack is destroyable after the event.

## Audience

AWS Summit attendees: SREs, DevOps engineers, platform teams. The demo must
survive scrutiny from experienced SREs (hence real infra + red-herring metrics).

## Current deployment (verified 2026-06-11)

Live in account **`719821274597`** (AWS profile `cloudops-demo`), region
**`us-east-1`**. Stack `DevOpsAgentDemoStack` is `UPDATE_COMPLETE`; DevOps Agent
space `quickmart-demo` (`e1e1499e-6e78-4d4d-a1a0-3b8c5b8be10b`) exists; all 6
alarms are in `OK` (clean baseline, ready to run). See `docs/STATUS.md`.

## Constraints (in-world rules the agent must respect)

- No Redis restart / FLUSHALL during business hours (09:00–23:00 HKT)
- No Aurora failover during business hours
- No MSK rebalance during the blackout window
- Mitigations must be reversible and low-risk (reduce lock TTL, not restart)
