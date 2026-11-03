import org.junit.Assert.*
import org.junit.Test

class CalcTest {
    @Test
    fun addsNumbers() {
        assertEquals(5, Calc.add(2, 3))
    }

    @Test
    fun adultsOnly() {
        assertTrue(Calc.isAdult(30))
        assertFalse(Calc.isAdult(5))
    }

    @Test
    fun discountCaps() {
        assertEquals(180, Calc.discount(200, 10))
    }
}
