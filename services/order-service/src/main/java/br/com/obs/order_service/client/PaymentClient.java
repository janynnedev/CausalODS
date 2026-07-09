package br.com.obs.order_service.client;

import br.com.obs.order_service.client.dto.PaymentRequest;
import br.com.obs.order_service.client.dto.PaymentResponse;
import br.com.obs.order_service.config.RestClientConfig;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;


@Component
public class PaymentClient {

    private final RestClient restClient;

    public PaymentClient(
        RestClient.Builder builder,
        @Value("${payment.service.url}") String paymentUrl) {

        this.restClient = builder
            .baseUrl(paymentUrl)
            .build();
    }

    public PaymentResponse createPayment(
            PaymentRequest request) {

        return restClient.post()
                .uri("/payments")
                .body(request)
                .retrieve()
                .body(PaymentResponse.class);
    }
}