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


def conform_lens(poly_xL, lf, lift_top, lift_bot, flip=False):
    """로프트 윗면을 따라 붙는 얇은 렌즈(헤드라이트 등). 윤곽은 체이킨으로 둥글린다."""
    pts, _ = chaikin_closed(poly_xL, [False] * len(poly_xL), 3)
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

# 바퀴: 이름, L, |x|, 반지름 R, 폭 W, 파츠
WHEELS = [
    ("F1", 1.38, 0.70, 0.25, 0.22, "front_wheels"),
    ("F2", 0.86, 0.72, 0.25, 0.22, "front_wheels"),
    ("R", -1.52, 0.86, 0.36, 0.40, "rear_wheels"),
]

# 메인 모노코크(노즈~꼬리). 오른쪽 절반 제어점: 아래 중앙, 아래 모서리*, 옆 최대폭, 숄더*, 덱 바깥, 덱 가운데, 위 중앙
TUB = SectionLoft([
    (2.30, [(0, .200), (.012, .200), (.018, .210), (.018, .225), (.014, .232), (.007, .235), (0, .236)]),
    (2.18, [(0, .170), (.050, .170), (.075, .205), (.078, .255), (.065, .280), (.033, .290), (0, .292)]),
    (1.95, [(0, .140), (.100, .140), (.150, .200), (.155, .290), (.130, .345), (.065, .365), (0, .368)]),
    (1.60, [(0, .120), (.160, .120), (.240, .210), (.260, .350), (.230, .430), (.115, .455), (0, .460)]),
    (1.10, [(0, .105), (.200, .105), (.300, .210), (.320, .400), (.290, .485), (.145, .510), (0, .515)]),
    (0.50, [(0, .100), (.220, .100), (.340, .210), (.360, .430), (.330, .520), (.165, .545), (0, .550)]),
    (-0.10, [(0, .100), (.220, .100), (.340, .210), (.360, .460), (.320, .570), (.160, .610), (0, .620)]),
    (-0.60, [(0, .110), (.220, .110), (.320, .210), (.340, .470), (.300, .590), (.150, .650), (0, .660)]),
    (-1.20, [(0, .120), (.200, .120), (.280, .215), (.290, .450), (.250, .560), (.125, .610), (0, .620)]),
    (-1.80, [(0, .150), (.150, .150), (.210, .225), (.215, .410), (.180, .490), (.090, .520), (0, .525)]),
    (-2.08, [(0, .200), (.080, .200), (.120, .255), (.120, .390), (.100, .435), (.050, .452), (0, .455)]),
    (-2.18, [(0, .300), (.020, .300), (.030, .315), (.030, .365), (.025, .375), (.012, .380), (0, .381)]),
], sharp=[False, True, False, True, False, False, False])


def side_ctrl(xi, xo, zb, zt):
    """사이드 바디 단면(오른쪽, 닫힌 다각형): 안쪽 아래, 바깥 아래*, 바깥 최대폭, 숄더*, 윗면 바깥, 윗면 안쪽, 안쪽 위."""
    w, h = xo - xi, zt - zb
    return [(xi, zb), (xo - 0.15 * w, zb), (xo, zb + 0.28 * h), (xo - 0.04 * w, zt - 0.22 * h),
            (xo - 0.30 * w, zt), (xi + 0.15 * w, zt + 0.02 * h), (xi, zt - 0.25 * h)]


SIDE = SectionLoft([(L, side_ctrl(*v)) for L, v in [
    (0.64, (0.28, 0.60, 0.14, 0.42)),
    (0.56, (0.28, 0.76, 0.12, 0.49)),
    (0.42, (0.28, 0.83, 0.10, 0.54)),
    (0.00, (0.28, 0.86, 0.10, 0.58)),
    (-0.50, (0.28, 0.86, 0.10, 0.61)),
    (-0.86, (0.28, 0.75, 0.11, 0.61)),
    (-1.04, (0.28, 0.62, 0.13, 0.59)),
    (-1.18, (0.26, 0.48, 0.17, 0.55)),
    (-1.28, (0.24, 0.32, 0.22, 0.50)),
]], sharp=[True, True, False, True, False, False, True], mirror=False)


