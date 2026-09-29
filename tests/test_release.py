"""A release never inherits unlisted runtime data or local deployment settings."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from tools.export_source import export_source, source_files


class ReleaseTest(unittest.TestCase):
    def test_unlisted_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'run.py').write_text('print("game")', encoding='utf-8')
            (root / 'server-config.json').write_text('private', encoding='utf-8')
            (root / 'room-backup.json').write_text('private', encoding='utf-8')
            manifest = root / 'manifest.txt'
            manifest.write_text('run.py\n', encoding='utf-8')
            output = root / 'release.zip'
            export_source(output, root, manifest)
            with ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ['run.py'])

    def test_manifest_rejects_private_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.txt'
            for name in (
                '../outside.txt',
                'server-config.json',
                '.git/config',
                'runtime/room.json',
                '.env.production',
                'key.pem',
                'missing.py',
            ):
                with self.subTest(name=name):
                    manifest.write_text(name, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        source_files(root, manifest)

    def test_project_manifest_contains_runtime_assets(self):
        files = dict(source_files())
        for name in (
            'data/stations.csv',
            'data/edges.csv',
            'data/network.svg',
            'config/dialogues.json',
            'config/rules.json',
            'public/game-text.json',
            'server-config.example.json',
        ):
            self.assertIn(name, files)
