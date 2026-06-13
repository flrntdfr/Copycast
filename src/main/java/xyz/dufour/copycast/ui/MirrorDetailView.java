package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.AttachEvent;
import com.vaadin.flow.component.DetachEvent;
import com.vaadin.flow.component.Text;
import com.vaadin.flow.component.button.Button;
import com.vaadin.flow.component.button.ButtonVariant;
import com.vaadin.flow.component.dialog.Dialog;
import com.vaadin.flow.component.grid.Grid;
import com.vaadin.flow.component.html.Anchor;
import com.vaadin.flow.component.html.Div;
import com.vaadin.flow.component.html.H2;
import com.vaadin.flow.component.html.Image;
import com.vaadin.flow.component.html.Paragraph;
import com.vaadin.flow.component.html.Span;
import com.vaadin.flow.dom.Element;
import com.vaadin.flow.component.notification.Notification;
import com.vaadin.flow.component.orderedlayout.FlexComponent;
import com.vaadin.flow.component.orderedlayout.HorizontalLayout;
import com.vaadin.flow.component.orderedlayout.VerticalLayout;
import com.vaadin.flow.component.textfield.TextField;
import com.vaadin.flow.data.renderer.ComponentRenderer;
import com.vaadin.flow.router.BeforeEvent;
import com.vaadin.flow.router.HasUrlParameter;
import com.vaadin.flow.router.PageTitle;
import com.vaadin.flow.router.Route;
import com.vaadin.flow.shared.Registration;
import xyz.dufour.copycast.feed.FeedGenerator;
import xyz.dufour.copycast.mirror.CatalogItem;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.refresh.RefreshService;

import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;

@Route("mirror")
@PageTitle("Mirror — Copycast")
public class MirrorDetailView extends VerticalLayout implements HasUrlParameter<String> {

    private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd")
            .withZone(ZoneId.systemDefault());

    private final MirrorStore store;
    private final RefreshService refresh;
    private final FeedGenerator feeds;

    /** Everything the header renders; it only rebuilds when this changes. */
    private record HeaderSnapshot(String title, boolean paused, String description,
                                  String sourceUrl, String imageSrc, boolean busy, String lastError) {
    }

    private String mirrorId;
    private final Grid<CatalogItem> grid = new Grid<>();
    private final VerticalLayout header = new VerticalLayout();
    private final Span catalogStats = new Span();
    private Registration pollRegistration;
    private List<CatalogItem> lastCatalog;
    private HeaderSnapshot lastHeader;

    public MirrorDetailView(MirrorStore store, RefreshService refresh, FeedGenerator feeds) {
        this.store = store;
        this.refresh = refresh;
        this.feeds = feeds;
        setSizeFull();
        addClassName("copycast-view");
        grid.addClassName("copycast-grid");
        header.setPadding(false);
        add(header);
        catalogStats.addClassName("copycast-stats");
        add(catalogStats);
        configureGrid();
        add(grid);
        expand(grid);
    }

    @Override
    public void setParameter(BeforeEvent event, String parameter) {
        this.mirrorId = parameter;
        // A different Mirror invalidates the render snapshots.
        this.lastHeader = null;
        this.lastCatalog = null;
        if (store.find(parameter).isEmpty()) {
            event.forwardTo(MainView.class);
            return;
        }
        reload();
    }

    private void configureGrid() {
        grid.setSizeFull();
        grid.addColumn(CatalogItem::title).setHeader("Episode").setFlexGrow(3);
        grid.addColumn(i -> i.pubDate() != null ? DATE.format(i.pubDate()) : "")
                .setHeader("Published").setFlexGrow(0).setWidth("130px");
        grid.addColumn(i -> i.archived() ? UiSupport.humanSize(i.sizeBytes()) : "")
                .setHeader("Size").setFlexGrow(0).setWidth("110px");
        grid.addColumn(new ComponentRenderer<>(this::statusBadge))
                .setHeader("Status").setFlexGrow(0).setWidth("160px");
        grid.addColumn(new ComponentRenderer<>(this::rowAction))
                .setHeader("").setFlexGrow(0).setWidth("130px");

        // Clicking a row expands it: description, artwork, and — once
        // archived — an inline audio player.
        grid.setItemDetailsRenderer(new ComponentRenderer<>(this::itemDetails));
        grid.setDetailsVisibleOnClick(true);
    }

    private Span statusBadge(CatalogItem item) {
        String text = switch (item.state()) {
            case LISTED -> "Listed";
            case DELISTED -> "Delisted by Source";
            case AVAILABLE -> "Available";
        };
        String color = switch (item.state()) {
            case LISTED -> "var(--lumo-success-text-color)";
            case DELISTED -> "var(--lumo-error-text-color)";
            case AVAILABLE -> "var(--lumo-secondary-text-color)";
        };
        Span badge = new Span(text);
        badge.getElement().getStyle().set("color", color);
        return badge;
    }

