package xyz.dufour.copycast;

import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;
import xyz.dufour.copycast.config.CopycastProperties;

import java.nio.file.Path;

public final class TestSupport {

    public static final String YTDLP_VERSION = "2026.06.09";

    private TestSupport() {
    }

    public static CopycastProperties props(Path dataDir) {
        return new CopycastProperties("http://localhost:8080", dataDir, 24, 15,
                new CopycastProperties.YtDlpProperties(YTDLP_VERSION, null, false));
    }

    public static ObjectMapper mapper() {
        // Jackson 3: java.time support is built in, no module registration.
        return JsonMapper.builder().build();
    }
}
