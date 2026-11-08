#!/usr/bin/env python3
"""
CTGP-7 Launcher – Theme Editor  (PyQt5)

A CTGP-7 launcher theme is a folder (or a .zip) containing:
    sprites/            -> PNG images (backgrounds, buttons, progress bar)
    sprites/textSprites -> PNG images of the texts
    sound/              -> .bcwav sounds

Features:
  SPRITES  replace with automatic resizing, recolor (hue / saturation / brightness),
           approximate preview of the launcher screens
  SOUNDS   play / waveform of .bcwav files, replace with a WAV/OGG converted with
           cwavtool (dspadpcm, pcm16, pcm8, imaadpcm, loop), export to WAV
  3DS      upload over FTP (ftpd on the 3DS) to  sdmc:/CTGP-7/MyStuff/LauncherFS
  THEME    open / save a folder, open / export a .zip

cwavtool: put the "cwavtool" folder (with the subfolders windows-x86_64, windows-i686,
linux-x86_64, linux-i686, mac-x86_64) next to this script. The right version is picked
automatically.

Requires: pip install PyQt5
"""
import array
import ftplib
import os
import platform
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zipfile

from PyQt5.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

try:
    from PyQt5.QtMultimedia import QSoundEffect
    HAVE_AUDIO = True
except ImportError:  # QtMultimedia missing: playback disabled
    HAVE_AUDIO = False

if getattr(sys, "frozen", False):          # PyInstaller executable
    APP_DIR = os.path.dirname(sys.executable)                   # folder of the .exe
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)              # embedded files
else:
    APP_DIR = BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
# the executable's folder comes first: sprites.zip / cwavtool can be replaced next to the .exe
SEARCH_DIRS = [APP_DIR] if APP_DIR == BUNDLE_DIR else [APP_DIR, BUNDLE_DIR]
DEFAULT_REMOTE_DIR = "/CTGP-7/MyStuff/LauncherFS"   # = sdmc:/CTGP-7/MyStuff/LauncherFS

# The reference sprites (names + expected sizes) come from the "sprites.zip" file.
# This table only gives a readable name and a group to each known file;
# a sprite that is not listed here is still handled (name derived from the file name).
LABELS = {
    "topBackground.png": ("Top screen background", "Top screen"),
    "topInfoBackground.png": ("Info frame – Launch mode", "Top screen"),
    "topInfoBackgroundUpd.png": ("Info frame – Update mode", "Top screen"),
    "bottomBackground.png": ("Bottom screen background", "Bottom screen"),
    "tinyButtonBackground.png": ("Button – normal", "Buttons"),
    "selTinyButtonBackground.png": ("Button – selected", "Buttons"),
    "barBorder.png": ("Progress bar – border", "Progress bar"),
    "barFill.png": ("Progress bar – fill", "Progress bar"),
    "launchText.png": ("Text – Launch button", "Texts"),
    "updateText.png": ("Text – Update button", "Texts"),
    "launchControlsSprite.png": ("Text – controls (Launch)", "Texts"),
    "updateControlsText.png": ("Text – controls (Update)", "Texts"),
    "pressBExit.png": ("Text – press B to exit", "Texts"),
    "launcherText.png": ("Text – Launcher", "Texts"),
    "installerText.png": ("Text – Installer", "Texts"),
    "changelogText.png": ("Text – Changelog", "Texts"),
    "creditsText.png": ("Text – Credits", "Texts"),
    "betaMode.png": ("Text – Beta mode", "Texts"),
}
LABEL_ORDER = {k: i for i, k in enumerate(LABELS)}
GROUPS = ["Top screen", "Bottom screen", "Buttons", "Progress bar", "Texts",
          "Other sprites", "Sounds"]
REF_ZIP_NAMES = ["sprites.zip", os.path.join("reference", "sprites.zip")]
SOUND_ZIP_NAMES = ["sound.zip", "sounds.zip", os.path.join("reference", "sound.zip")]
ENCODINGS = ["dspadpcm", "pcm16", "pcm8", "imaadpcm"]
ENC_NAMES = {0: "PCM 8-bit", 1: "PCM 16-bit", 2: "DSP-ADPCM", 3: "IMA-ADPCM"}


