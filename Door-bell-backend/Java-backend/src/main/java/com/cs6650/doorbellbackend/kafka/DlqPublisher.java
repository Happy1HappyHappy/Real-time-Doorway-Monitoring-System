/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Routes unprocessable Kafka messages to a single DLQ topic with
 * provenance headers, so poison messages are inspectable instead of silently dropped.
 */
package com.cs6650.doorbellbackend.kafka;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.internals.RecordHeader;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

@Slf4j
@Component
@RequiredArgsConstructor
public class DlqPublisher {

    private final KafkaTemplate<String, String> kafkaTemplate;

    @Value("${app.kafka.topic.dlq}")
    private String dlqTopic;

    /**
     * Send a failed message to the DLQ, preserving the original payload and tagging
     * it with the source topic and failure reason.
     *
     * Note: this is fire-and-forget — if the DLQ itself is unreachable, we log
     * and move on. The alternative (blocking the consumer) would be worse.
     */
    public void send(String sourceTopic, String key, String originalValue, Throwable cause) {
        try {
            ProducerRecord<String, String> record =
                    new ProducerRecord<>(dlqTopic, key, originalValue);
            record.headers().add(new RecordHeader(
                    "x-original-topic", sourceTopic.getBytes(StandardCharsets.UTF_8)));
            record.headers().add(new RecordHeader(
                    "x-failure-reason",
                    (cause.getClass().getSimpleName() + ": " + cause.getMessage())
                            .getBytes(StandardCharsets.UTF_8)));
            kafkaTemplate.send(record);
            log.warn("[DLQ] routed message from {} key={} reason={}",
                    sourceTopic, key, cause.getMessage());
        } catch (Exception e) {
            // DLQ failure is non-fatal — we already lost the original; logging is the
            // best we can do without blocking the consumer.
            log.error("[DLQ] failed to publish to {}: {}", dlqTopic, e.getMessage());
        }
    }
}
