object Calc {
    fun add(a: Int, b: Int): Int = a + b

    fun isAdult(age: Int): Boolean = age >= 18

    fun discount(price: Int, percent: Int): Int {
        val capped = if (percent > 100) 100 else percent
        return price - price * capped / 100
    }
}
