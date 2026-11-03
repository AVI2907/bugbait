using CalcLib;

namespace CalcLib.Tests;

public class CalcTests
{
    [Fact]
    public void Add_Works() => Assert.Equal(3, Calc.Add(1, 2));

    [Fact]
    public void Adult_Works()
    {
        Assert.True(Calc.IsAdult(30));
        Assert.False(Calc.IsAdult(5));
    }

    [Fact]
    public void Discount_Works() => Assert.Equal(180, Calc.Discount(200, 10));
}
