package com.cs6650.doorbellbackend.repository;

import com.cs6650.doorbellbackend.entity.Person;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PersonRepository extends JpaRepository<Person, Long> {
}
