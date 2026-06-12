package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.AttachEvent;
import com.vaadin.flow.component.DetachEvent;
import com.vaadin.flow.component.UI;
import com.vaadin.flow.component.button.Button;
import com.vaadin.flow.component.button.ButtonVariant;
import com.vaadin.flow.component.dialog.Dialog;
import com.vaadin.flow.component.grid.Grid;
import com.vaadin.flow.component.html.H1;
import com.vaadin.flow.component.html.Paragraph;
import com.vaadin.flow.component.html.Span;
import com.vaadin.flow.component.icon.Icon;
import com.vaadin.flow.component.icon.VaadinIcon;
import com.vaadin.flow.component.notification.Notification;
import com.vaadin.flow.component.notification.NotificationVariant;
import com.vaadin.flow.component.orderedlayout.FlexComponent;
import com.vaadin.flow.component.orderedlayout.HorizontalLayout;
import com.vaadin.flow.component.orderedlayout.VerticalLayout;
import com.vaadin.flow.component.progressbar.ProgressBar;
import com.vaadin.flow.component.textfield.IntegerField;
import com.vaadin.flow.component.textfield.TextField;
import com.vaadin.flow.router.PageTitle;
import com.vaadin.flow.router.Route;
import com.vaadin.flow.router.RouterLink;
import com.vaadin.flow.shared.Registration;
import xyz.dufour.copycast.feed.FeedGenerator;
import xyz.dufour.copycast.mirror.Mirror;
import xyz.dufour.copycast.mirror.MirrorStore;
import xyz.dufour.copycast.refresh.RefreshService;
import xyz.dufour.copycast.source.ProbeResult;
import xyz.dufour.copycast.source.ProbeService;
import xyz.dufour.copycast.ytdlp.YtDlp;

import java.util.concurrent.CompletableFuture;

@Route("")
@PageTitle("Copycast")
public class MainView extends VerticalLayout {

    private final MirrorStore store;
    private final ProbeService probe;
    private final RefreshService refresh;
    private final FeedGenerator feeds;
    private final YtDlp ytDlp;

    private final TextField urlField = new TextField();
    private final Button mirrorButton = new Button("Mirror");
    private final ProgressBar probing = new ProgressBar();
    private final Span ytDlpStatus = new Span();
    private final Grid<Mirror> grid = new Grid<>();
    private Registration pollRegistration;

    public MainView(MirrorStore store, ProbeService probe, RefreshService refresh,
                    FeedGenerator feeds, YtDlp ytDlp) {
        this.store = store;
        this.probe = probe;
        this.refresh = refresh;
        this.feeds = feeds;
        this.ytDlp = ytDlp;

        setSizeFull();
        add(new H1("Copycast"));
        add(ytDlpStatus);

        urlField.setPlaceholder("Podcast RSS feed, YouTube channel or playlist URL…");
        urlField.setWidthFull();
        urlField.setClearButtonVisible(true);
        mirrorButton.addThemeVariants(ButtonVariant.LUMO_PRIMARY);
        mirrorButton.addClickListener(e -> startProbe());
        urlField.addKeyPressListener(com.vaadin.flow.component.Key.ENTER, e -> startProbe());
        HorizontalLayout inputRow = new HorizontalLayout(urlField, mirrorButton);
        inputRow.setWidthFull();
        inputRow.setAlignItems(FlexComponent.Alignment.BASELINE);
        inputRow.expand(urlField);
        add(inputRow);

        probing.setIndeterminate(true);
        probing.setVisible(false);
        add(probing);

        configureGrid();
        add(grid);
        expand(grid);
        reload();
    }

    private void configureGrid() {
        grid.setSizeFull();
        grid.addColumn(new com.vaadin.flow.data.renderer.ComponentRenderer<>(mirror ->
                        new RouterLink(mirror.displayTitle(), MirrorDetailView.class, mirror.getId())))
                .setHeader("Mirror").setFlexGrow(3);
        grid.addColumn(mirror -> mirror.getType() == null ? "" : mirror.getType().name())
                .setHeader("Type").setFlexGrow(0).setWidth("90px");
        grid.addColumn(mirror -> store.episodes(mirror).size())
                .setHeader("Episodes").setFlexGrow(0).setWidth("110px");
        grid.addColumn(this::statusText).setHeader("Status").setFlexGrow(2);
        grid.addColumn(new com.vaadin.flow.data.renderer.ComponentRenderer<>(this::actions))
                .setHeader("Actions").setFlexGrow(0).setWidth("220px");
    }

    private String statusText(Mirror mirror) {
        if (refresh.isBusy(mirror.getId())) {
            return "Refreshing…";
        }
        if (mirror.isPaused()) {
            return "Paused";
        }
        if (mirror.getLastError() != null) {
            return "Warning (" + UiSupport.relative(mirror.getLastAttemptAt()) + "): " + mirror.getLastError();
        }
        if (mirror.getLastSuccessAt() != null) {
            return "OK, refreshed " + UiSupport.relative(mirror.getLastSuccessAt());
        }
        return "Never refreshed";
    }

