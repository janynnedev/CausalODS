package payment_service.controller;

import payment_service.dto.PaymentRequest;
import payment_service.dto.PaymentResponse;
import payment_service.fault.PaymentFaultInjector;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;


@RestController
@RequestMapping("/payments")
public class PaymentController {

    private static final Logger logger =
        LoggerFactory.getLogger(
            PaymentController.class
        );

    private final PaymentFaultInjector faultInjector;


    public PaymentController(
        PaymentFaultInjector faultInjector
    ) {

        this.faultInjector =
            faultInjector;
    }


    @PostMapping
    public PaymentResponse createPayment(
        @RequestBody PaymentRequest request
    ) {

        logger.warn(
            "PAYMENT_CONTROLLER_ENTERED orderId={}",
            request.getOrderId()
        );

        faultInjector.beforePayment();

        logger.info(
            "EVENT=PAYMENT_APPROVED orderId={} amount={}",
            request.getOrderId(),
            request.getAmount()
        );

        return new PaymentResponse(
            System.currentTimeMillis(),
            "APPROVED"
        );
    }


    @GetMapping("/{id}")
    public PaymentResponse getPayment(
        @PathVariable Long id
    ) {

        return new PaymentResponse(
            id,
            "APPROVED"
        );
    }
}