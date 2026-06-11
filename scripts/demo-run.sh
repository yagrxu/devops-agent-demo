#!/usr/bin/env bash
# demo-run.sh — One-command demo trigger: warm up PI data, then inject cascade.
#
# Usage:
#   ./demo-run.sh quick   # Instant alarms (booth demo)
#   ./demo-run.sh full    # 8-minute realistic cascade
#   ./demo-run.sh reset   # Reset alarms to OK
#   ./demo-run.sh warmup  # Just warm up PI (no injection)
#
# Prerequisites: curl, python3, boto3 (pip install boto3)

set -euo pipefail

PROFILE="${AWS_PROFILE:-cloudops-demo}"
REGION="${AWS_REGION:-us-east-1}"
CF_DOMAIN="${CF_DOMAIN:-d1f5i6o9w5rjge.cloudfront.net}"
BASE_URL="https://${CF_DOMAIN}"
WARMUP_SECONDS="${WARMUP_SECONDS:-30}"
WARMUP_CONCURRENCY="${WARMUP_CONCURRENCY:-10}"
MODE="${1:-quick}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INJECT_SCRIPT="${SCRIPT_DIR}/../cdk/scripts/inject_cascade.py"

echo "=== QuickMart Demo Runner ==="
echo "Mode: $MODE | Profile: $PROFILE | Region: $REGION"
echo "CloudFront: $BASE_URL"
echo ""

# --- Health check ---
health_check() {
  echo "[1/3] Health check..."
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ checkout-svc healthy (HTTP 200)"
  else
    echo "  ❌ checkout-svc returned HTTP $HTTP_CODE"
    echo "  The ECS service may be starting up. Waiting 10s and retrying..."
    sleep 10
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" != "200" ]; then
      echo "  ❌ Still unhealthy (HTTP $HTTP_CODE). Check ECS tasks."
      echo "  Continuing anyway — CloudWatch injection will still work."
    fi
  fi
  echo ""
}

# --- Warm up: generate checkout traffic to populate PI ---
warmup() {
  echo "[2/3] Warming up Performance Insights (${WARMUP_SECONDS}s, concurrency ${WARMUP_CONCURRENCY})..."
  echo "  Sending /checkout requests to generate SQL in PI..."

  END_TIME=$(($(date +%s) + WARMUP_SECONDS))
  COUNT=0
  ERRORS=0

  while [ "$(date +%s)" -lt "$END_TIME" ]; do
    for i in $(seq 1 "$WARMUP_CONCURRENCY"); do
      PRODUCT_ID=$((RANDOM % 100 + 1))
      curl -s -X POST "${BASE_URL}/checkout" \
        -H "Content-Type: application/json" \
        -d "{\"user_id\":\"demo-warmup-${i}\",\"product_id\":${PRODUCT_ID},\"quantity\":1}" \
        -o /dev/null -w "%{http_code}" 2>/dev/null | grep -q "201\|409" && COUNT=$((COUNT + 1)) || ERRORS=$((ERRORS + 1)) &
    done
    wait
    sleep 0.5
  done

  echo "  ✅ Warmup done: ${COUNT} successful checkouts, ${ERRORS} errors"
  echo "  PI should now have SQL samples + wait event data."
  echo ""
}

# --- Inject cascade ---
inject() {
  local inject_mode="$1"
  echo "[3/3] Injecting cascade (mode: ${inject_mode})..."

  if [ -f "$INJECT_SCRIPT" ]; then
    python3 "$INJECT_SCRIPT" --mode "$inject_mode" --profile "$PROFILE" --region "$REGION"
  else
    echo "  ❌ inject_cascade.py not found at: $INJECT_SCRIPT"
    exit 1
  fi
  echo ""
}

# --- Main ---
case "$MODE" in
  quick)
    health_check
    warmup
    inject quick
    echo "=== Demo ready. Alarms firing. Agent should begin investigation. ==="
    ;;
  full)
    health_check
    warmup
    inject full
    echo "=== Full cascade running (8 min). Watch CloudWatch / Slack. ==="
    ;;
  reset)
    echo "[*] Resetting demo..."
    inject reset
    echo "=== Reset complete. Ready for next demo run. ==="
    ;;
  warmup)
    health_check
    warmup
    echo "=== Warmup only — PI populated, no alarms triggered. ==="
    ;;
  *)
    echo "Usage: $0 {quick|full|reset|warmup}"
    exit 1
    ;;
esac
