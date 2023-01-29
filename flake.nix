{
  inputs = {
    nixpkgs = {
      url = "github:nixos/nixpkgs/nixos-unstable";
    };
    flake-utils = {
      url = "github:numtide/flake-utils";
    };
  };
  outputs = { nixpkgs, flake-utils, ... }: flake-utils.lib.eachDefaultSystem (system:
    let
      pkgs = import nixpkgs {
        inherit system;
      };
      cgif = {stdenv, fetchFromGitHub, meson, ninja}:
          stdenv.mkDerivation {
            name = "cgif";
            src = fetchFromGitHub {
              rev = "V0.3.0";
              owner = "dloebl";
              repo = "cgif";
              sha256 = "sha256-vSEPZEhp1Fpu0SiKWFjP8ESu3BKfKjQYWWeM75t/rEA=";
            };
            nativeBuildInputs = [ meson ninja ];
      };
    in rec {
      devShell = pkgs.mkShell {
        buildInputs = with pkgs; [
          (python3.withPackages(ps: with ps; [
            discordpy
            (ps.pyvips.override {
              vips = (pkgs.vips.overrideAttrs (old: {
                nativeBuildInputs = old.nativeBuildInputs ++ [(pkgs.callPackage cgif {})];
              }));
            })
            aiohttp
            asyncpg
            tomli
            pip
            ffmpeg-python
            magic
            requests
          ]))
        ];
      };
    }
  );
}
