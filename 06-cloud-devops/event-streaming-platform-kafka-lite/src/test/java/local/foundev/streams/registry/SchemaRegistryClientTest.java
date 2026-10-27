package local.foundev.streams.registry;

import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The registry client works with the real fake registry and falls back to the
 * bundled schema when the registry cannot be reached.
 */
class SchemaRegistryClientTest {

    @Test
    void theLatestSchemaComesFromTheBundle() throws IOException {
        SchemaRegistryClient client = new SchemaRegistryClient("http://localhost:1");

        String schema = client.getLatestSchema();

        assertNotNull(schema);
        assertTrue(schema.contains("schema"), "the bundled schema is a JSON schema: " + schema);
    }

    @Test
    void theSchemaIsCached() throws IOException {
        SchemaRegistryClient client = new SchemaRegistryClient("http://localhost:1");

        String first = client.getLatestSchema();
        String second = client.getLatestSchema();

        assertEquals(first, second);
    }

    @Test
    void anUnreachableRegistryFallsBackToTheBundle() throws IOException {
        // Port 1 is not listening: the registration fails and the bundled
        // schema is used instead.
        SchemaRegistryClient client = new SchemaRegistryClient("http://localhost:1");

        String schema = client.getOrRegisterValueSchema();

        assertNotNull(schema);
        assertTrue(schema.contains("schema"));
    }

    @Test
    void aBlankBaseUrlFallsBackToTheDefault() {
        SchemaRegistryClient client = new SchemaRegistryClient("  ");

        assertNotNull(client);
        assertEquals("http://localhost:8081", baseUrlOf(client));
    }

    @Test
    void theFakeRegistryAnswersTheRegistration() throws IOException {
        FakeSchemaRegistry registry = new FakeSchemaRegistry(0);
        try {
            registry.start();
            SchemaRegistryClient client = new SchemaRegistryClient(registry.getBaseUrl());

            String schema = client.getOrRegisterValueSchema();

            assertNotNull(schema);
            assertTrue(schema.contains("schema"));
        } finally {
            registry.stop();
        }
    }

    private static String baseUrlOf(SchemaRegistryClient client) {
        try {
            var field = SchemaRegistryClient.class.getDeclaredField("baseUrl");
            field.setAccessible(true);
            return (String) field.get(client);
        } catch (ReflectiveOperationException e) {
            throw new AssertionError(e);
        }
    }
}
