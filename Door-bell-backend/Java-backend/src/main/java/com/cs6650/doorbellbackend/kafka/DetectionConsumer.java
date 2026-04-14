package com.cs6650.doorbellbackend.kafka;

import com.cs6650.doorbellbackend.dto.DetectionEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class DetectionConsumer {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @KafkaListener(topics = "${app.kafka.topic.detections}", groupId = "doorbell-group")
    public void consume(String message) {
        try {
            DetectionEvent event = objectMapper.readValue(message, DetectionEvent.class);

            log.info("Received detection from camera={}, timestamp={}, persons={}",
                    event.getCameraId(),
                    event.getTimestamp(),
                    event.getDetections().size());

            for (DetectionEvent.PersonDetection person : event.getDetections()) {
                log.info("  trackId={}, confidence={:.2f}, bbox={}",
                        person.getTrackId(),
                        person.getConfidence(),
                        person.getBbox());

                // TODO: send embedding to ReIdService for cross-camera matching
                // TODO: send to BehaviorAnalysisService for anomaly detection
            }

        } catch (Exception e) {
            log.error("Failed to deserialize detection event: {}", e.getMessage());
        }
    }
}
