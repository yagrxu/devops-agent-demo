#!/usr/bin/env python3
"""
Flash Sale Cascade Simulator
Generates mock telemetry data simulating a cascading failure across Redis, RDS, and MSK.
Outputs JSON files representing CloudWatch/Performance Insights/Redis INFO responses.

Usage:
    python simulate_flash_sale.py --output-dir ../mock-telemetry/
"""

import json
import os
import random
import argparse
from datetime import datetime, timedelta

SCENARIO_START = datetime(2026, 6, 13, 14, 30, 0)  # 14:30 HKT demo time
NORMAL_RPS = 500
SPIKE_RPS = 5000


def add_jitter(value, pct=0.05):
    """Add realistic noise to metrics."""
    return round(value * (1 + random.uniform(-pct, pct)), 2)


def generate_redis_metrics(timeline_minutes):
    """Generate Redis INFO-style metrics for each minute of the scenario."""
    metrics = []

    for t in range(timeline_minutes + 1):
        ts = SCENARIO_START + timedelta(minutes=t)

        if t < 2:
            lock_keys = 200 + t * 50
            used_memory_gb = 18.2 + t * 0.1
            evictions_per_min = 0
            ops_per_sec = 12000 + t * 6000
        elif t < 3:
            lock_keys = 4800
            used_memory_gb = 22.1
            evictions_per_min = 0
            ops_per_sec = 48000
        elif t < 4:
            progress = (t - 3)
            lock_keys = 7200 + progress * 2300
            used_memory_gb = 25.4 + progress * 0.4
            evictions_per_min = 1200 + progress * 1600
            ops_per_sec = 52000 + progress * 3000
        elif t < 6:
            progress = (t - 4) / 2
            lock_keys = int(9500 + progress * 2500)
            used_memory_gb = 25.8 + progress * 0.1
            evictions_per_min = int(2800 + progress * 700)
            ops_per_sec = int(55000 - progress * 5000)
        elif t < 8:
            progress = (t - 6) / 2
            lock_keys = int(12000 + progress * 2000)
            used_memory_gb = 25.9
            evictions_per_min = int(3500 + progress * 500)
            ops_per_sec = int(50000 - progress * 2000)
        else:
            lock_keys = 14000
            used_memory_gb = 25.9
            evictions_per_min = 4000
            ops_per_sec = 48000

        entry = {
            "timestamp": ts.isoformat(),
            "elapsed_minutes": t,
            "redis_info": {
                "used_memory_bytes": int(add_jitter(used_memory_gb * 1024**3)),
                "used_memory_human": f"{add_jitter(used_memory_gb):.1f}G",
                "maxmemory_bytes": int(26 * 1024**3),
                "maxmemory_policy": "volatile-lfu",
                "evicted_keys_total": sum(range(t)) * 200 + evictions_per_min,
                "evicted_keys_per_min": int(add_jitter(evictions_per_min)),
                "instantaneous_ops_per_sec": int(add_jitter(ops_per_sec)),
                "connected_clients": int(add_jitter(180 + t * 15, 0.08)),
                "keyspace": {
                    "db0": {
                        "keys": int(add_jitter(195000 + lock_keys - evictions_per_min * 0.3)),
                        "expires": int(add_jitter(170000)),
                        "avg_ttl": int(add_jitter(45000))
                    }
                }
            },
            "keyspace_breakdown": {
                "session:*": {
                    "count": int(add_jitter(45000 + t * 800)),
                    "memory_mb": int(add_jitter(2800 + t * 50)),
                    "avg_ttl_sec": int(add_jitter(3200)),
                    "evictions_last_min": 0  # never evicted (no-evict class)
                },
                "cache:product:*": {
                    "count": int(add_jitter(max(120000 - evictions_per_min * 0.7, 80000))),
                    "memory_mb": int(add_jitter(max(8500 - evictions_per_min * 0.6, 5500))),
                    "avg_ttl_sec": int(add_jitter(180)),
                    "evictions_last_min": int(add_jitter(evictions_per_min * 0.7))
                },
                "lock:inv:*": {
                    "count": int(add_jitter(lock_keys)),
                    "memory_mb": int(add_jitter(lock_keys * 0.002 * 1024)),  # ~2KB per key
                    "avg_ttl_sec": 30,  # THE PROBLEM: should be 5
                    "evictions_last_min": 0  # locks are not volatile-lfu eligible (persist flag)
                },
                "cart:*": {
                    "count": int(add_jitter(30000 + t * 2000)),
                    "memory_mb": int(add_jitter(4200 + t * 200)),
                    "avg_ttl_sec": int(add_jitter(1500)),
                    "evictions_last_min": int(add_jitter(evictions_per_min * 0.3))
                }
            },
            "slowlog_sample": _generate_slowlog(t, lock_keys)
        }
        metrics.append(entry)

    return metrics


