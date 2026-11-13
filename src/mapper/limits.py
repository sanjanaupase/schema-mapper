import datetime
import json
import os
import threading

_LOCK = threading.Lock()
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
PATH = os.environ.get("QUOTA_FILE", os.path.join(_ROOT, "quota.json"))


class QuotaExceeded(RuntimeError):
    pass


def _limit():
    return int(os.environ.get("DAILY_CALL_LIMIT", "100"))


def _read():
    try:
        with open(PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _today():
    return datetime.date.today().isoformat()


def check_and_count():
    limit = _limit()
    if limit <= 0:
        return
    with _LOCK:
        count = _read().get(_today(), 0)
        if count >= limit:
            raise QuotaExceeded(f"Daily limit of {limit} model calls reached. Try again tomorrow.")
        with open(PATH, "w") as f:
            json.dump({_today(): count + 1}, f)


def remaining():
    limit = _limit()
    if limit <= 0:
        return None
    return max(0, limit - _read().get(_today(), 0))
