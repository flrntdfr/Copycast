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

    private static final Map<String, String> IMAGE_TYPES = Map.of(
            "jpg", "image/jpeg",
            "jpeg", "image/jpeg",
            "png", "image/png",
            "webp", "image/webp",
            "gif", "image/gif");

    private Mime() {
    }

    public static Set<String> audioExtensions() {
        return AUDIO_TYPES.keySet();
    }

    public static Set<String> imageExtensions() {
        return IMAGE_TYPES.keySet();
    }

    public static String forFileName(String fileName) {
        String ext = extension(fileName);
        String audio = AUDIO_TYPES.get(ext);
        if (audio != null) {
            return audio;
        }
        return IMAGE_TYPES.getOrDefault(ext, "application/octet-stream");
    }

    /** Best-effort image file extension from a Content-Type header and URL. */
    public static String imageExtension(String contentType, String url) {
        String type = contentType == null ? "" : contentType.split(";")[0].trim().toLowerCase(Locale.ROOT);
        for (Map.Entry<String, String> entry : IMAGE_TYPES.entrySet()) {
            if (entry.getValue().equals(type)) {
                return entry.getKey();
            }
        }
        String path = url == null ? "" : url.split("[?#]")[0];
        String fromUrl = extension(path);
        return IMAGE_TYPES.containsKey(fromUrl) ? fromUrl : "jpg";
    }

    public static String extension(String fileName) {
        int dot = fileName.lastIndexOf('.');
        return dot < 0 ? "" : fileName.substring(dot + 1).toLowerCase(Locale.ROOT);
    }
}
