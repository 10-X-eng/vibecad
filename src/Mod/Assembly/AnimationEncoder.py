# SPDX-License-Identifier: LGPL-2.1-or-later
"""Detached animation encoding. No document, Qt, or scene-graph access.

The caller owns the private output artifact and its atomic publication. Only
one input frame is decoded at a time, including for GIFs with local palettes.
This module is also the staged entry point for the existing isolation pool.
"""
from pathlib import Path
import json
import math


def encode_animation(frame_files, output_path, fps, size, *, check=lambda: None):
    destination = Path(output_path)
    suffix = destination.suffix.lower()
    if suffix not in {'.gif', '.mp4', '.avi'}:
        raise ValueError('Animation export requires GIF, MP4 or AVI')
    if not frame_files or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Animation export requires frames and a positive frame rate')
    size = tuple(size)
    if len(size) != 2 or any(type(value) is not int or value <= 0 for value in size):
        raise ValueError('Animation export requires positive integer dimensions')
    check()
    if suffix == '.gif':
        from PIL import Image, GifImagePlugin

        with destination.open('wb') as stream:
            for index, filename in enumerate(frame_files):
                check()
                with Image.open(filename) as source:
                    if source.size != size:
                        raise ValueError('Animation frame dimensions changed during export')
                    # Captures use the opaque viewport background, as the old
                    # saveImage(..., "Current") path did. Each frame gets its own
                    # palette so different poses/colours are not quantized using
                    # only the first frame's colours.
                    with source.convert('RGB') as rgb:
                        with rgb.quantize() as frame:
                            if index == 0:
                                header, _ = GifImagePlugin.getheader(frame, info={'loop': 0})
                                stream.writelines(header)
                            stream.writelines(GifImagePlugin.getdata(
                                frame, duration=int(1000 / fps),
                                include_color_table=True, disposal=2,
                            ))
            stream.write(b';')
    else:
        import cv2

        # Limit OpenCV's own parallel regions inside this admitted worker. The
        # codec implementation is separate from OpenCV's parallel scheduler.
        previous_threads = cv2.getNumThreads()
        cv2.setNumThreads(1)
        writer = None
        try:
            codec = 'mp4v' if suffix == '.mp4' else 'XVID'
            writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*codec), fps, size)
            if not writer.isOpened():
                raise RuntimeError('Could not open the animation video encoder')
            for filename in frame_files:
                check()
                frame = cv2.imread(str(filename))
                if frame is None:
                    raise ValueError('Could not read an animation frame')
                if (frame.shape[1], frame.shape[0]) != size:
                    raise ValueError('Animation frame dimensions changed during export')
                writer.write(frame)
        finally:
            if writer is not None:
                writer.release()
            cv2.setNumThreads(previous_threads)
    check()


if __name__ == '__main__':
    request = json.loads(Path('animation.json').read_text(encoding='utf-8'))
    encode_animation(request['frames'], request['output'], request['fps'], request['size'])
