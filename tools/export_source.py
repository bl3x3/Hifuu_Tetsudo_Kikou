"""Export only reviewed source files, without Git history or local playtest data."""

import argparse
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'release-files.txt'
PRIVATE_NAMES = {
    '.git',
    '.env',
    '.venv',
    'vendor',
    'runtime',
    'analysis',
    'design',
    'test-results',
    'dist',
    '__pycache__',
    'node_modules',
    'server-config.json',
}
PRIVATE_SUFFIXES = {'.pyc', '.db', '.log', '.pem', '.key', '.p12', '.psd'}


def source_files(root=ROOT, manifest=MANIFEST):
    """Use an explicit manifest so new, unreviewed files cannot slip into a release."""
    root = root.resolve()
    names = [
        line.strip()
        for line in manifest.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    if not names or len(names) != len(set(names)):
        raise ValueError('Release manifest must be nonempty and contain no duplicates')
    files = []
    for name in names:
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or '..' in relative.parts
            or '\\' in name
            or ':' in name
            or any(part in PRIVATE_NAMES or part.startswith('.env') for part in relative.parts)
            or relative.suffix.lower() in PRIVATE_SUFFIXES
        ):
            raise ValueError(f'Unsafe release path: {name}')
        path = root.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f'Missing file or external link: {name}')
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
            raise ValueError(f'Symlinks are not supported in a release: {name}')
        files.append((name, path))
    return files


def export_source(output, root=ROOT, manifest=MANIFEST):
    files = source_files(root, manifest)
    output = output.resolve()
    if output.suffix.lower() != '.zip' or any(output == path.resolve() for _, path in files):
        raise ValueError('Output must be a ZIP file separate from the source files')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.zip.tmp')
    try:
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
            for name, path in files:
                archive.write(path, name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'hifuu-source.zip')
    args = parser.parse_args()
    count = export_source(args.output)
    print(f'Exported {count} reviewed files: {args.output}')


if __name__ == '__main__':
    main()
