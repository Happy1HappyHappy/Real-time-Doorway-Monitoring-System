package com.cs6650.doorbellbackend.dto;

import lombok.Data;
import java.util.List;

@Data
public class DetectionEvent {
    private String cameraId;
    private String timestamp;
    private List<PersonDetection> detections;

    @Data
    public static class PersonDetection {
        private int trackId;
        private List<Double> bbox; // Frame to draw around the person
        private List<Double> embedding; // Vector that related to the person
        private double confidence;
    }
}