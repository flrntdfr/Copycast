package xyz.dufour.copycast.feed;

import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import xyz.dufour.copycast.config.CopycastProperties;
import xyz.dufour.copycast.mirror.Episode;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.mirror.SourceType;
import xyz.dufour.copycast.util.XmlUtil;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * Builds the Mirror Feed: the union of every Episode ever archived. Channel
 * metadata comes from the latest fetched original feed (RSS Sources) or the
 * descriptor; per-Episode metadata was captured at archive time.
 */
@Component
public class FeedGenerator {

    private final MirrorStore store;
    private final CopycastProperties props;

    public FeedGenerator(MirrorStore store, CopycastProperties props) {
        this.store = store;
        this.props = props;
    }

    public String feedUrl(Mirror mirror) {
        return props.normalizedBaseUrl() + "/feed/" + mirror.getId() + "/feed.xml";
    }

    public String mediaUrl(Mirror mirror, String fileName) {
        return props.normalizedBaseUrl() + "/feed/" + mirror.getId() + "/media/"
                + URLEncoder.encode(fileName, StandardCharsets.UTF_8);
    }

    public String generate(Mirror mirror) throws IOException {
        Document out = XmlUtil.newDocument();
        Element rss;
        Element channel;
        Element originalChannel = originalChannel(mirror);
        if (originalChannel != null) {
            Element originalRss = (Element) originalChannel.getParentNode();
            rss = (Element) out.importNode(originalRss, false);
            out.appendChild(rss);
            channel = out.createElement("channel");
            rss.appendChild(channel);
            NodeList children = originalChannel.getChildNodes();
            for (int i = 0; i < children.getLength(); i++) {
                Node child = children.item(i);
                if (child instanceof Element element && "item".equals(local(element))) {
                    continue;
                }
                channel.appendChild(out.importNode(child, true));
            }
            rewriteSelfLink(channel, mirror);
        } else {
            rss = out.createElement("rss");
            rss.setAttribute("xmlns:itunes", XmlUtil.ITUNES_NS);
            out.appendChild(rss);
            channel = out.createElement("channel");
            rss.appendChild(channel);
            appendText(channel, "title", orBlank(mirror.getTitle()));
            appendText(channel, "link", orBlank(mirror.getSourceUrl()));
            appendText(channel, "description", orBlank(mirror.getDescription()));
            if (mirror.getImageUrl() != null) {
                Element image = out.createElementNS(XmlUtil.ITUNES_NS, "itunes:image");
                image.setAttribute("href", mirror.getImageUrl());
                channel.appendChild(image);
            }
            if (mirror.getAuthor() != null) {
                Element author = out.createElementNS(XmlUtil.ITUNES_NS, "itunes:author");
                author.setTextContent(mirror.getAuthor());
                channel.appendChild(author);
            }
        }
        if (!rss.hasAttribute("version")) {
            rss.setAttribute("version", "2.0");
        }
        appendText(channel, "generator", "Copycast");

        List<Episode> episodes = store.episodes(mirror);
        for (Episode episode : episodes) {
            channel.appendChild(buildItem(out, mirror, episode));
        }
        return XmlUtil.serialize(out, true);
    }

    private Element originalChannel(Mirror mirror) {
        if (mirror.getType() != SourceType.RSS) {
            return null;
        }
        Path feed = store.feedXml(mirror.getId());
        if (!Files.isRegularFile(feed)) {
            return null;
        }
        try {
            Document doc = XmlUtil.parse(Files.readAllBytes(feed));
            return XmlUtil.child(doc.getDocumentElement(), "channel").orElse(null);
        } catch (IOException e) {
            return null;
        }
    }

    private Element buildItem(Document out, Mirror mirror, Episode episode) throws IOException {
        Element item = null;
        Path itemXml = store.episodesDir(mirror.getId()).resolve(episode.key() + ".item.xml");
        if (Files.isRegularFile(itemXml)) {
            Document doc = XmlUtil.parse(Files.readAllBytes(itemXml));
            item = (Element) out.importNode(doc.getDocumentElement(), true);
        }
        if (item == null) {
            item = out.createElement("item");
            appendText(item, "title", orBlank(episode.title()));
            if (episode.description() != null) {
                appendText(item, "description", episode.description());
            }
            if (episode.pubDate() != null) {
                appendText(item, "pubDate", DateTimeFormatter.RFC_1123_DATE_TIME
                        .format(ZonedDateTime.ofInstant(episode.pubDate(), ZoneOffset.UTC)));
            }
            if (episode.guid() != null) {
                Element guid = out.createElement("guid");
                guid.setAttribute("isPermaLink", "false");
                guid.setTextContent(episode.guid());
                item.appendChild(guid);
            }
            if (episode.durationSeconds() != null) {
                Element duration = out.createElementNS(XmlUtil.ITUNES_NS, "itunes:duration");
                duration.setTextContent(formatDuration(episode.durationSeconds()));
                item.appendChild(duration);
            }
        }
        Element enclosure = XmlUtil.child(item, "enclosure").orElse(null);
        if (enclosure == null) {
            enclosure = out.createElement("enclosure");
            item.appendChild(enclosure);
        }
        enclosure.setAttribute("url", mediaUrl(mirror, episode.fileName()));
        enclosure.setAttribute("length", String.valueOf(episode.sizeBytes()));
        enclosure.setAttribute("type", episode.mimeType());
        return item;
    }

    private void rewriteSelfLink(Element channel, Mirror mirror) {
        NodeList children = channel.getChildNodes();
        for (int i = 0; i < children.getLength(); i++) {
            if (children.item(i) instanceof Element element
                    && "link".equals(local(element))
                    && XmlUtil.ATOM_NS.equals(element.getNamespaceURI())
                    && "self".equals(element.getAttribute("rel"))) {
                element.setAttribute("href", feedUrl(mirror));
            }
        }
    }

    private static void appendText(Element parent, String name, String text) {
        Element element = parent.getOwnerDocument().createElement(name);
        element.setTextContent(text);
        parent.appendChild(element);
    }

    private static String formatDuration(long seconds) {
        return "%d:%02d:%02d".formatted(seconds / 3600, (seconds % 3600) / 60, seconds % 60);
    }

    private static String local(Element element) {
        return element.getLocalName() != null ? element.getLocalName() : element.getTagName();
    }

    private static String orBlank(String value) {
        return value == null ? "" : value;
    }
}
