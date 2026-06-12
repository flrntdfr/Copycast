package xyz.dufour.copycast.mirror;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.source.Rss;
import xyz.dufour.copycast.util.Ids;
import xyz.dufour.copycast.util.Mime;
import xyz.dufour.copycast.util.XmlUtil;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Filesystem persistence (see docs/adr/0001). Layout per Mirror:
 *
 * <pre>
 * {dataDir}/mirrors/{id}/
 *   mirror.json     descriptor (this is the Mirror)
 *   feed.xml        last successfully fetched original feed (RSS Sources)
 *   listing.json    last yt-dlp flat-playlist listing (yt-dlp Sources)
 *   archive.txt     yt-dlp download archive (dedup)
 *   episodes/       audio + {key}.item.xml / {key}.info.json sidecars
 * </pre>
 */
@Component
public class MirrorStore {

    private static final Logger log = LoggerFactory.getLogger(MirrorStore.class);

    private final CopycastProperties props;
    private final ObjectMapper mapper;

    public MirrorStore(CopycastProperties props, ObjectMapper mapper) {
        this.props = props;
        this.mapper = mapper;
    }

    public Path mirrorsDir() {
        return props.dataDir().resolve("mirrors");
    }

    public Path dir(String id) {
        return mirrorsDir().resolve(id);
    }

    public Path episodesDir(String id) {
        return dir(id).resolve("episodes");
    }

    public Path archiveFile(String id) {
        return dir(id).resolve("archive.txt");
    }

    public Path feedXml(String id) {
        return dir(id).resolve("feed.xml");
    }

    public Path listingJson(String id) {
        return dir(id).resolve("listing.json");
    }

    private Path descriptor(String id) {
        return dir(id).resolve("mirror.json");
    }

