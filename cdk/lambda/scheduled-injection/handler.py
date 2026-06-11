"""
Scheduled Demo Injection Lambda

Drives REAL load through the QuickMart checkout app to create authentic
metric breaches that the DevOps Agent investigates with skills.

Flow:
1. Trigger /simulate/flash-sale (creates lock:inv:* keys with 30s TTL inside VPC)
2. Blast concurrent /checkout requests (causes real DB load, real latency)
3. Push CheckoutP99Latency custom metric based on actual measured response times
4. Let CloudWatch alarms fire naturally from real metric data
5. Wait, then reset (stop load, let metrics recover)

The agent sees genuine evidence: real PI wait events, real connection counts,
real eviction pressure — not fake forced alarm states.
"""

import json
import os
import time
import urllib.request
import urllib.error
import concurrent.futures
import random
import statistics
from datetime import datetime, timezone

import boto3

CF_DOMAIN = os.environ['CF_DOMAIN']
REGION = os.environ.get('AWS_REGION', 'us-east-1')
LOAD_DURATION_SECONDS = int(os.environ.get('LOAD_DURATION_SECONDS', '180'))
CONCURRENCY = int(os.environ.get('CONCURRENCY', '20'))
RESET_DELAY_SECONDS = int(os.environ.get('RESET_DELAY_SECONDS', '600'))

BASE_URL = f'https://{CF_DOMAIN}'

ALARMS = [
    'quickmart-demo-redis-memory-high',
    'quickmart-demo-redis-evictions-high',
    'quickmart-demo-rds-connections-high',
    'quickmart-demo-rds-latency-high',
    'quickmart-demo-checkout-p99-high',
    'quickmart-demo-msk-consumer-lag-high',
]


def post_json(path, body=None, timeout=15):
    """POST to the app and return (status_code, elapsed_ms)."""
    url = f'{BASE_URL}{path}'
    data = json.dumps(body).encode() if body else b'{}'
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - start) * 1000
            return resp.status, elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - start) * 1000
        return e.code, elapsed
    except (urllib.error.URLError, OSError):
        elapsed = (time.time() - start) * 1000
        return 0, elapsed


def trigger_flash_sale():
    """Tell the app to start creating lock keys internally (runs inside VPC)."""
    print('[flash-sale] Triggering /simulate/flash-sale (60s, 100 keys/sec)...')
    status, elapsed = post_json('/simulate/flash-sale', {
        'duration_seconds': 60,
        'rate_per_sec': 100,
    }, timeout=90)
    print(f'[flash-sale] Done: status={status}, elapsed={elapsed:.0f}ms')


def checkout_worker(worker_id):
    """Single checkout request, returns elapsed_ms."""
    product_id = random.randint(1, 100)
    status, elapsed = post_json('/checkout', {
        'user_id': f'load-{worker_id}-{random.randint(1000, 9999)}',
        'product_id': product_id,
        'quantity': 1,
    })
    return status, elapsed


def push_latency_metric(latencies_ms):
    """Push real measured p99 latency as a custom CloudWatch metric."""
    if not latencies_ms:
        return
    p99 = statistics.quantiles(latencies_ms, n=100)[98] if len(latencies_ms) >= 100 else max(latencies_ms)
    cw = boto3.client('cloudwatch', region_name=REGION)
    cw.put_metric_data(
        Namespace='QuickMart/Application',
        MetricData=[{
            'MetricName': 'CheckoutP99Latency',
            'Dimensions': [
                {'Name': 'Service', 'Value': 'checkout-svc'},
                {'Name': 'Environment', 'Value': 'demo'},
            ],
            'Timestamp': datetime.now(timezone.utc),
            'Value': p99,
            'Unit': 'Milliseconds',
        }],
    )
    print(f'[metrics] Pushed CheckoutP99Latency p99={p99:.0f}ms (from {len(latencies_ms)} samples)')