def cowl_ctrl(w, zb, zte, ztc):
    """프런트 카울 단면(오른쪽 절반): 아래 중앙, 아래 바깥*, 앞전, 위 바깥*, 위 가운데, 위 중앙."""
    return [(0, zb), (w * 0.96, zb), (w, (zb + zte) / 2), (w * 0.96, zte), (w * 0.5, zte + (ztc - zte) * 0.75), (0, ztc)]


COWL = SectionLoft([(L, cowl_ctrl(*v)) for L, v in [
    (2.43, (0.02, 0.085, 0.100, 0.110)),
    (2.33, (0.22, 0.075, 0.115, 0.150)),
    (2.20, (0.48, 0.070, 0.130, 0.190)),
    (2.08, (0.70, 0.065, 0.145, 0.230)),
    (1.98, (0.84, 0.065, 0.160, 0.260)),
    (1.80, (0.84, 0.070, 0.175, 0.300)),
    (1.68, (0.84, 0.080, 0.185, 0.320)),
]], sharp=[False, True, False, True, False, False])


def horn_ctrl(xc, zl, zh, t, lean=8.0):
    """블레이드 혼 단면(얇은 렌즈, 바깥으로 기움): 아래*, 바깥, 위*, 안쪽."""
    k = math.tan(math.radians(lean))
    zm = (zl + zh) / 2
    return [(xc, zl), (xc + t / 2 + (zm - zl) * k, zm), (xc + (zh - zl) * k, zh), (xc - t / 2 + (zm - zl) * k, zm)]


HORN = SectionLoft([(L, horn_ctrl(*v)) for L, v in [
    (2.54, (0.700, 0.150, 0.165, 0.006)),
    (2.44, (0.780, 0.115, 0.235, 0.018)),
    (2.30, (0.835, 0.085, 0.295, 0.028)),
    (2.15, (0.858, 0.062, 0.335, 0.034)),
    (2.02, (0.865, 0.050, 0.360, 0.038)),
    (1.82, (0.865, 0.048, 0.365, 0.038)),
    (1.66, (0.865, 0.060, 0.330, 0.034)),
    (1.60, (0.865, 0.090, 0.280, 0.010)),
]], sharp=[True, False, True, False], mirror=False)

# 캐노피: (L, 반폭, 꼭대기 높이)
CANOPY = [(1.76, 0.020, 0.0), (1.56, 0.120, 0.52), (1.20, 0.220, 0.66), (0.80, 0.270, 0.76), (0.40, 0.290, 0.83),
          (0.10, 0.280, 0.86), (-0.06, 0.240, 0.82), (-0.16, 0.120, 0.68), (-0.20, 0.020, 0.0)]

POD_X, POD_Z = 0.205, 0.575
ENGINE_BAY = (-0.35, -1.29)  # 엔진룸 앞뒤 L


# ---------------------------------------------------------------- 파츠

BLUE, WHITE, RED, CARBON = 0, 1, 2, 3
PAINT = ["paint_blue", "paint_white", "paint_red", "carbon"]


def tub_mat(x, L, z, p=None):
    """패널: 0 오른쪽 옆면, 1 윗면, 2 왼쪽 옆면, 3 바닥, -1 끝 뚜껑."""
    if p == 3:
        return CARBON
    if p in (1, -1):
        return BLUE
    if p in (0, 2):
        return BLUE if L > 2.0 else WHITE
    if z < 0.16:
        return CARBON
    if L > 1.62:
        return BLUE if z > 0.30 or L > 2.05 else WHITE
    if L > -0.30:
        return BLUE if z > 0.44 else WHITE
    if z > 0.45:
        return WHITE if abs(x) < 0.07 else BLUE
    return WHITE


