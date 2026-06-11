> ⚠️ **SUPERSEDED — historical design doc.** This proposal describes the original
> **mock-telemetry** approach (static JSON snapshots, no live AWS). The project has
> since pivoted to **real, deployed AWS infrastructure** (CDK stack
> `DevOpsAgentDemoStack` in `us-east-1`). For the current design and operating
> guide see `.kiro/steering/`, `cdk/README.md`, and `docs/STATUS.md`.
>
> This file is kept for narrative reference (the incident story, skill business
> context, and demo-flow script remain valid).

# Demo Simulation Proposal: DevOps Agent Skills in Action

## Executive Summary

Simulate a realistic production incident end-to-end using **all three skills** within a single business narrative:  
**"E-commerce flash sale causes cascading failure across Redis → RDS → MSK."**

One triggering event (flash sale traffic spike) creates correlated symptoms across three services, demonstrating how DevOps Agent reasons about each through business-critical context.

---

## Architecture

### The Simulated System: "QuickMart" E-commerce Platform

```
┌──────────────────────────────────────────────────────────────────────┐
│                        QuickMart Architecture                          │
│                                                                        │
│  [Users] ──→ [API Gateway] ──→ [ECS: checkout-svc]                   │
│                                       │                                │
│                    ┌──────────────────┼──────────────────┐            │
│                    │                  │                   │            │
│                    ▼                  ▼                   ▼            │
│          ┌─────────────┐   ┌──────────────┐   ┌──────────────┐      │
│          │  ElastiCache │   │  Aurora PG   │   │    MSK       │      │
│          │  Redis 7.x   │   │  (orders DB) │   │  (events)    │      │
│          │              │   │              │   │              │      │
│          │ session:*    │   │ orders       │   │ order.placed │      │
│          │ cache:prod:* │   │ order_items  │   │ payment.proc │      │
│          │ lock:inv:*   │   │ inventory    │   │ inv.reserved │      │
│          │ cart:*       │   │ products     │   │              │      │
│          └─────────────┘   └──────────────┘   └──────────────┘      │
│                                                       │               │
│                                                       ▼               │
│                                            ┌──────────────────┐      │
│                                            │ ECS: reconciler  │      │
│                                            │ ECS: notifier    │      │
│                                            │ ECS: analytics   │      │
│                                            └──────────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
```

### Why This Architecture Works for Demo

1. **Single blast radius** — one flash-sale event creates symptoms in all three services
2. **Business-critical framing** — "checkout is broken" not "CPU is high"
3. **Cascading causality** — Redis lock contention → RDS connection storm → MSK consumer lag
4. **Forbidden mitigations apply** — demo happens during "business hours" (trading window)
5. **All three skills get exercised** in a natural sequence

---

## The Incident Scenario

### Timeline

```
T+0    Flash sale starts (10x normal traffic)
T+2m   Redis: lock:inv:* keys spike (inventory lock contention)
T+3m   Redis: evictions begin on cache:product:* (memory pressure from lock buildup)
T+4m   RDS: connection count spikes (cache misses → direct DB hits)
T+5m   RDS: checkout transaction p99 exceeds 200ms SLA (lock waits + connection pool exhaustion)
T+6m   MSK: order.placed topic lag grows (checkout-svc slower → producer rate drops, but reconciler consumer also slowed by DB connection competition)
T+8m   Alert fires: "checkout p99 > 500ms for 3 minutes"
T+8m   DevOps Agent begins investigation
```

### Root Cause Chain

```
Flash sale (10x traffic)
  └─→ Inventory lock contention in Redis (lock:inv:* TTL too long at 30s)
       └─→ Lock keys accumulate → memory pressure
            └─→ Eviction of cache:product:* (eviction_acceptable: true, but cold_start_cost high)
                 └─→ Cache miss storm → direct DB queries for product data
                      └─→ Aurora connection pool exhausted (max_connections hit)
                           └─→ Checkout transaction blocked on connection wait
                                └─→ order.placed producer rate drops 80%
                                     └─→ reconciler consumer also starved (shared DB pool)
                                          └─→ payment.proc topic lag > 60s SLA
```

---

## Demo Flow (Operator Script)

### Act 1: Alert Arrives (Skill: Redis)

**Trigger:** Simulated PagerDuty/Slack alert: "ElastiCache eviction rate > 1000/min, session keyspace affected"

