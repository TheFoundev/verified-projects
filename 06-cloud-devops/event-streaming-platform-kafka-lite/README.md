# Event Streaming Platform (Kafka-lite)

> A lightweight, original Apache Kafka-inspired event streaming platform demonstrating real-time clickstream processing with schema validation and stream aggregations.

## Overview

An original, self-contained implementation of a **clickstream event pipeline** for the Foundev portfolio. It ingests high-throughput click events, enforces schemas via a bundled JSON Schema with a Kafka Schema Registry compatible client, processes streams using Kafka Streams APIs, aggregates windowed statistics, serves materialized views through an embedded HTTP server, and provides a browser-based dashboard.

## Features

- **Click Ingestion**: `ClickProducer` with configurable burst/continuous modes, keying by session_id, deterministic event IDs, and delivery callbacks
- **Schema Enforcement**: `SchemaRegistryClient` with HTTP calls to Confluent Schema Registry, schema caching, graceful offline fallback to bundled schema, plus `SchemaValidatingDeserializer` with DLQ routing (`clicks-dlq` topic) on violations
- **In-Process Schema Registry**: `FakeSchemaRegistry` (JDK `com.sun.net.httpserver`) for development/testing without external services
- **Stream Processing**: `ClicksStreamsApp` using Kafka Streams with bot filtering (regex), windowed aggregations (tumbling 1-minute windows), sessionization support, and state stores with changelog topics
- **Resilient Processing**: `StreamsExceptionHandler` returns CONTINUE on deserialization errors; exactly-once semantics documented/configured
- **Materialized View API**: `ViewServer` (JDK-only HTTP) exposing `/stats/pages`, `/stats/sessions/<id>`, `/stats/top-referrers`, `/health`, reading from Streams state store or consuming changelog topics in standalone mode
- **Dashboard**: Responsive single-page HTML/JS dashboard (`resources/dashboard/index.html`) with auto-refresh, no CDN dependencies
- **Dockerized Stack**: Multi-stage `Dockerfile`, `docker-compose.yml` with KRaft Kafka broker, Schema Registry, producer, streams app, and view server with healthchecks
- **CLI Interface**: `Main` with subcommands `producer|streams|view|fake-registry|all-local` for flexible deployment

## Stack

`JUnit; Java; Kafka`
## Pipeline architecture

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ ClickProducer│───▶│  clicks topic     │───▶│ SchemaValidating │
│ (JSON events)│     │  (key=session_id) │     │   Deserializer   │
└─────────────┘     └──────────────────┘     └─────────┬───────┘
                                                          │ (valid)
                                                          ▼
                                              ┌─────────────────────────┐
                                              │   ClicksStreamsApp      │
                                              │  - Bot filter (regex)   │
                                              │  - Windowed aggregations│
                                              │  - Sessionization       │
                                              └───────────┬─────────────┘
                                                          │
                                        ┌─────────────────┼─────────────────┐
                                        ▼                 ▼                 ▼
                                    clean-clicks    page-stats      session-stats
                                        │                 │                 │
                                        │                 ▼                 │
                                        │         ┌─────────────────┐         │
                                        │         │  ViewServer     │         │
                                        │         │ (state store or │         │
                                        │         │  topic consumer)│         │
                                        │         └─────────┬───────┘         │
                                        │                   │                 │
                                        │                   ▼                 │
                                        │             ┌─────────────┐         │
                                        │             │   Dashboard   │         │
                                        │             │  (HTML/JS)    │         │
                                        │             └─────────────┘         │
                                        │
                                    (violations)──▶ clicks-dlq
                                                      (error headers)
```

## Run

### Full stack with Docker Compose (recommended)
```bash
cd event-streaming-platform-kafka-lite
docker-compose up --build
```
- Kafka: localhost:9092 (KRaft mode)
- Schema Registry: http://localhost:8081
- ViewServer API: http://localhost:8082
- Dashboard: Open `src/main/resources/dashboard/index.html` or serve it; it expects ViewServer on same origin or adjust fetch URLs

### Local development (without Docker)

#### All-local mode (fake registry + view server, no broker required for smoke)
```bash
java -cp "target/classes:target/dependency/*" local.foundev.streams.Main all-local
```
Starts FakeSchemaRegistry on :8081 and ViewServer on :8082.

#### Component by component
```bash
# Terminal 1: Fake Schema Registry
java -cp "target/classes:target/dependency/*" local.foundev.streams.Main fake-registry