def build_tub():
    tub = add_mesh("body", TUB.geo(96), PAINT, part="body", face_mat=tub_mat, sharp=40)

    # 콕핏: 둥근 상자를 빼서 시트가 들어갈 욕조 모양을 만든다.
    cockpit = add_mesh("cut_cockpit", box((0, 0.565, 0.75), (0.46, 1.17, 0.90), bevel=0.09, segments=4),
                       [], parent=None, smooth=False)
    boolean(tub, cockpit, "DIFFERENCE")
    bpy.data.objects.remove(cockpit)

    # 엔진룸: 같은 상자로 덮개 조각(교집합)을 먼저 떼어 내고, 차체에서는 빼낸다.
    L1, L0 = ENGINE_BAY
    bay_geo = box((0, (L1 + L0) / 2, 0.75), (0.42, L1 - L0, 0.90), bevel=0.06, segments=3)
    cap_geo = box((0, (L1 + L0) / 2, 0.865), (0.42, L1 - L0, 0.67), bevel=0.06, segments=3)  # z 0.53 위
    cover = add_mesh("engine_cover", None, None, part="engine", mesh_data=tub.data.copy())
    cap = add_mesh("cut_cap", cap_geo, [], parent=None, smooth=False)
    boolean(cover, cap, "INTERSECT")
    bpy.data.objects.remove(cap)
    cutter = add_mesh("cut_bay", bay_geo, [], parent=None, smooth=False)
    boolean(tub, cutter, "DIFFERENCE")
    bpy.data.objects.remove(cutter)

    def tub_mat_cut(x, L, z, p=None):
        if abs(x) < 0.235 and -0.03 < L < 1.16 and z < 0.505:
            return CARBON  # 콕핏 안쪽
        if abs(x) < 0.215 and L0 - 0.01 < L < L1 + 0.01 and z < 0.56:
            return CARBON  # 엔진룸 벽
        return tub_mat(x, L, z, p)

    finish(tub.data, tub_mat_cut, sharp=40)

    # 덮개: 앞쪽 힌지 피벗 아래로 옮기고, 가장자리에 이음매 틈이 보이도록 살짝 줄인다.
    hinge_z = TUB.top(L1, 0) - 0.01
    empty("piv_engine_cover", 0, L1, hinge_z, parent="chassis", part="engine")
    me = cover.data
    me.transform(Matrix.Translation(P(0, (L1 + L0) / 2, 0)) @ Matrix.Diagonal((0.985, 0.992, 1.0, 1.0))
                 @ Matrix.Translation(-P(0, (L1 + L0) / 2, 0)))
    finish(me, lambda x, L, z, p=None: CARBON if z < 0.56 else tub_mat(x, L, z, p), sharp=40)
    rebase(cover, "piv_engine_cover")


def build_engine():
    """엔진룸 안의 V형 엔진: 블록, 좌우 뱅크, 금색 캠 커버, 흡기 트럼펫, 은색 배기 헤더."""
    L1, L0 = ENGINE_BAY
    Lc, length = (L1 + L0) / 2, (L1 - L0) - 0.12
    parts = [box((0, Lc, 0.365), (0.24, length, 0.12), bevel=0.02)]
    gold, chrome = [], []
    up = Matrix.Rotation(math.radians(-90), 4, "Y")  # 회전체의 +x 축을 위(+z)로
    for s in (1, -1):
        parts.append(box((s * 0.085, Lc, 0.435), (0.095, length - 0.04, 0.10), bevel=0.015, roll=s * 32))
        gold.append(box((s * 0.112, Lc, 0.478), (0.068, length - 0.08, 0.03), bevel=0.011, roll=s * 32))
        for k in range(7):
            Lk = L1 - 0.10 - k * (length - 0.16) / 6
            chrome.append(box((s * 0.116, Lk, 0.496), (0.072, 0.012, 0.010), bevel=0.003, roll=s * 32))
        for k in range(6):
            Lk = L1 - 0.13 - k * (length - 0.22) / 5
            chrome.append(transform_geo(revolve([(0.0, 0.010), (0.07, 0.017), (0.07, 0.013), (0.0, 0.007)],
                                                (0, 0, 0), "X", seg=12),
                                        Matrix.Translation(P(s * 0.03, Lk, 0.45)) @ up))
    add_mesh("engine_block", merge(*parts), ["metal_dark"], part="engine", sharp=45)
    add_mesh("engine_cams", merge(*gold), ["gold"], part="engine", sharp=45)
    add_mesh("engine_trumpets", merge(*chrome), ["chrome"], part="engine", sharp=50)

    # 배기: 뱅크 바깥에서 나와 꼬리의 6연장 배기구로 모인다.
    exits = [(-0.07, 0.235), (-0.07, 0.305), (0.0, 0.305), (0.07, 0.235), (0.07, 0.305), (0.0, 0.235)]
    pipes = []
    for i, (ex, ez) in enumerate(exits):
        s = -1 if i < 3 else 1
        Lh = L1 - 0.18 - (i % 3) * 0.24
        k = i % 3
        ctrl = [P(s * 0.15, Lh, 0.42), P(s * 0.185, Lh - 0.06, 0.37), P(s * (0.16 - 0.035 * k), L0 + 0.05, 0.30 + 0.045 * k),
                P(ex, -1.70, ez), P(ex, -2.10, ez)]
        pipes.append(curve_tube(catmull(ctrl, per=8), 0.021, closed=False))
        pipes.append(revolve([(-2.07, 0.030), (-2.21, 0.031), (-2.21, 0.024), (-2.07, 0.023)], (ex, ez), "L", seg=24))
    add_mesh("exhaust", merge(*pipes), ["chrome"], part="engine", sharp=50)
    add_mesh("exhaust_dark", merge(*[revolve([(-2.12, 0.0), (-2.12, 0.024), (-2.14, 0.024), (-2.14, 0.0)],
                                             (ex, ez), "L", seg=16) for ex, ez in exits]),
             ["intake"], part="engine", smooth=False)


