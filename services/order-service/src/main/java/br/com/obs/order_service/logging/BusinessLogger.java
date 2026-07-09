package br.com.obs.order_service.logging;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

import java.util.Map;

public class BusinessLogger {

    private static final Logger logger = LoggerFactory.getLogger("BUSINESS");

    public static void log(String event, Map<String, Object> fields) {

        MDC.put("event", event);

        fields.forEach((k, v) -> {
            if (v != null) {
                MDC.put(k, String.valueOf(v));
            }
        });

        logger.info(event);

        MDC.remove("event");

        fields.keySet().forEach(MDC::remove);
    }
}