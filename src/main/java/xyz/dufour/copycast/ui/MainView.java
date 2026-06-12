package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.AttachEvent;
import com.vaadin.flow.component.DetachEvent;
import com.vaadin.flow.component.Key;
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
import com.vaadin.flow.component.progressbar.ProgressBar;
import com.vaadin.flow.component.orderedlayout.VerticalLayout;
import com.vaadin.flow.component.textfield.IntegerField;
import com.vaadin.flow.component.textfield.TextField;
import com.vaadin.flow.data.renderer.ComponentRenderer;
import com.vaadin.flow.data.value.ValueChangeMode;
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

import java.util.List;
import java.util.concurrent.CompletableFuture;

@Route("")
@PageTitle("Copycast")
public class MainView extends VerticalLayout {

    /** Traffic-light health of a Mirror, derived from its last Refresh. */
    private enum Health {
        OK("var(--lumo-success-color)"),
        WARN("#f59e0b"),
        ERROR("var(--lumo-error-color)"),
        PAUSED("var(--lumo-contrast-40pct)");

        final String color;

        Health(String color) {
            this.color = color;
        }
    }

    private final MirrorStore store;
    private final ProbeService probe;
    private final RefreshService refresh;
    private final FeedGenerator feeds;
    private final YtDlp ytDlp;

    /** Everything a grid row renders; rows only re-render when this changes. */
    private record RowSnapshot(String id, String title, String service, boolean paused,
                               Health health, String lastRefreshText, String healthTitle,
                               int episodes, String size, boolean busy) {
    }

    private final TextField urlField = new TextField();
    private final Button mirrorButton = new Button("Mirror");
    private final ProgressBar probing = new ProgressBar();
    private final Span stats = new Span();
    private final Grid<Mirror> grid = new Grid<>();
    private Registration pollRegistration;
    private List<RowSnapshot> lastRows;

    public MainView(MirrorStore store, ProbeService probe, RefreshService refresh,
                    FeedGenerator feeds, YtDlp ytDlp) {
        this.store = store;
        this.probe = probe;
        this.refresh = refresh;
        this.feeds = feeds;
        this.ytDlp = ytDlp;

        setSizeFull();
        addClassName("copycast-view");

        H1 title = new H1("Copycast");
        title.addClassName("copycast-title");
        add(title);

        urlField.setPlaceholder("Podcast RSS feed, YouTube channel or playlist URL…");
        urlField.setWidthFull();
        urlField.setClearButtonVisible(true);
        mirrorButton.addThemeVariants(ButtonVariant.LUMO_PRIMARY);
        mirrorButton.addClickListener(e -> startProbe());
        urlField.addKeyPressListener(Key.ENTER, e -> startProbe());
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

        RouterLink about = new RouterLink("Copycast v" + UiSupport.APP_VERSION, AboutView.class);
        HorizontalLayout footer = new HorizontalLayout(about, stats);
        footer.addClassName("copycast-stats");
        footer.setSpacing(false);
        footer.setAlignItems(FlexComponent.Alignment.BASELINE);
        add(footer);
        setHorizontalComponentAlignment(FlexComponent.Alignment.CENTER, footer);
        reload();
    }

    private void configureGrid() {
        grid.setSizeFull();
        grid.addClassName("copycast-grid");
        grid.addColumn(new ComponentRenderer<>(this::healthDot))
                .setHeader("").setFlexGrow(0).setWidth("52px");
        grid.addColumn(new ComponentRenderer<>(mirror ->
                        new RouterLink(mirror.displayTitle(), MirrorDetailView.class, mirror.getId())))
                .setHeader("Mirror").setFlexGrow(3);
        grid.addColumn(Mirror::displayService)
                .setHeader("Service").setFlexGrow(0).setWidth("110px");
        grid.addColumn(mirror -> store.episodes(mirror).size())
                .setHeader("Episodes").setFlexGrow(0).setWidth("110px");
        grid.addColumn(mirror -> UiSupport.gigabytes(store.sizeOnDiskBytes(mirror.getId())))
                .setHeader("Size").setFlexGrow(0).setWidth("110px");
        grid.addColumn(this::lastRefreshText)
                .setHeader("Last refresh").setFlexGrow(2);
        grid.addColumn(new ComponentRenderer<>(this::actions))
                .setHeader("Actions").setFlexGrow(0).setWidth("220px");
    }