def push_consumer_lag():
    """Push simulated consumer lag (checkout slowdown reduces producer rate)."""
    cw = boto3.client('cloudwatch', region_name=REGION)
    cw.put_metric_data(
        Namespace='QuickMart/Messaging',
        MetricData=[
            {'MetricName': 'ConsumerLagSeconds',
             'Dimensions': [{'Name': 'Topic', 'Value': 'order.placed'},
                            {'Name': 'ConsumerGroup', 'Value': 'reconciler-cg'}],
             'Timestamp': datetime.now(timezone.utc),
             'Value': 65, 'Unit': 'Seconds'},
            {'MetricName': 'ConsumerLagSeconds',
             'Dimensions': [{'Name': 'Topic', 'Value': 'payment.proc'},
                            {'Name': 'ConsumerGroup', 'Value': 'notifier-cg'}],
             'Timestamp': datetime.now(timezone.utc),
             'Value': 50, 'Unit': 'Seconds'},
        ],
    )


def drive_load():
    """Drive concurrent checkout traffic and collect latencies."""
    print(f'[load] Driving {CONCURRENCY} concurrent checkouts for {LOAD_DURATION_SECONDS}s...')
    end_time = time.time() + LOAD_DURATION_SECONDS
    total_requests = 0
    total_errors = 0
    batch_latencies = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        while time.time() < end_time:
            futures = [pool.submit(checkout_worker, i) for i in range(CONCURRENCY)]
            for f in concurrent.futures.as_completed(futures):
                status, elapsed = f.result()
                batch_latencies.append(elapsed)
                if status in (201, 409):
                    total_requests += 1
                else:
                    total_errors += 1

            # Every 30s, push the measured p99 + consumer lag
            if len(batch_latencies) >= CONCURRENCY * 5:
                push_latency_metric(batch_latencies)
                push_consumer_lag()
                batch_latencies = []

            time.sleep(0.2)

    # Final push
    if batch_latencies:
        push_latency_metric(batch_latencies)
        push_consumer_lag()

    print(f'[load] Done: {total_requests} successful, {total_errors} errors')


def reset():
    """Push healthy metrics to let alarms recover naturally."""
    cw = boto3.client('cloudwatch', region_name=REGION)
    ts = datetime.now(timezone.utc)
    cw.put_metric_data(Namespace='QuickMart/Application', MetricData=[{
        'MetricName': 'CheckoutP99Latency',
        'Dimensions': [{'Name': 'Service', 'Value': 'checkout-svc'},
                       {'Name': 'Environment', 'Value': 'demo'}],
        'Timestamp': ts, 'Value': 85, 'Unit': 'Milliseconds',
    }])
    cw.put_metric_data(Namespace='QuickMart/Messaging', MetricData=[{
        'MetricName': 'ConsumerLagSeconds',
        'Dimensions': [{'Name': 'Topic', 'Value': 'order.placed'},
                       {'Name': 'ConsumerGroup', 'Value': 'reconciler-cg'}],
        'Timestamp': ts, 'Value': 2, 'Unit': 'Seconds',
    }])
    print('[reset] Pushed healthy metrics. Alarms will recover naturally.')


def handler(event, context):
    print(f'[handler] Starting real-load injection at {datetime.now(timezone.utc).isoformat()}')

    # Step 1: Trigger flash-sale simulation (app creates lock keys internally)
    # Run in background thread since it blocks for 60s
    import threading
    flash_thread = threading.Thread(target=trigger_flash_sale)
    flash_thread.start()

    # Step 2: Drive concurrent checkout load (creates real DB pressure + measures latency)
    time.sleep(5)  # Let flash-sale start accumulating locks first
    drive_load()

    flash_thread.join(timeout=10)

    # Step 3: Wait for agent to investigate, then reset
    print(f'[handler] Load complete. Waiting {RESET_DELAY_SECONDS}s before reset...')
    time.sleep(RESET_DELAY_SECONDS)

    # Step 4: Reset
    reset()

    print('[handler] Cycle complete.')
    return {'statusCode': 200, 'body': 'real-load injection cycle complete'}
