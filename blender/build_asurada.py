"""Super Asurada 01 팬메이드 모델 생성기 (Blender 5.2, headless).

차체는 '단면 표 → 체이킨 곡선 → 길이 방향 단조 보간' 로프트로 만든다. 단면 제어점마다 날카로움 플래그가
있어 숄더 라인·엣지 같은 캐릭터 라인을 살린다. 날개는 익형 압출, 바퀴·노즐은 회전체, 콕핏과 엔진룸은
불리언으로 파낸다. 웹 뷰어가 움직일 파츠(캐노피, 엔진 커버, 프런트 플랩, 테일 핀, 에어 브레이크,
부스트 나셀, 바퀴)는 피벗 Empty 아래에 둔다. 각 오브젝트의 커스텀 속성 "part"는 glTF extras로 나가
뷰어의 파츠 설명과 연결된다. 모든 메시에 박스 투영 UV를 넣어 뷰어가 카본 같은 질감을 입힐 수 있다.

좌표: 미터, Blender Z-up, 차 앞쪽 = -Y. glTF/three.js에서는 앞쪽이 +Z가 된다.
스크립트 안의 (x, L, z)는 "차 좌표"로 L은 앞쪽(+)으로 잰 길이 방향 위치다.

실행:
  blender -b --factory-startup --python-exit-code 1 -P build_asurada.py -- \
      --glb blender/asurada.glb --web-glb web/asurada.glb --web web/asurada.gltf.json \
      --blend blender/asurada.blend [--preview preview.png]

--glb는 압축하지 않은 기준 GLB로, 다시 불러와 원본과 비교(round trip)한다.
--web-glb는 같은 장면을 meshopt(EXT_meshopt_compression)로 압축한 GLB로, GitHub Pages가 서빙한다.
--web은 압축 GLB의 바이너리 버퍼를 base64로 내장한 glTF JSON이다. claude.ai 아티팩트가 .glb를 서빙하지 않아
아티팩트와 로컬 미리보기는 이 파일을 읽는다. 두 웹용 파일 모두 뷰어에 MeshoptDecoder가 필요하다.
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
    opts = {"glb": None, "web_glb": None, "web": None, "blend": None, "preview": None}
    it = iter(argv)
    for a in it:
        key = a.lstrip("-").replace("-", "_")
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
    "paint_blue": dict(color="#1b3fb8", rough=0.3),
    "paint_white": dict(color="#eef1f5", rough=0.3),
    "paint_red": dict(color="#d3202c", rough=0.3),
    "paint_yellow": dict(color="#f2c230", rough=0.3),
    "paint_gray": dict(color="#7a808a", rough=0.4),
    "gold": dict(color="#c9a23a", rough=0.3, metal=1.0),
    "chrome": dict(color="#d8dde3", rough=0.12, metal=1.0),
    "carbon": dict(color="#17191d", rough=0.45),
    "tire": dict(color="#151515", rough=0.85),
    "rim": dict(color="#c9ced6", rough=0.25, metal=1.0),
    "metal_dark": dict(color="#3b3f45", rough=0.35, metal=1.0),
    "glass": dict(color="#7fd6f0", rough=0.05, alpha=0.35),
    "lamp_yellow": dict(color="#ffc83a", rough=0.1, alpha=0.8),
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
    """법선 정리, 면별 재질, 각도 기준 샤프 엣지, 박스 투영 UV."""
    bm = bmesh.new()
    bm.from_mesh(me)
    if recalc:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()
    if face_mat:
        pl = bm.faces.layers.int.get("panel")
        for f in bm.faces:
            c = f.calc_center_median() + origin
            f.material_index = face_mat(c.x, -c.y, c.z, f[pl] if pl else None)
    for f in bm.faces:
        f.smooth = smooth
    if smooth and sharp:
        lim = math.radians(sharp)
        for e in bm.edges:
            e.smooth = not (e.is_manifold and e.calc_face_angle(0.0) > lim)
    uv = bm.loops.layers.uv.verify()
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        a, b = ((1, 2), (0, 2), (0, 1))[ax]
        for lp in f.loops:
            co = lp.vert.co + origin
            lp[uv].uv = (co[a] * 2.0, co[b] * 2.0)
    bm.to_mesh(me)
    bm.free()


def add_mesh(name, geo, mats, parent="chassis", part=None, face_mat=None,
             smooth=True, sharp=35.0, recalc=True, mesh_data=None):
    """geo = (월드 좌표 정점, 면[, 면별 패널 번호]). 정점은 부모 피벗 기준 로컬 좌표로 옮겨 저장한다."""
    origin = NODES[parent][1] if parent else Vector()
    if mesh_data is None:
        verts, faces = geo[0], geo[1]
        me = bpy.data.meshes.new(name)
        me.from_pydata([Vector(v) - origin for v in verts], [], [tuple(f) for f in faces])
        if len(geo) > 2:
            me.attributes.new("panel", "INT", "FACE").data.foreach_set("value", geo[2])
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


def boolean(ob, cutter, op):
    """불리언 모디파이어를 평가 결과로 굳힌다(연산자 없이 데이터 API만 사용)."""
    mats = [m for m in ob.data.materials]
    mod = ob.modifiers.new("bool", "BOOLEAN")
    mod.operation = op
    mod.solver = "EXACT"
    mod.object = cutter
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.remove(mod)
    old = ob.data
    ob.data = me
    bpy.data.meshes.remove(old)
    if len(me.materials) == 0:
        for m in mats:
            me.materials.append(m)
    return me


def rebase(ob, parent):
    """원점(섀시)에 있던 메시를 피벗 아래로 옮긴다. 정점을 피벗 기준 로컬 좌표로 바꾼다."""
    pob, ppos = NODES[parent]
    ob.data.transform(Matrix.Translation(-ppos))
    ob.parent = pob
    ob.location = Vector()


# ---------------------------------------------------------------- 형상 도구

def merge(*geos):
    verts, faces = [], []
    for g in geos:
        v, f = g[0], g[1]
        off = len(verts)
        verts += list(v)
        faces += [[i + off for i in face] for face in f]
    return verts, faces


def transform_geo(geo, matrix):
    return ([matrix @ Vector(v) for v in geo[0]], geo[1]) + tuple(geo[2:])


def lean_geo(geo, x, L, z, degrees):
    """차의 L축(Blender Y축) 둘레로 기울인다. 양수면 위쪽이 +x로 눕는다."""
    p = P(x, L, z)
    m = Matrix.Translation(p) @ Matrix.Rotation(math.radians(degrees), 4, "Y") @ Matrix.Translation(-p)
    return transform_geo(geo, m)


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


def cosine_samples(L1, L0, count):
    """앞(L1)에서 뒤(L0)로, 양 끝이 촘촘한 코사인 간격."""
    span = L1 - L0
    return [L1 - span * (1 - math.cos(math.pi * i / (count - 1))) / 2 for i in range(count)]


def chaikin_closed(pts, sharp, iters):
    """닫힌 다각형 체이킨 곡선. sharp=True인 꼭짓점은 모서리로 남는다.
    점 개수는 제어점 수와 날카로움 패턴에만 달려서 단면끼리 위상이 같다."""
    for _ in range(iters):
        out, osh = [], []
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if sharp[i]:
                out.append(a)
                osh.append(True)
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
            osh += [False, False]
        pts, sharp = out, osh
    return pts, sharp


class SectionLoft:
    """단면 제어점 표로 만드는 로프트.
    rows = [(L, [(x, z), ...]), ...]  모든 행의 제어점 수가 같아야 한다.
    mirror=True면 제어점은 오른쪽 절반(아래 중앙 x=0 → 위 중앙 x=0)이고 왼쪽은 대칭으로 만든다."""

    def __init__(self, rows, sharp, mirror=True, iters=3):
        rows = sorted(rows, key=lambda r: r[0])
        self.Ls = [r[0] for r in rows]
        n = len(rows[0][1])
        self.fx = [Pchip(self.Ls, [r[1][k][0] for r in rows]) for k in range(n)]
        self.fz = [Pchip(self.Ls, [r[1][k][1] for r in rows]) for k in range(n)]
        self.sharp, self.mirror, self.iters = sharp, mirror, iters
        # 날카로운 점 사이 구간 번호. 링의 점 순서가 모든 단면에서 같아서 색 경계가 캐릭터 라인을 그대로 따른다.
        _, sh = self._ring(self.Ls[0])
        idx = [i for i, f in enumerate(sh) if f] or [0]
        self.panel_of = [(bisect.bisect_right(idx, j) - 1) % len(idx) for j in range(len(sh))]

    def _ring(self, L):
        c = [(fx(L), fz(L)) for fx, fz in zip(self.fx, self.fz)]
        sh = self.sharp
        if self.mirror:
            c = c + [(-x, z) for x, z in reversed(c[1:-1])]
            sh = list(sh) + list(reversed(sh[1:-1]))
        return chaikin_closed(c, sh, self.iters)

    def ring2d(self, L):
        return self._ring(L)[0]

    def geo(self, count, x0=0.0, flip=False, L_range=None):
        L1, L0 = L_range or (self.Ls[-1], self.Ls[0])
        s = -1 if flip else 1
        rings = [[P(s * (x0 + x), L, z) for x, z in self.ring2d(L)] for L in cosine_samples(L1, L0, count)]
        verts, faces = loft(rings)
        N = len(rings[0])
        panels = [self.panel_of[j] for _ in range(len(rings) - 1) for j in range(N)]
        return verts, faces, panels + [-1] * (len(faces) - len(panels))

    def top(self, L, x):
        """단면 L에서 가로 위치 x의 윗면 높이."""
        pts = self.ring2d(L)
        best = None
        for (x1, z1), (x2, z2) in zip(pts, pts[1:] + pts[:1]):
            if x1 != x2 and (x1 - x) * (x2 - x) <= 0:
                z = z1 + (x - x1) / (x2 - x1) * (z2 - z1)
                best = z if best is None else max(best, z)
        return best if best is not None else max(z for _, z in pts)


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


def box(center, size, bevel=0.0, segments=3, tilt=0.0, roll=0.0, yaw=0.0):
    """둥근 상자. center/size는 차 좌표 (x, L, z).
    tilt: X축(+면 윗부분이 뒤로), roll: L축(+면 윗부분이 +x로), yaw: 수직축(+면 앞쪽이 +x로), 단위 도."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bevel, segments=segments,
                        affect="EDGES", profile=0.5, clamp_overlap=True)
    rot = (Matrix.Rotation(math.radians(-yaw), 4, "Z") @ Matrix.Rotation(math.radians(roll), 4, "Y")
           @ Matrix.Rotation(math.radians(-tilt), 4, "X"))
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


