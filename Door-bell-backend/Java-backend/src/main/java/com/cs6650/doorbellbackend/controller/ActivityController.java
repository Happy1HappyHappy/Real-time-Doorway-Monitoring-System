/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: REST endpoints for reading activity: GET /api/live (who is in frame now) and
 * GET /api/detections, /api/analyses (time-window queries). Times in and out are naive UTC.
 * Without since/until, a query covers the last 24 hours.
 */
package com.cs6650.doorbellbackend.controller;

import com.cs6650.doorbellbackend.dto.ActivityViews.AnalysisView;
import com.cs6650.doorbellbackend.dto.ActivityViews.DetectionView;
import com.cs6650.doorbellbackend.dto.ActivityViews.ListResult;
import com.cs6650.doorbellbackend.dto.ActivityViews.LiveTrackView;
import com.cs6650.doorbellbackend.service.ActivityService;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;

@RestController
@RequestMapping("/api")
@RequiredArgsConstructor
public class ActivityController {

    private static final int MAX_LIMIT = 1000;

    private final ActivityService activityService;

    @GetMapping("/live")
    public List<LiveTrackView> live() {
        return activityService.live();
    }

    @GetMapping("/detections")
    public ListResult<DetectionView> detections(
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime since,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime until,
            @RequestParam(required = false) String cameraId,
            @RequestParam(required = false) Long personId,
            @RequestParam(defaultValue = "100") int limit
    ) {
        Window window = Window.of(since, until);
        return activityService.detections(window.since(), window.until(),
                blankToNull(cameraId), personId, clampLimit(limit));
    }

    @GetMapping("/analyses")
    public ListResult<AnalysisView> analyses(
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime since,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime until,
            @RequestParam(required = false) String cameraId,
            @RequestParam(required = false) Long personId,
            @RequestParam(defaultValue = "safe") String minLevel,
            @RequestParam(defaultValue = "100") int limit
    ) {
        if (!ActivityService.THREAT_LEVELS.contains(minLevel)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "minLevel must be one of " + ActivityService.THREAT_LEVELS);
        }
        Window window = Window.of(since, until);
        return activityService.analyses(window.since(), window.until(),
                blankToNull(cameraId), personId, minLevel, clampLimit(limit));
    }

    private record Window(LocalDateTime since, LocalDateTime until) {
        static Window of(LocalDateTime since, LocalDateTime until) {
            LocalDateTime to = until != null ? until : LocalDateTime.now(ZoneOffset.UTC);
            LocalDateTime from = since != null ? since : to.minusHours(24);
            if (from.isAfter(to)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "since must not be after until");
            }
            return new Window(from, to);
        }
    }

    private static int clampLimit(int limit) {
        return Math.max(1, Math.min(limit, MAX_LIMIT));
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
