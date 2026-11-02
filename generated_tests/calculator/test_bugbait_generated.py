"""Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug."""

import pytest
from calc import add, subtract, is_adult, discount


# Catches bug 1: calc.py: def add(a, b): -> def add(a, b=0):
def test_bug1_add_requires_two_arguments():
    with pytest.raises(TypeError):
        add(2)


# Catches bug 2: calc.py: return a - b -> return abs(a - b)
def test_bug2_subtract_negative_result():
    assert subtract(3, 5) == -2


# Catches bug 3: calc.py: return age >= 18 -> return age > 18
def test_bug3_is_adult_boundary():
    assert is_adult(18) is True
    assert is_adult(17) is False


# Catches bug 4: calc.py: if percent < 0 or percent > 100: -> if percent < 0 and percent > 100:
def test_bug4_discount_rejects_negative_percent():
    with pytest.raises(ValueError):
        discount(100, -10)


def test_bug4_discount_rejects_percent_over_100():
    with pytest.raises(ValueError):
        discount(100, 150)


# Catches bug 5: calc.py: return price * (1 - percent / 100) -> return price * (percent / 100)
def test_bug5_discount_twenty_percent():
    assert discount(100, 20) == 80


def test_bug5_discount_zero_and_full():
    assert discount(100, 0) == 100
    assert discount(100, 100) == 0
