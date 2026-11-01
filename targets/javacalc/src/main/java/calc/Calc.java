package calc;

public class Calc {
    public static int add(int a, int b) {
        return a + b;
    }

    public static int subtract(int a, int b) {
        return a - b;
    }

    public static boolean isAdult(int age) {
        return age >= 18;
    }

    public static double discount(double price, double percent) {
        if (percent < 0 || percent > 100) {
            throw new IllegalArgumentException("percent must be between 0 and 100");
        }
        return price * (1 - percent / 100);
    }
}
