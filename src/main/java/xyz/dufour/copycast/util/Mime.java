package xyz.dufour.copycast.util;

import java.util.Locale;
import java.util.Map;
import java.util.Set;

public final class Mime {

    private static final Map<String, String> AUDIO_TYPES = Map.ofEntries(
            Map.entry("mp3", "audio/mpeg"),
            Map.entry("m4a", "audio/mp4"),
            Map.entry("m4b", "audio/mp4"),
            Map.entry("aac", "audio/aac"),
            Map.entry("ogg", "audio/ogg"),
            Map.entry("oga", "audio/ogg"),
            Map.entry("opus", "audio/opus"),
            Map.entry("flac", "audio/flac"),
            Map.entry("wav", "audio/wav"),
            Map.entry("mka", "audio/x-matroska"));

    private Mime() {
    }

    public static Set<String> audioExtensions() {
        return AUDIO_TYPES.keySet();
    }

    public static String forFileName(String fileName) {
        return AUDIO_TYPES.getOrDefault(extension(fileName), "application/octet-stream");
    }

    public static String extension(String fileName) {
        int dot = fileName.lastIndexOf('.');
        return dot < 0 ? "" : fileName.substring(dot + 1).toLowerCase(Locale.ROOT);
    }
}
