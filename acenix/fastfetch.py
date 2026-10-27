"""Optional user-only Fastfetch logo, preserving all information modules."""
import json
from pathlib import Path
from .errors import ConflictError, ValidationError
from .storage import atomic_write, digest, safe_path

def jsonc(text):
    """Strip JSONC comments/trailing commas with length-preserving whitespace."""
    chars, index = list(text), 0
    while index < len(text):
        if text[index] == '"':
            index += 1
            while index < len(text):
                if text[index] == '\\':
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        elif text.startswith('//', index) or text.startswith('/*', index):
            end = text.find('\n', index) if text.startswith('//', index) else text.find('*/', index + 2)
            if end < 0:
                if text.startswith('/*', index):
                    raise ValidationError('Unterminated Fastfetch JSONC comment')
                end = len(text)
            elif text.startswith('/*', index):
                end += 2
            for pos in range(index, end):
                if chars[pos] not in '\r\n':
                    chars[pos] = ' '
            index = end
        else:
            index += 1
    clean = ''.join(chars)
    index, quoted = 0, False
    while index < len(clean):
        if clean[index] == '"':
            quoted = not quoted
        elif clean[index] == '\\' and quoted:
            index += 1
        elif clean[index] == ',' and not quoted:
            following = index + 1
            while following < len(clean) and clean[following].isspace():
                following += 1
            if following < len(clean) and clean[following] in '}]':
                chars[index] = ' '
        index += 1
    clean = ''.join(chars)
    try:
        value = json.loads(clean)
    except ValueError as error:
        raise ValidationError(f'Cannot safely read Fastfetch JSONC: {error}') from error
    if not isinstance(value, dict):
        raise ValidationError('Fastfetch configuration must be a JSON object.')
    return value, clean

def replace_logo(text, logo):
    data, clean = jsonc(text)
    decoder = json.JSONDecoder()
    index = clean.index('{') + 1
    while index < len(clean):
        while clean[index].isspace() or clean[index] == ',':
            index += 1
        if clean[index] == '}':
            break
        key, index = decoder.raw_decode(clean, index)
        while clean[index].isspace() or clean[index] == ':':
            index += 1
        start = index
        _, index = decoder.raw_decode(clean, index)
        if key == 'logo':
            return text[:start] + json.dumps(logo) + text[index:]
    position = clean.index('{') + 1
    return text[:position] + '\n  "logo": ' + json.dumps(logo) + (',' if data else '') + text[position:]

class Fastfetch:
    def __init__(self, config_root, store):
        self.directory = Path(config_root) / 'fastfetch'
        self.path = self.directory / 'config.jsonc'
        self.store = store

    def status(self):
        state = self.store.read('fastfetch.json')
        return {'enabled': bool(state), 'configuration': str(self.path),
                'externally_modified': bool(state) and (not self.path.exists() or digest(self.path.read_bytes()) != state['written'])}

    def enable(self, logo):
        if self.store.read('fastfetch.json'):
            if self.status()['externally_modified']:
                raise ConflictError('Fastfetch configuration changed externally; disable/reconcile before enabling again.')
            return {'result': 'already-enabled'}
        if logo is None or not Path(logo).is_file():
            raise ValidationError('The original AceNix ASCII logo is absent from this checkout (only PNG exists). Supply --logo /path/to/original-ascii.txt; no replacement is invented.')
        if (self.directory / 'config.json').exists() and not self.path.exists():
            raise ConflictError('Existing config.json found; migrate it explicitly to config.jsonc before enabling the logo.')
        original = safe_path(self.path).read_bytes() if self.path.exists() else None
        content = original.decode() if original is not None else '{}\n'
        destination = self.directory / 'acenix-logo.txt'
        if destination.exists():
            raise ConflictError(f'Logo destination already exists: {destination}')
        logo_bytes = Path(logo).read_bytes()
        logo_bytes.decode('utf-8')
        modified = replace_logo(content, {'type': 'file', 'source': str(destination), 'color': {'1': 'cyan', '2': 'white'}}).encode()
        if original is not None:
            atomic_write(self.store.root / 'fastfetch-original', original)
        # Journal before either user file is changed, so interruption is visible.
        state = {'existed': original is not None, 'original': digest(original or b''),
                 'written': digest(modified), 'logo': digest(logo_bytes), 'phase': 'prepared'}
        self.store.write('fastfetch.json', state)
        atomic_write(destination, logo_bytes)
        atomic_write(self.path, modified, expected=digest(original or b''))
        state['phase'] = 'enabled'
        self.store.write('fastfetch.json', state)
        return {'result': 'enabled', 'configuration': str(self.path), 'modules_preserved': True}

    def disable(self):
        state = self.store.read('fastfetch.json')
        if not state:
            return {'result': 'already-disabled'}
        current = self.path.read_bytes() if self.path.exists() else b''
        if digest(current) not in {state['written'], state['original']}:
            raise ConflictError('Fastfetch configuration has external edits; refusing to overwrite them. Original backup is retained.')
        if state['existed']:
            original = (self.store.root / 'fastfetch-original').read_bytes()
            if digest(original) != state['original']:
                raise ConflictError('Fastfetch backup checksum mismatch')
            atomic_write(self.path, original, expected=digest(current))
        elif self.path.exists():
            safe_path(self.path).unlink()
        logo = self.directory / 'acenix-logo.txt'
        if logo.exists():
            if digest(logo.read_bytes()) != state['logo']:
                raise ConflictError('Installed logo was modified; leaving it and its recovery journal intact.')
            safe_path(logo).unlink()
        self.store.write('fastfetch.json', {})
        return {'result': 'disabled', 'original_configuration_restored': True}

