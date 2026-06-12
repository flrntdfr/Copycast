package xyz.dufour.copycast.source;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.w3c.dom.Document;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.util.Http;
import xyz.dufour.copycast.util.XmlUtil;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.time.Duration;
import java.util.List;
import java.util.Optional;

/**
 * Validates a pasted URL before a Mirror is created: RSS feeds are detected
 * by fetching and parsing them directly; everything else is checked against
 * yt-dlp's extractors via a flat-playlist listing.
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

    public ProbeResult probe(String url) {
        Optional<ProbeResult> rss = probeRss(url);
        if (rss.isPresent()) {
            return rss.get();
        }
        return probeYtDlp(url);
    }

    private Optional<ProbeResult> probeRss(String url) {
        try {
            byte[] body = Http.get(url, Duration.ofSeconds(60));
            Document doc = XmlUtil.parse(body);
            return Rss.parse(doc).map(channel -> new ProbeResult(
                    true, SourceType.RSS,
                    channel.title(), channel.description(), channel.imageUrl(), channel.author(),
                    channel.items().size(), null));
        } catch (Exception e) {
            log.debug("Not an RSS feed ({}): {}", url, e.getMessage());
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
            int count = root.has("entries") ? root.path("entries").size() : 1;
            return new ProbeResult(true, SourceType.YTDLP,
                    root.path("title").asText(null),
                    root.path("description").asText(null),
                    thumbnail(root),
                    root.path("uploader").asText(root.path("channel").asText(null)),
                    count, null);
        } catch (Exception e) {
            return ProbeResult.unsupported("Probe failed: " + e.getMessage());
        }
    }

    public static String thumbnail(JsonNode root) {
        if (root.hasNonNull("thumbnail")) {
            return root.path("thumbnail").asText();
        }
        JsonNode thumbnails = root.path("thumbnails");
        if (thumbnails.isArray() && !thumbnails.isEmpty()) {
            return thumbnails.get(thumbnails.size() - 1).path("url").asText(null);
        }
        return null;
    }
}
