package xyz.dufour.copycast.source;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.w3c.dom.Document;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.util.Http;
import xyz.dufour.copycast.util.XmlUtil;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * Validates a pasted URL before a Mirror is created, as forgivingly as
 * possible: a missing scheme is inferred (https preferred), a pasted web
 * page is checked for an advertised RSS feed, and everything else is
 * checked against yt-dlp's extractors.
 */
@Service
public class ProbeService {

    private static final Logger log = LoggerFactory.getLogger(ProbeService.class);

    private final YtDlp ytDlp;
    private final ObjectMapper mapper;
    private final CopycastProperties props;

    public ProbeService(YtDlp ytDlp, ObjectMapper mapper, CopycastProperties props) {
        this.ytDlp = ytDlp;
        this.mapper = mapper;
        this.props = props;
    }

    public ProbeResult probe(String input) {
        List<String> candidates = candidateUrls(input);
        if (candidates.isEmpty()) {
            return ProbeResult.unsupported("Please enter a URL");
        }
        for (String candidate : candidates) {
            Http.Content content;
            try {
                content = Http.getContent(candidate, Duration.ofSeconds(60));
            } catch (Exception e) {
                log.debug("Unreachable candidate {}: {}", candidate, e.toString());
                continue;
            }
            // Pasted the feed itself?
            Optional<ProbeResult> rss = parseRss(content.finalUrl(), content.bytes());
            if (rss.isPresent()) {
                return rss.get();
            }
            // Pasted the podcast's site? Follow its advertised feed. A page
            // whose advertised feed isn't RSS 2.0 (e.g. YouTube's Atom feed)
            // falls through to yt-dlp on the page itself.
            String feedUrl = FeedLink.discover(
                    new String(content.bytes(), StandardCharsets.UTF_8), content.finalUrl());
            if (feedUrl != null) {
                try {
                    Http.Content feed = Http.getContent(feedUrl, Duration.ofSeconds(60));
                    Optional<ProbeResult> discovered = parseRss(feed.finalUrl(), feed.bytes());
                    if (discovered.isPresent()) {
                        return discovered.get();
                    }
                } catch (Exception e) {
                    log.debug("Advertised feed {} not usable: {}", feedUrl, e.toString());
                }
            }
            return probeYtDlp(candidate);
        }
        // Nothing reachable over plain HTTP; yt-dlp has its own networking.
        return probeYtDlp(candidates.getFirst());
    }

    /** Scheme-inferred fetch candidates, https before http. */
    static List<String> candidateUrls(String input) {
        String trimmed = input == null ? "" : input.trim();
        if (trimmed.isEmpty()) {
            return List.of();
        }
        if (trimmed.toLowerCase(Locale.ROOT).matches("^https?://.*")) {
            return List.of(trimmed);
        }
        return List.of("https://" + trimmed, "http://" + trimmed);
    }

    private Optional<ProbeResult> parseRss(String canonicalUrl, byte[] body) {
        try {
            Document doc = XmlUtil.parse(body);
            return Rss.parse(doc).map(channel -> new ProbeResult(
                    true, canonicalUrl, SourceType.RSS, "RSS",
                    channel.title(), channel.description(), channel.imageUrl(), channel.author(),
                    channel.items().size(), null));
        } catch (Exception e) {
            return Optional.empty();
        }
    }

    private ProbeResult probeYtDlp(String url) {
        try {
            YtDlp.Result result = ytDlp.run(props.dataDir(), Duration.ofMinutes(10),
                    List.of("-J", "--flat-playlist", "--no-warnings", url));
            if (!result.ok()) {
                return ProbeResult.unsupported("yt-dlp does not support this URL: " + result.stderrTail());
            }
            JsonNode root = mapper.readTree(result.stdout());
            return new ProbeResult(true, url, SourceType.YTDLP,
                    YtListing.serviceName(root),
                    root.path("title").asString(null),
                    root.path("description").asString(null),
                    thumbnail(root),
                    root.path("uploader").asString(root.path("channel").asString(null)),
                    YtListing.leafEntries(root).size(), null);
        } catch (Exception e) {
            return ProbeResult.unsupported("Probe failed: " + e.getMessage());
        }
    }

    public static String thumbnail(JsonNode root) {
        if (root.hasNonNull("thumbnail")) {
            return root.path("thumbnail").asString();
        }
        JsonNode thumbnails = root.path("thumbnails");
        if (thumbnails.isArray() && !thumbnails.isEmpty()) {
            return thumbnails.get(thumbnails.size() - 1).path("url").asString(null);
        }
        return null;
    }
}
