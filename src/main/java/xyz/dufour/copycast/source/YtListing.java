package xyz.dufour.copycast.source;

import tools.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Helpers for yt-dlp flat-playlist listings. A YouTube channel comes back as
 * a playlist of tab-playlists (Videos, Shorts, Live), so naive counting and
 * id collection see the tabs, not the videos — always flatten first.
 */
public final class YtListing {

    private static final Map<String, String> SERVICE_NAMES = Map.of(
            "youtube", "YouTube",
            "soundcloud", "SoundCloud",
            "vimeo", "Vimeo",
            "twitch", "Twitch",
            "bandcamp", "Bandcamp",
            "bilibili", "BiliBili");

    private YtListing() {
    }

    /** All leaf entries (actual videos/tracks), however deeply nested. */
    public static List<JsonNode> leafEntries(JsonNode root) {
        List<JsonNode> leaves = new ArrayList<>();
        collect(root, leaves);
        return leaves;
    }

    private static void collect(JsonNode node, List<JsonNode> out) {
        if (node.path("entries").isArray()) {
            node.path("entries").forEach(entry -> collect(entry, out));
        } else if (node.hasNonNull("id")) {
            out.add(node);
        }
    }

    /** Human-readable service name from the yt-dlp extractor, e.g. "YouTube". */
    public static String serviceName(JsonNode root) {
        String extractor = root.path("extractor").asString(null);
        if (extractor == null || extractor.isBlank()) {
            return null;
        }
        String base = extractor.split(":")[0].trim().toLowerCase(Locale.ROOT);
        String known = SERVICE_NAMES.get(base);
        if (known != null) {
            return known;
        }
        return base.substring(0, 1).toUpperCase(Locale.ROOT) + base.substring(1);
    }
}
