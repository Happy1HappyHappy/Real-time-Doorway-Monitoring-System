/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Read-only response shapes for the activity API (/api/live, /api/detections,
 * /api/analyses). All timestamps are naive UTC, same as what the workers write.
 */
package com.cs6650.doorbellbackend.dto;

import java.time.LocalDateTime;
import java.util.List;

public final class ActivityViews {

    private ActivityViews() {
    }

    /** A person currently in frame, with the latest VLM verdict for their track (null fields until one arrives). */
    public record LiveTrackView(
            String cameraId,
            int trackId,
            long personId,
            String nickname,
            LocalDateTime firstSeenAt,
            LocalDateTime lastSeenAt,
            String threatLevel,
            String description,
            String reason,
            LocalDateTime analyzedAt
    ) {
    }

    public record DetectionView(
            long id,
            Long personId,
            String nickname,
            String cameraId,
            Integer trackId,
            Double confidence,
            LocalDateTime detectedAt
    ) {
    }

    public record AnalysisView(
            long id,
            String cameraId,
            Integer trackId,
            Long personId,
            String nickname,
            String threatLevel,
            String description,
            String reason,
            LocalDateTime analyzedAt
    ) {
    }

    /** {@code total} counts every match in the window; {@code items} is cut off at the requested limit. */
    public record ListResult<T>(int total, List<T> items) {
    }
}
