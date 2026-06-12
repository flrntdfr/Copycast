package xyz.dufour.copycast.util;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class IdsTest {

    @Test
    void mirrorIdIsDeterministicAndCompact() {
        String a = Ids.mirrorId("https://example.com/feed.xml");
        String b = Ids.mirrorId("https://example.com/feed.xml");
        assertEquals(a, b);
        assertEquals(16, a.length());
    }

    @Test
    void mirrorIdIgnoresSurroundingWhitespace() {
        assertEquals(Ids.mirrorId("https://example.com/feed.xml"),
                Ids.mirrorId("  https://example.com/feed.xml  "));
    }

    @Test
    void differentSourcesGetDifferentIds() {
        assertNotEquals(Ids.mirrorId("https://example.com/a.xml"),
                Ids.mirrorId("https://example.com/b.xml"));
    }
}
