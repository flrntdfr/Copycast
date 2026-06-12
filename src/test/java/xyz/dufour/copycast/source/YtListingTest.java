package xyz.dufour.copycast.source;

import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;
import xyz.dufour.copycast.TestSupport;

import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class YtListingTest {

    private static JsonNode json(String body) throws IOException {
        return TestSupport.mapper().readTree(body);
    }

    @Test
    void flattensChannelTabPlaylistsToVideos() throws IOException {
        JsonNode channel = json("""
                {"id":"chan","title":"Chan","entries":[
                  {"id":"chan-videos","title":"Chan - Videos","entries":[{"id":"v1"},{"id":"v2"},{"id":"v3"}]},
                  {"id":"chan-shorts","title":"Chan - Shorts","entries":[{"id":"s1"}]}
                ]}
                """);
        List<JsonNode> leaves = YtListing.leafEntries(channel);
        assertEquals(4, leaves.size());
        assertEquals("v1", leaves.getFirst().path("id").asText());
    }

    @Test
    void flatPlaylistAndSingleVideoStillWork() throws IOException {
        assertEquals(2, YtListing.leafEntries(json("{\"entries\":[{\"id\":\"a\"},{\"id\":\"b\"}]}")).size());
        assertEquals(1, YtListing.leafEntries(json("{\"id\":\"single\"}")).size());
    }

    @Test
    void serviceNameComesFromTheExtractor() throws IOException {
        assertEquals("YouTube", YtListing.serviceName(json("{\"extractor\":\"youtube:tab\"}")));
        assertEquals("YouTube", YtListing.serviceName(json("{\"extractor\":\"youtube\"}")));
        assertEquals("SoundCloud", YtListing.serviceName(json("{\"extractor\":\"soundcloud:set\"}")));
        assertEquals("Twitch", YtListing.serviceName(json("{\"extractor\":\"twitch:vod\"}")));
        assertEquals("Somesite", YtListing.serviceName(json("{\"extractor\":\"somesite\"}")));
        assertNull(YtListing.serviceName(json("{}")));
    }
}
