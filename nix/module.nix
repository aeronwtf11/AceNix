flake: { config, lib, pkgs, ... }: {
  options.programs.acenix.enable = lib.mkEnableOption "the AceNix CLI";
  config = lib.mkIf config.programs.acenix.enable {
    environment.systemPackages = [ flake.packages.${pkgs.stdenv.hostPlatform.system}.default ];
  };
}