def _generate_slowlog(t, lock_keys):
    """Generate representative SLOWLOG entries."""
    entries = []
    if t >= 2:
        entries.append({
            "id": 10000 + t * 10,
            "timestamp": int((SCENARIO_START + timedelta(minutes=t)).timestamp()),
            "duration_usec": int(add_jitter(15000 + lock_keys * 0.5)),
            "command": ["SET", f"lock:inv:sku-{random.randint(1000,9999)}", "1", "EX", "30", "NX"],
            "client": "checkout-svc:6379"
        })
    if t >= 3:
        entries.append({
            "id": 10000 + t * 10 + 1,
            "timestamp": int((SCENARIO_START + timedelta(minutes=t)).timestamp()),
            "duration_usec": int(add_jitter(8000)),
            "command": ["GET", f"cache:product:{random.randint(10000,99999)}"],
            "client": "checkout-svc:6379"
        })
    return entries


def generate_rds_metrics(timeline_minutes):
    """Generate Aurora Performance Insights-style metrics."""
    metrics = []

    for t in range(timeline_minutes + 1):
        ts = SCENARIO_START + timedelta(minutes=t)

        if t < 3:
            active_conns = 45 + t * 11
            checkout_p99 = 85 + t * 15
            wait_events = {"CPU:CPU": 0.4, "IO:DataFileRead": 0.3, "Client:ClientRead": 0.1}
            conn_errors = 0
        elif t < 5:
            progress = (t - 3) / 2
            active_conns = int(78 + progress * 122)
            checkout_p99 = int(130 + progress * 350)
            wait_events = {"Client:ClientRead": 0.6 + progress * 0.27, "CPU:CPU": 0.2, "IO:DataFileRead": 0.1}
            conn_errors = int(progress * 45)
        else:
            active_conns = min(200, int(add_jitter(198)))
            checkout_p99 = int(add_jitter(650 + (t - 5) * 85))
            wait_events = {"Client:ClientRead": 0.87, "CPU:CPU": 0.08, "IO:DataFileRead": 0.03}
            conn_errors = int(add_jitter(45 + (t - 5) * 10))

        entry = {
            "timestamp": ts.isoformat(),
            "elapsed_minutes": t,
            "performance_insights": {
                "db_load": {
                    "avg": add_jitter(active_conns * 0.15),
                    "max": add_jitter(active_conns * 0.25)
                },
                "wait_events": {k: add_jitter(v) for k, v in wait_events.items()},
                "top_sql": _generate_top_sql(t, checkout_p99)
            },
            "cloudwatch": {
                "DatabaseConnections": int(add_jitter(active_conns)),
                "MaxConnections": 200,
                "CPUUtilization": add_jitter(min(35 + t * 5, 72)),
                "FreeableMemory_GB": add_jitter(max(12 - t * 0.3, 8)),
                "ReadIOPS": int(add_jitter(5000 + t * 800)),
                "WriteIOPS": int(add_jitter(2000 + t * 200)),
                "ReadLatency_ms": add_jitter(1.2 + t * 0.3),
                "WriteLatency_ms": add_jitter(2.1 + t * 0.1),
                "NetworkReceiveThroughput_MBps": add_jitter(45 + t * 8),
                "ConnectionErrors": conn_errors
            },
            "transaction_latency": {
                "checkout": {
                    "p50_ms": int(add_jitter(checkout_p99 * 0.3)),
                    "p95_ms": int(add_jitter(checkout_p99 * 0.7)),
                    "p99_ms": int(add_jitter(checkout_p99)),
                    "sla_p99_ms": 200,
                    "sla_breached": checkout_p99 > 200
                },
                "product_browse": {
                    "p50_ms": int(add_jitter(12 + t * 3)),
                    "p95_ms": int(add_jitter(25 + t * 5)),
                    "p99_ms": int(add_jitter(35 + t * 8)),
                    "sla_p99_ms": 50,
                    "sla_breached": (35 + t * 8) > 50
                }
            }
        }
        metrics.append(entry)

    return metrics