# Terminal 2: Click Producer (continuous, ~10 events/sec)
java -cp "target/classes:target/dependency/*" local.foundev.streams.Main producer localhost:9092

# Terminal 3: Kafka Streams App
java -cp "target/classes:target/dependency/*" local.foundev.streams.Main streams localhost:9092

# Terminal 4: View Server
java -cp "target/classes:target/dependency/*" local.foundev.streams.Main view 8082
```

### Canonical build (normal environments with Maven/JDK)
```bash
mvn clean package
```
Produces `target/event-streaming-platform-kafka-lite-1.0.0.jar`.

### Local verification gate
```bash
cd event-streaming-platform-kafka-lite
bash tools/verify.sh
```

## Structure

```text
event-streaming-platform-kafka-lite/
├── pom.xml
├── README.md
├── LICENSE
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── config/
│   ├── producer.properties
│   └── streams.properties
├── src/
│   ├── main/
│   │   ├── java/local/foundev/streams/
│   │   │   ├── Main.java
│   │   │   ├── api/ViewServer.java
│   │   │   ├── deser/SchemaValidatingDeserializer.java
│   │   │   ├── model/ClickEvent.java
│   │   │   ├── producer/ClickProducer.java
│   │   │   ├── registry/FakeSchemaRegistry.java
│   │   │   ├── registry/SchemaRegistryClient.java
│   │   │   └── streams/ClicksStreamsApp.java
│   │   └── resources/
│   │       ├── clicks-value.schema.json
│   │       └── dashboard/index.html
│   └── test/
│       └── java/local/foundev/streams/
├── tools/
│   └── verify.sh
├── .tools/   (gitignored, for local JAR downloads)
└── out/      (gitignored, build output)
```

## Verification

The canonical build is Maven:

```bash
mvn -B test        # 19 tests, 0 failures
```

The suite has three parts:

- `SchemaValidatingDeserializerTest`: a valid payload becomes an event, a
  missing or mistyped field is skipped and marked for the dead-letter queue, a
  broken payload is marked as a parse error, and the DLQ marking and the
  skip behaviour can be switched off.
- `SchemaRegistryClientTest`: the bundled schema, the cache, the fallback when
  the registry cannot be reached and the round trip against the
  `FakeSchemaRegistry`.
- `ClicksTopologyTest`: the whole pipeline with a `TopologyTestDriver` — the
  parse, the bot filter (Googlebot, python-requests) and the windowed
  counter per URL.

Writing the topology test uncovered two bugs that made the application
unusable at runtime: the page-stats store was registered twice, which made the
topology invalid, and the topics that carry the parsed clicks used the default
String serde instead of the ClickEvent serde, which failed with a
`ClassCastException` on the first write. Both are fixed.

`tools/verify.sh` stays available for an environment without Maven: it
compiles every source with the Eclipse compiler (ECJ) and runs the tests with
the JUnit 5 console launcher. It is the fallback, not the canonical build.

## Limitations

- **Build environment**: the canonical build is Maven (`mvn -B test`). The
  ECJ based `tools/verify.sh` is the fallback for an environment without Maven;
  Maven Central is the only network dependency of either.
- **Kafka Streams testing**: the topology is covered with a `TopologyTestDriver`, so no broker is needed. An end-to-end run against a real Kafka cluster still requires Docker; `tools/verify.sh` and the CI workflow cover the rest.
- **State store querying**: `ViewServer.getPageStatsStore()` references a queryable store; in standalone/topic-consumer mode it falls back to polling `page-stats` topic. Full Interactive Queries require a running Streams app with proper store configuration.
- **Schema validation**: `SchemaValidatingDeserializer` performs basic structural checks; a full JSON Schema validator is not bundled to avoid extra dependencies in this minimal setup.
- **DLQ production**: Violation routing marks headers for DLQ; actual production to `clicks-dlq` topic would require a producer injection/config in production wiring.
- **Dashboard API paths**: The dashboard fetches relative paths (`/stats/pages` etc.) assuming ViewServer serves them or is proxied; in standalone file-open scenarios CORS/origin may need adjustment.

## License

Apache-2.0. See `LICENSE`.
