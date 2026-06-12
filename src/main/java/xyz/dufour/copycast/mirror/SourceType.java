package xyz.dufour.copycast.mirror;

/**
 * How a Source is processed. RSS sources keep their original feed XML as the
 * metadata source of truth; everything else goes through yt-dlp end to end.
 */
public enum SourceType {
    RSS,
    YTDLP
}
