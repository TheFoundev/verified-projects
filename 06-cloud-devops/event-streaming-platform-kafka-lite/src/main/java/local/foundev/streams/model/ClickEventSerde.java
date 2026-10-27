package local.foundev.streams.model;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.common.errors.SerializationException;
import org.apache.kafka.common.serialization.Deserializer;
import org.apache.kafka.common.serialization.Serde;
import org.apache.kafka.common.serialization.Serializer;

import java.util.Map;

/**
 * The serde of a click event: JSON in, JSON out.
 *
 * The topics that carry the parsed clicks need it, because the value of the
 * stream is a {@link ClickEvent} and not the raw JSON of the input topic.
 */
public final class ClickEventSerde implements Serde<ClickEvent> {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Override
    public Serializer<ClickEvent> serializer() {
        return (topic, data) -> {
            try {
                return MAPPER.writeValueAsBytes(data);
            } catch (Exception e) {
                throw new SerializationException("could not serialise a click event", e);
            }
        };
    }

    @Override
    public Deserializer<ClickEvent> deserializer() {
        return (topic, bytes) -> {
            if (bytes == null) {
                return null;
            }
            try {
                return MAPPER.readValue(bytes, ClickEvent.class);
            } catch (Exception e) {
                throw new SerializationException("could not deserialise a click event", e);
            }
        };
    }

    @Override
    public void configure(Map<String, ?> configs, boolean isKey) {
        // nothing to configure
    }

    @Override
    public void close() {
        // nothing to close
    }
}
