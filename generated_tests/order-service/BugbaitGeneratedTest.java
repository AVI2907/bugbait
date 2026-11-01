// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.
package com.orderflow.order.order;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.assertThat;

public class BugbaitGeneratedTest {
    // Catches bug 1: src/main/java/com/orderflow/order/order/Order.java: .min(Comparator.comparingInt(Enum::ordinal)) -> .max(Comparator.comparingInt(Enum::ordinal))
    @Test
        void bug1_mixedNonCancelledPackagesReflectEarliestProgress() {
            Order order = new Order();
            SellerOrder paid = new SellerOrder();
            paid.setStatus(OrderStatus.PAID);
            paid.setOrder(order);
            SellerOrder shipped = new SellerOrder();
            shipped.setStatus(OrderStatus.SHIPPED);
            shipped.setOrder(order);
            order.getSellerOrders().add(paid);
            order.getSellerOrders().add(shipped);
            assertThat(order.computeOverallStatus()).isEqualTo(OrderStatus.PAID);
        }
}
