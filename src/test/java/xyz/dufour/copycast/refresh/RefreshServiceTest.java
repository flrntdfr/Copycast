package xyz.dufour.copycast.refresh;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import xyz.dufour.copycast.TestSupport;
import xyz.dufour.copycast.mirror.Episode;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.util.Ids;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Exercises the refresh pipeline end to end with a fake yt-dlp binary (a
 * shell script staged where {@link YtDlp} expects the pinned version) and a
 * local HTTP server standing in for the Source.
 */
class RefreshServiceTest {

    @TempDir
    Path dataDir;

    MirrorStore store;
    RefreshService service;
    HttpServer server;

    /**
     * Fake yt-dlp: logs every invocation, answers {@code -J} probes with
     * bin/listing.json, and in download mode materializes files from the
     * {@code -o} template — one per id in bin/ids.txt for playlist templates,
     * or a single mp3 otherwise.
     */
    private static final String FAKE_YTDLP = """
            #!/bin/sh
            DIR=$(dirname "$0")
            echo "$*" >> "$DIR/calls.log"
            if [ "$1" = "-J" ]; then
              cat "$DIR/listing.json"
              exit 0
            fi
            TEMPLATE=""
            prev=""
            for a in "$@"; do
              if [ "$prev" = "-o" ]; then TEMPLATE="$a"; fi
              prev="$a"
            done
            [ -n "$TEMPLATE" ] || exit 1
            if [ -f "$DIR/slow" ]; then sleep 10; fi
            case "$TEMPLATE" in
              *'%(id)s'*)
                while IFS= read -r id; do
                  f=$(printf '%s' "$TEMPLATE" | sed "s/%(id)s/$id/g; s/%(ext)s/m4a/g")
                  printf 'audio' > "$f"
                  base=${f%.m4a}
                  printf '{"id":"%s","title":"Video %s","upload_date":"20260601","duration":61,"webpage_url":"https://yt.example/w/%s"}' "$id" "$id" "$id" > "$base.info.json"
                  printf 'thumb' > "$base.webp"
                done < "$DIR/ids.txt"
                ;;
              *)
                f=$(printf '%s' "$TEMPLATE" | sed "s/%(ext)s/mp3/g")
                printf 'audio-bytes' > "$f"
                ;;
            esac
            exit 0
            """;