def conform_lens(poly_xL, lf, lift_top, lift_bot, flip=False, sharp=None):
    """로프트 윗면을 따라 붙는 얇은 렌즈(헤드라이트 등). 윤곽은 체이킨으로 둥글리되 sharp인 꼭짓점은 모서리로 남긴다."""
    pts, _ = chaikin_closed(poly_xL, sharp or [False] * len(poly_xL), 3)
    n = len(pts)
    cx, cL = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    ring_pts = pts + [(cx, cL)]
    s = -1 if flip else 1
    top = [P(s * x, L, lf.top(L, x) + lift_top) for x, L in ring_pts]
    bot = [P(s * x, L, lf.top(L, x) + lift_bot) for x, L in ring_pts]
    verts = top + bot
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((n, i, j))
        faces.append((2 * n + 1, n + 1 + j, n + 1 + i))
        faces.append((i, n + 1 + i, n + 1 + j, j))
    return verts, faces


def catmull(points, per=8):
    """제어점을 지나는 캐트멀-롬 곡선 샘플(끝점 포함)."""
    pts = [Vector(p) for p in points]
    ext = [pts[0] * 2 - pts[1]] + pts + [pts[-1] * 2 - pts[-2]]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(per):
            t = k / per
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t))
    out.append(pts[-1])
    return out


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


# ---------------------------------------------------------------- 치수표
# 치수는 도장 설명서의 측면·평면·정면·후면도에서 잰 값이다(blueprint.py가 뽑은 축척 0.0056m/px, 전장 약
# 4.97m). 모델이 도면과 얼마나 겹치는지는 blueprint_check.py로 잰다.
# 앞쪽은 낮은 쐐기 카울과 양 끝의 블레이드 혼, 가운데로 솟는 노즈 척추가 캐노피로 이어진다. 앞바퀴는 작고
# 긴 암 끝에 멀리 나가 있다. 스폰슨은 앞바퀴 뒤에서 넓어지고, 큰 부스트 나셀 두 개가 리어 바디를 이룬다.
# 나셀에서 15° 들린 리어 윙이 바깥 끝의 핀 포드로 이어지고, 테일 핀은 위쪽이 안으로 19° 기운 판이다.

# 바퀴: 이름, L, |x|, 반지름 R, 폭 W, 파츠
WHEELS = [
    ("F1", 1.22, 0.955, 0.235, 0.39, "front_wheels"),
    ("F2", 0.66, 0.955, 0.235, 0.39, "front_wheels"),
    ("R", -1.558, 0.97, 0.375, 0.52, "rear_wheels"),
]

# 척추(노즈~콕핏~엔진 덮개~나셀 사이 꼬리). 오른쪽 절반 제어점: 아래 중앙, 아래 모서리*, 옆 최대폭, 숄더*,
# 덱 바깥, 덱 가운데, 위 중앙. 노즈 끝은 카울 속에 낮게 묻히고 L 2.15부터 솟아 캐노피 앞끝으로 이어진다.
TUB = SectionLoft([
    (2.30, [(0, .090), (.010, .090), (.015, .095), (.015, .102), (.012, .106), (.006, .108), (0, .110)]),
    (2.20, [(0, .090), (.060, .090), (.080, .110), (.080, .180), (.060, .210), (.030, .218), (0, .220)]),
    (2.10, [(0, .090), (.120, .090), (.150, .150), (.150, .290), (.110, .335), (.055, .347), (0, .350)]),
    (2.00, [(0, .090), (.160, .090), (.200, .160), (.200, .330), (.150, .372), (.075, .382), (0, .384)]),
    (1.75, [(0, .090), (.240, .090), (.280, .170), (.280, .390), (.210, .432), (.105, .442), (0, .444)]),
    (1.45, [(0, .065), (.360, .065), (.440, .190), (.450, .440), (.380, .482), (.190, .492), (0, .494)]),
    (1.10, [(0, .065), (.380, .065), (.460, .200), (.470, .480), (.420, .535), (.210, .550), (0, .555)]),
    (0.60, [(0, .065), (.380, .065), (.460, .200), (.470, .490), (.420, .555), (.220, .575), (0, .580)]),
    (0.20, [(0, .065), (.360, .065), (.440, .200), (.450, .500), (.410, .565), (.220, .595), (0, .600)]),
    (-0.10, [(0, .065), (.320, .065), (.400, .200), (.410, .520), (.370, .600), (.200, .640), (0, .650)]),
    (-0.25, [(0, .065), (.300, .065), (.370, .200), (.380, .560), (.330, .660), (.170, .710), (0, .720)]),
    (-0.40, [(0, .065), (.280, .065), (.340, .200), (.350, .690), (.300, .885), (.150, .950), (0, .965)]),
    (-0.60, [(0, .070), (.260, .070), (.310, .200), (.300, .700), (.240, .860), (.120, .920), (0, .930)]),
    (-0.80, [(0, .080), (.240, .080), (.280, .200), (.260, .660), (.180, .820), (.090, .870), (0, .880)]),
    (-1.05, [(0, .100), (.220, .100), (.250, .220), (.220, .620), (.120, .760), (.060, .800), (0, .810)]),
    (-1.30, [(0, .120), (.200, .120), (.220, .240), (.180, .600), (.070, .800), (.035, .840), (0, .850)]),
    (-1.70, [(0, .140), (.180, .140), (.200, .260), (.140, .600), (.070, .840), (.035, .880), (0, .890)]),
    (-2.05, [(0, .200), (.140, .200), (.150, .300), (.100, .600), (.070, .840), (.035, .880), (0, .890)]),
    (-2.17, [(0, .400), (.030, .400), (.040, .450), (.040, .700), (.035, .800), (.020, .840), (0, .850)]),
], sharp=[False, True, False, True, False, False, False])


