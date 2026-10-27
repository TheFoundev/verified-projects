package local.foundev.streams.deser;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import local.foundev.streams.model.ClickEvent;
import org.apache.kafka.common.header.Headers;
import org.apache.kafka.common.serialization.Deserializer;

import java.nio.charset.StandardCharsets;
import java.util.Map;

public class SchemaValidatingDeserializer implements Deserializer<ClickEvent> {
    private final ObjectMapper mapper = new ObjectMapper();
    private boolean skipOnViolation = true;
    private boolean sendToDlq = true;

    @Override
    public void configure(Map<String, ?> configs, boolean isKey) {
        if (configs == null) {
            return; // the defaults are used
        }
        Object skip = configs.get("foundev.schema.skip");
        if (skip != null) {
            skipOnViolation = Boolean.parseBoolean(skip.toString());
        }
        Object dlq = configs.get("foundev.schema.dlq");
        if (dlq != null) {
            sendToDlq = Boolean.parseBoolean(dlq.toString());
        }
    }

    @Override
    public ClickEvent deserialize(String topic, byte[] data) {
        return deserialize(topic, null, data);
    }

    @Override
    public ClickEvent deserialize(String topic, Headers headers, byte[] data) {
        if (data == null) {
            return null;
        }
        try {
            JsonNode node = mapper.readTree(data);
            if (!isValid(node)) {
                if (headers != null && sendToDlq) {
                    headers.add("x-schema-error", "validation_failed".getBytes(StandardCharsets.UTF_8));
                    headers.add("x-original-topic", topic.getBytes(StandardCharsets.UTF_8));
                }
                if (skipOnViolation) {
                    return null; // Let StreamsExceptionHandler or filtering deal with null
                }
            }
            ClickEvent event = mapper.treeToValue(node, ClickEvent.class);
            // Basic required field checks
            if (event.getEventId() == null || event.getSessionId() == null || event.getUrl() == null || event.getUserAgent() == null) {
                if (headers != null && sendToDlq) {
                    headers.add("x-schema-error", "missing_required".getBytes(StandardCharsets.UTF_8));
                }
                if (skipOnViolation) {
                    return null;
                }
            }
            return event;
        } catch (Exception e) {
            if (headers != null && sendToDlq) {
                headers.add("x-schema-error", ("parse_error:" + e.getClass().getSimpleName()).getBytes(StandardCharsets.UTF_8));
            }
            if (skipOnViolation) {
                return null;
            }
            throw new RuntimeException(e);
        }
    }

    private boolean isValid(JsonNode node) {
        if (!node.isObject()) {
            return false;
        }
        if (!node.has("event_id") || !node.get("event_id").isTextual()) return false;
        if (!node.has("session_id") || !node.get("session_id").isTextual()) return false;
        if (!node.has("url") || !node.get("url").isTextual()) return false;
        if (!node.has("user_agent") || !node.get("user_agent").isTextual()) return false;
        if (!node.has("ts")) return false;
        return true;
    }

    @Override
    public void close() {
    }
}
