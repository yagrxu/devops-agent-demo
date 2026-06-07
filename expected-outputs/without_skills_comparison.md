# Comparison: Agent Response WITHOUT Skills vs WITH Skills

This document demonstrates the value proposition of customer-authored skills.

---

## Scenario: Same alert, same metrics, same agent.

**Alert:** "checkout-svc p99 latency > 500ms for 3 consecutive minutes"

---

## WITHOUT Skills (Generic DevOps Agent)

> ### Investigation Summary
>
> I've analyzed the alert for checkout-svc high latency. Here's what I found:
>
> **Redis:**
> - Memory usage is at 99.6% (25.9 GB / 26 GB)
> - Eviction rate is elevated at 4,000 keys/min
> - Consider scaling the cluster or adjusting maxmemory-policy
>
> **RDS (Aurora PostgreSQL):**
> - Connection count is at maximum (200/200)
> - CPU utilization is elevated at 72%
> - Consider increasing instance size or max_connections parameter
>
> **MSK:**
> - Consumer lag is elevated on multiple topics
> - order.placed: 85 seconds behind
> - payment.proc: 70 seconds behind
> - Consider scaling consumer groups
>
> **Recommendations:**
> 1. Scale Redis cluster (add nodes or upgrade node type)
> 2. Increase RDS max_connections or upgrade instance class
> 3. Scale MSK consumer groups
> 4. Consider enabling Auto Scaling for ECS services

### Problems with this response:

| Issue | Impact |
|-------|--------|
| Treats each service independently | Misses the cascading causality |
| Recommends scaling (expensive, slow) | Doesn't identify the cheap, fast fix (TTL revert) |
| No root cause identification | "Memory is high" is a symptom, not a cause |
| No business context | Doesn't know checkout is P1 during business hours |
| No change correlation | Doesn't identify the 2026-05-28 TTL change |
| No forbidden mitigation awareness | Might recommend restart/failover during business hours |
| No amplifier awareness | Doesn't understand retry storms making it worse |
| No cascade reasoning | Doesn't connect Redis → RDS → MSK |

---

## WITH Skills (Business-Aware DevOps Agent)

> ### Investigation Summary
>
> **🔴 P1 Incident — Checkout SLA Breached During Business Hours**
>
> #### Root Cause: Redis Lock TTL Regression
>
> `lock:inv:*` keyspace accumulated 70x (200 → 14,000 keys) due to TTL change on 2026-05-28 (QUICK-4421: increased from 5s to 30s). Under flash-sale concurrency (10x traffic), lock accumulation rate exceeds expiry rate, consuming 7.6 GB of Redis memory and forcing eviction of `cache:product:*`.
>
> #### Cascade Chain:
> ```
> Lock TTL 30s (should be 5s) → lock accumulation → memory pressure
>   → product cache evictions (cold_start_cost: 10x DB load)
>     → cache miss storm exhausts Aurora connection pool (200/200)
>       → checkout p99: 820ms (SLA: 200ms) — 4.1x SLA
>         → order producer rate collapses 90%
>           → reconciler consumer starved (shared DB pool)
>             → payment.proc lag: 70s (SLA: 60s)
> ```
>
> #### Fix (single action resolves entire cascade):
> **Revert lock:inv:* TTL from 30s → 5s**
> - Reversibility: High (can increase again)
> - Risk: Low (5s was the original value, proven under flash sales)
> - Expected recovery: ~7 minutes for full cascade resolution
>
> #### Forbidden mitigations (blocked):
> - ⛔ Redis restart (within 09:00-23:00 HKT)
> - ⛔ Aurora failover (within business hours)
> - ⛔ MSK rebalance (within blackout window)
>
> #### Amplifiers active:
> - checkout-svc retries 3x with 100ms backoff (worsening connection exhaustion)
> - No circuit breaker on cache miss → DB path

---

## Side-by-Side Comparison

| Dimension | Without Skills | With Skills |
|-----------|---------------|-------------|
| **Time to root cause** | Never reaches it | Immediate (change correlation) |
| **Fix identified** | Generic scaling | Specific: revert TTL to 5s |
| **Fix cost** | $$$$ (larger instances) | $0 (config change) |
| **Fix time** | 30-60 min (scaling) | 30 seconds (CONFIG SET) |
| **Cascade understood** | No | Yes — full chain mapped |
| **Business context** | No | P1, within business hours |
| **Safety** | May suggest dangerous actions | Blocks forbidden mitigations |
| **Amplifiers** | Not considered | Explicitly identified |
| **Red herrings** | Reports everything | Dismisses unrelated metrics |
| **Actionability** | "Consider scaling" | "Run this command, watch these metrics" |

---

## Key Takeaway for Audience

> **Skills encode what only YOU know about YOUR system.**
>
> AWS can tell you "memory is high" or "connections are maxed."
> Only YOUR skill knows that:
> - `lock:inv:*` should have 5s TTL, not 30s
> - Product cache eviction costs 10x DB load
> - Checkout is P1 during 09:00-23:00 HKT
> - You cannot restart Redis during business hours
> - checkout-svc retries amplify connection exhaustion
>
> This is the difference between "something is wrong" and "here's exactly what broke, why, and the single cheapest fix."
