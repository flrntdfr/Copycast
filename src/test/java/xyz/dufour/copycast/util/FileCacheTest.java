package xyz.dufour.copycast.util;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;

class FileCacheTest {

    @TempDir
    Path dir;

    @Test
    void loadsOnceWhileTheFileIsUnchanged() throws IOException {
        Path file = dir.resolve("a.txt");
        Files.writeString(file, "one");
        FileCache<String> cache = new FileCache<>();
        AtomicInteger loads = new AtomicInteger();

        for (int i = 0; i < 5; i++) {
            assertEquals("one", cache.get(file, f -> {
                loads.incrementAndGet();
                return readQuietly(f);
            }));
        }
        assertEquals(1, loads.get());
    }

    @Test
    void reloadsWhenMtimeChanges() throws IOException {
        Path file = dir.resolve("a.txt");
        Files.writeString(file, "one");
        FileCache<String> cache = new FileCache<>();
        AtomicInteger loads = new AtomicInteger();

        cache.get(file, f -> {
            loads.incrementAndGet();
            return readQuietly(f);
        });
        Files.writeString(file, "two");
        // Same content length is possible; force a distinct mtime.
        Files.setLastModifiedTime(file, FileTime.from(Instant.now().plusSeconds(5)));

        assertEquals("two", cache.get(file, f -> {
            loads.incrementAndGet();
            return readQuietly(f);
        }));
        assertEquals(2, loads.get());
    }

    @Test
    void reloadsWhenSizeChangesEvenWithSameMtime() throws IOException {
        Path file = dir.resolve("a.txt");
        Files.writeString(file, "one");
        FileTime fixed = FileTime.from(Instant.parse("2026-06-12T00:00:00Z"));
        Files.setLastModifiedTime(file, fixed);
        FileCache<String> cache = new FileCache<>();

        assertEquals("one", cache.get(file, FileCacheTest::readQuietly));
        Files.writeString(file, "longer content");
        Files.setLastModifiedTime(file, fixed);
        assertEquals("longer content", cache.get(file, FileCacheTest::readQuietly));
    }

    @Test
    void cachesNullResultsUntilTheFileChanges() throws IOException {
        Path file = dir.resolve("bad.txt");
        Files.writeString(file, "x");
        FileCache<String> cache = new FileCache<>();
        AtomicInteger loads = new AtomicInteger();

        for (int i = 0; i < 3; i++) {
            cache.get(file, f -> {
                loads.incrementAndGet();
                return null;
            });
        }
        assertEquals(1, loads.get());
    }

    @Test
    void clearForcesAReload() throws IOException {
        Path file = dir.resolve("a.txt");
        Files.writeString(file, "one");
        FileCache<String> cache = new FileCache<>();
        AtomicInteger loads = new AtomicInteger();

        cache.get(file, f -> {
            loads.incrementAndGet();
            return readQuietly(f);
        });
        cache.clear();
        cache.get(file, f -> {
            loads.incrementAndGet();
            return readQuietly(f);
        });
        assertEquals(2, loads.get());
    }

    private static String readQuietly(Path file) {
        try {
            return Files.readString(file);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }
}
