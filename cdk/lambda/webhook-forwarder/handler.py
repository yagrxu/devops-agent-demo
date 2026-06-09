"""Webhook Forwarder Lambda (Path A).

Receives SNS alarm notifications and forwards them as HMAC-signed incident
payloads to the AWS DevOps Agent generic webhook, which triggers an
autonomous investigation.

Signing format (verified live against the real endpoint):
  signature = base64( HMAC-SHA256(secret, "{timestamp}:{body}") )
  header  x-amzn-event-signature: <base64 signature>   (NO "sha256=" prefix)
  header  x-amzn-event-timestamp: <ISO-8601, current time at signing>
  body    json.dumps(payload, separators=(",", ":"))
The same timestamp string is used in BOTH the signed input and the header.
incidentId must be unique per request (the service deduplicates equal ids).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET_NAME = os.environ["SECRET_NAME"]
SERVICE_NAME = os.environ["SERVICE_NAME"]

_cached_secret: dict | None = None
http = urllib3.PoolManager()


def get_secret() -> dict:
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=SECRET_NAME)
    _cached_secret = json.loads(response["SecretString"])
    return _cached_secret


def validate_sns_message(message: dict) -> None:
    required = {
        "AlarmName": message.get("AlarmName"),
        "NewStateValue": message.get("NewStateValue"),
        "NewStateReason": message.get("NewStateReason"),
        "StateChangeTime": message.get("StateChangeTime"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


def build_webhook_payload(sns_message: dict) -> dict:
    alarm_name = sns_message["AlarmName"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    unique_suffix = time.time_ns()
    return {
        "eventType": "incident",
        "incidentId": f"{alarm_name}-{unique_suffix}",
        "action": "created",
        "priority": "HIGH",
        "title": alarm_name,
        "description": sns_message["NewStateReason"],
        "service": SERVICE_NAME,
        "timestamp": now,
    }


def compute_hmac_signature(body_str: str, secret: str, timestamp: str) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}:{body_str}".encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def forward_incident(payload: dict, webhook_url: str, hmac_secret: str) -> int:
    timestamp = payload["timestamp"]
    body_str = json.dumps(payload, separators=(",", ":"))
    signature = compute_hmac_signature(body_str, hmac_secret, timestamp)

    response = http.request(
        "POST",
        webhook_url,
        body=body_str.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-amzn-event-signature": signature,
            "x-amzn-event-timestamp": timestamp,
        },
    )
    status = response.status
    if status < 200 or status >= 300:
        logger.error(
            "Webhook POST failed: status=%d body=%s",
            status,
            response.data.decode("utf-8", errors="replace"),
        )
        raise RuntimeError(f"DevOps Agent webhook returned {status}")
    logger.info(
        "Webhook POST ok: status=%d incidentId=%s", status, payload["incidentId"]
    )
    return status


def lambda_handler(event: dict, context) -> dict:
    secret = get_secret()
    webhook_url = secret["url"]
    hmac_secret = secret["hmac_secret"]

    statuses = []
    for record in event.get("Records", []):
        sns_message = json.loads(record["Sns"]["Message"])
        validate_sns_message(sns_message)
        payload = build_webhook_payload(sns_message)
        statuses.append(forward_incident(payload, webhook_url, hmac_secret))

    return {"statusCode": 200, "forwarded": len(statuses)}
