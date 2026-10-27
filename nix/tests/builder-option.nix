{ config, lib, ... }: {
  options.acenix.testBuilderCommand = lib.mkOption {
    type = lib.types.str;
    default = "";
    description = "A build hook used only by the disposable AceNix acceptance test.";
  };
  config.system.systemBuilderCommands = config.acenix.testBuilderCommand;
}