def side_ctrl(xi, xo, zb, zt, zo):
    """스폰슨 단면(오른쪽, 닫힌 다각형): 안쪽 아래*, 바깥 아래*, 바깥 옆, 바깥 위*, 숄더, 윗면 안쪽, 안쪽 위*.
    윗면은 |x| 0.8 언저리 숄더(zt)에서 가장 높고 바깥 끝(zo)으로 갈수록 낮아진다(정면도에서 뒷바퀴가 보이는 높이)."""
    xs = max(xi + 0.03, xo - 0.16)
    return [(xi, zb), (xo - 0.04, zb), (xo, zb + 0.6 * (zo - zb)), (xo - 0.01, zo),
            (xs, zt), (xi + 0.08, zt + 0.004), (xi, zt - 0.04)]


# 스폰슨: 콕핏 옆 L 0.33에서 뾰족하게 시작해 L 0 언저리에서 |x| 0.95까지 벌어지고, 뒤끝은 뒷바퀴 앞에서 좁아진다.
SIDE = SectionLoft([(L, side_ctrl(*v)) for L, v in [
    (0.33, (0.30, 0.42, 0.31, 0.40, 0.37)),
    (0.25, (0.30, 0.52, 0.30, 0.42, 0.36)),
    (0.15, (0.30, 0.66, 0.28, 0.425, 0.35)),
    (0.05, (0.30, 0.79, 0.24, 0.43, 0.33)),
    (-0.05, (0.30, 0.905, 0.19, 0.44, 0.31)),
    (-0.20, (0.30, 0.93, 0.15, 0.45, 0.30)),
    (-0.40, (0.30, 0.945, 0.07, 0.46, 0.30)),
    (-0.70, (0.30, 0.94, 0.065, 0.45, 0.30)),
    (-0.85, (0.30, 0.88, 0.08, 0.44, 0.30)),
    (-1.00, (0.30, 0.795, 0.11, 0.42, 0.30)),
    (-1.10, (0.30, 0.74, 0.14, 0.41, 0.30)),
    (-1.14, (0.30, 0.62, 0.17, 0.40, 0.30)),
]], sharp=[True, True, False, True, False, False, True], mirror=False)


def cowl_ctrl(w, zb, zte, ztc):
    """프런트 카울 단면(오른쪽 절반): 아래 중앙, 아래 바깥*, 앞전, 위 바깥*, 위 가운데, 위 중앙."""
    return [(0, zb), (w * 0.96, zb), (w, (zb + zte) / 2), (w * 0.96, zte), (w * 0.5, zte + (ztc - zte) * 0.75), (0, ztc)]


# 프런트 카울: 앞전은 가운데(L 2.32)가 가장 앞선 깊은 V, 윗면은 앞전 높이 0.1에서 뒷전 0.33까지 오르는 쐐기.
COWL = SectionLoft([(L, cowl_ctrl(*v)) for L, v in [
    (2.32, (0.030, 0.072, 0.090, 0.100)),
    (2.29, (0.120, 0.071, 0.094, 0.115)),
    (2.25, (0.216, 0.070, 0.100, 0.135)),
    (2.20, (0.272, 0.070, 0.110, 0.155)),
    (2.15, (0.361, 0.070, 0.122, 0.175)),
    (2.10, (0.524, 0.070, 0.140, 0.200)),
    (2.05, (0.686, 0.070, 0.160, 0.220)),
    (2.00, (0.880, 0.070, 0.180, 0.240)),
    (1.85, (0.920, 0.070, 0.230, 0.280)),
    (1.70, (0.920, 0.070, 0.280, 0.310)),
    (1.62, (0.910, 0.072, 0.300, 0.325)),
]], sharp=[False, True, False, True, False, False])


def horn_ctrl(xi, xo, zb, zs, zc):
    """블레이드 혼 단면(오른쪽): 안쪽 아래*, 바깥 아래*, 바깥 위*, 마루, 안쪽 위*. 옆면은 파랑, 마루는 흰색."""
    return [(xi, zb), (xo, zb), (xo, zs), ((xi + xo) / 2, zc), (xi, zs)]


# 블레이드 혼: L 2.50 끝에서 뒤로 넓어져 밑동 |x| 0.85~1.13(맨 뒤는 카울 뒷전 바깥까지 0.76). 옆면 높이 0.25, 마루 0.35.
HORN = SectionLoft([(L, horn_ctrl(*v)) for L, v in [
    (2.50, (0.960, 0.994, 0.066, 0.085, 0.092)),
    (2.45, (0.938, 1.011, 0.061, 0.100, 0.108)),
    (2.40, (0.921, 1.033, 0.058, 0.115, 0.128)),
    (2.30, (0.899, 1.056, 0.056, 0.165, 0.185)),
    (2.20, (0.876, 1.072, 0.056, 0.210, 0.245)),
    (2.10, (0.860, 1.089, 0.056, 0.245, 0.300)),
    (2.00, (0.850, 1.100, 0.056, 0.250, 0.340)),
    (1.80, (0.850, 1.123, 0.056, 0.250, 0.350)),
    (1.60, (0.850, 1.128, 0.056, 0.250, 0.350)),
    (1.56, (0.780, 1.120, 0.058, 0.245, 0.330)),
    (1.50, (0.760, 0.930, 0.070, 0.200, 0.250)),
]], sharp=[True, True, True, False, True], mirror=False)

# 캐노피 유리: (L, 반폭, 꼭대기 높이). 앞끝 L 1.25에서 뾰족하게 시작해 L 0에서 반폭 0.35로 가장 넓다.
CANOPY = [(1.25, 0.020, 0.0), (1.20, 0.100, 0.552), (1.10, 0.140, 0.580), (1.00, 0.160, 0.610), (0.80, 0.200, 0.668),
          (0.60, 0.240, 0.728), (0.40, 0.280, 0.772), (0.30, 0.330, 0.785), (0.15, 0.345, 0.790), (0.00, 0.350, 0.792),
          (-0.10, 0.330, 0.795), (-0.20, 0.280, 0.800), (-0.27, 0.150, 0.800), (-0.31, 0.030, 0.0)]
# 캐노피 지붕: 유리 위 가운데의 흰 마루(평면도의 흰 띠). 뒤로 갈수록 높아져 측면 실루엣 꼭대기(1.0)가 된다.
ROOF = [(0.38, 0.020, 0.785), (0.30, 0.060, 0.800), (0.20, 0.100, 0.843), (0.00, 0.120, 0.904), (-0.15, 0.130, 0.945),
        (-0.25, 0.120, 0.985), (-0.32, 0.080, 1.005), (-0.40, 0.030, 0.985)]