    private com.vaadin.flow.component.Component rowAction(CatalogItem item) {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return new Span();
        }
        if (item.archived()) {
            Anchor download = new Anchor(feeds.mediaUrl(mirror, item.audioFileName()), "Download");
            download.setTarget("_blank");
            return download;
        }
        Button add = new Button("Add to feed", e -> {
            if (refresh.isBusy(mirrorId)) {
                Notification.show("Busy — try again once the current download finishes",
                        3000, Notification.Position.BOTTOM_START);
                return;
            }
            refresh.requestEpisode(mirrorId, item.key());
            Notification.show("Downloading \"" + item.title() + "\"…",
                    3000, Notification.Position.BOTTOM_START);
            reload();
        });
        add.addThemeVariants(ButtonVariant.LUMO_SMALL, ButtonVariant.LUMO_TERTIARY);
        return add;
    }

    private VerticalLayout itemDetails(CatalogItem item) {
        VerticalLayout details = new VerticalLayout();
        details.setPadding(false);
        details.setSpacing(false);

        Mirror mirror = store.find(mirrorId).orElse(null);
        String image = rowImage(mirror, item);
        if (image != null && !image.isBlank()) {
            Image cover = new Image(image, "");
            cover.setWidth("96px");
            cover.setHeight("96px");
            cover.addClassName("copycast-cover");
            details.add(cover);
        }

        String description = UiSupport.stripHtml(item.description());
        if (!description.isEmpty()) {
            Paragraph text = new Paragraph(description);
            text.addClassName("copycast-description");
            details.add(text);
        }

        if (mirror != null && item.archived()) {
            Div playerWrapper = new Div();
            playerWrapper.setWidthFull();
            Element audio = new Element("audio");
            audio.setAttribute("controls", true);
            audio.setAttribute("preload", "none");
            audio.setAttribute("src", feeds.mediaUrl(mirror, item.audioFileName()));
            audio.getStyle().set("width", "100%");
            playerWrapper.getElement().appendChild(audio);
            details.add(playerWrapper);
        } else if (item.state() == CatalogItem.State.AVAILABLE) {
            Span note = new Span("Not yet archived. Use \"Add to feed\" to download it.");
            note.addClassName("copycast-stats");
            details.add(note);
        }
        return details;
    }

    private String rowImage(Mirror mirror, CatalogItem item) {
        if (mirror != null && item.artworkFileName() != null) {
            return feeds.mediaUrl(mirror, item.artworkFileName());
        }
        return item.remoteImageUrl();
    }

    private void reload() {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        String imageSrc = store.findArtwork(mirrorId, MirrorStore.COVER)
                .map(file -> feeds.mediaUrl(mirror, file.getFileName().toString()))
                .orElse(mirror.getImageUrl());
        // Rebuilding the header recreates its buttons; skip when unchanged so
        // the 3s poll doesn't steal focus or flicker.
        HeaderSnapshot snapshot = new HeaderSnapshot(mirror.displayTitle(), mirror.isPaused(),
                mirror.getDescription(), mirror.getSourceUrl(), imageSrc,
                refresh.isBusy(mirrorId), mirror.getLastError());
        if (!snapshot.equals(lastHeader)) {
            rebuildHeader(mirror, imageSrc);
            lastHeader = snapshot;
        }

        // Only reset the grid when the data changed: resetting collapses the
        // expanded row and stops an inline player mid-playback.
        List<CatalogItem> catalog = store.catalog(mirror);
        if (!catalog.equals(lastCatalog)) {
            grid.setItems(catalog);
            lastCatalog = catalog;
            updateCatalogStats(catalog);
        }
    }

    private void updateCatalogStats(List<CatalogItem> catalog) {
        long listed = catalog.stream().filter(i -> i.state() == CatalogItem.State.LISTED).count();
        long available = catalog.stream().filter(i -> i.state() == CatalogItem.State.AVAILABLE).count();
        long delisted = catalog.stream().filter(i -> i.state() == CatalogItem.State.DELISTED).count();
        StringBuilder text = new StringBuilder(listed + " in feed");
        if (available > 0) {
            text.append(" · ").append(available).append(" available to add");
        }
        if (delisted > 0) {
            text.append(" · ").append(delisted).append(" delisted");
        }
        catalogStats.setText(text.toString());
    }

    private void rebuildHeader(Mirror mirror, String imageSrc) {
        header.removeAll();

        HorizontalLayout titleRow = new HorizontalLayout();
        titleRow.setAlignItems(FlexComponent.Alignment.CENTER);
        if (imageSrc != null && !imageSrc.isBlank()) {
            Image image = new Image(imageSrc, "");
            image.setWidth("64px");
            image.setHeight("64px");
            image.addClassName("copycast-cover");
            titleRow.add(image);
        }
        H2 heading = new H2(mirror.displayTitle());
        heading.addClassName("copycast-title");
        titleRow.add(heading);
        if (mirror.isPaused()) {
            titleRow.add(new Span("(paused)"));
        }
        header.add(titleRow);

        Paragraph about = new Paragraph();
        about.addClassName("copycast-description");
        String description = UiSupport.stripHtml(mirror.getDescription());
        if (!description.isEmpty()) {
            about.add(new Text(description + " "));
        }
        Anchor source = new Anchor(mirror.getSourceUrl(), "Source ↗");
        source.setTarget("_blank");
        about.add(source);
        header.add(about);

        TextField feedUrl = new TextField("Mirror Feed (subscribe to this)");
        feedUrl.setValue(feeds.feedUrl(mirror));
        feedUrl.setReadOnly(true);
        feedUrl.setWidthFull();
        Button copy = new Button("Copy");
        UiSupport.copyOnClick(copy, feeds.feedUrl(mirror));
        HorizontalLayout feedRow = new HorizontalLayout(feedUrl, copy);
        feedRow.setWidthFull();
        feedRow.setAlignItems(FlexComponent.Alignment.BASELINE);
        feedRow.expand(feedUrl);
        header.add(feedRow);

        Button back = new Button("← All Mirrors", e -> getUI().ifPresent(ui -> ui.navigate(MainView.class)));
        Button refreshNow = new Button(refresh.isBusy(mirrorId) ? "Refreshing…" : "Refresh now", e -> {
            refresh.request(mirrorId, RefreshService.Trigger.MANUAL);
            reload();
        });
        refreshNow.setEnabled(!refresh.isBusy(mirrorId));
        Button pause = new Button(mirror.isPaused() ? "Resume" : "Pause", e -> {
            mirror.setPaused(!mirror.isPaused());
            store.save(mirror);
            if (mirror.isPaused()) {
                refresh.cancel(mirrorId);
            } else {
                refresh.request(mirrorId, RefreshService.Trigger.MANUAL);
            }
            reload();
        });
        Button retarget = new Button("Change Source URL…", e -> openRetargetDialog(mirror));
        Button delete = new Button("Delete…", e -> UiSupport.confirm("Delete Mirror",
                "Delete \"" + mirror.displayTitle() + "\" and all its archived audio? This cannot be undone.",
                "Delete everything", () -> {
                    try {
                        store.delete(mirrorId);
                        getUI().ifPresent(ui -> ui.navigate(MainView.class));
                    } catch (Exception ex) {
                        Notification.show("Delete failed: " + ex.getMessage());
                    }
                }));
        delete.addThemeVariants(ButtonVariant.LUMO_ERROR, ButtonVariant.LUMO_TERTIARY);
        header.add(new HorizontalLayout(back, refreshNow, pause, retarget, delete));

        if (mirror.getLastError() != null) {
            Paragraph warning = new Paragraph("Last refresh: " + mirror.getLastError());
            warning.getStyle().set("color", "var(--lumo-error-text-color)");
            header.add(warning);
        }
    }

    private void openRetargetDialog(Mirror mirror) {
        Dialog dialog = new Dialog();
        dialog.setHeaderTitle("Change Source URL");
        dialog.add(new Paragraph("The Mirror ID and feed URL stay the same; only where "
                + "new Episodes come from changes. Use this when a podcast moves hosts."));
        TextField url = new TextField("New Source URL");
        url.setValue(mirror.getSourceUrl());
        url.setWidthFull();
        dialog.add(url);
        Button cancel = new Button("Cancel", e -> dialog.close());
        Button save = new Button("Retarget", e -> {
            String value = url.getValue() == null ? "" : url.getValue().trim();
            if (!value.startsWith("http://") && !value.startsWith("https://")) {
                Notification.show("Please enter an http(s) URL");
                return;
            }
            // Don't let a retarget collide with another Mirror's Source.
            var clash = store.findBySource(value)
                    .filter(other -> !other.getId().equals(mirror.getId()));
            if (clash.isPresent()) {
                Notification.show("That Source is already mirrored as \""
                        + clash.get().displayTitle() + "\"");
                return;
            }
            mirror.setSourceUrl(value);
            mirror.setDedupKey(xyz.dufour.copycast.util.Urls.dedupKey(value));
            store.save(mirror);
            dialog.close();
            reload();
        });
        save.addThemeVariants(ButtonVariant.LUMO_PRIMARY);
        dialog.getFooter().add(cancel, save);
        dialog.open();
    }

    @Override
    protected void onAttach(AttachEvent attachEvent) {
        super.onAttach(attachEvent);
        attachEvent.getUI().setPollInterval(3000);
        pollRegistration = attachEvent.getUI().addPollListener(e -> {
            if (mirrorId != null && store.find(mirrorId).isPresent()) {
                reload();
            }
        });
    }

    @Override
    protected void onDetach(DetachEvent detachEvent) {
        if (pollRegistration != null) {
            pollRegistration.remove();
        }
        detachEvent.getUI().setPollInterval(-1);
        super.onDetach(detachEvent);
    }
}
