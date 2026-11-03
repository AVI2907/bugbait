package gocalc

import "testing"

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Errorf("Add(1, 2) = %d, want 3", Add(1, 2))
	}
}

func TestIsAdult(t *testing.T) {
	if !IsAdult(30) {
		t.Error("IsAdult(30) = false, want true")
	}
	if IsAdult(5) {
		t.Error("IsAdult(5) = true, want false")
	}
}

func TestDiscount(t *testing.T) {
	if got := Discount(200, 10); got != 180 {
		t.Errorf("Discount(200, 10) = %d, want 180", got)
	}
}
