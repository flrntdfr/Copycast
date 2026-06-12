package xyz.dufour.copycast.mirror;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.time.Instant;

/**
 * The descriptor of a Mirror, persisted as mirror.json in the Mirror's data
 * directory. The id is minted from the Source URL at creation and frozen;
 * the Source URL itself may be retargeted later without changing the id.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class Mirror {

    private String id;
    private String sourceUrl;
    private SourceType type;
    private String service;
    private String title;
    private String description;
    private String imageUrl;
    private String author;
    private Integer cap;
    private boolean paused;
    private Instant createdAt;
    private Instant lastAttemptAt;
    private Instant lastSuccessAt;
    private String lastError;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getSourceUrl() {
        return sourceUrl;
    }

    public void setSourceUrl(String sourceUrl) {
        this.sourceUrl = sourceUrl;
    }

    public SourceType getType() {
        return type;
    }

    public void setType(SourceType type) {
        this.type = type;
    }

    public String getService() {
        return service;
    }

    public void setService(String service) {
        this.service = service;
    }

    /** Service name for display, e.g. "RSS" or "YouTube". */
    public String displayService() {
        if (service != null && !service.isBlank()) {
            return service;
        }
        return type == null ? "" : type.name();
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public String getImageUrl() {
        return imageUrl;
    }

    public void setImageUrl(String imageUrl) {
        this.imageUrl = imageUrl;
    }

    public String getAuthor() {
        return author;
    }

    public void setAuthor(String author) {
        this.author = author;
    }

    public Integer getCap() {
        return cap;
    }

    public void setCap(Integer cap) {
        this.cap = cap;
    }

    public boolean isPaused() {
        return paused;
    }

    public void setPaused(boolean paused) {
        this.paused = paused;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public Instant getLastAttemptAt() {
        return lastAttemptAt;
    }

    public void setLastAttemptAt(Instant lastAttemptAt) {
        this.lastAttemptAt = lastAttemptAt;
    }

    public Instant getLastSuccessAt() {
        return lastSuccessAt;
    }

    public void setLastSuccessAt(Instant lastSuccessAt) {
        this.lastSuccessAt = lastSuccessAt;
    }

    public String getLastError() {
        return lastError;
    }

    public void setLastError(String lastError) {
        this.lastError = lastError;
    }

    public String displayTitle() {
        return title != null && !title.isBlank() ? title : sourceUrl;
    }
}
