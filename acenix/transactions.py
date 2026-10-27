"""Candidate evaluation, durable backups, conflict checks and explicit activation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from .engine import source_snapshot
from .errors import AceNixError, ConflictError, PermissionError, ToolError
from .nixos import NixOS
from .rules import to_nix
from .storage import atomic_write, digest, now, safe_path

class Transactions:
    def __init__(self, backend, store):
        self.backend, self.store = backend, store

    def unchanged(self, plan):
        if source_snapshot(self.backend.target.root) != plan.snapshot:
            raise ConflictError('Native files changed since preview. Run preview again and review the new diff.')

    @contextmanager
    def candidate(self, plan):
        self.unchanged(plan)
        with tempfile.TemporaryDirectory(prefix='acenix-candidate-') as directory:
            root = Path(directory) / 'configuration'
            root.mkdir(mode=0o700)
            for relative, info in plan.snapshot.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                data = plan.changes[relative][1] if relative in plan.changes else (self.backend.target.root / relative).read_bytes()
                path.write_bytes(data)
                path.chmod(info['mode'])
            self.unchanged(plan)
            yield self.backend.target.relocated(root)

    def validate(self, plan, target):
        candidate = NixOS(target, runner=self.backend.run)
        for relative in plan.changes:
            self.backend.run(['nix-instantiate', '--parse', str(target.root / relative)])
        for row in plan.values:
            actual = candidate.get(row['option'])
            if actual != row['requested']:
                raise ConflictError(f'{row["option"]}: native merge priorities/imports prevent the requested effective value; no files were changed.')
        plan.derivation = candidate.check()
        plan.evaluated = True
        current = candidate.get('environment.systemPackages') if any(plan.packages.values()) else []
        installed = {p.get('outPath') for p in current if isinstance(p, dict)}
        for attr in plan.packages['add']:
            if candidate.package(attr)['outPath'] not in installed:
                raise ConflictError(f'{attr} was not included by the candidate module imports.')
        for attr in plan.packages['remove']:
            if candidate.package(attr)['outPath'] in installed:
                plan.warnings.append(f'{attr} remains installed through another native definition; only the owned expression is removed.')
        return plan

    def preview(self, plan):
        with self.candidate(plan) as target:
            self.validate(plan, target)
        self.store.write('preview.json', {'target': plan.target, 'snapshot': plan.snapshot, 'rules_hash': plan.rules_hash})
        return plan.public()

    def _permission_check(self, plan):
        for name in plan.changes:
            path = safe_path(self.backend.target.root / name)
            if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
                raise PermissionError(f'Cannot atomically replace {path}; run with explicitly granted administrator permissions. AceNix never invokes sudo.')

    def apply(self, plan, *, mode, confirm, rules_path):
        if mode not in {'build', 'test', 'boot', 'switch'}:
            raise ToolError(f'Unknown apply mode: {mode}')
        previous = self.store.read('preview.json')
        if previous and previous != {'target': plan.target, 'snapshot': plan.snapshot, 'rules_hash': plan.rules_hash}:
            raise ConflictError('Rules or native sources changed since the saved preview; run preview again.')
        if not plan.changes and mode != 'build':
            with self.candidate(plan) as target:
                self.validate(plan, target)
            return {'result': 'unchanged', 'message': 'No native source changes are required.'}
        if mode != 'build':
            self._permission_check(plan)
            if not Path('/etc/NIXOS').exists():
                raise ToolError('test/boot/switch activation requires NixOS; use --mode build for a candidate build.')
        with self.candidate(plan) as target:
            self.validate(plan, target)
            system = self.backend.build(target)
            if mode == 'build':
                self.store.history({'operation': 'build', 'result': 'built', 'system': system, 'target': plan.target})
                return {'result': 'built', 'system': system, 'sources_changed': False}
            confirm(plan.public(), mode)
            self.unchanged(plan)
            if digest(rules_path.read_bytes() if rules_path.exists() else b'') != plan.rules_hash:
                raise ConflictError('Rule file changed during candidate build; run preview again.')
            backup = self._backup(plan, system, mode)
            committed = []
            activated = False
            try:
                for name, (before, after) in plan.changes.items():
                    atomic_write(self.backend.target.root / name, after, expected=digest(before))
                    committed.append(name)
                    backup['committed'] = committed[:]
                    self._manifest(backup)
                self.backend.activate(system, mode)
                activated = True
                backup['status'] = 'completed'
                self._manifest(backup)
                state = self.store.read()
                state.update({'target': plan.target, 'managed_packages': plan.managed_packages,
                              'last_backup': backup['id'], 'last_system': system})
                self.store.write('state.json', state)
                self.store.write('preview.json', {})
                self.store.history({'operation': 'apply', 'mode': mode, 'result': 'completed',
                                    'backup': backup['id'], 'system': system, 'files': list(plan.changes)})
                return {'result': 'activated', 'mode': mode, 'system': system, 'backup': backup['id']}
            except BaseException as error:
                if activated:
                    # A state/journal failure after activation must not be
                    # described as a failed activation or automatic rollback.
                    raise ToolError(f'Native activation completed, but recording state failed. Preserve backup {backup["id"]} and run doctor: {error}') from error
                failures = []
                for name in reversed(committed):
                    before, after = plan.changes[name]
                    try:
                        atomic_write(self.backend.target.root / name, before, expected=digest(after))
                    except (OSError, AceNixError) as restore_error:
                        failures.append(f'{name}: {restore_error}')
                backup['status'] = 'recovery-required' if failures else 'sources-restored'
                backup['error'] = str(error)
                self._manifest(backup)
                self.store.history({'operation': 'apply', 'result': backup['status'], 'backup': backup['id']})
                message = f'Apply failed. Backup: {backup["id"]}. '
                message += 'Source restoration failed: ' + '; '.join(failures) if failures else 'Source files were restored.'
                message += ' Native activation may have partially changed runtime/profile state; inspect generations and use explicit rollback if needed.'
                raise ToolError(message + '\n' + str(error)) from error

    def _backup(self, plan, system, mode):
        identifier = uuid.uuid4().hex
        directory = self.store.root / 'backups' / identifier
        directory.mkdir(parents=True, mode=0o700)
        manifest = {'id': identifier, 'time': now(), 'target': plan.target, 'root': str(self.backend.target.root),
                    'status': 'prepared', 'system': system, 'mode': mode, 'files': {}, 'committed': [],
                    'previous_state': self.store.read()}
        for name, (before, after) in plan.changes.items():
            path = directory / 'files' / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(path, before)
            manifest['files'][name] = {'before': digest(before), 'after': digest(after)}
        self._manifest(manifest)
        return manifest

    def _manifest(self, manifest):
        atomic_write(self.store.root / 'backups' / manifest['id'] / 'manifest.json', json.dumps(manifest, indent=2).encode())

    def backups(self):
        result = []
        for path in sorted((self.store.root / 'backups').glob('*/manifest.json')):
            result.append(json.loads(path.read_text()))
        return result

    def recover(self, identifier, confirm):
        if not identifier or any(c not in '0123456789abcdef' for c in identifier) or len(identifier) != 32:
            raise ToolError('Backup identifiers are 32 hexadecimal characters.')
        path = self.store.root / 'backups' / identifier / 'manifest.json'
        if not path.is_file():
            raise ToolError(f'Backup not found: {identifier}')
        manifest = json.loads(path.read_text())
        if manifest['target'] != self.backend.target.identity or manifest['root'] != str(self.backend.target.root):
            raise ConflictError('Backup belongs to a different native target.')
        pending = []
        for name, hashes in manifest['files'].items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ConflictError('Unsafe backup path')
            native = safe_path(self.backend.target.root / relative)
            saved = safe_path(path.parent / 'files' / relative)
            data = saved.read_bytes()
            current = digest(native.read_bytes())
            if digest(data) != hashes['before']:
                raise ConflictError(f'Backup checksum mismatch: {name}')
            if current == hashes['before']:
                continue
            if current != hashes['after']:
                raise ConflictError(f'External edits to {name}; recovery will not overwrite them.')
            pending.append((native, data, hashes['after']))
        confirm({'backup': identifier, 'files': [str(p) for p, _, _ in pending], 'note': 'Source recovery does not roll back the running system.'}, 'recover')
        for native, data, expected in pending:
            atomic_write(native, data, expected=expected)
        manifest['status'] = 'sources-restored'
        self._manifest(manifest)
        self.store.write('state.json', manifest.get('previous_state', {}))
        self.store.write('preview.json', {})
        self.store.history({'operation': 'recover', 'result': 'sources-restored', 'backup': identifier})
        return {'result': 'sources-restored', 'backup': identifier, 'runtime_unchanged_by_recovery': True}
