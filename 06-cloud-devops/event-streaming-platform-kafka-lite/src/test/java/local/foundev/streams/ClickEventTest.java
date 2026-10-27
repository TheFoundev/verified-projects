package local.foundev.streams;

import local.foundev.streams.model.ClickEvent;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

public class ClickEventTest {
    @Test
    public void testClickEventCreation() {
        ClickEvent event = new ClickEvent("e1", "s1", "/home", "google.com", "Mozilla", System.currentTimeMillis(), "US", "desktop");
        assertEquals("e1", event.getEventId());
        assertEquals("s1", event.getSessionId());
        assertEquals("/home", event.getUrl());
        assertEquals("google.com", event.getReferrer());
    }
}
