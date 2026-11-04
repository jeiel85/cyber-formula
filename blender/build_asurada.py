"""Super Asurada 01 팬메이드 모델 생성기 (Blender 5.2, headless).

차체를 단면 로프트·익형 압출·회전체로 절차적으로 만들고, 웹 뷰어가 움직일 파츠
(캐노피, 플랩, 리어 윙, 에어로 핀, 에어 브레이크, 부스트 포드, 바퀴)는 피벗 Empty 아래에 둔다.
각 오브젝트의 커스텀 속성 "part"는 glTF extras로 나가 뷰어의 파츠 설명과 연결된다.

좌표: 미터, Blender Z-up, 차 앞쪽 = -Y. glTF/three.js에서는 앞쪽이 +Z가 된다.
스크립트 안의 (x, L, z)는 "차 좌표"로 L은 앞쪽(+)으로 잰 길이 방향 위치다.

실행:
  blender -b --factory-startup --python-exit-code 1 -P build_asurada.py -- \
      --glb blender/asurada.glb --web web/asurada.gltf.json --blend blender/asurada.blend [--preview preview.png]

--web은 GLB의 바이너리 버퍼를 base64로 내장한 glTF JSON이다. claude.ai 아티팩트가 .glb를 서빙하지 않아
뷰어는 이 파일을 읽는다(three.js GLTFLoader는 두 형식을 모두 읽는다).
"""
import bisect
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

SKILLS = os.path.expanduser("~/.claude/skills")
sys.path.append(os.path.join(SKILLS, "scenario-blender-expert", "scripts"))
sys.path.append(os.path.join(SKILLS, "scenario-blender-export", "scripts"))


# ---------------------------------------------------------------- 인자

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"glb": None, "web": None, "blend": None, "preview": None}
    it = iter(argv)
    for a in it:
        key = a.lstrip("-")
        if key not in opts:
            raise SystemExit(f"알 수 없는 인자: {a}")
        opts[key] = os.path.abspath(next(it))
    return opts


# ---------------------------------------------------------------- 재질

def srgb(hexcode):
    h = hexcode.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


# 뷰어가 이름으로 재질을 다시 입히므로 여기 값은 Blender 미리보기와 기본값 역할이다.
MATERIALS = {
    "paint_white": dict(color="#eef1f5", rough=0.3),
    "paint_blue": dict(color="#1c3fa8", rough=0.3),
    "paint_red": dict(color="#d3202c", rough=0.3),
    "paint_yellow": dict(color="#f1bb2b", rough=0.35),
    "carbon": dict(color="#17191d", rough=0.45),
    "tire": dict(color="#151515", rough=0.85),
    "rim": dict(color="#c9ced6", rough=0.25, metal=1.0),
    "metal_dark": dict(color="#3b3f45", rough=0.35, metal=1.0),
    "glass": dict(color="#22303f", rough=0.05, alpha=0.35),
    "glow_boost": dict(color="#9fd8ff", emit=6.0),
    "ai_screen": dict(color="#44e0ff", emit=3.0),
    "lamp_red": dict(color="#ff2a2a", emit=4.0),
    "seat": dict(color="#23262d", rough=0.8),
    "intake": dict(color="#06070a", rough=0.6),
}


def mat(name):
    m = bpy.data.materials.get(name)
    if m:
        return m
    spec = MATERIALS[name]
    col = srgb(spec["color"])
    m = bpy.data.materials.new(name)
    m.diffuse_color = col
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = col
    bsdf.inputs["Roughness"].default_value = spec.get("rough", 0.5)
    bsdf.inputs["Metallic"].default_value = spec.get("metal", 0.0)
    if "emit" in spec:
        bsdf.inputs["Emission Color"].default_value = col
        bsdf.inputs["Emission Strength"].default_value = spec["emit"]
    if "alpha" in spec:
        bsdf.inputs["Alpha"].default_value = spec["alpha"]
        m.surface_render_method = "BLENDED"
        m.use_backface_culling = False
        m.diffuse_color = col[:3] + (spec["alpha"],)
    return m


# ---------------------------------------------------------------- 장면 / 계층

COL = None
NODES = {}  # 이름 -> (오브젝트, 월드 위치)


def P(x, L, z):
    """차 좌표 → Blender 좌표 (앞쪽 = -Y)."""
    return Vector((x, -L, z))


def empty(name, x, L, z, parent=None, part=None):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_size = 0.08
    COL.objects.link(ob)
    wpos = P(x, L, z)
    ppos = Vector()
    if parent:
        ob.parent = NODES[parent][0]
        ppos = NODES[parent][1]
    ob.location = wpos - ppos
    if part:
        ob["part"] = part
    NODES[name] = (ob, wpos)
    return ob


