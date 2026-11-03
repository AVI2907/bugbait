export function add(a: number, b: number): number {
  return a + b;
}

export function isAdult(age: number): boolean {
  return age >= 18;
}

export function discount(price: number, percent: number): number {
  if (percent > 100) percent = 100;
  return price - (price * percent) / 100;
}
