package xyz.dufour.copycast.feed;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
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

/**
 * The public namespace. Everything a podcast client needs lives under
 * /feed/** and nothing else does, so authentication can later be added in
 * front of the UI without ever moving these URLs.
 */
@RestController
@RequestMapping("/feed")
public class FeedController {

    private final MirrorStore store;
    private final FeedGenerator generator;
    private final RefreshService refresh;

    public FeedController(MirrorStore store, FeedGenerator generator, RefreshService refresh) {
        this.store = store;
        this.generator = generator;
        this.refresh = refresh;
    }

    @GetMapping(value = "/{id}/feed.xml", produces = "application/rss+xml;charset=UTF-8")
    public ResponseEntity<byte[]> feed(@PathVariable String id) throws IOException {
        Mirror mirror = store.find(id).orElse(null);
        if (mirror == null) {
            return ResponseEntity.notFound().build();
        }
        // A fetch of the Mirror Feed triggers an asynchronous Refresh
        // (subject to the cooldown); the response is never delayed by it.
        refresh.request(id, RefreshService.Trigger.FEED_FETCH);
        return ResponseEntity.ok(generator.generate(mirror).getBytes(StandardCharsets.UTF_8));
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
}
