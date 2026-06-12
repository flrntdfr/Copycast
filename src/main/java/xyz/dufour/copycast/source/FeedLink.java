package xyz.dufour.copycast.source;

import java.net.URI;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * RSS autodiscovery: finds the feed a web page advertises in its head, e.g.
 * {@code <link rel="alternate" type="application/rss+xml" href="/rss">},
 * so users can paste the podcast's site instead of the feed URL.
 */
public final class FeedLink {

    private static final Pattern LINK_TAG = Pattern.compile("(?is)<link\\b[^>]*>");
    private static final int SCAN_LIMIT = 262_144;

    private FeedLink() {
    }

    /** The first advertised RSS feed URL, resolved against the page URL. */
    public static String discover(String html, String pageUrl) {
        String head = html.length() > SCAN_LIMIT ? html.substring(0, SCAN_LIMIT) : html;
        Matcher links = LINK_TAG.matcher(head);
        while (links.find()) {
            String tag = links.group();
            String rel = attribute(tag, "rel");
            String type = attribute(tag, "type");
            String href = attribute(tag, "href");
            if (rel != null && rel.toLowerCase().contains("alternate")
                    && type != null && type.trim().equalsIgnoreCase("application/rss+xml")
                    && href != null && !href.isBlank()) {
                String resolved = resolve(pageUrl, unescape(href));
                if (resolved != null) {
                    return resolved;
                }
            }
        }
        return null;
    }

    private static String attribute(String tag, String name) {
        Matcher matcher = Pattern
                .compile("(?i)\\b" + name + "\\s*=\\s*(\"([^\"]*)\"|'([^']*)'|([^\\s>]+))")
                .matcher(tag);
        if (!matcher.find()) {
            return null;
        }
        if (matcher.group(2) != null) {
            return matcher.group(2);
        }
        if (matcher.group(3) != null) {
            return matcher.group(3);
        }
        return matcher.group(4);
    }

    private static String unescape(String href) {
        return href.replace("&amp;", "&").replace("&#38;", "&").trim();
    }

    private static String resolve(String base, String href) {
        try {
            return URI.create(base).resolve(href).toString();
        } catch (Exception e) {
            return null;
        }
    }
}
