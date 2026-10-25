"""The previews of what is attached to a chat, and pictures in the user's message bubbles."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from voice_assistant.attach_strip import AttachStrip  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def jpeg_bytes():
    image = QImage(64, 48, QImage.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "JPG")
    return bytes(data)


def pump(app, seconds=1.0, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def files(tmp_path):
    picture = tmp_path / "photo.jpg"
    picture.write_bytes(jpeg_bytes())
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 3000)
    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    return picture, video, pdf


def items(files):
    picture, video, pdf = files
    return {"photo.jpg": {"path": picture, "kind": "image", "size": picture.stat().st_size},
            "clip.mp4": {"path": video, "kind": "video", "size": 3000},
            "bill.pdf": {"path": pdf, "kind": "pdf", "size": 8}}


def test_it_is_hidden_until_something_is_attached_and_shows_one_tile_each(app, files):
    strip = AttachStrip(frame_for_video=lambda p: None)
    strip.set_items({})
    assert strip.isHidden()
    strip.set_items(items(files))
    assert not strip.isHidden() and len(strip._tiles) == 3
    strip.set_items({})
    assert strip.isHidden() and not strip._tiles


def test_a_picture_tile_shows_the_picture_and_other_files_show_an_icon(app, files):
    strip = AttachStrip(frame_for_video=lambda p: None)
    strip.set_items(items(files))
    tiles = {t.path.name: t for t in strip._tiles.values()}
    assert tiles["photo.jpg"].picture.pixmap() is not None and not tiles["photo.jpg"].picture.pixmap().isNull()
    assert tiles["bill.pdf"].picture.text() != "" and tiles["bill.pdf"].picture.pixmap().isNull()


def test_a_video_gets_its_frame_found_in_the_background_and_remembered(app, files):
    calls = []

    def find(path):
        calls.append(path.name)
        return jpeg_bytes()

    strip = AttachStrip(frame_for_video=find)
    strip.set_items(items(files))
    video_path = str(files[1])
    pump(app, 3, until=lambda: video_path in strip.frames())
    assert calls == ["clip.mp4"] and video_path in strip.frames()
    tile = strip._tiles[video_path]
    assert not tile.picture.pixmap().isNull()
    strip.set_items(items(files))  # redrawing does not look for the frame again
    pump(app, 0.3)
    assert calls == ["clip.mp4"] and not strip._tiles[video_path].picture.pixmap().isNull()


def test_a_video_that_cannot_be_read_just_keeps_its_icon(app, files):
    strip = AttachStrip(frame_for_video=lambda p: (_ for _ in ()).throw(RuntimeError("no decoder")))
    strip.set_items(items(files))
    pump(app, 0.5)
    assert strip.frames() == {} and strip._tiles[str(files[1])].picture.text() != ""


def test_the_remove_button_reports_which_attachment(app, files):
    strip = AttachStrip(frame_for_video=lambda p: None)
    strip.set_items(items(files))
    removed = []
    strip.removed.connect(removed.append)
    tile = strip._tiles[str(files[2])]
    from PySide6.QtWidgets import QToolButton

    tile.findChild(QToolButton).click()
    assert removed == ["bill.pdf"]


def test_the_chat_shows_pictures_and_a_video_frame_above_the_message(app, files):
    from voice_assistant.app import Chat

    chat = Chat()
    picture, video, pdf = files
    chat.add_user("what is this?", [picture, video, pdf], {str(video): jpeg_bytes()})
    pixmaps = [w for w in chat.findChildren(QLabel) if w.pixmap() is not None and not w.pixmap().isNull()]
    texts = [w.text() for w in chat.findChildren(QLabel)]
    assert len(pixmaps) == 2  # the picture, and a frame of the video
    assert "what is this?" in texts and any("bill.pdf" in t for t in texts)  # the PDF is a chip with its name
    chat.clear()
    assert not chat.findChildren(QLabel)  # nothing is left behind when the chat is cleared
