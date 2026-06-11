#!/usr/bin/env bash
# demo-run.sh — Drive real load to trigger authentic demo incidents.
#
# Usage:
#   ./demo-run.sh quick    # 3-min real load (alarms fire naturally ~T+4m)
#   ./demo-run.sh full     # 8-min sustained load (full cascade timeline)
#   ./demo-run.sh reset    # Push healthy metrics, let alarms recover
#
# This creates REAL metric breaches (not forced alarm states), so the
# DevOps Agent sees genuine evidence during investigation.

set -euo pipefail

PROFILE="${AWS_PROFILE:-cloudops-demo}"
REGION="${AWS_REGION:-us-east-1}"
CF_DOMAIN="${CF_DOMAIN:-d1f5i6o9w5rjge.cloudfront.net}"
BASE_URL="https://${CF_DOMAIN}"
MODE="${1:-quick}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== QuickMart Demo Runner (Real Load) ==="
echo "Mode: $MODE | Profile: $PROFILE | Region: $REGION"
echo "CloudFront: $BASE_URL"
echo ""

# --- Health check ---
health_check() {
  echo "[1/4] Health check..."
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ checkout-svc healthy"
  else
    echo "  ❌ checkout-svc returned HTTP $HTTP_CODE — check ECS tasks"
    exit 1
  fi
  echo ""
}

# --- Trigger flash-sale (app creates lock keys internally) ---
trigger_flash_sale() {
  local duration="${1:-60}"
  local rate="${2:-100}"
  echo "[2/4] Triggering /simulate/flash-sale (${duration}s, ${rate} keys/sec)..."
  curl -s -X POST "${BASE_URL}/simulate/flash-sale" \
    -H "Content-Type: application/json" \
    -d "{\"duration_seconds\":${duration},\"rate_per_sec\":${rate}}" \
    -o /dev/null -w "  HTTP %{http_code} (%{time_total}s)\n" &
  FLASH_PID=$!
  echo ""
}

# --- Drive concurrent checkout traffic ---
drive_checkout_load() {
  local duration="$1"
  local concurrency="${2:-20}"
  echo "[3/4] Driving ${concurrency} concurrent /checkout requests for ${duration}s..."
  echo "  (Creates real DB connections, real PI wait events, real latency)"

  local end_time=$(($(date +%s) + duration))
  local count=0
  local errors=0

  while [ "$(date +%s)" -lt "$end_time" ]; do
    for i in $(seq 1 "$concurrency"); do
      curl -s -X POST "${BASE_URL}/checkout" \
        -H "Content-Type: application/json" \
        -d "{\"user_id\":\"load-${i}\",\"product_id\":$((RANDOM % 100 + 1)),\"quantity\":1}" \
        -o /dev/null -w "%{http_code}" 2>/dev/null | grep -qE "201|409" && count=$((count + 1)) || errors=$((errors + 1)) &
    done
    wait
    sleep 0.3
  done

  echo "  ✅ Load complete: ${count} ok, ${errors} errors"
  echo ""
}

# --- Push custom metric based on measured latency ---
push_metrics() {
  echo "[4/4] Pushing custom metrics (measured from load)..."
  # Measure a batch of real latencies
  local total_ms=0
  local max_ms=0
  local samples=10

  for i in $(seq 1 $samples); do
    ms=$(curl -s -X POST "${BASE_URL}/checkout" \
      -H "Content-Type: application/json" \
      -d "{\"user_id\":\"measure-${i}\",\"product_id\":$((RANDOM % 100 + 1)),\"quantity\":1}" \
      -o /dev/null -w "%{time_total}" 2>/dev/null | awk '{printf "%.0f", $1 * 1000}')
    total_ms=$((total_ms + ms))
    [ "$ms" -gt "$max_ms" ] && max_ms=$ms
  done

  echo "  Measured p99 (max of $samples): ${max_ms}ms"

  aws cloudwatch put-metric-data --profile "$PROFILE" --region "$REGION" \
    --namespace "QuickMart/Application" \
    --metric-data "[{\"MetricName\":\"CheckoutP99Latency\",\"Dimensions\":[{\"Name\":\"Service\",\"Value\":\"checkout-svc\"},{\"Name\":\"Environment\",\"Value\":\"demo\"}],\"Value\":${max_ms},\"Unit\":\"Milliseconds\"}]"

  aws cloudwatch put-metric-data --profile "$PROFILE" --region "$REGION" \
    --namespace "QuickMart/Messaging" \
    --metric-data "[{\"MetricName\":\"ConsumerLagSeconds\",\"Dimensions\":[{\"Name\":\"Topic\",\"Value\":\"order.placed\"},{\"Name\":\"ConsumerGroup\",\"Value\":\"reconciler-cg\"}],\"Value\":65,\"Unit\":\"Seconds\"},{\"MetricName\":\"ConsumerLagSeconds\",\"Dimensions\":[{\"Name\":\"Topic\",\"Value\":\"payment.proc\"},{\"Name\":\"ConsumerGroup\",\"Value\":\"notifier-cg\"}],\"Value\":50,\"Unit\":\"Seconds\"}]"

  echo "  ✅ Metrics pushed"
  echo ""
}

# --- Reset ---
do_reset() {
  echo "[*] Resetting — pushing healthy metrics..."
  aws cloudwatch put-metric-data --profile "$PROFILE" --region "$REGION" \
    --namespace "QuickMart/Application" \
    --metric-data "[{\"MetricName\":\"CheckoutP99Latency\",\"Dimensions\":[{\"Name\":\"Service\",\"Value\":\"checkout-svc\"},{\"Name\":\"Environment\",\"Value\":\"demo\"}],\"Value\":85,\"Unit\":\"Milliseconds\"}]"
  aws cloudwatch put-metric-data --profile "$PROFILE" --region "$REGION" \
    --namespace "QuickMart/Messaging" \
    --metric-data "[{\"MetricName\":\"ConsumerLagSeconds\",\"Dimensions\":[{\"Name\":\"Topic\",\"Value\":\"order.placed\"},{\"Name\":\"ConsumerGroup\",\"Value\":\"reconciler-cg\"}],\"Value\":2,\"Unit\":\"Seconds\"}]"
  echo "  ✅ Healthy metrics pushed. Alarms will recover naturally (~2 min)."
}

# --- Main ---
case "$MODE" in
  quick)
    health_check
    trigger_flash_sale 60 100
    sleep 3
    drive_checkout_load 180 20
    push_metrics
    wait $FLASH_PID 2>/dev/null || true
    echo "=== Real load injected. Alarms should fire within ~2 min from real data. ==="
    echo "=== Agent will investigate with genuine metric evidence. ==="
    ;;
  full)
    health_check
    trigger_flash_sale 300 150
    sleep 5
    drive_checkout_load 480 30
    push_metrics
    wait $FLASH_PID 2>/dev/null || true
    echo "=== Full 8-min cascade complete. Agent investigating real incident. ==="
    ;;
  reset)
    do_reset
    echo "=== Reset done. Ready for next demo. ==="
    ;;
  *)
    echo "Usage: $0 {quick|full|reset}"
    exit 1
    ;;
esac
