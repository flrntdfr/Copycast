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

    /** From the jar manifest; "dev" when running unpackaged. */
    static final String APP_VERSION = java.util.Optional
            .ofNullable(UiSupport.class.getPackage().getImplementationVersion())
            .orElse("dev");

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

    /**
     * Makes clicking {@code button} copy {@code text}. The copy runs in a
     * client-side click listener: browsers only grant clipboard access
     * within a user gesture, and a server round-trip (executeJs from a
     * server-side listener) arrives after that window has closed. The
     * async clipboard API needs a secure context (https or localhost);
     * everything else falls back to execCommand.
     */
    static void copyOnClick(Button button, String text) {
        button.getElement().executeJs("""
                this.__copyText = $0;
                if (!this.__copyWired) {
                  this.__copyWired = true;
                  this.addEventListener('click', () => {
                    const text = this.__copyText;
                    const fallback = () => {
                      const ta = document.createElement('textarea');
                      ta.value = text;
                      ta.style.position = 'fixed';
                      ta.style.opacity = '0';
                      document.body.appendChild(ta);
                      ta.focus();
                      ta.select();
                      try { document.execCommand('copy'); } catch (e) { }
                      document.body.removeChild(ta);
                    };
                    if (navigator.clipboard && window.isSecureContext) {
                      navigator.clipboard.writeText(text).catch(fallback);
                    } else {
                      fallback();
                    }
                  });
                }
                """, text);
        button.addClickListener(e -> Notification.show("Copied: " + text,
                3000, Notification.Position.BOTTOM_START));
    }

    static String gigabytes(long bytes) {
        return "%.2f GB".formatted(bytes / (1024.0 * 1024.0 * 1024.0));
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

    /**
     * Feed descriptions often contain HTML; rendering it raw would be an XSS
     * vector (it comes from external Sources), so reduce it to plain text.
     */
    static String stripHtml(String html) {
        if (html == null) {
            return "";
        }
        return html.replaceAll("<[^>]*>", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&#39;", "'")
                .replace("&nbsp;", " ")
                .replaceAll("\\s+", " ")
                .trim();
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
