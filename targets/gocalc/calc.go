// Package gocalc is BugBait's Go demo: a tiny calculator with weak boundary tests.
package gocalc

// Add returns the sum of a and b.
func Add(a, b int) int {
	return a + b
}

// IsAdult reports whether age counts as an adult (18 and over).
func IsAdult(age int) bool {
	return age >= 18
}

// Discount applies a percentage discount, capped at 100 percent.
func Discount(price, percent int) int {
	if percent > 100 {
		percent = 100
	}
	return price - price*percent/100
}
