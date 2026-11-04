"""모델 실루엣을 도면 실루엣(blueprint.json)과 같은 축척·같은 픽셀 격자로 렌더해 겹침 정도(IoU)를 잰다.

    blender -b blender/asurada.blend --factory-startup --python-exit-code 1 -P blender/blueprint_check.py -- \
        blender/blueprint.json OUT_DIR

면마다 OUT_DIR/bp_<면>.png를 남긴다: 회색 = 둘 다, 빨강 = 도면에만, 파랑 = 모델에만, 노랑 = 비교에서 뺀 영역.
도면 실루엣은 윤곽선 안쪽을 모두 채운 것이므로 모델 쪽도 바깥과 이어지지 않은 빈 곳은 채워서 비교한다.
마지막 줄에 BLUEPRINT_IOU {...} 를 출력한다. iou는 IGNORE 영역을 뺀 값, iou_raw는 뺀 영역까지 넣은 값이다.
"""
import json
import math
import os
import struct
import sys
import zlib

import bpy
import numpy as np
from mathutils import Vector


# 모델(GLB) 밖에서 뷰어가 실행 중에 만드는 부품이 차지하는 영역. 도면에는 그려져 있어 그대로 비교하면
# 모델과 무관한 차이로 남는다. 면 이름 → [(축 이름, 최소, 최대), ...] 상자들(|x|는 좌우 대칭으로 본다).
IGNORE = {
    # 앞 서스펜션 암(app.js buildSuspension): 차체 옆(|x| 0.47)과 앞 타이어 안쪽(0.76) 사이, 두 앞바퀴 앞뒤 끝까지
    "top": [(("x", 0.47, 0.76), ("L", 0.40, 1.50))],
}


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def fill_holes(mask):
    """바깥 테두리에서 이어지는 빈 곳만 바깥으로 남기고 나머지 구멍은 채운다(scipy 없이 반복 팽창)."""
    free = ~mask
    out = np.zeros_like(mask)
    out[0], out[-1], out[:, 0], out[:, -1] = free[0], free[-1], free[:, 0], free[:, -1]
    while True:
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        grown &= free
        if (grown == out).all():
            return ~out
        out = grown


def ref_mask(view):
    w, h = view["size"]
    m = np.zeros((h, w), bool)
    for u, runs in enumerate(view["columns"]):
        for a, b in runs:
            m[a:b + 1, u] = True
    return m


def ignore_mask(view_name, view):
    w, h = view["size"]
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    c = model_coords(view["frame"], u, v)
    out = np.zeros((h, w), bool)
    for box in IGNORE.get(view_name, []):
        m = np.ones((h, w), bool)
        for axis, lo, hi in box:
            val = np.abs(c[axis]) if axis == "x" else c[axis]
            m &= (val >= lo) & (val <= hi)
        out |= m
    return out


def model_coords(frame, u, v):
    out = {}
    for axis, p in (("u", u), ("v", v)):
        name, origin, step = frame[axis]
        out[name] = origin + step * p
    return out


def setup_scene():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.film_transparent = True
    sc.render.filter_size = 0.5
    sc.view_settings.view_transform = "Standard"
    sh = sc.display.shading
    sh.light, sh.color_type, sh.single_color = "FLAT", "SINGLE", (0, 0, 0)
    sh.show_shadows = sh.show_cavity = sh.show_object_outline = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    cam = bpy.data.objects.new("bp_cam", bpy.data.cameras.new("bp_cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.type = "ORTHO"
    cam.data.clip_start, cam.data.clip_end = 0.1, 40
    return sc, cam


def render_view(sc, cam, name, view, scale, tmp):
    w, h = view["size"]
    c = model_coords(view["frame"], (w - 1) / 2, (h - 1) / 2)
    if name == "side":
        loc, look = Vector((12, -c["L"], c["z"])), Vector((-1, 0, 0))
    elif name == "top":
        loc, look = Vector((c["x"], -c["L"], 12)), None
    elif name == "front":
        loc, look = Vector((c["x"], -12, c["z"])), Vector((0, 1, 0))
    else:
        loc, look = Vector((c["x"], 12, c["z"])), Vector((0, -1, 0))
    cam.location = loc
    cam.rotation_euler = (0, 0, math.pi) if look is None else look.to_track_quat("-Z", "Y").to_euler()
    cam.data.ortho_scale = max(w, h) * scale
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    path = os.path.join(tmp, f"_bp_{name}.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    os.remove(path)
    return px.reshape(h, w, 4)[::-1, :, 3] > 0.5


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    bp_path, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)
    with open(bp_path, encoding="utf-8") as f:
        bp = json.load(f)
    # 지면·바닥 같은 보조 물체는 빼고 차만 그린다.
    for ob in bpy.data.objects:
        if ob.type == "MESH" and ob.users_collection and ob.users_collection[0].name != "Asurada":
            ob.hide_render = True
    sc, cam = setup_scene()
    result = {}
    for name, view in bp["views"].items():
        ref = ref_mask(view)
        mod = fill_holes(render_view(sc, cam, name, view, bp["scale_m_per_px"], out_dir))
        skip = ignore_mask(name, view)
        keep = ~skip
        inter, union = (ref & mod & keep).sum(), ((ref | mod) & keep).sum()
        result[name] = {"iou": round(float(inter / union), 4),
                        "iou_raw": round(float((ref & mod).sum() / (ref | mod).sum()), 4),
                        "ref_only": int((ref & ~mod & keep).sum()), "model_only": int((mod & ~ref & keep).sum())}
        vis = np.full(ref.shape + (3,), 255, np.uint8)
        vis[ref & ~mod] = (225, 60, 60)
        vis[mod & ~ref] = (60, 90, 225)
        vis[ref & mod] = (165, 165, 165)
        vis[skip & (ref | mod)] = (235, 200, 70)
        write_png(os.path.join(out_dir, f"bp_{name}.png"), vis)
    print("BLUEPRINT_IOU", json.dumps(result))


main()
