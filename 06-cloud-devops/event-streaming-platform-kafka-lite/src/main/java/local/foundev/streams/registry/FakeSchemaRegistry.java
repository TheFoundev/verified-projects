package local.foundev.streams.registry;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

public class FakeSchemaRegistry {
    private HttpServer server;
    private final int port;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private String schema;

    public FakeSchemaRegistry(int port) {
        this.port = port;
    }

    public void start() throws IOException {
        if (running.get()) {
            return;
        }
        schema = loadBundledSchema();
        server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/subjects/clicks-value/versions", new RegisterHandler());
        server.createContext("/subjects/clicks-value/versions/latest", new LatestHandler());
        server.setExecutor(null);
        server.start();
        running.set(true);
    }

    public void stop() {
        if (server != null && running.get()) {
            server.stop(0);
            running.set(false);
        }
    }

    public String getBaseUrl() {
        return "http://localhost:" + port;
    }

    private class RegisterHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if ("POST".equals(exchange.getRequestMethod())) {
                String response = "{\"id\":1}";
                byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/vnd.schemaregistry.v1+json");
                exchange.sendResponseHeaders(200, bytes.length);
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(bytes);
                }
                return;
            }
            exchange.sendResponseHeaders(405, -1);
        }
    }

    private class LatestHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if ("GET".equals(exchange.getRequestMethod())) {
                String response = "{\"subject\":\"clicks-value\",\"version\":1,\"schema\":" + jsonString(schema) + "}";
                byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/vnd.schemaregistry.v1+json");
                exchange.sendResponseHeaders(200, bytes.length);
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(bytes);
                }
                return;
            }
            exchange.sendResponseHeaders(405, -1);
        }
    }

    private String loadBundledSchema() {
        try (InputStream is = getClass().getClassLoader().getResourceAsStream("clicks-value.schema.json")) {
            if (is == null) {
                return "{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"type\":\"object\",\"required\":[\"event_id\",\"session_id\",\"url\",\"user_agent\",\"ts\"]}";
            }
            return new String(is.readAllBytes());
        } catch (Exception e) {
            return "{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"type\":\"object\",\"required\":[\"event_id\",\"session_id\",\"url\",\"user_agent\",\"ts\"]}";
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
