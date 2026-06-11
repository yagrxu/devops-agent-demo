#!/usr/bin/env python3
"""
Flash Sale Cascade Error Injection Script

Simulates cascading failure by:
1. Pushing custom CloudWatch metrics (checkout latency, MSK lag)
2. Generating real Redis load (lock key accumulation)
3. Generating real RDS load (connection storms)
4. Triggering CloudWatch alarms

Usage:
    # Full cascade (8 minutes to alarm trigger)
    python inject_cascade.py --mode full --profile cloudops-demo --region us-east-1

    # Quick demo (push metrics directly to trigger alarms in 2 min)
    python inject_cascade.py --mode quick --profile cloudops-demo --region us-east-1

    # Reset (clear metrics, set alarms to OK)
    python inject_cascade.py --mode reset --profile cloudops-demo --region us-east-1

Prerequisites:
    pip install boto3 redis psycopg2-binary
"""

import argparse
import time
import json
import threading
from datetime import datetime, timezone

import boto3


REGION = 'us-east-1'
REDIS_CACHE_NAME = 'quickmart-demo-redis'

ALARMS = [
    'quickmart-demo-redis-memory-high',
    'quickmart-demo-redis-evictions-high',
    'quickmart-demo-rds-connections-high',
    'quickmart-demo-rds-latency-high',
    'quickmart-demo-checkout-p99-high',
    'quickmart-demo-msk-consumer-lag-high',
]

# Timeline: metric values at each minute
TIMELINE = [
    # min, checkout_p99_ms, msk_lag_sec, redis_mem_pct, redis_evictions, rds_connections
    (0,    85,   2,   70,    0,    45),
    (1,    90,   3,   75,    0,    48),
    (2,    95,   5,   85,    0,    52),
    (3,   130,   8,   92,  120,    78),
    (4,   280,  15,   96,  280,   165),
    (5,   480,  28,   98,  320,   198),
    (6,   650,  55,   99,  350,   200),
    (7,   750,  70,   99,  380,   200),
    (8,   820,  85,   99,  400,   200),
]


def get_session(profile: str, region: str):
    return boto3.Session(profile_name=profile, region_name=region)


def push_metrics(session, minute_data):
    """Push custom CloudWatch metrics for the current timestep."""
    cw = session.client('cloudwatch')
    ts = datetime.now(timezone.utc)
    minute, checkout_p99, msk_lag, redis_mem_pct, redis_evictions, rds_conns = minute_data

    metric_data = [
        {
            'MetricName': 'CheckoutP99Latency',
            'Dimensions': [
                {'Name': 'Service', 'Value': 'checkout-svc'},
                {'Name': 'Environment', 'Value': 'demo'},
            ],
            'Timestamp': ts,
            'Value': checkout_p99,
            'Unit': 'Milliseconds',
        },
        {
            'MetricName': 'ConsumerLagSeconds',
            'Dimensions': [
                {'Name': 'Topic', 'Value': 'order.placed'},
                {'Name': 'ConsumerGroup', 'Value': 'reconciler-cg'},
            ],
            'Timestamp': ts,
            'Value': msk_lag,
            'Unit': 'Seconds',
        },
        {
            'MetricName': 'ConsumerLagSeconds',
            'Dimensions': [
                {'Name': 'Topic', 'Value': 'payment.proc'},
                {'Name': 'ConsumerGroup', 'Value': 'notifier-cg'},
            ],
            'Timestamp': ts,
            'Value': msk_lag * 0.82,
            'Unit': 'Seconds',
        },
        {
            'MetricName': 'RedisLockKeyCount',
            'Dimensions': [
                {'Name': 'Keyspace', 'Value': 'lock:inv:*'},
                {'Name': 'Cluster', 'Value': REDIS_CACHE_NAME},
            ],
            'Timestamp': ts,
            'Value': 200 + minute * 1700,
            'Unit': 'Count',
        },
    ]

    # Push app metrics
    cw.put_metric_data(
        Namespace='QuickMart/Application',
        MetricData=[metric_data[0]],
    )
    cw.put_metric_data(
        Namespace='QuickMart/Messaging',
        MetricData=metric_data[1:3],
    )
    cw.put_metric_data(
        Namespace='QuickMart/Redis',
        MetricData=[metric_data[3]],
    )

    print(f"  [T+{minute}m] Pushed: checkout_p99={checkout_p99}ms, "
          f"msk_lag={msk_lag}s, redis_locks={200 + minute * 1700}")


def run_full_cascade(session):
    """Run the full 8-minute cascade, pushing metrics each minute."""
    print("Starting full cascade injection (8 minutes)...")
    print("Alarms should fire at ~T+4-5m (after 2 evaluation periods)")
    print()

    for step in TIMELINE:
        minute = step[0]
        print(f"[T+{minute}m] Injecting metrics...")
        push_metrics(session, step)

        if minute < len(TIMELINE) - 1:
            print(f"  Waiting 60s until T+{minute + 1}m...")
            time.sleep(60)

    print()
    print("Cascade injection complete.")
    print("Expected alarm state: checkout-p99 and msk-lag should be in ALARM.")
    print("DevOps Agent should begin investigation shortly.")


