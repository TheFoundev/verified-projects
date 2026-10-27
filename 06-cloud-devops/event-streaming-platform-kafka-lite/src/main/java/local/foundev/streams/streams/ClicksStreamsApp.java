package local.foundev.streams.streams;

import com.fasterxml.jackson.databind.ObjectMapper;
import local.foundev.streams.model.ClickEvent;
import local.foundev.streams.model.ClickEventSerde;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.utils.Bytes;
import org.apache.kafka.streams.KafkaStreams;
import org.apache.kafka.streams.StoreQueryParameters;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.Topology;
import org.apache.kafka.streams.kstream.*;
import org.apache.kafka.streams.kstream.WindowedSerdes;
import org.apache.kafka.streams.state.QueryableStoreTypes;
import org.apache.kafka.streams.state.ReadOnlyKeyValueStore;
import org.apache.kafka.streams.state.Stores;
import org.apache.kafka.streams.state.WindowStore;

import java.time.Duration;
import java.util.Properties;
import java.util.regex.Pattern;

public class ClicksStreamsApp {
    private static final Pattern BOT_PATTERN = Pattern.compile("(?i)(bot|crawler|spider|googlebot|bingbot|curl|wget|python|java)");
    private final KafkaStreams streams;
    /** The name of the store that holds the windowed page counters. */
    public static final String PAGE_STATS_STORE = "page-stats-store";
    /** The name of the store that holds the sessions. */
    public static final String SESSION_STORE = "session-store";
    /** The topic the clean clicks are written to. */
    public static final String CLEAN_TOPIC = "clean-clicks";
    /** The topic the windowed counters are written to. */
    public static final String PAGE_STATS_TOPIC = "page-stats";
    /** The topic the clicks are read from. */
    public static final String CLICKS_TOPIC = "clicks";

    public ClicksStreamsApp(Properties props) {
        Properties p = new Properties();
        p.putAll(props);
        p.putIfAbsent(StreamsConfig.APPLICATION_ID_CONFIG, "clicks-streams-app");
        p.putIfAbsent(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "localhost:9092");
        p.putIfAbsent(StreamsConfig.DEFAULT_KEY_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        p.putIfAbsent(StreamsConfig.DEFAULT_VALUE_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        p.putIfAbsent(StreamsConfig.PROCESSING_GUARANTEE_CONFIG, StreamsConfig.EXACTLY_ONCE_V2);

        this.streams = new KafkaStreams(buildTopology(), p);
    }

    /**
     * The topology of the application, built on its own so that it can be
     * exercised with a TopologyTestDriver (no broker needed).
     */
    public static Topology buildTopology() {
        StreamsBuilder builder = new StreamsBuilder();

        // The page-stats store is registered by the count() below (through
        // Materialized.as); adding it here as well made the topology invalid
        // ("a different StateStore has already been added with that name").

        builder.addStateStore(Stores.keyValueStoreBuilder(
                Stores.persistentKeyValueStore(SESSION_STORE),
                Serdes.String(),
                Serdes.String()
        ));

        KStream<String, String> clicks = builder.stream(CLICKS_TOPIC);

        ObjectMapper mapper = new ObjectMapper();
        KStream<String, ClickEvent> parsed = clicks
                .mapValues((key, value) -> {
                    try {
                        if (value == null) return null;
                        return mapper.readValue(value, ClickEvent.class);
                    } catch (Exception e) {
                        return null;
                    }
                })
                .filter((key, v) -> v != null);

        KStream<String, ClickEvent> clean = parsed
                .filter((key, event) -> event != null && event.getUserAgent() != null
                        && !BOT_PATTERN.matcher(event.getUserAgent()).find());

        // The value of the stream is a ClickEvent, so the topic needs the
        // matching serde: with the default one the write fails at runtime.
        clean.to(CLEAN_TOPIC, Produced.with(Serdes.String(), new ClickEventSerde()));

        KTable<Windowed<String>, Long> pageStats = clean
                // The repartition topic of the groupBy carries the events
                // themselves, so it needs the ClickEvent serde too.
                .groupBy((key, event) -> event.getUrl() == null ? "unknown" : event.getUrl(),
                        Grouped.with(Serdes.String(), new ClickEventSerde()))
                .windowedBy(TimeWindows.ofSizeWithNoGrace(Duration.ofMinutes(1)))
                .count(Materialized.<String, Long, WindowStore<Bytes, byte[]>>as(PAGE_STATS_STORE)
                        .withKeySerde(Serdes.String())
                        .withValueSerde(Serdes.Long()));

        pageStats.toStream().to(PAGE_STATS_TOPIC,
                Produced.with(WindowedSerdes.timeWindowedSerdeFrom(String.class), Serdes.Long()));

        return builder.build();
    }

    public void start() {
        streams.start();
    }

    public void shutdown() {
        streams.close();
    }

    public KafkaStreams getStreams() {
        return streams;
    }

    public ReadOnlyKeyValueStore<String, Long> getPageStatsStore() {
        return streams.store(StoreQueryParameters.fromNameAndType(PAGE_STATS_STORE, QueryableStoreTypes.keyValueStore()));
    }
}