def finish(me, face_mat=None, origin=Vector(), smooth=True, sharp=35.0, recalc=True):
    bm = bmesh.new()
    bm.from_mesh(me)
    if recalc:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if face_mat:
        for f in bm.faces:
            c = f.calc_center_median() + origin
            f.material_index = face_mat(c.x, -c.y, c.z)
    for f in bm.faces:
        f.smooth = smooth
    if smooth and sharp:
        lim = math.radians(sharp)
        for e in bm.edges:
            e.smooth = not (e.is_manifold and e.calc_face_angle(0.0) > lim)
    bm.to_mesh(me)
    bm.free()


def add_mesh(name, geo, mats, parent="chassis", part=None, face_mat=None,
             smooth=True, sharp=35.0, recalc=True, mesh_data=None):
    """geo = (월드 좌표 정점, 면). 정점은 부모 피벗 기준 로컬 좌표로 옮겨 저장한다."""
    origin = NODES[parent][1] if parent else Vector()
    if mesh_data is None:
        verts, faces = geo
        me = bpy.data.meshes.new(name)
        me.from_pydata([Vector(v) - origin for v in verts], [], [tuple(f) for f in faces])
        me.validate()
        for mn in mats:
            me.materials.append(mat(mn))
        finish(me, face_mat, origin, smooth, sharp, recalc)
    else:
        me = mesh_data
    ob = bpy.data.objects.new(name, me)
    COL.objects.link(ob)
    if parent:
        ob.parent = NODES[parent][0]
    if part:
        ob["part"] = part
    return ob


# ---------------------------------------------------------------- 형상 도구

def merge(*geos):
    verts, faces = [], []
    for v, f in geos:
        off = len(verts)
        verts += list(v)
        faces += [[i + off for i in face] for face in f]
    return verts, faces


class Pchip:
    """단조 3차 보간(오버슈트 없음). 단면 표를 매끄럽게 잇는 데 쓴다."""

    def __init__(self, xs, ys):
        n = len(xs)
        h = [xs[i + 1] - xs[i] for i in range(n - 1)]
        d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
        m = [0.0] * n
        m[0], m[-1] = d[0], d[-1]
        for i in range(1, n - 1):
            if d[i - 1] * d[i] <= 0:
                m[i] = 0.0
            else:
                w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
        self.xs, self.ys, self.h, self.m = xs, ys, h, m

    def __call__(self, x):
        i = min(max(bisect.bisect_right(self.xs, x) - 1, 0), len(self.xs) - 2)
        h = self.h[i]
        t = (x - self.xs[i]) / h
        return ((2 * t ** 3 - 3 * t ** 2 + 1) * self.ys[i] + (t ** 3 - 2 * t ** 2 + t) * h * self.m[i]
                + (-2 * t ** 3 + 3 * t ** 2) * self.ys[i + 1] + (t ** 3 - t ** 2) * h * self.m[i + 1])


class Profile:
    """(L, 파라미터...) 행의 표. at(L)이 보간된 파라미터를 돌려준다."""

    def __init__(self, rows):
        rows = sorted(rows, key=lambda r: r[0])
        xs = [r[0] for r in rows]
        self.L0, self.L1 = xs[0], xs[-1]
        self.f = [Pchip(xs, [r[k] for r in rows]) for k in range(1, len(rows[0]))]

    def at(self, L):
        return [f(L) for f in self.f]

    def samples(self, count):
        """앞(L1)에서 뒤(L0)로, 양 끝이 촘촘한 코사인 간격."""
        span = self.L1 - self.L0
        return [self.L1 - span * (1 - math.cos(math.pi * i / (count - 1))) / 2 for i in range(count)]


def se(t, a, bt, bb, n):
    """슈퍼타원 위의 점. 위/아래 반높이를 따로 둔다."""
    c, s = math.cos(t), math.sin(t)
    x = a * math.copysign(abs(c) ** (2.0 / n), c)
    z = (bt if s >= 0 else bb) * math.copysign(abs(s) ** (2.0 / n), s)
    return x, z


def ring(L, a, zc, bt, bb, n, N=48, x0=0.0):
    return [P(x0 + x, L, zc + z) for x, z in (se(2 * math.pi * i / N, a, bt, bb, n) for i in range(N))]


def loft(rings, cyclic=True, caps=True):
    N = len(rings[0])
    verts = [v for r in rings for v in r]
    faces = []
    span = N if cyclic else N - 1
    for i in range(len(rings) - 1):
        for j in range(span):
            j2 = (j + 1) % N
            faces.append((i * N + j, i * N + j2, (i + 1) * N + j2, (i + 1) * N + j))
    if caps and cyclic:
        for k, r in ((0, rings[0]), (len(rings) - 1, rings[-1])):
            c = sum(r, Vector()) / N
            ci = len(verts)
            verts.append(c)
            for j in range(N):
                faces.append((ci, k * N + (j + 1) % N, k * N + j))
    return verts, faces


