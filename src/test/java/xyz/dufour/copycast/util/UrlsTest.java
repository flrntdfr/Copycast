package xyz.dufour.copycast.util;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class UrlsTest {

    @Test
    void collapsesSchemeWwwTrailingSlashAndHostCase() {
        String canonical = "atp.fm/rss";
        assertEquals(canonical, Urls.dedupKey("https://atp.fm/rss"));
        assertEquals(canonical, Urls.dedupKey("http://atp.fm/rss"));
        assertEquals(canonical, Urls.dedupKey("https://www.atp.fm/rss"));
        assertEquals(canonical, Urls.dedupKey("https://ATP.FM/rss"));
        assertEquals(canonical, Urls.dedupKey("https://atp.fm/rss/"));
        assertEquals(canonical, Urls.dedupKey("  https://atp.fm/rss  "));
    }

    @Test
    void dropsTrackingParamsButKeepsMeaningfulOnes() {
        assertEquals("atp.fm/rss", Urls.dedupKey("https://atp.fm/rss?utm_source=x&fbclid=y"));
        // An auth token is meaningful — two tokens are different feeds.
        assertNotEquals(Urls.dedupKey("https://pod.example/rss?token=a"),
                Urls.dedupKey("https://pod.example/rss?token=b"));
        assertEquals("pod.example/rss?token=a",
                Urls.dedupKey("https://pod.example/rss?token=a&utm_medium=email"));
    }

    @Test
    void queryParameterOrderIsCanonical() {
        assertEquals(Urls.dedupKey("https://pod.example/rss?a=1&b=2"),
                Urls.dedupKey("https://pod.example/rss?b=2&a=1"));
    }

    @Test
    void distinctFeedsStayDistinct() {
        assertNotEquals(Urls.dedupKey("https://atp.fm/rss"),
                Urls.dedupKey("https://atp.fm/rss2"));
        assertNotEquals(Urls.dedupKey("https://a.example/rss"),
                Urls.dedupKey("https://b.example/rss"));
    }

    @Test
    void rootPathIsPreserved() {
        assertEquals("atp.fm", Urls.dedupKey("https://atp.fm/"));
        assertEquals("atp.fm", Urls.dedupKey("https://atp.fm"));
    }
}