def run_quick_demo(session):
    """Push peak metrics immediately + force alarm state for instant demo."""
    cw = session.client('cloudwatch')
    print("Quick mode: pushing peak metrics and forcing alarm states...")

    # Push several data points at peak values to satisfy evaluation periods
    for i in range(3):
        push_metrics(session, TIMELINE[-1])  # Push T+8 values
        if i < 2:
            time.sleep(5)

    # Force alarm states for immediate demo
    print()
    print("Forcing alarm states...")
    force_alarms = [
        'quickmart-demo-checkout-p99-high',
        'quickmart-demo-msk-consumer-lag-high',
        'quickmart-demo-redis-evictions-high',
        'quickmart-demo-rds-connections-high',
    ]
    for alarm_name in force_alarms:
        try:
            cw.set_alarm_state(
                AlarmName=alarm_name,
                StateValue='ALARM',
                StateReason='Threshold Crossed: metric breached configured threshold',
            )
            print(f"  {alarm_name} → ALARM")
        except Exception as e:
            print(f"  {alarm_name} → FAILED: {e}")

    print()
    print("Quick injection complete. All alarms in ALARM state.")
    print("DevOps Agent should pick up immediately.")


def run_reset(session):
    """Reset all alarms to OK and stop metric injection."""
    cw = session.client('cloudwatch')
    print("Resetting demo environment...")

    # Set all alarms to OK
    for alarm_name in ALARMS:
        try:
            cw.set_alarm_state(
                AlarmName=alarm_name,
                StateValue='OK',
                StateReason='Demo reset',
            )
            print(f"  {alarm_name} → OK")
        except Exception as e:
            print(f"  {alarm_name} → FAILED: {e}")

    # Push healthy metrics
    healthy_data = (0, 85, 2, 70, 0, 45)
    push_metrics(session, healthy_data)

    print()
    print("Reset complete. Ready for next demo run.")


def generate_redis_load(session, redis_endpoint: str, duration_sec: int = 120):
    """
    Generate actual Redis load if endpoint is reachable.
    Creates lock:inv:* keys with 30s TTL to simulate the misconfiguration.
    """
    try:
        import redis as redis_lib
    except ImportError:
        print("  redis-py not installed. Skipping real Redis load. (pip install redis)")
        return

    print(f"  Connecting to Redis: {redis_endpoint}...")
    try:
        r = redis_lib.Redis(host=redis_endpoint, port=6379, ssl=True, decode_responses=True)
        r.ping()
    except Exception as e:
        print(f"  Cannot connect to Redis: {e}. Skipping real load.")
        return

    print(f"  Generating lock:inv:* keys with 30s TTL for {duration_sec}s...")
    end_time = time.time() + duration_sec
    count = 0
    while time.time() < end_time:
        key = f"lock:inv:sku-{count:06d}"
        r.set(key, "1", ex=30, nx=True)
        count += 1
        if count % 100 == 0:
            time.sleep(0.01)  # ~100 keys/10ms = 10K keys/sec

    print(f"  Created {count} lock keys in Redis.")


def generate_rds_load(session, duration_sec: int = 120):
    """
    Generate actual RDS connection load if credentials are available.
    Opens many connections to simulate connection pool exhaustion.
    """
    try:
        import psycopg2
    except ImportError:
        print("  psycopg2 not installed. Skipping real RDS load. (pip install psycopg2-binary)")
        return

    # Get credentials from Secrets Manager
    sm = session.client('secretsmanager')
    try:
        secret = sm.get_secret_value(SecretId='devops-agent-demo/aurora-credentials')
        creds = json.loads(secret['SecretString'])
    except Exception as e:
        print(f"  Cannot retrieve Aurora credentials: {e}. Skipping real load.")
        return

    host = creds.get('host')
    port = creds.get('port', 5432)
    user = creds.get('username')
    password = creds.get('password')
    dbname = creds.get('dbname', 'quickmart')

    print(f"  Opening connections to Aurora: {host}...")
    connections = []
    target_conns = 40  # Enough to trigger alarm threshold of 50 (plus baseline)

    try:
        for i in range(target_conns):
            conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
            conn.autocommit = True
            connections.append(conn)
            if (i + 1) % 10 == 0:
                print(f"    Opened {i + 1}/{target_conns} connections")

        print(f"  Holding {len(connections)} connections for {duration_sec}s...")
        time.sleep(duration_sec)
    finally:
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
        print(f"  Closed all connections.")


def main():
    parser = argparse.ArgumentParser(description='DevOps Agent Demo - Error Injection')
    parser.add_argument('--mode', choices=['full', 'quick', 'reset'], required=True,
                        help='full: 8-min cascade, quick: instant alarms, reset: clear all')
    parser.add_argument('--profile', default='cloudops-demo', help='AWS profile name')
    parser.add_argument('--region', default=REGION, help='AWS region')
    parser.add_argument('--real-load', action='store_true',
                        help='Also generate real Redis/RDS load (requires network access)')
    parser.add_argument('--redis-endpoint', default=None,
                        help='Redis endpoint for real load generation')
    args = parser.parse_args()

    session = get_session(args.profile, args.region)
    print(f"Using profile: {args.profile}, region: {args.region}")
    print()

    if args.mode == 'full':
        if args.real_load and args.redis_endpoint:
            # Run real load in background threads
            redis_thread = threading.Thread(
                target=generate_redis_load,
                args=(session, args.redis_endpoint, 480)
            )
            rds_thread = threading.Thread(
                target=generate_rds_load,
                args=(session, 480)
            )
            redis_thread.start()
            rds_thread.start()

        run_full_cascade(session)

    elif args.mode == 'quick':
        run_quick_demo(session)

    elif args.mode == 'reset':
        run_reset(session)


if __name__ == '__main__':
    main()
