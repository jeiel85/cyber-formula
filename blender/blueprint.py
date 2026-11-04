"""도장 설명서 도면(측면·평면·정면·후면)에서 실루엣과 기준 치수를 뽑아 blueprint.json으로 저장한다.

도면 이미지는 저작물이라 저장소에 넣지 않는다(blueprint/ref/는 .gitignore). 저장소에는 이 스크립트가 뽑은
숫자(축척, 바퀴 위치, 면별 실루엣 런 길이)만 들어간다. 모델 쪽 대조는 blueprint_check.py가 한다.

    python -X utf8 blender/blueprint.py blueprint/ref/sheet_2x.png blender/blueprint.json [--grid OUT_DIR]

좌표: 모델과 같다. L은 차 앞쪽(+), x는 차의 왼쪽(+), z는 지면에서 위(+), 단위 m.
도면 네 면은 같은 축척의 정투영이다(측면 길이 888px ≈ 평면 길이 891px, 평면·정면·후면 폭 578px).
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

# 2배 캡처(1472×2078) 기준 면별 잘라낼 영역 (x0, y0, x1, y1)
CROPS = {
    "side": (60, 80, 960, 385),
    "top": (50, 400, 670, 1330),
    "front": (50, 1355, 670, 1690),
    "rear": (50, 1695, 670, 2035),
}
# 축척: 뒷바퀴 지름 134px가 0.75m가 되도록 잡은 값(전장 약 4.97m).
SCALE = 0.0056
# 기준점(도면 픽셀, 크롭 좌표): 측면 앞바퀴 1번 중심 x, 측면·정면·후면 지면 y, 평면 앞바퀴 1번 중심 y와 중심선 x
L_F1 = 1.22
REF = {
    "side": {"x_f1": 240.5, "ground": 301.5},
    "top": {"y_f1": 254.5, "center": 304.5},
    "front": {"center": 305.5, "ground": 302.0},
    "rear": {"center": 306.0, "ground": 335.5},
}


def ink(rgb):
    """흰 바탕에서 절반 이상 어두워진 픽셀(선·도색). 안티에일리어싱 가장자리는 50%에서 자른다."""
    return np.sqrt(((255 - rgb.astype(float)) ** 2).sum(axis=2)) > 90


def silhouette(rgb):
    """바깥 테두리와 이어진 바탕만 빼고 나머지를 실루엣으로 본다(흰 도색 면은 윤곽선이 감싸므로 채워진다).
    면 이름 라벨 같은 떨어진 조각은 가장 큰 덩어리만 남겨 지운다."""
    bg_like = ~ink(rgb)
    lab, _ = ndi.label(bg_like)
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    sil = ~np.isin(lab, list(border))
    lab, n = ndi.label(sil)
    sizes = ndi.sum(sil, lab, range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def rle_columns(mask):
    """열마다 채워진 구간 [시작, 끝] 목록(끝 포함)."""
    out = []
    for col in mask.T:
        d = np.diff(np.r_[0, col.astype(np.int8), 0])
        s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1
        out.append([[int(a), int(b)] for a, b in zip(s, e)])
    return out


def frames():
    """도면 픽셀(크롭 좌표, 픽셀 중심) → 모델 좌표 변환. 각 축은 value = origin + step * pixel."""
    s = SCALE
    side, top, front, rear = REF["side"], REF["top"], REF["front"], REF["rear"]
    return {
        # 측면: 오른쪽이 차 뒤, 아래가 지면
        "side": {"u": ["L", L_F1 + side["x_f1"] * s, -s], "v": ["z", side["ground"] * s, -s]},
        # 평면: 위가 차 앞, 오른쪽이 차의 오른쪽(-x)
        "top": {"u": ["x", top["center"] * s, -s], "v": ["L", L_F1 + top["y_f1"] * s, -s]},
        # 정면: 차를 마주 보므로 화면 오른쪽이 차의 왼쪽(+x)
        "front": {"u": ["x", -front["center"] * s, s], "v": ["z", front["ground"] * s, -s]},
        # 후면: 화면 오른쪽이 차의 오른쪽(-x)
        "rear": {"u": ["x", rear["center"] * s, -s], "v": ["z", rear["ground"] * s, -s]},
    }


def to_model(frame, u, v):
    au, ou, su = frame["u"]
    av, ov, sv = frame["v"]
    return {au: ou + su * u, av: ov + sv * v}


def measure(sheet):
    rgb = np.asarray(Image.open(sheet).convert("RGB"))
    fr = frames()
    views = {}
    for name, (x0, y0, x1, y1) in CROPS.items():
        sil = silhouette(rgb[y0:y1, x0:x1])
        ys, xs = np.nonzero(sil)
        views[name] = {
            "size": [int(x1 - x0), int(y1 - y0)],
            "frame": fr[name],
            "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            "columns": rle_columns(sil),
        }
    s = SCALE
    side, top = REF["side"], REF["top"]
    # 바퀴: 측면 원(중심·지름)과 평면 사각형(안·바깥 x)에서 읽은 값. 앞바퀴 두 쌍은 크기가 같다.
    wheels = {
        "front": {
            "L": [L_F1, round(L_F1 - (340.5 - side["x_f1"]) * s, 4)],
            "radius": round(42 * s, 4),
            # 평면도 오른쪽 앞 타이어 x 436~509px (중심선 304.5)
            "x_inner": round((436 - top["center"]) * s, 4),
            "x_outer": round((509 - top["center"]) * s, 4),
        },
        "rear": {
            "L": round(L_F1 - (736.5 - side["x_f1"]) * s, 4),
            "radius": round(67 * s, 4),
            # 후면도 오른쪽 뒤 타이어 x 433~525px (중심선 306)
            "x_inner": round((433 - REF["rear"]["center"]) * s, 4),
            "x_outer": round((525 - REF["rear"]["center"]) * s, 4),
        },
    }
    return {
        "source": "HobbySearch SUPER ASURADA SA01/C painting instruction (four orthographic views)",
        "scale_m_per_px": s,
        "note": "columns[i] = filled pixel runs [v0, v1] of image column u=i; map with frame.u/v (value = origin + step * px).",
        "wheels": wheels,
        "views": views,
    }


def grid(sheet, out_dir):
    """눈으로 치수를 읽기 위한 격자 겹친 도면(로컬 확인용, 저장소에 넣지 않는다). 0.1m 가는 선, 0.5m 굵은 선."""
    os.makedirs(out_dir, exist_ok=True)
    im = Image.open(sheet).convert("RGB")
    fr = frames()
    for name, (x0, y0, x1, y1) in CROPS.items():
        crop = im.crop((x0, y0, x1, y1))
        k = 3 if name == "side" else 2
        crop = crop.resize((crop.width * k, crop.height * k), Image.LANCZOS)
        d = ImageDraw.Draw(crop)
        f = fr[name]
        for axis, horizontal in (("u", False), ("v", True)):
            _, o, st = f[axis]
            n = crop.height if horizontal else crop.width
            lo, hi = sorted((o, o + st * n / k))
            for i in range(int(np.floor(lo * 10)), int(np.ceil(hi * 10)) + 1):
                val = i / 10
                p = (val - o) / st * k
                major = i % 5 == 0
                col = (255, 0, 200) if major else (0, 200, 220)
                if horizontal:
                    d.line([(0, p), (crop.width, p)], fill=col, width=1)
                    if major:
                        d.text((2, p + 1), f"{val:+.1f}", fill=col)
                else:
                    d.line([(p, 0), (p, crop.height)], fill=col, width=1)
                    if major:
                        d.text((p + 2, 2), f"{val:+.1f}", fill=col)
        crop.save(f"{out_dir}/grid_{name}.png")


if __name__ == "__main__":
    sheet, out = sys.argv[1], sys.argv[2]
    data = measure(sheet)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    if "--grid" in sys.argv:
        grid(sheet, sys.argv[sys.argv.index("--grid") + 1])
    print(json.dumps({k: v["bbox_px"] for k, v in data["views"].items()}), json.dumps(data["wheels"]))
