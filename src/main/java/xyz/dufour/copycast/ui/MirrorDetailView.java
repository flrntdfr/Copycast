package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.AttachEvent;
import com.vaadin.flow.component.DetachEvent;
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
import xyz.dufour.copycast.mirror.Episode;
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

    private String mirrorId;
    private final Grid<Episode> grid = new Grid<>();
    private final VerticalLayout header = new VerticalLayout();
    private Registration pollRegistration;
    private List<Episode> lastEpisodes;

    public MirrorDetailView(MirrorStore store, RefreshService refresh, FeedGenerator feeds) {
        this.store = store;
        this.refresh = refresh;
        this.feeds = feeds;
        setSizeFull();
        addClassName("copycast-view");
        grid.addClassName("copycast-grid");
        header.setPadding(false);
        add(header);
        configureGrid();
        add(grid);
        expand(grid);
    }

    @Override
    public void setParameter(BeforeEvent event, String parameter) {
        this.mirrorId = parameter;
        if (store.find(parameter).isEmpty()) {
            event.forwardTo(MainView.class);
            return;
        }
        reload();
    }

    private void configureGrid() {
        grid.setSizeFull();
        grid.addColumn(Episode::title).setHeader("Episode").setFlexGrow(3);
        grid.addColumn(e -> e.pubDate() != null ? DATE.format(e.pubDate()) : "")
                .setHeader("Published").setFlexGrow(0).setWidth("130px");
        grid.addColumn(e -> UiSupport.humanSize(e.sizeBytes()))
                .setHeader("Size").setFlexGrow(0).setWidth("110px");
        grid.addColumn(new ComponentRenderer<>(episode -> {
            Span badge = new Span(episode.delisted() ? "Delisted by Source" : "Listed");
            badge.getElement().getStyle().set("color", episode.delisted() ? "var(--lumo-error-text-color)"
                    : "var(--lumo-success-text-color)");
            return badge;
        })).setHeader("Source status").setFlexGrow(0).setWidth("170px");
        grid.addColumn(new ComponentRenderer<>(episode -> {
            Mirror mirror = store.find(mirrorId).orElse(null);
            if (mirror == null) {
                return new Span();
            }
            Anchor listen = new Anchor(feeds.mediaUrl(mirror, episode.fileName()), "Download");
            listen.setTarget("_blank");
            return listen;
        })).setHeader("").setFlexGrow(0).setWidth("110px");

        // Clicking a row expands it: description plus an inline audio player.
        grid.setItemDetailsRenderer(new ComponentRenderer<>(this::episodeDetails));
        grid.setDetailsVisibleOnClick(true);
    }

    private VerticalLayout episodeDetails(Episode episode) {
        VerticalLayout details = new VerticalLayout();
        details.setPadding(false);
        details.setSpacing(false);
        String description = UiSupport.stripHtml(episode.description());
        if (!description.isEmpty()) {
            Paragraph text = new Paragraph(description);
            text.addClassName("copycast-description");
            details.add(text);
        }
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror != null) {
            Div playerWrapper = new Div();
            playerWrapper.setWidthFull();
            Element audio = new Element("audio");
            audio.setAttribute("controls", true);
            audio.setAttribute("preload", "none");
            audio.setAttribute("src", feeds.mediaUrl(mirror, episode.fileName()));
            audio.getStyle().set("width", "100%");
            playerWrapper.getElement().appendChild(audio);
            details.add(playerWrapper);
        }
        return details;
    }

    private void reload() {
        Mirror mirror = store.find(mirrorId).orElse(null);
        if (mirror == null) {
            return;
        }
        header.removeAll();

        HorizontalLayout titleRow = new HorizontalLayout();
        titleRow.setAlignItems(FlexComponent.Alignment.CENTER);
        String imageSrc = store.findArtwork(mirrorId, MirrorStore.COVER)
                .map(file -> feeds.mediaUrl(mirror, file.getFileName().toString()))
                .orElse(mirror.getImageUrl());
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

        String description = UiSupport.stripHtml(mirror.getDescription());
        if (!description.isEmpty()) {
            Paragraph descriptionText = new Paragraph(description);
            descriptionText.addClassName("copycast-description");
            header.add(descriptionText);
        }
        header.add(new Paragraph("Source: " + mirror.getSourceUrl()));

        TextField feedUrl = new TextField("Mirror Feed (subscribe to this)");
        feedUrl.setValue(feeds.feedUrl(mirror));
        feedUrl.setReadOnly(true);
        feedUrl.setWidthFull();
        Button copy = new Button("Copy", e -> UiSupport.copyToClipboard(feeds.feedUrl(mirror)));
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

        // Only reset the grid when the data changed: resetting collapses the
        // expanded row and stops an inline player mid-playback.
        List<Episode> episodes = store.episodes(mirror);
        if (!episodes.equals(lastEpisodes)) {
            grid.setItems(episodes);
            lastEpisodes = episodes;
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
            mirror.setSourceUrl(value);
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
