from calc import add, subtract, is_adult, discount


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_is_adult():
    assert is_adult(30) is True
    assert is_adult(5) is False


def test_discount():
    assert discount(100, 50) == 50
