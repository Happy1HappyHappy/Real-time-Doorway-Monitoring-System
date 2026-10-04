/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Kafka consumer for VLM analysis-result messages that relays description,
 * threat level, and reasoning to connected WebSocket clients on /topic/detections, and keeps
 * each verdict in PostgreSQL so it can be looked up later (/api/analyses).
 */
package com.cs6650.doorbellbackend.kafka;

import com.cs6650.doorbellbackend.entity.AnalysisRecord;
import com.cs6650.doorbellbackend.repository.AnalysisRecordRepository;
import com.cs6650.doorbellbackend.service.DetectionService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;
import java.util.HashMap;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class AnalysisConsumer {

    private final ObjectMapper objectMapper;
    private final SimpMessagingTemplate messagingTemplate;
    private final DlqPublisher dlqPublisher;
    private final AnalysisRecordRepository analysisRecordRepository;
    private final DetectionService detectionService;

    @KafkaListener(topics = "${app.kafka.topic.analysis-results}", groupId = "doorbell-analysis-group")
    public void consume(ConsumerRecord<String, String> record) {
        String message = record.value();

        JsonNode node;
        try {
            node = objectMapper.readTree(message);
        } catch (JsonProcessingException e) {
            log.error("Bad analysis payload, sending to DLQ: {}", e.getMessage());
            dlqPublisher.send(record.topic(), record.key(), message, e);
            return;
        }

        try {
            String cameraId = node.path("cameraId").asText(null);
            Integer trackId = node.has("trackId") && !node.get("trackId").isNull()
                    ? node.get("trackId").asInt() : null;

            Map<String, Object> wsPayload = new HashMap<>();
            wsPayload.put("type", "analysis");
            wsPayload.put("cameraId", cameraId);
            wsPayload.put("trackId", trackId);
            wsPayload.put("description", node.path("description").asText(""));
            wsPayload.put("threatLevel", node.path("threatLevel").asText("safe"));
            wsPayload.put("suspicious", node.path("suspicious").asBoolean(false));
            wsPayload.put("reason", node.path("reason").asText(""));
            wsPayload.put("latencyMs", node.path("latencyMs").asInt(0));
            if (node.has("error")) {
                wsPayload.put("error", node.get("error").asText());
            }
            wsPayload.put("timestamp", node.path("timestamp").asText(""));

            messagingTemplate.convertAndSend((String) "/topic/detections", (Object) wsPayload);
            log.info("[VLM] [{}] track={} suspicious={} desc='{}'",
                    cameraId, trackId,
                    wsPayload.get("suspicious"),
                    ((String) wsPayload.get("description")));

            if (!node.has("error")) {
                saveVerdict(cameraId, trackId, node);
            }
        } catch (Exception e) {
            // Broadcast failure isn't recoverable on retry (payload is valid JSON,
            // the destination is the problem) — DLQ it and move on rather than stall.
            log.error("Failed to broadcast analysis result, sending to DLQ: {}", e.getMessage(), e);
            dlqPublisher.send(record.topic(), record.key(), message, e);
        }
    }

    private void saveVerdict(String cameraId, Integer trackId, JsonNode node) {
        try {
            AnalysisRecord record = new AnalysisRecord();
            record.setCameraId(cameraId);
            record.setTrackId(trackId);
            if (cameraId != null && trackId != null) {
                detectionService.personIdForTrack(cameraId, trackId).ifPresent(record::setPersonId);
            }
            record.setThreatLevel(node.path("threatLevel").asText("safe"));
            record.setDescription(node.path("description").asText(""));
            record.setReason(node.path("reason").asText(""));
            record.setLatencyMs(node.path("latencyMs").asInt(0));
            record.setAnalyzedAt(parseTimestamp(node.path("timestamp").asText("")));
            analysisRecordRepository.save(record);
        } catch (Exception e) {
            // The dashboard already has this verdict; losing the history row isn't worth a retry.
            log.warn("[VLM] [{}] could not save verdict for track={}: {}", cameraId, trackId, e.getMessage());
        }
    }

    private static LocalDateTime parseTimestamp(String text) {
        try {
            return LocalDateTime.parse(text);
        } catch (DateTimeParseException e) {
            return LocalDateTime.now(ZoneOffset.UTC);
        }
    }
}