**Agent uses:** `redis-keyspace-business-map` skill

**Agent reasoning (visible to audience):**
1. Maps alert to keyspace: evictions hitting `cache:product:*` (eviction_acceptable: true) but `lock:inv:*` is growing unbounded (no TTL enforcement → TTL audit catches it)
2. Identifies: `lock:inv:*` keys have 30s TTL but under contention aren't expiring fast enough — accumulation rate > expiry rate
3. Recommends: reduce lock TTL from 30s → 5s (reversible, low risk)
4. Notes: cannot restart Redis (forbidden during 09:00-23:00 HKT)

### Act 2: Checkout SLA Breach (Skill: RDS)

**Trigger:** Agent correlates: "checkout p99 now 480ms (SLA: 200ms)"

**Agent uses:** `rds-business-critical-slowdown` skill

**Agent reasoning:**
1. Scopes to `checkout` transaction (tables: orders, order_items, inventory)
2. Confirms: within business window (09:00-23:00 HKT) → P1
3. Performance Insights shows: wait event `Client:ClientRead` dominant (connection pool exhaustion)
4. Cross-references recent changes: no schema/param changes in 14 days
5. Identifies amplifier: cache miss storm (from Act 1) creating 10x normal DB query rate
6. Recommends: (a) fix Redis lock TTL first (upstream fix), (b) temporarily increase `max_connections` from 200→400, (c) if (a) resolves cache pressure within 5 min, no further action needed
7. Forbidden: no failover during trading hours

### Act 3: Payment Processing Lag (Skill: MSK)

**Trigger:** "payment.proc consumer lag > 60s (SLA: 30s)"

**Agent uses:** `msk-business-topic-lag` skill

**Agent reasoning:**
1. Scopes to `payment.proc` topic, consumer group `reconciler-cg`
2. Producer-side: `order.placed` produce rate dropped 80% (checkout slow → fewer orders completing)
3. Consumer-side: `reconciler` service sharing Aurora connection pool → also starved
4. No rebalance event (good — within blackout window anyway)
5. Ranks: upstream starvation (RDS) is root cause, not consumer regression
6. Recommends: resolve RDS issue (Act 2) → lag will self-heal as producer rate recovers. Monitor: if lag doesn't burn down within 10 min post-fix, scale reconciler consumer group.

### Act 4: Resolution & Summary

Agent produces unified incident summary:
- **Root cause:** Flash sale + overly-long Redis lock TTL (30s) caused cascading failure
- **Fix applied:** Lock TTL reduced to 5s
- **Recovery time:** ~5 minutes for full cascade to resolve
- **Follow-up recommendation:** Create goal to evaluate lock TTL settings weekly during sale seasons

---

## Technical Implementation

### What We Build

| Component | Purpose | Location |
|-----------|---------|----------|
| `infra/terraform/` | IaC for "QuickMart" (documentation-only, shows what would be deployed) | Reference architecture |
| `scripts/simulate_flash_sale.py` | Generates mock CloudWatch/Performance Insights metrics | Error simulation |
| `scripts/simulate_redis_pressure.py` | Generates mock Redis SLOWLOG, INFO, eviction data | Error simulation |
| `scripts/simulate_msk_lag.py` | Generates mock MSK consumer lag metrics | Error simulation |
| `mock-telemetry/` | Pre-baked metric snapshots for each T+N timestamp | Staged data |
| `scenarios/flash-sale-cascade.yaml` | Full scenario definition (timeline, expected agent reasoning) | Scenario config |
| `expected-outputs/` | Golden-file agent responses for each act | Validation |

### Mock Telemetry Approach

Since no live AWS account is available, we simulate by:

1. **Static metric snapshots** — JSON files representing CloudWatch `GetMetricData` responses at each timestamp
2. **Performance Insights mock** — pre-built `DimensionGroup` responses showing wait events
3. **Redis INFO mock** — simulated output of `INFO memory`, `INFO keyspace`, `SLOWLOG`
4. **MSK metrics mock** — consumer lag by partition, produce/consume rates

The DevOps Agent demo will reference these as "what the agent retrieved" — the operator narrates while showing the staged data on screen.

---

## Self-Challenge & Risk Assessment

### Challenge 1: "Is a cascading scenario too complex for a 8-minute booth demo?"

