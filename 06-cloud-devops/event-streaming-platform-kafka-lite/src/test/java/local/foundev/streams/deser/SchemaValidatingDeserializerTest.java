package local.foundev.streams.deser;

import local.foundev.streams.model.ClickEvent;
import org.apache.kafka.common.header.Headers;
import org.apache.kafka.common.header.internals.RecordHeaders;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The deserializer validates the payload and marks the dead-lettered ones. */
class SchemaValidatingDeserializerTest {

    private static final String VALID = "{"
            + "\"event_id\":\"e1\","
            + "\"session_id\":\"s1\","
            + "\"url\":\"https://shop.example.test/a\","
            + "\"user_agent\":\"Mozilla/5.0\","
            + "\"ts\":1700000000000}";

    private static SchemaValidatingDeserializer deserializer() {
        SchemaValidatingDeserializer deserializer = new SchemaValidatingDeserializer();
        deserializer.configure(null, false);
        return deserializer;
    }

    private static String header(Headers headers, String name) {
        var header = headers.lastHeader(name);
        return header == null ? null : new String(header.value(), StandardCharsets.UTF_8);
    }

    @Test
    void aValidPayloadBecomesAnEvent() {
        ClickEvent event = deserializer().deserialize("clicks", VALID.getBytes(StandardCharsets.UTF_8));

        assertNotNull(event);
        assertEquals("e1", event.getEventId());
        assertEquals("s1", event.getSessionId());
        assertEquals("https://shop.example.test/a", event.getUrl());
        assertEquals("Mozilla/5.0", event.getUserAgent());
        assertEquals(1700000000000L, event.getTs());
    }

    @Test
    void aMissingFieldIsSkippedAndMarked() {
        Headers headers = new RecordHeaders();

        ClickEvent event = deserializer().deserialize("clicks", headers,
                "{\"event_id\":\"e1\"}".getBytes(StandardCharsets.UTF_8));

        assertNull(event, "the message is skipped");
        assertEquals("validation_failed", header(headers, "x-schema-error"));
        assertEquals("clicks", header(headers, "x-original-topic"));
    }

    @Test
    void aFieldOfTheWrongTypeIsSkippedAndMarked() {
        Headers headers = new RecordHeaders();

        ClickEvent event = deserializer().deserialize("clicks", headers,
                ("{\"event_id\":42,\"session_id\":\"s1\",\"url\":\"u\",\"user_agent\":\"a\",\"ts\":1}")
                        .getBytes(StandardCharsets.UTF_8));

        assertNull(event);
        assertEquals("validation_failed", header(headers, "x-schema-error"));
    }

    @Test
    void aBrokenPayloadIsMarkedAsAParseError() {
        Headers headers = new RecordHeaders();

        ClickEvent event = deserializer().deserialize("clicks", headers, "{not json".getBytes(StandardCharsets.UTF_8));

        assertNull(event);
        assertTrue(header(headers, "x-schema-error").startsWith("parse_error:"),
                "got: " + header(headers, "x-schema-error"));
    }

    @Test
    void nullDataIsIgnored() {
        assertNull(deserializer().deserialize("clicks", (byte[]) null));
    }

    @Test
    void theDeadLetterMarkingCanBeSwitchedOff() {
        SchemaValidatingDeserializer deserializer = new SchemaValidatingDeserializer();
        deserializer.configure(java.util.Map.of("foundev.schema.dlq", "false"), false);
        Headers headers = new RecordHeaders();

        deserializer.deserialize("clicks", headers, "{not json".getBytes(StandardCharsets.UTF_8));

        assertNull(header(headers, "x-schema-error"), "no mark when the DLQ is off");
    }

    @Test
    void aViolationCanBeThrownInsteadOfSkipped() {
        SchemaValidatingDeserializer deserializer = new SchemaValidatingDeserializer();
        deserializer.configure(java.util.Map.of("foundev.schema.skip", "false"), false);

        org.junit.jupiter.api.Assertions.assertThrows(RuntimeException.class,
                () -> deserializer.deserialize("clicks", "{not json".getBytes(StandardCharsets.UTF_8)));
    }
}
