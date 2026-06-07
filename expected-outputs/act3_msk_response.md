# Act 3: MSK Investigation — Agent Output

> **Skill invoked:** `msk-business-topic-lag`  
> **Trigger:** order.placed lag = 85s (SLA: 30s), payment.proc lag = 70s (SLA: 60s)

---

## Incident Summary

| Field | Value |
|-------|-------|
| **Cluster** | quickmart-prod (arn:aws:kafka:ap-east-1:...) |
| **Affected topics** | `order.placed` (lag 85s, SLA 30s), `payment.proc` (lag 70s, SLA 60s) |
| **Business impact** | Payment processing delayed → customers charged late; order confirmation emails delayed |
| **Blackout window** | 09:00-23:00 HKT — WITHIN blackout → **no rebalance allowed** |

---

## Root Cause Analysis (ranked)

### 1. Upstream producer starvation (HIGH confidence)

**Evidence:**
- `order.placed` produce rate: dropped from 80/s → 8/s (90% collapse)
- Producer is `checkout-svc` — same service experiencing RDS connection exhaustion (Act 2)
- Checkout completions have collapsed → fewer orders being placed → fewer events produced
- This is NOT a consumer problem — lag is growing because inflow decreased AND consumer is also affected

**Known burst pattern comparison:**
- Flash sale should INCREASE produce rate (5-10x) per documented pattern
- Instead it decreased 90% — confirms abnormal behavior, not expected burst lag

### 2. Consumer starvation from shared connection pool (MEDIUM confidence)

**Evidence:**
- `reconciler-cg` consumer (3 instances) shares Aurora connection pool with checkout-svc
- Consumer throughput dropped proportionally with producer rate
- Both are starved by the same connection pool exhaustion
- Even if produce rate recovered instantly, consumer would need connections to process

### 3. ❌ NOT rebalance (verified)

- Zero rebalance events in the time window
- All 3 consumer instances for reconciler-cg reporting healthy
- Partition assignment stable

### 4. ❌ NOT partition skew (verified)

- Lag distribution across 6 partitions: uniform (within 15% variance)
- No hot partition detected

### 5. ❌ NOT broker saturation (verified)

- Broker CPU: 33% (healthy)
- Broker network in: 15 MB/s (well below capacity)
- Broker disk: 42% (healthy)

---

## Recommended Mitigations

| Priority | Action | Reversibility | Risk |
|----------|--------|---------------|------|
| 1 | **Resolve RDS connection issue (upstream fix)** — producer rate will recover when checkout-svc can complete orders again | High | Low — addresses root cause |
| 2 | Monitor lag burn-down for 10 minutes post-upstream-fix | n/a | n/a |
| 3 | **If lag doesn't burn down in 10 min:** Scale reconciler-cg from 3 → 6 consumers | High — can scale back | Low — more consumers will drain backlog faster |
| ⛔ | ~~Trigger consumer group rebalance~~ | **FORBIDDEN** — within rebalance blackout window (09:00-23:00 HKT). Rebalance = 30s consumer pause = worse SLA breach | |

---

## What to Watch Next

- `order.placed` produce rate (target: return to 80/s within 5 min of RDS fix)
- `order.placed` lag (target: burn down from 85s → < 30s within 10 min)
- `payment.proc` lag (target: burn down from 70s → < 60s within 10 min)
- reconciler-cg throughput (should recover as connection pool frees up)

---

## Red Herring Dismissed

> "inv.reserved lag = 180s" — This topic has SLA of 300s (analytics only). Current lag is within bounds. Impact is dashboard staleness, not customer-facing. Monitoring but not actionable.