# ============================================================== image utilities
def checker_pixmap(w, h, cell=8):
    pm = QPixmap(max(w, 1), max(h, 1))
    p = QPainter(pm)
    c1, c2 = QColor(200, 200, 200), QColor(160, 160, 160)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            p.fillRect(x, y, cell, cell, c1 if ((x // cell + y // cell) % 2 == 0) else c2)
    p.end()
    return pm


def fit_image(img, size, mode):
    """mode 0 = stretch, 1 = fit (transparent margins), 2 = fill (crop)."""
    tw, th = size
    img = img.convertToFormat(QImage.Format_ARGB32)
    if mode == 0:
        return img.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if mode == 1:
        sc = img.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QImage(tw, th, QImage.Format_ARGB32)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.drawImage((tw - sc.width()) // 2, (th - sc.height()) // 2, sc)
        p.end()
        return canvas
    sc = img.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    return sc.copy((sc.width() - tw) // 2, (sc.height() - th) // 2, tw, th)


def recolor_image(src, hue=0, sat=0, bri=0):
    """Shifts the hue (degrees), saturation and brightness (in %). Keeps the alpha channel."""
    img = src.convertToFormat(QImage.Format_ARGB32).copy()
    if hue == 0 and sat == 0 and bri == 0:
        return img
    w, h = img.width(), img.height()
    cache = {}
    for y in range(h):
        for x in range(w):
            px = img.pixel(x, y)
            a = (px >> 24) & 0xFF
            if a == 0:
                continue
            rgb = px & 0xFFFFFF
            new = cache.get(rgb)
            if new is None:
                c = QColor(rgb)
                hh, ss, vv, _ = c.getHsv()
                if hh >= 0:
                    hh = (hh + hue) % 360
                ss = max(0, min(255, int(ss * (1 + sat / 100.0))))
                vv = max(0, min(255, int(vv + bri * 2.55)))
                c.setHsv(hh, ss, vv)
                new = c.rgb() & 0xFFFFFF
                cache[rgb] = new
            img.setPixel(x, y, (a << 24) | new)
    return img


def find_theme_root(path):
    if os.path.isdir(os.path.join(path, "sprites")) or os.path.isdir(os.path.join(path, "sound")):
        return path
    for root, dirs, _files in os.walk(path):
        if "sprites" in dirs or "sound" in dirs:
            return root
    return path


def clear_dir(d):
    for name in os.listdir(d):
        p = os.path.join(d, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def label_for(rel):
    """(readable name, group) of a sprite, from its path."""
    base = os.path.basename(rel)
    if base in LABELS:
        return LABELS[base]
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", os.path.splitext(base)[0])
    return name, ("Texts" if "/textSprites/" in rel else "Other sprites")


def ref_sort_key(rel):
    label, group = label_for(rel)
    gi = GROUPS.index(group) if group in GROUPS else len(GROUPS)
    return (gi, LABEL_ORDER.get(os.path.basename(rel), 999), rel)


def load_reference(zip_path, dest_dir):
    """Extracts the reference sprites zip; returns {relative_path: info}.
    The expected sizes are read directly from the PNG files in the zip."""
    clear_dir(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        base = os.path.realpath(dest_dir)
        for n in z.namelist():  # zip-slip protection
            if not os.path.realpath(os.path.join(base, n)).startswith(base):
                raise ValueError(f"Unsafe path in archive: {n}")
        z.extractall(dest_dir)
    root = find_theme_root(dest_dir)
    prefix = ""
    walk_from = os.path.join(root, "sprites")
    if not os.path.isdir(walk_from):       # zip without a "sprites" folder
        walk_from, prefix = root, "sprites/"
    ref = {}
    for r, _d, fs in os.walk(walk_from):
        for f in fs:
            if not f.lower().endswith(".png"):
                continue
            full = os.path.join(r, f)
            im = QImage(full)
            if im.isNull():
                continue
            rel = prefix + os.path.relpath(full, root if not prefix else walk_from).replace("\\", "/")
            label, group = label_for(rel)
            ref[rel] = {"size": (im.width(), im.height()), "label": label,
                        "group": group, "path": full}
    if not ref:
        raise ValueError("No PNG sprite found in this zip.")
    return ref


def load_sound_reference(zip_path, dest_dir):
    """Extracts the reference sounds zip; returns {"sound/x.bcwav": info}."""
    clear_dir(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        base = os.path.realpath(dest_dir)
        for n in z.namelist():  # zip-slip protection
            if not os.path.realpath(os.path.join(base, n)).startswith(base):
                raise ValueError(f"Unsafe path in archive: {n}")
        z.extractall(dest_dir)
    ref = {}
    for r, _d, fs in os.walk(dest_dir):
        for f in sorted(fs):
            if not f.lower().endswith(".bcwav") or f.startswith("._"):
                continue
            full = os.path.join(r, f)
            try:
                with open(full, "rb") as fh:
                    pi = parse_cwav(fh.read())
            except CwavError:
                continue
            ref["sound/" + f] = {"path": full, "label": f, "encoding": pi["encoding"],
                                 "rate": pi["rate"], "channels": len(pi["channels"]),
                                 "loop": pi["loop"]}
    if not ref:
        raise ValueError("No valid .bcwav file found in this zip.")
    return ref


# ================================================================ cwavtool
def platform_dir():
    """Name of the cwavtool subfolder matching this machine."""
    m = platform.machine().lower()
    is64 = sys.maxsize > 2 ** 32 or m in ("amd64", "x86_64", "arm64", "aarch64")
    if sys.platform.startswith("win"):
        return "windows-x86_64" if is64 else "windows-i686"
    if sys.platform == "darwin":
        return "mac-x86_64"          # on Apple Silicon: requires Rosetta
    return "linux-x86_64" if is64 else "linux-i686"


def find_cwavtool(custom=None):
    exe = "cwavtool.exe" if sys.platform.startswith("win") else "cwavtool"
    cands = []
    if custom:
        cands.append(custom)
    for base in SEARCH_DIRS:
        cands += [
            os.path.join(base, "cwavtool", platform_dir(), exe),
            os.path.join(base, platform_dir(), exe),
            os.path.join(base, "cwavtool", exe),
            os.path.join(base, exe),
        ]
    w = shutil.which("cwavtool")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c):
            if not sys.platform.startswith("win") and not os.access(c, os.X_OK):
                try:
                    os.chmod(c, 0o755)
                except OSError:
                    pass
            return c
    return None


def run_cwavtool(tool, src, dst, encoding="dspadpcm", loop_start=None, loop_end=None):
    """Converts a WAV/OGG to BCWAV. Returns (ok, message)."""
    cmd = [tool, "-i", src, "-o", dst, "-e", encoding]
    if loop_start is not None:
        cmd += ["-ls", str(int(loop_start)), "-le", "end" if loop_end in (None, 0) else str(int(loop_end))]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           stdin=subprocess.DEVNULL, **kw)
    except Exception as e:
        return False, f"Could not run cwavtool: {e}"
    out = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        return False, out or f"cwavtool failed (code {r.returncode})"
    return True, out


# ================================================================ BCWAV / CWAV
class CwavError(Exception):
    pass


def parse_cwav(data):
    """Reads the header of a CWAV/BCWAV. Returns a dict of information."""
    if len(data) < 0x60 or data[:4] != b"CWAV":
        raise CwavError("This is not a valid CWAV/BCWAV file.")
    E = "<" if data[4:6] == b"\xff\xfe" else ">"
    info_off, _info_size = struct.unpack_from(E + "II", data, 0x18)
    data_off, _data_size = struct.unpack_from(E + "II", data, 0x24)
    if data[info_off:info_off + 4] != b"INFO" or data[data_off:data_off + 4] != b"DATA":
        raise CwavError("INFO/DATA blocks not found.")
    enc, loop = data[info_off + 8], data[info_off + 9]
    rate, loop_start, loop_end = struct.unpack_from(E + "III", data, info_off + 0x0C)
    base = info_off + 0x1C
    nch = struct.unpack_from(E + "I", data, base)[0]
    if not 1 <= nch <= 8:
        raise CwavError(f"Invalid number of channels ({nch}).")
    chans = []
    for i in range(nch):
        ref_off = struct.unpack_from(E + "I", data, base + 4 + 8 * i + 4)[0]
        ch = base + ref_off
        samp_off = struct.unpack_from(E + "I", data, ch + 4)[0]
        adpcm_off = struct.unpack_from(E + "I", data, ch + 12)[0]
        coefs = ima = None
        if enc == 2:
            coefs = list(struct.unpack_from(E + "16h", data, ch + adpcm_off))
        elif enc == 3:   # IMA-ADPCM: initial predictor (s16) + step index (u8)
            ima = struct.unpack_from(E + "hB", data, ch + adpcm_off)
        chans.append({"data": data_off + 8 + samp_off, "coefs": coefs, "ima": ima})
    return {"endian": E, "encoding": enc, "loop": bool(loop), "rate": rate,
            "loop_start": loop_start, "loop_end": loop_end, "frames": loop_end,
            "channels": chans, "data_end": data_off + _data_size}


def _decode_dsp(raw, coefs, n_frames):
    out = array.array("h")
    h1 = h2 = 0
    pos, remaining, n = 0, n_frames, len(raw)
    while remaining > 0 and pos + 8 <= n:
        hdr = raw[pos]
        scale = 1 << (hdr & 0xF)
        idx = (hdr >> 4) & 7
        c1, c2 = coefs[idx * 2], coefs[idx * 2 + 1]
        take = 14 if remaining > 14 else remaining
        for i in range(take):
            b = raw[pos + 1 + (i >> 1)]
            nib = (b >> 4) if (i & 1) == 0 else (b & 0xF)
            if nib >= 8:
                nib -= 16
            s = (((nib * scale) << 11) + 1024 + c1 * h1 + c2 * h2) >> 11
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            out.append(s)
            h2, h1 = h1, s
        pos += 8
        remaining -= take
    return out


IMA_STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55,
            60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
            337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
            1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
            5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
            20350, 22385, 24623, 27086, 29794, 32767]
IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


def _decode_ima(raw, pred, idx, n_frames):
    """4-bit IMA-ADPCM (low nibble first). The initial predictor is the 1st sample."""
    out = array.array("h", [pred])
    n = len(raw) * 2
    for i in range(min(n_frames - 1, n)):
        b = raw[i >> 1]
        nib = (b & 0xF) if (i & 1) == 0 else (b >> 4)
        step = IMA_STEP[idx]
        diff = step >> 3
        if nib & 1:
            diff += step >> 2
        if nib & 2:
            diff += step >> 1
        if nib & 4:
            diff += step
        pred += -diff if nib & 8 else diff
        if pred > 32767:
            pred = 32767
        elif pred < -32768:
            pred = -32768
        idx += IMA_INDEX[nib & 7]
        idx = 0 if idx < 0 else (88 if idx > 88 else idx)
        out.append(pred)
    return out


def decode_cwav(data):
    """Returns (info, [array('h') per channel]). Raises CwavError if the encoding is not handled."""
    info = parse_cwav(data)
    E, enc, frames = info["endian"], info["encoding"], info["frames"]
    chans = []
    for ch in info["channels"]:
        start = ch["data"]
        if enc == 2:
            raw = data[start:info["data_end"]]
            chans.append(_decode_dsp(raw, ch["coefs"], frames))
        elif enc == 1:
            a = array.array("h")
            a.frombytes(data[start:start + frames * 2])
            if (E == "<") != (sys.byteorder == "little"):
                a.byteswap()
            chans.append(a)
        elif enc == 0:
            chans.append(array.array("h", [(b - 256 if b > 127 else b) << 8
                                           for b in data[start:start + frames]]))
        elif enc == 3 and ch["ima"]:
            pred, idx = ch["ima"]
            chans.append(_decode_ima(data[start:info["data_end"]], pred, min(idx, 88), frames))
        else:
            raise CwavError(f"Encoding not supported for preview (code {enc}). "
                            "Replacing and exporting the .bcwav still works.")
    return info, chans


def write_wav(path, chans, rate):
    n = min(len(c) for c in chans)
    nch = len(chans)
    inter = array.array("h", [0]) * (n * nch)
    for i, c in enumerate(chans):
        inter[i::nch] = c[:n]
    if sys.byteorder == "big":
        inter.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(inter.tobytes())


# ================================================================ FTP (3DS)
def ftp_makedirs(ftp, path):
    ftp.cwd("/")
    for part in [p for p in path.split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def ftp_run(cfg, files, log, progress, should_stop):
    """files=None: connection test. Otherwise uploads [(local_path, relative_path)]."""
    ftp = ftplib.FTP()
    log(f"Connecting to {cfg['host']}:{cfg['port']}…")
    ftp.connect(cfg["host"], cfg["port"], timeout=15)
    ftp.login(cfg["user"] or "anonymous", cfg["password"] or "")
    ftp.set_pasv(True)
    log("Connected. " + ftp.getwelcome().splitlines()[0])
    if files is None:
        ftp_makedirs_check = ftp.pwd()
        ftp.quit()
        return f"Connection OK (current folder: {ftp_makedirs_check})."
    remote = cfg["remote_dir"].replace("\\", "/").replace("sdmc:", "")
    if not remote.startswith("/"):
        remote = "/" + remote
    done_dirs = set()
    total = len(files)
    sent = 0
    for i, (local, rel) in enumerate(files):
        if should_stop():
            log("Upload cancelled.")
            break
        sub = posixpath.dirname(rel.replace("\\", "/"))
        target_dir = posixpath.join(remote, sub) if sub else remote
        if target_dir not in done_dirs:
            ftp_makedirs(ftp, target_dir)
            done_dirs.add(target_dir)
        else:
            ftp.cwd(target_dir)
        progress(i, total)
        log(f"Uploading: {rel}")
        with open(local, "rb") as f:
            ftp.storbinary("STOR " + posixpath.basename(rel), f)
        sent += 1
    progress(total, total)
    try:
        ftp.quit()
    except Exception:
        pass
    return f"{sent}/{total} file(s) uploaded to {remote}"


class FtpWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal(bool, str)

    def __init__(self, cfg, files=None):
        super().__init__()
        self.cfg, self.files, self._stop = cfg, files, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            msg = ftp_run(self.cfg, self.files, self.log.emit, self.progress.emit, lambda: self._stop)
            self.done.emit(True, msg)
        except Exception as e:
            self.done.emit(False, f"{type(e).__name__}: {e}")


class FtpDialog(QDialog):
    def __init__(self, parent, workdir, origdir):
        super().__init__(parent)
        self.setWindowTitle("Send the theme to the 3DS (FTP)")
        self.setWindowIcon(QIcon(os.path.join(BUNDLE_DIR, "assets", "icon_ftpd.png")))
        self.resize(560, 520)
        self.workdir, self.origdir = workdir, origdir
        self.worker = None
        self.settings = QSettings("CTGP7LauncherEditor", "Editor")
        v = QVBoxLayout(self)
        help_lbl = QLabel(
            "On the 3DS, start an FTP server (e.g. <b>ftpd</b>) and note the IP address it shows. "
            "The 3DS and the computer must be on the same Wi-Fi network.<br>"
            "Destination folder: <code>sdmc:/CTGP-7/MyStuff/LauncherFS</code>")
        help_lbl.setWordWrap(True)
        v.addWidget(help_lbl)
        form = QFormLayout()
        self.host = QLineEdit(self.settings.value("ftp_host", ""))
        self.host.setPlaceholderText("e.g. 192.168.1.42")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.settings.value("ftp_port", 5000)))
        self.user = QLineEdit(self.settings.value("ftp_user", "anonymous"))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.remote = QLineEdit(self.settings.value("ftp_remote", DEFAULT_REMOTE_DIR))
        form.addRow("3DS IP address:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("User:", self.user)
        form.addRow("Password:", self.pw)
        form.addRow("Remote folder:", self.remote)
        v.addLayout(form)
        self.chk_sound = QCheckBox("Also send the sounds (sound/)")
        self.chk_sound.setChecked(True)
        self.chk_changed = QCheckBox("Only send modified files")
        v.addWidget(self.chk_sound)
        v.addWidget(self.chk_changed)
        self.bar = QProgressBar()
        v.addWidget(self.bar)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        v.addWidget(self.logbox, 1)
        row = QHBoxLayout()
        self.b_test = QPushButton("Test connection")
        self.b_send = QPushButton("Send")
        self.b_close = QPushButton("Close")
        self.b_test.clicked.connect(self.test)
        self.b_send.clicked.connect(self.send)
        self.b_close.clicked.connect(self.close)
        row.addWidget(self.b_test)
        row.addStretch()
        row.addWidget(self.b_send)
        row.addWidget(self.b_close)
        v.addLayout(row)

    def cfg(self):
        c = {"host": self.host.text().strip(), "port": self.port.value(),
             "user": self.user.text().strip(), "password": self.pw.text(),
             "remote_dir": self.remote.text().strip() or DEFAULT_REMOTE_DIR}
        self.settings.setValue("ftp_host", c["host"])
        self.settings.setValue("ftp_port", c["port"])
        self.settings.setValue("ftp_user", c["user"])
        self.settings.setValue("ftp_remote", c["remote_dir"])
        return c

    def collect_files(self):
        files = []
        for root, _d, fs in os.walk(self.workdir):
            for f in fs:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.workdir).replace("\\", "/")
                if rel.startswith("sound/") and not self.chk_sound.isChecked():
                    continue
                if self.chk_changed.isChecked():
                    o = os.path.join(self.origdir, rel)
                    if os.path.isfile(o) and open(o, "rb").read() == open(full, "rb").read():
                        continue
                files.append((full, rel))
        return sorted(files, key=lambda t: t[1])

    def _start(self, files):
        cfg = self.cfg()
        if not cfg["host"]:
            QMessageBox.warning(self, "FTP", "Enter the 3DS IP address.")
            return
        self.logbox.clear()
        self.bar.setValue(0)
        for b in (self.b_test, self.b_send):
            b.setEnabled(False)
        self.worker = FtpWorker(cfg, files)
        self.worker.log.connect(self.logbox.appendPlainText)
        self.worker.progress.connect(lambda d, t: (self.bar.setMaximum(max(t, 1)), self.bar.setValue(d)))
        self.worker.done.connect(self._finished)
        self.worker.start()

    def test(self):
        self._start(None)

    def send(self):
        files = self.collect_files()
        if not files:
            QMessageBox.information(self, "FTP", "No files to send.")
            return
        self._start(files)

    def _finished(self, ok, msg):
        self.logbox.appendPlainText(("✔ " if ok else "✘ ") + msg)
        for b in (self.b_test, self.b_send):
            b.setEnabled(True)

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        e.accept()


# ================================================================ waveform
class WaveformWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.chan = None
        self.setMinimumHeight(110)

    def set_samples(self, chans):
        self.chan = chans[0] if chans else None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(28, 30, 36))
        w, h = self.width(), self.height()
        p.setPen(QColor(70, 74, 84))
        p.drawLine(0, h // 2, w, h // 2)
        a = self.chan
        if not a or len(a) < 2 or w < 2:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "No preview")
            return
        p.setPen(QColor(90, 200, 170))
        n = len(a)
        for x in range(w):
            s, e = x * n // w, max((x + 1) * n // w, x * n // w + 1)
            seg = a[s:e]
            if not seg:
                continue
            lo, hi = min(seg), max(seg)
            y1 = h // 2 - int(hi / 32768.0 * (h // 2 - 2))
            y2 = h // 2 - int(lo / 32768.0 * (h // 2 - 2))
            p.drawLine(x, y1, x, max(y2, y1))


# ================================================================ main window
class LauncherEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CTGP-7 Creator – Theme Editor Launcher")
        self.setFixedSize(1280, 840)
        self.setWindowIcon(QIcon(os.path.join(BUNDLE_DIR, "assets", "icon.png")))

        self.settings = QSettings("CTGP7LauncherEditor", "Editor")
        self.workdir = tempfile.mkdtemp(prefix="ctgp7_work_")
        self.origdir = tempfile.mkdtemp(prefix="ctgp7_orig_")
        self.audiodir = tempfile.mkdtemp(prefix="ctgp7_audio_")
        self.refdir = tempfile.mkdtemp(prefix="ctgp7_ref_")
        self.ref = {}                 # reference sprites (read from sprites.zip)
        self.sounddir = tempfile.mkdtemp(prefix="ctgp7_refsnd_")
        self.ref_sound = {}           # reference sounds (read from sound.zip)
        self.ref_zip = self.ref_sound_zip = None
        self.source = None
        self.dirty = False
        self.current = None
        self.preview_img = None
        self.sound_data = None        # (info, channels) of the displayed sound
        self.effect = QSoundEffect(self) if HAVE_AUDIO else None
        self._slider_labels = []

        self.recolor_timer = QTimer(self)
        self.recolor_timer.setSingleShot(True)
        self.recolor_timer.setInterval(250)
        self.recolor_timer.timeout.connect(self.update_recolor_preview)

        self._build_ui()
        self._build_menu()
        self.load_reference_auto()
        self.load_sound_reference_auto()
        self.new_theme(silent=True)
        missing = [n for n, ok in (("sprites.zip", self.ref), ("sound.zip", self.ref_sound)) if not ok]
        if missing:
            self.statusBar().showMessage(
                "Reference zip not found: " + ", ".join(missing) + " — place it next to the "
                "program.")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Item", "Size"])
        self.tree.setIconSize(QSize(48, 32))
        self.tree.setColumnWidth(0, 250)
        self.tree.currentItemChanged.connect(self.on_select)
        lv.addWidget(self.tree)
        b_addsnd = QPushButton("Add a sound (WAV/OGG → BCWAV)…")
        b_addsnd.clicked.connect(self.add_sound_dialog)
        lv.addWidget(b_addsnd)
        b_ftp = QPushButton("Send to the 3DS (FTP)…")
        b_ftp.clicked.connect(self.open_ftp)
        
        
        lv.addWidget(b_ftp)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab)

        # ---- sprite tab
        sp = QWidget()
        sv = QVBoxLayout(sp)
        self.info = QLabel("Select a sprite or a sound in the list.")
        self.info.setWordWrap(True)
        sv.addWidget(self.info)
        self.img_label = QLabel(alignment=Qt.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.img_label)
        self.scroll.setAlignment(Qt.AlignCenter)
        sv.addWidget(self.scroll, 1)

        zr = QHBoxLayout()
        zr.addWidget(QLabel("Zoom:"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(25, 800)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.refresh_image)
        zr.addWidget(self.zoom, 1)
        self.zoom_lbl = QLabel("100%")
        zr.addWidget(self.zoom_lbl)
        sv.addLayout(zr)

        rr = QHBoxLayout()
        self.chk_resize = QCheckBox("Resize to the expected size")
        self.chk_resize.setChecked(True)
        self.fit_mode = QComboBox()
        self.fit_mode.addItems(["Stretch", "Fit (transparent margins)", "Fill (crop)"])
        b_rep = QPushButton("Replace the sprite…")
        b_rep.clicked.connect(self.replace_dialog)
        b_exp = QPushButton("Export…")
        b_exp.clicked.connect(self.export_sprite)
        b_rev = QPushButton("Revert to original")
        b_rev.clicked.connect(self.revert_current)
        rr.addWidget(self.chk_resize)
        rr.addWidget(self.fit_mode)
        rr.addStretch()
        rr.addWidget(b_rep)
        rr.addWidget(b_exp)
        rr.addWidget(b_rev)
        sv.addLayout(rr)

        gb = QGroupBox("Recolor")
        gv = QVBoxLayout(gb)
        self.sl_hue = self._slider(gv, "Hue", -180, 180, "°")
        self.sl_sat = self._slider(gv, "Saturation", -100, 100, "%")
        self.sl_bri = self._slider(gv, "Brightness", -100, 100, "%")
        gr = QHBoxLayout()
        self.chk_text = QCheckBox("Include the texts (for \"whole theme\")")
        b_reset = QPushButton("Reset sliders")
        b_reset.clicked.connect(self.reset_sliders)
        b_one = QPushButton("Apply to this sprite")
        b_one.clicked.connect(self.apply_recolor_current)
        b_all = QPushButton("Apply to the whole theme")
        b_all.clicked.connect(self.apply_recolor_all)
        gr.addWidget(self.chk_text)
        gr.addStretch()
        gr.addWidget(b_reset)
        gr.addWidget(b_one)
        gr.addWidget(b_all)
        gv.addLayout(gr)
        sv.addWidget(gb)
        self.tab_sprite = sp
        self.tabs.addTab(sp, "Sprite")

        # ---- sound tab
        sd = QWidget()
        dv = QVBoxLayout(sd)
        self.snd_info = QLabel("Select a sound in the list (\"Sounds\" group).")
        self.snd_info.setWordWrap(True)
        dv.addWidget(self.snd_info)
        self.wave = WaveformWidget()
        dv.addWidget(self.wave)
        pr = QHBoxLayout()
        self.b_play = QPushButton("▶ Play")
        self.b_play.clicked.connect(self.play_sound)
        self.b_stop = QPushButton("■ Stop")
        self.b_stop.clicked.connect(lambda: self.effect and self.effect.stop())
        if not HAVE_AUDIO:
            self.b_play.setEnabled(False)
            self.b_stop.setEnabled(False)
            self.b_play.setToolTip("QtMultimedia is not available in this PyQt5 installation")
        b_wavexp = QPushButton("Export as WAV…")
        b_wavexp.clicked.connect(self.export_sound_wav)
        b_bcexp = QPushButton("Export the .bcwav…")
        b_bcexp.clicked.connect(self.export_sprite)
        b_srev = QPushButton("Revert to original")
        b_srev.clicked.connect(self.revert_current)
        pr.addWidget(self.b_play)
        pr.addWidget(self.b_stop)
        pr.addStretch()
        pr.addWidget(b_wavexp)
        pr.addWidget(b_bcexp)
        pr.addWidget(b_srev)
        dv.addLayout(pr)

        cg = QGroupBox("Replace with a WAV / OGG file (converted with cwavtool)")
        cv = QVBoxLayout(cg)
        er = QHBoxLayout()
        er.addWidget(QLabel("Encoding:"))
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(ENCODINGS)
        er.addWidget(self.enc_combo)
        self.chk_loop = QCheckBox("Loop")
        er.addWidget(self.chk_loop)
        er.addWidget(QLabel("start (frame):"))
        self.loop_start = QSpinBox()
        self.loop_start.setRange(0, 2 ** 31 - 1)
        er.addWidget(self.loop_start)
        er.addWidget(QLabel("end (0 = end of sound):"))
        self.loop_end = QSpinBox()
        self.loop_end.setRange(0, 2 ** 31 - 1)
        er.addWidget(self.loop_end)
        er.addStretch()
        cv.addLayout(er)
        self.tool_lbl = QLabel("")
        self.tool_lbl.setWordWrap(True)
        cv.addWidget(self.tool_lbl)
        b_repsnd = QPushButton("Choose a WAV/OGG and replace this sound…")
        b_repsnd.clicked.connect(self.replace_sound_dialog)
        cv.addWidget(b_repsnd)
        dv.addWidget(cg)
        dv.addStretch()
        self.tab_sound = sd
        self.tabs.addTab(sd, "Sound")

        # ---- preview tab
        pv = QWidget()
        pvl = QVBoxLayout(pv)
        top = QHBoxLayout()
        top.addWidget(QLabel("Screen:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Launch", "Update"])
        self.mode_combo.currentIndexChanged.connect(self.render_preview)
        top.addWidget(self.mode_combo)
        top.addWidget(QLabel("Progress:"))
        self.prog = QSlider(Qt.Horizontal)
        self.prog.setRange(0, 100)
        self.prog.setValue(60)
        self.prog.setMaximumWidth(160)
        self.prog.valueChanged.connect(self.render_preview)
        top.addWidget(self.prog)
        top.addStretch()
        pvl.addLayout(top)
        note = QLabel("Approximate preview: the positions of the elements are estimated, "
                      "it is meant to judge how the colors work together, not the exact alignment.")
        note.setStyleSheet("color:gray;")
        note.setWordWrap(True)
        pvl.addWidget(note)
        self.top_lbl = QLabel(alignment=Qt.AlignCenter)
        self.bot_lbl = QLabel(alignment=Qt.AlignCenter)
        pvl.addWidget(self.top_lbl)
        pvl.addWidget(self.bot_lbl)
        pvl.addStretch()
        self.tab_preview = pv
        self.tabs.addTab(pv, "Launcher preview")

        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 900])
        self.setCentralWidget(split)
        self.statusBar().showMessage("Ready")
        self.update_tool_label()

    def _slider(self, layout, name, lo, hi, unit):
        row = QHBoxLayout()
        lbl = QLabel(name)
        lbl.setMinimumWidth(80)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(0)
        val = QLabel(f"0{unit}")
        val.setMinimumWidth(50)
        self._slider_labels.append((val, unit))
        s.valueChanged.connect(lambda v, vl=val, u=unit: vl.setText(f"{v}{u}"))
        s.valueChanged.connect(lambda _v: self.recolor_timer.start())
        row.addWidget(lbl)
        row.addWidget(s, 1)
        row.addWidget(val)
        layout.addLayout(row)
        return s

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")

        def act(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(fn)
            menu.addAction(a)

        act(m, "New theme (from the reference sprites)", lambda: self.new_theme(), "Ctrl+N")
        act(m, "Open a folder…", self.open_folder, "Ctrl+O")
        act(m, "Open a .zip…", self.open_zip)
        m.addSeparator()
        act(m, "Save", self.save, "Ctrl+S")
        act(m, "Save as (folder)…", self.save_as, "Ctrl+Shift+S")
        act(m, "Export as .zip…", self.export_zip)
        act(m, "Send to the 3DS (FTP)…", self.open_ftp, "Ctrl+E")
        m.addSeparator()
        act(m, "Quit", self.close, "Ctrl+Q")
        t = self.menuBar().addMenu("&Tools")

        t.addSeparator()

        act(t, "Check cwavtool", self.check_cwavtool)

    # ------------------------------------------------------------ helpers
    def path_of(self, rel):
        return os.path.join(self.workdir, rel)

    def load_qimage(self, rel):
        p = self.path_of(rel)
        return QImage(p) if os.path.isfile(p) else QImage()

    # ---------------------------------------------------- reference sprites
    def load_reference_auto(self):
        cands = [self.settings.value("ref_zip", "") or None]
        cands += [os.path.join(b, n) for b in SEARCH_DIRS for n in REF_ZIP_NAMES]
        for c in cands:
            if c and os.path.isfile(c):
                try:
                    self.ref = load_reference(c, self.refdir)
                    self.ref_zip = c
                    return True
                except Exception:
                    continue
        self.ref = {}
        self.ref_zip = None
        return False

    def choose_reference(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Reference sprites zip (sprites.zip)", "", "ZIP archives (*.zip)")
        if not p:
            return
        try:
            self.ref = load_reference(p, self.refdir)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.ref_zip = p
        self.settings.setValue("ref_zip", p)
        self.populate_tree(select=self.current)
        self.render_preview()
        self.statusBar().showMessage(f"{len(self.ref)} reference sprites loaded from {p}")

    def load_sound_reference_auto(self):
        cands = [self.settings.value("sound_zip", "") or None]
        cands += [os.path.join(b, n) for b in SEARCH_DIRS for n in SOUND_ZIP_NAMES]
        for c in cands:
            if c and os.path.isfile(c):
                try:
                    self.ref_sound = load_sound_reference(c, self.sounddir)
                    self.ref_sound_zip = c
                    return True
                except Exception:
                    continue
        self.ref_sound = {}
        self.ref_sound_zip = None
        return False

    def choose_sound_reference(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Reference sounds zip (sound.zip)", "", "ZIP archives (*.zip)")
        if not p:
            return
        try:
            self.ref_sound = load_sound_reference(p, self.sounddir)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.ref_sound_zip = p
        self.settings.setValue("sound_zip", p)
        self.populate_tree(select=self.current)
        self.statusBar().showMessage(f"{len(self.ref_sound)} reference sounds loaded from {p}")

    def reference_file(self, rel):
        """Path of the reference file (sprite or sound) for a relative path, otherwise None."""
        info = self.ref.get(rel) or self.ref_sound.get(rel)
        return info["path"] if info else None

    def restore_missing(self):
        ns = nd = 0
        for table in (self.ref, self.ref_sound):
            for rel, info in table.items():
                if not os.path.isfile(self.path_of(rel)):
                    os.makedirs(os.path.dirname(self.path_of(rel)), exist_ok=True)
                    shutil.copyfile(info["path"], self.path_of(rel))
                    if table is self.ref:
                        ns += 1
                    else:
                        nd += 1
        if ns or nd:
            self.mark_dirty()
            self.populate_tree(select=self.current)
            self.render_preview()
        self.statusBar().showMessage(
            f"Restored from the reference: {ns} sprite(s), {nd} sound(s)")

    def expected_size(self, rel):
        if rel in self.ref:
            return self.ref[rel]["size"]
        o = os.path.join(self.origdir, rel)
        if os.path.isfile(o):
            im = QImage(o)
            return (im.width(), im.height())
        return None

    def mark_dirty(self):
        self.dirty = True
        self.setWindowTitle("CTGP-7 Creator – Theme Editor Launcher *")

    def confirm_discard(self):
        if not self.dirty:
            return True
        return QMessageBox.question(
            self, "Unsaved changes",
            "There are unsaved changes. Continue and lose them?") == QMessageBox.Yes

    def all_sprite_paths(self):
        found = []
        base = os.path.join(self.workdir, "sprites")
        for root, _d, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".png"):
                    found.append(os.path.relpath(os.path.join(root, f), self.workdir).replace("\\", "/"))
        return found

    def all_sound_paths(self):
        d = os.path.join(self.workdir, "sound")
        if not os.path.isdir(d):
            return []
        return sorted("sound/" + f for f in os.listdir(d) if f.lower().endswith(".bcwav"))

    def populate_tree(self, select=None):
        self.tree.blockSignals(True)
        self.tree.clear()
        present = set(self.all_sprite_paths())
        entries = [(rel, self.ref[rel]["label"], self.ref[rel]["group"])
                   for rel in sorted(self.ref, key=ref_sort_key)]
        for rel in sorted(present - set(self.ref), key=ref_sort_key):
            label, group = label_for(rel)
            if group != "Texts":
                label, group = rel[len("sprites/"):], "Other sprites"
            entries.append((rel, label, group))
        present_snd = set(self.all_sound_paths())
        for rel in sorted(self.ref_sound):
            entries.append((rel, os.path.basename(rel), "Sounds"))
        for rel in sorted(present_snd - set(self.ref_sound)):
            entries.append((rel, os.path.basename(rel), "Sounds"))
        groups = {}
        for g in GROUPS:
            it = QTreeWidgetItem(self.tree, [g])
            it.setFlags(Qt.ItemIsEnabled)
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            groups[g] = it
        target = None
        for rel, label, g in entries:
            it = QTreeWidgetItem(groups[g], [label, ""])
            it.setData(0, Qt.UserRole, rel)
            if rel.endswith(".bcwav"):
                if os.path.isfile(self.path_of(rel)):
                    it.setText(1, f"{os.path.getsize(self.path_of(rel)) // 1024} KB")
                else:
                    it.setText(1, "missing")
                    it.setForeground(0, QBrush(QColor("gray")))
                    it.setForeground(1, QBrush(QColor("gray")))
            else:
                img = self.load_qimage(rel)
                exp = self.expected_size(rel)
                if img.isNull():
                    it.setText(1, "missing")
                    it.setForeground(0, QBrush(QColor("gray")))
                    it.setForeground(1, QBrush(QColor("gray")))
                else:
                    it.setText(1, f"{img.width()}×{img.height()}")
                    it.setIcon(0, QIcon(QPixmap.fromImage(img).scaled(
                        48, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                    if exp and (img.width(), img.height()) != tuple(exp):
                        it.setForeground(1, QBrush(QColor("#d62828")))
                        it.setToolTip(1, f"Expected size: {exp[0]}×{exp[1]}")
            if rel == select:
                target = it
        for _g, it in list(groups.items()):
            if it.childCount() == 0:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
            self.on_select(target, None)

    # --------------------------------------------------- new / open
    def new_theme(self, silent=False):
        if not silent and not self.confirm_discard():
            return
        clear_dir(self.workdir)
        clear_dir(self.origdir)
        os.makedirs(self.path_of("sound"), exist_ok=True)
        for table in (self.ref, self.ref_sound):
            for rel, info in table.items():
                os.makedirs(os.path.dirname(self.path_of(rel)), exist_ok=True)
                shutil.copyfile(info["path"], self.path_of(rel))
        shutil.copytree(self.workdir, self.origdir, dirs_exist_ok=True)
        self._after_load(
            f"New theme created from the reference ({len(self.ref)} sprites, {len(self.ref_sound)} sounds)"
            if (self.ref or self.ref_sound) else "Empty theme (no reference zip loaded)", None)

    def open_folder(self):
        if not self.confirm_discard():
            return
        d = QFileDialog.getExistingDirectory(self, "Choose the theme folder")
        if not d:
            return
        root = find_theme_root(d)
        self._load_from(root)
        self._after_load(f"Folder opened: {root}", root)

    def open_zip(self):
        if not self.confirm_discard():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Open a theme .zip", "", "ZIP archives (*.zip)")
        if not p:
            return
        tmp = tempfile.mkdtemp(prefix="ctgp7_zip_")
        try:
            with zipfile.ZipFile(p) as z:
                base = os.path.realpath(tmp)
                for n in z.namelist():  # zip-slip protection
                    if not os.path.realpath(os.path.join(base, n)).startswith(base):
                        raise ValueError(f"Unsafe path in archive: {n}")
                z.extractall(tmp)
            self._load_from(find_theme_root(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._after_load(f"Archive opened: {p}", None)

    def _load_from(self, root):
        clear_dir(self.workdir)
        clear_dir(self.origdir)
        shutil.copytree(root, self.workdir, dirs_exist_ok=True)
        shutil.copytree(root, self.origdir, dirs_exist_ok=True)

    def _after_load(self, msg, source):
        self.source = source
        self.dirty = False
        self.current = None
        self.preview_img = None
        self.sound_data = None
        if self.effect:
            self.effect.stop()
        self.setWindowTitle("CTGP-7 Launcher – Theme Editor")
        self.info.setText("Select a sprite or a sound in the list.")
        self.img_label.clear()
        self.snd_info.setText("Select a sound in the list (\"Sounds\" group).")
        self.wave.set_samples(None)
        self.populate_tree()
        self.render_preview()
        self.statusBar().showMessage(msg)

    # ---------------------------------------------------------- selection
    def on_select(self, item, _prev):
        if item is None:
            return
        rel = item.data(0, Qt.UserRole)
        if not rel:
            return
        self.current = rel
        if self.effect:
            self.effect.stop()
        if rel.endswith(".bcwav"):
            self.tabs.setCurrentWidget(self.tab_sound)
            self.load_sound(rel)
        else:
            self.preview_img = None
            self.reset_sliders()
            self.tabs.setCurrentWidget(self.tab_sprite)
            self.refresh_image()

    def on_tab(self, _idx):
        prev = getattr(self, "tab_preview", None)
        if prev is not None and self.tabs.currentWidget() is prev:
            self.render_preview()

    # ================================================================ SPRITES
    def refresh_image(self):
        z = self.zoom.value()
        self.zoom_lbl.setText(f"{z}%")
        if not self.current or self.current.endswith(".bcwav"):
            return
        img = self.preview_img if self.preview_img is not None else self.load_qimage(self.current)
        exp = self.expected_size(self.current)
        label = self.ref[self.current]["label"] if self.current in self.ref else self.current
        if img.isNull():
            self.info.setText(f"<b>{label}</b> — <i>missing from the theme</i>"
                              + (f" (expected size: {exp[0]}×{exp[1]})" if exp else "")
                              + "<br>Use \"Replace the sprite…\" to add it.")
            self.img_label.clear()
            return
        txt = f"<b>{label}</b><br>{self.current} — {img.width()}×{img.height()} px"
        if exp:
            if (img.width(), img.height()) == tuple(exp):
                txt += f" (expected size ✔ {exp[0]}×{exp[1]})"
            else:
                txt += f" — <span style='color:#d62828'>expected size: {exp[0]}×{exp[1]}</span>"
        if self.preview_img is not None:
            txt += "<br><i>Recolor preview not applied</i>"
        self.info.setText(txt)
        w, h = img.width() * z // 100, img.height() * z // 100
        base = checker_pixmap(max(w, 1), max(h, 1))
        p = QPainter(base)
        p.drawImage(0, 0, img.scaled(max(w, 1), max(h, 1), Qt.IgnoreAspectRatio,
                                     Qt.FastTransformation if z >= 100 else Qt.SmoothTransformation))
        p.end()
        self.img_label.setPixmap(base)
        self.img_label.resize(base.size())

    def replace_dialog(self):
        if not self.current or self.current.endswith(".bcwav"):
            QMessageBox.information(self, "Info", "Select a sprite in the list first.")
            return
        p, _ = QFileDialog.getOpenFileName(self, "New image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if p:
            self.replace_sprite(self.current, p, self.chk_resize.isChecked(), self.fit_mode.currentIndex())

    def replace_sprite(self, rel, path, resize=True, mode=0):
        img = QImage(path)
        if img.isNull():
            QMessageBox.critical(self, "Error", "Could not read this image.")
            return False
        exp = self.expected_size(rel)
        if resize and exp:
            img = fit_image(img, exp, mode)
        else:
            img = img.convertToFormat(QImage.Format_ARGB32)
        os.makedirs(os.path.dirname(self.path_of(rel)), exist_ok=True)
        if not img.save(self.path_of(rel), "PNG"):
            QMessageBox.critical(self, "Error", "Failed to save the image.")
            return False
        self.mark_dirty()
        self.preview_img = None
        self.populate_tree(select=rel)
        self.render_preview()
        self.statusBar().showMessage(f"Sprite replaced: {rel}")
        return True

    def export_sprite(self):
        """Exports the current file as is (PNG or BCWAV)."""
        if not self.current or not os.path.isfile(self.path_of(self.current)):
            return
        ext = os.path.splitext(self.current)[1]
        p, _ = QFileDialog.getSaveFileName(self, "Export", os.path.basename(self.current), f"*{ext}")
        if p:
            shutil.copyfile(self.path_of(self.current), p)

    def revert_current(self):
        if not self.current:
            return
        o = os.path.join(self.origdir, self.current)
        if not os.path.isfile(o):
            o = self.reference_file(self.current) or o   # fallback: reference file
        if not os.path.isfile(o):
            QMessageBox.information(self, "Info", "No original for this item.")
            return
        os.makedirs(os.path.dirname(self.path_of(self.current)), exist_ok=True)
        shutil.copyfile(o, self.path_of(self.current))
        self.mark_dirty()
        self.preview_img = None
        self.populate_tree(select=self.current)
        self.render_preview()

    # ---------------------------------------------------------- recolor
    def slider_values(self):
        return self.sl_hue.value(), self.sl_sat.value(), self.sl_bri.value()

    def reset_sliders(self):
        for s in (self.sl_hue, self.sl_sat, self.sl_bri):
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        for lbl, unit in self._slider_labels:
            lbl.setText(f"0{unit}")
        self.preview_img = None
        if self.current and not self.current.endswith(".bcwav"):
            self.refresh_image()

    def update_recolor_preview(self):
        if not self.current or self.current.endswith(".bcwav") or not os.path.isfile(self.path_of(self.current)):
            return
        hue, sat, bri = self.slider_values()
        if (hue, sat, bri) == (0, 0, 0):
            self.preview_img = None
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.preview_img = recolor_image(self.load_qimage(self.current), hue, sat, bri)
            finally:
                QApplication.restoreOverrideCursor()
        self.refresh_image()

    def apply_recolor_current(self):
        if not self.current or self.current.endswith(".bcwav") or not os.path.isfile(self.path_of(self.current)):
            return
        hue, sat, bri = self.slider_values()
        if (hue, sat, bri) == (0, 0, 0):
            return
        img = self.preview_img if self.preview_img is not None else \
            recolor_image(self.load_qimage(self.current), hue, sat, bri)
        img.save(self.path_of(self.current), "PNG")
        self.mark_dirty()
        rel = self.current
        self.populate_tree(select=rel)
        self.render_preview()
        self.statusBar().showMessage(f"Recolor applied: {rel}")

    def apply_recolor_all(self):
        hue, sat, bri = self.slider_values()
        if (hue, sat, bri) == (0, 0, 0):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            count = self.recolor_all(hue, sat, bri, self.chk_text.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        self.mark_dirty()
        self.populate_tree(select=self.current)
        self.render_preview()
        self.statusBar().showMessage(f"Recolor applied to {count} sprite(s)")

    def recolor_all(self, hue, sat, bri, include_text):
        count = 0
        for rel in self.all_sprite_paths():
            if not include_text and "/textSprites/" in rel:
                continue
            recolor_image(self.load_qimage(rel), hue, sat, bri).save(self.path_of(rel), "PNG")
            count += 1
        return count

    # ================================================================ SOUNDS
    def custom_tool(self):
        return self.settings.value("cwavtool_path", "") or None

    def tool(self):
        return find_cwavtool(self.custom_tool())

    def update_tool_label(self):
        t = self.tool()
        if t:
            self.tool_lbl.setText(f"cwavtool: <code>{t}</code>")
            self.tool_lbl.setStyleSheet("color:gray;")
        else:
            self.tool_lbl.setText(
                f"<b>cwavtool not found.</b> Put the \"cwavtool\" folder next to the program "
                f"(expected subfolder: <code>{platform_dir()}</code>).")
            self.tool_lbl.setStyleSheet("color:#d62828;")

    def choose_cwavtool(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose the cwavtool executable")
        if p:
            self.settings.setValue("cwavtool_path", p)
            self.update_tool_label()

    def check_cwavtool(self):
        t = self.tool()
        if not t:
            QMessageBox.warning(self, "cwavtool", f"Not found for this platform ({platform_dir()}).")
            return
        try:
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
            r = subprocess.run([t], capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL, **kw)
            out = (r.stdout + r.stderr).strip()
        except Exception as e:
            QMessageBox.critical(self, "cwavtool", f"{t}\n\nCannot be started: {e}")
            return
        QMessageBox.information(self, "cwavtool", f"{t}\n\nOK, the program starts.\n\n{out[:500]}")

    def load_sound(self, rel):
        self.sound_data = None
        self.wave.set_samples(None)
        p = self.path_of(rel)
        refinfo = self.ref_sound.get(rel)
        if not os.path.isfile(p):
            txt = f"<b>{os.path.basename(rel)}</b> — <i>missing from the theme</i>"
            if refinfo:
                txt += (f"<br>Reference: {ENC_NAMES.get(refinfo['encoding'], '?')}, {refinfo['rate']} Hz, "
                        f"{refinfo['channels']} channel(s)"
                        + (", loop" if refinfo["loop"] else ""))
            txt += "<br>Use \"Revert to original\" to restore it, or replace it with a WAV/OGG."
            self.snd_info.setText(txt)
            return
        size = os.path.getsize(p)
        head = f"<b>{os.path.basename(rel)}</b> — {size / 1024:.1f} KB"
        try:
            with open(p, "rb") as f:
                info, chans = decode_cwav(f.read())
        except CwavError as e:
            try:
                with open(p, "rb") as f:
                    pi = parse_cwav(f.read())
                head += (f"<br>{ENC_NAMES.get(pi['encoding'], '?')}, {pi['rate']} Hz, "
                         f"{len(pi['channels'])} channel(s)")
            except CwavError:
                pass
            self.snd_info.setText(head + f"<br><i>{e}</i>")
            return
        self.sound_data = (info, chans)
        dur = len(chans[0]) / float(info["rate"]) if info["rate"] else 0
        txt = (f"{head}<br>{ENC_NAMES.get(info['encoding'], '?')} • {info['rate']} Hz • "
               f"{len(chans)} channel(s) • {dur:.2f} s")
        if info["loop"]:
            txt += f" • loop {info['loop_start']} → {info['loop_end']}"
        if len(chans) > 2:
            txt += "<br><i>More than 2 channels: playback uses the first two.</i>"
        if refinfo:
            diffs = []
            if refinfo["rate"] != info["rate"]:
                diffs.append(f"sample rate {info['rate']} Hz instead of {refinfo['rate']} Hz")
            if refinfo["channels"] != len(chans):
                diffs.append(f"{len(chans)} channel(s) instead of {refinfo['channels']}")
            if refinfo["loop"] != info["loop"]:
                diffs.append("loop " + ("enabled" if info["loop"] else "missing") + " (reference: "
                             + ("with" if refinfo["loop"] else "without") + ")")
            if diffs:
                txt += ("<br><span style='color:#b36b00'>Differs from the reference: "
                        + ", ".join(diffs) + ".</span>")
            else:
                txt += "<br><span style='color:gray'>Same format as the reference.</span>"
        self.snd_info.setText(txt)
        self.wave.set_samples(chans)

    def play_sound(self):
        if not self.effect or not self.sound_data:
            return
        info, chans = self.sound_data
        out = os.path.join(self.audiodir, "preview.wav")
        write_wav(out, chans[:2], info["rate"])
        self.effect.stop()
        # new URL each time to force a reload
        n = os.path.join(self.audiodir, f"p{id(chans)}_{len(os.listdir(self.audiodir))}.wav")
        shutil.copyfile(out, n)
        self.effect.setSource(QUrl.fromLocalFile(n))
        self.effect.setVolume(0.9)
        self.effect.play()

    def export_sound_wav(self):
        if not self.sound_data:
            QMessageBox.information(self, "Info", "No decoded preview for this sound.")
            return
        info, chans = self.sound_data
        base = os.path.splitext(os.path.basename(self.current))[0] + ".wav"
        p, _ = QFileDialog.getSaveFileName(self, "Export as WAV", base, "WAV (*.wav)")
        if p:
            write_wav(p, chans, info["rate"])

    def sound_options(self):
        enc = self.enc_combo.currentText()
        if self.chk_loop.isChecked():
            return enc, self.loop_start.value(), self.loop_end.value()
        return enc, None, None

    def replace_sound_dialog(self):
        if not self.current or not self.current.endswith(".bcwav"):
            QMessageBox.information(self, "Info", "Select a sound in the list first (\"Sounds\" group).")
            return
        p, _ = QFileDialog.getOpenFileName(self, "New sound", "", "Audio (*.wav *.ogg)")
        if p:
            enc, ls, le = self.sound_options()
            self.replace_sound(self.current, p, enc, ls, le)

    def add_sound_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "New sound", "", "Audio (*.wav *.ogg)")
        if not p:
            return
        default = os.path.splitext(os.path.basename(p))[0]
        name, ok = QInputDialog.getText(
            self, "Sound name",
            "File name (without extension).\nExamples of theme sounds: beepboopclick, lag",
            text=default)
        name = name.strip() if ok else ""
        if not name:
            return
        if not re.fullmatch(r"[A-Za-z0-9_\-. ]+", name) or ".." in name:
            QMessageBox.warning(self, "Invalid name", "Use only letters, digits, _ - . and spaces.")
            return
        rel = f"sound/{name}.bcwav"
        enc, ls, le = self.sound_options()
        self.replace_sound(rel, p, enc, ls, le)

    def replace_sound(self, rel, src, encoding="dspadpcm", loop_start=None, loop_end=None):
        tool = self.tool()
        if not tool:
            self.update_tool_label()
            QMessageBox.critical(
                self, "cwavtool not found",
                f"Cannot convert without cwavtool.\nExpected subfolder: {platform_dir()}\n"
                "Put the \"cwavtool\" folder next to the program.")
            return False
        if os.path.splitext(src)[1].lower() not in (".wav", ".ogg"):
            QMessageBox.warning(self, "Format", "cwavtool only accepts WAV and OGG files.")
            return False
        tmp_out = os.path.join(self.audiodir, "convert_out.bcwav")
        if os.path.isfile(tmp_out):
            os.remove(tmp_out)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, msg = run_cwavtool(tool, src, tmp_out, encoding, loop_start, loop_end)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            QMessageBox.critical(self, "Conversion failed", msg)
            return False
        os.makedirs(os.path.dirname(self.path_of(rel)), exist_ok=True)
        shutil.copyfile(tmp_out, self.path_of(rel))
        self.mark_dirty()
        self.populate_tree(select=rel)
        self.statusBar().showMessage(f"Sound converted ({encoding}): {rel}")
        return True

    # =============================================================== launcher preview
    def build_preview(self):
        mode = self.mode_combo.currentIndex()
        top = QImage(400, 240, QImage.Format_ARGB32)
        bot = QImage(320, 240, QImage.Format_ARGB32)
        top.fill(QColor(25, 25, 25))
        bot.fill(QColor(25, 25, 25))

        def draw(p, rel, x, y):
            im = self.load_qimage(rel)
            if not im.isNull():
                p.drawImage(x, y, im)

        p = QPainter(top)
        draw(p, "sprites/topBackground.png", 0, 0)
        if mode == 0:
            draw(p, "sprites/topInfoBackground.png", 50, 10)
            draw(p, "sprites/textSprites/launchControlsSprite.png", 0, 220)
        else:
            draw(p, "sprites/topInfoBackgroundUpd.png", 50, 10)
            bx, by = 89, 172
            draw(p, "sprites/barBorder.png", bx, by)
            fill = self.load_qimage("sprites/barFill.png")
            if not fill.isNull():
                iw = int((222 - 16) * self.prog.value() / 100)
                p.save()
                p.setClipRect(bx + 8, by + 14, iw, 20)
                for tx in range(bx + 8, bx + 8 + iw, max(fill.width(), 1)):
                    p.drawImage(tx, by + 14, fill.scaled(fill.width(), 20))
                p.restore()
            draw(p, "sprites/textSprites/updateControlsText.png", 0, 220)
        p.end()

        p = QPainter(bot)
        draw(p, "sprites/bottomBackground.png", 0, 0)
        if mode == 0:
            draw(p, "sprites/selTinyButtonBackground.png", 5, 50)
            draw(p, "sprites/textSprites/launchText.png", 5, 50)
            draw(p, "sprites/tinyButtonBackground.png", 5, 120)
            draw(p, "sprites/textSprites/updateText.png", 5, 120)
        else:
            draw(p, "sprites/textSprites/pressBExit.png", 0, 221)
        p.end()
        return top, bot

    def render_preview(self):
        top, bot = self.build_preview()
        self.top_lbl.setPixmap(QPixmap.fromImage(top.scaled(600, 360, Qt.KeepAspectRatio, Qt.FastTransformation)))
        self.bot_lbl.setPixmap(QPixmap.fromImage(bot.scaled(480, 360, Qt.KeepAspectRatio, Qt.FastTransformation)))

    # ------------------------------------------------------------ save / 3DS
    def size_problems(self):
        bad = []
        for rel, info in self.ref.items():
            size = info["size"]
            im = self.load_qimage(rel)
            if im.isNull():
                bad.append(f"{rel}: missing")
            elif (im.width(), im.height()) != size:
                bad.append(f"{rel}: {im.width()}×{im.height()} instead of {size[0]}×{size[1]}")
        return bad

    def confirm_sizes(self, action="Save"):
        bad = self.size_problems()
        if not bad:
            return True
        return QMessageBox.question(
            self, "Unexpected sizes",
            "Some sprites do not have the size expected by the launcher:\n\n"
            + "\n".join(bad[:12]) + f"\n\n{action} anyway?") == QMessageBox.Yes

    def save(self):
        if not self.source:
            return self.save_as()
        self._write_to(self.source)

    def save_as(self):
        d = QFileDialog.getExistingDirectory(self, "Destination folder")
        if d:
            self._write_to(d)

    def _write_to(self, dest):
        if not self.confirm_sizes():
            return
        try:
            shutil.copytree(self.workdir, dest, dirs_exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.source = dest
        self.dirty = False
        self.setWindowTitle("CTGP-7 Launcher – Theme Editor")
        self.statusBar().showMessage(f"Theme saved to {dest}")

    def export_zip(self):
        if not self.confirm_sizes("Export"):
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export as .zip", "theme.zip", "ZIP archives (*.zip)")
        if p:
            self.write_zip(p)
            self.statusBar().showMessage(f"Archive created: {p}")

    def write_zip(self, path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _d, files in os.walk(self.workdir):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, self.workdir))

    def open_ftp(self):
        if not self.confirm_sizes("Send"):
            return
        FtpDialog(self, self.workdir, self.origdir).exec_()

    def closeEvent(self, e):
        if not self.confirm_discard():
            e.ignore()
            return
        if self.effect:
            self.effect.stop()
        for d in (self.workdir, self.origdir, self.audiodir, self.refdir, self.sounddir):
            shutil.rmtree(d, ignore_errors=True)
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = LauncherEditor()
    if "--selftest" in sys.argv:        # build check: no window
        print("APP_DIR          :", APP_DIR)
        print("BUNDLE_DIR       :", BUNDLE_DIR)
        print("reference sprites:", len(w.ref))
        print("reference sounds :", len(w.ref_sound))
        print("cwavtool         :", w.tool())
        w.dirty = False
        w.close()
        sys.exit(0)
    w.show()
    sys.exit(app.exec_())
