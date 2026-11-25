"""Tests for the pure helper functions (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys
import time

import pytest

_HELPERS = Path(__file__).parents[1] / "custom_components" / "pcloud_sync" / "helpers.py"
_spec = importlib.util.spec_from_file_location("pcloud_sync_helpers", _HELPERS)
helpers = importlib.util.module_from_spec(_spec)
sys.modules["pcloud_sync_helpers"] = helpers
_spec.loader.exec_module(helpers)


# --------------------------------------------------------------- remote paths


@pytest.mark.parametrize(
    ("segments", "expected"),
    [
        (("/HomeAssistant/Sync", "frigate/clips"), "/HomeAssistant/Sync/frigate/clips"),
        (("HomeAssistant//Sync/", ""), "/HomeAssistant/Sync"),
        (("/a", "b\\c"), "/a/b/c"),
        (("/a", "./b"), "/a/b"),
        (("/",), "/"),
    ],
)
def test_join_remote(segments, expected):
    assert helpers.join_remote(*segments) == expected


@pytest.mark.parametrize("bad", ["../etc", "/a/../../b", "a/.."])
def test_join_remote_rejects_traversal(bad):
    with pytest.raises(helpers.UnsafePathError):
        helpers.join_remote("/root", bad)


def test_sanitize_name():
    assert helpers.sanitize_name('cam:1<2>"x"|?*.jpg') == "cam_1_2__x____.jpg"
    assert helpers.sanitize_name("name. ") == "name"
    assert len(helpers.sanitize_name("x" * 400)) == 255
    for bad in ("", ".", "..", "   "):
        with pytest.raises(helpers.UnsafePathError):
            helpers.sanitize_name(bad)


def test_date_subfolder():
    assert helpers.date_subfolder(datetime(2026, 3, 7, 12, 0)) == "2026/03/07"


# --------------------------------------------------------------- patterns


def test_split_patterns():
    assert helpers.split_patterns("*.mp4, *.jpg\n*.png,") == ["*.mp4", "*.jpg", "*.png"]
    assert helpers.split_patterns("") == []
    assert helpers.split_patterns(None) == []


@pytest.mark.parametrize(
    ("relative", "include", "exclude", "expected"),
    [
        ("a.mp4", ["*.mp4"], [], True),
        ("a.jpg", ["*.mp4"], [], False),
        ("cam/a.jpg", ["cam/*"], [], True),
        ("a.mp4", [], [], True),  # empty include = everything
        ("a.tmp", ["*"], ["*.tmp"], False),
        (".hidden", ["*"], [".*"], False),
        (".cache/a.mp4", ["*"], [".*"], False),  # hidden folder
        ("cache/a.mp4", ["*"], ["cache"], False),  # folder name
        ("x/cache.mp4", ["*"], ["cache"], True),
    ],
)
def test_matches(relative, include, exclude, expected):
    assert helpers.matches(relative, include, exclude) is expected


# --------------------------------------------------------------- watch folders


def test_parse_watch_folders():
    folders = helpers.parse_watch_folders(
        """
        # comment
        /media/frigate/clips -> frigate/clips
        /config/www/snapshots/
        /media/frigate/clips -> duplicate/ignored
        """
    )
    assert folders == [
        helpers.WatchFolder("/media/frigate/clips", "frigate/clips"),
        helpers.WatchFolder("/config/www/snapshots", "snapshots"),
    ]


@pytest.mark.parametrize("bad", ["relative/path", "/media -> ../x"])
def test_parse_watch_folders_invalid(bad):
    with pytest.raises(helpers.UnsafePathError):
        helpers.parse_watch_folders(bad)


# --------------------------------------------------------------- file system


def _touch(path: Path, content: bytes = b"x", age: float = 3600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    old = time.time() - age
    os.utime(path, (old, old))


def test_scan_folder(tmp_path: Path):
    _touch(tmp_path / "a.mp4")
    _touch(tmp_path / "sub" / "b.jpg")
    _touch(tmp_path / "c.tmp")
    _touch(tmp_path / "fresh.mp4", age=0)
    found = {
        f.relative
        for f in helpers.scan_folder(str(tmp_path), ["*"], ["*.tmp"], min_age=60, now=time.time())
    }
    assert found == {"a.mp4", "sub/b.jpg"}


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_scan_folder_ignores_symlinks(tmp_path: Path):
    outside = tmp_path / "outside"
    _touch(outside / "secret.txt")
    base = tmp_path / "base"
    base.mkdir()
    (base / "link_dir").symlink_to(outside, target_is_directory=True)
    (base / "link_file").symlink_to(outside / "secret.txt")
    assert list(helpers.scan_folder(str(base), ["*"], [], min_age=0, now=time.time())) == []


def test_sha1(tmp_path: Path):
    file = tmp_path / "f.bin"
    file.write_bytes(b"hello")
    expected = "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
    assert helpers.sha1_of_file(file) == expected
    assert helpers.sha1_of_bytes(b"hello") == expected


def test_is_within(tmp_path: Path):
    assert helpers.is_within(tmp_path / "a" / "b", tmp_path)
    assert helpers.is_within(tmp_path, tmp_path)
    assert not helpers.is_within(tmp_path.parent, tmp_path)
    assert not helpers.is_within(tmp_path / ".." / "x", tmp_path)


def test_remove_empty_parents(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c.txt"
    _touch(deep)
    _touch(tmp_path / "a" / "keep.txt")
    deep.unlink()
    helpers.remove_empty_parents(deep, tmp_path)
    assert not (tmp_path / "a" / "b").exists()
    assert (tmp_path / "a").exists()  # not empty -> kept
    assert tmp_path.exists()


def test_human_size():
    assert helpers.human_size(512) == "512 B"
    assert helpers.human_size(1536) == "1.5 KB"
    assert helpers.human_size(5 * 1024**3) == "5.0 GB"
