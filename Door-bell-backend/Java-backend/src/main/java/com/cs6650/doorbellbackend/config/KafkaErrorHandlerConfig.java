/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Configures the shared Spring Kafka error handler so consumers retry
 * transient failures with backoff and route exhausted records to a single DLQ topic.
 */
package com.cs6650.doorbellbackend.config;

import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.util.backoff.FixedBackOff;

@Slf4j
@Configuration
public class KafkaErrorHandlerConfig {

    @Value("${app.kafka.topic.dlq}")
    private String dlqTopic;

    /**
     * Retry transient failures (e.g., DB outage) up to 3 times with 2s backoff,
     * then send the failing record to a single shared DLQ topic. This prevents
     * one bad message from stalling the partition forever (poison pill) while
     * still giving short outages a chance to recover before data loss.
     */
    @Bean
    public DefaultErrorHandler kafkaErrorHandler(KafkaTemplate<String, String> kafkaTemplate) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, ex) -> {
                    log.error("[DLQ] retries exhausted for topic={} partition={} offset={} reason={}",
                            record.topic(), record.partition(), record.offset(), ex.getMessage());
                    return new TopicPartition(dlqTopic, -1);
                });

        // 3 retries, 2s apart — tuned for short blips (network, DB restart),
        // not long outages. Long outages should page someone, not pile up retries.
        FixedBackOff backOff = new FixedBackOff(2000L, 3L);
        return new DefaultErrorHandler(recoverer, backOff);
    }
}