def build_sides():
    for side, s in (("L", 1), ("R", -1)):
        def side_mat(x, L, z, p=None):
            """패널: 0 바닥, 1 바깥 옆면, 2 윗면, 3 안쪽, -1 앞뒤 뚜껑."""
            if p == 0:
                return CARBON
            if p == 2:
                return RED if 0.46 < L < 0.60 else BLUE
            return WHITE
        add_mesh(f"sidepod_{side}", SIDE.geo(44, flip=(s < 0)), PAINT, part="sidepods", face_mat=side_mat, sharp=40)

        # 앞쪽 흡기구: 앞면 단면을 줄여 어두운 면을 덧댄다.
        r = SIDE.ring2d(0.64)
        cx = sum(p[0] for p in r) / len(r)
        cz = sum(p[1] for p in r) / len(r)
        inner = [P(s * (cx + (x - cx) * 0.78), 0.646, cz + (z - cz) * 0.72) for x, z in r]
        inner2 = [P(s * (cx + (x - cx) * 0.74), 0.642, cz + (z - cz) * 0.68) for x, z in r]
        add_mesh(f"sidepod_intake_{side}", loft([inner2, inner]), ["intake"], part="sidepods", sharp=None)

        # 옆구리 루버: 어두운 판 + 가로 날개 5장
        Lc = -0.62
        xo = max(x for x, z in SIDE.ring2d(Lc) if 0.2 < z < 0.35)
        louver = [box((s * (xo - 0.004), Lc, 0.30), (0.02, 0.40, 0.11), bevel=0.006)]
        add_mesh(f"louver_{side}", merge(*louver), ["intake"], part="sidepods", sharp=50)
        slats = [box((s * (xo + 0.004), Lc, 0.258 + k * 0.021), (0.018, 0.38, 0.007), bevel=0.002, roll=s * 20)
                 for k in range(5)]
        add_mesh(f"louver_slats_{side}", merge(*slats), ["metal_dark"], part="sidepods", sharp=50)

        # 윗면 냉각 팬: 어두운 원판, 날개 12장, 은색 허브
        fx, fL = 0.50, -0.20
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

        # 사이드 바디 앞의 작은 빨간 베인
        add_mesh(f"vane_{side}", plate([(0.60, 0.46), (0.47, 0.58), (0.40, 0.58), (0.50, 0.46)], s * 0.33, 0.012, bevel=0.003),
                 ["paint_red"], part="sidepods", sharp=40)

    # 플로어와 디퓨저
    floor = SectionLoft([(L, [(0, .058), (w * .97, .058), (w, .070), (w * .97, .082), (0, .082)]) for L, w in [
        (0.66, 0.30), (0.58, 0.70), (0.40, 0.80), (-0.85, 0.80), (-1.02, 0.62), (-1.12, 0.40)]],
        sharp=[False, True, False, True, False], iters=2)
    add_mesh("floor", floor.geo(30), ["carbon"], part="underbody", sharp=50)
    strakes = merge(*[plate([(-1.30, 0.07), (-2.06, 0.07), (-2.12, 0.24), (-1.75, 0.20)], x, 0.014)
                      for x in (-0.32, -0.14, 0.14, 0.32)])
    add_mesh("diffuser", strakes, ["carbon"], part="underbody", sharp=50)
    add_mesh("rain_light", box((0, -2.19, 0.36), (0.06, 0.014, 0.03), bevel=0.004), ["lamp_red"], part="underbody", sharp=50)


