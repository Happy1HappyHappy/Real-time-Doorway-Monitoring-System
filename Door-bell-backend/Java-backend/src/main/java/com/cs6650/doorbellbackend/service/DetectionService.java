/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Core detection service that performs re-identification via Qdrant, persists
 * Person and DetectionRecord rows in PostgreSQL, caches track-to-person mappings per camera,
 * keeps track of who is currently in frame (served by /api/live), and broadcasts
 * detection/left/position events to WebSocket clients.
 */
package com.cs6650.doorbellbackend.service;

import com.cs6650.doorbellbackend.dto.DetectionEvent;
import com.cs6650.doorbellbackend.entity.DetectionRecord;
import com.cs6650.doorbellbackend.entity.Person;
import com.cs6650.doorbellbackend.repository.DetectionRecordRepository;
import com.cs6650.doorbellbackend.repository.PersonRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Slf4j
@Service
@RequiredArgsConstructor
public class DetectionService {

    private final QdrantService qdrantService;
    private final PersonRepository personRepository;
    private final DetectionRecordRepository detectionRecordRepository;
    private final SimpMessagingTemplate messagingTemplate;

    // trackId → personId cache per camera
    private final ConcurrentHashMap<String, Map<Integer, Long>> trackPersonCache = new ConcurrentHashMap<>();

    // "cameraId:trackId" → identified person currently in frame, refreshed by position events
    private final ConcurrentHashMap<String, LiveTrack> liveTracks = new ConcurrentHashMap<>();

    // A track with no position update for this long counts as gone, e.g. its worker restarted
    // without sending "left" and the new process is reusing track IDs from 1.
    static final Duration LIVE_MAX_IDLE = Duration.ofSeconds(10);

    /** Times are naive UTC, the same clock the Python workers stamp events with. */
    public record LiveTrack(String cameraId, int trackId, long personId,
                            LocalDateTime firstSeenAt, LocalDateTime lastSeenAt) {
        LiveTrack seenAt(LocalDateTime when) {
            return new LiveTrack(cameraId, trackId, personId, firstSeenAt, when);
        }
    }

    private static String trackKey(String cameraId, int trackId) {
        return cameraId + ":" + trackId;
    }

    private static LocalDateTime nowUtc() {
        return LocalDateTime.now(ZoneOffset.UTC);
    }

    /** The person ReID matched to this track, while the track is still in frame. */
    public Optional<Long> personIdForTrack(String cameraId, int trackId) {
        LiveTrack live = liveTracks.get(trackKey(cameraId, trackId));
        if (live == null || live.lastSeenAt().isBefore(nowUtc().minus(LIVE_MAX_IDLE))) {
            return Optional.empty();
        }
        return Optional.of(live.personId());
    }

    /** Identified people currently in frame, ordered by camera and then arrival. */
    public List<LiveTrack> liveTracks() {
        return liveTracks(LIVE_MAX_IDLE);
    }

    List<LiveTrack> liveTracks(Duration maxIdle) {
        LocalDateTime cutoff = nowUtc().minus(maxIdle);
        liveTracks.values().removeIf(t -> t.lastSeenAt().isBefore(cutoff));
        return liveTracks.values().stream()
                .sorted(Comparator.comparing(LiveTrack::cameraId).thenComparing(LiveTrack::firstSeenAt))
                .toList();
    }

    public void processLeft(DetectionEvent event) {
        Map<Integer, Long> cache = trackPersonCache.get(event.getCameraId());
        if (cache != null && event.getLeftTrackIds() != null) {
            event.getLeftTrackIds().forEach(cache::remove);
        }
        if (event.getLeftTrackIds() != null) {
            event.getLeftTrackIds().forEach(id -> liveTracks.remove(trackKey(event.getCameraId(), id)));
        }
        Map<String, Object> wsPayload = Map.of(
                "type", "left",
                "cameraId", event.getCameraId(),
                "trackIds", event.getLeftTrackIds()
        );
        messagingTemplate.convertAndSend((String) "/topic/detections", (Object) wsPayload);
        log.info("[RE-ID] [{}] LEFT trackIds={}", event.getCameraId(), event.getLeftTrackIds());
    }

