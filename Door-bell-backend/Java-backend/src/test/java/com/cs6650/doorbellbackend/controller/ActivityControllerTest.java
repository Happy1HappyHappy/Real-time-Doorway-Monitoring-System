/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: MockMvc tests for ActivityController — query parameter parsing, the default
 * 24-hour window, limit clamping, and 400s for bad levels or an inverted window.
 */
package com.cs6650.doorbellbackend.controller;

import com.cs6650.doorbellbackend.dto.ActivityViews.DetectionView;
import com.cs6650.doorbellbackend.dto.ActivityViews.ListResult;
import com.cs6650.doorbellbackend.dto.ActivityViews.LiveTrackView;
import com.cs6650.doorbellbackend.service.ActivityService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class ActivityControllerTest {

    @Mock private ActivityService activityService;
    @InjectMocks private ActivityController controller;

    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @Test
    void detections_passesWindowAndFiltersThrough() throws Exception {
        LocalDateTime since = LocalDateTime.parse("2026-10-03T08:00:00");
        LocalDateTime until = LocalDateTime.parse("2026-10-03T12:30:00");
        DetectionView row = new DetectionView(5L, 42L, "Mail carrier", "cam-01", 7, 0.91,
                LocalDateTime.parse("2026-10-03T09:15:02.123456"));
        when(activityService.detections(since, until, "cam-01", 42L, 20))
                .thenReturn(new ListResult<>(1, List.of(row)));

        mockMvc.perform(get("/api/detections")
                        .param("since", "2026-10-03T08:00:00")
                        .param("until", "2026-10-03T12:30:00")
                        .param("cameraId", "cam-01")
                        .param("personId", "42")
                        .param("limit", "20"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].personId").value(42))
                .andExpect(jsonPath("$.items[0].nickname").value("Mail carrier"))
                .andExpect(jsonPath("$.items[0].detectedAt").value("2026-10-03T09:15:02.123456"));
    }

    @Test
    void detections_defaultsToLast24Hours() throws Exception {
        mockMvc.perform(get("/api/detections")).andExpect(status().isOk());

        ArgumentCaptor<LocalDateTime> since = ArgumentCaptor.forClass(LocalDateTime.class);
        ArgumentCaptor<LocalDateTime> until = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(activityService).detections(since.capture(), until.capture(), isNull(), isNull(), eq(100));
        assertThat(Duration.between(since.getValue(), until.getValue())).isEqualTo(Duration.ofHours(24));
        assertThat(until.getValue()).isCloseTo(LocalDateTime.now(ZoneOffset.UTC), within(1, ChronoUnit.MINUTES));
    }

    @Test
    void detections_clampsLimitAndIgnoresBlankCamera() throws Exception {
        mockMvc.perform(get("/api/detections").param("limit", "50000").param("cameraId", " "))
                .andExpect(status().isOk());

        verify(activityService).detections(any(), any(), isNull(), isNull(), eq(1000));
    }

    @Test
    void analyses_passesMinLevel() throws Exception {
        mockMvc.perform(get("/api/analyses").param("minLevel", "watch"))
                .andExpect(status().isOk());

        verify(activityService).analyses(any(), any(), isNull(), isNull(), eq("watch"), eq(100));
    }

    @Test
    void analyses_unknownLevel_returns400() throws Exception {
        mockMvc.perform(get("/api/analyses").param("minLevel", "panic"))
                .andExpect(status().isBadRequest());

        verifyNoInteractions(activityService);
    }

    @Test
    void analyses_sinceAfterUntil_returns400() throws Exception {
        mockMvc.perform(get("/api/analyses")
                        .param("since", "2026-10-03T12:00:00")
                        .param("until", "2026-10-03T08:00:00"))
                .andExpect(status().isBadRequest());

        verifyNoInteractions(activityService);
    }

    @Test
    void live_returnsServiceResult() throws Exception {
        LocalDateTime t = LocalDateTime.parse("2026-10-03T16:15:00");
        when(activityService.live()).thenReturn(List.of(new LiveTrackView(
                "cam-01", 3, 42L, "Mail carrier", t, t.plusSeconds(40),
                "safe", "holding a package", "ordinary item", t.plusSeconds(4))));

        mockMvc.perform(get("/api/live"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].cameraId").value("cam-01"))
                .andExpect(jsonPath("$[0].personId").value(42))
                .andExpect(jsonPath("$[0].threatLevel").value("safe"))
                .andExpect(jsonPath("$[0].firstSeenAt").value("2026-10-03T16:15:00"));
    }
}