def build_front():
    def cowl_mat(x, L, z, p=None):
        """패널: 0·2 앞전, 1 윗면, 3 바닥. 윗면 줄무늬와 번호는 뷰어가 데칼로 입힌다."""
        return {1: WHITE, 3: CARBON}.get(p, BLUE)
    add_mesh("front_cowl", COWL.geo(32), PAINT, part="front_cowl", face_mat=cowl_mat, sharp=40)
    for side, s in (("L", 1), ("R", -1)):
        add_mesh(f"horn_{side}", HORN.geo(28, flip=(s < 0)), ["paint_blue"], part="front_cowl", sharp=40)
        # 헤드라이트: 노즈 양옆의 노란 렌즈
        add_mesh(f"headlight_{side}", conform_lens([(0.13, 2.18), (0.18, 2.15), (0.32, 1.88), (0.27, 1.84), (0.15, 2.04)],
                                                   COWL, 0.016, -0.004, flip=(s < 0)),
                 ["lamp_yellow"], part="nose", sharp=60)
        # 카울 뒷전 플랩(에어로 모드에서 각도를 키운다)
        empty(f"piv_front_flap_{side}", s * 0.42, 1.70, 0.215, parent="chassis", part="front_cowl")
        add_mesh(f"front_flap_{side}", wing(s * 0.28, s * 0.56, 0.12, 1.70, 0.215, 12, t=0.10),
                 ["paint_blue"], parent=f"piv_front_flap_{side}", part="front_cowl", sharp=40)


def build_cockpit():
    # 캐노피: 앞쪽 힌지, 뒤쪽이 들린다. 밑단은 차체 윗면 곡선에 맞춘다.
    Lh = CANOPY[0][0]
    empty("piv_canopy", 0, Lh, TUB.top(Lh, 0), parent="chassis", part="canopy")
    Ls = [r[0] for r in reversed(CANOPY)]
    fa = Pchip(Ls, [r[1] for r in reversed(CANOPY)])
    ft = Pchip(Ls, [r[2] for r in reversed(CANOPY)])
    nc, M = 2.4, 27
    rings, rim_l, rim_r = [], [], []
    for L in cosine_samples(CANOPY[0][0], CANOPY[-1][0], 36):
        a = max(fa(L), 0.004)
        base_c = TUB.top(L, 0)
        peak = max(ft(L), base_c + 0.004)
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
    add_mesh("canopy_frame", curve_tube(rim_l + list(reversed(rim_r)), 0.016), ["paint_blue"],
             parent="piv_canopy", part="canopy", sharp=None)

    # 실내
    seat = merge(box((0, 0.20, 0.335), (0.36, 0.40, 0.07), bevel=0.03),
                 box((0, -0.01, 0.50), (0.36, 0.07, 0.36), bevel=0.03, tilt=22),
                 box((0, -0.06, 0.70), (0.20, 0.08, 0.12), bevel=0.03, tilt=22))
    add_mesh("seat", seat, ["seat"], part="cockpit", sharp=50)
    add_mesh("steering", merge(torus((0, 0.66, 0.50), 0.09, 0.014, tilt=-12),
                               box((0, 0.73, 0.48), (0.03, 0.14, 0.03), tilt=-12),
                               box((0, 0.66, 0.50), (0.12, 0.02, 0.05), bevel=0.01, tilt=-12)),
             ["carbon"], part="cockpit", sharp=None)
    add_mesh("dash", box((0, 0.86, 0.47), (0.40, 0.14, 0.12), bevel=0.02), ["carbon"], part="cockpit", sharp=50)
    add_mesh("ai_screen", box((0, 0.785, 0.49), (0.24, 0.008, 0.075), tilt=-18), ["ai_screen"],
             part="cyber_system", sharp=50)


