"""Build local web assets from the supplied originals; no crop or repaint."""

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    '标题页.png': 'title.webp',
    'avatar0.jpg': 'avatar-unknown.webp',
    'avatar_renko.jpg': 'avatar-renko.webp',
    'avatar_hearn.jpg': 'avatar-maribel.webp',
    'avatar_yukari.jpg': 'avatar-yukari.webp',
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

if __name__ == '__main__':
    target = ROOT / 'public' / 'assets'
    target.mkdir(exist_ok=True)
    for source, name in FILES.items():
        with Image.open(ROOT / 'assets' / source) as im:
            im.save(target / name, 'WEBP', quality=88, method=6)
            print(f'{name}: {im.width}x{im.height}, {(target/name).stat().st_size:,} bytes')
