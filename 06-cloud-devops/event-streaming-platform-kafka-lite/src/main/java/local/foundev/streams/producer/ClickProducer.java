package local.foundev.streams.producer;

import com.fasterxml.jackson.databind.ObjectMapper;
import local.foundev.streams.model.ClickEvent;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringSerializer;

import java.util.Properties;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public class ClickProducer {
    private final KafkaProducer<String, String> producer;
    private final ObjectMapper mapper = new ObjectMapper();
    private final String topic;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private ExecutorService executor;
    private final Random random = new Random();

    public ClickProducer(Properties props, String topic) {
        Properties p = new Properties();
        p.putAll(props);
        p.putIfAbsent(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        p.putIfAbsent(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        p.putIfAbsent(ProducerConfig.ACKS_CONFIG, "1");
        p.putIfAbsent(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, "false"); // Simple for demo
        this.producer = new KafkaProducer<>(p);
        this.topic = topic == null ? "clicks" : topic;
    }

    public void startBurst(long events, long delayMs) {
        running.set(true);
        executor = Executors.newSingleThreadExecutor();
        executor.submit(() -> {
            for (int i = 0; i < events && running.get(); i++) {
                ClickEvent event = generateEvent(i);
                try {
                    String json = mapper.writeValueAsString(event);
                    String key = event.getSessionId();
                    producer.send(new ProducerRecord<>(topic, key, json), (metadata, exception) -> {
                        if (exception != null) {
                            System.err.println("Send failed: " + exception.getMessage());
                        }
                    });
                } catch (Exception e) {
                    System.err.println("Error generating/sending event: " + e.getMessage());
                }
                if (delayMs > 0) {
                    try {
                        Thread.sleep(delayMs);
                    } catch (InterruptedException ex) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
            producer.flush();
        });
    }

    public void generateAndSendContinuous(long ratePerSecond) {
        running.set(true);
        executor = Executors.newSingleThreadExecutor();
        long delayMs = ratePerSecond <= 0 ? 10 : Math.max(1, 1000 / ratePerSecond);
        executor.submit(() -> {
            int counter = 0;
            while (running.get()) {
                ClickEvent event = generateEvent(counter++);
                try {
                    String json = mapper.writeValueAsString(event);
                    producer.send(new ProducerRecord<>(topic, event.getSessionId(), json), (metadata, exception) -> {
                        if (exception != null && running.get()) {
                            System.err.println("Send failed: " + exception.getMessage());
                        }
                    });
                } catch (Exception e) {
                    // continue
                }
                try {
                    Thread.sleep(delayMs);
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            producer.flush();
        });
    }

    private ClickEvent generateEvent(int idx) {
        String[] urls = {"/home", "/products", "/cart", "/checkout", "/blog/post1", "/about"};
        String[] referrers = {"google.com", "twitter.com", "direct", "news.site", null};
        String[] uas = {
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            "Googlebot/2.1 (+http://www.google.com/bot.html)",
            "curl/7.68.0"
        };
        String sessionId = "sess-" + (random.nextInt(100));
        String eventId = UUID.nameUUIDFromBytes((sessionId + "-" + idx + "-" + System.currentTimeMillis()).getBytes()).toString();
        return new ClickEvent(
                eventId,
                sessionId,
                urls[random.nextInt(urls.length)],
                referrers[random.nextInt(referrers.length)],
                uas[random.nextInt(uas.length)],
                System.currentTimeMillis(),
                random.nextBoolean() ? "US" : "EU",
                random.nextBoolean() ? "desktop" : "mobile"
        );
    }

    public void shutdown() throws InterruptedException {
        running.set(false);
        if (executor != null) {
            executor.shutdown();
            executor.awaitTermination(5, TimeUnit.SECONDS);
        }
        producer.flush();
        producer.close();
    }
}
