/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: JPA entity for one VLM verdict on a tracked person: threat level, description,
 * and reason, plus the person it was matched to when ReID had already resolved the track.
 */
package com.cs6650.doorbellbackend.entity;

import jakarta.persistence.*;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Data
@NoArgsConstructor
@Entity
@Table(name = "analysis_records",
        indexes = @Index(name = "idx_analysis_records_analyzed_at", columnList = "analyzed_at"))
public class AnalysisRecord {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "camera_id")
    private String cameraId;

    @Column(name = "track_id")
    private Integer trackId;

    // Null when the verdict came back before ReID matched the track to a person,
    // or for people who never got close enough to the camera to be matched.
    @Column(name = "person_id")
    private Long personId;

    @Column(name = "threat_level", length = 16, nullable = false)
    private String threatLevel;

    @Column(length = 1000)
    private String description;

    @Column(length = 1000)
    private String reason;

    @Column(name = "latency_ms")
    private Integer latencyMs;

    @Column(name = "analyzed_at", nullable = false)
    private LocalDateTime analyzedAt;
}
