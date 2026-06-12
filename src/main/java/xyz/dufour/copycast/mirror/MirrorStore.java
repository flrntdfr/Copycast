package xyz.dufour.copycast.mirror;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.source.Rss;
import xyz.dufour.copycast.util.FileCache;
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
import java.util.concurrent.ConcurrentHashMap;
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

    static final long SIZE_TTL_MILLIS = 30_000;

    private record SizeEntry(long computedAt, long bytes) {
    }

    private final CopycastProperties props;
    private final ObjectMapper mapper;
    // Sidecars are immutable once written; these caches turn the steady-state
    // read path (UI polls, feed generation) into stat calls instead of parses.
    private final FileCache<SidecarMeta> sidecarCache = new FileCache<>();
    private final FileCache<Set<String>> currentKeysCache = new FileCache<>();
    private final Map<String, SizeEntry> sizeCache = new ConcurrentHashMap<>();

    public MirrorStore(CopycastProperties props, ObjectMapper mapper) {
        this.props = props;
        this.mapper = mapper;
    }

    /** Drops all runtime caches; they refill lazily. */
    void clearRuntimeCaches() {
        sidecarCache.clear();
        currentKeysCache.clear();
        sizeCache.clear();
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
            return Optional.of(mapper.readValue(Files.readAllBytes(file), Mirror.class));
        } catch (Exception e) {
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
        mirror.setService(probe.service());
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
        // Deleting is rare; dropping all caches is simpler than tracking
        // which entries belong to this Mirror.
        clearRuntimeCaches();
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

    /**
     * Basename of the archived channel artwork. Cannot collide with episode
     * keys (16-hex digests or yt-dlp video ids).
     */
    public static final String COVER = "cover";

    /** The audio file for an episode key, if it has been archived. */
    public Optional<Path> findAudio(String id, String key) {
        return findByExtensions(id, key, Mime.audioExtensions());
    }

    /** Archived artwork for an episode key or {@link #COVER}, if present. */
    public Optional<Path> findArtwork(String id, String baseName) {
        return findByExtensions(id, baseName, Mime.imageExtensions());
    }

    private Optional<Path> findByExtensions(String id, String baseName, java.util.Set<String> extensions) {
        Path episodes = episodesDir(id);
        if (!Files.isDirectory(episodes)) {
            return Optional.empty();
        }
        for (String ext : extensions) {
            Path candidate = episodes.resolve(baseName + "." + ext);
            if (Files.isRegularFile(candidate)) {
                return Optional.of(candidate);
            }
        }
        return Optional.empty();
    }

    /**
     * Total bytes a Mirror occupies on disk (audio, artwork, metadata).
     * Cached briefly: the tree walk is the most expensive read we have, and
     * nobody needs the size column accurate to the second.
     */
    public long sizeOnDiskBytes(String id) {
        SizeEntry cached = sizeCache.get(id);
        long now = System.currentTimeMillis();
        if (cached != null && now - cached.computedAt() < SIZE_TTL_MILLIS) {
            return cached.bytes();
        }
        long bytes = computeSizeOnDisk(id);
        sizeCache.put(id, new SizeEntry(now, bytes));
        return bytes;
    }

    private long computeSizeOnDisk(String id) {
        Path root = dir(id);
        if (!Files.isDirectory(root)) {
            return 0;
        }
        try (Stream<Path> walk = Files.walk(root)) {
            return walk.filter(Files::isRegularFile).mapToLong(f -> f.toFile().length()).sum();
        } catch (IOException e) {
            return 0;
        }
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

    /** Metadata captured at archive time; immutable on disk, hence cacheable. */
    private record SidecarMeta(String title, String description, Instant pubDate,
                               Long durationSeconds, String guid) {
    }

    private Episode readEpisode(Mirror mirror, String key, Path audio, boolean delisted) {
        long size = audio.toFile().length();
        String mime = Mime.forFileName(audio.getFileName().toString());
        Path itemXml = episodesDir(mirror.getId()).resolve(key + ".item.xml");
        SidecarMeta meta = null;
        if (Files.isRegularFile(itemXml)) {
            meta = sidecarCache.get(itemXml, this::loadItemMeta);
        }
        if (meta == null) {
            Path infoJson = episodesDir(mirror.getId()).resolve(key + ".info.json");
            if (Files.isRegularFile(infoJson)) {
                meta = sidecarCache.get(infoJson, this::loadInfoMeta);
            }
        }
        if (meta != null) {
            return new Episode(key,
                    firstNonBlank(meta.title(), key),
                    meta.description(),
                    meta.pubDate(),
                    audio, size, mime,
                    meta.durationSeconds(),
                    meta.guid(),
                    delisted);
        }
        Instant mtime = Instant.ofEpochMilli(audio.toFile().lastModified());
        return new Episode(key, key, null, mtime, audio, size, mime, null, key, delisted);
    }

    /** Episode keys the Source currently lists; empty when undeterminable. */
    public Set<String> currentKeys(Mirror mirror) {
        if (mirror.getType() == SourceType.RSS) {
            Path feed = feedXml(mirror.getId());
            if (!Files.isRegularFile(feed)) {
                return Set.of();
            }
            return currentKeysCache.get(feed, this::loadRssKeys);
        }
        Path listing = listingJson(mirror.getId());
        if (!Files.isRegularFile(listing)) {
            return Set.of();
        }
        return currentKeysCache.get(listing, this::loadListingKeys);
    }

    private SidecarMeta loadItemMeta(Path itemXml) {
        try {
            Document doc = XmlUtil.parse(Files.readAllBytes(itemXml));
            Element item = doc.getDocumentElement();
            return new SidecarMeta(
                    XmlUtil.childText(item, "title"),
                    XmlUtil.childText(item, "description"),
                    Rss.pubDate(item),
                    parseDuration(XmlUtil.childNs(item, XmlUtil.ITUNES_NS, "duration")
                            .map(e -> e.getTextContent().trim()).orElse(null)),
                    XmlUtil.childText(item, "guid"));
        } catch (Exception e) {
            log.warn("Unreadable item sidecar {}: {}", itemXml, e.getMessage());
            return null;
        }
    }

    private SidecarMeta loadInfoMeta(Path infoJson) {
        try {
            JsonNode info = mapper.readTree(Files.readAllBytes(infoJson));
            return new SidecarMeta(
                    info.path("title").asText(null),
                    info.path("description").asText(null),
                    infoTimestamp(info),
                    info.hasNonNull("duration") ? (long) info.path("duration").asDouble() : null,
                    info.path("webpage_url").asText(null));
        } catch (Exception e) {
            log.warn("Unreadable info sidecar {}: {}", infoJson, e.getMessage());
            return null;
        }
    }

    private Set<String> loadRssKeys(Path feed) {
        try {
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
        } catch (Exception e) {
            log.warn("Could not parse {}: {}", feed, e.getMessage());
            return Set.of();
        }
    }

    private Set<String> loadListingKeys(Path listing) {
        try {
            JsonNode root = mapper.readTree(Files.readAllBytes(listing));
            Set<String> keys = new HashSet<>();
            // Channels nest videos inside tab playlists; flatten before
            // collecting ids or everything looks Delisted.
            for (JsonNode entry : xyz.dufour.copycast.source.YtListing.leafEntries(root)) {
                keys.add(entry.path("id").asText());
            }
            return keys;
        } catch (Exception e) {
            log.warn("Could not parse {}: {}", listing, e.getMessage());
            return Set.of();
        }
    }

    /**
     * A change token for everything the Mirror Feed is generated from, plus
     * the most recent modification time. Used to cache generated feeds and
     * answer conditional requests from podcast clients.
     */
    public record Fingerprint(long token, Instant lastModified) {
    }

    public Fingerprint fingerprint(String id) {
        long token = 1;
        Instant last = Instant.EPOCH;
        for (Path path : List.of(dir(id).resolve("mirror.json"), feedXml(id),
                listingJson(id), episodesDir(id))) {
            long millis;
            try {
                millis = Files.getLastModifiedTime(path).toMillis();
            } catch (IOException e) {
                millis = -1;
            }
            token = token * 31 + millis;
            if (millis > last.toEpochMilli()) {
                last = Instant.ofEpochMilli(millis);
            }
        }
        return new Fingerprint(token, last);
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
