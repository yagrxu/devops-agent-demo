---
name: msk-business-topic-lag
description: >-
  Diagnose MSK consumer lag on the QuickMart event streaming platform when
  order.placed or payment.proc topics breach their SLA thresholds. Activate when:
  alarm quickmart-demo-msk-consumer-lag-high fires, ConsumerLagSeconds exceeds 30s
  on order.placed or 60s on payment.proc, or downstream payment/notification
  processing is reported delayed. Differentiates between producer starvation
  (upstream checkout slowdown), consumer regression, and rebalance events.
  Critical: during 09:00-23:00 HKT, do NOT trigger consumer rebalances.
---

# MSK Business-Topic Lag — QuickMart

## Target cluster

- **Cluster:** `quickmart-demo-msk` (MSK Serverless, IAM auth)
- **Region:** us-east-1
- **CloudWatch custom metric:** `QuickMart/Messaging` → `ConsumerLagSeconds`
  - Dimensions: `Topic` + `ConsumerGroup`
- **Alarm:** `quickmart-demo-msk-consumer-lag-high` (threshold: 30s on order.placed/reconciler-cg)

## Business-critical topics

| Topic | Consumer group | Lag SLA | Downstream business impact |
|-------|---------------|---------|---------------------------|
| **order.placed** | `reconciler-cg` | **30s** | Payment processing delayed → customer charged late |
| **payment.proc** | `notifier-cg` | 60s | Order confirmation email delayed |
| inv.reserved | `analytics-cg` | 300s | Analytics dashboard stale (low priority, ignore) |

## Producer services

| Service | Topics produced | Baseline rate | Notes |
|---------|----------------|---------------|-------|
| `checkout-svc` | order.placed, inv.reserved | 80 msg/sec | **Same service that does checkout** — if checkout is slow, produce rate drops |
| `payment-svc` | payment.proc | 75 msg/sec | Independent service |

## Known burst patterns

- **Flash sale:** 5–10x produce rate for 2–4 hours. Lag may spike to 10s but should recover within 2 minutes. If lag does NOT recover → abnormal.

## Rebalance blackout windows

| Window | Reason |
|--------|--------|
| **09:00–23:00 HKT** | Rebalance during peak = ~30s consumer pause = instant SLA breach |

## Diagnostic procedure

1. **Identify which topic is breaching.** Check `ConsumerLagSeconds` per topic:
   - `order.placed` > 30s → P1 (payment processing impact)
   - `payment.proc` > 60s → P1 (customer notification impact)
   - `inv.reserved` > 300s → P3 (analytics only, ignore unless asked)

2. **Producer-side check (most likely cause in cascade).** Compare current produce rate to baseline (80 msg/sec for order.placed).
   - If produce rate **dropped** (e.g., from 80 to 8/sec): this is **producer starvation**, not consumer failure. The producer (`checkout-svc`) is slow because it's blocked upstream (DB connections exhausted, which is caused by Redis cache-miss storm).
   - If produce rate is **elevated** (burst): check against known burst patterns. Flash sale should recover within 2 min.

3. **Consumer-side check.** Has consumer throughput dropped independently?
   - `reconciler-cg` consumer **shares the Aurora connection pool** with `checkout-svc`. If DB connections are exhausted, the consumer is also starved.
   - Check for recent consumer deploys (none in recent changes).

4. **Rebalance check.** Any rebalance events during the lag window? If yes, and it's within the 09:00–23:00 HKT blackout → that rebalance itself is an incident.

5. **Partition skew check.** If lag is concentrated on a few partitions → key skew issue. If uniform across partitions → systemic cause (producer or consumer starvation).

6. **Establish the cascade root cause:**
   ```
   Redis lock:inv:* TTL too long (30s)
     → cache:product:* evictions → cache-miss storm
       → Aurora connection pool exhausted
         → checkout-svc blocked → produce rate drops 90%
         → reconciler consumer also blocked (shared connection pool)
           → order.placed lag grows unbounded
             → payment.proc lag follows
   ```

7. **Determine if this is self-healing.** If the upstream Redis/RDS issue is being fixed, MSK lag will burn down naturally once:
   - Producer rate recovers (checkout-svc unblocked)
   - Consumer rate recovers (connection pool frees up)
   - Expected burn-down time: ~5–10 min after upstream fix

## Forbidden mitigations

- ❌ **Do NOT trigger a consumer rebalance during 09:00–23:00 HKT** (scaling consumers, reassigning partitions, or restarting consumer groups all cause ~30s pause)
- ❌ Do not modify producer configs from the diagnostic side

## Recommended mitigations (priority order)

1. ✅ **Resolve the upstream RDS/Redis issue** (see `rds-business-critical-slowdown` and `redis-keyspace-business-map` skills). Once checkout-svc recovers, producer rate and consumer throughput both restore naturally. Lag burns down without MSK-side intervention.

2. ⚠️ **If lag doesn't burn down within 10 min after upstream fix:** Scale `reconciler-cg` consumers from 3 to 6. This triggers a rebalance (~30s pause) — only do this **outside the blackout window** or with explicit operator waiver.

3. ⚠️ **If partition skew detected:** Rekey affected messages. Requires producer-side change — escalate to the checkout-svc team.

## Gotchas

- A **drop** in produce rate during a flash sale is abnormal (you'd expect an increase). This pattern strongly indicates the producer is blocked, not that demand decreased.
- `reconciler-cg` sharing Aurora connections with `checkout-svc` means both producer and consumer are starved simultaneously — lag grows from both sides.
- `inv.reserved` topic lag (analytics) is almost always irrelevant during an incident. Don't get distracted by it unless it's specifically asked about.
- The MSK lag alarm fires on `order.placed`/`reconciler-cg`, but the real fix is Redis (2 services upstream). The skill's job is to **rank upstream starvation as the root cause** and direct attention there.
