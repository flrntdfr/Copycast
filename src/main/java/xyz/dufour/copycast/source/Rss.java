package xyz.dufour.copycast.source;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import xyz.dufour.copycast.util.XmlUtil;

import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Optional;

/** Parsing helpers for original podcast RSS feeds. */
public final class Rss {

    public record Channel(String title, String description, String imageUrl, String author,
                          Element channelElement, List<Element> items) {
    }

    private Rss() {
    }

    public static Optional<Channel> parse(Document doc) {
        Element root = doc.getDocumentElement();
        if (root == null || !"rss".equals(root.getLocalName() != null ? root.getLocalName() : root.getTagName())) {
            return Optional.empty();
        }
        return XmlUtil.child(root, "channel").map(channel -> new Channel(
                XmlUtil.childText(channel, "title"),
                XmlUtil.childText(channel, "description"),
                imageUrl(channel),
                XmlUtil.childNs(channel, XmlUtil.ITUNES_NS, "author")
                        .map(e -> e.getTextContent().trim()).orElse(null),
                channel,
                XmlUtil.children(channel, "item")));
    }

    private static String imageUrl(Element channel) {
        return XmlUtil.child(channel, "image")
                .map(image -> XmlUtil.childText(image, "url"))
                .or(() -> XmlUtil.childNs(channel, XmlUtil.ITUNES_NS, "image")
                        .map(e -> e.getAttribute("href")))
                .orElse(null);
    }

    /** The stable identity of an item: its guid, falling back to the enclosure URL. */
    public static String itemIdentity(Element item) {
        String guid = XmlUtil.childText(item, "guid");
        if (guid != null && !guid.isBlank()) {
            return guid;
        }
        return enclosureUrl(item);
    }

    public static String enclosureUrl(Element item) {
        return XmlUtil.child(item, "enclosure")
                .map(e -> e.getAttribute("url"))
                .filter(url -> !url.isBlank())
                .orElse(null);
    }

    public static Instant pubDate(Element item) {
        String text = XmlUtil.childText(item, "pubDate");
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            return ZonedDateTime.parse(text, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
        } catch (Exception e) {
            return null;
        }
    }
}
