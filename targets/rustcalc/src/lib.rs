pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn is_adult(age: i32) -> bool {
    age >= 18
}

pub fn discount(price: i32, percent: i32) -> i32 {
    let percent = if percent > 100 { 100 } else { percent };
    price - price * percent / 100
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds() {
        assert_eq!(add(1, 2), 3);
    }

    #[test]
    fn adult() {
        assert!(is_adult(30));
        assert!(!is_adult(5));
    }

    #[test]
    fn disc() {
        assert_eq!(discount(200, 10), 180);
    }
}
