package xyz.dufour.copycast.util;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * Deterministic identifiers. A Mirror ID is minted from the Source URL at
 * creation time and then frozen: retargeting the Source later does not
 * change the ID (see the design session — feed URLs must never move).
 */
public final class Ids {

    private Ids() {
    }

    public static String mirrorId(String sourceUrl) {
        return hash16(sourceUrl);
    }

    public static String episodeKey(String guidOrEnclosureUrl) {
        return hash16(guidOrEnclosureUrl);
    }

    private static String hash16(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(input.trim().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes, 0, 8);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
