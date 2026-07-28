package br.com.obs.order_service.fault;

import java.util.Locale;
import java.util.Random;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

@Component
public class OrderFaultInjector {

    private static final Logger logger =
            LoggerFactory.getLogger(OrderFaultInjector.class);

    private static final Set<String> VALID_MODES = Set.of(
            "none",
            "latency",
            "error",
            "latency_error"
    );

    private final String mode;
    private final long delayMs;
    private final double rate;
    private final Random random;

    public OrderFaultInjector(
            @Value("${causalods.fault.mode:none}")
            String mode,

            @Value("${causalods.fault.delay-ms:1500}")
            long delayMs,

            @Value("${causalods.fault.rate:1.0}")
            double rate,

            @Value("${causalods.fault.seed:42}")
            long seed) {

        this.mode = normalizeMode(mode);
        this.delayMs = validateDelay(delayMs);
        this.rate = validateRate(rate);
        this.random = new Random(seed);

        logger.info(
                "CAUSALODS_ORDER_FAULT_CONFIG "
                        + "mode={} rate={} delayMs={} seed={}",
                this.mode,
                this.rate,
                this.delayMs,
                seed
        );
    }

    /**
     * Deve ser chamado no início do processamento do pedido,
     * antes da chamada ao payment-service.
     */
    public void beforeOrder() {

        if (!shouldInject()) {
            return;
        }

        logger.warn(
                "CAUSALODS_ORDER_FAULT_INJECTED "
                        + "mode={} rate={} delayMs={}",
                mode,
                rate,
                delayMs
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

    private synchronized boolean shouldInject() {

        if ("none".equals(mode)) {
            return false;
        }

        return random.nextDouble() < rate;
    }

    private void applyLatency() {

        logger.warn(
                "CAUSALODS_ORDER_LATENCY "
                        + "delayMs={}",
                delayMs
        );

        try {

            Thread.sleep(delayMs);

        } catch (InterruptedException exception) {

            Thread.currentThread().interrupt();

            throw new ResponseStatusException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "Injeção de latência interrompida "
                            + "no order-service.",
                    exception
            );
        }
    }

    private void applyError() {

        logger.warn(
                "CAUSALODS_ORDER_ERROR "
                        + "status={}",
                HttpStatus.SERVICE_UNAVAILABLE.value()
        );

        throw new ResponseStatusException(
                HttpStatus.SERVICE_UNAVAILABLE,
                "Falha injetada no order-service."
        );
    }

    private static String normalizeMode(
            String rawMode) {

        if (
                rawMode == null
                || rawMode.isBlank()
        ) {
            return "none";
        }

        String normalized = rawMode
                .trim()
                .toLowerCase(Locale.ROOT);

        if (!VALID_MODES.contains(normalized)) {

            throw new IllegalArgumentException(
                    "causalods.fault.mode inválido: "
                            + rawMode
                            + ". Valores permitidos: "
                            + VALID_MODES
            );
        }

        return normalized;
    }

    private static long validateDelay(
            long delayMs) {

        if (delayMs < 0) {

            throw new IllegalArgumentException(
                    "causalods.fault.delay-ms "
                            + "não pode ser negativo."
            );
        }

        return delayMs;
    }

    private static double validateRate(
            double rate) {

        if (
                Double.isNaN(rate)
                || rate < 0.0
                || rate > 1.0
        ) {

            throw new IllegalArgumentException(
                    "causalods.fault.rate deve "
                            + "estar entre 0.0 e 1.0."
            );
        }

        return rate;
    }
}