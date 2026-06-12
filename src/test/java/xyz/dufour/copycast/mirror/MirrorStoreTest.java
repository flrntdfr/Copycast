package xyz.dufour.copycast.mirror;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import xyz.dufour.copycast.TestSupport;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.util.Ids;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MirrorStoreTest {

    @TempDir
    Path dataDir;

    MirrorStore store;

    @BeforeEach
    void setUp() {
        store = new MirrorStore(TestSupport.props(dataDir), TestSupport.mapper());
    }

    private static ProbeResult rssProbe(String title) {
        return new ProbeResult(true, SourceType.RSS, "RSS", title, "Desc",
                "https://img.example/c.png", "Jane", 3, null);
    }

    @Test
    void createAndFindRoundtrip() throws IOException {
        Mirror created = store.create("https://pod.example/feed.xml", rssProbe("My Pod"), 5);
        assertEquals(Ids.mirrorId("https://pod.example/feed.xml"), created.getId());

        Mirror loaded = store.find(created.getId()).orElseThrow();
        assertEquals("My Pod", loaded.getTitle());
        assertEquals("https://pod.example/feed.xml", loaded.getSourceUrl());
        assertEquals(SourceType.RSS, loaded.getType());
        assertEquals(5, loaded.getCap());
        assertFalse(loaded.isPaused());
        assertNotNull(loaded.getCreatedAt());
        assertTrue(Files.isDirectory(store.episodesDir(created.getId())));
    }

    @Test
    void findBySourceUrlMatchesExactly() throws IOException {
        store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        assertTrue(store.findBySourceUrl("https://pod.example/feed.xml").isPresent());
        assertTrue(store.findBySourceUrl("https://pod.example/other.xml").isEmpty());
    }

    @Test
    void listSortsByTitleCaseInsensitively() throws IOException {
        store.create("https://a.example/1", rssProbe("banana"), null);
        store.create("https://a.example/2", rssProbe("Apple"), null);
        store.create("https://a.example/3", rssProbe("cherry"), null);
        List<String> titles = store.list().stream().map(Mirror::getTitle).toList();
        assertEquals(List.of("Apple", "banana", "cherry"), titles);
    }

    @Test
    void saveUpdatesDescriptorAtomically() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        mirror.setPaused(true);
        mirror.setLastError("boom");
        mirror.setLastAttemptAt(Instant.parse("2026-06-12T10:00:00Z"));
        store.save(mirror);

        Mirror loaded = store.find(mirror.getId()).orElseThrow();
        assertTrue(loaded.isPaused());
        assertEquals("boom", loaded.getLastError());
        assertEquals(Instant.parse("2026-06-12T10:00:00Z"), loaded.getLastAttemptAt());
    }

    @Test
    void deleteRemovesTheWholeMirrorDirectory() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("k.mp3"), "audio");
        store.delete(mirror.getId());
        assertFalse(Files.exists(store.dir(mirror.getId())));
        assertTrue(store.find(mirror.getId()).isEmpty());
    }

    @Test
    void findAudioChecksAllKnownExtensions() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("k1.m4a"), "x");
        assertTrue(store.findAudio(mirror.getId(), "k1").isPresent());
        assertTrue(store.findAudio(mirror.getId(), "missing").isEmpty());
    }

    @Test
    void findArtworkLocatesCoverAndEpisodeImages() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("cover.png"), "img");
        Files.writeString(store.episodesDir(mirror.getId()).resolve("k1.webp"), "img");
        assertTrue(store.findArtwork(mirror.getId(), MirrorStore.COVER).isPresent());
        assertTrue(store.findArtwork(mirror.getId(), "k1").isPresent());
        assertTrue(store.findArtwork(mirror.getId(), "k2").isEmpty());
        // Artwork files are never mistaken for Episodes.
        assertTrue(store.episodes(mirror).isEmpty());
    }

    @Test
    void episodeMetadataComesFromItemXmlSidecar() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        String key = Ids.episodeKey("ep-1");
        Files.writeString(store.episodesDir(mirror.getId()).resolve(key + ".mp3"), "abc");
        Files.writeString(store.episodesDir(mirror.getId()).resolve(key + ".item.xml"), """
                <item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <title>Episode One</title>
                  <guid isPermaLink="false">ep-1</guid>
                  <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
                  <description>The first one</description>
                  <itunes:duration>1:02:03</itunes:duration>
                </item>
                """);

        List<Episode> episodes = store.episodes(mirror);
        assertEquals(1, episodes.size());
        Episode episode = episodes.getFirst();
        assertEquals("Episode One", episode.title());
        assertEquals("The first one", episode.description());
        assertEquals(Instant.parse("2026-06-01T10:00:00Z"), episode.pubDate());
        assertEquals(3, episode.sizeBytes());
        assertEquals("audio/mpeg", episode.mimeType());
        assertEquals(3723, episode.durationSeconds());
        assertEquals("ep-1", episode.guid());
        assertFalse(episode.delisted());
    }

    @Test
    void episodeMetadataComesFromInfoJsonSidecar() throws IOException {
        Mirror mirror = store.create("https://yt.example/c/chan", ytdlpProbe(), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid1.m4a"), "abcd");
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid1.info.json"), """
                {"id":"vid1","title":"Video One","description":"d","timestamp":1750000000,
                 "duration":61.7,"webpage_url":"https://yt.example/w/vid1"}
                """);

        Episode episode = store.episodes(mirror).getFirst();
        assertEquals("Video One", episode.title());
        assertEquals(Instant.ofEpochSecond(1750000000), episode.pubDate());
        assertEquals("audio/mp4", episode.mimeType());
        assertEquals(61, episode.durationSeconds());
        assertEquals("https://yt.example/w/vid1", episode.guid());
    }

    @Test
    void episodeWithoutSidecarsFallsBackToFileFacts() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("orphan.opus"), "x");

        Episode episode = store.episodes(mirror).getFirst();
        assertEquals("orphan", episode.title());
        assertEquals("audio/opus", episode.mimeType());
        assertNotNull(episode.pubDate());
        assertNull(episode.durationSeconds());
    }

    @Test
    void delistedIsComputedAgainstTheCurrentSourceFeed() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        String listedKey = Ids.episodeKey("g1");
        String droppedKey = Ids.episodeKey("g2");
        Files.writeString(store.episodesDir(mirror.getId()).resolve(listedKey + ".mp3"), "x");
        Files.writeString(store.episodesDir(mirror.getId()).resolve(droppedKey + ".mp3"), "x");
        Files.writeString(store.feedXml(mirror.getId()), """
                <rss version="2.0"><channel><title>A</title>
                  <item><guid>g1</guid></item>
                </channel></rss>
                """);

        List<Episode> episodes = store.episodes(mirror);
        assertEquals(2, episodes.size());
        assertFalse(byKey(episodes, listedKey).delisted());
        assertTrue(byKey(episodes, droppedKey).delisted());
    }

    @Test
    void nothingIsDelistedWhenTheSourceWasNeverFetched() throws IOException {
        Mirror mirror = store.create("https://pod.example/feed.xml", rssProbe("A"), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("k.mp3"), "x");
        assertFalse(store.episodes(mirror).getFirst().delisted());
    }

    @Test
    void currentKeysForYtdlpComeFromTheListing() throws IOException {
        Mirror mirror = store.create("https://yt.example/c/chan", ytdlpProbe(), null);
        Files.writeString(store.listingJson(mirror.getId()),
                "{\"entries\":[{\"id\":\"a\"},{\"id\":\"b\"}]}");
        assertEquals(Set.of("a", "b"), store.currentKeys(mirror));

        Files.writeString(store.listingJson(mirror.getId()), "{\"id\":\"single\"}");
        assertEquals(Set.of("single"), store.currentKeys(mirror));

        // YouTube channels nest videos inside tab playlists.
        Files.writeString(store.listingJson(mirror.getId()), """
                {"id":"chan","entries":[
                  {"id":"chan-videos","entries":[{"id":"v1"},{"id":"v2"}]},
                  {"id":"chan-shorts","entries":[{"id":"s1"}]}
                ]}
                """);
        assertEquals(Set.of("v1", "v2", "s1"), store.currentKeys(mirror));
    }

    @Test
    void createStoresTheServiceName() throws IOException {
        Mirror mirror = store.create("https://yt.example/c/chan", ytdlpProbe(), null);
        assertEquals("YouTube", store.find(mirror.getId()).orElseThrow().getService());
        assertEquals("YouTube", mirror.displayService());
    }

    @Test
    void infoTimestampFallsBackThroughTheKnownFields() throws IOException {
        var mapper = TestSupport.mapper();
        assertEquals(Instant.ofEpochSecond(100),
                MirrorStore.infoTimestamp(mapper.readTree("{\"timestamp\":100}")));
        assertEquals(Instant.ofEpochSecond(200),
                MirrorStore.infoTimestamp(mapper.readTree("{\"release_timestamp\":200}")));
        assertEquals(Instant.parse("2026-06-01T00:00:00Z"),
                MirrorStore.infoTimestamp(mapper.readTree("{\"upload_date\":\"20260601\"}")));
        assertNull(MirrorStore.infoTimestamp(mapper.readTree("{}")));
    }

    private static ProbeResult ytdlpProbe() {
        return new ProbeResult(true, SourceType.YTDLP, "YouTube", "Chan", "d", null, "Up", 2, null);
    }

    private static Episode byKey(List<Episode> episodes, String key) {
        return episodes.stream().filter(e -> e.key().equals(key)).findFirst().orElseThrow();
    }
}
