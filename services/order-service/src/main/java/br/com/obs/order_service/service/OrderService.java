package br.com.obs.order_service.service;

import br.com.obs.order_service.client.PaymentClient;
import br.com.obs.order_service.dto.CreateOrderRequest;
import br.com.obs.order_service.logging.BusinessLogger;
import br.com.obs.order_service.model.Order;
import org.springframework.stereotype.Service;

import br.com.obs.order_service.client.dto.PaymentRequest;
import br.com.obs.order_service.client.dto.PaymentResponse;

import io.micrometer.observation.Observation;
import io.micrometer.observation.ObservationRegistry;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Service
public class OrderService {

    private final PaymentClient paymentClient;
    private final ObservationRegistry observationRegistry;

    private static final Logger logger =
            LoggerFactory.getLogger(OrderService.class);

    public OrderService(
            PaymentClient paymentClient,
            ObservationRegistry observationRegistry) {

        this.paymentClient = paymentClient;
        this.observationRegistry = observationRegistry;
    }

    public Order createOrder(CreateOrderRequest request) {

        logger.info(
                "Pedido recebido. Produto={}, Quantidade={}",
                request.getProduct(),
                request.getQuantity()
        );

        logger.info("Chamando Payment Service");

        PaymentRequest paymentRequest =
                new PaymentRequest(
                        System.currentTimeMillis(),
                        2500.0
                );

        PaymentResponse payment =
                paymentClient.createPayment(paymentRequest);

        logger.info(
                "Pagamento aprovado. PaymentId={}, Status={}",
                payment.getPaymentId(),
                payment.getStatus()
        );

        Order order = new Order(
                System.currentTimeMillis(),
                request.getProduct(),
                request.getQuantity(),
                payment.getStatus()
        );

        BusinessLogger.log(
                "ORDER_CREATED",
                Map.of(
                        "orderId", order.getId(),
                        "product", order.getProduct(),
                        "quantity", order.getQuantity(),
                        "status", order.getStatus()
                )
        );

        return order;         
    }
}