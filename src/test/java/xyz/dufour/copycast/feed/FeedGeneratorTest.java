package xyz.dufour.copycast.feed;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import xyz.dufour.copycast.TestSupport;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.util.Ids;
import xyz.dufour.copycast.util.XmlUtil;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FeedGeneratorTest {

    @TempDir
    Path dataDir;

    MirrorStore store;
    FeedGenerator generator;

    @BeforeEach
    void setUp() {
        var props = TestSupport.props(dataDir);
        store = new MirrorStore(props, TestSupport.mapper());
        generator = new FeedGenerator(store, props);
    }

    private Mirror rssMirror() throws IOException {
        return store.create("https://pod.example/feed.xml",
                new ProbeResult(true, SourceType.RSS, "My Pod", "About things",
                        "https://img.example/c.png", "Jane", 1, null), null);
    }

    private static String itemXml(String guid, String title, String pubDate) {
        return """
                <item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <title>%s</title>
                  <guid isPermaLink="false">%s</guid>
                  <pubDate>%s</pubDate>
                  <description>Notes for %s</description>
                  <itunes:duration>0:30:00</itunes:duration>
                  <enclosure url="https://pod.example/%s.mp3" length="999" type="audio/mpeg"/>
                </item>
                """.formatted(title, guid, pubDate, title, guid);
    }

    private void archiveEpisode(Mirror mirror, String guid, String title, String pubDate, String audio)
            throws IOException {
        String key = Ids.episodeKey(guid);
        Files.writeString(store.episodesDir(mirror.getId()).resolve(key + ".mp3"), audio);
        Files.writeString(store.episodesDir(mirror.getId()).resolve(key + ".item.xml"),
                itemXml(guid, title, pubDate));
    }

    private static Document parse(String xml) throws IOException {
        return XmlUtil.parse(xml.getBytes(StandardCharsets.UTF_8));
    }

    private static Element channelOf(Document doc) {
        return XmlUtil.child(doc.getDocumentElement(), "channel").orElseThrow();
    }

    @Test
    void preservesOriginalChannelMetadataAndRewritesEnclosures() throws IOException {
        Mirror mirror = rssMirror();
        Files.writeString(store.feedXml(mirror.getId()), """
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
                     xmlns:atom="http://www.w3.org/2005/Atom">
                  <channel>
                    <title>My Pod</title>
                    <description>About things</description>
                    <language>en</language>
                    <itunes:author>Jane</itunes:author>
                    <atom:link rel="self" href="https://pod.example/feed.xml" type="application/rss+xml"/>
                    <item><guid>ep-1</guid></item>
                  </channel>
                </rss>
                """);
        archiveEpisode(mirror, "ep-1", "Episode One", "Mon, 01 Jun 2026 10:00:00 GMT", "12345");

        Document doc = parse(generator.generate(mirror));
        Element channel = channelOf(doc);

        assertEquals("My Pod", XmlUtil.childText(channel, "title"));
        assertEquals("en", XmlUtil.childText(channel, "language"));
        assertEquals("Jane", XmlUtil.childNs(channel, XmlUtil.ITUNES_NS, "author")
                .orElseThrow().getTextContent());
        assertEquals("Copycast", XmlUtil.childText(channel, "generator"));

        // The atom self link must point at the Mirror Feed, not the Source.
        Element self = XmlUtil.childNs(channel, XmlUtil.ATOM_NS, "link").orElseThrow();
        assertEquals(generator.feedUrl(mirror), self.getAttribute("href"));

        List<Element> items = XmlUtil.children(channel, "item");
        assertEquals(1, items.size());
        Element item = items.getFirst();
        assertEquals("Episode One", XmlUtil.childText(item, "title"));
        assertEquals("ep-1", XmlUtil.childText(item, "guid"));

        Element enclosure = XmlUtil.child(item, "enclosure").orElseThrow();
        String key = Ids.episodeKey("ep-1");
        assertEquals(generator.mediaUrl(mirror, key + ".mp3"), enclosure.getAttribute("url"));
        assertEquals("5", enclosure.getAttribute("length"));
        assertEquals("audio/mpeg", enclosure.getAttribute("type"));
    }

    @Test
    void mirrorFeedIsTheUnionOfEverythingArchived() throws IOException {
        Mirror mirror = rssMirror();
        // The Source now lists only ep-2; ep-1 was archived earlier.
        Files.writeString(store.feedXml(mirror.getId()), """
                <rss version="2.0"><channel><title>My Pod</title>
                  <item><guid>ep-2</guid></item>
                </channel></rss>
                """);
        archiveEpisode(mirror, "ep-1", "Old One", "Mon, 01 Jun 2020 10:00:00 GMT", "a");
        archiveEpisode(mirror, "ep-2", "New One", "Mon, 01 Jun 2026 10:00:00 GMT", "b");

        Document doc = parse(generator.generate(mirror));
        List<Element> items = XmlUtil.children(channelOf(doc), "item");
        List<String> guids = items.stream().map(i -> XmlUtil.childText(i, "guid")).toList();
        assertEquals(2, items.size());
        // Newest first; the dropped Episode is still present.
        assertEquals(List.of("ep-2", "ep-1"), guids);
    }

    @Test
    void ytdlpMirrorSynthesizesChannelAndItemsFromInfoJson() throws IOException {
        Mirror mirror = store.create("https://yt.example/c/chan",
                new ProbeResult(true, SourceType.YTDLP, "Chan", "A channel",
                        "https://img.example/chan.png", "Up", 1, null), null);
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid1.m4a"), "abcd");
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid1.webp"), "img");
        Files.writeString(store.episodesDir(mirror.getId()).resolve("vid1.info.json"), """
                {"id":"vid1","title":"Video One","description":"d","upload_date":"20260601",
                 "duration":90,"webpage_url":"https://yt.example/w/vid1"}
                """);

        Document doc = parse(generator.generate(mirror));
        Element channel = channelOf(doc);
        assertEquals("Chan", XmlUtil.childText(channel, "title"));
        assertEquals("https://img.example/chan.png",
                XmlUtil.childNs(channel, XmlUtil.ITUNES_NS, "image").orElseThrow().getAttribute("href"));

        Element item = XmlUtil.children(channel, "item").getFirst();
        assertEquals("Video One", XmlUtil.childText(item, "title"));
        assertEquals(generator.mediaUrl(mirror, "vid1.webp"),
                XmlUtil.childNs(item, XmlUtil.ITUNES_NS, "image").orElseThrow().getAttribute("href"));
        Element guid = XmlUtil.child(item, "guid").orElseThrow();
        assertEquals("https://yt.example/w/vid1", guid.getTextContent());
        assertEquals("false", guid.getAttribute("isPermaLink"));
        assertEquals("0:01:30", XmlUtil.childNs(item, XmlUtil.ITUNES_NS, "duration")
                .orElseThrow().getTextContent());

        Instant pubDate = ZonedDateTime.parse(XmlUtil.childText(item, "pubDate"),
                DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
        assertEquals(Instant.parse("2026-06-01T00:00:00Z"), pubDate);

        Element enclosure = XmlUtil.child(item, "enclosure").orElseThrow();
        assertEquals("audio/mp4", enclosure.getAttribute("type"));
        assertEquals("4", enclosure.getAttribute("length"));
    }

    @Test
    void synthesizesChannelAndItemWhenNoMetadataWasEverFetched() throws IOException {
        Mirror mirror = rssMirror();
        Files.writeString(store.episodesDir(mirror.getId()).resolve("orphan.mp3"), "x");

        Document doc = parse(generator.generate(mirror));
        Element channel = channelOf(doc);
        assertEquals("My Pod", XmlUtil.childText(channel, "title"));

        Element item = XmlUtil.children(channel, "item").getFirst();
        assertEquals("orphan", XmlUtil.childText(item, "title"));
        assertTrue(XmlUtil.child(item, "enclosure").isPresent());
    }

    @Test
    void artworkReferencesAreRewrittenToArchivedCopies() throws IOException {
        Mirror mirror = rssMirror();
        Files.writeString(store.feedXml(mirror.getId()), """
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <channel>
                    <title>My Pod</title>
                    <image><url>https://img.example/remote.png</url></image>
                    <itunes:image href="https://img.example/remote.png"/>
                    <item><guid>ep-1</guid></item>
                  </channel>
                </rss>
                """);
        archiveEpisode(mirror, "ep-1", "Episode One", "Mon, 01 Jun 2026 10:00:00 GMT", "a");
        String key = Ids.episodeKey("ep-1");
        Files.writeString(store.episodesDir(mirror.getId()).resolve("cover.jpg"), "img");
        Files.writeString(store.episodesDir(mirror.getId()).resolve(key + ".png"), "img");

        Document doc = parse(generator.generate(mirror));
        Element channel = channelOf(doc);
        String coverUrl = generator.mediaUrl(mirror, "cover.jpg");

        Element image = XmlUtil.child(channel, "image").orElseThrow();
        assertEquals(coverUrl, XmlUtil.childText(image, "url"));
        assertEquals(coverUrl, XmlUtil.childNs(channel, XmlUtil.ITUNES_NS, "image")
                .orElseThrow().getAttribute("href"));

        Element item = XmlUtil.children(channel, "item").getFirst();
        assertEquals(generator.mediaUrl(mirror, key + ".png"),
                XmlUtil.childNs(item, XmlUtil.ITUNES_NS, "image").orElseThrow().getAttribute("href"));
    }

    @Test
    void remoteArtworkIsKeptWhenNothingIsArchived() throws IOException {
        Mirror mirror = rssMirror();
        Files.writeString(store.feedXml(mirror.getId()), """
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <channel>
                    <title>My Pod</title>
                    <itunes:image href="https://img.example/remote.png"/>
                  </channel>
                </rss>
                """);

        Document doc = parse(generator.generate(mirror));
        assertEquals("https://img.example/remote.png",
                XmlUtil.childNs(channelOf(doc), XmlUtil.ITUNES_NS, "image")
                        .orElseThrow().getAttribute("href"));
    }

    @Test
    void publicUrlsAreBuiltFromTheConfiguredBaseUrl() throws IOException {
        Mirror mirror = rssMirror();
        assertEquals("http://localhost:8080/feed/" + mirror.getId() + "/feed.xml",
                generator.feedUrl(mirror));
        assertEquals("http://localhost:8080/feed/" + mirror.getId() + "/media/k.mp3",
                generator.mediaUrl(mirror, "k.mp3"));
    }
}
