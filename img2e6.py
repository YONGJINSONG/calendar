#!/usr/bin/env python3
"""
img2e6.py - 사진을 7.3" Spectra 6 (E6) 패널용 4bpp C 배열 헤더로 변환합니다.

사용법:
    python3 img2e6.py photo.jpg pic1
      -> pic1.h 생성 (const uint8_t pic1[] = {...}, 192000 바이트)

출력 형식:
    - 800x480, 픽셀당 4비트
    - 한 바이트에 두 픽셀. 상위 니블 = 왼쪽(짝수 x), 하위 니블 = 오른쪽
    - E6 raw 코드: 0x0=흰 0x2=초록 0x6=빨강 0xB=노랑 0xD=파랑 0xF=검정
"""

import sys
from PIL import Image

W, H = 800, 480

# (R, G, B, E6 raw code)
E6 = [
    (255, 255, 255, 0x0),
    (0,   255, 0,   0x2),
    (255, 0,   0,   0x6),
    (255, 255, 0,   0xB),
    (0,   0,   255, 0xD),
    (0,   0,   0,   0xF),
]


def fit_cover(img, w, h):
    """비율 유지하며 화면을 꽉 채우고 넘치는 부분은 가운데 기준으로 잘라냅니다."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
    img = img.resize(new, Image.LANCZOS)
    left = (new[0] - w) // 2
    top = (new[1] - h) // 2
    return img.crop((left, top, left + w, top + h))


def convert(src_path, name):
    img = Image.open(src_path).convert("RGB")
    img = fit_cover(img, W, H)

    pal_img = Image.new("P", (1, 1))
    flat = []
    for r, g, b, _ in E6:
        flat += [r, g, b]
    flat += [0] * (768 - len(flat))
    pal_img.putpalette(flat)

    q = img.quantize(palette=pal_img, dither=Image.FLOYDSTEINBERG)
    idx = q.tobytes()  # 픽셀당 1바이트, 값은 E6 리스트의 인덱스 0..5

    lut = bytes(E6[i][3] if i < len(E6) else 0x0 for i in range(256))
    codes = idx.translate(lut)

    out = bytearray(W * H // 2)
    for y in range(H):
        row = codes[y * W:(y + 1) * W]
        base = y * (W // 2)
        for x in range(0, W, 2):
            out[base + (x >> 1)] = (row[x] << 4) | row[x + 1]

    lines = []
    for i in range(0, len(out), 16):
        chunk = ", ".join("0x%02X" % b for b in out[i:i + 16])
        lines.append("  " + chunk + ",")

    header = (
        "#pragma once\n"
        "#include <stdint.h>\n\n"
        "// %s: %dx%d, 4bpp E6, %d bytes\n"
        "const uint8_t %s[] = {\n%s\n};\n"
        % (name, W, H, len(out), name, "\n".join(lines))
    )
    with open(name + ".h", "w") as f:
        f.write(header)
    print("%s.h 생성 완료 (%d 바이트)" % (name, len(out)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
