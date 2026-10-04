/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Unit tests for ActivityService — level/camera/person filtering, totals counted
 * before the limit, nickname lookup, and matching the latest VLM verdict to each live track.
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
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ActivityServiceTest {

    @Mock private DetectionService detectionService;
    @Mock private DetectionRecordRepository detectionRecordRepository;
    @Mock private AnalysisRecordRepository analysisRecordRepository;
    @Mock private PersonRepository personRepository;

    @InjectMocks private ActivityService activityService;

    private static final LocalDateTime SINCE = LocalDateTime.parse("2026-10-03T00:00:00");
    private static final LocalDateTime UNTIL = LocalDateTime.parse("2026-10-04T00:00:00");

    private static Person person(long id, String nickname) {
        Person p = new Person("cam-01", SINCE);
        p.setId(id);
        p.setNickname(nickname);
        return p;
    }

    private static AnalysisRecord verdict(long id, String cameraId, int trackId, Long personId,
                                          String level, LocalDateTime at) {
        AnalysisRecord a = new AnalysisRecord();
        a.setId(id);
        a.setCameraId(cameraId);
        a.setTrackId(trackId);
        a.setPersonId(personId);
        a.setThreatLevel(level);
        a.setDescription(level + " description");
        a.setReason(level + " reason");
        a.setAnalyzedAt(at);
        return a;
    }

    private static DetectionRecord detection(long id, String cameraId, int trackId, Person person, String at) {
        DetectionRecord d = new DetectionRecord();
        d.setId(id);
        d.setCameraId(cameraId);
        d.setTrackId(trackId);
        d.setPerson(person);
        d.setConfidence(0.9);
        d.setDetectedAt(LocalDateTime.parse(at));
        return d;
    }

    @Test
    void analyses_filtersByLevelAndCamera_totalCountsBeforeLimit() {
        when(analysisRecordRepository.findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(SINCE, UNTIL)).thenReturn(List.of(
                verdict(4, "cam-01", 7, 42L, "alert", LocalDateTime.parse("2026-10-03T10:04:00")),
                verdict(3, "cam-01", 7, 42L, "watch", LocalDateTime.parse("2026-10-03T10:03:00")),
                verdict(2, "cam-02", 9, null, "alert", LocalDateTime.parse("2026-10-03T10:02:00")),
                verdict(1, "cam-01", 7, 42L, "safe", LocalDateTime.parse("2026-10-03T10:01:00"))));
        when(personRepository.findAllById(any())).thenReturn(List.of(person(42, "Mail carrier")));

        ListResult<AnalysisView> result = activityService.analyses(SINCE, UNTIL, "cam-01", null, "watch", 1);

        assertThat(result.total()).isEqualTo(2);
        assertThat(result.items()).singleElement().satisfies(v -> {
            assertThat(v.id()).isEqualTo(4L);
            assertThat(v.threatLevel()).isEqualTo("alert");
            assertThat(v.nickname()).isEqualTo("Mail carrier");
        });
    }

    @Test
    void analyses_personFilterSkipsUnidentifiedVerdicts() {
        when(analysisRecordRepository.findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(SINCE, UNTIL)).thenReturn(List.of(
                verdict(2, "cam-02", 9, null, "alert", LocalDateTime.parse("2026-10-03T10:02:00")),
                verdict(1, "cam-01", 7, 42L, "safe", LocalDateTime.parse("2026-10-03T10:01:00"))));
        when(personRepository.findAllById(any())).thenReturn(List.of(person(42, null)));

        ListResult<AnalysisView> result = activityService.analyses(SINCE, UNTIL, null, 42L, "safe", 100);

        assertThat(result.items()).extracting(AnalysisView::id).containsExactly(1L);
        assertThat(result.items().get(0).nickname()).isNull();
    }

    @Test
    void detections_filtersByPersonAndCarriesNickname() {
        Person alice = person(1, "Alice");
        Person bob = person(2, null);
        when(detectionRecordRepository.findByDetectedAtBetweenOrderByDetectedAtDesc(SINCE, UNTIL)).thenReturn(List.of(
                detection(12, "cam-02", 4, alice, "2026-10-03T18:00:00"),
                detection(11, "cam-01", 3, bob, "2026-10-03T17:00:00"),
                detection(10, "cam-01", 1, alice, "2026-10-03T09:00:00")));

        ListResult<DetectionView> result = activityService.detections(SINCE, UNTIL, null, 1L, 100);

        assertThat(result.total()).isEqualTo(2);
        assertThat(result.items()).extracting(DetectionView::id).containsExactly(12L, 10L);
        assertThat(result.items()).allSatisfy(v -> assertThat(v.nickname()).isEqualTo("Alice"));
    }

    @Test
    void live_attachesLatestVerdictForTheSameTrack() {
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        when(detectionService.liveTracks()).thenReturn(List.of(
                new DetectionService.LiveTrack("cam-01", 7, 42L, now.minusSeconds(20), now)));
        when(analysisRecordRepository.findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(any(), any())).thenReturn(List.of(
                verdict(3, "cam-01", 8, 43L, "alert", now.minusSeconds(1)),
                verdict(2, "cam-01", 7, 42L, "watch", now.minusSeconds(3)),
                verdict(1, "cam-01", 7, 42L, "safe", now.minusSeconds(9))));
        when(personRepository.findAllById(any())).thenReturn(List.of(person(42, "Mail carrier")));

        List<LiveTrackView> live = activityService.live();

        assertThat(live).singleElement().satisfies(v -> {
            assertThat(v.personId()).isEqualTo(42L);
            assertThat(v.nickname()).isEqualTo("Mail carrier");
            assertThat(v.threatLevel()).isEqualTo("watch");
        });
    }

    @Test
    void live_ignoresVerdictFromBeforeTheTrackStarted() {
        // Track IDs restart when a worker restarts, so an old "track 7" verdict must not stick to a new person.
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        when(detectionService.liveTracks()).thenReturn(List.of(
                new DetectionService.LiveTrack("cam-01", 7, 42L, now.minusSeconds(5), now)));
        when(analysisRecordRepository.findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(any(), any())).thenReturn(List.of(
                verdict(1, "cam-01", 7, 41L, "alert", now.minusSeconds(90))));
        when(personRepository.findAllById(any())).thenReturn(List.of(person(42, null)));

        List<LiveTrackView> live = activityService.live();

        assertThat(live).singleElement().satisfies(v -> assertThat(v.threatLevel()).isNull());
    }

    @Test
    void live_nobodyInFrame_skipsQueries() {
        when(detectionService.liveTracks()).thenReturn(List.of());

        assertThat(activityService.live()).isEmpty();
        verifyNoInteractions(analysisRecordRepository, personRepository);
    }
}
