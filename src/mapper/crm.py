import hashlib
import json
import random
import time
import urllib.error
import urllib.request

RETRYABLE = {429, 500, 502, 503, 504}


def idempotency_key(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:32]


def http_sender(url):
    def send(record, key):
        req = urllib.request.Request(
            url,
            data=json.dumps(record).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, {}
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers.items())
    return send


def mock_sender(failure_rate=0.3):
    def send(record, key):
        if random.random() < failure_rate:
            return 429, {"Retry-After": "0"}
        return 201, {}
    return send


def push(records, send, max_retries=4, base_delay=0.5, sleep=time.sleep):
    sent, retried, dead = 0, 0, []
    for rec in records:
        key = idempotency_key(rec)
        for attempt in range(max_retries + 1):
            status, headers = send(rec, key)
            if 200 <= status < 300:
                sent += 1
                break
            if status in RETRYABLE and attempt < max_retries:
                retried += 1
                hdrs = {k.lower(): v for k, v in headers.items()}
                ra = hdrs.get("retry-after", "")
                delay = float(ra) if ra.replace(".", "", 1).isdigit() else base_delay * (2 ** attempt)
                sleep(min(delay, 30) + random.uniform(0, 0.2))
                continue
            dead.append({"record": rec, "status": status, "key": key})
            break
    return {"sent": sent, "retried": retried, "dead_letter": dead}
