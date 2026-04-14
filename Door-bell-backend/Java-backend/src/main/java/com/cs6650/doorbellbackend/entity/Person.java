package com.cs6650.doorbellbackend.entity;

import jakarta.persistence.*;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Data
@NoArgsConstructor
@Entity
@Table(name = "persons")
public class Person {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "first_seen_camera")
    private String firstSeenCamera;

    @Column(name = "first_seen_at")
    private LocalDateTime firstSeenAt;

    @Column(name = "last_seen_at")
    private LocalDateTime lastSeenAt;

    public Person(String cameraId, LocalDateTime timestamp) {
        this.firstSeenCamera = cameraId;
        this.firstSeenAt = timestamp;
        this.lastSeenAt = timestamp;
    }
}