def loft_profile(prof, count, N=48, x0=0.0):
    return loft([ring(L, *prof.at(L), N=N, x0=x0) for L in prof.samples(count)])


def box(center, size, bevel=0.0, segments=3, tilt=0.0):
    """둥근 상자. center/size는 차 좌표 (x, L, z), tilt는 X축 회전(도, +면 윗부분이 뒤로 눕는다)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bevel, segments=segments,
                        affect="EDGES", profile=0.5)
    rot = Matrix.Rotation(math.radians(-tilt), 4, "X")
    c = P(*center)
    bm.verts.index_update()
    verts = [rot @ v.co + c for v in bm.verts]
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    return verts, faces


def airfoil(nu=14, t=0.12, camber=-0.04):
    us = [0.5 * (1 - math.cos(math.pi * i / nu)) for i in range(nu + 1)]

    def yt(u):
        return 5 * t * (0.2969 * math.sqrt(u) - 0.1260 * u - 0.3516 * u ** 2 + 0.2843 * u ** 3 - 0.1036 * u ** 4)

    def yc(u):
        return camber * 4 * u * (1 - u)

    upper = [(u, yc(u) + yt(u)) for u in reversed(us)]
    lower = [(u, yc(u) - yt(u)) for u in us[1:-1]]
    return upper + lower


def wing(x0, x1, chord, L_le, z_le, aoa, t=0.12, camber=-0.04):
    """앞전 (L_le, z_le)에서 뒤로 뻗는 익형 날개. aoa>0이면 뒷전이 올라간다(다운포스 방향)."""
    prof = airfoil(t=t, camber=camber)
    a = math.radians(aoa)
    dL, dz = -math.cos(a), math.sin(a)
    nL, nz = math.sin(a), math.cos(a)
    rings = [[P(x, L_le + (u * dL + v * nL) * chord, z_le + (u * dz + v * nz) * chord) for u, v in prof]
             for x in (x0, x1)]
    return loft(rings)


def plate(poly_Lz, xc, thick, bevel=0.005):
    """옆면 윤곽(L, z) 다각형을 두께만큼 X로 돌출한 판."""
    bm = bmesh.new()
    n = len(poly_Lz)
    rows = [[bm.verts.new(P(x, L, z)) for L, z in poly_Lz] for x in (xc - thick / 2, xc + thick / 2)]
    bm.faces.new(rows[0])
    bm.faces.new(list(reversed(rows[1])))
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((rows[0][i], rows[0][j], rows[1][j], rows[1][i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if bevel:
        bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bevel, segments=2,
                        affect="EDGES", profile=0.5, clamp_overlap=True)
    bm.verts.index_update()
    verts = [v.co.copy() for v in bm.verts]
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    return verts, faces


def revolve(profile, axis_point, axis, seg=48):
    """닫힌 (축 방향 위치, 반지름) 윤곽을 축 둘레로 돌린 회전체. axis는 'X'(바퀴) 또는 'L'(노즐)."""
    n = len(profile)
    verts, faces = [], []
    for k in range(seg):
        ph = 2 * math.pi * k / seg
        c, s = math.cos(ph), math.sin(ph)
        for a, r in profile:
            if axis == "X":
                verts.append(Vector(axis_point) + Vector((a, r * c, r * s)))
            else:  # 차의 L축(=Blender -Y) 둘레
                x0, z0 = axis_point
                verts.append(P(x0 + r * c, a, z0 + r * s))
    for k in range(seg):
        k2 = (k + 1) % seg
        for i in range(n):
            i2 = (i + 1) % n
            faces.append((k * n + i, k * n + i2, k2 * n + i2, k2 * n + i))
    return verts, faces


def torus(center, R, r, tilt, seg=40, tseg=10):
    """L축을 바라보는 고리(스티어링 휠). tilt는 X축 기울기(도)."""
    rot = Matrix.Rotation(math.radians(-tilt), 3, "X")
    c0 = P(*center)
    verts, faces = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        radial = Vector((math.cos(a), 0, math.sin(a)))
        for j in range(tseg):
            b = 2 * math.pi * j / tseg
            local = radial * (R + r * math.cos(b)) + Vector((0, 1, 0)) * (r * math.sin(b))
            verts.append(rot @ local + c0)
    for i in range(seg):
        i2 = (i + 1) % seg
        for j in range(tseg):
            j2 = (j + 1) % tseg
            faces.append((i * tseg + j, i * tseg + j2, i2 * tseg + j2, i2 * tseg + j))
    return verts, faces


def curve_tube(pts, radius, closed=True):
    cu = bpy.data.curves.new("tube_tmp", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, co in zip(sp.points, pts):
        p.co = (co.x, co.y, co.z, 1.0)
    sp.use_cyclic_u = closed
    tmp = bpy.data.objects.new("tube_tmp", cu)
    COL.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    verts = [v.co.copy() for v in me.vertices]
    faces = [list(p.vertices) for p in me.polygons]
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(cu)
    bpy.data.meshes.remove(me)
    return verts, faces


# ---------------------------------------------------------------- 차체 치수표

# 메인 모노코크: (L, 반폭 a, 중심높이 zc, 윗반높이 bt, 아랫반높이 bb, 슈퍼타원 지수 n)
BODY = Profile([
    (2.62, 0.015, 0.270, 0.015, 0.015, 2.0),
    (2.50, 0.085, 0.272, 0.055, 0.050, 2.3),
    (2.25, 0.150, 0.280, 0.095, 0.080, 2.5),
    (1.90, 0.200, 0.300, 0.125, 0.100, 2.7),
    (1.50, 0.250, 0.320, 0.150, 0.120, 2.9),
    (1.05, 0.300, 0.340, 0.180, 0.140, 3.0),
    (0.65, 0.380, 0.350, 0.205, 0.160, 3.2),
    (0.20, 0.440, 0.360, 0.215, 0.170, 3.4),
    (-0.30, 0.460, 0.360, 0.230, 0.170, 3.4),
    (-0.80, 0.430, 0.360, 0.250, 0.170, 3.3),
    (-1.30, 0.360, 0.350, 0.220, 0.160, 3.2),
    (-1.75, 0.270, 0.340, 0.170, 0.140, 3.0),
    (-2.05, 0.190, 0.330, 0.120, 0.110, 2.8),
    (-2.22, 0.100, 0.330, 0.060, 0.060, 2.5),
    (-2.26, 0.030, 0.330, 0.020, 0.020, 2.2),
])

SIDEPOD = Profile([
    (0.52, 0.160, 0.260, 0.110, 0.120, 3.2),
    (0.40, 0.190, 0.260, 0.140, 0.150, 3.4),
    (0.00, 0.200, 0.260, 0.150, 0.160, 3.4),
    (-0.50, 0.190, 0.250, 0.140, 0.150, 3.2),
    (-0.95, 0.130, 0.230, 0.100, 0.120, 3.0),
    (-1.18, 0.050, 0.210, 0.040, 0.050, 2.6),
])
SIDEPOD_X = 0.58

AIRBOX = Profile([
    (-0.60, 0.100, 0.660, 0.110, 0.060, 2.6),
    (-0.75, 0.130, 0.660, 0.130, 0.080, 2.8),
    (-1.10, 0.120, 0.620, 0.100, 0.080, 2.8),
    (-1.45, 0.060, 0.560, 0.050, 0.050, 2.5),
])

# 부스트 포드: (L, 반지름) — 원형 단면
POD = Profile([
    (-0.48, 0.020), (-0.52, 0.080), (-0.60, 0.130), (-0.72, 0.160), (-0.90, 0.170),
    (-1.60, 0.170), (-1.80, 0.165), (-1.90, 0.158),
])
POD_X, POD_Z = 0.36, 0.68

CANOPY = Profile([
    (0.80, 0.030, 0.010), (0.66, 0.170, 0.100), (0.50, 0.260, 0.180), (0.30, 0.310, 0.250),
    (0.05, 0.330, 0.280), (-0.20, 0.320, 0.270), (-0.40, 0.270, 0.200), (-0.52, 0.170, 0.100),
    (-0.58, 0.040, 0.020),
])

FLOOR = Profile([
    (1.05, 0.22), (0.80, 0.48), (0.55, 0.62), (-1.05, 0.62), (-1.15, 0.55), (-1.95, 0.52), (-2.12, 0.40),
])

# 바퀴: 이름, L, |x|, 반지름 R, 폭 W, 파츠
WHEELS = [
    ("F1", 1.86, 0.63, 0.27, 0.24, "front_wheels"),
    ("F2", 1.20, 0.67, 0.27, 0.24, "front_wheels"),
    ("R", -1.50, 0.83, 0.36, 0.40, "rear_wheels"),
]


def body_top(L, x):
    a, zc, bt, _bb, n = BODY.at(L)
    u = min(abs(x) / a, 1.0)
    return zc + bt * (1 - u ** n) ** (1 / n)


# ---------------------------------------------------------------- 파츠

def build_body():
    WHITE, BLUE, RED, CARBON = 0, 1, 2, 3

    def body_mat(x, L, z):
        if z < 0.235:
            return CARBON
        if L > 1.32:
            return BLUE
        if L > 1.24:
            return RED
        if L < -0.70 and abs(x) < 0.09:
            return BLUE
        return WHITE

    body = add_mesh("body", loft_profile(BODY, 96, N=56), ["paint_white", "paint_blue", "paint_red", "carbon"],
                    part="body", face_mat=body_mat)

    # 콕핏 포켓: 둥근 상자를 빼서 시트가 들어갈 욕조 모양을 만든다.
    cutter = add_mesh("pocket_cutter", box((0, 0.055, 0.68), (0.48, 0.80, 0.76), bevel=0.09, segments=4),
                      [], parent=None, smooth=False)
    mod = body.modifiers.new("pocket", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.solver = "EXACT"
    mod.object = cutter
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(body.evaluated_get(dg))
    body.modifiers.remove(mod)
    old = body.data
    body.data = me
    bpy.data.meshes.remove(old)
    bpy.data.objects.remove(cutter)
    if len(me.materials) == 0:
        for mn in ["paint_white", "paint_blue", "paint_red", "carbon"]:
            me.materials.append(mat(mn))

    def body_mat_cut(x, L, z):
        if abs(x) < 0.25 and -0.36 < L < 0.47 and z < 0.545:
            return CARBON  # 포켓 안쪽
        return body_mat(x, L, z)

    finish(me, body_mat_cut, sharp=40.0)

    # 에어박스(드라이버 뒤 흡기)
    add_mesh("airbox", loft_profile(AIRBOX, 28, N=40), ["paint_white", "paint_blue"], part="engine",
             face_mat=lambda x, L, z: 1 if z > 0.74 or (L < -1.0 and abs(x) < 0.05) else 0)
    add_mesh("airbox_intake", loft([ring(-0.596, 0.075, 0.68, 0.075, 0.06, 2.4, N=32),
                                    ring(-0.592, 0.070, 0.68, 0.070, 0.055, 2.4, N=32)]),
             ["intake"], part="engine")

    # 사이드 포드 + 흡기구
    for side, s in (("L", 1), ("R", -1)):
        x0 = s * SIDEPOD_X
        add_mesh(f"sidepod_{side}", loft_profile(SIDEPOD, 40, N=48, x0=x0), ["paint_white", "paint_blue", "paint_red"],
                 part="sidepods",
                 face_mat=lambda x, L, z: 2 if L > 0.47 and z > 0.14 else (1 if z < 0.19 else 0))
        add_mesh(f"sidepod_intake_{side}", loft([ring(0.524, 0.135, 0.265, 0.085, 0.095, 3.2, N=40, x0=x0),
                                                 ring(0.528, 0.125, 0.265, 0.078, 0.088, 3.2, N=40, x0=x0)]),
                 ["intake"], part="sidepods")

    # 플로어와 디퓨저
    floor_rows = [(L, a, 0.07, 0.012, 0.012, 8.0) for L, a in [(r, FLOOR.at(r)[0]) for r in FLOOR.samples(24)]]
    add_mesh("floor", loft([ring(*row, N=40) for row in floor_rows]), ["carbon"], part="underbody", sharp=50)
    strakes = merge(*[plate([(-1.55, 0.08), (-2.10, 0.08), (-2.16, 0.27), (-1.85, 0.21)], x, 0.014)
                      for x in (-0.30, -0.12, 0.12, 0.30)])
    add_mesh("diffuser", strakes, ["carbon"], part="underbody", sharp=50)
    add_mesh("rain_light", box((0, -2.255, 0.305), (0.10, 0.02, 0.045), bevel=0.006), ["lamp_red"],
             part="underbody", sharp=50)

    # 미러
    for side, s in (("L", 1), ("R", -1)):
        g = merge(box((s * 0.47, 0.40, 0.61), (0.15, 0.07, 0.07), bevel=0.028, segments=4),
                  box((s * 0.415, 0.41, 0.53), (0.025, 0.04, 0.15), bevel=0.008))
        add_mesh(f"mirror_{side}", g, ["paint_blue"], part="cockpit", sharp=50)


def build_cockpit():
    # 캐노피: 앞쪽 힌지, 뒤쪽이 들린다. 밑단은 차체 윗면 곡선에 맞춘다.
    Lh = 0.80
    empty("piv_canopy", 0, Lh, body_top(Lh, 0), parent="chassis", part="canopy")
    nc, M = 2.3, 25
    rings, rim_l, rim_r = [], [], []
    for L in CANOPY.samples(30):
        a, h = CANOPY.at(L)
        r = []
        for j in range(M):
            th = math.pi * j / (M - 1)
            c, s = math.cos(th), math.sin(th)
            x = a * math.copysign(abs(c) ** (2 / nc), c)
            z = body_top(L, x) - 0.004 + h * abs(s) ** (2 / nc)
            r.append(P(x, L, z))
        rings.append(r)
        rim_l.append(P(a, L, body_top(L, a) + 0.004))
        rim_r.append(P(-a, L, body_top(L, -a) + 0.004))
    add_mesh("canopy_glass", loft(rings, cyclic=False), ["glass"], parent="piv_canopy", part="canopy",
             recalc=False, sharp=None)
    add_mesh("canopy_frame", curve_tube(rim_l + list(reversed(rim_r)), 0.013), ["carbon"],
             parent="piv_canopy", part="canopy", sharp=None)

    # 실내
    seat = merge(box((0, -0.16, 0.335), (0.36, 0.34, 0.07), bevel=0.03),
                 box((0, -0.33, 0.47), (0.36, 0.07, 0.30), bevel=0.03, tilt=18))
    add_mesh("seat", seat, ["seat"], part="cockpit", sharp=50)
    add_mesh("steering", merge(torus((0, 0.20, 0.475), 0.085, 0.013, tilt=-15),
                               box((0, 0.265, 0.455), (0.03, 0.13, 0.03), tilt=-15)),
             ["carbon"], part="cockpit", sharp=None)
    add_mesh("dash", box((0, 0.40, 0.46), (0.40, 0.10, 0.12), bevel=0.02), ["carbon"], part="cockpit", sharp=50)
    add_mesh("ai_screen", box((0, 0.345, 0.475), (0.22, 0.008, 0.07), tilt=-15), ["ai_screen"],
             part="cyber_system", sharp=50)


def build_front_wing():
    g = merge(
        wing(-0.80, 0.80, 0.30, 2.58, 0.085, 6, t=0.11),
        plate([(2.62, 0.05), (2.62, 0.20), (2.40, 0.26), (2.20, 0.24), (2.18, 0.05)], 0.80, 0.02),
        plate([(2.62, 0.05), (2.62, 0.20), (2.40, 0.26), (2.20, 0.24), (2.18, 0.05)], -0.80, 0.02),
        plate([(2.50, 0.10), (2.47, 0.24), (2.33, 0.24), (2.34, 0.10)], 0.07, 0.014),
        plate([(2.50, 0.10), (2.47, 0.24), (2.33, 0.24), (2.34, 0.10)], -0.07, 0.014),
    )
    add_mesh("front_wing", g, ["paint_blue"], part="front_wing", sharp=40)
    for side, s in (("L", 1), ("R", -1)):
        empty(f"piv_front_flap_{side}", s * 0.47, 2.31, 0.135, parent="chassis", part="front_wing")
        add_mesh(f"front_flap_{side}", wing(s * 0.16, s * 0.78, 0.13, 2.31, 0.135, 22, t=0.10),
                 ["paint_red"], parent=f"piv_front_flap_{side}", part="front_wing", sharp=40)


def build_rear_wing():
    end_poly = [(-1.90, 0.40), (-1.98, 0.98), (-2.10, 1.10), (-2.46, 1.12), (-2.46, 0.70), (-2.34, 0.40)]
    g = merge(plate(end_poly, 0.56, 0.025, bevel=0.007), plate(end_poly, -0.56, 0.025, bevel=0.007),
              wing(-0.56, 0.56, 0.22, -2.02, 0.44, 8, t=0.12))
    add_mesh("rear_wing", g, ["paint_blue"], part="rear_wing", sharp=40)
    empty("piv_rw_main", 0, -2.03, 0.90, parent="chassis", part="rear_wing")
    add_mesh("rear_wing_main", wing(-0.5475, 0.5475, 0.34, -2.03, 0.90, 14, t=0.12),
             ["paint_white"], parent="piv_rw_main", part="rear_wing", sharp=40)
    empty("piv_rw_flap", 0, -2.30, 1.00, parent="chassis", part="rear_wing")
    add_mesh("rear_wing_flap", wing(-0.5475, 0.5475, 0.15, -2.30, 1.00, 35, t=0.10),
             ["paint_red"], parent="piv_rw_flap", part="rear_wing", sharp=40)


def build_aero_and_airbrake():
    for side, s in (("L", 1), ("R", -1)):
        # 에어로 핀: 사이드 포드 안에 접혀 있다가 에어로 모드에서 바깥으로 0.22m 나온다.
        empty(f"piv_fin_{side}", s * 0.59, -0.55, 0.30, parent="chassis", part="aero_fins")
        x_in, x_out = (0.48, 0.70) if s > 0 else (-0.70, -0.48)
        add_mesh(f"aero_fin_{side}", wing(x_in, x_out, 0.36, -0.37, 0.29, 4, t=0.10),
                 ["paint_blue"], parent=f"piv_fin_{side}", part="aero_fins", sharp=40)

        # 에어 브레이크: 사이드 포드 윗면 패널. 뒷전 힌지, 앞쪽이 들려 바람을 받는다.
        x0 = s * SIDEPOD_X
        empty(f"piv_airbrake_{side}", x0, -0.88, 0.405, parent="chassis", part="airbrake")
        add_mesh(f"airbrake_{side}", box((x0, -0.63, 0.412), (0.22, 0.50, 0.014), bevel=0.005),
                 ["paint_blue", "paint_red"], parent=f"piv_airbrake_{side}", part="airbrake",
                 face_mat=lambda x, L, z: 1 if z < 0.411 else 0, sharp=50)


def build_boost_pods():
    for side, s in (("L", 1), ("R", -1)):
        x0 = s * POD_X
        piv = f"piv_pod_{side}"
        empty(piv, x0, -1.20, POD_Z, parent="chassis", part="boost_pods")
        rings = [ring(L, r, POD_Z, r, r, 2.0, N=48, x0=x0) for L, r in ((L, POD.at(L)[0]) for L in POD.samples(40))]
        verts, faces = loft(rings)
        faces = faces[:-48]  # 뒤쪽 뚜껑은 노즐 안이 보이도록 연다
        add_mesh(f"boost_pod_{side}", (verts[:-1], faces), ["paint_white", "paint_blue", "paint_red"],
                 parent=piv, part="boost_pods",
                 face_mat=lambda x, L, z: 2 if (L > -0.66 or -1.08 < L < -1.00) else (1 if z < POD_Z - 0.07 else 0))
        nozzle = [(-1.86, 0.160), (-2.03, 0.168), (-2.03, 0.146), (-1.93, 0.124), (-1.86, 0.128)]
        add_mesh(f"boost_nozzle_{side}", revolve(nozzle, (x0, POD_Z), "L", seg=48), ["metal_dark"],
                 parent=piv, part="boost_pods", sharp=45)
        add_mesh(f"boost_glow_{side}", loft([ring(-1.94, 0.125, POD_Z, 0.125, 0.125, 2.0, N=40, x0=x0),
                                             ring(-1.945, 0.124, POD_Z, 0.124, 0.124, 2.0, N=40, x0=x0)]),
                 ["glow_boost"], parent=piv, part="boost_pods")
        add_mesh(f"boost_pylon_{side}", plate([(-0.95, 0.42), (-1.00, 0.58), (-1.55, 0.58), (-1.58, 0.42)],
                                              s * 0.36, 0.03, bevel=0.008),
                 ["paint_blue"], part="boost_pods", sharp=40)


def wheel_meshes(R, W):
    Rin = R * 0.66
    rc, ar, aw = (R + Rin) / 2, (R - Rin) / 2 + 0.004, W / 2
    tire_prof = [(aw * math.copysign(abs(math.sin(t)) ** 0.5, math.sin(t)),
                  rc + ar * math.copysign(abs(math.cos(t)) ** 0.5, math.cos(t)))
                 for t in (2 * math.pi * i / 28 for i in range(28))]
    rim_prof = [(-0.44 * W, 0.985 * Rin), (0.44 * W, 0.985 * Rin), (0.44 * W, 0.93 * Rin), (0.30 * W, 0.86 * Rin),
                (0.26 * W, 0.30 * Rin), (0.30 * W, 0.15 * Rin), (0.30 * W, 0.03),
                (-0.30 * W, 0.03), (-0.30 * W, 0.15 * Rin), (-0.26 * W, 0.30 * Rin), (-0.30 * W, 0.86 * Rin),
                (-0.44 * W, 0.93 * Rin)]
    spokes = []
    for k in range(5):
        a = 2 * math.pi * k / 5
        for sx in (1, -1):
            v, f = box((0, 0, 0), (0.035, 0.03, 0.70 * Rin), bevel=0.006, segments=2)
            # box는 차 좌표로 만들어지므로 로컬 축(X=차축)에 맞게 다시 놓는다
            rot = Matrix.Rotation(a, 4, "X")
            off = Vector((sx * 0.29 * W, 0, 0.51 * Rin))
            spokes.append(([rot @ (Vector((p.x, p.y, p.z)) + off) for p in v], f))
    hub_prof = [(-0.36 * W, 0.01), (0.36 * W, 0.01), (0.36 * W, 0.20 * Rin), (-0.36 * W, 0.20 * Rin)]

    def to_mesh(name, geo, mats, sharp):
        verts, faces = geo
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], [tuple(f) for f in faces])
        me.validate()
        for mn in mats:
            me.materials.append(mat(mn))
        finish(me, sharp=sharp)
        return me

    return (to_mesh(f"tire_{R}", revolve(tire_prof, (0, 0, 0), "X", seg=64), ["tire"], 60),
            to_mesh(f"rim_{R}", merge(revolve(rim_prof, (0, 0, 0), "X", seg=56), *spokes), ["rim"], 40),
            to_mesh(f"hub_{R}", revolve(hub_prof, (0, 0, 0), "X", seg=24), ["paint_red"], 40))


def build_wheels():
    cache = {}
    for tag, L, ax, R, W, part in WHEELS:
        key = (R, W)
        if key not in cache:
            cache[key] = wheel_meshes(R, W)
        tire, rim, hub = cache[key]
        for side, s in (("L", 1), ("R", -1)):
            name = f"wheel_{tag}{side}"
            empty(name, s * ax, L, R, parent="Asurada", part=part)
            add_mesh(f"tire_{tag}{side}", None, None, parent=name, part=part, mesh_data=tire)
            add_mesh(f"rim_{tag}{side}", None, None, parent=name, part=part, mesh_data=rim)
            add_mesh(f"hub_{tag}{side}", None, None, parent=name, part=part, mesh_data=hub)


def glb_to_embedded_json(glb_path, out_path):
    """GLB(JSON 청크 + BIN 청크)를 버퍼가 data URI로 내장된 단일 glTF JSON으로 바꾼다."""
    import base64
    import json
    import struct
    with open(glb_path, "rb") as f:
        data = f.read()
    magic, _version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"GLB가 아닙니다: {glb_path}")
    doc, binary, off = None, None, 12
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8:off + 8 + clen]
        off += 8 + clen
        if ctype == b"JSON":
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == b"BIN\x00":
            binary = chunk
    if doc is None or binary is None or len(doc.get("buffers", [])) != 1:
        raise ValueError("JSON/BIN 청크가 하나씩 있는 GLB만 변환합니다.")
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode("ascii")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- 미리보기

def render_preview(path):
    import numpy as np
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.view_settings.view_transform = "Standard"
    sh = sc.display.shading
    sh.light, sh.color_type = "STUDIO", "MATERIAL"
    sh.show_shadows, sh.show_cavity = True, True
    sh.cavity_type = "BOTH"
    W, H = 800, 450
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W, H, 100
    world = bpy.data.worlds.new("preview")
    world.color = (0.05, 0.06, 0.08)
    sc.world = world
    cam = bpy.data.objects.new("preview_cam", bpy.data.cameras.new("preview_cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    target = Vector((0, 0, 0.45))
    views = [("hero", (3.8, -5.4, 2.4), 40), ("side", (7.5, 0, 0.7), 40),
             ("top", (0, 0.01, 9.0), 40), ("rear", (-3.9, 5.2, 2.1), 40)]
    tmp = os.path.dirname(path)
    paths = []
    for name, pos, lens in views:
        cam.location = pos
        cam.data.lens = lens
        cam.rotation_euler = (target - Vector(pos)).to_track_quat("-Z", "Y").to_euler()
        p = os.path.join(tmp, f"_view_{name}.png")
        sc.render.filepath = p
        bpy.ops.render.render(write_still=True)
        paths.append(p)
    sheet = np.zeros((2 * H, 2 * W, 4), np.float32)
    for k, p in enumerate(paths):
        img = bpy.data.images.load(p)
        a = np.empty(W * H * 4, np.float32)
        img.pixels.foreach_get(a)
        r, c = divmod(k, 2)
        sheet[(1 - r) * H:(2 - r) * H, c * W:(c + 1) * W] = a.reshape(H, W, 4)
        bpy.data.images.remove(img)
        os.remove(p)
    out = bpy.data.images.new("sheet", 2 * W, 2 * H)
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    bpy.data.objects.remove(cam)


# ---------------------------------------------------------------- main

def main():
    global COL
    opts = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    COL = bpy.data.collections.new("Asurada")
    bpy.context.scene.collection.children.link(COL)

    empty("Asurada", 0, 0, 0)
    empty("chassis", 0, 0, 0, parent="Asurada")
    build_body()
    build_cockpit()
    build_front_wing()
    build_rear_wing()
    build_aero_and_airbrake()
    build_boost_pods()
    build_wheels()

    objs = list(COL.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)

    import bx_audit
    gate = bx_audit.deliverable([o.name for o in meshes], profile="render")
    inside_out = [e for e in gate["errors"] if "inside out" in e]
    print("AUDIT_ERRORS", len(gate["errors"]))
    for e in gate["errors"][:40]:
        print("  -", e)

    if opts["glb"]:
        import bx_export as bxe
        os.makedirs(os.path.dirname(opts["glb"]), exist_ok=True)
        bxe.export(opts["glb"], "gltf", [o.name for o in objs], export_extras=True)
        rt = bxe.round_trip(opts["glb"], [o.name for o in meshes], preset="gltf")
        print("ROUNDTRIP_DIFFS", rt.get("diffs"))
        if opts["web"]:
            os.makedirs(os.path.dirname(opts["web"]), exist_ok=True)
            glb_to_embedded_json(opts["glb"], opts["web"])
            print("WEB_GLTF", opts["web"], os.path.getsize(opts["web"]))
    if opts["blend"]:
        os.makedirs(os.path.dirname(opts["blend"]), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=opts["blend"])
    if opts["preview"]:
        render_preview(opts["preview"])
    print(f"RESULT objects={len(objs)} meshes={len(meshes)} tris={tris} inside_out={len(inside_out)}")


if __name__ == "__main__":
    main()
