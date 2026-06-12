package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.UI;
import com.vaadin.flow.component.button.Button;
import com.vaadin.flow.component.button.ButtonVariant;
import com.vaadin.flow.component.dialog.Dialog;
import com.vaadin.flow.component.html.Paragraph;
import com.vaadin.flow.component.notification.Notification;

import java.time.Duration;
import java.time.Instant;

final class UiSupport {

    private UiSupport() {
    }

    static void confirm(String title, String message, String confirmCaption, Runnable onConfirm) {
        Dialog dialog = new Dialog();
        dialog.setHeaderTitle(title);
        dialog.add(new Paragraph(message));
        Button cancel = new Button("Cancel", e -> dialog.close());
        Button ok = new Button(confirmCaption, e -> {
            dialog.close();
            onConfirm.run();
        });
        ok.addThemeVariants(ButtonVariant.LUMO_PRIMARY, ButtonVariant.LUMO_ERROR);
        dialog.getFooter().add(cancel, ok);
        dialog.open();
    }

    static void copyToClipboard(String text) {
        UI.getCurrent().getPage().executeJs("navigator.clipboard.writeText($0)", text);
        Notification.show("Copied: " + text, 3000, Notification.Position.BOTTOM_START);
    }

    static String relative(Instant instant) {
        if (instant == null) {
            return "never";
        }
        Duration ago = Duration.between(instant, Instant.now());
        if (ago.toMinutes() < 1) {
            return "just now";
        }
        if (ago.toHours() < 1) {
            return ago.toMinutes() + " min ago";
        }
        if (ago.toDays() < 1) {
            return ago.toHours() + " h ago";
        }
        return ago.toDays() + " d ago";
    }

    static String humanSize(long bytes) {
        if (bytes < 1024) {
            return bytes + " B";
        }
        double value = bytes;
        for (String unit : new String[]{"KB", "MB", "GB", "TB"}) {
            value /= 1024;
            if (value < 1024) {
                return "%.1f %s".formatted(value, unit);
            }
        }
        return "%.1f PB".formatted(value / 1024);
    }
}