def _generate_top_sql(t, checkout_p99):
    """Generate top SQL by wait time."""
    sqls = [
        {
            "sql_id": "sql_checkout_insert",
            "sql_text": "INSERT INTO orders (user_id, total, status) VALUES ($1, $2, 'pending')",
            "avg_latency_ms": int(checkout_p99 * 0.4),
            "calls_per_min": int(add_jitter(max(500 - t * 30, 80))),
            "wait_event": "Client:ClientRead" if t > 3 else "CPU:CPU"
        },
        {
            "sql_id": "sql_inventory_update",
            "sql_text": "UPDATE inventory SET reserved = reserved + 1 WHERE sku = $1 AND available > reserved",
            "avg_latency_ms": int(checkout_p99 * 0.6),
            "calls_per_min": int(add_jitter(max(500 - t * 30, 80))),
            "wait_event": "Client:ClientRead" if t > 3 else "LWLock:BufferMapping"
        },
        {
            "sql_id": "sql_product_select",
            "sql_text": "SELECT p.*, pi.url FROM products p JOIN product_images pi ON p.id = pi.product_id WHERE p.id = $1",
            "avg_latency_ms": int(add_jitter(15 + t * 8)),
            "calls_per_min": int(add_jitter(2000 + t * 500)),  # spikes from cache misses
            "wait_event": "IO:DataFileRead"
        }
    ]
    return sqls


def generate_msk_metrics(timeline_minutes):
    """Generate MSK consumer lag and throughput metrics."""
    metrics = []

    for t in range(timeline_minutes + 1):
        ts = SCENARIO_START + timedelta(minutes=t)

        # order.placed topic
        if t < 3:
            op_produce_rate = add_jitter(80 + t * 30)  # initially spikes with traffic
            op_lag_sec = add_jitter(2 + t * 1)
        elif t < 5:
            progress = (t - 3) / 2
            op_produce_rate = add_jitter(80 - progress * 65)  # collapses as checkout slows
            op_lag_sec = add_jitter(5 + progress * 50)
        else:
            op_produce_rate = add_jitter(max(8, 80 - (t - 3) * 15))
            op_lag_sec = add_jitter(min(55 + (t - 5) * 15, 90))

        # payment.proc topic
        if t < 4:
            pp_produce_rate = add_jitter(75 + t * 10)
            pp_lag_sec = add_jitter(1 + t * 2)
        elif t < 6:
            progress = (t - 4) / 2
            pp_produce_rate = add_jitter(75 - progress * 50)
            pp_lag_sec = add_jitter(8 + progress * 40)
        else:
            pp_produce_rate = add_jitter(max(12, 75 - (t - 4) * 12))
            pp_lag_sec = add_jitter(min(48 + (t - 6) * 11, 75))

        entry = {
            "timestamp": ts.isoformat(),
            "elapsed_minutes": t,
            "topics": {
                "order.placed": {
                    "produce_rate_per_sec": round(op_produce_rate),
                    "consume_rate_per_sec": round(max(op_produce_rate * 0.7, 5)),
                    "consumer_group": "reconciler-cg",
                    "lag_seconds": round(op_lag_sec),
                    "lag_sla_seconds": 30,
                    "sla_breached": op_lag_sec > 30,
                    "partitions": _generate_partition_lag(6, op_lag_sec),
                    "consumer_instances": 3,
                    "rebalance_events": []
                },
                "payment.proc": {
                    "produce_rate_per_sec": round(pp_produce_rate),
                    "consume_rate_per_sec": round(max(pp_produce_rate * 0.8, 8)),
                    "consumer_group": "notifier-cg",
                    "lag_seconds": round(pp_lag_sec),
                    "lag_sla_seconds": 60,
                    "sla_breached": pp_lag_sec > 60,
                    "partitions": _generate_partition_lag(3, pp_lag_sec),
                    "consumer_instances": 2,
                    "rebalance_events": []
                },
                "inv.reserved": {
                    "produce_rate_per_sec": round(add_jitter(op_produce_rate * 0.9)),
                    "consume_rate_per_sec": round(add_jitter(op_produce_rate * 0.6)),
                    "consumer_group": "analytics-cg",
                    "lag_seconds": round(add_jitter(op_lag_sec * 2.1)),
                    "lag_sla_seconds": 300,
                    "sla_breached": False,  # low priority, never breaches 300s
                    "partitions": _generate_partition_lag(6, op_lag_sec * 2),
                    "consumer_instances": 2,
                    "rebalance_events": []
                }
            },
            "broker_metrics": {
                "cpu_percent": add_jitter(25 + t * 2),
                "network_in_mbps": add_jitter(12 + t * 1.5),
                "network_out_mbps": add_jitter(18 + t * 2),
                "disk_used_percent": add_jitter(42)
            }
        }
        metrics.append(entry)

    return metrics


