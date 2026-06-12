package xyz.dufour.copycast.refresh;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.source.ProbeService;
import xyz.dufour.copycast.source.Rss;
import xyz.dufour.copycast.util.Http;
import xyz.dufour.copycast.util.Ids;
import xyz.dufour.copycast.util.XmlUtil;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Runs Refreshes one at a time. Triggers: the scheduler (refresh-hours),
 * a manual action in the UI, or a fetch of the Mirror Feed (with cooldown).
 * A failed Refresh never degrades the Mirror Feed.
 */
@Service
public class RefreshService {

    public enum Trigger {
        MANUAL, SCHEDULED, FEED_FETCH
    }

    private static final Logger log = LoggerFactory.getLogger(RefreshService.class);

    private final MirrorStore store;
    private final YtDlp ytDlp;
    private final ObjectMapper mapper;
    private final CopycastProperties props;
    private final ExecutorService queue = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "refresh-queue");
        thread.setDaemon(true);
        return thread;
    });
    private final Set<String> busy = ConcurrentHashMap.newKeySet();

    public RefreshService(MirrorStore store, YtDlp ytDlp, ObjectMapper mapper, CopycastProperties props) {
        this.store = store;
        this.ytDlp = ytDlp;
        this.mapper = mapper;
        this.props = props;
    }

    public boolean isBusy(String mirrorId) {
        return busy.contains(mirrorId);
    }

    public void request(String mirrorId, Trigger trigger) {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        if (trigger != Trigger.MANUAL && mirror.isPaused()) {
            return;
        }
        if (trigger == Trigger.FEED_FETCH && withinCooldown(mirror)) {
            return;
        }
        if (!busy.add(mirrorId)) {
            return;
        }
        queue.submit(() -> {
            try {
                refresh(mirrorId);
            } finally {
                busy.remove(mirrorId);
            }
        });
    }

    private boolean withinCooldown(Mirror mirror) {
        Instant last = mirror.getLastAttemptAt();
        return last != null
                && last.plus(Duration.ofMinutes(props.fetchCooldownMinutes())).isAfter(Instant.now());
    }

    @Scheduled(initialDelayString = "PT1M", fixedDelayString = "PT10M")
    public void scheduledScan() {
        Duration cadence = Duration.ofHours(props.refreshHours());
        for (Mirror mirror : store.list()) {
            Instant last = mirror.getLastAttemptAt();
            if (!mirror.isPaused() && (last == null || last.plus(cadence).isBefore(Instant.now()))) {
                request(mirror.getId(), Trigger.SCHEDULED);
            }
        }
    }

    private void refresh(String mirrorId) {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        log.info("Refreshing {} ({})", mirror.displayTitle(), mirrorId);
        mirror.setLastAttemptAt(Instant.now());
        try {
            String warnings = mirror.getType() == SourceType.RSS
                    ? refreshRss(mirror)
                    : refreshYtDlp(mirror);
            mirror.setLastSuccessAt(Instant.now());
            mirror.setLastError(warnings);
        } catch (Exception e) {
            String message = e.getMessage() != null ? e.getMessage() : e.toString();
            log.warn("Refresh of {} failed: {}", mirrorId, message);
            mirror.setLastError(message);
        }
        store.save(mirror);
    }

    /** Returns a warning summary, or null if everything succeeded. */
    private String refreshRss(Mirror mirror) throws IOException, InterruptedException {
        byte[] xml = Http.get(mirror.getSourceUrl(), Duration.ofMinutes(2));
        Document doc = XmlUtil.parse(xml);
        Rss.Channel channel = Rss.parse(doc)
                .orElseThrow(() -> new IOException("Source is not a valid RSS feed anymore"));
        writeAtomically(store.feedXml(mirror.getId()), xml);
        updateMeta(mirror, channel.title(), channel.description(), channel.imageUrl(), channel.author());

        List<Element> items = channel.items();
        if (mirror.getCap() != null && items.size() > mirror.getCap()) {
            items = items.subList(0, mirror.getCap());
        }
        Path episodesDir = store.episodesDir(mirror.getId());
        Files.createDirectories(episodesDir);
        int failures = 0;
        String lastFailure = null;
        for (Element item : items) {
            String identity = Rss.itemIdentity(item);
            String enclosure = Rss.enclosureUrl(item);
            if (identity == null || enclosure == null) {
                continue;
            }
            String key = Ids.episodeKey(identity);
            if (store.findAudio(mirror.getId(), key).isEmpty()) {
                YtDlp.Result result = ytDlp.run(store.dir(mirror.getId()), Duration.ofHours(2), List.of(
                        "--no-progress", "--no-warnings",
                        "--download-archive", store.archiveFile(mirror.getId()).toString(),
                        // -x without --audio-format: enclosures that are already
                        // audio are archived byte-identical, never re-encoded.
                        "-x",
                        "-o", episodesDir.resolve(key + ".%(ext)s").toString(),
                        enclosure));
                if (!result.ok() || store.findAudio(mirror.getId(), key).isEmpty()) {
                    failures++;
                    lastFailure = result.stderrTail();
                    continue;
                }
            }
            // Capture the item's metadata permanently at archive time so the
            // Episode survives the Source dropping it (union semantics).
            Path itemXml = episodesDir.resolve(key + ".item.xml");
            if (!Files.exists(itemXml)) {
                writeAtomically(itemXml, XmlUtil.serialize(item, true).getBytes(StandardCharsets.UTF_8));
            }
        }
        return failures == 0 ? null : failures + " episode(s) failed, last error: " + lastFailure;
    }

    /** Returns a warning summary, or null if everything succeeded. */
    private String refreshYtDlp(Mirror mirror) throws IOException, InterruptedException {
        YtDlp.Result listing = ytDlp.run(store.dir(mirror.getId()), Duration.ofMinutes(15),
                List.of("-J", "--flat-playlist", "--no-warnings", mirror.getSourceUrl()));
        if (!listing.ok()) {
            throw new IOException("Listing failed: " + listing.stderrTail());
        }
        writeAtomically(store.listingJson(mirror.getId()), listing.stdout().getBytes(StandardCharsets.UTF_8));
        JsonNode root = mapper.readTree(listing.stdout());
        updateMeta(mirror,
                root.path("title").asText(null),
                root.path("description").asText(null),
                ProbeService.thumbnail(root),
                root.path("uploader").asText(root.path("channel").asText(null)));

        Path episodesDir = store.episodesDir(mirror.getId());
        Files.createDirectories(episodesDir);
        List<String> args = new ArrayList<>(List.of(
                "--no-progress", "--no-warnings", "--ignore-errors",
                "--download-archive", store.archiveFile(mirror.getId()).toString(),
                // Prefer a native AAC stream so extraction is a lossless remux;
                // only truly video-only sources get converted.
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-x",
                "--write-info-json", "--no-write-playlist-metafiles",
                "-o", episodesDir.resolve("%(id)s.%(ext)s").toString()));
        if (mirror.getCap() != null) {
            args.add("--playlist-end");
            args.add(String.valueOf(mirror.getCap()));
        }
        args.add(mirror.getSourceUrl());
        YtDlp.Result download = ytDlp.run(store.dir(mirror.getId()), Duration.ofHours(12), args);
        return download.ok() ? null : "Some downloads failed: " + download.stderrTail();
    }

    private void updateMeta(Mirror mirror, String title, String description, String imageUrl, String author) {
        if (title != null && !title.isBlank()) {
            mirror.setTitle(title);
        }
        if (description != null && !description.isBlank()) {
            mirror.setDescription(description);
        }
        if (imageUrl != null && !imageUrl.isBlank()) {
            mirror.setImageUrl(imageUrl);
        }
        if (author != null && !author.isBlank()) {
            mirror.setAuthor(author);
        }
    }

    private static void writeAtomically(Path target, byte[] bytes) throws IOException {
        Path tmp = target.resolveSibling(target.getFileName() + ".tmp");
        Files.write(tmp, bytes);
        Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