    @BeforeEach
    void setUp() throws IOException {
        var props = TestSupport.props(dataDir);
        var mapper = TestSupport.mapper();
        store = new MirrorStore(props, mapper);
        Path bin = dataDir.resolve("bin");
        Files.createDirectories(bin);
        Path script = bin.resolve("yt-dlp-" + TestSupport.YTDLP_VERSION);
        Files.writeString(script, FAKE_YTDLP);
        assertTrue(script.toFile().setExecutable(true));
        service = new RefreshService(store, new YtDlp(props), mapper, props);
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    private String serveFeed(String bodyTemplate, int status) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        // The feed template may reference artwork via {base}.
        String body = bodyTemplate.replace("{base}", base);
        server.createContext("/feed.xml", exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.createContext("/art/", exchange -> {
            byte[] bytes = "img".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type",
                    exchange.getRequestURI().getPath().endsWith(".png") ? "image/png" : "image/jpeg");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        return base + "/feed.xml";
    }

    private static String rssFeed(String title, String... guids) {
        StringBuilder items = new StringBuilder();
        for (String guid : guids) {
            items.append("""
                    <item>
                      <title>Episode %s</title>
                      <guid isPermaLink="false">%s</guid>
                      <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
                      <itunes:image href="{base}/art/%s.jpg"/>
                      <enclosure url="https://pod.example/%s.mp3" length="1" type="audio/mpeg"/>
                    </item>
                    """.formatted(guid, guid, guid, guid));
        }
        return """
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
                  <title>%s</title>
                  <description>d</description>
                  <itunes:image href="{base}/art/cover.png"/>
                  %s
                </channel></rss>
                """.formatted(title, items);
    }

    private Mirror createRssMirror(String url, Integer cap) throws IOException {
        return store.create(url, new ProbeResult(true, url, SourceType.RSS, "RSS", "Stale Title",
                null, null, null, 0, null), cap);
    }

    private Mirror awaitRefresh(String id, Instant previousAttempt) throws InterruptedException {
        long deadline = System.currentTimeMillis() + 15_000;
        while (System.currentTimeMillis() < deadline) {
            Mirror mirror = store.find(id).orElseThrow();
            Instant attempt = mirror.getLastAttemptAt();
            boolean advanced = attempt != null
                    && (previousAttempt == null || attempt.isAfter(previousAttempt));
            if (advanced && !service.isBusy(id)) {
                return mirror;
            }
            Thread.sleep(50);
        }
        throw new AssertionError("Refresh did not complete in time");
    }

    @Test
    void rssRefreshArchivesEpisodesAndCapturesMetadata() throws Exception {
        String url = serveFeed(rssFeed("Live Pod", "ep-1", "ep-2"), 200);
        Mirror mirror = createRssMirror(url, null);

        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        Mirror after = awaitRefresh(mirror.getId(), null);

        assertNull(after.getLastError());
        assertNotNull(after.getLastSuccessAt());
        assertEquals("Live Pod", after.getTitle());
        assertTrue(Files.isRegularFile(store.feedXml(mirror.getId())));

        List<Episode> episodes = store.episodes(after);
        assertEquals(2, episodes.size());
        for (String guid : List.of("ep-1", "ep-2")) {
            String key = Ids.episodeKey(guid);
            assertTrue(store.findAudio(mirror.getId(), key).isPresent());
            // Metadata captured permanently at archive time (union semantics).
            assertTrue(Files.isRegularFile(store.episodesDir(mirror.getId()).resolve(key + ".item.xml")));
            // Episode Artwork archived alongside the audio.
            assertTrue(store.findArtwork(mirror.getId(), key).isPresent());
        }
        assertTrue(episodes.stream().noneMatch(Episode::delisted));

        // Channel Artwork archived with the extension from its content type.
        assertTrue(Files.isRegularFile(store.episodesDir(mirror.getId()).resolve("cover.png")));
    }

    @Test
    void capLimitsHowMuchBacklogIsArchived() throws Exception {
        String url = serveFeed(rssFeed("Live Pod", "ep-1", "ep-2", "ep-3"), 200);
        Mirror mirror = createRssMirror(url, 1);

        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        awaitRefresh(mirror.getId(), null);

        assertEquals(1, store.episodes(store.find(mirror.getId()).orElseThrow()).size());
        assertTrue(store.findAudio(mirror.getId(), Ids.episodeKey("ep-1")).isPresent());
    }

    @Test
    void failedRefreshRecordsTheErrorAndNeverDegradesTheArchive() throws Exception {
        // Port 1 on localhost: connection refused immediately.
        Mirror mirror = createRssMirror("http://127.0.0.1:1/feed.xml", null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("k.mp3"), "precious");

        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        Mirror after = awaitRefresh(mirror.getId(), null);

        assertNotNull(after.getLastError());
        assertNull(after.getLastSuccessAt());
        assertEquals("Stale Title", after.getTitle());
        assertEquals(1, store.episodes(after).size());
        assertEquals("precious", Files.readString(store.episodesDir(mirror.getId()).resolve("k.mp3")));
    }

    @Test
    void feedFetchTriggerIsThrottledByTheCooldown() throws Exception {
        Mirror mirror = createRssMirror("http://127.0.0.1:1/feed.xml", null);
        Instant recent = Instant.now();
        mirror.setLastAttemptAt(recent);
        store.save(mirror);

        service.request(mirror.getId(), RefreshService.Trigger.FEED_FETCH);
        Thread.sleep(300);

        assertFalse(service.isBusy(mirror.getId()));
        assertEquals(recent, store.find(mirror.getId()).orElseThrow().getLastAttemptAt());
        assertFalse(Files.exists(dataDir.resolve("bin").resolve("calls.log")));
    }

    @Test
    void manualTriggerBypassesCooldownAndPause() throws Exception {
        String url = serveFeed(rssFeed("Live Pod", "ep-1"), 200);
        Mirror mirror = createRssMirror(url, null);
        Instant recent = Instant.now();
        mirror.setLastAttemptAt(recent);
        mirror.setPaused(true);
        store.save(mirror);

        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        Mirror after = awaitRefresh(mirror.getId(), recent);

        assertNotNull(after.getLastSuccessAt());
        assertEquals(1, store.episodes(after).size());
    }

    @Test
    void pausingCancelsTheRunningRefreshAndKillsYtDlp() throws Exception {
        // The fake yt-dlp sleeps 10s per download when this marker exists.
        Files.writeString(dataDir.resolve("bin").resolve("slow"), "");
        String url = serveFeed(rssFeed("Live Pod", "ep-1"), 200);
        Mirror mirror = createRssMirror(url, null);

        long start = System.currentTimeMillis();
        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        while (!service.isBusy(mirror.getId()) && System.currentTimeMillis() - start < 2000) {
            Thread.sleep(20);
        }
        Thread.sleep(300);

        Mirror paused = store.find(mirror.getId()).orElseThrow();
        paused.setPaused(true);
        store.save(paused);
        service.cancel(mirror.getId());

        Mirror after = awaitRefresh(mirror.getId(), null);
        long elapsed = System.currentTimeMillis() - start;
        // Well under the fake download's 10s sleep: the process was killed.
        assertTrue(elapsed < 8000, "refresh should end quickly after cancel, took " + elapsed + " ms");
        assertTrue(store.findAudio(mirror.getId(), Ids.episodeKey("ep-1")).isEmpty());
        assertNull(after.getLastSuccessAt());
        assertNotNull(after.getLastError());
        assertTrue(after.getLastError().contains("Paused"));
    }

    @Test
    void scheduledScanSkipsPausedMirrors() throws Exception {
        Mirror mirror = createRssMirror("http://127.0.0.1:1/feed.xml", null);
        mirror.setPaused(true);
        store.save(mirror);

        service.scheduledScan();
        Thread.sleep(300);

        assertFalse(service.isBusy(mirror.getId()));
        assertNull(store.find(mirror.getId()).orElseThrow().getLastAttemptAt());
    }

    @Test
    void ytdlpRefreshStoresListingDownloadsEpisodesAndComputesDelisted() throws Exception {
        Path bin = dataDir.resolve("bin");
        Files.writeString(bin.resolve("listing.json"),
                "{\"id\":\"chan\",\"title\":\"Chan Title\",\"uploader\":\"Up\","
                        + "\"entries\":[{\"id\":\"vid1\"},{\"id\":\"vid2\"}]}");
        Files.writeString(bin.resolve("ids.txt"), "vid1\nvid2\n");
        Mirror mirror = store.create("https://yt.example/c/chan",
                new ProbeResult(true, "https://yt.example/c/chan", SourceType.YTDLP, "YouTube", "Old", null, null, null, 2, null), null);

        service.request(mirror.getId(), RefreshService.Trigger.MANUAL);
        Mirror after = awaitRefresh(mirror.getId(), null);

        assertNull(after.getLastError());
        assertEquals("Chan Title", after.getTitle());
        assertEquals("Up", after.getAuthor());
        assertTrue(Files.isRegularFile(store.listingJson(mirror.getId())));

        List<Episode> episodes = store.episodes(after);
        assertEquals(2, episodes.size());
        Episode first = episodes.getFirst();
        assertEquals("audio/mp4", first.mimeType());
        assertTrue(first.title().startsWith("Video vid"));
        assertTrue(episodes.stream().noneMatch(Episode::delisted));
        // --write-thumbnail produced per-episode Artwork.
        assertTrue(store.findArtwork(mirror.getId(), "vid1").isPresent());

        // An Episode the Source no longer lists shows as Delisted in the UI…
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid3.m4a"), "x");
        List<Episode> withStale = store.episodes(after);
        assertEquals(3, withStale.size());
        assertTrue(withStale.stream().filter(e -> e.key().equals("vid3"))
                .findFirst().orElseThrow().delisted());
    }
}
