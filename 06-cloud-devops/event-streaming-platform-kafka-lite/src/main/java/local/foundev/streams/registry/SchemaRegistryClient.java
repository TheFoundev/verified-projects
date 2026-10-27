package local.foundev.streams.registry;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public class SchemaRegistryClient {
    private final String baseUrl;
    private final HttpClient httpClient;
    private final Map<String, String> schemaCache = new ConcurrentHashMap<>();
    private static final String SUBJECT = "clicks-value";

    public SchemaRegistryClient(String baseUrl) {
        this.baseUrl = baseUrl == null || baseUrl.isBlank() ? "http://localhost:8081" : baseUrl;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();
    }

    public String getOrRegisterValueSchema() throws IOException {
        String cached = schemaCache.get(SUBJECT);
        if (cached != null && !cached.isBlank()) {
            return cached;
        }
        String schema = loadBundledSchema();
        try {
            registerSchema(schema);
        } catch (Exception ignored) {
            // Fallback to bundled if registry unavailable
        }
        schemaCache.put(SUBJECT, schema);
        return schema;
    }

    public void registerSchema(String schema) throws IOException, InterruptedException {
        try {
            String body = "{\"schema\":" + jsonString(schema) + "}";
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + "/subjects/" + SUBJECT + "/versions"))
                    .timeout(Duration.ofSeconds(3))
                    .header("Content-Type", "application/vnd.schemaregistry.v1+json")
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 300 && response.statusCode() != 409) {
                throw new IOException("Failed to register schema: " + response.statusCode() + " " + response.body());
            }
        } catch (Exception e) {
            throw e;
        }
    }

    public String getLatestSchema() throws IOException {
        String cached = schemaCache.get(SUBJECT);
        if (cached != null && !cached.isBlank()) {
            return cached;
        }
        String schema = loadBundledSchema();
        schemaCache.put(SUBJECT, schema);
        return schema;
    }

    private String loadBundledSchema() throws IOException {
        try (InputStream is = getClass().getClassLoader().getResourceAsStream("clicks-value.schema.json")) {
            if (is == null) {
                return "{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"type\":\"object\"}";
            }
            return new String(is.readAllBytes());
        }
    }

    private String jsonString(String s) {
        return "\"" + s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t") + "\"";
    }
}
