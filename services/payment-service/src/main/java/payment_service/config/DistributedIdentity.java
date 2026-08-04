package payment_service.config;

import io.opentelemetry.api.baggage.Baggage;

import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;

public final class DistributedIdentity {

    public static final String HTTP_HEADER = "X-Correlation-Id";
    public static final String BAGGAGE_KEY = "business.correlation.id";
    public static final String MDC_KEY = "correlationId";
    public static final int MAX_LENGTH = 128;

    private static final Pattern SAFE_ID = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    );

    private DistributedIdentity() {}

    public static Optional<String> valid(String value) {
        if (value == null) {
            return Optional.empty();
        }
        String candidate = value.trim();
        String lower = candidate.toLowerCase();
        boolean sensitiveMarker = lower.contains("bearer")
                || lower.contains("token") || lower.contains("secret")
                || lower.contains("password") || lower.contains("passwd")
                || lower.contains("email") || lower.contains("phone")
                || lower.contains("cpf") || lower.contains("ssn");
        if (candidate.length() > MAX_LENGTH
                || !SAFE_ID.matcher(candidate).matches()
                || sensitiveMarker) {
            return Optional.empty();
        }
        return Optional.of(candidate);
    }

    public static String resolveOrGenerate(String headerValue) {
        return valid(headerValue)
                .or(() -> valid(Baggage.current().getEntryValue(BAGGAGE_KEY)))
                .orElseGet(() -> UUID.randomUUID().toString());
    }
}
