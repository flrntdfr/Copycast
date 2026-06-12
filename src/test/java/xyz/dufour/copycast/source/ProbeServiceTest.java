package xyz.dufour.copycast.source;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import xyz.dufour.copycast.TestSupport;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Exercises the smart URL handling against a local HTTP server: scheme
 * inference (https tried first, http fallback) and RSS autodiscovery from
 * an HTML page. yt-dlp is never reached in these paths.
 */
class ProbeServiceTest {

    private static final String RSS = """
            <rss version="2.0"><channel>
              <title>Discovered Pod</title>
              <description>d</description>
              <item><title>One</title><guid>g1</guid></item>
              <item><title>Two</title><guid>g2</guid></item>
            </channel></rss>
            """;

    @TempDir
    Path dataDir;

    ProbeService probe;
    HttpServer server;
    String hostPort;

    @BeforeEach
    void setUp() throws IOException {
        var props = TestSupport.props(dataDir);
        probe = new ProbeService(new YtDlp(props), TestSupport.mapper(), props);
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        hostPort = "127.0.0.1:" + server.getAddress().getPort();
        serve("/rss", "application/rss+xml", RSS);
        serve("/", "text/html", """
                <!DOCTYPE html>
                <html><head>
                    <title>Discovered Pod</title>
                    <link rel="alternate" type="application/rss+xml" title="RSS" href="/rss">
                </head><body>welcome</body></html>
                """);
        server.start();
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    private void serve(String path, String contentType, String body) {
        server.createContext(path, exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", contentType);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
    }

    @Test
    void candidateUrlsPreferHttpsWhenSchemeIsMissing() {
        assertEquals(List.of("https://atp.fm/rss", "http://atp.fm/rss"),
                ProbeService.candidateUrls(" atp.fm/rss "));
        assertEquals(List.of("https://atp.fm"), ProbeService.candidateUrls("https://atp.fm"));
        assertEquals(List.of("HTTP://atp.fm"), ProbeService.candidateUrls("HTTP://atp.fm"));
        assertTrue(ProbeService.candidateUrls("  ").isEmpty());
    }

    @Test
    void schemeIsInferredForADirectFeedUrl() {
        // https://127.0.0.1:port fails (no TLS), so the http fallback wins.
        ProbeResult result = probe.probe(hostPort + "/rss");

        assertTrue(result.supported());
        assertEquals(SourceType.RSS, result.type());
        assertEquals("Discovered Pod", result.title());
        assertEquals(2, result.episodeCount());
        assertEquals("http://" + hostPort + "/rss", result.url());
    }

    @Test
    void feedIsDiscoveredFromThePodcastsWebsite() {
        ProbeResult result = probe.probe("http://" + hostPort + "/");

        assertTrue(result.supported());
        assertEquals(SourceType.RSS, result.type());
        assertEquals("Discovered Pod", result.title());
        // The Mirror's canonical Source is the discovered feed, not the page.
        assertEquals("http://" + hostPort + "/rss", result.url());
    }

    @Test
    void blankInputIsRejected() {
        ProbeResult result = probe.probe("   ");
        assertFalse(result.supported());
    }
}