# 부스트 나셀: 축(|x|, z)과 파란 페어링(L, 반지름, 축 높이). 페어링은 L −0.30에서 뾰족하게 시작한다.
NACELLE_X, NACELLE_Z = 0.335, 0.70
NACELLE = [(-0.30, 0.010, 0.640), (-0.36, 0.050, 0.645), (-0.45, 0.090, 0.650), (-0.60, 0.130, 0.660),
           (-0.80, 0.170, 0.675), (-1.00, 0.200, 0.688), (-1.20, 0.225, 0.695), (-1.45, 0.248, 0.700),
           (-1.65, 0.260, 0.700), (-1.83, 0.265, 0.700)]
ENGINE_BAY = (-0.62, -1.40)  # 엔진룸 앞뒤 L
# 엔진은 두 나셀 페어링 사이의 V자 틈(z 0.7에서 |x| 0.10~0.20) 바로 아래까지 올려 덮개를 열면 위에서 보이게 한다.
BAY_FLOOR, CAP_Z, BAY_HALF = 0.47, 0.76, 0.20
ENGINE_W = 0.72  # V형 엔진 가로 축척(페어링 사이 틈에 맞춘다)

# 리어 윙: (|x|, 앞전 L, 뒷전 L, 가운데 높이, 두께). 나셀 안쪽 뿌리에서 핀 포드 속 끝까지 15° 들린다.
REAR_WING = [(0.45, -1.15, -1.96, 0.800, 0.15), (1.12, -1.26, -2.22, 1.010, 0.10)]


def blade_x(z):
    """테일 핀 바깥 판의 가로 위치(정면·후면도 띠의 가운데: z 1.585에서 |x| 1.19, z 0.42에서 1.6). 위쪽이 안으로 19° 기운다."""
    return 1.19 + (1.585 - z) / 2.88


def strip_x(z):
    """핀 안쪽 판(포드 위로 솟은 얇은 판)의 가로 위치. 위쪽이 안으로 13° 기운다."""
    return 0.99 - (z - 1.30) * 0.229


# 테일 핀 바깥 판의 옆모습(L, z): 파란 몸판과 뒷전의 노란 띠. 앞쪽 아랫변(z 1.01)은 핀 포드 바깥 벽에 붙는다.
FIN_BLUE = [(-1.27, 1.01), (-1.30, 1.04), (-1.40, 1.14), (-1.50, 1.196), (-1.60, 1.229), (-1.70, 1.257),
            (-1.80, 1.347), (-1.90, 1.487), (-1.97, 1.555), (-2.06, 1.600), (-2.14, 1.00), (-2.20, 0.55),
            (-2.215, 0.495), (-2.20, 0.49), (-2.15, 0.45), (-2.10, 0.457), (-2.05, 0.52), (-2.00, 0.584),
            (-1.95, 0.65), (-1.90, 0.73), (-1.85, 0.811), (-1.80, 0.875), (-1.75, 0.956), (-1.71, 1.01)]
FIN_YELLOW = [(-2.06, 1.600), (-2.13, 1.604), (-2.25, 1.43), (-2.37, 1.24), (-2.36, 0.94), (-2.33, 0.62),
              (-2.25, 0.54), (-2.215, 0.495), (-2.20, 0.55), (-2.14, 1.00)]
FIN_STRIP = [(-1.72, 1.25), (-1.80, 1.35), (-1.90, 1.47), (-1.98, 1.53), (-2.12, 1.53), (-2.17, 1.42), (-2.17, 1.25)]


def pod_ctrl(xi, zb, zt, xo=None, zbo=1.01):
    """핀 포드 단면(오른쪽): 안쪽 아래, 바깥 아래, 바깥 위, 안쪽 위, 안쪽 어깨(모두 모서리). 바깥 벽은 핀 판 바로
    안쪽이고, 바닥은 리어 윙처럼 바깥으로 갈수록 올라간다(정면도 0.93 → 1.01). 안쪽 벽은 수직이고 윗모서리만
    안으로 0.06 들어 평면도의 노란 윗면 안쪽 끝(|x| 0.99)에 맞춘다."""
    xb = xo if xo is not None else blade_x(zbo) - 0.012
    xt = xo if xo is not None else blade_x(zt) - 0.012
    k = min(0.08, (zt - zb) * 0.4)
    return [(xi, zb), (xb, zbo), (xt, zt), (xi - 0.06, zt), (xi, zt - k)]


# 핀 포드: 리어 윙 끝을 감싸는 상자. 윗변이 측면도의 오목한 어깨선(L −1.25에서 1.0 → L −2.0에서 1.38)이다.
POD = SectionLoft([(L, pod_ctrl(*v)) for L, v in [
    (-1.23, (1.27, 0.950, 0.970, 1.32, 0.950)),
    (-1.30, (1.060, 0.950, 1.040)),
    (-1.40, (1.055, 0.940, 1.140)),
    (-1.50, (1.050, 0.935, 1.196)),
    (-1.70, (1.050, 0.930, 1.257)),
    (-1.85, (1.050, 0.930, 1.320)),
    (-2.00, (1.050, 0.930, 1.380)),
    (-2.15, (1.030, 0.930, 1.380)),
    (-2.27, (1.030, 0.930, 1.360)),
    (-2.31, (1.040, 0.950, 1.300)),
]], sharp=[True, True, True, True, True], mirror=False, iters=1)


# ---------------------------------------------------------------- 파츠

BLUE, WHITE, RED, CARBON, YELLOW, GRAY = 0, 1, 2, 3, 4, 5
PAINT = ["paint_blue", "paint_white", "paint_red", "carbon", "paint_yellow", "paint_gray"]


def tub_mat(x, L, z, p=None):
    """패널: 0 오른쪽 옆면, 1 윗면, 2 왼쪽 옆면, 3 바닥, -1 끝 뚜껑.
    도면처럼 노즈·콕핏 둘레·엔진 덮개는 흰색, 나셀 사이의 꼬리는 파랑이고 끝 윗면은 노랑이다.
    앞바퀴 사이 옆면은 z 0.33 아래가 회색 섀시다(측면도에서 흰 몸판 아래로 서스펜션이 붙는 부분)."""
    if p == 3:
        return CARBON
    if L < -1.25:
        return YELLOW if p == 1 and L < -1.95 else BLUE
    if p in (0, 2) and z < 0.33 and 0.20 < L < 1.58:
        return GRAY
    return WHITE


# 콕핏 욕조: 캐노피 발자국 안쪽(L −0.15~0.85, |x| 0.20)
COCKPIT = ((0, 0.35, 0.80), (0.40, 1.00, 1.04))