    private Health health(Mirror mirror) {
        if (mirror.isPaused()) {
            return Health.PAUSED;
        }
        if (mirror.getLastAttemptAt() == null) {
            return Health.WARN;
        }
        boolean lastAttemptSucceeded = mirror.getLastSuccessAt() != null
                && !mirror.getLastSuccessAt().isBefore(mirror.getLastAttemptAt());
        if (!lastAttemptSucceeded) {
            return Health.ERROR;
        }
        return mirror.getLastError() == null ? Health.OK : Health.WARN;
    }

    private Span healthDot(Mirror mirror) {
        Health health = health(mirror);
        Span dot = new Span();
        dot.addClassName("copycast-health-dot");
        if (refresh.isBusy(mirror.getId())) {
            dot.addClassName("blinking");
        }
        dot.getStyle().set("background-color", health.color);
        dot.getElement().setAttribute("title", healthText(mirror, health));
        return dot;
    }

    /** The column only reports the last update; health lives in the dot. */
    private String lastRefreshText(Mirror mirror) {
        if (refresh.isBusy(mirror.getId())) {
            return "Refreshing…";
        }
        return UiSupport.relative(mirror.getLastSuccessAt());
    }

    private String healthText(Mirror mirror, Health health) {
        if (refresh.isBusy(mirror.getId())) {
            return "Refreshing…";
        }
        return switch (health) {
            case PAUSED -> "Paused";
            case OK -> "OK · refreshed " + UiSupport.relative(mirror.getLastSuccessAt());
            case WARN -> mirror.getLastAttemptAt() == null
                    ? "Never refreshed"
                    : "Warnings · " + mirror.getLastError();
            case ERROR -> "Failing · " + mirror.getLastError();
        };
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
                    if (mirror.isPaused()) {
                        refresh.cancel(mirror.getId());
                    } else {
                        refresh.request(mirror.getId(), RefreshService.Trigger.MANUAL);
                    }
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
        delete.addThemeVariants(ButtonVariant.LUMO_ERROR);
        return new HorizontalLayout(copy, refreshNow, pause, delete);
    }

    private static Button iconButton(VaadinIcon icon, String tooltip, Runnable action) {
        Button button = new Button(new Icon(icon), e -> action.run());
        button.addThemeVariants(ButtonVariant.LUMO_SMALL, ButtonVariant.LUMO_TERTIARY);
        button.setTooltipText(tooltip);
        return button;
    }

    private void startProbe() {
        String input = urlField.getValue() == null ? "" : urlField.getValue().trim();
        if (input.isEmpty()) {
            notifyError("Please enter a URL");
            return;
        }
        // Cheap exact-match dedup before probing; the authoritative check
        // runs afterwards against the resolved canonical URL.
        if (notifyIfAlreadyMirrored(input)) {
            return;
        }
        mirrorButton.setEnabled(false);
        probing.setVisible(true);
        UI ui = UI.getCurrent();
        CompletableFuture.runAsync(() -> {
            ProbeResult result = probe.probe(input);
            ui.access(() -> {
                mirrorButton.setEnabled(true);
                probing.setVisible(false);
                if (!result.supported()) {
                    notifyError(result.error());
                } else if (!notifyIfAlreadyMirrored(result.url())) {
                    openCreateDialog(result.url(), result);
                }
            });
        });
    }

    private boolean notifyIfAlreadyMirrored(String sourceUrl) {
        var existing = store.findBySourceUrl(sourceUrl);
        existing.ifPresent(mirror -> Notification.show(
                "Already mirrored as \"" + mirror.displayTitle() + "\"",
                4000, Notification.Position.BOTTOM_START));
        return existing.isPresent();
    }

