package xyz.dufour.copycast.source;

import xyz.dufour.copycast.mirror.SourceType;

/**
 * Outcome of probing a pasted URL before creating a Mirror. {@code url} is
 * the resolved canonical Source URL — scheme added, redirects followed, and
 * feeds discovered from HTML pages — which the Mirror is created with.
 */
public record ProbeResult(
        boolean supported,
        String url,
        SourceType type,
        String service,
        String title,
        String description,
        String imageUrl,
        String author,
        int episodeCount,
        String error) {

    public static ProbeResult unsupported(String error) {
        return new ProbeResult(false, null, null, null, null, null, null, null, 0, error);
    }
}
