#!/usr/bin/env bash
set -euo pipefail

# Deploy the Slack integration using local slack-config.json
# Usage: ./scripts/deploy-slack.sh [--profile PROFILE] [--region REGION]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$CDK_DIR/slack-config.json"

PROFILE="${AWS_PROFILE:-}"
REGION="${AWS_REGION:-ap-southeast-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: $CONFIG_FILE not found."
  echo "Copy slack-config.example.json to slack-config.json and fill in your values."
  exit 1
fi

AWS_OPTS=()
if [[ -n "$PROFILE" ]]; then
  AWS_OPTS+=(--profile "$PROFILE")
fi
AWS_OPTS+=(--region "$REGION")

# Parse config
BOT_TOKEN=$(jq -r '.slack.botToken' "$CONFIG_FILE")
SIGNING_SECRET=$(jq -r '.slack.signingSecret' "$CONFIG_FILE")
WEBHOOK_URL=$(jq -r '.webhook.url' "$CONFIG_FILE")
HMAC_SECRET=$(jq -r '.webhook.hmacSecret' "$CONFIG_FILE")
OPERATOR_ROLE_ARN=$(jq -r '.operatorRoleArn' "$CONFIG_FILE")
DEPLOYMENT_ID=$(jq -r '.deploymentId' "$CONFIG_FILE")
AGENT_SPACE_ID="${AGENT_SPACE_ID:-89d37b8a-5dda-49b7-b54e-ba205ad942f6}"

PROJECT_NAME="quickmart-demo"
WEBHOOK_SECRET_NAME="$PROJECT_NAME/devops-agent-webhook"
SLACK_SECRET_NAME="$PROJECT_NAME/slack-bot"

echo "=== Deploying Slack Integration ==="
echo "  Region:         $REGION"
echo "  Deployment ID:  $DEPLOYMENT_ID"
echo "  Operator Role:  $OPERATOR_ROLE_ARN"
echo ""

# --- Create/update Secrets Manager secrets ---
create_or_update_secret() {
  local name="$1"
  local value="$2"
  if aws secretsmanager describe-secret --secret-id "$name" "${AWS_OPTS[@]}" &>/dev/null; then
    echo "  Updating secret: $name"
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" "${AWS_OPTS[@]}" >/dev/null
  else
    echo "  Creating secret: $name"
    aws secretsmanager create-secret --name "$name" --secret-string "$value" "${AWS_OPTS[@]}" >/dev/null
  fi
}

WEBHOOK_SECRET_VALUE=$(jq -n \
  --arg url "$WEBHOOK_URL" \
  --arg hmac "$HMAC_SECRET" \
  '{url: $url, hmac_secret: $hmac}')

SLACK_SECRET_VALUE=$(jq -n \
  --arg bot_token "$BOT_TOKEN" \
  --arg signing_secret "$SIGNING_SECRET" \
  --arg agent_space_id "$AGENT_SPACE_ID" \
  --arg operator_role_arn "$OPERATOR_ROLE_ARN" \
  '{bot_token: $bot_token, signing_secret: $signing_secret, agent_space_id: $agent_space_id, operator_role_arn: $operator_role_arn}')

echo "--- Secrets ---"
create_or_update_secret "$WEBHOOK_SECRET_NAME" "$WEBHOOK_SECRET_VALUE"
create_or_update_secret "$SLACK_SECRET_NAME" "$SLACK_SECRET_VALUE"

# Get secret ARNs
WEBHOOK_SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$WEBHOOK_SECRET_NAME" "${AWS_OPTS[@]}" --query 'ARN' --output text)
SLACK_SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SLACK_SECRET_NAME" "${AWS_OPTS[@]}" --query 'ARN' --output text)

echo ""
echo "--- CDK Deploy ---"
cd "$CDK_DIR"
npx cdk deploy DevOpsAgentDemoStack --require-approval never \
  -c slackWebhookSecretArn="$WEBHOOK_SECRET_ARN" \
  -c slackSecretArn="$SLACK_SECRET_ARN" \
  -c slackOperatorRoleArn="$OPERATOR_ROLE_ARN" \
  -c slackDeploymentId="$DEPLOYMENT_ID" \
  -c slackWebhookSecretName="$WEBHOOK_SECRET_NAME" \
  -c slackSecretName="$SLACK_SECRET_NAME"

echo ""
echo "=== Done ==="
echo ""
echo "NEXT STEPS:"
echo "1. Copy the SlackApiEndpoint from the outputs above"
echo "2. In your Slack app settings:"
echo "   - Event Subscriptions → Request URL: <endpoint>"
echo "   - Slash Commands → Create /devops with Request URL: <endpoint>"
echo "   - OAuth scopes: chat:write, commands, app_mentions:read"
echo "   - Subscribe to bot events: app_mention"