def build_tub():
    tub = add_mesh("body", TUB.geo(110), PAINT, part="body", face_mat=tub_mat, sharp=40)

    # 콕핏: 둥근 상자를 빼서 시트가 들어갈 욕조 모양을 만든다(캐노피 밑단 안쪽).
    cockpit = add_mesh("cut_cockpit", box(*COCKPIT, bevel=0.10, segments=4), [], parent=None, smooth=False)
    boolean(tub, cockpit, "DIFFERENCE")
    bpy.data.objects.remove(cockpit)
    (cx, cL, _), (sx, sL, _) = COCKPIT

    # 엔진룸: 엔진 덮개 윗면에서 덮개 캡(교집합)을 먼저 떼어 내고, 차체에서는 엔진룸 상자를 빼낸다.
    L1, L0 = ENGINE_BAY
    Lc, Ln = (L1 + L0) / 2, L1 - L0
    bay_geo = box((0, Lc, (BAY_FLOOR + 1.6) / 2), (2 * BAY_HALF, Ln, 1.6 - BAY_FLOOR), bevel=0.07, segments=3)
    cap_geo = box((0, Lc, (CAP_Z + 1.6) / 2), (2 * BAY_HALF, Ln, 1.6 - CAP_Z), bevel=0.07, segments=3)
    cover = add_mesh("engine_cover", None, None, part="engine", mesh_data=tub.data.copy())
    cap = add_mesh("cut_cap", cap_geo, [], parent=None, smooth=False)
    boolean(cover, cap, "INTERSECT")
    bpy.data.objects.remove(cap)
    cutter = add_mesh("cut_bay", bay_geo, [], parent=None, smooth=False)
    boolean(tub, cutter, "DIFFERENCE")
    bpy.data.objects.remove(cutter)

    def tub_mat_cut(x, L, z, p=None):
        if abs(x) < sx / 2 + 0.005 and cL - sL / 2 - 0.01 < L < cL + sL / 2 + 0.01 and z < TUB.top(L, x) - 0.015:
            return CARBON  # 콕핏 안쪽
        if abs(x) < BAY_HALF + 0.01 and L0 - 0.01 < L < L1 + 0.01 and z < CAP_Z:
            return CARBON  # 엔진룸 벽
        return tub_mat(x, L, z, p)

    finish(tub.data, tub_mat_cut, sharp=40)

    # 덮개: 앞쪽 힌지 피벗 아래로 옮기고, 가장자리에 이음매 틈이 보이도록 살짝 줄인다.
    empty("piv_engine_cover", 0, L1, TUB.top(L1, 0) - 0.01, parent="chassis", part="engine")
    me = cover.data
    me.transform(Matrix.Translation(P(0, Lc, 0)) @ Matrix.Diagonal((0.985, 0.992, 1.0, 1.0))
                 @ Matrix.Translation(-P(0, Lc, 0)))
    finish(me, lambda x, L, z, p=None: CARBON if z < CAP_Z + 0.03 else tub_mat(x, L, z, p), sharp=40)
    rebase(cover, "piv_engine_cover")

    # 콕핏 뒤 지붕 끝의 빨간 상자(측면도의 빨간 블록). 엔진 덮개 힌지보다 앞에 둔다.
    add_mesh("roll_light", box((0, -0.525, 0.945), (0.13, 0.12, 0.12), bevel=0.025), ["paint_red"], part="cockpit", sharp=45)


def build_engine():
    """엔진룸 안의 V형 엔진: 블록, 좌우 뱅크, 금색 캠 커버, 흡기 트럼펫, 은색 배기 헤더."""
    L1, L0 = ENGINE_BAY
    Lc, length = (L1 + L0) / 2, (L1 - L0) - 0.22
    z0 = BAY_FLOOR + 0.075  # 블록 중심
    w = ENGINE_W
    parts = [box((0, Lc, z0), (0.26 * w, length, 0.12), bevel=0.02)]
    gold, chrome = [], []
    up = Matrix.Rotation(math.radians(-90), 4, "Y")  # 회전체의 +x 축을 위(+z)로
    for s in (1, -1):
        parts.append(box((s * 0.09 * w, Lc, z0 + 0.07), (0.10 * w, length - 0.04, 0.10), bevel=0.015, roll=s * 32))
        gold.append(box((s * 0.118 * w, Lc, z0 + 0.115), (0.07 * w, length - 0.08, 0.03), bevel=0.011, roll=s * 32))
        for k in range(7):
            Lk = L1 - 0.10 - k * (length - 0.16) / 6
            chrome.append(box((s * 0.122 * w, Lk, z0 + 0.133), (0.074 * w, 0.012, 0.010), bevel=0.003, roll=s * 32))
        for k in range(6):
            Lk = L1 - 0.13 - k * (length - 0.22) / 5
            chrome.append(transform_geo(revolve([(0.0, 0.010), (0.07, 0.017), (0.07, 0.013), (0.0, 0.007)],
                                                (0, 0, 0), "X", seg=12),
                                        Matrix.Translation(P(s * 0.032 * w, Lk, z0 + 0.085)) @ up))
    add_mesh("engine_block", merge(*parts), ["metal_dark"], part="engine", sharp=45)
    add_mesh("engine_cams", merge(*gold), ["gold"], part="engine", sharp=45)
    add_mesh("engine_trumpets", merge(*chrome), ["chrome"], part="engine", sharp=50)

    # 배기: 뱅크 바깥에서 나와 엔진룸 바닥 아래로 내려가 꼬리 아래의 6연장 배기구로 모인다.
    exits = [(-0.07, 0.30), (-0.07, 0.37), (0.0, 0.37), (0.07, 0.30), (0.07, 0.37), (0.0, 0.30)]
    pipes = []
    for i, (ex, ez) in enumerate(exits):
        s = -1 if i < 3 else 1
        k = i % 3
        Lh = L1 - 0.18 - k * 0.24
        ctrl = [P(s * 0.16 * w, Lh, z0 + 0.05), P(s * 0.20 * w, Lh - 0.06, z0 + 0.0),
                P(s * (0.17 - 0.035 * k), L0 + 0.05, BAY_FLOOR - 0.04 - 0.035 * k),
                P(ex, -1.90, ez), P(ex, -2.15, ez)]
        pipes.append(curve_tube(catmull(ctrl, per=8), 0.021, closed=False))
        pipes.append(revolve([(-2.12, 0.030), (-2.25, 0.031), (-2.25, 0.024), (-2.12, 0.023)], (ex, ez), "L", seg=24))
    add_mesh("exhaust", merge(*pipes), ["chrome"], part="engine", sharp=50)
    add_mesh("exhaust_dark", merge(*[revolve([(-2.22, 0.0), (-2.22, 0.024), (-2.235, 0.024), (-2.235, 0.0)],
                                             (ex, ez), "L", seg=16) for ex, ez in exits]),
             ["intake"], part="engine", smooth=False)


