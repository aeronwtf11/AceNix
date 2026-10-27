"""Inspect native generations; source backups and system rollback stay distinct."""
import json
from pathlib import Path
import re
import shutil
from .errors import ToolError, ValidationError
from .nixos import run

PROFILE = Path('/nix/var/nix/profiles/system')

def generations(runner=run, profile=PROFILE):
    output = runner(['nix-env', '--profile', str(profile), '--list-generations'])
    result = []
    running = Path('/run/current-system').resolve()
    booted = Path('/run/booted-system').resolve()
    for line in output.splitlines():
        match = re.fullmatch(r'\s*(\d+)\s+(\S+)\s+(\S+)(?:\s+\(current\))?\s*', line)
        if not match:
            raise ToolError(f'Unrecognized native generation output: {line!r}')
        number = int(match[1])
        path = profile.parent / f'{profile.name}-{number}-link'
        result.append({'generation': number, 'created': f'{match[2]} {match[3]}',
                       'profile_default': '(current)' in line, 'running': path.resolve() == running,
                       'booted': path.resolve() == booted, 'path': str(path), 'boot_default': None})
    note = 'Next-boot default is unknown; the native system profile default is shown separately.'
    if shutil.which('bootctl'):
        try:
            entries = json.loads(runner(['bootctl', 'list', '--json=short']))
            for entry in entries:
                match = re.search(r'nixos-generation-(\d+)', entry.get('id', ''))
                if entry.get('isDefault') and match:
                    for row in result:
                        row['boot_default'] = row['generation'] == int(match[1])
                    note = 'Next-boot default reported by systemd-boot.'
        except (ToolError, ValueError, TypeError):
            pass
    return {'generations': result, 'note': note}

def rollback(backend, store, number, mode, confirm):
    if not Path('/etc/NIXOS').exists():
        raise ToolError('Native system rollback requires a running NixOS machine.')
    rows = generations(backend.run)['generations']
    if number is not None and number not in {row['generation'] for row in rows}:
        raise ValidationError(f'Native generation {number} does not exist.')
    confirm({'generation': number if number is not None else 'previous', 'mode': mode,
             'note': 'This changes native runtime/boot state, not configuration.nix. Use recover for source backups.'}, 'rollback')
    try:
        if number is None:
            backend.run(backend.target.rebuild_args(mode) + ['--rollback'], timeout=1800)
        else:
            system = next(Path(row['path']).resolve() for row in rows if row['generation'] == number)
            if mode != 'test':
                backend.run(['nix-env', '--profile', str(PROFILE), '--switch-generation', str(number)])
            backend.run([str(system / 'bin/switch-to-configuration'), mode], timeout=1800)
    except ToolError as error:
        store.history({'operation': 'rollback', 'generation': number, 'mode': mode, 'result': 'failed-or-partial'})
        raise ToolError(f'Rollback failed; native profile/runtime state may have partially changed. Inspect generations.\n{error}') from error
    store.history({'operation': 'rollback', 'generation': number, 'mode': mode, 'result': 'completed'})
    return {'result': 'rolled-back', 'generation': number, 'mode': mode, 'source_files_changed': False}

