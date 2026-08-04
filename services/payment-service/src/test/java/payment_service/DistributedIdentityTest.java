package payment_service;

import org.junit.jupiter.api.Test;
import payment_service.config.DistributedIdentity;
import payment_service.filter.DistributedIdentityV3Filter;
import io.opentelemetry.api.baggage.Baggage;
import io.opentelemetry.context.Scope;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.junit.jupiter.api.Assertions.*;

class DistributedIdentityTest {

    @Test
    void paymentAcceptsWorkflowPropagatedByOrderService() {
        assertEquals(
                "workflow-http-1",
                DistributedIdentity.resolveOrGenerate("workflow-http-1")
        );
    }

    @Test
    void paymentDoesNotAcceptOversizedOrPiiValues() {
        assertTrue(DistributedIdentity.valid("x".repeat(129)).isEmpty());
        assertTrue(DistributedIdentity.valid("person@example.com").isEmpty());
    }

    @Test
    void paymentV3FilterSanitizesIncomingBaggage() throws Exception {
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
