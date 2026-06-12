package xyz.dufour.copycast.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.nio.file.Path;

@ConfigurationProperties(prefix = "copycast")
public record CopycastProperties(
        String baseUrl,
        Path dataDir,
        int refreshHours,
        int fetchCooldownMinutes,
        YtDlpProperties ytdlp) {

    public record YtDlpProperties(String version, boolean autoDownload) {
    }

    /** Base URL without trailing slash, for building public links. */
    public String normalizedBaseUrl() {
        String url = baseUrl == null ? "" : baseUrl.trim();
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }
}
