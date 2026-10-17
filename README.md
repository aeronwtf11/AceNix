# AceNix

**One System. Your Decisions.**

AceNix is an open-source project aiming to bring a simple, rule-based configuration and management interface to **NixOS**. It is inspired by the ideas behind AceOS, a separate, future Linux distribution project.

AceNix does **not** replace NixOS, Nix, or systemd. Instead, it aims to make everyday system management more approachable while keeping NixOS's declarative configuration and generations.

## Project status

🚧 **Early development.** The initial focus is a Python prototype for reading, parsing, and validating AceNix configuration files. The commands and features below are **planned**, not available yet.

## Planned features

- A clear, rule-based configuration format (`config.sys`)
- A command-line interface for changing and applying settings
- Integration with existing NixOS configuration and package management
- Support for NixOS generations and rollbacks
- Optional desktop environment management

## Example configuration (proposed)

```ini
[system]
hostname:ace-pc
network:false
desktop:false

[packages]
pkg.firefox:true
```

## Planned CLI

```sh
acenix setr network:true
acenix apply
acenix install firefox
```

These examples illustrate the intended user experience and **will not work until implemented**.

## AceNix and AceOS

- **AceNix:** A management layer built for NixOS.
- **AceOS:** A separate, long-term project to build an independent Linux distribution.

The projects may eventually share a reusable configuration engine called **Ace Core**.

## Contributing

AceNix is intended to be developed openly. Contributions, feedback, and ideas are welcome as the project takes shape.

## License

A license has not been selected yet. Until a license is added, this repository should not be assumed to grant permission to reuse or redistribute its code.
