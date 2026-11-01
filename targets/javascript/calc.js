function add(a, b) {
  return a + b;
}

function subtract(a, b) {
  return a - b;
}

function isAdult(age) {
  return age >= 18;
}

function discount(price, percent) {
  if (percent < 0 || percent > 100) {
    throw new Error("percent must be between 0 and 100");
  }
  return price * (1 - percent / 100);
}

module.exports = { add, subtract, isAdult, discount };
