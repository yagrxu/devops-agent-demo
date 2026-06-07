"""
QuickMart Checkout Service (Demo)

Simulates a real checkout flow:
1. Acquire inventory lock in Redis (SET lock:inv:<sku> EX <ttl> NX)
2. Read product from Redis cache (GET cache:product:<id>), fallback to DB
3. Insert order into Aurora PostgreSQL
4. Produce order.placed event to MSK
5. Release lock

Environment variables:
  REDIS_HOST        - ElastiCache endpoint
  REDIS_PORT        - default 6379
  REDIS_SSL         - "true" for TLS
  DB_HOST           - Aurora endpoint
  DB_PORT           - default 5432
  DB_NAME           - default "quickmart"
  DB_SECRET_ARN     - Secrets Manager ARN for credentials
  MSK_BOOTSTRAP     - MSK bootstrap servers
  LOCK_TTL_SECONDS  - Lock TTL (default 5; set to 30 to simulate the bug)
  AWS_REGION        - default ap-southeast-1
"""

import json
import os
import time
import uuid
import random
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request
import redis
import psycopg2
from psycopg2 import pool
import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration ---
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))
REDIS_SSL = os.environ.get('REDIS_SSL', 'true').lower() == 'true'
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = int(os.environ.get('DB_PORT', '5432'))
DB_NAME = os.environ.get('DB_NAME', 'quickmart')
DB_SECRET_ARN = os.environ.get('DB_SECRET_ARN', '')
MSK_BOOTSTRAP = os.environ.get('MSK_BOOTSTRAP', '')
LOCK_TTL_SECONDS = int(os.environ.get('LOCK_TTL_SECONDS', '5'))
AWS_REGION = os.environ.get('AWS_REGION', 'ap-southeast-1')

# --- Globals (lazy init) ---
redis_client = None
db_pool = None
kafka_producer = None


def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            ssl=REDIS_SSL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return redis_client


def get_db_credentials():
    if DB_SECRET_ARN:
        sm = boto3.client('secretsmanager', region_name=AWS_REGION)
        secret = sm.get_secret_value(SecretId=DB_SECRET_ARN)
        return json.loads(secret['SecretString'])
    return {
        'username': os.environ.get('DB_USER', 'demoadmin'),
        'password': os.environ.get('DB_PASSWORD', ''),
        'host': DB_HOST,
        'port': DB_PORT,
        'dbname': DB_NAME,
    }


def get_db_pool():
    global db_pool
    if db_pool is None:
        creds = get_db_credentials()
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            host=creds.get('host', DB_HOST),
            port=creds.get('port', DB_PORT),
            user=creds['username'],
            password=creds['password'],
            dbname=creds.get('dbname', DB_NAME),
        )
    return db_pool


def get_kafka_producer():
    global kafka_producer
    if kafka_producer is None and MSK_BOOTSTRAP:
        try:
            from confluent_kafka import Producer
            kafka_producer = Producer({
                'bootstrap.servers': MSK_BOOTSTRAP,
                'security.protocol': 'SASL_SSL',
                'sasl.mechanism': 'AWS_MSK_IAM',
                'client.id': 'checkout-svc',
            })
        except Exception as e:
            logger.warning(f"Kafka producer init failed: {e}")
    return kafka_producer


