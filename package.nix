{ lib, python3Packages, makeWrapper, nix, nixos-rebuild, systemd }:
python3Packages.buildPythonApplication {
  pname = "acenix";
  version = "0.2.0";
  pyproject = true;
  src = lib.cleanSourceWith {
    src = ./.;
    filter = path: type:
      let name = baseNameOf path;
      in lib.cleanSourceFilter path type
        && !(builtins.elem name [ "build" "dist" ".venv" ".pytest_cache" ".ruff_cache" "__pycache__" ])
        && !(lib.hasSuffix ".egg-info" name)
        && !(lib.hasSuffix ".pyc" name);
  };
  build-system = [ python3Packages.setuptools ];
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  disabledTestMarks = [ "nix" ];
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
