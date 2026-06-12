package xyz.dufour.copycast.util;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public final class Http {

    public static final String USER_AGENT = "Copycast/0.1";

    private static final HttpClient CLIENT = HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.ALWAYS)
            .connectTimeout(Duration.ofSeconds(20))
            .build();

    private Http() {
    }

    /** A fetched body, its content type, and the final URL after redirects. */
    public record Content(byte[] bytes, String contentType, String finalUrl) {
    }

    public static byte[] get(String url, Duration timeout) throws IOException, InterruptedException {
        return getContent(url, timeout).bytes();
    }

    public static Content getContent(String url, Duration timeout) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .timeout(timeout)
                .header("User-Agent", USER_AGENT)
                .GET()
                .build();
        HttpResponse<byte[]> response = CLIENT.send(request, HttpResponse.BodyHandlers.ofByteArray());
        if (response.statusCode() / 100 != 2) {
            throw new IOException("HTTP " + response.statusCode() + " fetching " + url);
        }
        return new Content(response.body(),
                response.headers().firstValue("Content-Type").orElse(""),
                response.uri().toString());
    }
}
