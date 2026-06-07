# Act 2: RDS Investigation — Agent Output

> **Skill invoked:** `rds-business-critical-slowdown`  
> **Trigger:** checkout p99 = 820ms (SLA: 200ms) — breached for 3 minutes

---

## Incident Summary

| Field | Value |
|-------|-------|
| **DB Instance** | quickmart-prod-aurora-pg (Aurora PostgreSQL 15.4) |
| **Affected transaction** | `checkout` (tables: orders, order_items, inventory) |
| **Current p99** | 820ms |
| **SLA p99** | 200ms |
| **SLA delta** | +620ms (4.1x SLA) |
| **Business window** | 14:30 HKT — WITHIN business hours → **P1** |

---

## Root Cause Analysis (ranked)

### 1. Connection pool exhaustion from upstream cache miss storm (HIGH confidence)

**Evidence:**
- Active connections: 200/200 (max_connections reached)
- Dominant wait event: `Client:ClientRead` at 87% of total wait time
- `Client:ClientRead` = queries waiting for a free connection from the pool
- Connection errors: 45/min (new connections being rejected)
- Product query rate spiked from 2,000/min → 4,500/min (cache:product:* evictions forcing direct DB lookups)

**Cross-reference with Redis (Act 1):**
- Redis `cache:product:*` evictions at 4,000/min → every evicted cache key = direct DB query
- This created a 2.25x increase in query volume that the connection pool cannot absorb

### 2. Retry amplification (confirmed amplifier)

**Evidence:**
- Config: checkout-svc retries failed DB calls 3x with 100ms backoff
- At 200 connections with 45 errors/min, each error generates 3 retries
- Effective additional load: 135 retry attempts/min competing for connections
- This amplifies the connection exhaustion, not causes it

### 3. ❌ NOT schema/parameter regression (ruled out)

- No recent changes in 14 days (verified against change log)
- No new indexes, no parameter group updates, no instance class change
- Query plans unchanged in Performance Insights history

### 4. ❌ NOT IO bottleneck (dismissed)

- Read IOPS elevated (11,400) but latency normal (2.1ms)
- IO wait events only 3% of total — not the bottleneck

---

## Recommended Mitigations

| Priority | Action | Reversibility | Risk |
|----------|--------|---------------|------|
| 1 | **Fix Redis lock TTL upstream** (Act 1) — this will eliminate cache miss storm | High | Low — addresses root cause |
| 2 | Wait 5 minutes for cache:product:* to rebuild | n/a | n/a |
| 3 | **If not resolved in 5 min:** increase `max_connections` from 200 → 400 | High — Aurora parameter change, no restart | Medium — treats symptom; higher connection count increases per-connection memory usage |
| ⛔ | ~~Failover to reader~~ | **FORBIDDEN** — no failover during business hours (09:00-23:00 HKT) | |
| ⛔ | ~~Parameter group change without DBA approval~~ | **FORBIDDEN** — mitigation #3 requires DBA approval if used | |

---

## What to Watch Next

- Checkout p99 (target: < 200ms within 5 min of Redis fix)
- Active connections (should drop from 200 → ~60 as cache rebuilds)
- Product query rate (should drop from 4,500/min → ~2,000/min baseline)
- Connection errors (should reach 0)

---

## Red Herring Dismissed

> "API Gateway 5xx rate = 0.3%" — These are timeout responses from checkout-svc. Downstream symptom of the latency spike, not an independent failure. Will self-resolve when checkout latency normalizes.
