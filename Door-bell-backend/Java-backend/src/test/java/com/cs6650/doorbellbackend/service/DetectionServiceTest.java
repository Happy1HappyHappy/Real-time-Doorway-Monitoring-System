/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Unit tests for DetectionService covering Qdrant match/new-person branches,
 * the track→person cache used by position updates, and left-event cleanup.
 */
package com.cs6650.doorbellbackend.service;

import com.cs6650.doorbellbackend.dto.DetectionEvent;
import com.cs6650.doorbellbackend.entity.DetectionRecord;
import com.cs6650.doorbellbackend.entity.Person;
import com.cs6650.doorbellbackend.repository.DetectionRecordRepository;
import com.cs6650.doorbellbackend.repository.PersonRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.messaging.simp.SimpMessagingTemplate;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DetectionServiceTest {

    @Mock private QdrantService qdrantService;
    @Mock private PersonRepository personRepository;
    @Mock private DetectionRecordRepository detectionRecordRepository;
    @Mock private SimpMessagingTemplate messagingTemplate;

    @InjectMocks private DetectionService detectionService;

    private static final String CAM = "cam-01";
    private static final String TS = "2026-05-28T10:00:00";

    private DetectionEvent.PersonDetection detection(int trackId, List<Double> embedding) {
        DetectionEvent.PersonDetection d = new DetectionEvent.PersonDetection();
        d.setTrackId(trackId);
        d.setBbox(List.of(0.1, 0.2, 0.3, 0.4));
        d.setEmbedding(embedding);
        d.setConfidence(0.9);
        return d;
    }

    private DetectionEvent detectionEvent(DetectionEvent.PersonDetection... ds) {
        DetectionEvent e = new DetectionEvent();
        e.setType("detection");
        e.setCameraId(CAM);
        e.setTimestamp(TS);
        e.setDetections(List.of(ds));
        return e;
    }

    @Test
    void processDetection_qdrantMatch_updatesLastSeenAndDoesNotUpsert() {
        List<Double> emb = List.of(0.1, 0.2, 0.3);
        long matchedId = 42L;
        Person known = new Person(CAM, LocalDateTime.parse(TS));
        known.setId(matchedId);
        known.setNickname("Alice");

        when(qdrantService.searchSimilar(emb)).thenReturn(Optional.of(matchedId));
        when(personRepository.findById(matchedId)).thenReturn(Optional.of(known));
        when(personRepository.getReferenceById(matchedId)).thenReturn(known);

        detectionService.processDetection(detectionEvent(detection(7, emb)));

        ArgumentCaptor<Person> personCaptor = ArgumentCaptor.forClass(Person.class);
        verify(personRepository).save(personCaptor.capture());
        assertThat(personCaptor.getValue().getLastSeenAt()).isEqualTo(LocalDateTime.parse(TS));

        verify(qdrantService, never()).upsert(any(Long.class), any(), any(Long.class));
        verify(detectionRecordRepository).save(any(DetectionRecord.class));

        ArgumentCaptor<Object> payloadCaptor = ArgumentCaptor.forClass(Object.class);
        verify(messagingTemplate).convertAndSend(eq("/topic/detections"), payloadCaptor.capture());
        Map<?, ?> payload = (Map<?, ?>) payloadCaptor.getValue();
        assertThat(payload.get("type")).isEqualTo("detection");
        assertThat(payload.get("personId")).isEqualTo(matchedId);
        assertThat(payload.get("nickname")).isEqualTo("Alice");
        assertThat(payload.get("trackId")).isEqualTo(7);
    }

    @Test
    void processDetection_qdrantMiss_createsPersonAndUpserts() {
        List<Double> emb = List.of(0.5, 0.6, 0.7);
        when(qdrantService.searchSimilar(emb)).thenReturn(Optional.empty());

        Person saved = new Person(CAM, LocalDateTime.parse(TS));
        saved.setId(100L);
        when(personRepository.save(any(Person.class))).thenReturn(saved);
        when(personRepository.getReferenceById(100L)).thenReturn(saved);

        detectionService.processDetection(detectionEvent(detection(3, emb)));

        verify(qdrantService).upsert(eq(100L), eq(emb), eq(100L));
        verify(detectionRecordRepository).save(any(DetectionRecord.class));
    }

    @Test
    void processDetection_noEmbedding_skipsQdrant() {
        Person saved = new Person(CAM, LocalDateTime.parse(TS));
        saved.setId(200L);
        when(personRepository.save(any(Person.class))).thenReturn(saved);
        when(personRepository.getReferenceById(200L)).thenReturn(saved);

        detectionService.processDetection(detectionEvent(detection(5, null)));

        verify(qdrantService, never()).searchSimilar(any());
        verify(qdrantService, never()).upsert(any(Long.class), any(), any(Long.class));
        verify(personRepository).save(any(Person.class));
    }

    @Test
    void processDetection_bboxPersistedOnRecord() {
        when(qdrantService.searchSimilar(any())).thenReturn(Optional.empty());
        Person saved = new Person(CAM, LocalDateTime.parse(TS));
        saved.setId(1L);
        when(personRepository.save(any(Person.class))).thenReturn(saved);
        when(personRepository.getReferenceById(1L)).thenReturn(saved);

        DetectionEvent.PersonDetection d = detection(1, List.of(0.1));
        d.setBbox(List.of(0.11, 0.22, 0.33, 0.44));

        detectionService.processDetection(detectionEvent(d));

        ArgumentCaptor<DetectionRecord> recordCaptor = ArgumentCaptor.forClass(DetectionRecord.class);
        verify(detectionRecordRepository).save(recordCaptor.capture());
        DetectionRecord r = recordCaptor.getValue();
        assertThat(r.getBboxX()).isEqualTo(0.11);
        assertThat(r.getBboxY()).isEqualTo(0.22);
        assertThat(r.getBboxW()).isEqualTo(0.33);
        assertThat(r.getBboxH()).isEqualTo(0.44);
        assertThat(r.getTrackId()).isEqualTo(1);
        assertThat(r.getCameraId()).isEqualTo(CAM);
    }

    @Test
    void processLeft_broadcastsAndPurgesCache() {
        // Seed the cache via processDetection
        when(qdrantService.searchSimilar(any())).thenReturn(Optional.empty());
        Person saved = new Person(CAM, LocalDateTime.parse(TS));
        saved.setId(11L);
        when(personRepository.save(any(Person.class))).thenReturn(saved);
        when(personRepository.getReferenceById(11L)).thenReturn(saved);
        detectionService.processDetection(detectionEvent(detection(9, List.of(0.1))));

        DetectionEvent leftEvt = new DetectionEvent();
        leftEvt.setType("left");
        leftEvt.setCameraId(CAM);
        leftEvt.setLeftTrackIds(List.of(9));

        detectionService.processLeft(leftEvt);

        // After left, a position update for track 9 should produce nothing.
        DetectionEvent.TrackPosition tp = new DetectionEvent.TrackPosition();
        tp.setTrackId(9);
        tp.setBbox(List.of(0.5, 0.5, 0.1, 0.1));
        DetectionEvent posEvt = new DetectionEvent();
        posEvt.setCameraId(CAM);
        posEvt.setTracks(List.of(tp));

        // 1 detection broadcast + 1 left broadcast already; verify no extra messages for stale track
        detectionService.processPosition(posEvt);
        verify(messagingTemplate, times(2)).convertAndSend(anyString(), any(Object.class));
    }

    @Test
    void processPosition_broadcastsKnownTracksOnly() {
        when(qdrantService.searchSimilar(any())).thenReturn(Optional.empty());
        Person saved = new Person(CAM, LocalDateTime.parse(TS));
        saved.setId(55L);
        when(personRepository.save(any(Person.class))).thenReturn(saved);
        when(personRepository.getReferenceById(55L)).thenReturn(saved);
        detectionService.processDetection(detectionEvent(detection(2, List.of(0.1))));

        DetectionEvent.TrackPosition known = new DetectionEvent.TrackPosition();
        known.setTrackId(2);
        known.setBbox(List.of(0.5, 0.5, 0.1, 0.1));
        DetectionEvent.TrackPosition unknown = new DetectionEvent.TrackPosition();
        unknown.setTrackId(999);
        unknown.setBbox(List.of(0.1, 0.1, 0.1, 0.1));

        DetectionEvent posEvt = new DetectionEvent();
        posEvt.setCameraId(CAM);
        posEvt.setTracks(List.of(known, unknown));

        detectionService.processPosition(posEvt);

        ArgumentCaptor<Object> payloadCaptor = ArgumentCaptor.forClass(Object.class);
        verify(messagingTemplate, times(2)).convertAndSend(anyString(), payloadCaptor.capture());
        Map<?, ?> posPayload = (Map<?, ?>) payloadCaptor.getAllValues().get(1);
        assertThat(posPayload.get("type")).isEqualTo("position");
        List<?> tracks = (List<?>) posPayload.get("tracks");
        assertThat(tracks).hasSize(1);
        Map<?, ?> first = (Map<?, ?>) tracks.get(0);
        assertThat(first.get("personId")).isEqualTo(55L);
    }

    @Test
    void processPosition_unknownCamera_doesNothing() {
        DetectionEvent.TrackPosition tp = new DetectionEvent.TrackPosition();
        tp.setTrackId(1);
        tp.setBbox(List.of(0.5, 0.5, 0.1, 0.1));
        DetectionEvent evt = new DetectionEvent();
        evt.setCameraId("unknown-cam");
        evt.setTracks(List.of(tp));

        detectionService.processPosition(evt);

        verify(messagingTemplate, never()).convertAndSend(anyString(), any(Object.class));
    }
}
