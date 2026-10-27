"""Least-significant-bit steganography over a generated image."""
from pathlib import Path

import numpy as np
from PIL import Image

MESSAGE = "foundev lsb demo: meet at the library at four."


def make_cover(size=48, seed=42):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    red = (128 + 60 * np.sin(xx / 5.0)).astype(np.uint8)
    green = (110 + 50 * np.cos(yy / 6.0)).astype(np.uint8)
    blue = (90 + 40 * np.sin((xx + yy) / 8.0)).astype(np.uint8)
    noise = rng.integers(-4, 5, (size, size, 3))
    return np.clip(np.stack([red, green, blue], axis=2) + noise, 0, 255).astype(np.uint8)


def to_bits(payload):
    data = payload.encode("utf-8")
    header = len(data).to_bytes(4, "big")
    stream = []
    for byte in header + data:
        stream.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
    return stream


def embed(cover, message):
    bits = to_bits(message)
    capacity = cover[:, :, 2].size
    if len(bits) > capacity:
        raise ValueError("message does not fit in the image")
    stego = cover.copy()
    flat = stego[:, :, 2].reshape(-1)
    for index, bit in enumerate(bits):
        flat[index] = (flat[index] & 0xFE) | bit
    return stego


def extract(stego):
    flat = stego[:, :, 2].reshape(-1)
    bits = [int(value) & 1 for value in flat]
    header = bits[:32]
    length = 0
    for bit in header:
        length = (length << 1) | bit
    if length <= 0 or length * 8 + 32 > len(bits):
        return ""
    payload_bits = bits[32:32 + length * 8]
    data = bytearray()
    for index in range(0, len(payload_bits), 8):
        value = 0
        for bit in payload_bits[index:index + 8]:
            value = (value << 1) | bit
        data.append(value)
    return data.decode("utf-8", errors="replace")


def psnr(original, stego):
    difference = original.astype(float) - stego.astype(float)
    mse = float(np.mean(difference ** 2))
    if mse == 0:
        return 99.0
    return round(10 * np.log10((255.0 ** 2) / mse), 2)


def run(quick=False, base_dir="artifacts"):
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    cover = make_cover(40 if quick else 64)
    message = MESSAGE if not quick else "short demo message"
    stego = embed(cover, message)
    recovered = extract(stego)
    Image.fromarray(cover).save(base / "cover.png")
    Image.fromarray(stego).save(base / "stego.png")
    return {
        "capacity_bits": int(cover[:, :, 2].size),
        "message_bits": len(to_bits(message)),
        "psnr": psnr(cover, stego),
        "decoded_ok": recovered == message,
        "cover": str(base / "cover.png"),
        "stego": str(base / "stego.png"),
    }


def demo(base_dir="artifacts"):
    summary = run(quick=True, base_dir=base_dir)
    stego = np.asarray(Image.open(Path(base_dir) / "stego.png"))
    print(f"Encoded {summary['message_bits']} bits into {summary['capacity_bits']} channels.")
    print(f"Decoded correctly: {summary['decoded_ok']}, PSNR: {summary['psnr']} dB")
    print("Recovered:", extract(stego))