def init_db():
    """Create tables if they don't exist."""
    try:
        p = get_db_pool()
        conn = p.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS products (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        price DECIMAL(10,2) NOT NULL,
                        stock INT NOT NULL DEFAULT 100
                    );
                    CREATE TABLE IF NOT EXISTS orders (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        user_id VARCHAR(64) NOT NULL,
                        product_id INT REFERENCES products(id),
                        quantity INT NOT NULL DEFAULT 1,
                        total DECIMAL(10,2) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    INSERT INTO products (name, price, stock)
                    SELECT 'Product-' || i, (random() * 100 + 10)::decimal(10,2), 1000
                    FROM generate_series(1, 100) i
                    ON CONFLICT DO NOTHING;
                """)
            conn.commit()
            logger.info("Database initialized.")
        finally:
            p.putconn(conn)
    except Exception as e:
        logger.error(f"DB init failed: {e}")


# --- Routes ---

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': 'checkout-svc', 'timestamp': datetime.now(timezone.utc).isoformat()})


@app.route('/checkout', methods=['POST'])
def checkout():
    """
    Simulate checkout:
    1. Acquire Redis lock for inventory
    2. Check product cache (Redis), fallback to DB
    3. Insert order
    4. Produce Kafka event
    5. Release lock
    """
    start = time.time()
    user_id = request.json.get('user_id', f'user-{random.randint(1000,9999)}')
    product_id = request.json.get('product_id', random.randint(1, 100))
    quantity = request.json.get('quantity', 1)

    r = get_redis()
    lock_key = f"lock:inv:sku-{product_id}"
    order_id = str(uuid.uuid4())

    # Step 1: Acquire lock
    lock_acquired = r.set(lock_key, order_id, ex=LOCK_TTL_SECONDS, nx=True)
    if not lock_acquired:
        elapsed = (time.time() - start) * 1000
        return jsonify({'error': 'inventory_locked', 'elapsed_ms': elapsed}), 409

    try:
        # Step 2: Check product cache
        cache_key = f"cache:product:{product_id}"
        product_data = r.get(cache_key)

        if not product_data:
            # Cache miss — query DB
            p = get_db_pool()
            conn = p.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, price, stock FROM products WHERE id = %s", (product_id,))
                    row = cur.fetchone()
                    if row:
                        product_data = json.dumps({'id': row[0], 'name': row[1], 'price': float(row[2]), 'stock': row[3]})
                        r.set(cache_key, product_data, ex=300)
            finally:
                p.putconn(conn)

        if not product_data:
            return jsonify({'error': 'product_not_found'}), 404

        product = json.loads(product_data) if isinstance(product_data, str) else product_data
        total = product['price'] * quantity

        # Step 3: Insert order
        p = get_db_pool()
        conn = p.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO orders (id, user_id, product_id, quantity, total, status) VALUES (%s, %s, %s, %s, %s, 'pending')",
                    (order_id, user_id, product_id, quantity, total)
                )
                cur.execute(
                    "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
                    (quantity, product_id, quantity)
                )
            conn.commit()
        finally:
            p.putconn(conn)

        # Step 4: Produce Kafka event
        producer = get_kafka_producer()
        if producer:
            event = json.dumps({
                'order_id': order_id,
                'user_id': user_id,
                'product_id': product_id,
                'quantity': quantity,
                'total': float(total),
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })
            producer.produce('order.placed', key=order_id, value=event)
            producer.poll(0)

    finally:
        # Step 5: Release lock
        r.delete(lock_key)

    elapsed = (time.time() - start) * 1000
    return jsonify({
        'order_id': order_id,
        'status': 'pending',
        'total': float(total),
        'elapsed_ms': round(elapsed, 2),
    }), 201


@app.route('/simulate/flash-sale', methods=['POST'])
def simulate_flash_sale():
    """
    Trigger flash-sale load: rapidly create lock keys without releasing them.
    This simulates the bug where LOCK_TTL_SECONDS is too high under load.
    """
    duration_sec = request.json.get('duration_seconds', 60)
    rate_per_sec = request.json.get('rate_per_sec', 100)

    r = get_redis()
    count = 0
    end_time = time.time() + duration_sec

    while time.time() < end_time:
        batch_start = time.time()
        for _ in range(rate_per_sec):
            sku = random.randint(1, 10000)
            key = f"lock:inv:sku-{sku}"
            r.set(key, str(uuid.uuid4()), ex=LOCK_TTL_SECONDS, nx=True)
            count += 1
        elapsed = time.time() - batch_start
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

    return jsonify({
        'locks_created': count,
        'duration_seconds': duration_sec,
        'lock_ttl': LOCK_TTL_SECONDS,
        'message': f'Created {count} lock keys with TTL={LOCK_TTL_SECONDS}s'
    })


@app.route('/simulate/connection-storm', methods=['POST'])
def simulate_connection_storm():
    """Open many DB connections to simulate pool exhaustion."""
    target = request.json.get('connections', 30)
    hold_seconds = request.json.get('hold_seconds', 60)

    creds = get_db_credentials()
    connections = []

    try:
        for i in range(target):
            conn = psycopg2.connect(
                host=creds.get('host', DB_HOST),
                port=creds.get('port', DB_PORT),
                user=creds['username'],
                password=creds['password'],
                dbname=creds.get('dbname', DB_NAME),
            )
            conn.autocommit = True
            connections.append(conn)

        time.sleep(hold_seconds)
    finally:
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass

    return jsonify({
        'connections_opened': len(connections),
        'hold_seconds': hold_seconds,
    })


@app.route('/config', methods=['GET'])
def get_config():
    """Show current configuration (for demo visibility)."""
    return jsonify({
        'lock_ttl_seconds': LOCK_TTL_SECONDS,
        'redis_host': REDIS_HOST,
        'db_host': DB_HOST,
        'msk_bootstrap': MSK_BOOTSTRAP[:50] + '...' if MSK_BOOTSTRAP else 'not configured',
    })


@app.route('/config/lock-ttl', methods=['PUT'])
def set_lock_ttl():
    """Dynamically change lock TTL (simulate fix)."""
    global LOCK_TTL_SECONDS
    new_ttl = request.json.get('ttl_seconds', 5)
    old_ttl = LOCK_TTL_SECONDS
    LOCK_TTL_SECONDS = new_ttl
    return jsonify({'old_ttl': old_ttl, 'new_ttl': new_ttl})


# --- Startup ---
with app.app_context():
    try:
        init_db()
    except Exception as e:
        logger.warning(f"Startup DB init deferred: {e}")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
