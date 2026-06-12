package xyz.dufour.copycast.ytdlp;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.util.Http;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Manages the pinned yt-dlp binary (see docs/adr/0002) and runs it. The
 * image does not bundle yt-dlp; the exact version comes from the config
 * file and is downloaded once into the data directory.
 */
@Component
public class YtDlp {

    public record Result(int exitCode, String stdout, String stderr) {
        public boolean ok() {
            return exitCode == 0;
        }

        public String stderrTail() {
            String[] lines = stderr.strip().split("\n");
            int from = Math.max(0, lines.length - 3);
            return String.join(" | ", List.of(lines).subList(from, lines.length));
        }
    }

    private static final Logger log = LoggerFactory.getLogger(YtDlp.class);

    private final CopycastProperties props;
    private volatile Path binary;
    private volatile String installError;

    public YtDlp(CopycastProperties props) {
        this.props = props;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void installOnStartup() {
        try {
            ensureInstalled();
            log.info("yt-dlp {} ready at {}", version(), binary);
        } catch (Exception e) {
            installError = e.getMessage();
            log.error("yt-dlp {} could not be installed: {}", version(), e.getMessage());
        }
    }

    public synchronized void ensureInstalled() throws IOException, InterruptedException {
        if (binary != null && Files.isExecutable(binary)) {
            return;
        }
        // The Docker image bundles the binary matching the pinned version
        // (the build greps it from application.yaml — see docs/adr/0002).
        Path bundledDir = props.ytdlp().bundledDir();
        if (bundledDir != null) {
            Path bundled = bundledDir.resolve("yt-dlp-" + version());
            if (Files.isExecutable(bundled)) {
                binary = bundled;
                return;
            }
        }
        Path target = props.dataDir().resolve("bin").resolve("yt-dlp-" + version());
        if (Files.isExecutable(target)) {
            binary = target;
            return;
        }
        // Fallback for a config pin that differs from the bundled binary
        // (version bumped without an image rebuild).
        if (!props.ytdlp().autoDownload()) {
            throw new IOException("Binary missing at " + target + " and auto-download is disabled");
        }
        Files.createDirectories(target.getParent());
        String url = "https://github.com/yt-dlp/yt-dlp/releases/download/" + version() + "/" + assetName();
        log.info("Downloading yt-dlp {} from {}", version(), url);
        byte[] bytes = Http.get(url, Duration.ofMinutes(5));
        Path tmp = Files.createTempFile(target.getParent(), "yt-dlp", ".part");
        Files.write(tmp, bytes);
        if (!tmp.toFile().setExecutable(true)) {
            throw new IOException("Could not mark " + tmp + " executable");
        }
        Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING);
        binary = target;
        installError = null;
    }

    public boolean isReady() {
        return binary != null && Files.isExecutable(binary);
    }

    public String installError() {
        return installError;
    }

    public String version() {
        return props.ytdlp().version();
    }

    /** yt-dlp versions are release dates (yyyy.MM.dd). */
    public LocalDate releaseDate() {
        try {
            return LocalDate.parse(version(), DateTimeFormatter.ofPattern("yyyy.MM.dd"));
        } catch (Exception e) {
            return null;
        }
    }

    public Result run(Path workDir, Duration timeout, List<String> args)
            throws IOException, InterruptedException {
        return run(workDir, timeout, args, process -> {
        });
    }

    /**
     * Runs yt-dlp, handing the spawned process to {@code onStart} so callers
     * can cancel it (e.g. when a Mirror is paused mid-download).
     */
    public Result run(Path workDir, Duration timeout, List<String> args,
                      java.util.function.Consumer<Process> onStart)
            throws IOException, InterruptedException {
        ensureInstalled();
        List<String> command = new ArrayList<>();
        command.add(binary.toString());
        command.addAll(args);
        ProcessBuilder builder = new ProcessBuilder(command);
        builder.directory(workDir.toFile());
        Process process = builder.start();
        onStart.accept(process);
        CompletableFuture<String> out = readAsync(process.getInputStream());
        CompletableFuture<String> err = readAsync(process.getErrorStream());
        if (!process.waitFor(timeout.toSeconds(), TimeUnit.SECONDS)) {
            destroyTree(process, true);
            throw new IOException("yt-dlp timed out after " + timeout);
        }
        return new Result(process.exitValue(), drain(out), drain(err));
    }

    /**
     * Terminates yt-dlp and everything it spawned (ffmpeg etc.) — killing
     * only the parent leaves orphans holding the output pipes open.
     */
    public static void destroyTree(Process process, boolean forcibly) {
        process.descendants().forEach(forcibly ? ProcessHandle::destroyForcibly : ProcessHandle::destroy);
        if (forcibly) {
            process.destroyForcibly();
        } else {
            process.destroy();
        }
    }

    private static CompletableFuture<String> readAsync(java.io.InputStream stream) {
        return CompletableFuture.supplyAsync(() -> {
            try (stream) {
                return new String(stream.readAllBytes());
            } catch (IOException e) {
                return "";
            }
        });
    }

    /** Orphaned grandchildren can keep a pipe open; never block on it forever. */
    private static String drain(CompletableFuture<String> future) {
        try {
            return future.get(5, TimeUnit.SECONDS);
        } catch (Exception e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return "";
        }
    }

    private static String assetName() {
        String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        String arch = System.getProperty("os.arch", "").toLowerCase(Locale.ROOT);
        if (os.contains("mac")) {
            return "yt-dlp_macos";
        }
        if (os.contains("win")) {
            return "yt-dlp.exe";
        }
        return arch.contains("aarch64") || arch.contains("arm64")
                ? "yt-dlp_linux_aarch64"
                : "yt-dlp_linux";
    }
}
