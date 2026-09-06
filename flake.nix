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
            python313
            uv
            nodejs_22
            ffmpeg
            postgresql_17
            ruff
            pyright
            jq
            gnumake
            gh
          ];

          # uv must use the Nix-provided interpreter, never download one.
          UV_PYTHON = "${pkgs.python313}/bin/python3.13";
          UV_PYTHON_DOWNLOADS = "never";

          shellHook = ''
            echo "Copycast dev shell — $(python3.13 --version), uv $(uv --version | cut -d' ' -f2), node $(node --version)"
            echo "Run 'make help' for available targets."
          '';
        };
      });
    };
}
