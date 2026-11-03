<?php

use PHPUnit\Framework\TestCase;

require_once __DIR__ . "/../calc.php";

final class CalcTest extends TestCase {
    public function testAdd(): void {
        $this->assertSame(3, add(1, 2));
    }

    public function testAdult(): void {
        $this->assertTrue(is_adult(30));
        $this->assertFalse(is_adult(5));
    }

    public function testDiscount(): void {
        $this->assertSame(180, discount(200, 10));
    }
}
