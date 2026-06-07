# GitHub OIDC Bootstrap — Terraform (Dual-Account)

This Terraform project bootstraps the foundational AWS infrastructure required before the main CI/CD pipeline can operate. It is a **one-time, manual deployment** run from a developer's local machine.

It provisions resources in **two separate AWS accounts** — one for `test` and one for `release` (production) — so that the GitHub Actions workflow can deploy to the correct account based on the target branch/environment.

## What It Creates (per account)

- **GitHub Actions OIDC Provider** — Registers GitHub's token endpoint as an IAM identity provider for short-lived OIDC token authentication.
- **IAM Role** — An assumable role (`aiops-demo-github-actions-role`) with a trust policy scoped to the specific branch and GitHub environment.
- **IAM Policies** — Terraform state access (S3 + DynamoDB) and AdministratorAccess (demo only).
- **S3 Bucket** — Stores Terraform remote state with versioning, KMS encryption, and public access blocked.
- **DynamoDB Table** — Provides state locking to prevent concurrent Terraform operations.

## Architecture

```
GitHub Actions Workflow
├── push to "test" branch  ──► assumes role in TEST AWS Account
└── push to "release" branch ──► assumes role in RELEASE AWS Account

Each account has:
├── OIDC Provider (token.actions.githubusercontent.com)
├── IAM Role (aiops-demo-github-actions-role)
├── IAM Policies (AdministratorAccess + state access)
├── S3 Bucket (terraform state)
└── DynamoDB Table (state locking)
```

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.7.0
- AWS CLI configured with **two profiles**, one for each account:
  - Test account profile (e.g., `cloudops-demo`)
  - Release account profile (e.g., `cloudops-demo-prod`)
- Both profiles must have permissions to create IAM, S3, and DynamoDB resources

## Configuration

1. Copy the example tfvars file:

   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Edit `terraform.tfvars` and set the required values:

   | Variable | Required | Description |
   |---|---|---|
   | `github_org` | **Yes** | Your GitHub organization or username |
   | `github_repo` | **Yes** | Repository name (without the org prefix) |
   | `project_name` | No | Project name for resource naming (default: `aiops-demo`) |
   | `environments` | **Yes** | Map of environment configs (see below) |
   | `tags` | No | Common tags applied to all resources |

   Each entry in `environments` requires:

   | Field | Description |
   |---|---|
   | `aws_profile` | AWS CLI profile name for this account |
   | `aws_region` | AWS region (e.g., `us-east-1`) |
   | `branch` | Git branch that triggers deploy to this environment |
   | `environment_name` | GitHub Actions environment name |
   | `state_bucket_name` | S3 bucket name for Terraform state (must be globally unique) |
   | `lock_table_name` | DynamoDB table name for state locking |

## Deployment

This project uses **local Terraform state** intentionally — it must be deployable before any remote backend exists.

### Step 1: Initialize

```bash
terraform init
```

### Step 2: Review the plan

```bash
terraform plan
```

### Step 3: Apply

```bash
terraform apply
```

### Step 4: Configure GitHub repository environments

After a successful apply, set the role ARN as a **variable** in each GitHub Actions environment:

```bash
# Set the test environment variable
gh variable set AWS_DEPLOY_ROLE_ARN \
  --repo yagrxu/serverless-demo-for-aiops \
  --env test \
  --body "$(terraform output -raw test_deploy_role_arn)"

# Set the release environment variable
gh variable set AWS_DEPLOY_ROLE_ARN \
  --repo yagrxu/serverless-demo-for-aiops \
  --env release \
  --body "$(terraform output -raw release_deploy_role_arn)"
```

> **Note:** The deploy workflow uses `${{ vars.AWS_DEPLOY_ROLE_ARN }}` (environment-level variable), so each GitHub environment (`test`, `release`) must have its own value pointing to the corresponding account's role.

## GitHub Actions Environment Setup

In your GitHub repository, create two environments:

1. **Settings → Environments → New environment**
2. Create `test` environment:
   - Add variable `AWS_DEPLOY_ROLE_ARN` = test account role ARN
   - (Optional) No branch protection for faster iteration
3. Create `release` environment:
   - Add variable `AWS_DEPLOY_ROLE_ARN` = release account role ARN
   - (Recommended) Add branch protection: only `release` branch

## File Structure

```
init/github/tf/
├── providers.tf                 # Terraform + dual AWS providers (test + release)
├── variables.tf                 # Input variables
├── main.tf                      # OIDC providers + IAM roles (both accounts)
├── iam_policies.tf              # IAM policies (both accounts)
├── state_backend.tf             # S3 buckets + DynamoDB tables (both accounts)
├── outputs.tf                   # Outputs for both accounts
├── terraform.tfvars.example     # Example variable values
└── README.md                    # This file
```

## Outputs

| Output | Description |
|---|---|
| `test_oidc_provider_arn` | OIDC provider ARN (test account) |
| `test_deploy_role_arn` | IAM role ARN for test deploys |
| `test_deploy_role_name` | IAM role name (test account) |
| `test_state_bucket_name` | S3 state bucket (test account) |
| `test_state_lock_table_name` | DynamoDB lock table (test account) |
| `release_oidc_provider_arn` | OIDC provider ARN (release account) |
| `release_deploy_role_arn` | IAM role ARN for release deploys |
| `release_deploy_role_name` | IAM role name (release account) |
| `release_state_bucket_name` | S3 state bucket (release account) |
| `release_state_lock_table_name` | DynamoDB lock table (release account) |

## Relationship to the Deploy Workflow

The GitHub Actions workflow (`.github/workflows/deploy.yml`) resolves the target environment from the branch name:

- `test` branch → `test` environment → assumes role in test account
- `release` branch → `release` environment → assumes role in release account

The workflow step:
```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
```

This reads the environment-scoped variable, which points to the correct account's role.

## Migration from Single-Account Setup

If you previously had a single-account setup:

1. The existing test account resources (OIDC provider, role, state bucket) remain unchanged.
2. Run `terraform plan` to see what new resources will be created for the release account.
3. You may need to `terraform import` existing resources if they were created manually.
4. Update the GitHub repo to have two environments (`test`, `release`) with their respective `AWS_DEPLOY_ROLE_ARN` variables.
