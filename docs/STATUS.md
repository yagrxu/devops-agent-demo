# STATUS — Demo Readiness & Gap Analysis

_Last updated: 2026-06-11. Goal: live DevOps Agent skills demo at HK Summit 2026._

## TL;DR

The project **pivoted** from the original mock-telemetry proposal (`PROPOSAL.md`)
to **real, deployed AWS infrastructure**. The stack is **currently live and
healthy**. The main remaining risks are around **skill registration** (not
automated), **region drift in helper scripts**, and a **full end-to-end rehearsal**.

## Deployment (verified 2026-06-11)

- **Account:** `719821274597` (AWS profile `cloudops-demo`)
- **Region:** `us-east-1`
- **Stack `DevOpsAgentDemoStack`:** `UPDATE_COMPLETE` ✅
- **ECS cluster `quickmart-demo`:** present
- **DevOps Agent space `quickmart-demo`:** `e1e1499e-6e78-4d4d-a1a0-3b8c5b8be10b` (created 2026-06-10)
- **All 6 CloudWatch alarms:** `OK` (clean baseline — cascade not currently triggered)

> Note: the local `cdk/cdk.context.json` synth cache references account
> `613477150601` (the default profile), but the stack is **not** deployed there.
> Use `--profile cloudops-demo` for all live operations.

## Done ✅

- **CDK stack** (`DevOpsAgentDemoStack`) with VPC, ElastiCache Redis Serverless,
  Aurora Serverless v2, MSK Serverless, ECS Fargate `checkout-svc`, ALB→CloudFront,
  6 CloudWatch alarms, SNS topic.
- **Real application** (`app/app.py`) implementing the actual checkout flow plus
  `/simulate/*` and `/config/lock-ttl` endpoints. The bug (`LOCK_TTL_SECONDS=30`)
  is wired in.
- **DevOps Agent provisioning via CDK**: agent space, AWS monitor association,
  operator app, event-channel webhook (idempotent custom-resource lambdas).
- **Slack integration**: Path A (alarm → webhook → autonomous investigation) and
  Path B (interactive Slack ↔ agent chat).
- **CI/CD**: GitHub Actions deploy/destroy in `us-east-1` via OIDC role.
- **Error injection**: `inject_cascade.py` with `quick` / `full` / `reset` modes.
- **Operator runbook**: `OPERATOR_RESET_CHECKLIST.md`.

## Remaining / gaps ⚠️

1. **Skill registration is not automated or version-controlled as artifacts.**
   - The 3 skills exist only as a Python `SKILLS` dict in
     `cdk/scripts/register_skills.py`, which **only prints configs** — it does not
     reliably register them (the AWS-source association path is stubbed with a
     placeholder `serviceId='aws-source'`).
   - There is **no `skills/` directory** even though `register_skills.py` and the
     proposal reference one.
   - **Action needed**: confirm how skills are actually registered with the DevOps
     Agent for this demo (console? API?), then either codify that or document the
     manual steps in the operator runbook.

2. **Region drift in helper scripts.** ✅ Fixed 2026-06-11: `inject_cascade.py`
   and `register_skills.py` now default to `us-east-1`, and the dead
   `AURORA_CLUSTER` constant was removed. Still pass `--profile cloudops-demo`.

   Also fixed 2026-06-11: the two Redis alarms were wired to dimension
   `CacheClusterId` (node-based) but ElastiCache **Serverless** emits with
   `clusterId` — so they never received data and could never fire. Corrected to
   `clusterId`. (Note: the 4 GB `BytesUsedForCache` threshold is still
   unrealistic for the lock-key load and may need right-sizing for a visible demo.)

3. **Full end-to-end rehearsal not yet confirmed.** The stack is deployed and
   healthy in `719821274597` (`cloudops-demo`), with all alarms at `OK`. What has
   **not** been verified in this session is a complete live run:
   alarm→webhook→agent investigation→Slack, in both `quick` and `full` modes.
   - **Action needed**: a timed rehearsal in the demo account, capturing the
     agent's skilled output for the booth.

4. **Doc drift.** `PROPOSAL.md` describes the superseded mock approach and is
   easily mistaken for current design (now flagged at its top). Legacy
   `mock-telemetry/`, `scenarios/`, `expected-outputs/`, root `scripts/`, `infra/`,
   and `init/` remain — keep as Screen A / reference content or archive them.

5. **MSK bootstrap not wired into the app.** In the CDK stack the ECS task env
   `MSK_BOOTSTRAP` is set to `''` with a comment that it needs the bootstrap
   endpoint after cluster creation. The Kafka producer is therefore a no-op.
   Decide whether real MSK production is required for the demo or whether the
   custom `ConsumerLagSeconds` metric (pushed by `inject_cascade.py`) is sufficient.

## Open questions ❓

- ~~Which AWS account/profile is the demo deployed in?~~ **Resolved:** account
  `719821274597`, profile `cloudops-demo`, region `us-east-1`. (Confirm console
  access for the live agent investigation view during the booth.)
- Are skills registered per-space and do they persist across stack updates, or do
  they need re-registering after each deploy?
- Is the "without skills" comparison (the money shot) scripted/captured anywhere
  for the booth, or shown live?

## Suggested next steps (priority order)

1. Run a full rehearsal in the demo account; capture the agent's skilled output.
2. Nail down and document/automate skill registration.
3. Fix region defaults (and the Aurora cluster name) in `cdk/scripts/*.py`.
4. Decide on MSK: wire real bootstrap, or rely on injected lag metric and note it.
5. Archive or clearly label legacy mock artifacts.
