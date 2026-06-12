{
  description = "Copycast — self-hosted podcast mirroring and archiving";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system:
        f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            jdk21
            maven
            # yt-dlp -x needs ffmpeg; the yt-dlp binary itself is managed by
            # the app (pinned in config/application.yaml, see ADR 0002).
            ffmpeg
            # Optional: lets the Vaadin build use a system node instead of
            # downloading its own.
            nodejs_22
          ];

          JAVA_HOME = pkgs.jdk21.home;

          shellHook = ''
            echo "Copycast dev shell — Java $(java -version 2>&1 | head -1 | cut -d'\"' -f2), Maven $(mvn -v 2>/dev/null | head -1 | cut -d' ' -f3)"
            echo "Run 'make help' for available targets."
          '';
        };
      });
    };
}
