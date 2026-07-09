package payment_service.controller;

import payment_service.dto.PaymentRequest;
import payment_service.dto.PaymentResponse;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@RestController
@RequestMapping("/payments")
public class PaymentController {

    private static final Logger logger =
        LoggerFactory.getLogger(PaymentController.class);

    @PostMapping
    public PaymentResponse createPayment(
            @RequestBody PaymentRequest request) {

        logger.info("EVENT=PAYMENT_APPROVED orderId={} amount={}",
            request.getOrderId(),
            request.getAmount());

        return new PaymentResponse(
                System.currentTimeMillis(),
                "APPROVED");
    }

    @GetMapping("/{id}")
    public PaymentResponse getPayment(@PathVariable Long id) {

        return new PaymentResponse(
                id,
                "APPROVED");
    }
}