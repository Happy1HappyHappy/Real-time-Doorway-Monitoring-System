/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Unit tests for AnalysisConsumer — verdicts are broadcast and saved with the
 * matched person, VLM errors are broadcast but not saved, and a failed save never dead-letters.
 */
package com.cs6650.doorbellbackend.kafka;

import com.cs6650.doorbellbackend.entity.AnalysisRecord;
import com.cs6650.doorbellbackend.repository.AnalysisRecordRepository;
import com.cs6650.doorbellbackend.service.DetectionService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.messaging.simp.SimpMessagingTemplate;

import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AnalysisConsumerTest {

    @Mock private SimpMessagingTemplate messagingTemplate;
    @Mock private DlqPublisher dlqPublisher;
    @Mock private AnalysisRecordRepository analysisRecordRepository;
    @Mock private DetectionService detectionService;

    private AnalysisConsumer consumer;

    private static final String VERDICT = """
            {"type": "analysis-result", "cameraId": "cam-01", "trackId": 7,
             "timestamp": "2026-10-03T16:15:02", "description": "person in a black mask peering in",
             "threatLevel": "watch", "suspicious": false, "reason": "face covering", "latencyMs": 1830}
            """;

    @BeforeEach
    void setUp() {
        consumer = new AnalysisConsumer(new ObjectMapper(), messagingTemplate, dlqPublisher,
                analysisRecordRepository, detectionService);
    }

    private static ConsumerRecord<String, String> record(String json) {
        return new ConsumerRecord<>("doorbell-analysis-results", 0, 0L, "cam-01:7", json);
    }

    @Test
    void verdict_isBroadcastAndSavedWithMatchedPerson() {
        when(detectionService.personIdForTrack("cam-01", 7)).thenReturn(Optional.of(42L));

        consumer.consume(record(VERDICT));

        verify(messagingTemplate).convertAndSend(eq("/topic/detections"), any(Object.class));
        ArgumentCaptor<AnalysisRecord> saved = ArgumentCaptor.forClass(AnalysisRecord.class);
        verify(analysisRecordRepository).save(saved.capture());
        AnalysisRecord r = saved.getValue();
        assertThat(r.getCameraId()).isEqualTo("cam-01");
        assertThat(r.getTrackId()).isEqualTo(7);
        assertThat(r.getPersonId()).isEqualTo(42L);
        assertThat(r.getThreatLevel()).isEqualTo("watch");
        assertThat(r.getDescription()).isEqualTo("person in a black mask peering in");
        assertThat(r.getLatencyMs()).isEqualTo(1830);
        assertThat(r.getAnalyzedAt()).isEqualTo(LocalDateTime.parse("2026-10-03T16:15:02"));
    }

    @Test
    void verdictBeforeReid_isSavedWithoutPerson() {
        when(detectionService.personIdForTrack("cam-01", 7)).thenReturn(Optional.empty());

        consumer.consume(record(VERDICT));

        ArgumentCaptor<AnalysisRecord> saved = ArgumentCaptor.forClass(AnalysisRecord.class);
        verify(analysisRecordRepository).save(saved.capture());
        assertThat(saved.getValue().getPersonId()).isNull();
    }

    @Test
    void vlmError_isBroadcastButNotSaved() {
        consumer.consume(record("""
                {"type": "analysis-result", "cameraId": "cam-01", "trackId": 7,
                 "timestamp": "2026-10-03T16:15:02", "error": "ReadTimeout: ollama", "latencyMs": 120000}
                """));

        verify(messagingTemplate).convertAndSend(eq("/topic/detections"), any(Object.class));
        verify(analysisRecordRepository, never()).save(any());
    }

    @Test
    void failedSave_doesNotDeadLetter() {
        when(detectionService.personIdForTrack("cam-01", 7)).thenReturn(Optional.empty());
        when(analysisRecordRepository.save(any())).thenThrow(new RuntimeException("db down"));

        consumer.consume(record(VERDICT));

        verify(messagingTemplate).convertAndSend(eq("/topic/detections"), any(Object.class));
        verifyNoInteractions(dlqPublisher);
    }
}
