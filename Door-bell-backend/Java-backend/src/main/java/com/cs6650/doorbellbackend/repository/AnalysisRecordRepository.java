/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Spring Data JPA repository for AnalysisRecord entities.
 */
package com.cs6650.doorbellbackend.repository;

import com.cs6650.doorbellbackend.entity.AnalysisRecord;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface AnalysisRecordRepository extends JpaRepository<AnalysisRecord, Long> {

    List<AnalysisRecord> findByAnalyzedAtBetweenOrderByAnalyzedAtDesc(LocalDateTime from, LocalDateTime to);
}
