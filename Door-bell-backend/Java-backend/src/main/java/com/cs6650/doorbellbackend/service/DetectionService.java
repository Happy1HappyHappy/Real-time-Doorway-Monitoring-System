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

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class DetectionService {

    private final QdrantService qdrantService;
    private final PersonRepository personRepository;
    private final DetectionRecordRepository detectionRecordRepository;
    private final SimpMessagingTemplate messagingTemplate;

    public void processDetection(DetectionEvent event) {
        String cameraId = event.getCameraId();
        LocalDateTime timestamp = LocalDateTime.parse(event.getTimestamp());

        for (DetectionEvent.PersonDetection detection : event.getDetections()) {
            List<Double> embedding = detection.getEmbedding();

            Long personId;
            if (embedding != null && !embedding.isEmpty()) {
                // Search Qdrant for matching person
                Optional<Long> match = qdrantService.searchSimilar(embedding);

                if (match.isPresent()) {
                    // Known person — update last seen
                    personId = match.get();
                    Person person = personRepository.findById(personId).orElse(null);
                    if (person != null) {
                        person.setLastSeenAt(timestamp);
                        personRepository.save(person);
                    }
                    log.info("[{}] Matched existing person #{}", cameraId, personId);
                } else {
                    // New person — create and store embedding
                    Person person = new Person(cameraId, timestamp);
                    person = personRepository.save(person);
                    personId = person.getId();
                    qdrantService.upsert(personId, embedding, personId);
                    log.info("[{}] New person #{}", cameraId, personId);
                }
            } else {
                // No embedding — create person without Qdrant
                Person person = new Person(cameraId, timestamp);
                person = personRepository.save(person);
                personId = person.getId();
                log.info("[{}] New person #{} (no embedding)", cameraId, personId);
            }

            // Save detection record
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

            // Push to WebSocket
            messagingTemplate.convertAndSend("/topic/detections", Map.of(
                    "personId", personId,
                    "cameraId", cameraId,
                    "trackId", detection.getTrackId(),
                    "confidence", detection.getConfidence(),
                    "timestamp", event.getTimestamp()
            ));
        }
    }
}
