# Act 1: Redis Investigation — Agent Output

> **Skill invoked:** `redis-keyspace-business-map`  
> **Trigger:** CloudWatch alarm — ElastiCache eviction rate > 1000/min

---

## Incident Summary

| Field | Value |
|-------|-------|
| **Cluster** | quickmart-prod-redis |
| **Symptom keyspace** | `lock:inv:*` (purpose: distributed lock) |
| **Eviction victim** | `cache:product:*` (purpose: product catalog cache) |
| **Business window** | 14:30 HKT — WITHIN peak hours (09:00-23:00) |
| **Severity** | **P1** — `lock:inv:*` has `eviction_acceptable: false` and is growing unbounded |

---

## Root Cause Analysis (ranked)

### 1. Lock TTL regression (HIGH confidence)

**Evidence:**
- `lock:inv:*` grew from 200 keys (baseline) to 14,000 keys (70x)
- TTL = 30 seconds (changed from 5s on 2026-05-28, ticket QUICK-4421)
- Under flash-sale load: ~160 lock SET/sec × 30s TTL = 4,800 keys steady-state (vs. 800 at 5s TTL)
- Accumulation outpaces expiry because lock-holders are contending (actual TTL expiry delayed)

**Why this is the root cause:** The 2026-05-28 change increased TTL 6x. Under normal load (500 RPS), this was fine. Under flash sale (5000 RPS), lock accumulation rate exceeds what volatile-lfu can manage without evicting other keyspaces.

### 2. Memory pressure → eviction cascade (consequence of #1)

**Evidence:**
- Used memory: 25.9 GB / 26 GB maxmemory (99.6%)
- Eviction policy: `volatile-lfu` — evicts keys with TTL, least-frequently-used first
- `cache:product:*` is eviction-eligible (TTL set, LFU rank lower than session/cart)
- Eviction rate: 4,000/min → mostly hitting product cache
- Cold start cost of product cache eviction: "10x DB query load"

### 3. ❌ NOT session keyspace (dismissed)

- `session:*` has no evictions (0 in last window)
- Session keys are frequently accessed (high LFU rank) → not selected by volatile-lfu

---

## Recommended Mitigations

| Priority | Action | Reversibility | Risk |
|----------|--------|---------------|------|
| 1 | **Reduce `lock:inv:*` TTL from 30s to 5s** (revert QUICK-4421 change) | High — can increase again | Low — 5s was the original value that worked under previous flash sales |
| 2 | Monitor for 3 minutes — lock keys should drain to < 500 | n/a | n/a |
| ⛔ | ~~Restart Redis~~ | **FORBIDDEN** — within business hours (09:00-23:00 HKT) | |
| ⛔ | ~~FLUSHALL~~ | **FORBIDDEN** — never allowed per policy | |

---

## What to Watch Next

- `lock:inv:*` key count (should drop from 14,000 → < 500 within 3 min of fix)
- Eviction rate (should drop from 4,000/min → < 100/min)
- `cache:product:*` key count (should rebuild as eviction pressure drops)
- Downstream: RDS connection count (cache rebuilding = fewer direct DB queries)

---

## Red Herring Dismissed

> "notifier-svc CPU spike to 78%" — Processing email backlog. No Redis dependency, no `orders` table access. Unrelated symptom.
