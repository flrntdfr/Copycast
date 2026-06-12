package xyz.dufour.copycast.source;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class FeedLinkTest {

    private static final String PAGE = "https://atp.fm/";

    @Test
    void discoversRelativeFeedFromTypicalHead() {
        String html = """
                <!DOCTYPE html>
                <html lang="en">
                    <head>
                        <title>Accidental Tech Podcast</title>
                        <meta name="description" content="Three nerds discussing tech.">
                        <link rel="alternate" type="application/rss+xml" title="RSS" href="/rss">
                    </head>
                </html>
                """;
        assertEquals("https://atp.fm/rss", FeedLink.discover(html, PAGE));
    }

    @Test
    void absoluteHrefIsKeptAsIs() {
        String html = "<link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://feeds.example.com/show\">";
        assertEquals("https://feeds.example.com/show", FeedLink.discover(html, PAGE));
    }

    @Test
    void attributeOrderQuotingAndCaseDoNotMatter() {
        String html = "<LINK HREF='/feed.xml' TYPE='APPLICATION/RSS+XML' REL='Alternate'>";
        assertEquals("https://atp.fm/feed.xml", FeedLink.discover(html, PAGE));
    }

    @Test
    void htmlEntitiesInHrefAreUnescaped() {
        String html = "<link rel=\"alternate\" type=\"application/rss+xml\" href=\"/rss?a=1&amp;b=2\">";
        assertEquals("https://atp.fm/rss?a=1&b=2", FeedLink.discover(html, PAGE));
    }

    @Test
    void firstRssLinkWins() {
        String html = """
                <link rel="alternate" type="application/atom+xml" href="/atom">
                <link rel="alternate" type="application/rss+xml" href="/main">
                <link rel="alternate" type="application/rss+xml" href="/comments">
                """;
        assertEquals("https://atp.fm/main", FeedLink.discover(html, PAGE));
    }

    @Test
    void nonFeedLinksAreIgnored() {
        String html = """
                <link rel="stylesheet" href="/style.css">
                <link rel="alternate" type="application/json+oembed" href="/oembed">
                <link rel="icon" href="/favicon.ico">
                """;
        assertNull(FeedLink.discover(html, PAGE));
    }

    @Test
    void pageWithoutLinksYieldsNull() {
        assertNull(FeedLink.discover("<html><body>hello</body></html>", PAGE));
    }
}
