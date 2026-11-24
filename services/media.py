# -*- coding: utf-8 -*-
"""ffmpeg helpers. Everything works without ffmpeg; it only makes files nicer and smaller."""
import functools
import logging
import os
import shutil
import subprocess

_logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def ffmpeg_path():
    return shutil.which('ffmpeg')


def has_ffmpeg():
    return bool(ffmpeg_path())


def _run(args, timeout):
    result = subprocess.run([ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-y'] + args,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or b'').decode('utf-8', 'replace').strip()[-400:] or 'ffmpeg failed')


def remux(path):
    """Copy the streams into a fresh file so it has a duration and an index. Returns the new size."""
    if not has_ffmpeg() or not os.path.exists(path):
        return 0
    tmp = path + '.tmp' + os.path.splitext(path)[1]
    try:
        _run(['-i', path, '-c', 'copy', tmp], timeout=1800)
        os.replace(tmp, path)
        return os.path.getsize(path)
    except Exception as error:  # noqa: BLE001 - the original file stays usable
        _logger.warning('isd_meeting: could not repair %s: %s', path, error)
        if os.path.exists(tmp):
            os.remove(tmp)
        return 0


def to_mp4(source, target):
    _run(['-i', source, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26', '-pix_fmt', 'yuv420p',
          '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', target], timeout=6 * 3600)
    return os.path.getsize(target)


def to_speech_mp3(source, target_dir, segment_seconds=None):
    """Mono 16 kHz MP3 at 32 kbit/s (about 14 MB an hour), optionally cut in pieces. Returns the paths."""
    os.makedirs(target_dir, exist_ok=True)
    args = ['-i', source, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '32k']
    if segment_seconds:
        args += ['-f', 'segment', '-segment_time', str(segment_seconds), '-reset_timestamps', '1',
                 os.path.join(target_dir, 'part%03d.mp3')]
    else:
        args += [os.path.join(target_dir, 'part000.mp3')]
    _run(args, timeout=3600)
    return sorted(os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.endswith('.mp3'))
