package xyz.dufour.copycast.feed;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.refresh.RefreshService;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class FeedControllerTest {

    @TempDir
    Path episodesDir;

    MirrorStore store;
    FeedGenerator generator;
    RefreshService refresh;
    MockMvc mvc;
    Mirror mirror;

    @BeforeEach
    void setUp() {
        store = mock(MirrorStore.class);
        generator = mock(FeedGenerator.class);
        refresh = mock(RefreshService.class);
        mvc = MockMvcBuilders.standaloneSetup(new FeedController(store, generator, refresh)).build();
        mirror = new Mirror();
        mirror.setId("abc");
    }

    @Test
    void feedIsServedAndTriggersAnAsyncRefresh() throws Exception {
        when(store.find("abc")).thenReturn(Optional.of(mirror));
        when(generator.generate(mirror)).thenReturn("<rss/>");

        mvc.perform(get("/feed/abc/feed.xml"))
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.parseMediaType("application/rss+xml;charset=UTF-8")))
                .andExpect(content().string("<rss/>"));

        verify(refresh).request("abc", RefreshService.Trigger.FEED_FETCH);
    }

    @Test
    void unknownMirrorIs404AndTriggersNothing() throws Exception {
        when(store.find("nope")).thenReturn(Optional.empty());
        mvc.perform(get("/feed/nope/feed.xml")).andExpect(status().isNotFound());
        verify(refresh, never()).request(any(), any());
    }

    @Test
    void mediaIsServedWithAudioContentType() throws Exception {
        when(store.find("abc")).thenReturn(Optional.of(mirror));
        when(store.episodesDir("abc")).thenReturn(episodesDir);
        Files.writeString(episodesDir.resolve("k.mp3"), "hello!");

        mvc.perform(get("/feed/abc/media/k.mp3"))
                .andExpect(status().isOk())
                .andExpect(content().contentType("audio/mpeg"))
                .andExpect(header().string(HttpHeaders.ACCEPT_RANGES, "bytes"))
                .andExpect(content().string("hello!"));
    }

    @Test
    void mediaSupportsByteRangesForSeeking() throws Exception {
        when(store.find("abc")).thenReturn(Optional.of(mirror));
        when(store.episodesDir("abc")).thenReturn(episodesDir);
        Files.writeString(episodesDir.resolve("k.mp3"), "hello!");

        mvc.perform(get("/feed/abc/media/k.mp3").header(HttpHeaders.RANGE, "bytes=0-2"))
                .andExpect(status().isPartialContent())
                .andExpect(content().string("hel"));
    }

    @Test
    void mediaRejectsPathTraversal() throws Exception {
        when(store.find("abc")).thenReturn(Optional.of(mirror));
        when(store.episodesDir("abc")).thenReturn(episodesDir);

        mvc.perform(get("/feed/abc/media/{file}", "..mirror.json"))
                .andExpect(status().isNotFound());
    }

    @Test
    void missingMediaFileIs404() throws Exception {
        when(store.find("abc")).thenReturn(Optional.of(mirror));
        when(store.episodesDir("abc")).thenReturn(episodesDir);

        mvc.perform(get("/feed/abc/media/missing.mp3")).andExpect(status().isNotFound());
    }
}
