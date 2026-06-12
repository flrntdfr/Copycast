package xyz.dufour.copycast.mirror;

import java.nio.file.Path;
import java.time.Instant;

/**
 * One archived Episode, reconstructed from the files on disk. Metadata was
 * captured at archive time and is permanent; {@code delisted} means the
 * Source no longer lists this Episode (surfaced in the UI only — the Mirror
 * Feed always contains the union of everything archived).
 */
public record Episode(
        String key,
        String title,
        String description,
        Instant pubDate,
        Path audioFile,
        long sizeBytes,
        String mimeType,
        Long durationSeconds,
        String guid,
        boolean delisted) {

    public String fileName() {
        return audioFile.getFileName().toString();
    }
}
