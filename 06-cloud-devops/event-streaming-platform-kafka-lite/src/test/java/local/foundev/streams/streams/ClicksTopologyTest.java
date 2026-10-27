package local.foundev.streams.streams;

import com.fasterxml.jackson.databind.ObjectMapper;
import local.foundev.streams.model.ClickEvent;
import org.apache.kafka.common.serialization.Serde;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.TopologyTestDriver;
import org.apache.kafka.streams.kstream.Windowed;
import org.apache.kafka.streams.kstream.WindowedSerdes;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The topology is exercised with a TopologyTestDriver: no broker, no
 * network, and the whole pipeline is checked (parse, bot filter, windowed
 * aggregation).
 */
class ClicksTopologyTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private TopologyTestDriver driver;
    private TestInputTopic<String, String> clicks;
    private TestOutputTopic<String, ClickEvent> clean;
    private TestOutputTopic<Windowed<String>, Long> pageStats;

    @BeforeEach
    void setUp() {
        Properties props = new Properties();
        props.put(StreamsConfig.APPLICATION_ID_CONFIG, "clicks-topology-test");
        props.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "dummy:9092");
        props.put(StreamsConfig.DEFAULT_KEY_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        props.put(StreamsConfig.DEFAULT_VALUE_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        driver = new TopologyTestDriver(ClicksStreamsApp.buildTopology(), props);
        clicks = driver.createInputTopic(ClicksStreamsApp.CLICKS_TOPIC,
                Serdes.String().serializer(), Serdes.String().serializer());
        clean = driver.createOutputTopic(ClicksStreamsApp.CLEAN_TOPIC,
                Serdes.String().deserializer(), clickEventDeserializer());
        pageStats = driver.createOutputTopic(ClicksStreamsApp.PAGE_STATS_TOPIC,
                WindowedSerdes.timeWindowedSerdeFrom(String.class).deserializer(),
                Serdes.Long().deserializer());
    }

    @AfterEach
    void tearDown() {
        driver.close();
    }

    private static org.apache.kafka.common.serialization.Deserializer<ClickEvent> clickEventDeserializer() {
        return (topic, bytes) -> {
            try {
                return MAPPER.readValue(bytes, ClickEvent.class);
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        };
    }

    private static String click(String id, String url, String userAgent) {
        return "{\"event_id\":\"" + id + "\",\"session_id\":\"s1\",\"url\":\"" + url
                + "\",\"user_agent\":\"" + userAgent + "\",\"ts\":1700000000000}";
    }

    @Test
    void theTopologyIsBuilt() {
        assertNotNull(ClicksStreamsApp.buildTopology());
        assertFalse(ClicksStreamsApp.buildTopology().describe().toString().isEmpty());
    }

    @Test
    void aValidClickReachesTheCleanTopic() {
        clicks.pipeInput("k1", click("e1", "https://shop.example.test/a", "Mozilla/5.0"));

        var events = clean.readValuesToList();
        assertEquals(1, events.size());
        assertEquals("e1", events.get(0).getEventId());
    }

    @Test
    void aBotIsFilteredOut() {
        clicks.pipeInput("k1", click("e1", "https://shop.example.test/a", "Googlebot/2.1"));

        assertTrue(clean.readValuesToList().isEmpty(), "the bot must not reach the clean topic");
    }

    @Test
    void aCrawlerIsFilteredOut() {
        clicks.pipeInput("k1", click("e1", "https://shop.example.test/a", "python-requests/2.0"));

        assertTrue(clean.readValuesToList().isEmpty(), "the crawler must not reach the clean topic");
    }

    @Test
    void aBrokenPayloadIsFilteredOut() {
        clicks.pipeInput("k1", "{not json");

        assertTrue(clean.readValuesToList().isEmpty(), "the broken payload must not reach the clean topic");
    }

    @Test
    void theClicksAreCountedPerUrlInTheWindow() {
        clicks.pipeInput("k1", click("e1", "https://shop.example.test/a", "Mozilla/5.0"));
        clicks.pipeInput("k2", click("e2", "https://shop.example.test/a", "Mozilla/5.0"));
        clicks.pipeInput("k3", click("e3", "https://shop.example.test/b", "Mozilla/5.0"));

        driver.advanceWallClockTime(Duration.ofMinutes(2));

        var counts = pageStats.readKeyValuesToList();
        assertFalse(counts.isEmpty(), "the counters must be published when the window closes");
        // The windowed aggregation publishes an update for every click, so the
        // last value of a window is its counter.
        java.util.Map<String, Long> latest = new java.util.LinkedHashMap<>();
        for (var count : counts) {
            latest.put(count.key.key(), count.value);
        }
        long total = latest.values().stream().mapToLong(Long::longValue).sum();
        assertEquals(3L, total, "every click is counted: " + counts);
        assertEquals(2L, latest.get("https://shop.example.test/a"));
        assertEquals(1L, latest.get("https://shop.example.test/b"));
    }
}