def build_sides():
    L_front = SIDE.Ls[-1]
    for side, s in (("L", 1), ("R", -1)):
        def side_mat(x, L, z, p=None):
            """패널: 0 바닥, 1 바깥 옆면, 2 윗면, 3 안쪽, -1 앞뒤 뚜껑."""
            if p == 0:
                return CARBON
            return WHITE if p == 3 else BLUE
        add_mesh(f"sidepod_{side}", SIDE.geo(44, flip=(s < 0)), PAINT, part="sidepods", face_mat=side_mat, sharp=40)

        # 앞쪽 흡기구: 앞면 단면을 줄여 어두운 면을 덧댄다.
        r = SIDE.ring2d(L_front)
        cx = sum(p[0] for p in r) / len(r)
        cz = sum(p[1] for p in r) / len(r)
        inner = [P(s * (cx + (x - cx) * 0.78), L_front + 0.006, cz + (z - cz) * 0.70) for x, z in r]
        inner2 = [P(s * (cx + (x - cx) * 0.74), L_front + 0.002, cz + (z - cz) * 0.66) for x, z in r]
        add_mesh(f"sidepod_intake_{side}", loft([inner2, inner]), ["intake"], part="sidepods", sharp=None)

        # 옆구리 루버: 어두운 판 + 가로 날개 4장
        Lc = -0.45
        xo = max(x for x, z in SIDE.ring2d(Lc) if 0.17 < z < 0.30)
        add_mesh(f"louver_{side}", box((s * (xo - 0.004), Lc, 0.235), (0.02, 0.36, 0.085), bevel=0.006),
                 ["intake"], part="sidepods", sharp=50)
        slats = [box((s * (xo + 0.004), Lc, 0.205 + k * 0.02), (0.018, 0.34, 0.006), bevel=0.002, roll=s * 20)
                 for k in range(4)]
        add_mesh(f"louver_slats_{side}", merge(*slats), ["metal_dark"], part="sidepods", sharp=50)

        # 윗면 냉각 팬: 어두운 원판, 날개 12장, 은색 허브
        fx, fL = 0.62, -0.05
        z = SIDE.top(fL, fx) + 0.003
        base = transform_geo(revolve([(0.0, 0.002), (0.012, 0.002), (0.012, 0.095), (0.0, 0.095)], (0, 0, 0), "X", 40),
                             Matrix.Translation(P(s * fx, fL, z)) @ Matrix.Rotation(math.radians(90), 4, "Y"))
        add_mesh(f"fan_base_{side}", base, ["intake"], part="sidepods", sharp=50)
        blades = [transform_geo(box((0, 0, 0), (0.012, 0.07, 0.004), bevel=0.0),
                                Matrix.Translation(P(s * fx, fL, z + 0.010)) @ Matrix.Rotation(math.radians(k * 30), 4, "Z")
                                @ Matrix.Translation(Vector((0, 0.05, 0))) @ Matrix.Rotation(math.radians(25), 4, "Y"))
                  for k in range(12)]
        hub = transform_geo(revolve([(0.0, 0.0005), (0.014, 0.0005), (0.014, 0.028), (0.0, 0.028)], (0, 0, 0), "X", 24),
                            Matrix.Translation(P(s * fx, fL, z + 0.004)) @ Matrix.Rotation(math.radians(90), 4, "Y"))
        add_mesh(f"fan_blades_{side}", merge(*blades, hub), ["chrome"], part="sidepods", sharp=50)

        # 캐노피 뒤 옆구리의 빨간 베인: 안쪽 앞에서 바깥 뒤로 비스듬히 뻗는다(평면도 |x| 0.32~0.51, L 0.55~0.40).
        add_mesh(f"vane_{side}", box((s * 0.42, 0.475, 0.48), (0.24, 0.075, 0.17), bevel=0.02, yaw=-s * 38),
                 ["paint_red"], part="sidepods", sharp=40)

        # 서스펜션 레일: 차체 옆을 따라 앞바퀴 두 쌍의 암을 받치는 회색 보(평면도 |x| 0.46~0.55, L 0.45~1.5)
        add_mesh(f"susp_rail_{side}", box((s * 0.505, 0.97, 0.245), (0.085, 1.06, 0.14), bevel=0.022),
                 ["paint_gray"], part="suspension", sharp=45)

    # 플로어와 디퓨저, 꼬리 아래 기어박스
    floor = SectionLoft([(L, [(0, .053), (w * .97, .053), (w, .065), (w * .97, .077), (0, .077)]) for L, w in [
        (0.42, 0.40), (0.30, 0.55), (-1.12, 0.68), (-1.25, 0.55), (-2.20, 0.45)]],
        sharp=[False, True, False, True, False], iters=2)
    add_mesh("floor", floor.geo(30), ["carbon"], part="underbody", sharp=50)
    strakes = merge(*[plate([(-1.45, 0.07), (-2.10, 0.07), (-2.17, 0.24), (-1.90, 0.22)], x, 0.014)
                      for x in (-0.34, -0.16, 0.16, 0.34)])
    add_mesh("diffuser", strakes, ["carbon"], part="underbody", sharp=50)
    add_mesh("gearbox", box((0, -2.03, 0.27), (0.86, 0.32, 0.30), bevel=0.03), ["paint_gray"], part="underbody", sharp=50)
    add_mesh("rain_light", box((0, -2.195, 0.38), (0.05, 0.014, 0.03), bevel=0.004), ["lamp_red"], part="underbody", sharp=50)


def build_front():
    def cowl_mat(x, L, z, p=None):
        """패널: 0·2 앞전, 1 윗면, 3 바닥. 윗면 줄무늬와 번호는 뷰어가 데칼로 입힌다."""
        return {1: WHITE, 3: CARBON}.get(p, BLUE)
    add_mesh("front_cowl", COWL.geo(32), PAINT, part="front_cowl", face_mat=cowl_mat, sharp=40)

    def horn_mat(x, L, z, p=None):
        """패널: 0 바닥, 1 바깥 옆면, 2 마루, 3 안쪽 옆면. 마루는 끝(L 2.0 앞)만 파랗고 뒤는 흰색."""
        if p == 0:
            return CARBON
        return WHITE if p == 2 and L < 2.0 else BLUE
    for side, s in (("L", 1), ("R", -1)):
        add_mesh(f"horn_{side}", HORN.geo(28, flip=(s < 0)), PAINT, part="front_cowl", face_mat=horn_mat, sharp=40)
        # 노란 셰브런 램프: 노즈 척추 옆 경사면을 따라 비스듬히 붙는다.
        add_mesh(f"headlight_{side}", conform_lens([(0.148, 1.94), (0.182, 1.94), (0.315, 1.46), (0.250, 1.46),
                                                    (0.148, 1.64)], TUB, 0.012, -0.006, flip=(s < 0),
                                                   sharp=[True, True, True, True, False]),
                 ["lamp_yellow"], part="nose", sharp=60)
        # 카울 뒷전 플랩(에어로 모드에서 각도를 키운다). 앞바퀴(L 1.455)보다 앞, 카울 뒷전 바깥쪽 윗면에 얹는다.
        empty(f"piv_front_flap_{side}", s * 0.64, 1.64, 0.316, parent="chassis", part="front_cowl")
        add_mesh(f"front_flap_{side}", wing(s * 0.48, s * 0.80, 0.08, 1.64, 0.316, 12, t=0.10),
                 ["paint_blue"], parent=f"piv_front_flap_{side}", part="front_cowl", sharp=40)


def build_cockpit():
    # 캐노피: 앞쪽 힌지, 뒤쪽이 들린다. 밑단은 차체 윗면 곡선에 맞춘다.
    Lh = CANOPY[0][0]
    empty("piv_canopy", 0, Lh, TUB.top(Lh, 0), parent="chassis", part="canopy")
    Ls = [r[0] for r in reversed(CANOPY)]
    fa = Pchip(Ls, [r[1] for r in reversed(CANOPY)])
    ft = Pchip(Ls, [r[2] for r in reversed(CANOPY)])
    nc, M = 2.4, 27

    def glass_peak(L):
        return max(ft(L), TUB.top(L, 0) + 0.004)

    rings, rim_l, rim_r = [], [], []
    for L in cosine_samples(CANOPY[0][0], CANOPY[-1][0], 36):
        a = max(fa(L), 0.004)
        base_c = TUB.top(L, 0)
        peak = glass_peak(L)
        r = []
        for j in range(M):
            th = math.pi * j / (M - 1)
            c, s = math.cos(th), math.sin(th)
            x = a * math.copysign(abs(c) ** (2 / nc), c)
            zb = TUB.top(L, x) - 0.004
            r.append(P(x, L, zb + (peak - base_c) * abs(s) ** (2 / nc)))
        rings.append(r)
        rim_l.append(P(a, L, TUB.top(L, a) + 0.006))
        rim_r.append(P(-a, L, TUB.top(L, -a) + 0.006))
    add_mesh("canopy_glass", loft(rings, cyclic=False), ["glass"], parent="piv_canopy", part="canopy",
             recalc=False, sharp=None)
    add_mesh("canopy_frame", curve_tube(rim_l + list(reversed(rim_r)), 0.016), ["paint_white"],
             parent="piv_canopy", part="canopy", sharp=None)

    # 지붕 마루: 유리 꼭대기(유리가 끝난 뒤는 차체 윗면)에 얹힌 흰 렌즈.
    Lr = [r[0] for r in reversed(ROOF)]
    ra = Pchip(Lr, [r[1] for r in reversed(ROOF)])
    rp = Pchip(Lr, [r[2] for r in reversed(ROOF)])
    roof = []
    for L in cosine_samples(ROOF[0][0], ROOF[-1][0], 24):
        on_glass = CANOPY[-1][0] < L < CANOPY[0][0]
        base = (glass_peak(L) if on_glass else TUB.top(L, 0)) - 0.04
        peak = max(rp(L), base + 0.045)
        roof.append(ring(L, max(ra(L), 0.004), base, peak - base, 0.01, 2.2, N=32))
    add_mesh("canopy_roof", loft(roof), ["paint_white"], parent="piv_canopy", part="canopy", sharp=None)

    # 실내
    seat = merge(box((0, 0.20, 0.335), (0.36, 0.40, 0.07), bevel=0.03),
                 box((0, -0.02, 0.50), (0.36, 0.07, 0.36), bevel=0.03, tilt=22),
                 box((0, -0.07, 0.70), (0.22, 0.08, 0.12), bevel=0.03, tilt=22))
    add_mesh("seat", seat, ["seat"], part="cockpit", sharp=50)
    add_mesh("steering", merge(torus((0, 0.58, 0.52), 0.09, 0.014, tilt=-12),
                               box((0, 0.65, 0.50), (0.03, 0.14, 0.03), tilt=-12),
                               box((0, 0.58, 0.52), (0.12, 0.02, 0.05), bevel=0.01, tilt=-12)),
             ["carbon"], part="cockpit", sharp=None)
    add_mesh("dash", box((0, 0.80, 0.50), (0.36, 0.12, 0.12), bevel=0.02), ["carbon"], part="cockpit", sharp=50)
    add_mesh("ai_screen", box((0, 0.735, 0.52), (0.24, 0.008, 0.08), tilt=-18), ["ai_screen"],
             part="cyber_system", sharp=50)


