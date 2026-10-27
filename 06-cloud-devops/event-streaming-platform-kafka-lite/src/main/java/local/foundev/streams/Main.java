package local.foundev.streams;

import local.foundev.streams.api.ViewServer;
import local.foundev.streams.producer.ClickProducer;
import local.foundev.streams.registry.FakeSchemaRegistry;
import local.foundev.streams.streams.ClicksStreamsApp;

import java.io.IOException;
import java.util.Properties;

public class Main {
    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            printUsage();
            return;
        }
        String cmd = args[0];
        switch (cmd) {
            case "fake-registry" -> {
                FakeSchemaRegistry reg = new FakeSchemaRegistry(8081);
                reg.start();
                System.out.println("Fake Schema Registry started on " + reg.getBaseUrl());
                Runtime.getRuntime().addShutdownHook(new Thread(reg::stop));
                Thread.currentThread().join();
            }
            case "producer" -> {
                Properties props = new Properties();
                props.setProperty("bootstrap.servers", getOrDefault(args, 1, "localhost:9092"));
                ClickProducer producer = new ClickProducer(props, "clicks");
                producer.generateAndSendContinuous(10);
                Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                    try { producer.shutdown(); } catch (InterruptedException ignored) {}
                }));
                Thread.currentThread().join();
            }
            case "streams" -> {
                Properties props = new Properties();
                props.setProperty("bootstrap.servers", getOrDefault(args, 1, "localhost:9092"));
                ClicksStreamsApp streams = new ClicksStreamsApp(props);
                streams.start();
                Runtime.getRuntime().addShutdownHook(new Thread(streams::shutdown));
                Thread.currentThread().join();
            }
            case "view" -> {
                int port = Integer.parseInt(getOrDefault(args, 1, "8082"));
                ViewServer server = new ViewServer(port);
                server.start();
                System.out.println("ViewServer started on http://localhost:" + port);
                Runtime.getRuntime().addShutdownHook(new Thread(server::stop));
                Thread.currentThread().join();
            }
            case "all-local" -> {
                FakeSchemaRegistry reg = new FakeSchemaRegistry(8081);
                reg.start();
                ViewServer view = new ViewServer(8082);
                view.start();
                System.out.println("all-local mode: fake-registry on 8081, view-server on 8082");
                Runtime.getRuntime().addShutdownHook(new Thread(() -> { reg.stop(); view.stop(); }));
                Thread.currentThread().join();
            }
            default -> printUsage();
        }
    }

    private static String getOrDefault(String[] args, int idx, String def) {
        return args.length > idx ? args[idx] : def;
    }

    private static void printUsage() {
        System.out.println("Usage: java local.foundev.streams.Main <producer|streams|view|fake-registry|all-local> [args]");
    }
}