    private HorizontalLayout actions(Mirror mirror) {
        Button copy = iconButton(VaadinIcon.COPY, "Copy feed URL",
                () -> UiSupport.copyToClipboard(feeds.feedUrl(mirror)));
        Button refreshNow = iconButton(VaadinIcon.REFRESH, "Refresh now", () -> {
            refresh.request(mirror.getId(), RefreshService.Trigger.MANUAL);
            reload();
        });
        Button pause = iconButton(mirror.isPaused() ? VaadinIcon.PLAY : VaadinIcon.PAUSE,
                mirror.isPaused() ? "Resume" : "Pause", () -> {
                    mirror.setPaused(!mirror.isPaused());
                    store.save(mirror);
                    reload();
                });
        Button delete = iconButton(VaadinIcon.TRASH, "Delete",
                () -> UiSupport.confirm("Delete Mirror",
                        "Delete \"" + mirror.displayTitle() + "\" and all its archived audio? "
                                + "This cannot be undone — if the Source is gone, this is your only copy.",
                        "Delete everything", () -> {
                            try {
                                store.delete(mirror.getId());
                                reload();
                            } catch (Exception ex) {
                                notifyError("Delete failed: " + ex.getMessage());
                            }
                        }));
        return new HorizontalLayout(copy, refreshNow, pause, delete);
    }

    private static Button iconButton(VaadinIcon icon, String tooltip, Runnable action) {
        Button button = new Button(new Icon(icon), e -> action.run());
        button.addThemeVariants(ButtonVariant.LUMO_SMALL, ButtonVariant.LUMO_TERTIARY);
        button.setTooltipText(tooltip);
        return button;
    }

    private void startProbe() {
        String url = urlField.getValue() == null ? "" : urlField.getValue().trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            notifyError("Please enter an http(s) URL");
            return;
        }
        var existing = store.findBySourceUrl(url);
        if (existing.isPresent()) {
            Notification.show("Already mirrored as \"" + existing.get().displayTitle() + "\"",
                    4000, Notification.Position.BOTTOM_START);
            return;
        }
        mirrorButton.setEnabled(false);
        probing.setVisible(true);
        UI ui = UI.getCurrent();
        CompletableFuture.runAsync(() -> {
            ProbeResult result = probe.probe(url);
            ui.access(() -> {
                mirrorButton.setEnabled(true);
                probing.setVisible(false);
                if (result.supported()) {
                    openCreateDialog(url, result);
                } else {
                    notifyError(result.error());
                }
            });
        });
    }

    private void openCreateDialog(String url, ProbeResult result) {
        Dialog dialog = new Dialog();
        dialog.setHeaderTitle("Mirror \"" + (result.title() != null ? result.title() : url) + "\"?");
        VerticalLayout content = new VerticalLayout();
        content.setPadding(false);
        content.add(new Paragraph("Detected as " + result.type() + " with "
                + result.episodeCount() + " episode(s)."));
        if (result.description() != null && !result.description().isBlank()) {
            String snippet = result.description().length() > 300
                    ? result.description().substring(0, 300) + "…"
                    : result.description();
            content.add(new Paragraph(snippet));
        }
        IntegerField cap = new IntegerField("Archive only the latest N episodes");
        cap.setPlaceholder("empty = full backlog");
        cap.setMin(1);
        cap.setWidthFull();
        content.add(cap);
        dialog.add(content);
        Button cancel = new Button("Cancel", e -> dialog.close());
        Button create = new Button("Create Mirror", e -> {
            dialog.close();
            try {
                Mirror mirror = store.create(url, result, cap.getValue());
                refresh.request(mirror.getId(), RefreshService.Trigger.MANUAL);
                urlField.clear();
                reload();
                Notification done = Notification.show("Mirror created. Feed: " + feeds.feedUrl(mirror),
                        6000, Notification.Position.BOTTOM_START);
                done.addThemeVariants(NotificationVariant.LUMO_SUCCESS);
            } catch (Exception ex) {
                notifyError("Could not create Mirror: " + ex.getMessage());
            }
        });
        create.addThemeVariants(ButtonVariant.LUMO_PRIMARY);
        dialog.getFooter().add(cancel, create);
        dialog.open();
    }

    private void reload() {
        grid.setItems(store.list());
        String release = ytDlp.releaseDate() != null ? ", released " + ytDlp.releaseDate() : "";
        if (ytDlp.isReady()) {
            ytDlpStatus.setText("yt-dlp " + ytDlp.version() + release + " — ready");
        } else {
            String error = ytDlp.installError() != null ? ": " + ytDlp.installError() : " (installing…)";
            ytDlpStatus.setText("yt-dlp " + ytDlp.version() + release + " — not available" + error);
        }
    }

    private static void notifyError(String message) {
        Notification notification = Notification.show(message, 5000, Notification.Position.BOTTOM_START);
        notification.addThemeVariants(NotificationVariant.LUMO_ERROR);
    }

    @Override
    protected void onAttach(AttachEvent attachEvent) {
        super.onAttach(attachEvent);
        attachEvent.getUI().setPollInterval(3000);
        pollRegistration = attachEvent.getUI().addPollListener(e -> reload());
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
