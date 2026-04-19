package com.cs6650.doorbellbackend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class QdrantService {

    private final RestClient qdrantRestClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${qdrant.collection-name}")
    private String collectionName;

    @Value("${qdrant.similarity-threshold}")
    private double similarityThreshold;

    private static final int VECTOR_SIZE = 512;

    @PostConstruct
    public void ensureCollection() {
        try {
            qdrantRestClient.get()
                    .uri("/collections/{name}", collectionName)
                    .retrieve()
                    .body(String.class);
            log.info("Qdrant collection '{}' already exists", collectionName);
        } catch (Exception e) {
            log.info("Creating Qdrant collection '{}'", collectionName);
            Map<String, Object> body = Map.of(
                    "vectors", Map.of(
                            "size", VECTOR_SIZE,
                            "distance", "Cosine"
                    )
            );
            qdrantRestClient.put()
                    .uri("/collections/{name}", collectionName)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            log.info("Qdrant collection '{}' created", collectionName);
        }
    }

    /**
     * Search for the most similar embedding. Returns the personId if above threshold.
     */
    public Optional<Long> searchSimilar(List<Double> embedding) {
        Map<String, Object> body = Map.of(
                "vector", embedding,
                "limit", 1,
                "with_payload", true,
                "score_threshold", similarityThreshold
        );

        try {
            String response = qdrantRestClient.post()
                    .uri("/collections/{name}/points/search", collectionName)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .body(String.class);

            JsonNode result = objectMapper.readTree(response).get("result");
            if (result != null && !result.isEmpty()) {
                long personId = result.get(0).get("payload").get("personId").asLong();
                double score = result.get(0).get("score").asDouble();
                log.info("[RE-ID] Qdrant match: personId={} score={}", personId, String.format("%.4f", score));
                return Optional.of(personId);
            }
        } catch (Exception e) {
            log.error("Qdrant search failed: {}", e.getMessage());
        }
        return Optional.empty();
    }

    /**
     * Insert a new embedding with the associated personId.
     */
    public void upsert(long pointId, List<Double> embedding, long personId) {
        Map<String, Object> body = Map.of(
                "points", List.of(Map.of(
                        "id", pointId,
                        "vector", embedding,
                        "payload", Map.of("personId", personId)
                ))
        );

        try {
            qdrantRestClient.put()
                    .uri("/collections/{name}/points", collectionName)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            log.info("Qdrant upsert: pointId={} personId={}", pointId, personId);
        } catch (Exception e) {
            log.error("Qdrant upsert failed: {}", e.getMessage());
        }
    }
}
