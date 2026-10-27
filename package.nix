{ lib, python3Packages, makeWrapper, nix, nixos-rebuild, systemd }:
python3Packages.buildPythonApplication {
  pname = "acenix";
  version = "0.2.0";
  pyproject = true;
  src = lib.cleanSource ./.;
  build-system = [ python3Packages.setuptools ];
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pytestFlags = [ "-m" "not nix" ];
  pythonImportsCheck = [ "acenix" "core.main" ];
  postFixup = ''
    wrapProgram $out/bin/acenix --prefix PATH : ${lib.makeBinPath [ nix nixos-rebuild systemd ]}
  '';
  meta = {
    description = "Imperative commands with declarative NixOS foundations";
    mainProgram = "acenix";
    platforms = lib.platforms.linux;
    # The project owner has not selected a license; do not invent one here.
  };
}
