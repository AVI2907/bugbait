const test = require('node:test');
const assert = require('node:assert');
const { add, subtract, isAdult, discount } = require('./calc.js');

test('add', () => {
  assert.equal(add(2, 3), 5);
});

test('subtract', () => {
  assert.equal(subtract(5, 3), 2);
});

test('isAdult', () => {
  assert.equal(isAdult(30), true);
  assert.equal(isAdult(5), false);
});

test('discount', () => {
  assert.equal(discount(100, 50), 50);
});
