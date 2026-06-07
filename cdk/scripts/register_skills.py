#!/usr/bin/env python3
"""
Register DevOps Agent Skills for Demo

Registers the 3 skills (Redis, RDS, MSK) with the DevOps Agent service
and creates an agent space with appropriate configurations.

Usage:
    python register_skills.py --profile cloudops-demo --region ap-southeast-1 --agent-space-id <ID>

    # To create a new agent space first:
    python register_skills.py --profile cloudops-demo --region ap-southeast-1 --create-space
"""

import argparse
import json
import sys

import boto3


REGION = 'ap-southeast-1'

# Skill configurations matching the skills/ directory definitions
SKILLS = {
    'redis-keyspace-business-map': {
        'title': 'Redis Keyspace Business Map',
        'description': 'Diagnose ElastiCache Redis issues per keyspace business purpose',
        'config': {
            'cluster_id': 'quickmart-demo-redis',
            'keyspaces': [
                {
                    'prefix': 'session:*',
                    'purpose': 'session',
                    'eviction_acceptable': False,
                    'ttl_expected_seconds': 3600,
                    'cold_start_cost': 'user logout, forced re-login',
                },
                {
                    'prefix': 'cache:product:*',
                    'purpose': 'cache',
                    'eviction_acceptable': True,
                    'ttl_expected_seconds': 300,
                    'cold_start_cost': '10x DB query load for product catalog',
                },
                {
                    'prefix': 'lock:inv:*',
                    'purpose': 'lock',
                    'eviction_acceptable': False,
                    'ttl_expected_seconds': 5,
                    'cold_start_cost': 'duplicate inventory reservations',
                },
                {
                    'prefix': 'cart:*',
                    'purpose': 'cache',
                    'eviction_acceptable': True,
                    'ttl_expected_seconds': 1800,
                    'cold_start_cost': 'user must re-add items to cart',
                },
            ],
            'forbidden_mitigations': [
                'never restart during 09:00-23:00 HKT',
                'never FLUSHALL or FLUSHDB',
            ],
            'recent_changes': [
                {
                    'date': '2026-05-28',
                    'change': 'lock:inv:* TTL changed from 5s to 30s (ticket QUICK-4421)',
                    'author': 'dev-team',
                },
            ],
        },
    },
    'rds-business-critical-slowdown': {
        'title': 'RDS Business-Critical Slowdown',
        'description': 'Diagnose RDS/Aurora slowdowns through business-critical transactions',
        'config': {
            'db_identifier': 'quickmart-demo-aurora',
            'critical_transactions': [
                {
                    'name': 'checkout',
                    'tables': ['orders', 'order_items', 'inventory'],
                    'sla_p99_ms': 200,
                    'business_window': '09:00-23:00 HKT daily',
                },
                {
                    'name': 'product_browse',
                    'tables': ['products', 'product_images'],
                    'sla_p99_ms': 50,
                    'business_window': 'always',
                },
            ],
            'forbidden_mitigations': [
                'no failover 09:00-23:00 HKT (business hours)',
                'no parameter group changes without DBA approval',
            ],
            'amplifiers': [
                'checkout-svc retries failed DB calls 3x with 100ms backoff',
                'product cache miss triggers synchronous DB query (no circuit breaker)',
            ],
            'recent_changes': [],
        },
    },
    'msk-business-topic-lag': {
        'title': 'MSK Business Topic Lag',
        'description': 'Diagnose MSK consumer lag through business-critical topic impact',
        'config': {
            'cluster_arn': 'arn:aws:kafka:ap-southeast-1:ACCOUNT:cluster/quickmart-demo-msk',
            'topics': [
                {
                    'name': 'order.placed',
                    'consumer_group': 'reconciler-cg',
                    'lag_sla_seconds': 30,
                    'downstream_business_impact': 'payment processing delayed',
                },
                {
                    'name': 'payment.proc',
                    'consumer_group': 'notifier-cg',
                    'lag_sla_seconds': 60,
                    'downstream_business_impact': 'order confirmation email delayed',
                },
            ],
            'rebalance_blackouts': [
                {
                    'window': '09:00-23:00 HKT',
                    'reason': 'rebalance during peak = 30s consumer pause',
                },
            ],
            'known_burst_patterns': [
                {
                    'pattern': 'flash sale: 5-10x produce rate for 2-4 hours',
                    'expected_lag_behavior': 'lag may spike to 10s, should recover within 2 min',
                },
            ],
            'recent_changes': [],
        },
    },
}


def get_client(profile: str, region: str):
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client('devops-agent')


