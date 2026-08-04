package br.com.obs.order_service.filter;

import br.com.obs.order_service.config.DistributedIdentity;
import io.opentelemetry.api.baggage.Baggage;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.context.Scope;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
@Profile("identity-v3")
public class DistributedIdentityV3Filter extends OncePerRequestFilter {
    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {
        String correlationId = DistributedIdentity.resolveOrGenerate(
                request.getHeader(DistributedIdentity.HTTP_HEADER)
        );
        MDC.put(DistributedIdentity.MDC_KEY, correlationId);
        MDC.put(DistributedIdentity.BAGGAGE_KEY, correlationId);
        Span.current().setAttribute(DistributedIdentity.BAGGAGE_KEY, correlationId);
        response.setHeader(DistributedIdentity.HTTP_HEADER, correlationId);

        Baggage controlledBaggage = Baggage.builder()
                .put(DistributedIdentity.BAGGAGE_KEY, correlationId)
                .build();
        try (Scope ignored = controlledBaggage.makeCurrent()) {
            filterChain.doFilter(request, response);
        } finally {
            MDC.remove(DistributedIdentity.MDC_KEY);
            MDC.remove(DistributedIdentity.BAGGAGE_KEY);
        }
    }
}
