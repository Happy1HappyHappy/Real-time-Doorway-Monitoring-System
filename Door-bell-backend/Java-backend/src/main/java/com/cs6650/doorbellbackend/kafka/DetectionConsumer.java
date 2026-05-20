/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Kafka consumer for doorbell-detections events that dispatches detection,
 * left, and position messages to the DetectionService for processing.
 */
package com.cs6650.doorbellbackend.kafka;

import com.cs6650.doorbellbackend.dto.DetectionEvent;
import com.cs6650.doorbellbackend.service.DetectionService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class DetectionConsumer {

    private final ObjectMapper objectMapper;
    private final DetectionService detectionService;
    private final DlqPublisher dlqPublisher;

    @KafkaListener(topics = "${app.kafka.topic.detections}", groupId = "doorbell-group")
    public void consume(ConsumerRecord<String, String> record) {
        String message = record.value();

        // Deserialization failures are never recoverable by retry — route to DLQ
        // so the consumer keeps making progress (avoids poison-pill stalling).
        DetectionEvent event;
        try {
            event = objectMapper.readValue(message, DetectionEvent.class);
        } catch (JsonProcessingException e) {
            log.error("Bad detection payload, sending to DLQ: {}", e.getMessage());
            dlqPublisher.send(record.topic(), record.key(), message, e);
            return;
        }

        // Business-logic failures (DB down, etc.) DO propagate — Spring Kafka's
        // default error handler will retry, then send to DLQ after attempts exhaust.
        // This is critical: we must NOT commit the offset on a DB outage.
        try {
            if ("left".equals(event.getType())) {
                log.info("Received left event from camera={}, trackIds={}",
                        event.getCameraId(), event.getLeftTrackIds());
                detectionService.processLeft(event);
            } else if ("position".equals(event.getType())) {
                detectionService.processPosition(event);
            } else {
                log.info("Received detection from camera={}, persons={}",
                        event.getCameraId(), event.getDetections().size());
                detectionService.processDetection(event);
            }
        } catch (Exception e) {
            log.error("Detection processing failed (will be retried by Spring Kafka): {}",
                    e.getMessage(), e);
            throw e;
        }
    }
}
