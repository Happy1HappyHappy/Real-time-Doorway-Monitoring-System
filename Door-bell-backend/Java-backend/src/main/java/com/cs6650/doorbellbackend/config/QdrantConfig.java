package com.cs6650.doorbellbackend.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

@Configuration
public class QdrantConfig {

    @Value("${qdrant.host}")
    private String host;

    @Value("${qdrant.port}")
    private int port;

    @Bean
    public RestClient qdrantRestClient() {
        return RestClient.builder()
                .baseUrl("http://" + host + ":" + port)
                .build();
    }
}
