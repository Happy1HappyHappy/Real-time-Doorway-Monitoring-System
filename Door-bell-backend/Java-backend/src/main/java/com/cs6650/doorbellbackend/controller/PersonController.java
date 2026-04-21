package com.cs6650.doorbellbackend.controller;

import com.cs6650.doorbellbackend.entity.Person;
import com.cs6650.doorbellbackend.repository.PersonRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/persons")
@RequiredArgsConstructor
public class PersonController {

    private final PersonRepository personRepository;

    @GetMapping
    public List<Person> listAll() {
        return personRepository.findAll();
    }

    @PutMapping("/{personId}/nickname")
    public ResponseEntity<Person> setNickname(
            @PathVariable Long personId,
            @RequestBody Map<String, String> body
    ) {
        return personRepository.findById(personId)
                .map(person -> {
                    String nickname = body.get("nickname");
                    person.setNickname(nickname == null || nickname.isBlank() ? null : nickname.trim());
                    return ResponseEntity.ok(personRepository.save(person));
                })
                .orElse(ResponseEntity.notFound().build());
    }
}