def create_agent_space(client, name: str = 'quickmart-demo'):
    """Create a new agent space for the demo."""
    print(f"Creating agent space: {name}...")
    response = client.create_agent_space(
        name=name,
        description='QuickMart flash-sale cascade demo for HK Summit 2026',
        locale='en',
        tags={
            'Project': 'hk-summit-2026',
            'Purpose': 'demo',
            'Owner': 'yagrxu',
        },
    )
    space_id = response['agentSpace']['agentSpaceId']
    print(f"  Created: {space_id}")
    return space_id


def create_backlog_task_for_investigation(client, agent_space_id: str):
    """Create a backlog task that triggers investigation using skills."""
    print("Creating investigation task...")
    response = client.create_backlog_task(
        agentSpaceId=agent_space_id,
        taskType='INVESTIGATION',
        title='Flash Sale Cascade - Checkout SLA Breach',
        description=(
            'Checkout p99 latency has exceeded 200ms SLA for 3+ minutes. '
            'Multiple alarms firing: Redis evictions high, RDS connections maxed, '
            'MSK consumer lag on order.placed exceeding 30s. '
            'Investigate root cause across Redis (lock:inv:* keyspace), '
            'Aurora (connection pool), and MSK (producer/consumer health).'
        ),
        priority='CRITICAL',
    )
    task_id = response['task']['taskId']
    print(f"  Created task: {task_id}")
    print(f"  Status: {response['task']['status']}")
    return task_id


def register_aws_source(client, agent_space_id: str, account_id: str, role_arn: str):
    """Register AWS account as a source for the agent to query CloudWatch/etc."""
    print(f"Registering AWS source account {account_id}...")
    # First register the service
    # For AWS accounts, we use associate_service directly
    # The agent needs an AWS source association to query CloudWatch metrics
    try:
        response = client.associate_service(
            agentSpaceId=agent_space_id,
            serviceId='aws-source',  # This will need the actual registered service ID
            configuration={
                'sourceAws': {
                    'accountId': account_id,
                    'accountType': 'source',
                    'assumableRoleArn': role_arn,
                },
            },
        )
        print(f"  Associated AWS source: {response['association']['associationId']}")
        return response['association']['associationId']
    except Exception as e:
        print(f"  Note: AWS source association may need pre-registered service. Error: {e}")
        return None


def print_skill_configs():
    """Print skill configurations for manual registration via console/CLI."""
    print("\n" + "=" * 70)
    print("SKILL CONFIGURATIONS FOR REGISTRATION")
    print("=" * 70)
    print()
    print("Use these configurations when registering skills with DevOps Agent.")
    print("Skills can be registered via console, CLI, or API.")
    print()

    for skill_name, skill in SKILLS.items():
        print(f"\n{'─' * 70}")
        print(f"Skill: {skill['title']}")
        print(f"Name:  {skill_name}")
        print(f"Description: {skill['description']}")
        print(f"{'─' * 70}")
        print(f"Configuration JSON:")
        print(json.dumps(skill['config'], indent=2))
        print()


def main():
    parser = argparse.ArgumentParser(description='Register DevOps Agent Skills for Demo')
    parser.add_argument('--profile', default='cloudops-demo', help='AWS profile')
    parser.add_argument('--region', default=REGION, help='AWS region')
    parser.add_argument('--agent-space-id', default=None, help='Existing agent space ID')
    parser.add_argument('--create-space', action='store_true', help='Create new agent space')
    parser.add_argument('--create-task', action='store_true', help='Create investigation task')
    parser.add_argument('--print-configs', action='store_true', help='Print skill configs only')
    parser.add_argument('--account-id', default=None, help='AWS account ID for source registration')
    parser.add_argument('--role-arn', default=None, help='IAM role ARN for agent to assume')
    args = parser.parse_args()

    if args.print_configs:
        print_skill_configs()
        return

    client = get_client(args.profile, args.region)
    print(f"Using profile: {args.profile}, region: {args.region}")
    print()

    # Create or use existing agent space
    agent_space_id = args.agent_space_id
    if args.create_space:
        agent_space_id = create_agent_space(client)
    elif not agent_space_id:
        print("Error: provide --agent-space-id or --create-space")
        sys.exit(1)

    print(f"Using agent space: {agent_space_id}")
    print()

    # Register AWS source if account details provided
    if args.account_id and args.role_arn:
        register_aws_source(client, agent_space_id, args.account_id, args.role_arn)
        print()

    # Create investigation task if requested
    if args.create_task:
        create_backlog_task_for_investigation(client, agent_space_id)
        print()

    # Print skill configs for reference
    print_skill_configs()

    print("\n" + "=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    print("""
1. Register skills via DevOps Agent console or CLI using configs above
2. Associate AWS source account so agent can query CloudWatch
3. Run inject_cascade.py to trigger alarms
4. Observe DevOps Agent using skills during investigation
""")


if __name__ == '__main__':
    main()
