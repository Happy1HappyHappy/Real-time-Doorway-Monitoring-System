/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Spring configuration exposing a shared Jackson ObjectMapper bean.
 */
package com.cs6650.doorbellbackend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class JacksonConfig {

    @Bean
    public ObjectMapper objectMapper() {
        return new ObjectMapper();
    }
}
