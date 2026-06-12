package xyz.dufour.copycast;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
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
        return JsonMapper.builder().addModule(new JavaTimeModule()).build();
    }
}
