def add(a, b)
  a + b
end

def adult?(age)
  age >= 18
end

def discount(price, percent)
  percent = 100 if percent > 100
  price - price * percent / 100
end
