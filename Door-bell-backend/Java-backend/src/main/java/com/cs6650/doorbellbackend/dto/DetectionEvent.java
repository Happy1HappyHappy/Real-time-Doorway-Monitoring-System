package com.cs6650.doorbellbackend.dto;

import lombok.Data;
import java.util.List;

@Data
public class DetectionEvent {
    private String type; // "detection" or "left"
    private String cameraId;
    private String timestamp;
    private Long kafkaProducedAt;
    private List<PersonDetection> detections;
    private List<Integer> leftTrackIds; // only for type="left"
    private List<TrackPosition> tracks; // only for type="position"

    @Data
    public static class PersonDetection {
        private int trackId;
        private List<Double> bbox;
        private List<Double> embedding;
        private double confidence;
    }

    @Data
    public static class TrackPosition {
        private int trackId;
        private List<Double> bbox;
    }
}