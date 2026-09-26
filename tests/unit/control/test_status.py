from __future__ import annotations

import time

import pytest

from tbot_console.control.status import duration, metric, problem, status_payload


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (-3, "0 с"),
        (12.4, "12 с"),
        (600, "10 мин"),
        (5400, "1 ч 30 мин"),
        (7200, "2 ч"),
        (3 * 86400, "3 дн"),
    ],
)
def test_duration(seconds, text):
    assert duration(seconds) == text


def test_a_metric_carries_only_what_was_given():
    assert metric("Биржа", "открыта") == {"label": "Биржа", "value": "открыта"}
    assert metric("Биржа", "закрыта", "часы торгов", "warn") == {
        "label": "Биржа",
        "value": "закрыта",
        "hint": "часы торгов",
        "tone": "warn",
    }


@pytest.mark.parametrize(
    ("tone", "problem_tones", "expected"),
    [
        ("ok", [], "ok"),
        ("idle", [], "idle"),
        ("ok", ["idle", "idle"], "ok"),
        ("idle", ["warn"], "warn"),
        ("ok", ["warn", "bad", "warn"], "bad"),
        ("bad", ["warn"], "bad"),
    ],
)
def test_the_tone_rises_to_the_worst_problem(tone, problem_tones, expected):
    problems = [problem(f"p{i}", t) for i, t in enumerate(problem_tones)]
    payload = status_payload(tone, "заголовок", [], problems, 5.0)
    assert payload == {
        "v": 1,
        "tone": expected,
        "headline": "заголовок",
        "metrics": [],
        "problems": problems,
        "updated_at": 5.0,
    }


def test_the_envelope_is_stamped_now_by_default():
    before = time.time()
    assert status_payload("ok", "h", [], [])["updated_at"] >= before
