#!/usr/bin/env python3
"""Generate frontend catalog data. --check is deterministic and offline for CI."""
from __future__ import annotations
import argparse
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def generated_files(root: Path = ROOT) -> dict[Path, str]:
    data = json.loads((root / 'src/utils/provider_catalog.json').read_text(encoding='utf-8'))
    fish = [model['id'] for model in data['providers']['fishaudio']['models']['tts']]
    local = runpy.run_path(str(root / 'admin_ui/backend/api/models_catalog.py'))
    kokoro = local['KOKORO_TTS_MODELS'][0]
    kokoro_data = {
        'language_codes': local['KOKORO_LANGUAGE_CODES'],
        'voices': [
            {'id': voice, 'language': kokoro['voice_languages'][voice], 'lang_code': kokoro['voice_lang_codes'][voice]}
            for voice in kokoro['voice_files']
        ],
    }
    return {
        root / 'admin_ui/frontend/src/config/providerCatalog.generated.json': json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        root / 'admin_ui/frontend/src/config/fishAudioModels.json': json.dumps(fish, indent=4) + '\n',
        root / 'admin_ui/frontend/src/config/kokoroVoices.generated.json': json.dumps(kokoro_data, ensure_ascii=False, indent=2) + '\n',
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    stale = []
    for path, expected in generated_files().items():
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(expected, encoding='utf-8')
    if stale:
        print('Provider catalog generated files are stale: ' + ', '.join(stale))
        print('Run python scripts/generate_provider_catalog.py')
        return 1
    print('Provider catalog generated files are synchronized')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
