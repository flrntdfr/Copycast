package xyz.dufour.copycast.mirror;

import java.time.Instant;

/**
 * One item of a Mirror's catalog as shown on the detail page: every episode
 * the Source advertises, plus anything archived. Its {@link State} says
 * whether it is in the published Mirror Feed.
 */
public record CatalogItem(
        String key,
        String title,
        String description,
        Instant pubDate,
        State state,
        long sizeBytes,
        String audioFileName,
        String artworkFileName,
        String remoteImageUrl) {

    public enum State {
        /** Archived and present in the current Source feed — in the Mirror Feed. */
        LISTED,
        /** Archived but the Source no longer lists it — kept in the Mirror Feed. */
        DELISTED,
        /** Advertised by the Source but not archived — absent from the Mirror Feed. */
        AVAILABLE
    }

    public boolean archived() {
        return state != State.AVAILABLE;
    }
}
