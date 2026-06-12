package xyz.dufour.copycast.ui;

import com.vaadin.flow.component.Text;
import com.vaadin.flow.component.html.Anchor;
import com.vaadin.flow.component.html.H1;
import com.vaadin.flow.component.html.H3;
import com.vaadin.flow.component.html.Paragraph;
import com.vaadin.flow.component.orderedlayout.VerticalLayout;
import com.vaadin.flow.router.PageTitle;
import com.vaadin.flow.router.Route;
import com.vaadin.flow.router.RouterLink;
import xyz.dufour.copycast.ytdlp.YtDlp;

@Route("about")
@PageTitle("About — Copycast")
public class AboutView extends VerticalLayout {

    public AboutView(YtDlp ytDlp) {
        addClassName("copycast-view");

        H1 title = new H1("Copycast");
        title.addClassName("copycast-title");
        add(title);
        add(new Paragraph("Version " + UiSupport.APP_VERSION
                + " — self-hosted podcast mirroring and archiving."));

        Anchor repo = new Anchor("https://github.com/flrntdfr/Copycast",
                "github.com/flrntdfr/Copycast");
        repo.setTarget("_blank");
        add(new Paragraph(new Text("Source code, issues and releases: "), repo));

        add(new H3("Credits"));
        Anchor ytDlpLink = new Anchor("https://github.com/yt-dlp/yt-dlp",
                "yt-dlp " + ytDlp.version());
        ytDlpLink.setTarget("_blank");
        String release = ytDlp.releaseDate() != null
                ? " (released " + ytDlp.releaseDate() + ")"
                : "";
        add(new Paragraph(new Text("All downloading is powered by "), ytDlpLink,
                new Text(release + " — Copycast would not exist without it.")));
        Anchor ffmpeg = new Anchor("https://ffmpeg.org", "FFmpeg");
        ffmpeg.setTarget("_blank");
        add(new Paragraph(new Text("Audio extraction relies on "), ffmpeg, new Text(".")));

        add(new RouterLink("← All Mirrors", MainView.class));
    }
}
