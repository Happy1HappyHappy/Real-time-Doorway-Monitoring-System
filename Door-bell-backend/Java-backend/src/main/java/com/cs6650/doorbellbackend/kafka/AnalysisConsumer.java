package com.cs6650.doorbellbackend.kafka;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class AnalysisConsumer {

    private final ObjectMapper objectMapper;
    private final SimpMessagingTemplate messagingTemplate;

    @KafkaListener(topics = "${app.kafka.topic.analysis-results}", groupId = "doorbell-analysis-group")
    public void consume(String message) {
        try {
            JsonNode node = objectMapper.readTree(message);
            String cameraId = node.path("cameraId").asText(null);
            Integer trackId = node.has("trackId") && !node.get("trackId").isNull()
                    ? node.get("trackId").asInt() : null;

            Map<String, Object> wsPayload = new HashMap<>();
            wsPayload.put("type", "analysis");
            wsPayload.put("cameraId", cameraId);
            wsPayload.put("trackId", trackId);
            wsPayload.put("description", node.path("description").asText(""));
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
        } catch (Exception e) {
            log.error("Failed to process analysis result: {}", e.getMessage(), e);
        }
    }
}