    public List<Mirror> list() {
        if (!Files.isDirectory(mirrorsDir())) {
            return List.of();
        }
        try (Stream<Path> dirs = Files.list(mirrorsDir())) {
            return dirs.filter(Files::isDirectory)
                    .map(d -> find(d.getFileName().toString()))
                    .flatMap(Optional::stream)
                    .sorted(java.util.Comparator.comparing(m -> m.displayTitle().toLowerCase()))
                    .toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public Optional<Mirror> find(String id) {
        Path file = descriptor(id);
        if (!Files.isRegularFile(file)) {
            return Optional.empty();
        }
        try {
            return Optional.of(mapper.readValue(file.toFile(), Mirror.class));
        } catch (IOException e) {
            log.warn("Unreadable descriptor {}: {}", file, e.getMessage());
            return Optional.empty();
        }
    }

    public Optional<Mirror> findBySourceUrl(String sourceUrl) {
        String wanted = sourceUrl.trim();
        return list().stream().filter(m -> wanted.equals(m.getSourceUrl())).findFirst();
    }

    public Mirror create(String sourceUrl, ProbeResult probe, Integer cap) throws IOException {
        Mirror mirror = new Mirror();
        mirror.setId(Ids.mirrorId(sourceUrl));
        mirror.setSourceUrl(sourceUrl.trim());
        mirror.setType(probe.type());
        mirror.setTitle(probe.title());
        mirror.setDescription(probe.description());
        mirror.setImageUrl(probe.imageUrl());
        mirror.setAuthor(probe.author());
        mirror.setCap(cap);
        mirror.setCreatedAt(Instant.now());
        Files.createDirectories(episodesDir(mirror.getId()));
        save(mirror);
        return mirror;
    }

    public synchronized void save(Mirror mirror) {
        try {
            Files.createDirectories(dir(mirror.getId()));
            Path tmp = descriptor(mirror.getId()).resolveSibling("mirror.json.tmp");
            mapper.writerWithDefaultPrettyPrinter().writeValue(tmp.toFile(), mirror);
            Files.move(tmp, descriptor(mirror.getId()), StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public void delete(String id) throws IOException {
        Path root = dir(id);
        if (!Files.exists(root)) {
            return;
        }
        try (Stream<Path> walk = Files.walk(root)) {
            List<Path> paths = walk.sorted(java.util.Comparator.reverseOrder()).toList();
            for (Path path : paths) {
                Files.deleteIfExists(path);
            }
        }
    }

    /** The audio file for an episode key, if it has been archived. */
    public Optional<Path> findAudio(String id, String key) {
        Path episodes = episodesDir(id);
        if (!Files.isDirectory(episodes)) {
            return Optional.empty();
        }
        for (String ext : Mime.audioExtensions()) {
            Path candidate = episodes.resolve(key + "." + ext);
            if (Files.isRegularFile(candidate)) {
                return Optional.of(candidate);
            }
        }
        return Optional.empty();
    }

    /** All archived Episodes, newest first, with Delisted state computed. */
    public List<Episode> episodes(Mirror mirror) {
        Path episodesDir = episodesDir(mirror.getId());
        if (!Files.isDirectory(episodesDir)) {
            return List.of();
        }
        Set<String> current = currentKeys(mirror);
        Map<String, Path> audioByKey = new HashMap<>();
        try (Stream<Path> files = Files.list(episodesDir)) {
            files.filter(Files::isRegularFile).forEach(file -> {
                String name = file.getFileName().toString();
                if (Mime.audioExtensions().contains(Mime.extension(name))) {
                    audioByKey.put(name.substring(0, name.length() - Mime.extension(name).length() - 1), file);
                }
            });
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        List<Episode> episodes = new ArrayList<>();
        for (Map.Entry<String, Path> entry : audioByKey.entrySet()) {
            boolean delisted = !current.isEmpty() && !current.contains(entry.getKey());
            episodes.add(readEpisode(mirror, entry.getKey(), entry.getValue(), delisted));
        }
        episodes.sort(java.util.Comparator.comparing(Episode::pubDate,
                java.util.Comparator.nullsLast(java.util.Comparator.reverseOrder())));
        return episodes;
    }

    private Episode readEpisode(Mirror mirror, String key, Path audio, boolean delisted) {
        long size = audio.toFile().length();
        String mime = Mime.forFileName(audio.getFileName().toString());
        Path itemXml = episodesDir(mirror.getId()).resolve(key + ".item.xml");
        if (Files.isRegularFile(itemXml)) {
            try {
                Document doc = XmlUtil.parse(Files.readAllBytes(itemXml));
                Element item = doc.getDocumentElement();
                return new Episode(key,
                        firstNonBlank(XmlUtil.childText(item, "title"), key),
                        XmlUtil.childText(item, "description"),
                        Rss.pubDate(item),
                        audio, size, mime,
                        parseDuration(XmlUtil.childNs(item, XmlUtil.ITUNES_NS, "duration")
                                .map(e -> e.getTextContent().trim()).orElse(null)),
                        XmlUtil.childText(item, "guid"),
                        delisted);
            } catch (IOException e) {
                log.warn("Unreadable item sidecar {}: {}", itemXml, e.getMessage());
            }
        }
        Path infoJson = episodesDir(mirror.getId()).resolve(key + ".info.json");
        if (Files.isRegularFile(infoJson)) {
            try {
                JsonNode info = mapper.readTree(infoJson.toFile());
                return new Episode(key,
                        firstNonBlank(info.path("title").asText(null), key),
                        info.path("description").asText(null),
                        infoTimestamp(info),
                        audio, size, mime,
                        info.hasNonNull("duration") ? (long) info.path("duration").asDouble() : null,
                        info.path("webpage_url").asText(null),
                        delisted);
            } catch (IOException e) {
                log.warn("Unreadable info sidecar {}: {}", infoJson, e.getMessage());
            }
        }
        Instant mtime = Instant.ofEpochMilli(audio.toFile().lastModified());
        return new Episode(key, key, null, mtime, audio, size, mime, null, key, delisted);
    }

    /** Episode keys the Source currently lists; empty when undeterminable. */
    public Set<String> currentKeys(Mirror mirror) {
        try {
            if (mirror.getType() == SourceType.RSS) {
                Path feed = feedXml(mirror.getId());
                if (!Files.isRegularFile(feed)) {
                    return Set.of();
                }
                Set<String> keys = new HashSet<>();
                Rss.parse(XmlUtil.parse(Files.readAllBytes(feed))).ifPresent(channel -> {
                    for (Element item : channel.items()) {
                        String identity = Rss.itemIdentity(item);
                        if (identity != null) {
                            keys.add(Ids.episodeKey(identity));
                        }
                    }
                });
                return keys;
            }
            Path listing = listingJson(mirror.getId());
            if (!Files.isRegularFile(listing)) {
                return Set.of();
            }
            JsonNode root = mapper.readTree(listing.toFile());
            Set<String> keys = new HashSet<>();
            if (root.has("entries")) {
                root.path("entries").forEach(entry -> {
                    String id = entry.path("id").asText(null);
                    if (id != null) {
                        keys.add(id);
                    }
                });
            } else if (root.hasNonNull("id")) {
                keys.add(root.path("id").asText());
            }
            return keys;
        } catch (IOException e) {
            log.warn("Could not determine current keys for {}: {}", mirror.getId(), e.getMessage());
            return Set.of();
        }
    }

    public static Instant infoTimestamp(JsonNode info) {
        if (info.hasNonNull("timestamp")) {
            return Instant.ofEpochSecond(info.path("timestamp").asLong());
        }
        if (info.hasNonNull("release_timestamp")) {
            return Instant.ofEpochSecond(info.path("release_timestamp").asLong());
        }
        String uploadDate = info.path("upload_date").asText(null);
        if (uploadDate != null && uploadDate.length() == 8) {
            try {
                return LocalDate.parse(uploadDate, DateTimeFormatter.BASIC_ISO_DATE)
                        .atStartOfDay(ZoneOffset.UTC).toInstant();
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static Long parseDuration(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            String[] parts = text.split(":");
            long seconds = 0;
            for (String part : parts) {
                seconds = seconds * 60 + (long) Double.parseDouble(part.trim());
            }
            return seconds;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String firstNonBlank(String a, String b) {
        return a != null && !a.isBlank() ? a : b;
    }
}
