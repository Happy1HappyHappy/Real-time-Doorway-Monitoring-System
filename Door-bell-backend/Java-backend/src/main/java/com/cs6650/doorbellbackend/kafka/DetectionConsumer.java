package com.cs6650.doorbellbackend.kafka;

import com.cs6650.doorbellbackend.dto.DetectionEvent;
import com.cs6650.doorbellbackend.service.DetectionService;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class DetectionConsumer {

    private final ObjectMapper objectMapper;
    private final DetectionService detectionService;

    @KafkaListener(topics = "${app.kafka.topic.detections}", groupId = "doorbell-group")
    public void consume(String message) {
        try {
            DetectionEvent event = objectMapper.readValue(message, DetectionEvent.class);
            log.info("Received detection from camera={}, persons={}",
                    event.getCameraId(), event.getDetections().size());

            detectionService.processDetection(event);
        } catch (Exception e) {
            log.error("Failed to process detection event: {}", e.getMessage(), e);
        }
    }
}
