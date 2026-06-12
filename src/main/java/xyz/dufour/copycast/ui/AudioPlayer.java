package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.button.Button;
import com.vaadin.flow.component.button.ButtonVariant;
import com.vaadin.flow.component.html.RangeInput;
import com.vaadin.flow.component.html.Span;
import com.vaadin.flow.component.icon.Icon;
import com.vaadin.flow.component.icon.VaadinIcon;
import com.vaadin.flow.component.orderedlayout.FlexComponent;
import com.vaadin.flow.component.orderedlayout.HorizontalLayout;
import com.vaadin.flow.dom.Element;

/**
 * Slim inline audio player: play/pause, seek bar and elapsed/total time for
 * one Episode at a time. Hidden until the first play. Seek and time updates
 * are wired entirely client-side; the server only hears play/pause events to
 * keep the toggle icon in sync.
 */
public class AudioPlayer extends HorizontalLayout {

    private final Element audio = new Element("audio");
    private final Button playPause = new Button(new Icon(VaadinIcon.PLAY));
    private final Span title = new Span();
    private final RangeInput seek = new RangeInput();
    private final Span time = new Span("0:00 / 0:00");
    private String currentUrl;

    public AudioPlayer() {
        addClassName("copycast-player");
        setWidthFull();
        setAlignItems(FlexComponent.Alignment.CENTER);
        setVisible(false);

        audio.setAttribute("preload", "none");
        getElement().appendChild(audio);

        playPause.addThemeVariants(ButtonVariant.LUMO_SMALL, ButtonVariant.LUMO_TERTIARY);
        playPause.addClickListener(e -> audio.executeJs("this.paused ? this.play() : this.pause()"));
        audio.addEventListener("play", e -> playPause.setIcon(new Icon(VaadinIcon.PAUSE)));
        audio.addEventListener("pause", e -> playPause.setIcon(new Icon(VaadinIcon.PLAY)));

        title.addClassName("copycast-player-title");
        seek.setMin(0);
        seek.setMax(1000);
        seek.setValue(0.0);
        time.addClassName("copycast-player-time");

        add(playPause, title, seek, time);
        expand(seek);

        getElement().executeJs("""
                const audio = $0, seek = $1, time = $2;
                const fmt = s => {
                  if (!isFinite(s)) return '0:00';
                  s = Math.floor(s);
                  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
                  return (h > 0 ? h + ':' + String(m).padStart(2, '0') : m)
                      + ':' + String(s % 60).padStart(2, '0');
                };
                const update = () => {
                  if (audio.duration) seek.value = audio.currentTime / audio.duration * 1000;
                  time.textContent = fmt(audio.currentTime) + ' / ' + fmt(audio.duration);
                };
                audio.addEventListener('timeupdate', update);
                audio.addEventListener('durationchange', update);
                seek.addEventListener('input', () => {
                  if (audio.duration) audio.currentTime = seek.value / 1000 * audio.duration;
                });
                """, audio, seek, time);
    }

    /** Plays the given media URL; calling again with the same URL toggles pause. */
    public void play(String url, String episodeTitle) {
        setVisible(true);
        if (url.equals(currentUrl)) {
            audio.executeJs("this.paused ? this.play() : this.pause()");
            return;
        }
        currentUrl = url;
        title.setText(episodeTitle);
        audio.setAttribute("src", url);
        audio.executeJs("this.play()");
    }
}
