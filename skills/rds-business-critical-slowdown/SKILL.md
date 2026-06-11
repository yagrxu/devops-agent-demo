---
name: rds-business-critical-slowdown
description: >-
  Diagnose Aurora PostgreSQL slowdowns on the QuickMart checkout database when
  checkout p99 latency breaches the 200ms SLA, DatabaseConnections spike, or
  alarms quickmart-demo-rds-* or quickmart-demo-checkout-p99-high fire. Scopes
  investigation to the checkout transaction (tables: orders, order_items,
  inventory), checks the business window, identifies upstream amplifiers (Redis
  cache-miss storm), and respects failover restrictions. Activate on: "checkout
  slow", "p99 SLA breach", "connection pool exhausted", "database connections
  high", or any correlation between Redis evictions and DB load.
---

# RDS Business-Critical Slowdown — QuickMart

## Target database

- **Cluster:** Aurora Serverless v2 PostgreSQL 16.8, database `quickmart`
- **Cluster identifier:** `devopsagentdemostack-demoaurora5ca44a7f-6ao1ewsu3o1c`
- **Instance:** `devopsagentdemostack-demoaurorawriter9d416fa0-rwfgp5jujkbj` (db.serverless)
- **Region:** us-east-1
- **Performance Insights:** Enabled (Database Insights Advanced, 465-day retention)
- **Credentials:** Secrets Manager `devops-agent-demo/aurora-credentials`
- **CloudWatch:** `AWS/RDS` dimension `DBClusterIdentifier`
- **Custom metric:** `QuickMart/Application` → `CheckoutP99Latency` (dims: Service=checkout-svc, Environment=demo)

## Critical transactions

| Transaction | Tables | p99 SLA | Business window | Priority if breached |
|-------------|--------|---------|-----------------|---------------------|
| **checkout** | orders, order_items, inventory | **200ms** | 09:00–23:00 HKT daily | **P1** |
| product_browse | products, product_images | 50ms | always | P2 |

## Recent changes

**None in the last 14 days.** No schema changes, no parameter-group changes, no instance-class changes. This rules out a database-side regression — look upstream.

## Known amplifiers (make it worse but are NOT the root cause)

1. **`checkout-svc` retries failed DB calls 3x with 100ms backoff** — under connection saturation, retries multiply the load by 3x
2. **Product cache miss triggers synchronous DB query (no circuit breaker)** — when Redis `cache:product:*` is evicted, every miss becomes a direct `SELECT` on Aurora. At 10x normal miss rate, this exhausts the connection pool.

## Diagnostic procedure

1. **Scope to checkout transaction.** Confirm the degradation is on `orders`/`order_items`/`inventory` queries. Check `QuickMart/Application` → `CheckoutP99Latency` — if above 200ms, this is the checkout transaction breaching SLA.

2. **Check business window.** If current time is 09:00–23:00 HKT → **P1 severity**. (The demo is always run during this window.)

3. **Read the dominant wait event** in Performance Insights (Database Insights Advanced is enabled):
   - If **`Client:ClientRead`** dominates (~87% of wait time) → this is **connection-pool exhaustion**, not slow SQL. Transactions are waiting for a connection, not executing slowly.
   - If `Lock:transactionid` dominates → lock contention on checkout tables.
   - If `IO:DataFileRead` dominates → cold buffer pool / IO saturation.

4. **Check connection count.** `DatabaseConnections` at/near max means new checkout transactions queue. The latency is **wait time**, not execution time.

5. **Rule out DB regression.** No recent changes in 14 days → this is NOT a deploy/schema/parameter regression. Look upstream.

6. **Identify the upstream cause.** Check if `quickmart-demo-redis-evictions-high` or `quickmart-demo-redis-memory-high` is also firing. If yes:
   - Redis `cache:product:*` evictions → cache-miss storm → 10x product queries hitting DB directly
   - Combined with 3x retry amplifier = 30x normal DB load
   - **Root cause is upstream in Redis** (lock:inv:* TTL misconfiguration). The DB is the victim, not the source.

7. **Confirm the cascade pattern:**
   ```
   Redis lock:inv:* TTL too long (30s, should be 5s)
     → memory pressure → cache:product:* evictions
       → cache-miss storm (10x DB queries, no circuit breaker)
         → Aurora connection pool exhausted
           → checkout p99 breaches 200ms SLA
   ```

## Forbidden mitigations

- ❌ **No failover during 09:00–23:00 HKT** (business hours)
- ❌ **No parameter-group changes without DBA approval**
- ❌ Do not kill active transactions without explicit operator confirmation

## Recommended mitigations (priority order)

1. ✅ **Fix the upstream Redis lock TTL** (see `redis-keyspace-business-map` skill) — reduces cache-miss volume at the source. No DB change needed. This is the real fix.

2. ⚠️ **Bridge only (if Redis fix doesn't resolve within 5 min):** Temporarily increase `max_connections` parameter (e.g., 200→400). Aurora Serverless applies this dynamically without restart. **Treats symptom only** — use as a stopgap while the upstream fix takes effect.

3. ❌ Do NOT fail over (forbidden during business hours).

## Gotchas

- High p99 with `Client:ClientRead`-dominant wait profile = connection starvation, NOT slow queries. Don't waste time hunting for bad SQL.
- The retry amplifier (3x) means the DB looks 3x busier than real demand. Once the upstream cause resolves, load collapses rapidly.
- `DatabaseConnections` is published at the `DBClusterIdentifier` dimension for this cluster.
- Aurora Serverless v2 scales ACUs automatically, but `max_connections` is still bounded by the parameter group — ACU scaling doesn't bypass connection limits.