    public void processDetection(DetectionEvent event) {
        long pipelineStart = System.currentTimeMillis();
        String cameraId = event.getCameraId();
        LocalDateTime timestamp = LocalDateTime.parse(event.getTimestamp());

        // Measure cross-service latency (Python → Java)
        if (event.getKafkaProducedAt() != null) {
            long kafkaLatency = pipelineStart - event.getKafkaProducedAt();
            log.info("[LATENCY] [{}] kafka_transit={}ms", cameraId, kafkaLatency);
        }

        for (DetectionEvent.PersonDetection detection : event.getDetections()) {
            List<Double> embedding = detection.getEmbedding();

            Long personId;
            String nickname = null;
            if (embedding != null && !embedding.isEmpty()) {
                // Search Qdrant for matching person
                long qdrantStart = System.currentTimeMillis();
                Optional<Long> match = qdrantService.searchSimilar(embedding);
                long qdrantMs = System.currentTimeMillis() - qdrantStart;
                log.info("[LATENCY] [{}] qdrant_search={}ms", cameraId, qdrantMs);

                if (match.isPresent()) {
                    // Known person — update last seen
                    personId = match.get();
                    Person person = personRepository.findById(personId).orElse(null);
                    if (person != null) {
                        person.setLastSeenAt(timestamp);
                        personRepository.save(person);
                        // qdrantService.upsert(personId, embedding, personId); // Update embedding with latest
                        nickname = person.getNickname();

                    }
                    log.info("[RE-ID] [{}] MATCH person #{}", cameraId, personId);
                } else {
                    // New person — create and store embedding
                    Person person = new Person(cameraId, timestamp);
                    person = personRepository.save(person);
                    personId = person.getId();
                    qdrantService.upsert(personId, embedding, personId);
                    log.info("[RE-ID] [{}] NEW person #{}", cameraId, personId);
                }
            } else {
                // No embedding — create person without Qdrant
                Person person = new Person(cameraId, timestamp);
                person = personRepository.save(person);
                personId = person.getId();
                log.info("[RE-ID] [{}] NEW person #{} (no embedding)", cameraId, personId);
            }

            // Save detection record
            long dbStart = System.currentTimeMillis();
            DetectionRecord record = new DetectionRecord();
            record.setPerson(personRepository.getReferenceById(personId));
            record.setCameraId(cameraId);
            record.setTrackId(detection.getTrackId());
            record.setConfidence(detection.getConfidence());
            record.setDetectedAt(timestamp);

            List<Double> bbox = detection.getBbox();
            if (bbox != null && bbox.size() == 4) {
                record.setBboxX(bbox.get(0));
                record.setBboxY(bbox.get(1));
                record.setBboxW(bbox.get(2));
                record.setBboxH(bbox.get(3));
            }

            detectionRecordRepository.save(record);
            long dbMs = System.currentTimeMillis() - dbStart;
            log.info("[LATENCY] [{}] postgres_save={}ms", cameraId, dbMs);

            // Cache trackId → personId for position updates
            trackPersonCache.computeIfAbsent(cameraId, k -> new ConcurrentHashMap<>())
                    .put(detection.getTrackId(), personId);
            LocalDateTime now = nowUtc();
            liveTracks.put(trackKey(cameraId, detection.getTrackId()),
                    new LiveTrack(cameraId, detection.getTrackId(), personId, now, now));

            // Push to WebSocket
            long totalMs = System.currentTimeMillis() - pipelineStart;
            Map<String, Object> wsPayload = new HashMap<>();
            wsPayload.put("type", "detection");
            wsPayload.put("personId", personId);
            wsPayload.put("nickname", nickname);
            wsPayload.put("cameraId", cameraId);
            wsPayload.put("trackId", detection.getTrackId());
            wsPayload.put("confidence", detection.getConfidence());
            wsPayload.put("timestamp", event.getTimestamp());
            wsPayload.put("bbox", detection.getBbox());
            messagingTemplate.convertAndSend((String) "/topic/detections", (Object) wsPayload);

            log.info("[LATENCY] [{}] java_total={}ms (qdrant+db+ws)", cameraId, totalMs);
        }
    }

    public void processPosition(DetectionEvent event) {
        String cameraId = event.getCameraId();
        Map<Integer, Long> cache = trackPersonCache.getOrDefault(cameraId, Map.of());
        if (cache.isEmpty() || event.getTracks() == null) return;

        LocalDateTime now = nowUtc();
        List<Map<String, Object>> tracks = new ArrayList<>();
        for (DetectionEvent.TrackPosition t : event.getTracks()) {
            Long personId = cache.get(t.getTrackId());
            if (personId == null) continue;
            liveTracks.computeIfPresent(trackKey(cameraId, t.getTrackId()), (key, live) -> live.seenAt(now));
            Map<String, Object> entry = new HashMap<>();
            entry.put("personId", personId);
            entry.put("bbox", t.getBbox());
            tracks.add(entry);
        }

        if (!tracks.isEmpty()) {
            Map<String, Object> wsPayload = new HashMap<>();
            wsPayload.put("type", "position");
            wsPayload.put("cameraId", cameraId);
            wsPayload.put("tracks", tracks);
            messagingTemplate.convertAndSend((String) "/topic/detections", (Object) wsPayload);
        }
    }
}
