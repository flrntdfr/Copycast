package xyz.dufour.copycast.refresh;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
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
import xyz.dufour.copycast.source.YtListing;
import xyz.dufour.copycast.util.Http;
import xyz.dufour.copycast.util.Ids;
import xyz.dufour.copycast.util.Mime;
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
    private final Set<String> cancelRequested = ConcurrentHashMap.newKeySet();
    private final java.util.Map<String, Process> runningProcesses = new ConcurrentHashMap<>();

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
                refresh(mirrorId, trigger);
            } finally {
                busy.remove(mirrorId);
                cancelRequested.remove(mirrorId);
                runningProcesses.remove(mirrorId);
            }
        });
    }

    /**
     * Archives a single back-catalog Episode on demand, identified by its
     * key. Queued on the same serial worker as Refreshes and skipped if the
     * Mirror is already busy. Once archived, the Episode joins the Mirror
     * Feed and survives future capped Refreshes.
     */
    public void requestEpisode(String mirrorId, String episodeKey) {
        if (store.find(mirrorId).isEmpty() || !busy.add(mirrorId)) {
            return;
        }
        queue.submit(() -> {
            try {
                archiveOne(mirrorId, episodeKey);
            } finally {
                busy.remove(mirrorId);
                cancelRequested.remove(mirrorId);
                runningProcesses.remove(mirrorId);
            }
        });
    }

    private void archiveOne(String mirrorId, String episodeKey) {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        log.info("Archiving episode {} of {} ({})", episodeKey, mirror.displayTitle(), mirrorId);
        mirror.setLastAttemptAt(Instant.now());
        try {
            String failure = mirror.getType() == SourceType.RSS
                    ? archiveRssEpisode(mirror, episodeKey)
                    : archiveYtDlpEpisode(mirror, episodeKey);
            if (failure == null) {
                mirror.setLastSuccessAt(Instant.now());
                mirror.setLastError(null);
            } else {
                mirror.setLastError(failure);
            }
        } catch (Exception e) {
            String message = e.getMessage() != null ? e.getMessage() : e.toString();
            log.warn("Archiving episode {} of {} failed: {}", episodeKey, mirrorId, message);
            mirror.setLastError(message);
        }
        store.save(mirror);
    }

    /** Re-fetches the Source feed, finds the item by key, and archives it. */
    private String archiveRssEpisode(Mirror mirror, String episodeKey)
            throws IOException, InterruptedException {
        byte[] xml = Http.get(mirror.getSourceUrl(), Duration.ofMinutes(2));
        Document doc = XmlUtil.parse(xml);
        Rss.Channel channel = Rss.parse(doc)
                .orElseThrow(() -> new IOException("Source is not a valid RSS feed anymore"));
        // Refresh the stored feed so the catalog reflects the Source.
        writeAtomically(store.feedXml(mirror.getId()), xml);
        fetchArtworkIfMissing(mirror.getId(), MirrorStore.COVER, channel.imageUrl());
        for (Element item : channel.items()) {
            String identity = Rss.itemIdentity(item);
            if (identity != null && Ids.episodeKey(identity).equals(episodeKey)) {
                return archiveRssItem(mirror, item);
            }
        }
        return "Episode is no longer listed by the Source";
    }

    /** Finds the entry's URL in the stored listing and downloads just it. */
    private String archiveYtDlpEpisode(Mirror mirror, String episodeKey)
            throws IOException, InterruptedException {
        Path listing = store.listingJson(mirror.getId());
        if (!Files.isRegularFile(listing)) {
            return "No listing available yet; refresh first";
        }
        JsonNode root = mapper.readTree(Files.readAllBytes(listing));
        String url = null;
        for (JsonNode entry : YtListing.leafEntries(root)) {
            if (episodeKey.equals(entry.path("id").asString(null))) {
                url = entry.path("url").asString(entry.path("webpage_url").asString(null));
                break;
            }
        }
        if (url == null) {
            return "Episode is no longer listed by the Source";
        }
        Path episodesDir = store.episodesDir(mirror.getId());
        Files.createDirectories(episodesDir);
        YtDlp.Result download = runCancellable(mirror.getId(), Duration.ofHours(2), List.of(
                "--no-progress", "--no-warnings", "--ignore-errors",
                "--download-archive", store.archiveFile(mirror.getId()).toString(),
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-x", "--write-info-json", "--write-thumbnail", "--no-write-playlist-metafiles",
                "-o", episodesDir.resolve("%(id)s.%(ext)s").toString(),
                url));
        return download.ok() ? null : "Download failed: " + download.stderrTail();
    }

    /**
     * Cancels an in-flight Refresh: the running yt-dlp process is terminated
     * (it leaves .part files and its download archive, so the next Refresh
     * resumes where it stopped). Used when a Mirror is paused mid-download.
     */
    public void cancel(String mirrorId) {
        if (!busy.contains(mirrorId)) {
            return;
        }
        cancelRequested.add(mirrorId);
        Process process = runningProcesses.get(mirrorId);
        if (process != null) {
            YtDlp.destroyTree(process, false);
        }
    }

    private boolean isCancelled(String mirrorId) {
        return cancelRequested.contains(mirrorId);
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

    private void refresh(String mirrorId, Trigger trigger) {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        // The Mirror may have been paused while this run sat in the queue.
        if (mirror.isPaused() && trigger != Trigger.MANUAL) {
            return;
        }
        log.info("Refreshing {} ({})", mirror.displayTitle(), mirrorId);
        mirror.setLastAttemptAt(Instant.now());
        try {
            String warnings = mirror.getType() == SourceType.RSS
                    ? refreshRss(mirror)
                    : refreshYtDlp(mirror);
            if (!isCancelled(mirrorId)) {
                mirror.setLastSuccessAt(Instant.now());
            }
            mirror.setLastError(warnings);
        } catch (Exception e) {
            String message = e.getMessage() != null ? e.getMessage() : e.toString();
            log.warn("Refresh of {} failed: {}", mirrorId, message);
            mirror.setLastError(message);
        }
        if (isCancelled(mirrorId)) {
            log.info("Refresh of {} cancelled (paused)", mirrorId);
            mirror.setLastError("Paused mid-refresh; downloads resume on the next refresh");
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
        fetchArtworkIfMissing(mirror.getId(), MirrorStore.COVER, channel.imageUrl());

        List<Element> items = channel.items();
        if (mirror.getCap() != null && items.size() > mirror.getCap()) {
            items = items.subList(0, mirror.getCap());
        }
        Files.createDirectories(store.episodesDir(mirror.getId()));
        int failures = 0;
        String lastFailure = null;
        for (Element item : items) {
            if (isCancelled(mirror.getId())) {
                break;
            }
            String fail = archiveRssItem(mirror, item);
            if (fail != null) {
                failures++;
                lastFailure = fail;
            }
        }
        return failures == 0 ? null : failures + " episode(s) failed, last error: " + lastFailure;
    }

    /**
     * Archives one RSS item: downloads its enclosure if not already present,
     * captures its metadata permanently (item.xml) so it survives the Source
     * dropping it, and archives its artwork. Returns null on success (or a
     * skip), else a short failure message.
     */
    private String archiveRssItem(Mirror mirror, Element item) throws IOException, InterruptedException {
        String identity = Rss.itemIdentity(item);
        String enclosure = Rss.enclosureUrl(item);
        if (identity == null || enclosure == null) {
            return null;
        }
        String key = Ids.episodeKey(identity);
        Path episodesDir = store.episodesDir(mirror.getId());
        Files.createDirectories(episodesDir);
        if (store.findAudio(mirror.getId(), key).isEmpty()) {
            YtDlp.Result result = runCancellable(mirror.getId(), Duration.ofHours(2), List.of(
                    "--no-progress", "--no-warnings",
                    "--download-archive", store.archiveFile(mirror.getId()).toString(),
                    // -x without --audio-format: enclosures that are already
                    // audio are archived byte-identical, never re-encoded.
                    "-x",
                    "-o", episodesDir.resolve(key + ".%(ext)s").toString(),
                    enclosure));
            if (isCancelled(mirror.getId())) {
                return null;
            }
            if (!result.ok() || store.findAudio(mirror.getId(), key).isEmpty()) {
                return result.stderrTail();
            }
        }
        Path itemXml = episodesDir.resolve(key + ".item.xml");
        if (!Files.exists(itemXml)) {
            writeAtomically(itemXml, XmlUtil.serialize(item, true).getBytes(StandardCharsets.UTF_8));
        }
        fetchArtworkIfMissing(mirror.getId(), key, Rss.itemImageUrl(item));
        return null;
    }

    /** Returns a warning summary, or null if everything succeeded. */
    private String refreshYtDlp(Mirror mirror) throws IOException, InterruptedException {
        YtDlp.Result listing = runCancellable(mirror.getId(), Duration.ofMinutes(15),
                List.of("-J", "--flat-playlist", "--no-warnings", mirror.getSourceUrl()));
        if (isCancelled(mirror.getId())) {
            return null;
        }
        if (!listing.ok()) {
            throw new IOException("Listing failed: " + listing.stderrTail());
        }
        writeAtomically(store.listingJson(mirror.getId()), listing.stdout().getBytes(StandardCharsets.UTF_8));
        JsonNode root = mapper.readTree(listing.stdout());
        updateMeta(mirror,
                root.path("title").asString(null),
                root.path("description").asString(null),
                ProbeService.thumbnail(root),
                root.path("uploader").asString(root.path("channel").asString(null)));
        String service = YtListing.serviceName(root);
        if (service != null) {
            mirror.setService(service);
        }
        fetchArtworkIfMissing(mirror.getId(), MirrorStore.COVER, ProbeService.thumbnail(root));

        Path episodesDir = store.episodesDir(mirror.getId());
        Files.createDirectories(episodesDir);
        List<String> args = new ArrayList<>(List.of(
                "--no-progress", "--no-warnings", "--ignore-errors",
                "--download-archive", store.archiveFile(mirror.getId()).toString(),
                // Prefer a native AAC stream so extraction is a lossless remux;
                // only truly video-only sources get converted.
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-x",
                "--write-info-json", "--write-thumbnail", "--no-write-playlist-metafiles",
                "-o", episodesDir.resolve("%(id)s.%(ext)s").toString()));
        if (mirror.getCap() != null) {
            args.add("--playlist-end");
            args.add(String.valueOf(mirror.getCap()));
        }
        args.add(mirror.getSourceUrl());
        YtDlp.Result download = runCancellable(mirror.getId(), Duration.ofHours(12), args);
        if (isCancelled(mirror.getId()) || download.ok()) {
            return null;
        }
        return "Some downloads failed: " + download.stderrTail();
    }

    private YtDlp.Result runCancellable(String mirrorId, Duration timeout, List<String> args)
            throws IOException, InterruptedException {
        if (isCancelled(mirrorId)) {
            return new YtDlp.Result(143, "", "cancelled before start");
        }
        try {
            return ytDlp.run(store.dir(mirrorId), timeout, args, process -> {
                runningProcesses.put(mirrorId, process);
                // Cancel may have raced with process start; never leave a
                // process running for a Mirror that was just paused.
                if (isCancelled(mirrorId)) {
                    YtDlp.destroyTree(process, false);
                }
            });
        } finally {
            runningProcesses.remove(mirrorId);
        }
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

    /**
     * Archives channel or episode Artwork next to the audio. Best effort:
     * artwork must never fail a Refresh, and existing files are kept (they
     * may be the only surviving copy).
     */
    private void fetchArtworkIfMissing(String mirrorId, String baseName, String url) {
        if (url == null || url.isBlank() || store.findArtwork(mirrorId, baseName).isPresent()) {
            return;
        }
        try {
            Http.Content content = Http.getContent(url, Duration.ofMinutes(1));
            String ext = Mime.imageExtension(content.contentType(), url);
            writeAtomically(store.episodesDir(mirrorId).resolve(baseName + "." + ext), content.bytes());
        } catch (Exception e) {
            log.debug("Artwork fetch failed for {} ({}): {}", mirrorId, url, e.getMessage());
        }
    }

    private static void writeAtomically(Path target, byte[] bytes) throws IOException {
        Path tmp = target.resolveSibling(target.getFileName() + ".tmp");
        Files.write(tmp, bytes);
        Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
