"""Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug."""

from calc import is_adult
from calc import discount
from calc import add, subtract, is_adult, discount
import pytest


# Catches: return age >= 18 -> return age > 18
def test_bug1_is_adult_boundary():
    assert is_adult(18) is True
    assert is_adult(17) is False


# Catches: if percent < 0 or percent > 100: -> if percent <= 0 or percent > 100:
def test_bug2_discount_zero_percent():
    assert discount(100, 0) == 100


# Catches: return price * (1 - percent / 100) -> return price * percent / 100
def test_bug3_discount_returns_final_price():
    assert discount(100, 20) == 80


def test_bug3_discount_zero_percent_returns_full_price():
    assert discount(100, 0) == 100


# Catches: return a - b -> return abs(a - b)
def test_bug4_subtract_negative_result():
    assert subtract(3, 5) == -2


# Catches: def add(a, b): -> def add(a, b=1):
def test_bug5_add_requires_two_args():
    with pytest.raises(TypeError):
        add(2)
