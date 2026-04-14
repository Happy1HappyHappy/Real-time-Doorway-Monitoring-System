package com.cs6650.doorbellbackend.repository;

import com.cs6650.doorbellbackend.entity.DetectionRecord;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DetectionRecordRepository extends JpaRepository<DetectionRecord, Long> {
}
