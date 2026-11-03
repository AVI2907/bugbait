import test from "node:test";
import assert from "node:assert/strict";
import { add, isAdult, discount } from "./calc.ts";

test("add", () => {
  assert.equal(add(1, 2), 3);
});

test("isAdult", () => {
  assert.equal(isAdult(30), true);
  assert.equal(isAdult(5), false);
});

test("discount", () => {
  assert.equal(discount(200, 10), 180);
});
