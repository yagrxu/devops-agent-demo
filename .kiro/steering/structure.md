# Structure: Repo Layout (Current vs Legacy)

The repo has two layers: the **current real-infrastructure demo** (CDK + app +
Slack + DevOps Agent) and **legacy mock-telemetry artifacts** from the original
proposal. Know which is which before editing.

## Current / active

```
demo/
├── cdk/                          # ★ The real demo — CDK stack
│   ├── bin/app.ts                # CDK app entry (DevOpsAgentDemoStack, us-east-1)
│   ├── lib/
│   │   ├── demo-infra-stack.ts   # ★ All infra: VPC, Redis, Aurora, MSK, ECS, ALB/CF, alarms, agent
│   │   └── slack-construct.ts    # Slack integration (Path A webhook + Path B interactive)
│   ├── lambda/                   # Custom-resource + Slack lambdas (see tech.md)
│   ├── scripts/
│   │   ├── inject_cascade.py     # ★ Error injection (quick/full/reset) — fix region default
│   │   ├── register_skills.py    # ⚠ Mostly manual; prints skill configs (see legacy notes)
│   │   └── deploy-slack.sh       # Local Slack deploy helper
│   ├── slack-config.json         # Local Slack creds (gitignored secrets live here)
│   ├── package.json / tsconfig.json / cdk.json
│   └── README.md                 # Accurate operating guide for the CDK stack
├── app/                          # ★ checkout-svc Flask app (containerised, runs on ECS)
│   ├── app.py                    # Real checkout flow + /simulate/* + /config endpoints
│   ├── Dockerfile
│   └── requirements.txt
├── .github/workflows/deploy.yml  # ★ CI deploy/destroy (us-east-1, OIDC role)
├── OPERATOR_RESET_CHECKLIST.md   # Booth operator runbook (still relevant)
├── .kiro/steering/               # ★ These steering files
└── docs/STATUS.md                # Gap analysis vs goal (see that file)
```

## Legacy / reference (from the original mock-data proposal)

These predate the pivot to real infrastructure. They are useful as **"Screen A"
narrative/reference content** and as the source of truth for skill business
context, but they are NOT wired into the deployed stack. Do not assume the agent
reads these at runtime.

```
demo/
├── PROPOSAL.md                   # ⚠ SUPERSEDED — original mock-telemetry design
├── mock-telemetry/               # Static JSON metric snapshots (Redis/RDS/MSK timelines)
├── scenarios/flash-sale-cascade.yaml  # Scenario definition (timeline + expected reasoning)
├── expected-outputs/             # Golden agent responses per act + "without skills" comparison
├── scripts/simulate_flash_sale.py     # Old standalone mock generator (≠ cdk/scripts/*)
├── infra/quickmart-architecture.yaml  # Reference architecture doc
└── init/                         # Old scaffolding (github/tf) — appears unused
```

## Two `scripts/` directories — don't confuse them

- `cdk/scripts/` → **current**, operates the real stack (inject_cascade, register_skills, deploy-slack)
- `scripts/` (repo root) → **legacy** mock generator (simulate_flash_sale.py)

## Where skill business-context lives

The authoritative skill definitions (keyspaces, SLAs, forbidden mitigations,
recent changes) are encoded in `cdk/scripts/register_skills.py` (the `SKILLS`
dict) and described narratively in `scenarios/flash-sale-cascade.yaml`. There is
**no standalone `skills/` directory** despite references to one — see STATUS.md.

## Build artifacts (ignore)

- `cdk/cdk.out/` — synth output, regenerated, not source
- `.DS_Store`, `.claude/` (local agent settings) — incidental
