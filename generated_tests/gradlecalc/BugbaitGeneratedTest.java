// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.
package calc;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class BugbaitGeneratedTest {
    // Catches bug 1: src/main/java/calc/Calc.java: return age >= 18; -> return age > 18;
    @Test
        void bug1_adultBoundaryAtEighteen() {
            assertTrue(Calc.isAdult(18));
            assertFalse(Calc.isAdult(17));
            assertTrue(Calc.isAdult(19));
        }
}