def _generate_partition_lag(num_partitions, avg_lag):
    """Generate uniform lag across partitions (no skew in this scenario)."""
    return {
        f"partition-{i}": {
            "lag_seconds": round(add_jitter(avg_lag, 0.15)),
            "offset_behind": int(add_jitter(avg_lag * 80, 0.1))
        }
        for i in range(num_partitions)
    }


def generate_red_herrings(timeline_minutes):
    """Generate unrelated metric spikes that agent should dismiss."""
    return [
        {
            "timestamp": (SCENARIO_START + timedelta(minutes=4)).isoformat(),
            "service": "notifier-svc",
            "metric": "CPUUtilization",
            "value": 78.3,
            "baseline": 25.0,
            "explanation": "Processing backlog of notification emails (consequence, not cause)",
            "agent_should_dismiss": True
        },
        {
            "timestamp": (SCENARIO_START + timedelta(minutes=5)).isoformat(),
            "service": "api-gateway",
            "metric": "5XXError_rate",
            "value": 0.003,
            "baseline": 0.0001,
            "explanation": "Timeout responses from checkout-svc (symptom of cascade)",
            "agent_should_dismiss": True
        },
        {
            "timestamp": (SCENARIO_START + timedelta(minutes=6)).isoformat(),
            "service": "analytics-consumer",
            "metric": "inv.reserved_lag_seconds",
            "value": 180,
            "baseline": 15,
            "explanation": "Low priority topic, SLA is 300s — within bounds",
            "agent_should_dismiss": True
        }
    ]


def main():
    parser = argparse.ArgumentParser(description="Generate mock telemetry for flash sale cascade demo")
    parser.add_argument("--output-dir", default="../mock-telemetry", help="Output directory")
    parser.add_argument("--timeline-minutes", type=int, default=12, help="Scenario duration")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)

    # Generate all telemetry
    redis_data = generate_redis_metrics(args.timeline_minutes)
    rds_data = generate_rds_metrics(args.timeline_minutes)
    msk_data = generate_msk_metrics(args.timeline_minutes)
    red_herrings = generate_red_herrings(args.timeline_minutes)

    # Write individual service files
    with open(os.path.join(args.output_dir, "redis_metrics.json"), "w") as f:
        json.dump(redis_data, f, indent=2, default=str)

    with open(os.path.join(args.output_dir, "rds_metrics.json"), "w") as f:
        json.dump(rds_data, f, indent=2, default=str)

    with open(os.path.join(args.output_dir, "msk_metrics.json"), "w") as f:
        json.dump(msk_data, f, indent=2, default=str)

    with open(os.path.join(args.output_dir, "red_herrings.json"), "w") as f:
        json.dump(red_herrings, f, indent=2, default=str)

    # Write combined timeline view
    combined = {
        "scenario": "QuickMart Flash Sale Cascade",
        "start_time": SCENARIO_START.isoformat(),
        "timeline": []
    }
    for t in range(args.timeline_minutes + 1):
        combined["timeline"].append({
            "elapsed_minutes": t,
            "timestamp": (SCENARIO_START + timedelta(minutes=t)).isoformat(),
            "redis": redis_data[t],
            "rds": rds_data[t],
            "msk": msk_data[t]
        })
    combined["red_herrings"] = red_herrings

    with open(os.path.join(args.output_dir, "combined_timeline.json"), "w") as f:
        json.dump(combined, f, indent=2, default=str)

    print(f"Generated telemetry for {args.timeline_minutes} minutes in {args.output_dir}/")
    print(f"  - redis_metrics.json ({len(redis_data)} data points)")
    print(f"  - rds_metrics.json ({len(rds_data)} data points)")
    print(f"  - msk_metrics.json ({len(msk_data)} data points)")
    print(f"  - red_herrings.json ({len(red_herrings)} entries)")
    print(f"  - combined_timeline.json (full timeline)")


if __name__ == "__main__":
    main()
