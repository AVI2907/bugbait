namespace CalcLib;

public static class Calc
{
    public static int Add(int a, int b) => a + b;
    public static bool IsAdult(int age) => age >= 18;
    public static int Discount(int price, int percent)
    {
        if (percent > 100) percent = 100;
        return price - price * percent / 100;
    }
}
