package xyz.dufour.copycast.util;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

/**
 * Memoizes values parsed from files, validated by mtime and size. Episode
 * sidecars are immutable once written, so steady-state reads (UI polls,
 * feed generation) hit the cache instead of re-parsing XML/JSON.
 */
public final class FileCache<T> {

    private record Entry<T>(FileTime mtime, long size, T value) {
    }

    private final Map<Path, Entry<T>> entries = new ConcurrentHashMap<>();

    /**
     * Returns the cached value for {@code file}, reloading via {@code loader}
     * when the file's mtime or size changed. The loader's result (including
     * null) is cached until the file changes on disk.
     */
    public T get(Path file, Function<Path, T> loader) {
        FileTime mtime;
        long size;
        try {
            mtime = Files.getLastModifiedTime(file);
            size = Files.size(file);
        } catch (IOException e) {
            entries.remove(file);
            return loader.apply(file);
        }
        Entry<T> cached = entries.get(file);
        if (cached != null && cached.mtime().equals(mtime) && cached.size() == size) {
            return cached.value();
        }
        T value = loader.apply(file);
        entries.put(file, new Entry<>(mtime, size, value));
        return value;
    }

    public void clear() {
        entries.clear();
    }
}
