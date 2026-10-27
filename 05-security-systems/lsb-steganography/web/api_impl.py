"""The domain logic behind the API of the steganography demo.

The cover image is built, the message is embedded in the least significant
bit of the blue channel, and the image is read back: the numbers are the
real ones of the pipeline.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src.pipeline import MESSAGE, embed, extract, make_cover, psnr, to_bits  # noqa: E402

MESSAGE = MESSAGE  # the message of the project


def _run(base="artifacts"):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    cover = make_cover(64)
    stego = embed(cover, MESSAGE)
    recovered = extract(stego)
    Image.fromarray(cover).save(base / "cover.png")
    Image.fromarray(stego).save(base / "stego.png")
    return cover, stego, recovered


def summary():
    cover, stego, recovered = _run()
    return {
        "stats": {
            "capacity_bits": int(cover[:, :, 2].size),
            "message_bits": len(to_bits(MESSAGE)),
            "psnr": round(psnr(cover, stego), 3),
            "decoded_ok": recovered == MESSAGE,
        },
        "series": [
            {"label": "blue channel", "value": int(cover[:, :, 2].size)},
            {"label": "bits of the message", "value": len(to_bits(MESSAGE))},
        ],
        "lists": {
            "channels": [
                {"channel": name, "mean": round(float(stego[:, :, index].mean()), 3),
                 "changed": int(np.count_nonzero(cover[:, :, index] != stego[:, :, index]))}
                for index, name in enumerate(("red", "green", "blue"))
            ]
        },
    }


def handle(method, path, payload, user):
    if method == "GET" and path.endswith("/message"):
        return 200, {"message": MESSAGE, "bits": len(to_bits(MESSAGE))}
    if method == "POST" and path.endswith("/encode"):
        message = str(payload.get("message") or MESSAGE)
        cover = make_cover(64)
        stego = embed(cover, message)
        recovered = extract(stego)
        return 200, {
            "message": message,
            "bits": len(to_bits(message)),
            "psnr": round(psnr(cover, stego), 3),
            "decoded_ok": recovered == message,
        }
    return 404, {"error": "not found"}