**Risk:** Audience loses the thread across 3 services.

**Mitigation:** 
- Each Act is self-contained (can demo just Act 1 if short on time)
- Visual timeline on Screen A shows cascade clearly
- Agent's reasoning output IS the narrative — audience reads it, not the operator
- Operator has "skip to resolution" escape hatch

**Verdict:** Keep cascade — it's the differentiator. Single-service demos are boring and don't show agent reasoning across boundaries.

### Challenge 2: "Mock data might feel fake to experienced SREs"

**Risk:** Savvy attendees notice metrics are too clean, too perfectly correlated.

**Mitigation:**
- Add noise to mock telemetry (jitter, unrelated spikes)
- Include "red herring" metrics (CPU spike on unrelated service, a random 5xx from API GW)
- Make agent explicitly dismiss red herrings in reasoning ("CPU spike on notifier-svc is uncorrelated — no orders table access")

**Verdict:** Add 2-3 red herrings. They actually make the demo MORE impressive — shows agent filtering signal from noise.

### Challenge 3: "What if someone asks 'can I try it?'"

**Risk:** No live account means no interactive demo.

**Mitigation:**
- Pre-script 2-3 alternative questions the audience can "ask" the agent (operator types them)
- Example: "What if we just scaled the DB?" → Agent explains why that's treating symptom not cause
- Example: "Can we flush the Redis locks?" → Agent checks forbidden mitigations, says no (within business hours)
- Have a "try it yourself" card with QR code to DevOps Agent docs/sign-up

**Verdict:** Acceptable. Booth demos are always staged; transparency about "staged scenario" is fine.

### Challenge 4: "Does this actually prove the skills add value over generic DevOps Agent?"

**Risk:** Audience thinks "the agent would find this anyway without skills."

**Mitigation:**
- Show BEFORE/WITHOUT skill: agent says "Redis evictions are high, CPU is fine" (generic)
- Show AFTER/WITH skill: agent says "lock:inv:* is accumulating (TTL audit failed), pushing evictions onto cache:product:*, which has cold_start_cost='10x DB load' — this is the upstream cause of your checkout SLA breach"
- The business context (SLA, forbidden mitigations, amplifiers) is ONLY available through skills

**Verdict:** Add a 30-second "without skill" comparison. This is the money shot.

### Challenge 5: "Timeline realism — can a cascade really happen in 8 minutes?"

**Risk:** Real incidents take longer; compressed timeline feels artificial.

**Mitigation:**
- Frame as "we've compressed a real incident for demo purposes"
- Real flash sale cascades DO happen this fast (traffic spike is instant, cascade follows in minutes)
- The agent investigation part is realistically fast — it's querying APIs, not waiting

**Verdict:** Acceptable. State upfront: "This is a compressed real-world scenario."

### Challenge 6: "What's the recovery/reset story between demos?"

**Risk:** After showing resolution, need to reset state for next audience.

**Mitigation:**
- All state is mock telemetry files — "reset" = switch terminal to fresh scenario
- Slack thread: use a new channel per demo run (pre-created: #demo-run-1, #demo-run-2, ...)
- Operator has 30-second reset checklist between demos

**Verdict:** Trivial to reset. Non-issue.

---

## Deliverables Checklist

- [ ] `demo/scenarios/flash-sale-cascade.yaml` — Full timeline + expected agent outputs
- [ ] `demo/mock-telemetry/` — Metric snapshots per timestamp (Redis, RDS, MSK)
- [ ] `demo/scripts/` — Python scripts generating mock data (reproducible)
- [ ] `demo/expected-outputs/` — Golden agent responses for each act
- [ ] `demo/infra/` — Reference Terraform showing QuickMart architecture
- [ ] Visual timeline diagram for Screen A (architecture + cascade arrows)
- [ ] Operator reset checklist
- [ ] "Without skill" comparison output

---

## Recommended Next Steps

1. **Approve this proposal** — adjust narrative if needed
2. **Build scenario YAML** — defines exact timeline, metrics, and expected reasoning
3. **Generate mock telemetry** — Python scripts producing realistic CloudWatch/PI/Redis/MSK data
4. **Write golden agent outputs** — what the agent should say at each act (serves as demo script)
5. **Build "without skill" comparison** — generic agent output for contrast
6. **Create visual assets** — architecture diagram, timeline, cascade flow for Screen A
