package xyz.dufour.copycast.util;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import javax.xml.transform.OutputKeys;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerFactory;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public final class XmlUtil {

    public static final String ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd";
    public static final String ATOM_NS = "http://www.w3.org/2005/Atom";

    private XmlUtil() {
    }

    private static DocumentBuilderFactory safeFactory() {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        try {
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        } catch (ParserConfigurationException | IllegalArgumentException ignored) {
            // Best effort; not all parsers support every feature.
        }
        return factory;
    }

    public static Document parse(byte[] bytes) throws IOException {
        try {
            return safeFactory().newDocumentBuilder().parse(new ByteArrayInputStream(bytes));
        } catch (Exception e) {
            throw new IOException("Invalid XML: " + e.getMessage(), e);
        }
    }

    public static Document newDocument() {
        try {
            return safeFactory().newDocumentBuilder().newDocument();
        } catch (ParserConfigurationException e) {
            throw new IllegalStateException(e);
        }
    }

    public static String serialize(Node node, boolean xmlDeclaration) throws IOException {
        try {
            Transformer transformer = TransformerFactory.newInstance().newTransformer();
            transformer.setOutputProperty(OutputKeys.ENCODING, "UTF-8");
            transformer.setOutputProperty(OutputKeys.OMIT_XML_DECLARATION, xmlDeclaration ? "no" : "yes");
            StringWriter writer = new StringWriter();
            transformer.transform(new DOMSource(node), new StreamResult(writer));
            return writer.toString();
        } catch (Exception e) {
            throw new IOException("Failed to serialize XML", e);
        }
    }

    /** Direct child elements with the given local name, any namespace. */
    public static List<Element> children(Element parent, String localName) {
        List<Element> result = new ArrayList<>();
        NodeList nodes = parent.getChildNodes();
        for (int i = 0; i < nodes.getLength(); i++) {
            if (nodes.item(i) instanceof Element element && localName.equals(localName(element))) {
                result.add(element);
            }
        }
        return result;
    }

    public static Optional<Element> child(Element parent, String localName) {
        List<Element> matches = children(parent, localName);
        return matches.isEmpty() ? Optional.empty() : Optional.of(matches.getFirst());
    }

    /** First direct child with the given namespace and local name. */
    public static Optional<Element> childNs(Element parent, String namespace, String localName) {
        NodeList nodes = parent.getChildNodes();
        for (int i = 0; i < nodes.getLength(); i++) {
            if (nodes.item(i) instanceof Element element
                    && localName.equals(localName(element))
                    && namespace.equals(element.getNamespaceURI())) {
                return Optional.of(element);
            }
        }
        return Optional.empty();
    }

    public static String childText(Element parent, String localName) {
        return child(parent, localName).map(e -> e.getTextContent().trim()).orElse(null);
    }

    private static String localName(Element element) {
        return element.getLocalName() != null ? element.getLocalName() : element.getTagName();
    }
}
