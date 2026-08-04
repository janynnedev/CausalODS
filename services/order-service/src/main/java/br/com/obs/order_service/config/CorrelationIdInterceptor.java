package br.com.obs.order_service.config;

import org.slf4j.MDC;
import org.springframework.http.HttpRequest;
import org.springframework.http.client.ClientHttpRequestExecution;
import org.springframework.http.client.ClientHttpRequestInterceptor;
import org.springframework.http.client.ClientHttpResponse;

import java.io.IOException;

public class CorrelationIdInterceptor
        implements ClientHttpRequestInterceptor {

    @Override
    public ClientHttpResponse intercept(
            HttpRequest request,
            byte[] body,
            ClientHttpRequestExecution execution)
            throws IOException {

        String canonical = DistributedIdentity.valid(
                MDC.get(DistributedIdentity.MDC_KEY)
        ).orElse(null);
        String existing = request.getHeaders().getFirst(
                DistributedIdentity.HTTP_HEADER
        );

        if (canonical != null) {
            if (DistributedIdentity.valid(existing).filter(canonical::equals).isEmpty()) {
                request.getHeaders().set(DistributedIdentity.HTTP_HEADER, canonical);
            }
        } else if (DistributedIdentity.valid(existing).isEmpty()) {
            request.getHeaders().remove(DistributedIdentity.HTTP_HEADER);
        }

        return execution.execute(request, body);
    }
}
