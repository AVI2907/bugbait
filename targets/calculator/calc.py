def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def is_adult(age):
    return age >= 18


def discount(price, percent):
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price * (1 - percent / 100)
