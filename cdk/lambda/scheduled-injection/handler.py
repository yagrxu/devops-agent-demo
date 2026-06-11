"""
Scheduled Demo Injection Lambda

Runs on a schedule (every 2 hours) to:
1. Warm up the checkout app (generates PI data)
2. Inject cascade metrics + force alarms (triggers agent investigation)
3. Wait 10 minutes
4. Reset alarms to OK

This keeps the demo environment exercised and validates the full
alarm → SNS → webhook → DevOps Agent chain continuously.
"""

import json
import os
import time
import urllib.request
import urllib.error
import concurrent.futures
import random
from datetime import datetime, timezone

import boto3

CF_DOMAIN = os.environ['CF_DOMAIN']
REGION = os.environ.get('AWS_REGION', 'us-east-1')
WARMUP_SECONDS = int(os.environ.get('WARMUP_SECONDS', '30'))
WARMUP_CONCURRENCY = int(os.environ.get('WARMUP_CONCURRENCY', '10'))
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

# Peak metrics matching inject_cascade.py TIMELINE[-1]
PEAK_METRICS = {
    'checkout_p99': 820,
    'msk_lag': 85,
    'redis_locks': 14000,
}


def send_checkout(i):
    """Send a single checkout request."""
    product_id = random.randint(1, 100)
    data = json.dumps({'user_id': f'sched-{i}', 'product_id': product_id, 'quantity': 1}).encode()
    req = urllib.request.Request(
        f'{BASE_URL}/checkout',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except (urllib.error.URLError, urllib.error.HTTPError):
        return 0


def warmup():
    """Drive checkout traffic to populate Performance Insights."""
    print(f'[warmup] Sending traffic to {BASE_URL}/checkout for {WARMUP_SECONDS}s...')
    end_time = time.time() + WARMUP_SECONDS
    count = 0
    errors = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=WARMUP_CONCURRENCY) as pool:
        while time.time() < end_time:
            futures = [pool.submit(send_checkout, i) for i in range(WARMUP_CONCURRENCY)]
            for f in concurrent.futures.as_completed(futures):
                status = f.result()
                if status in (201, 409):
                    count += 1
                else:
                    errors += 1
            time.sleep(0.3)

    print(f'[warmup] Done: {count} ok, {errors} errors')


def inject_quick():
    """Push peak metrics and force alarms to ALARM state."""
    cw = boto3.client('cloudwatch', region_name=REGION)
    ts = datetime.now(timezone.utc)

    # Push peak custom metrics (3 rounds to satisfy evaluation periods)
    for _ in range(3):
        cw.put_metric_data(Namespace='QuickMart/Application', MetricData=[{
            'MetricName': 'CheckoutP99Latency',
            'Dimensions': [{'Name': 'Service', 'Value': 'checkout-svc'}, {'Name': 'Environment', 'Value': 'demo'}],
            'Timestamp': ts, 'Value': PEAK_METRICS['checkout_p99'], 'Unit': 'Milliseconds',
        }])
        cw.put_metric_data(Namespace='QuickMart/Messaging', MetricData=[
            {'MetricName': 'ConsumerLagSeconds',
             'Dimensions': [{'Name': 'Topic', 'Value': 'order.placed'}, {'Name': 'ConsumerGroup', 'Value': 'reconciler-cg'}],
             'Timestamp': ts, 'Value': PEAK_METRICS['msk_lag'], 'Unit': 'Seconds'},
            {'MetricName': 'ConsumerLagSeconds',
             'Dimensions': [{'Name': 'Topic', 'Value': 'payment.proc'}, {'Name': 'ConsumerGroup', 'Value': 'notifier-cg'}],
             'Timestamp': ts, 'Value': PEAK_METRICS['msk_lag'] * 0.82, 'Unit': 'Seconds'},
        ])
        cw.put_metric_data(Namespace='QuickMart/Redis', MetricData=[{
            'MetricName': 'RedisLockKeyCount',
            'Dimensions': [{'Name': 'Keyspace', 'Value': 'lock:inv:*'}, {'Name': 'Cluster', 'Value': 'quickmart-demo-redis'}],
            'Timestamp': ts, 'Value': PEAK_METRICS['redis_locks'], 'Unit': 'Count',
        }])
        time.sleep(3)

    # Force alarm states
    for alarm in ALARMS:
        try:
            cw.set_alarm_state(
                AlarmName=alarm,
                StateValue='ALARM',
                StateReason='Scheduled demo injection - flash sale cascade',
            )
            print(f'[inject] {alarm} → ALARM')
        except Exception as e:
            print(f'[inject] {alarm} failed: {e}')


def reset():
    """Reset all alarms to OK."""
    cw = boto3.client('cloudwatch', region_name=REGION)
    for alarm in ALARMS:
        try:
            cw.set_alarm_state(AlarmName=alarm, StateValue='OK', StateReason='Scheduled reset')
            print(f'[reset] {alarm} → OK')
        except Exception as e:
            print(f'[reset] {alarm} failed: {e}')

    # Push healthy metrics
    ts = datetime.now(timezone.utc)
    cw.put_metric_data(Namespace='QuickMart/Application', MetricData=[{
        'MetricName': 'CheckoutP99Latency',
        'Dimensions': [{'Name': 'Service', 'Value': 'checkout-svc'}, {'Name': 'Environment', 'Value': 'demo'}],
        'Timestamp': ts, 'Value': 85, 'Unit': 'Milliseconds',
    }])
    cw.put_metric_data(Namespace='QuickMart/Messaging', MetricData=[{
        'MetricName': 'ConsumerLagSeconds',
        'Dimensions': [{'Name': 'Topic', 'Value': 'order.placed'}, {'Name': 'ConsumerGroup', 'Value': 'reconciler-cg'}],
        'Timestamp': ts, 'Value': 2, 'Unit': 'Seconds',
    }])


def handler(event, context):
    print(f'[handler] Starting scheduled injection at {datetime.now(timezone.utc).isoformat()}')

    # Step 1: Warmup
    warmup()

    # Step 2: Inject
    inject_quick()
    print(f'[handler] Injection done. Waiting {RESET_DELAY_SECONDS}s before reset...')

    # Step 3: Wait then reset
    time.sleep(RESET_DELAY_SECONDS)
    reset()

    print('[handler] Cycle complete.')
    return {'statusCode': 200, 'body': 'injection cycle complete'}
