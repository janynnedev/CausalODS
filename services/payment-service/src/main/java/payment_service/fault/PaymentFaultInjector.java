package payment_service.fault;

import java.util.Locale;
import java.util.Random;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

@Component
public class PaymentFaultInjector {

    private static final Logger logger =
    LoggerFactory.getLogger(
        PaymentFaultInjector.class
    );

    private final String mode;
    private final long delayMs;
    private final double rate;
    private final Random random;

    public PaymentFaultInjector(
        @Value("${causalods.fault.mode:none}")
        String mode,

        @Value("${causalods.fault.delay-ms:1500}")
        long delayMs,

        @Value("${causalods.fault.rate:1.0}")
        double rate,

        @Value("${causalods.fault.seed:42}")
        long seed
    ) {

        this.mode = normalizeMode(mode);
        this.delayMs = validateDelay(delayMs);
        this.rate = validateRate(rate);
        this.random = new Random(seed);

        logger.info(
            "CAUSALODS_FAULT_CONFIG mode={} rate={} delayMs={} seed={}",
            this.mode,
            this.rate,
            this.delayMs,
            seed
        );
    }

    public void beforePayment() {

        logger.warn(
            "CAUSALODS_FAULT_CHECK mode={} rate={}",
            mode,
            rate
        );

        if (!shouldInject()) {
            logger.warn(
                "CAUSALODS_FAULT_SKIPPED mode={} rate={}",
                mode,
                rate
            );

            return;
        }

        logger.warn(
            "CAUSALODS_FAULT_INJECTED mode={}",
            mode
        );

        switch (mode) {

            case "latency" ->
                applyLatency();

            case "error" ->
                applyError();

            case "latency_error" -> {
                applyLatency();
                applyError();
            }

            case "none" -> {
                // Nenhuma falha.
            }

            default ->
                throw new IllegalStateException(
                    "Modo de falha não suportado: "
                    + mode
                );
        }
    }

    public String getMode() {
        return mode;
    }

    private boolean shouldInject() {

        return !"none".equals(mode)
            && random.nextDouble() < rate;
    }

    private void applyLatency() {

        try {

            Thread.sleep(delayMs);

        } catch (InterruptedException exception) {

            Thread.currentThread().interrupt();

            throw new ResponseStatusException(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "Injeção de latência interrompida.",
                exception
            );
        }
    }

    private void applyError() {

        throw new ResponseStatusException(
            HttpStatus.SERVICE_UNAVAILABLE,
            "Falha controlada no payment-service."
        );
    }

    private static String normalizeMode(
        String mode
    ) {

        if (mode == null) {
            return "none";
        }

        return mode
            .trim()
            .toLowerCase(Locale.ROOT);
    }

    private static long validateDelay(
        long delayMs
    ) {

        if (delayMs < 0) {

            throw new IllegalArgumentException(
                "causalods.fault.delay-ms "
                + "não pode ser negativo."
            );
        }

        return delayMs;
    }

    private static double validateRate(
        double rate
    ) {

        if (rate < 0.0 || rate > 1.0) {

            throw new IllegalArgumentException(
                "causalods.fault.rate deve estar "
                + "entre 0.0 e 1.0."
            );
        }

        return rate;
    }
}