def build_rear():
    # 리어 덱: 꼬리 위를 가로질러 테일 핀 받침과 부스트 나셀을 받친다.
    deck = SectionLoft([(L, [(0, .50 + d), (w * .93, .50 + d), (w, .555), (w * .93, .61 - d * .5), (w * .5, .622 - d * .5),
                             (0, .628 - d * .5)]) for L, w, d in [
        (-1.20, 0.30, 0.06), (-1.28, 0.62, 0.02), (-1.45, 0.645, 0.0), (-1.90, 0.645, 0.0), (-2.04, 0.60, 0.02),
        (-2.10, 0.40, 0.05)]], sharp=[False, True, False, True, False, False], iters=2)
    add_mesh("rear_deck", deck.geo(24), PAINT, part="tail_fins",
             face_mat=lambda x, L, z, p=None: CARBON if p == 3 else BLUE, sharp=40)
    for side, s in (("L", 1), ("R", -1)):
        x0 = s * POD_X
        # 부스트 나셀: 파란 페어링(고정) + 빨간 슈라우드·노즐·터빈(부스트 때 뒤로 밀림)
        fair = [ring(L, r, POD_Z, r, r, 2.0, N=48, x0=x0) for L, r in
                [(-1.26, 0.02), (-1.30, 0.07), (-1.36, 0.115), (-1.43, 0.148), (-1.50, 0.158)]]
        add_mesh(f"pod_fairing_{side}", loft(fair), ["paint_blue"], part="boost_pods", sharp=None)
        piv = f"piv_pod_{side}"
        empty(piv, x0, -1.75, POD_Z, parent="chassis", part="boost_pods")
        shroud = [(-1.38, 0.150), (-1.47, 0.150), (-1.49, 0.161), (-2.00, 0.163), (-2.08, 0.152),
                  (-2.08, 0.137), (-1.80, 0.141), (-1.40, 0.140)]
        add_mesh(f"boost_shroud_{side}", revolve(shroud, (x0, POD_Z), "L", seg=56), ["paint_red"],
                 parent=piv, part="boost_pods", sharp=45)
        nozzle = [(-1.80, 0.140), (-2.05, 0.122), (-2.05, 0.111), (-1.82, 0.129)]
        spike = [(-1.84, 0.0), (-1.84, 0.074), (-1.97, 0.052), (-2.07, 0.0)]
        add_mesh(f"boost_nozzle_{side}", merge(revolve(nozzle, (x0, POD_Z), "L", seg=48),
                                               revolve(spike, (x0, POD_Z), "L", seg=32)),
                 ["chrome"], parent=piv, part="boost_pods", sharp=45)
        blades = [transform_geo(box((0, 0, 0), (0.008, 0.02, 0.064)),
                                Matrix.Translation(P(x0, -1.86, POD_Z)) @ Matrix.Rotation(math.radians(k * 30), 4, "Y")
                                @ Matrix.Translation(Vector((0, 0, 0.106))) @ Matrix.Rotation(math.radians(30), 4, "Z"))
                  for k in range(12)]
        add_mesh(f"boost_blades_{side}", merge(*blades), ["metal_dark"], parent=piv, part="boost_pods", sharp=50)
        add_mesh(f"boost_glow_{side}", revolve([(-1.835, 0.07), (-1.835, 0.139), (-1.84, 0.139), (-1.84, 0.07)],
                                               (x0, POD_Z), "L", seg=40),
                 ["glow_boost"], parent=piv, part="boost_pods", smooth=False)

        # 테일 핀: 흰 받침 블록(고정) + 파란 핀(에어로 모드에서 솟는다)
        add_mesh(f"tailfin_base_{side}", box((s * 0.57, -1.68, 0.615), (0.13, 0.86, 0.13), bevel=0.03),
                 ["paint_white"], part="tail_fins", sharp=40)
        empty(f"piv_tailfin_{side}", s * 0.575, -1.68, 0.66, parent="chassis", part="tail_fins")
        fin = plate([(-1.24, 0.64), (-1.62, 0.84), (-2.04, 1.29), (-2.20, 1.36), (-2.47, 1.33), (-2.33, 1.08),
                     (-2.14, 0.66)], s * 0.575, 0.045, bevel=0.010)
        fin = lean_geo(fin, s * 0.575, -1.68, 0.66, s * 12)
        add_mesh(f"tailfin_{side}", fin, ["paint_blue", "paint_white"], parent=f"piv_tailfin_{side}", part="tail_fins",
                 face_mat=lambda x, L, z, p=None: 1 if z > 1.27 else 0, sharp=40)

        # 에어 브레이크: 사이드 바디 윗면 패널. 뒷전 힌지, 앞쪽이 들려 바람을 받는다.
        ax, aL = 0.49, -0.81
        az = SIDE.top(aL, ax) + 0.002
        empty(f"piv_airbrake_{side}", s * ax, aL - 0.19, az, parent="chassis", part="airbrake")
        add_mesh(f"airbrake_{side}", box((s * ax, aL, az + 0.007), (0.20, 0.38, 0.014), bevel=0.005),
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
