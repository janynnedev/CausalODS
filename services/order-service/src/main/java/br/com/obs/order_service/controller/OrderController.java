package br.com.obs.order_service.controller;

import br.com.obs.order_service.dto.CreateOrderRequest;
import br.com.obs.order_service.model.Order;
import br.com.obs.order_service.service.OrderService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PostMapping
    public Order createOrder(
            @RequestBody CreateOrderRequest request) {

        return orderService.createOrder(request);
    }
}