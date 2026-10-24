"""Build local web assets from the supplied originals; no crop or repaint."""

import argparse
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    '标题页.png': 'title.webp',
    'avatar0.jpg': 'avatar-unknown.webp',
    'avatar_renko.jpg': 'avatar-renko.webp',
    'avatar_hearn.jpg': 'avatar-maribel.webp',
    'avatar_yukari.jpg': 'avatar-yukari.webp',
    '堇子.jpg': 'avatar-sumireko.webp',
    '莲子.jpg': 'avatar-brown.webp',
    '帽子.png': 'hat.webp',
    '堇子cg.jpg': 'ending-sumireko-loss.webp',
    'cg01.png': 'ending-hifuu.webp',
    'cg02.jpg': 'ending-yukari.webp',
    'cg03.jpg': 'ending-timeout.webp',
    '标题页车厢母图.png': 'title-carriage.webp',
    '候车大厅.png': 'boarding-lounge.webp',
    '手机入口背景.png': 'phone-entry.webp',
    '日行窗景.png': 'window-day.webp',
    '夜行窗景.png': 'window-night.webp',
    '旅程中止背景.png': 'journey-aborted.webp',
    '纸张底纹.png': 'paper.webp',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, help='Directory containing the original images')
    parser.add_argument('sources', nargs='*', help='Original filenames to export; defaults to all known images')
    args = parser.parse_args()
    sources = args.sources or list(FILES)
    unknown = [source for source in sources if source not in FILES]
    if unknown:
        parser.error(f'Unknown source filenames: {", ".join(unknown)}')
    source_dir = args.source_dir
    if source_dir is None:
        source_dir = ROOT / 'assets'
        if not source_dir.is_dir():
            source_dir = ROOT / 'design' / 'assets'
    missing = [source for source in sources if not (source_dir / source).is_file()]
    if missing:
        parser.error(f'Missing images in {source_dir}: {", ".join(missing)}')
    target = ROOT / 'public' / 'assets'
    target.mkdir(exist_ok=True)
    for source in sources:
        name = FILES[source]
        with Image.open(source_dir / source) as im:
            options = {'lossless': True} if source == '帽子.png' else {'quality': 88}
            im.save(target / name, 'WEBP', method=6, **options)
            print(f'{name}: {im.width}x{im.height}, {(target/name).stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
