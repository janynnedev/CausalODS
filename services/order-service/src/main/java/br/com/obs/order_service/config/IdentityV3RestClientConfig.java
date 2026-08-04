package br.com.obs.order_service.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;
import org.springframework.web.client.RestClient;

@Configuration
@Profile("identity-v3")
public class IdentityV3RestClientConfig {
    @Bean
    @Primary
    RestClient.Builder identityV3RestClientBuilder() {
        return RestClient.builder().requestInterceptor(new CorrelationIdInterceptor());
    }
}
