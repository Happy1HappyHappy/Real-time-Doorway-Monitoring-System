/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Read side of the activity API. Answers "who is in frame right now" from the live
 * track state, and looks up detections and VLM verdicts in a time window with nicknames filled in.
 * The MCP server uses this, but anything that wants history without the WebSocket can too.
 */
package com.cs6650.doorbellbackend.service;

import com.cs6650.doorbellbackend.dto.ActivityViews.AnalysisView;
import com.cs6650.doorbellbackend.dto.ActivityViews.DetectionView;
import com.cs6650.doorbellbackend.dto.ActivityViews.ListResult;
import com.cs6650.doorbellbackend.dto.ActivityViews.LiveTrackView;
import com.cs6650.doorbellbackend.entity.AnalysisRecord;
import com.cs6650.doorbellbackend.entity.DetectionRecord;
import com.cs6650.doorbellbackend.entity.Person;
import com.cs6650.doorbellbackend.repository.AnalysisRecordRepository;
import com.cs6650.doorbellbackend.repository.DetectionRecordRepository;
import com.cs6650.doorbellbackend.repository.PersonRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class ActivityService {

    /** Ordered from least to most severe, matching what vlm-worker emits. */
    public static final List<String> THREAT_LEVELS = List.of("safe", "watch", "alert");

    // vlm-worker re-checks every 5 s, so the latest verdict for someone in frame is always recent.
    private static final Duration LIVE_VERDICT_WINDOW = Duration.ofMinutes(2);
    // The first verdict can land a little before the detection that creates the live track.
    private static final Duration VERDICT_EARLY_SLACK = Duration.ofSeconds(30);

    private final DetectionService detectionService;
    private final DetectionRecordRepository detectionRecordRepository;
    private final AnalysisRecordRepository analysisRecordRepository;
    private final PersonRepository personRepository;

    public List<LiveTrackView> live() {
        List<DetectionService.LiveTrack> tracks = detectionService.liveTracks();
        if (tracks.isEmpty()) {
            return List.of();
        }

        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        List<AnalysisRecord> recentVerdicts = analysisRecordRepository
                .findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(now.minus(LIVE_VERDICT_WINDOW), now.plusMinutes(1));
        Map<Long, String> nicknames = nicknames(tracks.stream().map(DetectionService.LiveTrack::personId).toList());

        return tracks.stream().map(track -> {
            // Newest first, so the first hit for this camera + track is the latest verdict.
            AnalysisRecord verdict = recentVerdicts.stream()
                    .filter(v -> track.cameraId().equals(v.getCameraId())
                            && Integer.valueOf(track.trackId()).equals(v.getTrackId())
                            && !v.getAnalyzedAt().isBefore(track.firstSeenAt().minus(VERDICT_EARLY_SLACK)))
                    .findFirst()
                    .orElse(null);
            return new LiveTrackView(
                    track.cameraId(), track.trackId(), track.personId(), nicknames.get(track.personId()),
                    track.firstSeenAt(), track.lastSeenAt(),
                    verdict == null ? null : verdict.getThreatLevel(),
                    verdict == null ? null : verdict.getDescription(),
                    verdict == null ? null : verdict.getReason(),
                    verdict == null ? null : verdict.getAnalyzedAt());
        }).toList();
    }

    public ListResult<DetectionView> detections(LocalDateTime since, LocalDateTime until,
                                                String cameraId, Long personId, int limit) {
        List<DetectionRecord> matches = detectionRecordRepository
                .findByDetectedAtBetweenOrderByDetectedAtDesc(since, until).stream()
                .filter(d -> cameraId == null || cameraId.equals(d.getCameraId()))
                .filter(d -> personId == null || (d.getPerson() != null && personId.equals(d.getPerson().getId())))
                .toList();

        List<DetectionView> items = matches.stream().limit(limit).map(d -> {
            Person person = d.getPerson();
            return new DetectionView(
                    d.getId(),
                    person == null ? null : person.getId(),
                    person == null ? null : person.getNickname(),
                    d.getCameraId(), d.getTrackId(), d.getConfidence(), d.getDetectedAt());
        }).toList();
        return new ListResult<>(matches.size(), items);
    }

    public ListResult<AnalysisView> analyses(LocalDateTime since, LocalDateTime until, String cameraId,
                                             Long personId, String minLevel, int limit) {
        int minRank = THREAT_LEVELS.indexOf(minLevel);
        List<AnalysisRecord> matches = analysisRecordRepository
                .findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(since, until).stream()
                .filter(a -> THREAT_LEVELS.indexOf(a.getThreatLevel()) >= minRank)
                .filter(a -> cameraId == null || cameraId.equals(a.getCameraId()))
                .filter(a -> personId == null || personId.equals(a.getPersonId()))
                .toList();

        List<AnalysisRecord> page = matches.stream().limit(limit).toList();
        Map<Long, String> nicknames = nicknames(
                page.stream().map(AnalysisRecord::getPersonId).filter(Objects::nonNull).toList());
        List<AnalysisView> items = page.stream().map(a -> new AnalysisView(
                a.getId(), a.getCameraId(), a.getTrackId(), a.getPersonId(),
                a.getPersonId() == null ? null : nicknames.get(a.getPersonId()),
                a.getThreatLevel(), a.getDescription(), a.getReason(), a.getAnalyzedAt())).toList();
        return new ListResult<>(matches.size(), items);
    }

    private Map<Long, String> nicknames(Collection<Long> personIds) {
        Map<Long, String> names = new HashMap<>();
        if (personIds.isEmpty()) {
            return names;
        }
        for (Person p : personRepository.findAllById(new HashSet<>(personIds))) {
            if (p.getNickname() != null) {
                names.put(p.getId(), p.getNickname());
            }
        }
        return names;
    }
}
