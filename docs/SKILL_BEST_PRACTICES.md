# Best Practices: Creating and Adopting DevOps Agent Skills

A guide for teams building custom skills for AWS DevOps Agent — based on
lessons learned from the QuickMart demo and the official skill specification.

---

## What is a skill?

A skill is a **structured instruction set** (Markdown) that teaches the DevOps
Agent how to investigate a specific operational scenario. It provides the
domain-specific knowledge, decision trees, and business context that the agent
wouldn't know on its own.

**Without skills:** "Redis evictions are high. CPU is fine."
**With skills:** "lock:inv:* is accumulating (TTL audit failed — 30s instead of 5s),
pushing evictions onto cache:product:* whose cold_start_cost is 10x DB load. This
is the upstream cause of the checkout SLA breach."

---

## Skill file structure

```
my-skill/
├── SKILL.md              # Required: main instructions with frontmatter
├── references/           # Optional: detailed sub-procedures
│   ├── diagnostic-flow.md
│   └── mitigation-catalog.md
└── assets/               # Optional: images, topology diagrams
```

- **SKILL.md** is the only mandatory file (< 500 lines recommended)
- **references/** loaded on demand by the agent — for deep sub-procedures
- **assets/** for diagrams, data files
- ❌ **No `scripts/`** — rejected on upload (not yet supported)
- ❌ Max zip size: **6 MB**

---

## Frontmatter (critical for activation)

```yaml
---
name: my-skill-name
description: >-
  When to activate this skill. Include specific symptoms, services,
  alarm names, error patterns. The agent reads this to decide if the
  skill is relevant to the current investigation.
---
```

The `description` field is **how the agent decides to use your skill**. Write it
like a trigger condition, not a summary. Include:
- Specific alarm names (`quickmart-demo-redis-*`)
- Symptom keywords ("evictions high", "consumer lag", "p99 breach")
- Service/resource identifiers
- What it's NOT for (prevents false activation)

---

## Key principles

### 1. Hardcode your business context — don't use placeholders

❌ Bad (template):
```markdown
Investigate issues on `{cluster_id}` with keyspace `{prefix}`.
```

✅ Good (deployed):
```markdown
Investigate issues on `quickmart-demo-redis` with keyspace `lock:inv:*`
(expected TTL 5s, baseline 200 keys).
```

The agent has no way to resolve placeholders or read config files. Everything it
needs must be **in the SKILL.md text itself** when uploaded. Write skills as
deployed instances, not reusable templates.

If you want reusability, keep a template version in your repo and generate
instance-specific versions for each environment/team.

### 2. Tell the agent what it wouldn't know on its own

Skip generic knowledge (what CloudWatch is, how to read metrics). Focus on:
- **Business context:** which keyspaces matter, what the SLA is, what "acceptable
  eviction" means for THIS team
- **Recent changes:** the specific commit/ticket that introduced the bug
- **Forbidden mitigations:** what NOT to do (failover blackouts, compliance rules)
- **Cascade relationships:** "if X fires, check Y because they're connected via Z"
- **Gotchas:** non-obvious facts that defy reasonable assumptions

### 3. Favor step-by-step procedures over declarative statements

❌ Bad: "The skill identifies TTL misconfigurations."

✅ Good:
```markdown
1. Check lock:inv:* key count. If above 200 (baseline), this is the suspect.
2. Sample TTLs. If 30s instead of 5s → the 2026-05-28 change is the root cause.
3. Verify cascade: are cache:product:* evictions rising in correlation?
```

### 4. Include decision trees for branching scenarios

```markdown
- If `Client:ClientRead` wait event dominates → connection pool exhaustion
  → look upstream (Redis cache-miss storm)
- If `Lock:transactionid` dominates → lock contention on checkout tables
  → look at concurrent batch jobs
- If `IO:DataFileRead` dominates → cold buffer pool
  → check if instance was recently restarted
```

### 5. State what's forbidden before recommending what to do

The agent will recommend mitigations. If certain actions are blocked by policy
(failover blackouts, change windows), state them prominently so the agent never
suggests them — even if they're technically optimal.

### 6. Cross-reference related skills

If your incident typically cascades across services, reference the other skills:
```markdown
Once Redis lock TTL is fixed, verify the downstream effect:
- RDS connections should drop (see `rds-business-critical-slowdown` skill)
- MSK lag should burn down (see `msk-business-topic-lag` skill)
```

---

## Adoption workflow

### Step 1: Identify the scenario

Start from a real incident. Ask:
- What did the on-call engineer know that the agent didn't?
- What business context was required to triage correctly?
- What mitigations were considered but rejected (and why)?

### Step 2: Write the SKILL.md

Use this structure:
1. **Target resource** (specific identifiers, not placeholders)
2. **Business context** (SLAs, transaction names, impact descriptions)
3. **Recent changes** (the #1 thing to check first)
4. **Diagnostic procedure** (numbered steps with decision points)
5. **Forbidden mitigations** (what NOT to do)
6. **Recommended fix** (with reversibility rating)
7. **Gotchas** (non-obvious facts)

### Step 3: Upload to Agent Space

1. Zip the skill directory (SKILL.md + optional references/)
2. Navigate to **Agent Space Operator Web App → Skills → Add skill → Upload**
3. Select agent types: Generic (all), or target specific ones:
   - **Incident Triage** — initial classification
   - **Incident RCA** — root cause analysis (most common for ops skills)
   - **Incident Mitigation** — fix recommendations
4. Upload the zip

### Step 4: Test with a real trigger

- Trigger the alarm or condition the skill is designed for
- Verify the agent loads and applies the skill
- Check that the agent's reasoning references your business context
- Compare "without skill" vs "with skill" output

### Step 5: Iterate

Common issues on first deploy:
- **Skill not activated:** Improve the `description` — make trigger conditions
  more specific (add alarm names, service identifiers)
- **Skill loaded but not used:** The agent found no real evidence matching the
  skill's diagnostic steps. Ensure real metrics/data exist (not just forced alarms).
- **Wrong recommendation:** Tighten the forbidden mitigations or adjust the
  decision tree ordering.

---

## Anti-patterns to avoid

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Placeholder values (`{cluster_id}`) | Agent can't resolve them | Hardcode for each environment |
| Generic advice ("check if CPU is high") | Agent already knows this | Add only domain-specific knowledge |
| Wall of text with no structure | Agent can't extract actionable steps | Use numbered procedures + decision trees |
| Too broad scope ("all database issues") | Skill activates on everything, helps nothing | Scope to ONE specific scenario/failure mode |
| Relying on config.yaml at runtime | No config file mechanism exists in DevOps Agent | Bake all values into SKILL.md |
| Over 500 lines in SKILL.md | Token budget issues, diluted focus | Move deep procedures to references/ |

---

## Example: from template to deployed skill

**Template** (in your repo, for multiple teams):
```markdown
Diagnose Redis issues on `{cluster_id}`. Keyspace `{prefix}` has
TTL `{ttl}` and cold-start cost `{cost}`.
```

**Deployed instance** (uploaded to Agent Space):
```markdown
Diagnose Redis issues on `quickmart-demo-redis`. Keyspace `lock:inv:*`
has expected TTL 5s. On 2026-05-28 (ticket QUICK-4421) this was
changed to 30s — under flash-sale concurrency, locks accumulate
unbounded. Cold-start cost: duplicate inventory reservations.
```

The second version is what the agent can actually use.

---

## Maintenance

- **Update skills when infrastructure changes** (new cluster, new SLA, new team)
- **Update `recent_changes` after every relevant deploy** — this is the #1 signal
  the agent uses for root cause correlation
- **Review forbidden mitigations quarterly** — blackout windows change
- **Version your skills in git** — treat them like runbooks, not throwaway docs
