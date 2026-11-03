require "minitest/autorun"
require_relative "../calc"

class TestCalc < Minitest::Test
  def test_add
    assert_equal 3, add(1, 2)
  end

  def test_adult
    assert adult?(30)
    refute adult?(5)
  end

  def test_discount
    assert_equal 180, discount(200, 10)
  end
end
