"""Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug."""

from calc import is_adult, subtract


# Catches bug 1: calc.py: return age >= 18 -> return age > 18
def test_bug1_18():
    assert is_adult(18) is True


# Catches bug 3: calc.py: return a - b -> return abs(a - b)
def test_bug3_neg():
    assert subtract(2, 5) == -3
