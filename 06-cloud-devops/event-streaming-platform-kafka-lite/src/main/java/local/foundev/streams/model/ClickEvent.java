package local.foundev.streams.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Objects;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ClickEvent {
    @JsonProperty("event_id")
    private String eventId;
    @JsonProperty("session_id")
    private String sessionId;
    private String url;
    private String referrer;
    @JsonProperty("user_agent")
    private String userAgent;
    private long ts;
    private String geo;
    private String device;

    public ClickEvent() {
    }

    public ClickEvent(String eventId, String sessionId, String url, String referrer, String userAgent, long ts, String geo, String device) {
        this.eventId = eventId;
        this.sessionId = sessionId;
        this.url = url;
        this.referrer = referrer;
        this.userAgent = userAgent;
        this.ts = ts;
        this.geo = geo;
        this.device = device;
    }

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public String getUrl() {
        return url;
    }

    public void setUrl(String url) {
        this.url = url;
    }

    public String getReferrer() {
        return referrer;
    }

    public void setReferrer(String referrer) {
        this.referrer = referrer;
    }

    public String getUserAgent() {
        return userAgent;
    }

    public void setUserAgent(String userAgent) {
        this.userAgent = userAgent;
    }

    public long getTs() {
        return ts;
    }

    public void setTs(long ts) {
        this.ts = ts;
    }

    public String getGeo() {
        return geo;
    }

    public void setGeo(String geo) {
        this.geo = geo;
    }

    public String getDevice() {
        return device;
    }

    public void setDevice(String device) {
        this.device = device;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof ClickEvent)) return false;
        ClickEvent that = (ClickEvent) o;
        return ts == that.ts && Objects.equals(eventId, that.eventId) && Objects.equals(sessionId, that.sessionId) && Objects.equals(url, that.url);
    }

    @Override
    public int hashCode() {
        return Objects.hash(eventId, sessionId, url, ts);
    }
}
