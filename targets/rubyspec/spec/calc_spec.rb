require_relative "../calc"

RSpec.describe "calc" do
  it "adds" do
    expect(add(1, 2)).to eq(3)
  end

  it "checks adulthood" do
    expect(adult?(30)).to be true
    expect(adult?(5)).to be false
  end

  it "discounts" do
    expect(discount(200, 10)).to eq(180)
  end
end
