from __future__ import annotations

import time
from typing import Any

TONE_RANK = {"idle": 0, "ok": 1, "warn": 2, "bad": 3}


def metric(
    label: str, value: str, hint: str | None = None, tone: str | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {"label": label, "value": value}
    if hint is not None:
        item["hint"] = hint
    if tone is not None:
        item["tone"] = tone
    return item


def problem(text: str, tone: str) -> dict[str, str]:
    return {"text": text, "tone": tone}


def status_payload(
    tone: str,
    headline: str,
    metrics: list[dict[str, Any]],
    problems: list[dict[str, str]],
    now: float | None = None,
) -> dict[str, Any]:
    worst = max([tone, *(p["tone"] for p in problems)], key=TONE_RANK.__getitem__)
    return {
        "v": 1,
        "tone": worst,
        "headline": headline,
        "metrics": metrics,
        "problems": problems,
        "updated_at": time.time() if now is None else now,
    }


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{hours // 24} дн"
