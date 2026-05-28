/**
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: MockMvc tests for PersonController nickname endpoint — covers happy path,
 * trimming, blank-to-null normalisation, and 404 for missing person.
 */
package com.cs6650.doorbellbackend.controller;

import com.cs6650.doorbellbackend.entity.Person;
import com.cs6650.doorbellbackend.repository.PersonRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class PersonControllerTest {

    @Mock private PersonRepository personRepository;
    @InjectMocks private PersonController controller;

    private MockMvc mockMvc;
    private final ObjectMapper json = new ObjectMapper();

    @BeforeEach
    void setUp() {
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    private Person makePerson(long id) {
        Person p = new Person("cam-01", LocalDateTime.now());
        p.setId(id);
        return p;
    }

    @Test
    void listAll_returnsRepoContents() throws Exception {
        Person p = makePerson(1L);
        p.setNickname("Alice");
        when(personRepository.findAll()).thenReturn(List.of(p));

        mockMvc.perform(get("/api/persons"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(1))
                .andExpect(jsonPath("$[0].nickname").value("Alice"));
    }

    @Test
    void setNickname_happyPath_trimsAndSaves() throws Exception {
        Person p = makePerson(42L);
        when(personRepository.findById(42L)).thenReturn(Optional.of(p));
        when(personRepository.save(any(Person.class))).thenAnswer(inv -> inv.getArgument(0));

        mockMvc.perform(put("/api/persons/42/nickname")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("nickname", "  Bob  "))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nickname").value("Bob"));

        ArgumentCaptor<Person> captor = ArgumentCaptor.forClass(Person.class);
        verify(personRepository).save(captor.capture());
        assertThat(captor.getValue().getNickname()).isEqualTo("Bob");
    }

    @Test
    void setNickname_blank_storedAsNull() throws Exception {
        Person p = makePerson(1L);
        p.setNickname("Old");
        when(personRepository.findById(1L)).thenReturn(Optional.of(p));
        when(personRepository.save(any(Person.class))).thenAnswer(inv -> inv.getArgument(0));

        mockMvc.perform(put("/api/persons/1/nickname")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("nickname", "   "))))
                .andExpect(status().isOk());

        ArgumentCaptor<Person> captor = ArgumentCaptor.forClass(Person.class);
        verify(personRepository).save(captor.capture());
        assertThat(captor.getValue().getNickname()).isNull();
    }

    @Test
    void setNickname_missingKey_storedAsNull() throws Exception {
        Person p = makePerson(1L);
        p.setNickname("Old");
        when(personRepository.findById(1L)).thenReturn(Optional.of(p));
        when(personRepository.save(any(Person.class))).thenAnswer(inv -> inv.getArgument(0));

        mockMvc.perform(put("/api/persons/1/nickname")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        ArgumentCaptor<Person> captor = ArgumentCaptor.forClass(Person.class);
        verify(personRepository).save(captor.capture());
        assertThat(captor.getValue().getNickname()).isNull();
    }

    @Test
    void setNickname_unknownId_returns404() throws Exception {
        when(personRepository.findById(999L)).thenReturn(Optional.empty());

        mockMvc.perform(put("/api/persons/999/nickname")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("nickname", "x"))))
                .andExpect(status().isNotFound());
    }
}
