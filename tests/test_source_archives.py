"""Regression checks for verified, sequential source archive extraction."""

import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package


class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive_number = 0

    def make_archive(self, entries, mode="w:xz"):
        """Bytes are files, None is a directory, and strings are symlinks."""
        self.archive_number += 1
        path = self.root / ("source-" + str(self.archive_number) + ".tar")
        with tarfile.open(str(path), mode) as archive:
            for name, value in entries:
                member = tarfile.TarInfo(name)
                if value is None:
                    member.type = tarfile.DIRTYPE
                elif isinstance(value, str):
                    member.type = tarfile.SYMTYPE
                    member.linkname = value
                else:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
                    continue
                archive.addfile(member)
        return path

    def test_reads_files_in_original_archive_order(self):
        entries = [
            ("source/z.c", b"last alphabetically"),
            ("source/nested/b.c", b"deeper path"),
            ("source/a.c", b"first alphabetically"),
        ]
        for mode in ("w", "w:gz", "w:bz2", "w:xz"):
            with self.subTest(mode=mode):
                path = self.make_archive(entries, mode)
                accessed = []
                original_open = tarfile.open

                def observe(*args, **kwargs):
                    archive = original_open(*args, **kwargs)
                    original_extractfile = archive.extractfile

                    def record(member):
                        accessed.append((member.name, member.offset_data))
                        return original_extractfile(member)

                    archive.extractfile = record
                    return archive

                destination = self.root / ("output-" + str(self.archive_number))
                with mock.patch.object(package.tarfile, "open", side_effect=observe):
                    package.extract_source_archive(path, destination, "source")
                self.assertEqual([name for name, _ in accessed], [name for name, _ in entries])
                offsets = [offset for _, offset in accessed]
                self.assertEqual(offsets, sorted(offsets))
                for name, content in entries:
                    self.assertEqual((destination / name.split("/", 1)[1]).read_bytes(), content)

    def test_directory_entry_can_follow_its_file(self):
        path = self.make_archive([
            ("source/nested/file.c", b"content"),
            ("source/nested/", None),
            ("source/empty", b""),
        ])
        destination = self.root / "output"
        package.extract_source_archive(path, destination, "source")
        self.assertEqual((destination / "nested/file.c").read_bytes(), b"content")
        self.assertEqual((destination / "empty").read_bytes(), b"")

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires Windows privileges")
    def test_symlink_can_precede_its_target(self):
        path = self.make_archive([
            ("source/link", "target.c"),
            ("source/target.c", b"content"),
        ])
        destination = self.root / "output"
        package.extract_source_archive(path, destination, "source")
        self.assertTrue((destination / "link").is_symlink())
        self.assertEqual(os.readlink(str(destination / "link")), "target.c")
        self.assertEqual((destination / "link").read_bytes(), b"content")

    def test_rejects_unsafe_members_before_extracting_any_files(self):
        cases = [
            [("source/../escape", b"bad")],
            [("/source/escape", b"bad")],
            [("other/file", b"bad")],
            [("source/link", "../../escape")],
            [("source/duplicate", b"one"), ("source/duplicate", b"two")],
            [("source/link", "target"), ("source/link/child", b"bad")],
            [("source/link/child", b"bad"), ("source/link", "target")],
        ]
        for unsafe in cases:
            with self.subTest(members=unsafe):
                path = self.make_archive([("source/first", b"valid")] + unsafe)
                destination = self.root / ("output-" + str(self.archive_number))
                with self.assertRaises(ValueError):
                    package.extract_source_archive(path, destination, "source")
                self.assertEqual(list(destination.iterdir()), [])

    def test_source_revision_verifies_sha256_before_extraction(self):
        path = self.make_archive([("source/file.c", b"content")])
        data = path.read_bytes()
        for matches in (False, True):
            with self.subTest(checksum_matches=matches):
                case = self.root / ("valid" if matches else "invalid")
                package_root = case / "package"
                package_root.mkdir(parents=True)
                metadata = {"source": {
                    "path": "source",
                    "url": "https://example.invalid/source.tar.xz",
                    "archive_sha256": hashlib.sha256(data).hexdigest() if matches else "0" * 64,
                    "strip_prefix": "source",
                    "revision": "pinned-revision",
                }}
                response = io.BytesIO(data)
                response.geturl = lambda: metadata["source"]["url"]
                with mock.patch.object(package, "urlopen", return_value=response), \
                        mock.patch.object(package, "extract_source_archive", wraps=package.extract_source_archive) as extract:
                    if matches:
                        revision, source = package.source_revision(package_root, metadata, True, case / "work")
                        self.assertEqual(revision, "pinned-revision")
                        self.assertEqual((source / "file.c").read_bytes(), b"content")
                        extract.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                            package.source_revision(package_root, metadata, True, case / "work")
                        extract.assert_not_called()
                        self.assertFalse((case / "work/upstream-source.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
