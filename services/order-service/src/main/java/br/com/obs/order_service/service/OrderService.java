package br.com.obs.order_service.service;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import br.com.obs.order_service.client.PaymentClient;
import br.com.obs.order_service.client.dto.PaymentRequest;
import br.com.obs.order_service.client.dto.PaymentResponse;
import br.com.obs.order_service.dto.CreateOrderRequest;
import br.com.obs.order_service.fault.OrderFaultInjector;
import br.com.obs.order_service.logging.BusinessLogger;
import br.com.obs.order_service.model.Order;

@Service
public class OrderService {

    private static final Logger logger =
            LoggerFactory.getLogger(OrderService.class);

    private final PaymentClient paymentClient;

    private final OrderFaultInjector faultInjector;

    public OrderService(
            PaymentClient paymentClient,
            OrderFaultInjector faultInjector) {

        this.paymentClient = paymentClient;
        this.faultInjector = faultInjector;
    }

    public Order createOrder(
            CreateOrderRequest request) {

        logger.info(
                "Pedido recebido. Produto={}, Quantidade={}",
                request.getProduct(),
                request.getQuantity()
        );

        /*
         * A injeção precisa ocorrer antes da chamada ao
         * payment-service para que a causa raiz seja
         * atribuída ao order-service.
         *
         * mode=none:
         *     continua normalmente.
         *
         * mode=latency:
         *     aguarda o tempo configurado e continua.
         *
         * mode=error:
         *     lança uma exceção e não chama o pagamento.
         */
        faultInjector.beforeOrder();

        logger.info(
                "Chamando Payment Service"
        );

        PaymentRequest paymentRequest =
                new PaymentRequest(
                        System.currentTimeMillis(),
                        2500.0
                );

        PaymentResponse payment =
                paymentClient.createPayment(
                        paymentRequest
                );

        logger.info(
                "Pagamento aprovado. PaymentId={}, Status={}",
                payment.getPaymentId(),
                payment.getStatus()
        );

        Order order =
                new Order(
                        System.currentTimeMillis(),
                        request.getProduct(),
                        request.getQuantity(),
                        payment.getStatus()
                );

        BusinessLogger.log(
                "ORDER_CREATED",
                Map.of(
                        "orderId",
                        order.getId(),

                        "product",
                        order.getProduct(),

                        "quantity",
                        order.getQuantity(),

                        "status",
                        order.getStatus()
                )
        );

        return order;
    }
}