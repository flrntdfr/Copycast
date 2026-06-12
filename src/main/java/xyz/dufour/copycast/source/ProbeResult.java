package xyz.dufour.copycast.source;

import xyz.dufour.copycast.mirror.SourceType;

/** Outcome of probing a pasted URL before creating a Mirror. */
public record ProbeResult(
        boolean supported,
        SourceType type,
        String service,
        String title,
        String description,
        String imageUrl,
        String author,
        int episodeCount,
        String error) {

    public static ProbeResult unsupported(String error) {
        return new ProbeResult(false, null, null, null, null, null, null, 0, error);
    }
}
