// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.

const test = require('node:test');
const assert = require('node:assert');
const { isAdult } = require('./calc.js');
const { discount } = require('./calc.js');
const { subtract } = require('./calc.js');


// Catches bug 1: calc.js: return age >= 18; -> return age > 18;
test('bug1: isAdult returns true at exactly 18', () => {
  assert.strictEqual(isAdult(18), true);
});

test('bug1: isAdult returns false just below 18', () => {
  assert.strictEqual(isAdult(17), false);
});


// Catches bug 2: calc.js: if (percent < 0 || percent > 100) { -> if (percent < 0 && percent > 100) {
test('bug2: discount throws for percent outside 0-100', () => {
  assert.throws(() => discount(100, -10), /percent must be between 0 and 100/);
  assert.throws(() => discount(100, 150), /percent must be between 0 and 100/);
});


// Catches bug 3: calc.js: return a - b; -> return Math.abs(a - b);
test('bug3: subtract returns negative when a < b', () => {
  assert.strictEqual(subtract(3, 5), -2);
});
