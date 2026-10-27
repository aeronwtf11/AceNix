{
  description = "AceNix development, package and checks";
  inputs.nixpkgs.url = "https://codeload.github.com/NixOS/nixpkgs/tar.gz/1f607aa79b6356ed45d3cd3c8079c196e79efa2c";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      packages = eachSystem (system:
        let pkgs = import nixpkgs { inherit system; };
        in { default = pkgs.callPackage ./package.nix { }; });
      checks = eachSystem (system: { package = self.packages.${system}.default; });
      devShells = eachSystem (system:
        let pkgs = import nixpkgs { inherit system; };
        in { default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pytest p.setuptools p.build ])) pkgs.nix pkgs.nixos-rebuild ];
          shellHook = ''
            export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
            export NIX_PATH="nixpkgs=${nixpkgs}''${NIX_PATH:+:$NIX_PATH}"
          '';
        }; });
      nixosModules.default = import ./nix/module.nix self;
    };
}
