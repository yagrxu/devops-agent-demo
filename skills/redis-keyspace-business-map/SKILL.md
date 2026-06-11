---
name: redis-keyspace-business-map
description: >-
  Diagnose ElastiCache Redis issues on cluster quickmart-demo-redis by mapping
  each key prefix to its business purpose, eviction tolerance, and expected TTL.
  Activate when: Redis evictions are high, memory pressure on quickmart-demo-redis,
  lock:inv:* keys growing, cache:product:* miss rate spiking, or any alarm named
  quickmart-demo-redis-*. This skill answers whether evictions are hitting
  critical keyspaces (sessions, locks) vs acceptable ones (product cache), and
  catches TTL misconfigurations causing unbounded key accumulation.
---

# Redis Keyspace Business-Map — QuickMart

## Target cluster

- **Cluster:** `quickmart-demo-redis` (ElastiCache Redis Serverless, engine 7)
- **Region:** us-east-1
- **CloudWatch namespace:** `AWS/ElastiCache`, dimension `clusterId = quickmart-demo-redis`
- **Custom metric:** `QuickMart/Redis` → `RedisLockKeyCount` (dimension `Keyspace=lock:inv:*`)
- **Maxmemory policy:** volatile-lfu

## Keyspace business map

| Prefix | Purpose | Eviction acceptable? | Expected TTL | Baseline key count | Cold-start cost if lost |
|--------|---------|---------------------|--------------|-------------------|--------------------------|
| `session:*` | User sessions | **NO** | 3600s | ~45,000 | User logout, forced re-login |
| `cache:product:*` | Product catalog cache | Yes | 300s | ~120,000 | **10x DB query load** (synchronous fallback to Aurora, no circuit breaker) |
| `lock:inv:*` | Inventory locks | **NO** | **5s** | ~200 | Duplicate inventory reservations, oversell |
| `cart:*` | Shopping cart | Yes | 1800s | ~30,000 | User must re-add items to cart |

## Recent changes (CHECK FIRST — this is almost always the cause)

| Date | Change | Ticket | Impact |
|------|--------|--------|--------|
| **2026-05-28** | `lock:inv:*` TTL changed from **5s → 30s** | QUICK-4421 ("reduce lock acquisition retries") | Under flash-sale concurrency, create rate (160/sec) exceeds expire rate (47/sec) → locks accumulate unbounded |

## Diagnostic procedure

1. **Check lock:inv:* key count.** If significantly above baseline (~200), this is the primary suspect. Under the 30s TTL misconfiguration, flash-sale traffic causes unbounded accumulation.

2. **TTL audit on lock:inv:*.** Sample keys — if TTL is 30s (not the correct 5s), the 2026-05-28 change (QUICK-4421) is the root cause. The accumulation rate under load exceeds the expiry rate:
   - At 160 locks/sec with 30s TTL: steady-state = 4,800 keys
   - At 500+ locks/sec (flash sale): accumulation grows without bound

3. **Check what is being evicted.** Memory pressure from lock accumulation forces eviction of `cache:product:*`. This keyspace IS eviction-acceptable, **but** its cold-start cost is catastrophic: every evicted product key triggers a synchronous DB query (no circuit breaker), causing a 10x cache-miss storm on Aurora.

4. **Verify session:* is safe.** Sessions are in a different eviction class (no-eviction priority). Confirm `session:*` count is stable. If sessions are evicting → escalate immediately (customer-visible logout storm).

5. **Establish causality chain:**
   ```
   lock:inv:* accumulation (root cause: TTL too long)
     → memory pressure
       → cache:product:* evictions (acceptable but costly)
         → cache-miss storm → 10x DB queries → Aurora connection exhaustion
   ```

6. **Correlate with downstream:** Check if `quickmart-demo-rds-connections-high` alarm is also firing. If yes, the Redis lock issue is the upstream root cause of the RDS problem.

## Forbidden mitigations

- ❌ **NEVER restart Redis during 09:00–23:00 HKT** (business hours)
- ❌ **NEVER `FLUSHALL` or `FLUSHDB`** — destroys sessions, carts, and in-flight locks
- ❌ Do not change maxmemory-policy without DBA approval

## Recommended fix

✅ **Reduce `lock:inv:*` TTL from 30s back to 5s** (revert the 2026-05-28 change, ticket QUICK-4421).

- **Reversibility:** High — can increase again if needed
- **Risk:** Low — 5s was the proven value under previous flash sales
- **Effect:** Accumulated locks drain within 5s, memory pressure drops, `cache:product:*` evictions stop, downstream cache-miss storm resolves
- **How:** Application config change (`LOCK_TTL_SECONDS=5`) or Redis `CONFIG SET` for immediate effect

## Gotchas

- ElastiCache **Serverless** publishes metrics under dimension `clusterId` (not `CacheClusterId`). Querying the wrong dimension returns no data and looks falsely healthy.
- `cache:product:*` being "eviction acceptable" is a trap — acceptable ≠ harmless. The 10x DB amplification makes it the bridge between a Redis issue and an Aurora outage.
- Lock accumulation is a **rate problem** (create > expire), not a memory-cap problem. Raising memory limits does not fix it; fixing the TTL does.
- The `LOCK_TTL_SECONDS` is set in the ECS task environment (`checkout-svc`). Current value is 30 (the bug). Correct value is 5.
