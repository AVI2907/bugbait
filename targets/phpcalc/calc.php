<?php

function add($a, $b) {
    return $a + $b;
}

function is_adult($age) {
    return $age >= 18;
}

function discount($price, $percent) {
    if ($percent > 100) {
        $percent = 100;
    }
    return $price - $price * $percent / 100;
}
