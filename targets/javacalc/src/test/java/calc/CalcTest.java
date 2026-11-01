package calc;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class CalcTest {
    @Test
    void adds() {
        assertEquals(5, Calc.add(2, 3));
    }

    @Test
    void subtracts() {
        assertEquals(2, Calc.subtract(5, 3));
    }

    @Test
    void adult() {
        assertTrue(Calc.isAdult(30));
        assertFalse(Calc.isAdult(5));
    }

    @Test
    void discounts() {
        assertEquals(50, Calc.discount(100, 50));
    }
}
