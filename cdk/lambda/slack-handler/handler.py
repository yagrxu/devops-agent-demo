"""Slack Handler Lambda (Path B, ack phase).

Receives Slack events from API Gateway, verifies authenticity, and
acknowledges within Slack's 3-second deadline. Fire-and-forget invokes
the Worker Lambda for the actual DevOps Agent call.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import parse_qs

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET_NAME = os.environ["SLACK_SECRET_NAME"]
WORKER_FUNCTION_NAME = os.environ["WORKER_FUNCTION_NAME"]
MAX_QUESTION_LENGTH = 4000
TIMESTAMP_MAX_AGE_SECONDS = 5 * 60
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"

_cached_secret: dict | None = None
_lambda_client = None
_http = urllib3.PoolManager()


def get_secret() -> dict:
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=SECRET_NAME)
    _cached_secret = json.loads(resp["SecretString"])
    return _cached_secret


def _lambda():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client


def verify_slack_signature(
    body: str, timestamp: str, signature: str, signing_secret: str
) -> bool:
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        logger.warning("Invalid Slack timestamp")
        return False
    if abs(time.time() - ts) > TIMESTAMP_MAX_AGE_SECONDS:
        logger.warning("Slack timestamp too old")
        return False

    basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def is_bot_event(event: dict) -> bool:
    return "bot_id" in event


def extract_user_text(event: dict) -> str:
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    return truncate(text)


def truncate(text: str, limit: int = MAX_QUESTION_LENGTH) -> str:
    return text[:limit] if len(text) > limit else text


def handle_url_verification(body: dict) -> dict:
    return _resp(200, json.dumps({"challenge": body["challenge"]}))


def _resp(status: int, body: str = "") -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    }


def post_usage_hint(channel: str, bot_token: str) -> None:
    _http.request(
        "POST",
        SLACK_POST_URL,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {bot_token}",
        },
        body=json.dumps(
            {
                "channel": channel,
                "text": "Ask me about your infrastructure, e.g. "
                "`@DevOps how many investigations are open?` "
                "or `/devops investigate checkout latency`.",
            }
        ).encode("utf-8"),
    )


def post_placeholder(channel: str, bot_token: str) -> str | None:
    resp = _http.request(
        "POST",
        SLACK_POST_URL,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {bot_token}",
        },
        body=json.dumps(
            {"channel": channel, "text": ":mag: Investigating..."}
        ).encode("utf-8"),
    )
    if resp.status != 200:
        logger.warning("Failed to post placeholder: status=%d", resp.status)
        return None
    data = json.loads(resp.data)
    if not data.get("ok"):
        logger.warning("Placeholder post returned ok=false: %s", data.get("error"))
        return None
    return data.get("ts")


def dispatch_worker(
    question: str, channel_id: str, user_id: str, message_ts: str | None = None
) -> None:
    payload = {
        "question": question,
        "channel_id": channel_id,
        "user_id": user_id,
    }
    if message_ts:
        payload["message_ts"] = message_ts
    _lambda().invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )


def _parse_body(raw_body: str, content_type: str) -> dict:
    if "application/json" in content_type:
        return json.loads(raw_body)
    if "application/x-www-form-urlencoded" in content_type:
        return {k: v[0] for k, v in parse_qs(raw_body).items()}
    try:
        return json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        return {k: v[0] for k, v in parse_qs(raw_body).items()}


def lambda_handler(event: dict, context) -> dict:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    raw_body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    secret = get_secret()
    signing_secret = secret["signing_secret"]
    bot_token = secret["bot_token"]

    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not verify_slack_signature(raw_body, timestamp, signature, signing_secret):
        logger.warning("Slack signature verification failed")
        return _resp(401, "Invalid signature")

    content_type = headers.get("content-type", "")
    body = _parse_body(raw_body, content_type)

    if body.get("type") == "url_verification":
        return handle_url_verification(body)

    if "command" in body:
        channel_id = body.get("channel_id", "")
        user_id = body.get("user_id", "")
        text = (body.get("text", "") or "").strip()
        if not text:
            post_usage_hint(channel_id, bot_token)
            return _resp(200)
        message_ts = post_placeholder(channel_id, bot_token)
        dispatch_worker(truncate(text), channel_id, user_id, message_ts)
        return _resp(200)

    if body.get("type") == "event_callback":
        inner = body.get("event", {})
        if is_bot_event(inner):
            return _resp(200)
        if inner.get("type") == "app_mention":
            channel_id = inner.get("channel", "")
            user_id = inner.get("user", "")
            text = extract_user_text(inner)
            if not text.strip():
                post_usage_hint(channel_id, bot_token)
                return _resp(200)
            message_ts = post_placeholder(channel_id, bot_token)
            dispatch_worker(text, channel_id, user_id, message_ts)
            return _resp(200)

    return _resp(200)
