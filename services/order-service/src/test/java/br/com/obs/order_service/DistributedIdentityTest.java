package br.com.obs.order_service;

import br.com.obs.order_service.config.CorrelationIdInterceptor;
import br.com.obs.order_service.config.DistributedIdentity;
import br.com.obs.order_service.filter.DistributedIdentityV3Filter;
import io.opentelemetry.api.baggage.Baggage;
import io.opentelemetry.context.Scope;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.http.HttpMethod;
import org.springframework.mock.http.client.MockClientHttpRequest;
import org.springframework.mock.http.client.MockClientHttpResponse;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.net.URI;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;

class DistributedIdentityTest {

    @AfterEach
    void clearMdc() {
        MDC.clear();
    }

    @Test
    void acceptsReceivedCorrelationIdAndGeneratesWhenAbsent() {
        assertEquals(
                "workflow-42",
                DistributedIdentity.resolveOrGenerate("workflow-42")
        );
        assertTrue(
                DistributedIdentity.valid(
                        DistributedIdentity.resolveOrGenerate(null)
                ).isPresent()
        );
    }

    @Test
    void rejectsOversizedAndSensitiveLookingValues() {
        assertTrue(DistributedIdentity.valid("x".repeat(129)).isEmpty());
        assertTrue(DistributedIdentity.valid("person@example.com").isEmpty());
        assertTrue(DistributedIdentity.valid("Bearer secret token").isEmpty());
    }

    @Test
    void propagatesExplicitWorkflowWithoutOverwritingW3cHeaders() throws Exception {
        MDC.put(DistributedIdentity.MDC_KEY, "workflow-http-1");
        MockClientHttpRequest request = new MockClientHttpRequest(
                HttpMethod.POST, URI.create("http://payment-service/payments")
        );
        request.getHeaders().add("traceparent", "00-" + "a".repeat(32) + "-" + "b".repeat(16) + "-01");
        request.getHeaders().add("tracestate", "vendor=value");

        new CorrelationIdInterceptor().intercept(
                request,
                new byte[0],
                (outgoing, body) -> new MockClientHttpResponse(
                        "{}".getBytes(StandardCharsets.UTF_8), 200
                )
        );

        assertEquals("workflow-http-1", request.getHeaders().getFirst("X-Correlation-Id"));
        assertNotNull(request.getHeaders().getFirst("traceparent"));
        assertEquals("vendor=value", request.getHeaders().getFirst("tracestate"));
        assertNull(request.getHeaders().getFirst("authorization"));
        assertNull(request.getHeaders().getFirst("cookie"));
    }

    @Test
    void interceptorReplacesInvalidPreexistingCorrelationWithCanonical() throws Exception {
        MDC.put(DistributedIdentity.MDC_KEY, "workflow-canonical");
        MockClientHttpRequest request = new MockClientHttpRequest(
                HttpMethod.POST, URI.create("http://payment-service/payments")
        );
        request.getHeaders().add("X-Correlation-Id", "x".repeat(129));
        new CorrelationIdInterceptor().intercept(request, new byte[0],
                (outgoing, body) -> new MockClientHttpResponse(new byte[0], 200));
        assertEquals("workflow-canonical",
                request.getHeaders().getFirst("X-Correlation-Id"));
    }

    @Test
    void v3FilterDropsEveryIncomingBaggageEntryExceptCanonicalIdentity() throws Exception {
        Baggage incoming = Baggage.builder()
                .put("business.correlation.id", "workflow-incoming")
                .put("tenant", "acme").put("token", "secret")
                .put("email", "person@example.com")
                .put("authorization", "Bearer value")
                .put("arbitrary", "value").build();
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Correlation-Id", "workflow-canonical");
        MockHttpServletResponse response = new MockHttpServletResponse();

        try (Scope ignored = incoming.makeCurrent()) {
            new ExposedV3Filter().run(request, response, (req, res) -> {
                assertEquals("workflow-canonical", Baggage.current()
                        .getEntryValue("business.correlation.id"));
                for (String forbidden : new String[]{
                        "tenant", "token", "email", "authorization", "arbitrary"}) {
                    assertNull(Baggage.current().getEntryValue(forbidden));
                }
            });
        }
    }

    private static final class ExposedV3Filter extends DistributedIdentityV3Filter {
        void run(MockHttpServletRequest request, MockHttpServletResponse response,
                 jakarta.servlet.FilterChain chain) throws Exception {
            doFilterInternal(request, response, chain);
        }
    }
}