def panel_wing(sections, flip=False):
    """(|x|, 앞전 L, 뒷전 L, 가운데 높이, 두께) 단면 사이를 대칭 익형으로 이은 테이퍼 날개."""
    s = -1 if flip else 1
    rings = []
    for x, Lle, Lte, zm, th in sections:
        c = Lle - Lte
        rings.append([P(s * x, Lle - u * c, zm + v * c) for u, v in airfoil(t=th / c, camber=0.0)])
    return loft(rings)


def lean_plate(poly_Lz, line, thick, s):
    """옆모습 윤곽을 두께만큼 돌출한 뒤 높이에 따라 가로로 민다(정면에서 line(z)를 따라 기운 판)."""
    verts, faces = plate(poly_Lz, 0.0, thick, bevel=0.008)
    return [Vector((s * (v.x + line(v.z)), v.y, v.z)) for v in verts], faces


def build_rear():
    Ls = [r[0] for r in reversed(NACELLE)]
    fr = Pchip(Ls, [r[1] for r in reversed(NACELLE)])
    fz = Pchip(Ls, [r[2] for r in reversed(NACELLE)])
    (wx0, wLe0, wTe0, wz0, _), (wx1, wLe1, wTe1, wz1, _) = REAR_WING
    for side, s in (("L", 1), ("R", -1)):
        x0 = s * NACELLE_X
        # 부스트 나셀: 파란 페어링(고정) + 빨간 슈라우드·노즐·터빈(부스트 때 뒤로 밀림)
        fair = []
        for L in cosine_samples(NACELLE[0][0], NACELLE[-1][0], 28):
            r = max(fr(L), 0.004)
            fair.append(ring(L, r, fz(L), r, r, 2.0, N=48, x0=x0))
        add_mesh(f"pod_fairing_{side}", loft(fair), ["paint_blue"], part="boost_pods", sharp=None)
        piv = f"piv_pod_{side}"
        empty(piv, x0, -2.00, NACELLE_Z, parent="chassis", part="boost_pods")
        shroud = [(-1.80, 0.247), (-1.86, 0.247), (-1.88, 0.268), (-2.10, 0.275), (-2.25, 0.258),
                  (-2.36, 0.238), (-2.36, 0.220), (-2.10, 0.246), (-1.82, 0.240)]
        add_mesh(f"boost_shroud_{side}", revolve(shroud, (x0, NACELLE_Z), "L", seg=56), ["paint_red"],
                 parent=piv, part="boost_pods", sharp=45)
        nozzle = [(-2.12, 0.240), (-2.36, 0.224), (-2.42, 0.190), (-2.47, 0.140), (-2.47, 0.122), (-2.40, 0.172),
                  (-2.14, 0.222)]
        spike = [(-2.15, 0.0), (-2.15, 0.130), (-2.32, 0.090), (-2.44, 0.0)]
        add_mesh(f"boost_nozzle_{side}", merge(revolve(nozzle, (x0, NACELLE_Z), "L", seg=48),
                                               revolve(spike, (x0, NACELLE_Z), "L", seg=32)),
                 ["chrome"], parent=piv, part="boost_pods", sharp=45)
        blades = [transform_geo(box((0, 0, 0), (0.010, 0.028, 0.105)),
                                Matrix.Translation(P(x0, -2.17, NACELLE_Z)) @ Matrix.Rotation(math.radians(k * 30), 4, "Y")
                                @ Matrix.Translation(Vector((0, 0, 0.175))) @ Matrix.Rotation(math.radians(30), 4, "Z"))
                  for k in range(12)]
        add_mesh(f"boost_blades_{side}", merge(*blades), ["metal_dark"], parent=piv, part="boost_pods", sharp=50)
        add_mesh(f"boost_glow_{side}", revolve([(-2.165, 0.120), (-2.165, 0.240), (-2.17, 0.240), (-2.17, 0.120)],
                                               (x0, NACELLE_Z), "L", seg=40),
                 ["glow_boost"], parent=piv, part="boost_pods", smooth=False)

        # 리어 윙: 나셀에서 핀 포드까지 15° 들린 날개. 윗면 뒷전은 노랑.
        def wing_mat(x, L, z, p=None):
            t = (abs(x) - wx0) / (wx1 - wx0)
            zm, te = wz0 + (wz1 - wz0) * t, wTe0 + (wTe1 - wTe0) * t
            return YELLOW if z > zm and L < te + 0.15 else BLUE
        add_mesh(f"rear_wing_{side}", panel_wing(REAR_WING, flip=(s < 0)), PAINT, part="tail_fins",
                 face_mat=wing_mat, sharp=40)

        # 테일 핀 묶음(포드 + 바깥 판 + 안쪽 판): 에어로 모드에서 함께 솟는다. 피벗은 포드 바닥 가운데.
        fp = f"piv_tailfin_{side}"
        empty(fp, s * 1.18, -1.70, 0.94, parent="chassis", part="tail_fins")

        def pod_mat(x, L, z, p=None):
            """패널: 0 바닥, 1 바깥, 2 윗면, 3 안쪽 윗모서리, 4 안쪽. 윗면 뒤쪽은 노랑."""
            if p == 0:
                return CARBON
            return YELLOW if p == 2 and L < -1.65 else BLUE
        add_mesh(f"fin_pod_{side}", POD.geo(30, flip=(s < 0)), PAINT, parent=fp, part="tail_fins",
                 face_mat=pod_mat, sharp=40)
        blue = lean_plate(FIN_BLUE, blade_x, 0.045, s)
        yellow = lean_plate(FIN_YELLOW, blade_x, 0.045, s)
        fin = merge(blue, transform_geo(yellow, Matrix.Translation(Vector((0, 0, 0.0005)))))
        n_blue = len(blue[1])
        add_mesh(f"tailfin_{side}", (fin[0], fin[1], [1 if i >= n_blue else 0 for i in range(len(fin[1]))]),
                 ["paint_blue", "paint_yellow"], parent=fp, part="tail_fins", face_mat=lambda x, L, z, p=None: p, sharp=40)
        add_mesh(f"fin_strip_{side}", lean_plate(FIN_STRIP, strip_x, 0.03, s), ["paint_blue"], parent=fp,
                 part="tail_fins", sharp=40)

        # 에어 브레이크: 스폰슨 윗면 뒤쪽 패널. 뒷전 힌지, 앞쪽이 들려 바람을 받는다.
        ax, aL = 0.66, -0.55
        az = SIDE.top(aL, ax) + 0.002
        empty(f"piv_airbrake_{side}", s * ax, aL - 0.20, az, parent="chassis", part="airbrake")
        add_mesh(f"airbrake_{side}", box((s * ax, aL, az + 0.007), (0.26, 0.40, 0.014), bevel=0.005),
                 ["paint_blue", "paint_red"], parent=f"piv_airbrake_{side}", part="airbrake",
                 face_mat=lambda x, L, z, p=None, az=az: 1 if z < az + 0.006 else 0, sharp=50)


