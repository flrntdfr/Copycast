package xyz.dufour.copycast.feed;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.refresh.RefreshService;
import xyz.dufour.copycast.util.Mime;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The public namespace. Everything a podcast client needs lives under
 * /feed/** and nothing else does, so authentication can later be added in
 * front of the UI without ever moving these URLs.
 */
@RestController
@RequestMapping("/feed")
public class FeedController {

    private static final MediaType RSS_XML = MediaType.parseMediaType("application/rss+xml;charset=UTF-8");

    /** A generated Mirror Feed, valid while the store fingerprint matches. */
    private record CachedFeed(long token, byte[] body, String etag, Instant lastModified) {
    }

    private final MirrorStore store;
    private final FeedGenerator generator;
    private final RefreshService refresh;
    private final Map<String, CachedFeed> feedCache = new ConcurrentHashMap<>();

    public FeedController(MirrorStore store, FeedGenerator generator, RefreshService refresh) {
        this.store = store;
        this.generator = generator;
        this.refresh = refresh;
    }

    @GetMapping(value = "/{id}/feed.xml")
    public ResponseEntity<byte[]> feed(@PathVariable String id,
                                       @RequestHeader(value = HttpHeaders.IF_NONE_MATCH, required = false)
                                       String ifNoneMatch) throws IOException {
        Mirror mirror = store.find(id).orElse(null);
        if (mirror == null) {
            return ResponseEntity.notFound().build();
        }
        // A fetch of the Mirror Feed triggers an asynchronous Refresh
        // (subject to the cooldown); the response is never delayed by it.
        refresh.request(id, RefreshService.Trigger.FEED_FETCH);

        MirrorStore.Fingerprint fingerprint = store.fingerprint(id);
        CachedFeed cached = feedCache.get(id);
        if (cached == null || cached.token() != fingerprint.token()) {
            byte[] body = generator.generate(mirror).getBytes(StandardCharsets.UTF_8);
            // Weak ETag: Tomcat refuses to gzip responses with a strong one
            // (the compressed bytes would no longer match it).
            cached = new CachedFeed(fingerprint.token(), body,
                    "W/\"" + sha256Hex(body).substring(0, 20) + "\"", fingerprint.lastModified());
            feedCache.put(id, cached);
        }
        if (cached.etag().equals(ifNoneMatch)) {
            return ResponseEntity.status(HttpStatus.NOT_MODIFIED)
                    .eTag(cached.etag())
                    .lastModified(cached.lastModified())
                    .cacheControl(CacheControl.noCache())
                    .build();
        }
        return ResponseEntity.ok()
                .eTag(cached.etag())
                .lastModified(cached.lastModified())
                .cacheControl(CacheControl.noCache())
                .contentType(RSS_XML)
                .body(cached.body());
    }

    @GetMapping("/{id}/media/{fileName}")
    public ResponseEntity<Resource> media(@PathVariable String id, @PathVariable String fileName) {
        if (store.find(id).isEmpty() || fileName.contains("/") || fileName.contains("..")) {
            return ResponseEntity.notFound().build();
        }
        Path file = store.episodesDir(id).resolve(fileName).normalize();
        if (!file.startsWith(store.episodesDir(id)) || !Files.isRegularFile(file)) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok()
                .header(HttpHeaders.ACCEPT_RANGES, "bytes")
                .contentType(MediaType.parseMediaType(Mime.forFileName(fileName)))
                .body(new FileSystemResource(file));
    }

    private static String sha256Hex(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