    private void openCreateDialog(String url, ProbeResult result) {
        Dialog dialog = new Dialog();
        dialog.setHeaderTitle("Mirror \"" + (result.title() != null ? result.title() : url) + "\"?");
        VerticalLayout content = new VerticalLayout();
        content.setPadding(false);
        content.add(new Paragraph("Detected as " + (result.service() != null ? result.service() : result.type())
                + " with " + result.episodeCount() + " episode(s)."));
        if (result.description() != null && !result.description().isBlank()) {
            String snippet = result.description().length() > 300
                    ? result.description().substring(0, 300) + "…"
                    : result.description();
            content.add(new Paragraph(snippet));
        }
        IntegerField cap = new IntegerField("Episodes to archive");
        cap.setStepButtonsVisible(true);
        cap.setMin(1);
        if (result.episodeCount() > 0) {
            cap.setMax(result.episodeCount());
            cap.setValue(result.episodeCount());
        }
        // Compact, but wide enough that the label never ellipsizes.
        cap.setWidth("12em");
        cap.addClassName("copycast-cap");
        cap.setValueChangeMode(ValueChangeMode.EAGER);
        // The explanation sits outside the field so it can use the full
        // dialog width instead of wrapping in a narrow column.
        Span capHelper = new Span();
        capHelper.addClassName("copycast-stats");
        Runnable updateCapHelper = () -> capHelper.setText(capText(cap.getValue(), result.episodeCount()));
        cap.addValueChangeListener(e -> updateCapHelper.run());
        updateCapHelper.run();
        content.add(cap, capHelper);
        content.setHorizontalComponentAlignment(FlexComponent.Alignment.CENTER, cap, capHelper);
        dialog.add(content);
        Button cancel = new Button("Cancel", e -> dialog.close());
        Button create = new Button("Create Mirror", e -> {
            dialog.close();
            try {
                // Choosing the maximum means the full backlog: store no cap so
                // future Episodes are archived too instead of rolling a window.
                Integer chosenCap = cap.getValue() != null && cap.getValue() == result.episodeCount()
                        ? null : cap.getValue();
                Mirror mirror = store.create(url, result, chosenCap);
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

    private RowSnapshot snapshot(Mirror mirror) {
        Health health = health(mirror);
        return new RowSnapshot(mirror.getId(), mirror.displayTitle(), mirror.displayService(),
                mirror.isPaused(), health, lastRefreshText(mirror), healthText(mirror, health),
                store.episodes(mirror).size(),
                UiSupport.gigabytes(store.sizeOnDiskBytes(mirror.getId())),
                refresh.isBusy(mirror.getId()));
    }

    private static String capText(Integer value, int episodeCount) {
        String tail = " and will keep adding new episodes as they are released, unless paused.";
        if (value == null || (episodeCount > 0 && value >= episodeCount)) {
            return "Will archive the full backlog" + tail;
        }
        return "Will archive the last " + value + (value == 1 ? " episode" : " episodes") + tail;
    }

    private void reload() {
        List<Mirror> mirrors = store.list();
        // Resetting the grid rebuilds every row component; skip it when
        // nothing a row renders has changed (the 3s poll mostly no-ops).
        List<RowSnapshot> rows = mirrors.stream().map(this::snapshot).toList();
        if (!rows.equals(lastRows)) {
            grid.setItems(mirrors);
            lastRows = rows;
        }

        int episodes = mirrors.stream().mapToInt(m -> store.episodes(m).size()).sum();
        long bytes = mirrors.stream().mapToLong(m -> store.sizeOnDiskBytes(m.getId())).sum();
        // Non-breaking space: a normal leading space would be collapsed away.
        String text = " · " + mirrors.size() + (mirrors.size() == 1 ? " Mirror · " : " Mirrors · ")
                + episodes + (episodes == 1 ? " Episode · " : " Episodes · ")
                + UiSupport.gigabytes(bytes);
        if (!ytDlp.isReady()) {
            text += " — yt-dlp unavailable"
                    + (ytDlp.installError() != null ? ": " + ytDlp.installError() : " (installing…)");
        }
        stats.setText(text);
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