def wheel_meshes(R, W, disc):
    Rin = R * 0.66
    rc, ar, aw = (R + Rin) / 2, (R - Rin) / 2 + 0.004, W / 2
    tire_prof = [(aw * math.copysign(abs(math.sin(t)) ** 0.45, math.sin(t)),
                  rc + ar * math.copysign(abs(math.cos(t)) ** 0.5, math.cos(t)))
                 for t in (2 * math.pi * i / 24 for i in range(24))]

    def to_mesh(name, geo, mats, sharp):
        verts, faces = geo
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], [tuple(f) for f in faces])
        me.validate()
        for mn in mats:
            me.materials.append(mat(mn))
        finish(me, sharp=sharp)
        return me

    tire = to_mesh(f"tire_{R}", revolve(tire_prof, (0, 0, 0), "X", seg=56), ["tire"], 60)
    if disc:
        # 뒷바퀴: 흰 에어로 디스크(양면이 살짝 볼록)
        prof = [(-0.44 * W, 0.985 * Rin), (0.44 * W, 0.985 * Rin), (0.44 * W, 0.94 * Rin), (0.40 * W, 0.60 * Rin),
                (0.38 * W, 0.18 * Rin), (0.38 * W, 0.02), (-0.38 * W, 0.02), (-0.38 * W, 0.18 * Rin),
                (-0.40 * W, 0.60 * Rin), (-0.44 * W, 0.94 * Rin)]
        rim = to_mesh(f"rim_{R}", revolve(prof, (0, 0, 0), "X", seg=64), ["paint_white"], 40)
        hub = to_mesh(f"hub_{R}", revolve([(-0.42 * W, 0.01), (0.42 * W, 0.01), (0.42 * W, 0.15 * Rin), (-0.42 * W, 0.15 * Rin)],
                                          (0, 0, 0), "X", seg=24), ["paint_red"], 40)
        return tire, rim, hub
    rim_prof = [(-0.44 * W, 0.985 * Rin), (0.44 * W, 0.985 * Rin), (0.44 * W, 0.93 * Rin), (0.30 * W, 0.86 * Rin),
                (0.26 * W, 0.30 * Rin), (0.30 * W, 0.15 * Rin), (0.30 * W, 0.03),
                (-0.30 * W, 0.03), (-0.30 * W, 0.15 * Rin), (-0.26 * W, 0.30 * Rin), (-0.30 * W, 0.86 * Rin),
                (-0.44 * W, 0.93 * Rin)]
    spokes = []
    for k in range(6):
        a = 2 * math.pi * k / 6
        for sx in (1, -1):
            v, f = box((0, 0, 0), (0.03, 0.026, 0.70 * Rin), bevel=0.006, segments=2)
            rot = Matrix.Rotation(a, 4, "X")
            off = Vector((sx * 0.29 * W, 0, 0.51 * Rin))
            spokes.append(([rot @ (Vector((p.x, p.y, p.z)) + off) for p in v], f))
    rim = to_mesh(f"rim_{R}", merge(revolve(rim_prof, (0, 0, 0), "X", seg=56), *spokes), ["rim"], 40)
    hub = to_mesh(f"hub_{R}", revolve([(-0.36 * W, 0.01), (0.36 * W, 0.01), (0.36 * W, 0.20 * Rin), (-0.36 * W, 0.20 * Rin)],
                                      (0, 0, 0), "X", seg=24), ["metal_dark"], 40)
    return tire, rim, hub


def build_wheels():
    cache = {}
    for tag, L, ax, R, W, part in WHEELS:
        key = (R, W)
        if key not in cache:
            cache[key] = wheel_meshes(R, W, disc=(part == "rear_wheels"))
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
    buffers = doc.get("buffers", []) if doc else []
    # GLB 본문은 항상 첫 버퍼다. meshopt 압축 파일은 압축 해제 크기만 적힌 대체(fallback) 버퍼를 더 갖는데,
    # 데이터가 없으므로 그대로 둔다.
    extra = [b for b in buffers[1:] if not b.get("extensions", {}).get("EXT_meshopt_compression", {}).get("fallback")]
    if doc is None or binary is None or not buffers or "uri" in buffers[0] or extra:
        raise ValueError("본문 버퍼 하나(+meshopt 대체 버퍼)만 있는 GLB만 변환합니다.")
    buffers[0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode("ascii")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- 미리보기

def render_preview(path, views=None):
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
    views = views or [("hero", (3.8, -5.4, 2.4), 40), ("side", (7.5, 0, 0.7), 40),
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
    build_tub()
    build_engine()
    build_sides()
    build_front()
    build_cockpit()
    build_rear()
    build_wheels()

    objs = list(COL.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)

    import bx_audit
    gate = bx_audit.deliverable([o.name for o in meshes], profile="render")
    inside_out = [e for e in gate["errors"] if "inside out" in e]
    print("AUDIT_ERRORS", len(gate["errors"]))
    for e in gate["errors"][:60]:
        if "floating" not in e:
            print("  -", e)

    if opts["glb"]:
        import bx_export as bxe
        os.makedirs(os.path.dirname(opts["glb"]), exist_ok=True)
        bxe.export(opts["glb"], "gltf", [o.name for o in objs], export_extras=True)
        rt = bxe.round_trip(opts["glb"], [o.name for o in meshes], preset="gltf")
        print("ROUNDTRIP_DIFFS", rt.get("diffs"))
        if opts["web"] or opts["web_glb"]:
            packed = opts["web_glb"] or os.path.join(os.path.dirname(opts["glb"]), "_web_tmp.glb")
            os.makedirs(os.path.dirname(packed), exist_ok=True)
            bxe.export(packed, "gltf", [o.name for o in objs], export_extras=True,
                       export_meshopt_compression_enable=True, export_meshopt_extension="EXT_meshopt_compression")
            print("WEB_GLB", packed, os.path.getsize(packed), bxe.glb_summary(packed).get("extensions_required"))
            if opts["web"]:
                os.makedirs(os.path.dirname(opts["web"]), exist_ok=True)
                glb_to_embedded_json(packed, opts["web"])
                print("WEB_GLTF", opts["web"], os.path.getsize(opts["web"]))
            if not opts["web_glb"]:
                os.remove(packed)
    if opts["blend"]:
        os.makedirs(os.path.dirname(opts["blend"]), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=opts["blend"])
    if opts["preview"]:
        render_preview(opts["preview"])
    print(f"RESULT objects={len(objs)} meshes={len(meshes)} tris={tris} inside_out={len(inside_out)}")


if __name__ == "__main__":
    main()
