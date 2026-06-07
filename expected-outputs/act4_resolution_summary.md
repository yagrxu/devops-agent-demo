# Act 4: Unified Resolution Summary — Agent Output

> **Incident resolved.** All SLAs restored.

---

## Incident Report

### Timeline

| Time | Event |
|------|-------|
| T+0 | Flash sale starts (5000 RPS, 10x normal) |
| T+2m | Redis `lock:inv:*` keys begin accumulating (30s TTL × high concurrency) |
| T+3m | Memory pressure triggers eviction of `cache:product:*` |
| T+4m | Cache misses flood Aurora with product queries |
| T+5m | Aurora connection pool exhausted (200/200) → checkout SLA breached |
| T+6m | Checkout throughput collapses → MSK producer rate drops 90% |
| T+8m | **Alert fires.** DevOps Agent investigation begins. |
| T+8.5m | **Root cause identified:** Redis lock TTL regression (QUICK-4421) |
| T+9m | **Fix applied:** `lock:inv:*` TTL reverted from 30s → 5s |
| T+10m | Lock keys draining (14,000 → 800) |
| T+11m | Evictions cease, product cache rebuilding |
| T+12m | Aurora connections drop to 80, checkout p99 = 120ms ✅ |
| T+14m | MSK lag burning down (order.placed: 15s) ✅ |
| T+16m | **All SLAs met. Incident closed.** |

### Root Cause

**Single root cause:** Redis lock TTL increased from 5s to 30s on 2026-05-28 (ticket QUICK-4421: "reduce lock acquisition retries"). Under normal load, this change was harmless. Under flash-sale concurrency (10x), it caused lock key accumulation that exceeded Redis memory budget, triggering a cascade:

```
Lock TTL too long (30s)
  → Lock keys accumulate under high concurrency
    → Memory pressure evicts product cache
      → Cache miss storm overwhelms DB connection pool
        → Checkout transactions blocked
          → Order producer rate collapses
            → Downstream MSK consumers starved
```

### Fix Applied

```
CONFIG SET lock:inv:* TTL = 5 (reverted to pre-QUICK-4421 value)
```

### Recovery Evidence

| Metric | Peak (incident) | Post-fix (T+16m) | Baseline |
|--------|-----------------|-------------------|----------|
| lock:inv:* keys | 14,000 | 180 | 200 |
| Redis evictions/min | 4,000 | 0 | 0 |
| RDS active connections | 200 (maxed) | 52 | 45 |
| Checkout p99 | 820ms | 95ms | 85ms |
| order.placed lag | 85s | 4s | 2s |
| payment.proc lag | 70s | 3s | 1s |

---

## Follow-up Recommendations

### Immediate (this week)

1. **Permanent fix:** Update application code to use 5s lock TTL (not just CONFIG SET). Add to deployment config.
2. **Post-mortem:** Document that QUICK-4421's TTL increase was not load-tested under flash-sale conditions.

### Short-term (next sprint)

3. **Add circuit breaker** between cache miss path and direct DB query (prevent cache miss storm from exhausting connections).
4. **Separate connection pools** for reconciler consumer vs. checkout-svc (prevent consumer starvation from checkout contention).
5. **Add CloudWatch alarm** for `lock:inv:*` key count > 1000 (early warning).

### Goal Created

> **New DevOps Agent goal:** "Evaluate Redis lock TTL settings weekly during sale seasons"
> - Type: `CUSTOMER_DEFINED`
> - Schedule: `rate(7 days)` during June-August (sale season)
> - Evaluates: lock key count vs. baseline under current traffic patterns

---

## Skills Value Demonstration

### Without skills (generic agent response):

> "Redis evictions are elevated at 4000/min. Memory usage is at 99.6%. Consider scaling the cluster or adjusting maxmemory-policy. RDS connections are high. MSK lag is elevated across multiple topics."

### With skills (business-aware response):

> "`lock:inv:*` (inventory locks) accumulated 70x due to TTL regression on 2026-05-28. This pushed evictions onto `cache:product:*` (cold_start_cost: 10x DB load), which exhausted Aurora's connection pool, breaching checkout SLA (820ms vs 200ms target). Cannot restart Redis (business hours) or failover Aurora (forbidden). Fix: revert lock TTL from 30s → 5s. Cascade will self-resolve in ~7 minutes."

**The difference:** Business context (which keyspace matters, what the cold_start_cost is, which mitigations are forbidden, what changed recently) transforms generic metrics into actionable diagnosis.
