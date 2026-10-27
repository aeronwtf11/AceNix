"""Reusable rule documents; activation uses the same native transaction path."""
from pathlib import Path
import re
from .errors import ConflictError, ValidationError
from .rules import parse
from .storage import atomic_write

class Profiles:
    def __init__(self, store):
        self.store = store

    def path(self, name):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
            raise ValidationError('Profile names use 1–64 letters, numbers, underscores or hyphens.')
        return self.store.root / 'profiles' / (name + '.sys')

    def list(self):
        return sorted(path.stem for path in (self.store.root / 'profiles').glob('*.sys'))

    def show(self, name):
        path = self.path(name)
        if not path.is_file():
            raise ValidationError(f'Profile not found: {name}')
        text = path.read_text()
        parse(text, str(path))
        return text

    def create(self, name, text):
        path = self.path(name)
        if path.exists():
            raise ConflictError(f'Profile already exists: {name}')
        parse(text, str(path))
        atomic_write(path, text.encode())
        return {'result': 'created', 'profile': name}

    def delete(self, name):
        self.show(name)
        self.path(name).unlink()
        return {'result': 'deleted', 'profile': name, 'native_configuration_unchanged': True}

    def export(self, name, destination):
        text = self.show(name)
        path = Path(destination).absolute()
        if path.exists():
            raise ConflictError(f'Export destination already exists: {path}')
        atomic_write(path, text.encode())
        return {'result': 'exported', 'file': str(path)}

