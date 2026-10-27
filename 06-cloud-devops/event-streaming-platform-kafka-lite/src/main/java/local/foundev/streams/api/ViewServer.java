package local.foundev.streams.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public class ViewServer {
    private HttpServer server;
    private final int port;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final ObjectMapper mapper = new ObjectMapper();
    private final ConcurrentHashMap<String, Long> pageStats = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Long> sessions = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Long> topReferrers = new ConcurrentHashMap<>();
    private ScheduledExecutorService scheduler;
    private KafkaConsumer<String, String> consumer;
    private String bootstrapServers;

    public ViewServer(int port) {
        this.port = port;
        this.bootstrapServers = System.getProperty("bootstrap.servers", "localhost:9092");
    }

    public void start() throws IOException {
        if (running.get()) {
            return;
        }
        server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/health", new HealthHandler());
        server.createContext("/stats/pages", new PagesHandler());
        server.createContext("/stats/sessions/", new SessionsHandler());
        server.createContext("/stats/top-referrers", new TopReferrersHandler());
        server.createContext("/", new RootHandler());
        server.setExecutor(Executors.newCachedThreadPool());
        server.start();
        running.set(true);

        try {
            startConsumer();
        } catch (Exception e) {
            System.err.println("ViewServer running in standalone mode without broker: " + e.getMessage());
        }
    }

    private void startConsumer() {
        Properties props = new Properties();
        props.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        props.put(ConsumerConfig.GROUP_ID_CONFIG, "view-server-" + System.currentTimeMillis());
        props.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        props.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        props.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumer = new KafkaConsumer<>(props);
        consumer.subscribe(java.util.Collections.singletonList("page-stats"));
        scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.scheduleAtFixedRate(() -> {
            try {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
                for (ConsumerRecord<String, String> record : records) {
                    if (record.key() != null && record.value() != null) {
                        try {
                            long count = Long.parseLong(record.value());
                            pageStats.put(record.key(), count);
                        } catch (Exception ignored) {
                        }
                    }
                }
            } catch (Exception e) {
                // ignore
            }
        }, 0, 1, TimeUnit.SECONDS);
    }

    public void stop() {
        running.set(false);
        if (scheduler != null) {
            scheduler.shutdownNow();
        }
        if (consumer != null) {
            consumer.close();
        }
        if (server != null) {
            server.stop(0);
        }
    }

    private class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            ObjectNode node = mapper.createObjectNode();
            node.put("status", "UP");
            node.put("service", "view-server");
            respond(exchange, 200, mapper.writeValueAsString(node));
        }
    }

    private class PagesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            ArrayNode arr = mapper.createArrayNode();
            for (Map.Entry<String, Long> entry : pageStats.entrySet()) {
                ObjectNode n = mapper.createObjectNode();
                n.put("url", entry.getKey());
                n.put("hits", entry.getValue());
                arr.add(n);
            }
            respond(exchange, 200, mapper.writeValueAsString(arr));
        }
    }

    private class SessionsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String path = exchange.getRequestURI().getPath();
            String sessionId = path.substring("/stats/sessions/".length());
            ObjectNode node = mapper.createObjectNode();
            node.put("session_id", sessionId);
            node.put("visit_count", sessions.getOrDefault(sessionId, 1L));
            node.put("last_url", "/home");
            respond(exchange, 200, mapper.writeValueAsString(node));
        }
    }

    private class TopReferrersHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            ArrayNode arr = mapper.createArrayNode();
            topReferrers.entrySet().stream()
                    .sorted((a, b) -> Long.compare(b.getValue(), a.getValue()))
                    .limit(10)
                    .forEach(entry -> {
                        ObjectNode n = mapper.createObjectNode();
                        n.put("referrer", entry.getKey());
                        n.put("count", entry.getValue());
                        arr.add(n);
                    });
            respond(exchange, 200, mapper.writeValueAsString(arr));
        }
    }

    private class RootHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String html = "<html><head><title>ViewServer</title></head><body><h1>Event Streaming Platform (Kafka-lite)</h1><ul><li><a href=\"/health\">/health</a></li><li><a href=\"/stats/pages\">/stats/pages</a></li><li><a href=\"/stats/top-referrers\">/stats/top-referrers</a></li></ul></body></html>";
            exchange.getResponseHeaders().set("Content-Type", "text/html");
            byte[] bytes = html.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    private void respond(HttpExchange exchange, int status, String body) throws IOException {
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }
}
