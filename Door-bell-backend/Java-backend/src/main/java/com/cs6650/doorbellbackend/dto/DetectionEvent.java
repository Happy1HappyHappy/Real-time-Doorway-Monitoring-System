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

    @Data
    public static class PersonDetection {
        private int trackId;
        private List<Double> bbox; // Frame to draw around the person
        private List<Double> embedding; // Vector that related to the person
        private double confidence;
    }
}