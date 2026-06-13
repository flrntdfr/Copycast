package xyz.dufour.copycast.util;

import java.net.URI;
import java.util.Arrays;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * URL helpers for duplicate detection. {@link #dedupKey} reduces a Source
 * URL to a canonical comparison key so that trivially different URLs for the
 * same feed — scheme, {@code www.}, trailing slash, host case, tracking
 * parameters, query order — map to one Mirror. It is a comparison key only:
 * the real, untouched URL is still used for fetching (so auth tokens in the
 * query survive).
 */
public final class Urls {

    private static final Set<String> TRACKING_PARAMS =
            Set.of("fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src");

    private Urls() {
    }

    public static String dedupKey(String url) {
        if (url == null) {
            return "";
        }
        String trimmed = url.trim();
        try {
            URI uri = URI.create(trimmed);
            String host = uri.getHost();
            if (host == null) {
                return trimmed.toLowerCase(Locale.ROOT);
            }
            host = host.toLowerCase(Locale.ROOT);
            if (host.startsWith("www.")) {
                host = host.substring(4);
            }
            // Scheme is intentionally omitted: the same feed served over http
            // and https is one show.
            String path = uri.getPath() == null ? "" : uri.getPath();
            // Strip trailing slashes so "/rss", "/rss/" and "/" (root) all align.
            while (path.endsWith("/")) {
                path = path.substring(0, path.length() - 1);
            }
            String query = canonicalQuery(uri.getRawQuery());
            return host + path + (query.isEmpty() ? "" : "?" + query);
        } catch (Exception e) {
            return trimmed.toLowerCase(Locale.ROOT);
        }
    }

    private static String canonicalQuery(String rawQuery) {
        if (rawQuery == null || rawQuery.isBlank()) {
            return "";
        }
        return Arrays.stream(rawQuery.split("&"))
                .filter(p -> !p.isBlank())
                .filter(p -> !isTracking(p.split("=", 2)[0]))
                .sorted()
                .collect(Collectors.joining("&"));
    }

    private static boolean isTracking(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return lower.startsWith("utm_") || TRACKING_PARAMS.contains(lower);
    }
}
