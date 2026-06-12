package xyz.dufour.copycast.source;

import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import xyz.dufour.copycast.util.XmlUtil;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RssTest {

    private static Document doc(String xml) throws IOException {
        return XmlUtil.parse(xml.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void parsesChannelMetadataAndItems() throws IOException {
        Rss.Channel channel = Rss.parse(doc("""
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <channel>
                    <title>My Pod</title>
                    <description>About things</description>
                    <itunes:author>Jane</itunes:author>
                    <image><url>https://img.example/cover.png</url></image>
                    <item><title>One</title></item>
                    <item><title>Two</title></item>
                  </channel>
                </rss>
                """)).orElseThrow();
        assertEquals("My Pod", channel.title());
        assertEquals("About things", channel.description());
        assertEquals("Jane", channel.author());
        assertEquals("https://img.example/cover.png", channel.imageUrl());
        assertEquals(2, channel.items().size());
    }

    @Test
    void fallsBackToItunesImage() throws IOException {
        Rss.Channel channel = Rss.parse(doc("""
                <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
                  <channel>
                    <title>T</title>
                    <itunes:image href="https://img.example/itunes.png"/>
                  </channel>
                </rss>
                """)).orElseThrow();
        assertEquals("https://img.example/itunes.png", channel.imageUrl());
    }

    @Test
    void itemIdentityPrefersGuidOverEnclosure() throws IOException {
        Rss.Channel channel = Rss.parse(doc("""
                <rss version="2.0"><channel><title>T</title>
                  <item>
                    <guid>g-1</guid>
                    <enclosure url="https://pod.example/a.mp3"/>
                  </item>
                  <item>
                    <enclosure url="https://pod.example/b.mp3"/>
                  </item>
                  <item><title>no identity</title></item>
                </channel></rss>
                """)).orElseThrow();
        assertEquals("g-1", Rss.itemIdentity(channel.items().get(0)));
        assertEquals("https://pod.example/b.mp3", Rss.itemIdentity(channel.items().get(1)));
        assertNull(Rss.itemIdentity(channel.items().get(2)));
    }

    @Test
    void parsesRfc1123PubDate() throws IOException {
        Rss.Channel channel = Rss.parse(doc("""
                <rss version="2.0"><channel><title>T</title>
                  <item><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
                  <item><pubDate>not a date</pubDate></item>
                  <item></item>
                </channel></rss>
                """)).orElseThrow();
        assertEquals(Instant.parse("2026-06-01T10:00:00Z"), Rss.pubDate(channel.items().get(0)));
        assertNull(Rss.pubDate(channel.items().get(1)));
        assertNull(Rss.pubDate(channel.items().get(2)));
    }

    @Test
    void rejectsNonRssDocuments() throws IOException {
        Optional<Rss.Channel> atom = Rss.parse(doc("<feed xmlns=\"http://www.w3.org/2005/Atom\"/>"));
        assertTrue(atom.isEmpty());
        Optional<Rss.Channel> noChannel = Rss.parse(doc("<rss version=\"2.0\"/>"));
        assertTrue(noChannel.isEmpty());
    }
}
