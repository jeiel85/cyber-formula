import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutlinePass } from 'three/addons/postprocessing/OutlinePass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { DecalGeometry } from 'three/addons/geometries/DecalGeometry.js';

const $ = (id) => document.getElementById(id);
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const V = (x, y, z) => new THREE.Vector3(x, y, z);

// ------------------------------------------------------------------ 데이터
// 좌표는 three.js 기준(미터, Y-up, 차 앞쪽 = +Z). 수치 사실은 build_asurada.py의 모델 치수다.

const PARTS = [
  {
    id: 'nose', name: '노즈 콘', en: 'Nose cone',
    desc: '차체 맨 앞에서 공기를 가장 먼저 가르는 부분입니다. 끝을 뾰족하게 좁혀 정면 저항을 줄이고, 아래로 내려온 파일런 두 개가 프런트 윙을 붙잡습니다. 파란 도장과 빨간 띠는 이 모델의 팬메이드 리버리입니다.',
    facts: [['노즈 끝 ~ 캐노피 앞', '1.82 m'], ['차체 전장', '5.08 m'], ['단면', '슈퍼타원 로프트']],
    actions: [], anchor: [0, 0.47, 1.95], space: 'chassis',
    cam: { pos: [2.0, 1.1, 3.7], target: [0, 0.32, 1.9] },
  },
  {
    id: 'front_wing', name: '프런트 윙', en: 'Front wing',
    desc: '메인 플레인과 좌우 플랩으로 이뤄진 2단 날개입니다. 위아래를 뒤집은 익형이라 공기를 위로 밀어내며 앞바퀴를 노면에 눌러 줍니다. 에어로 모드에서는 플랩을 더 세우고, 랠리 모드에서는 노면에 닿지 않도록 눕힙니다.',
    facts: [['폭', '1.62 m'], ['메인 플레인 받음각', '6°'], ['플랩 받음각 (서킷)', '22°']],
    actions: ['mode:aero', 'mode:rally'], anchor: [0.62, 0.25, 2.42], space: 'chassis',
    cam: { pos: [2.4, 0.9, 4.0], target: [0.2, 0.15, 2.35] },
  },
  {
    id: 'front_wheels', name: '프런트 4륜', en: 'Six-wheel layout · front',
    desc: '앞바퀴를 두 쌍, 모두 네 개로 나눈 6륜 구조입니다. 바퀴를 작게 만들어 정면 면적을 줄이면서 앞쪽 접지 면적은 넓혀 제동과 코너 진입을 안정시키는 발상으로, 1976년 F1의 티렐 P34가 실제로 쓴 방식이기도 합니다.',
    facts: [['타이어 지름', '0.54 m'], ['타이어 폭', '0.24 m'], ['앞 두 축 간격', '0.66 m']],
    actions: ['boost'], anchor: [0.82, 0.42, 1.53], space: 'root',
    cam: { pos: [3.2, 0.9, 2.7], target: [0.6, 0.27, 1.5] },
  },
  {
    id: 'suspension', name: '서스펜션 암', en: 'Wishbone suspension',
    desc: '노란 위시본 암이 바퀴를 차체에 잇습니다. 바퀴마다 위아래 두 쌍씩 모두 스물네 개입니다. 랠리 모드로 바꾸면 차체가 140mm 떠오르면서 암이 길게 늘어나는 모습을 볼 수 있습니다.',
    facts: [['랠리 모드 차고', '+140 mm'], ['에어로 모드 차고', '−20 mm'], ['암 개수', '24개']],
    actions: ['mode:rally', 'mode:circuit'], anchor: [0.42, 0.34, 1.86], space: 'root',
    cam: { pos: [1.9, 0.55, 3.0], target: [0.4, 0.28, 1.6] },
  },
  {
    id: 'cockpit', name: '콕핏', en: 'Cockpit',
    desc: '모노코크를 파내 만든 1인승 콕핏입니다. 시트, 스티어링 휠, 대시보드가 들어 있고 양옆에 리어뷰 미러가 달려 있습니다. 캐노피를 열면 안쪽이 잘 보입니다.',
    facts: [['콕핏 개구부', '0.48 × 0.80 m'], ['시트', '1인승']],
    actions: ['canopy'], anchor: [0.36, 0.64, -0.05], space: 'chassis',
    cam: { pos: [1.5, 1.6, 1.0], target: [0, 0.45, 0.0] },
  },
  {
    id: 'canopy', name: '캐노피', en: 'Canopy',
    desc: '드라이버를 감싸는 투명 캐노피입니다. 이 모델에서는 앞쪽 힌지를 축으로 뒤쪽이 들리며 열립니다. 밑단은 차체 윗면 곡선을 그대로 따라가도록 만들어 닫혔을 때 틈 없이 맞물립니다.',
    facts: [['힌지 위치', '앞쪽'], ['개방 각도', '57°'], ['길이', '1.38 m']],
    actions: ['canopy'], anchor: [0, 0.88, 0.12], space: 'chassis',
    cam: { pos: [2.1, 1.8, 1.9], target: [0, 0.7, 0.0] },
  },
  {
    id: 'cyber_system', name: '사이버 시스템 ASURADA', en: 'Cyber System AI',
    desc: '대시보드의 빛나는 화면이 이 차의 두뇌인 AI 내비게이션 아스라다입니다. 원작에서 아스라다는 드라이버와 대화하며 주행 데이터를 분석하고 조언하는 사이버 시스템으로 나옵니다. 이 뷰어에서는 왼쪽 아래 콘솔에 아스라다의 메시지가 뜹니다.',
    facts: [['위치', '대시보드 중앙'], ['화면', '실시간 상태 표시']],
    actions: ['talk', 'canopy'], anchor: [0, 0.56, 0.36], space: 'chassis',
    cam: { pos: [0.5, 1.15, -0.6], target: [0, 0.5, 0.32] },
  },
  {
    id: 'sidepods', name: '사이드 포드', en: 'Sidepods',
    desc: '콕핏 양옆의 긴 포드입니다. 앞쪽 흡기구로 들어온 공기가 냉각 장치를 지나갑니다. 윗면에는 에어 브레이크 패널이, 옆구리 안쪽에는 에어로 모드에서 펼쳐지는 사이드 핀이 숨어 있습니다.',
    facts: [['길이', '1.70 m'], ['최대 폭', '0.40 m']],
    actions: ['airbrake', 'mode:aero'], anchor: [0.82, 0.40, 0.15], space: 'chassis',
    cam: { pos: [2.8, 0.8, 1.3], target: [0.55, 0.28, -0.2] },
  },
  {
    id: 'aero_fins', name: '사이드 에어로 핀', en: 'Deployable aero fins',
    desc: '평소에는 사이드 포드 안에 접혀 있다가 에어로 모드에서 좌우로 22cm씩 미끄러져 나오는 보조 날개입니다. 차체 중앙부에서 다운포스를 더해 고속 코너 안정성을 높이는 역할로 설정했습니다.',
    facts: [['전개 거리', '0.22 m'], ['코드 길이', '0.36 m'], ['받음각', '4°']],
    actions: ['mode:aero', 'mode:circuit'], anchor: [0.88, 0.32, -0.55], space: 'chassis',
    cam: { pos: [2.4, 1.0, 0.0], target: [0.7, 0.3, -0.55] },
  },
  {
    id: 'airbrake', name: '에어 브레이크', en: 'Air brakes',
    desc: '사이드 포드 윗면의 패널입니다. 뒤쪽 힌지를 축으로 앞쪽이 들려 바람을 정면으로 받아 감속을 돕습니다. 패널 안쪽은 빨간색이라 펼쳐지면 뒤따르는 차에서도 눈에 잘 띕니다.',
    facts: [['패널 길이', '0.50 m'], ['최대 각도', '57°'], ['힌지', '뒤쪽']],
    actions: ['airbrake'], anchor: [0.60, 0.52, -0.62], space: 'chassis',
    cam: { pos: [2.0, 1.5, 0.4], target: [0.5, 0.42, -0.6] },
  },
  {
    id: 'engine', name: '엔진 커버 & 에어박스', en: 'Engine cover · airbox',
    desc: '드라이버 머리 뒤의 에어박스가 엔진 흡기를 받아들이고, 그 뒤로 엔진 커버가 꼬리까지 이어집니다. 두 부스트 포드 사이를 지나는 파란 줄무늬가 리어 윙까지 시선을 이끕니다.',
    facts: [['에어박스 높이', '지면에서 0.77 m'], ['커버 길이', '1.70 m']],
    actions: [], anchor: [0, 0.84, -0.85], space: 'chassis',
    cam: { pos: [1.6, 1.8, -2.4], target: [0, 0.6, -1.0] },
  },
  {
    id: 'boost_pods', name: '부스트 포드', en: 'Boost pods',
    desc: '차체 뒤쪽 위에 얹힌 두 개의 원통형 유닛으로, 순간적으로 큰 추진력을 더하는 부스트 장치입니다. 빨간 돔에서 시작해 꼬리의 노즐로 끝납니다. 부스트를 켜면 노즐에서 불꽃이 뿜어지고 포드가 7cm 뒤로 밀려나며 바퀴와 롤링 로드가 돌기 시작합니다.',
    facts: [['길이', '1.55 m'], ['지름', '0.34 m'], ['충전 시간', '1.2 초']],
    actions: ['boost'], anchor: [0.36, 0.92, -1.15], space: 'chassis',
    cam: { pos: [2.4, 1.6, -3.5], target: [0.25, 0.65, -1.4] },
  },
  {
    id: 'rear_wheels', name: '리어 타이어', en: 'Rear wheels',
    desc: '구동을 맡는 큰 뒷바퀴입니다. 앞바퀴보다 지름도 폭도 훨씬 커서 부스트의 출력을 노면에 전달합니다. 부스트를 켜면 회전하는 모습을 볼 수 있습니다.',
    facts: [['타이어 지름', '0.72 m'], ['타이어 폭', '0.40 m'], ['뒤 트레드', '1.66 m']],
    actions: ['boost'], anchor: [1.06, 0.50, -1.5], space: 'root',
    cam: { pos: [3.2, 0.9, -2.7], target: [0.8, 0.36, -1.5] },
  },
  {
    id: 'rear_wing', name: '리어 윙', en: 'Rear wing',
    desc: '키 큰 파란 엔드플레이트 사이에 메인 플레인, 플랩, 아래쪽 빔 윙을 단 3단 구성입니다. 에어로 모드에서는 메인과 플랩을 함께 세워 다운포스를 키우고, 랠리 모드에서는 눕혀 저항을 줄입니다.',
    facts: [['엔드플레이트 높이', '1.12 m'], ['메인 받음각 (서킷)', '14°'], ['플랩 받음각 (서킷)', '35°']],
    actions: ['mode:aero', 'mode:rally'], anchor: [0, 1.2, -2.22], space: 'chassis',
    cam: { pos: [1.9, 1.5, -4.1], target: [0, 0.85, -2.2] },
  },
  {
    id: 'underbody', name: '플로어 & 디퓨저', en: 'Floor · diffuser',
    desc: '차체 바닥의 평평한 플로어와 꼬리 아래에서 비스듬히 올라가는 디퓨저입니다. 바닥 아래를 지나는 공기를 빠르게 뽑아내 차를 노면 쪽으로 끌어당깁니다. 꼬리 끝의 빨간 등은 레인 라이트로, 부스트 중에는 깜빡입니다.',
    facts: [['디퓨저 핀', '4장'], ['플로어 높이', '지면에서 58 mm']],
    actions: ['boost'], anchor: [0, 0.16, -2.05], space: 'chassis',
    cam: { pos: [1.3, 0.35, -4.1], target: [0, 0.2, -1.9] },
  },
];
const PART_BY_ID = Object.fromEntries(PARTS.map((p, i) => [p.id, { ...p, no: i + 1 }]));

const MODES = {
  circuit: {
    name: '서킷', ride: 0, rwMain: 0, rwFlap: 0, fFlap: 0, fin: 0,
    note: '테크니컬 서킷 모드. 기본 세팅으로, 윙 각도와 차고가 표준 위치에 있습니다.',
    say: '테크니컬 서킷 모드로 돌아갑니다. 기본 세팅입니다.',
  },
  aero: {
    name: '에어로', ride: -0.02, rwMain: 0.12, rwFlap: 0.26, fFlap: 0.16, fin: 0.22,
    note: '에어로 모드. 리어 윙과 프런트 플랩을 세우고 사이드 핀을 펼쳐 다운포스를 키웁니다. 차고 −20mm.',
    say: '에어로 모드. 리어 윙을 세우고 사이드 핀을 전개합니다.',
  },
  rally: {
    name: '랠리', ride: 0.14, rwMain: -0.12, rwFlap: -0.25, fFlap: -0.18, fin: 0,
    note: '랠리 모드. 차고를 140mm 올리고 윙을 눕혀 험한 노면에 대비합니다.',
    say: '랠리 모드. 차고를 140mm 올립니다. 노면 충격에 대비하세요.',
  },
};

const VIEWS = {
  hero: { pos: [4.3, 2.0, 4.7], target: [0, 0.45, 0] },
  side: { pos: [6.6, 0.9, 0], target: [0, 0.45, 0] },
  front: { pos: [0, 0.95, 6.4], target: [0, 0.45, 0] },
  rear: { pos: [0, 1.5, -6.2], target: [0, 0.5, 0] },
  top: { pos: [0, 7.6, 0.01], target: [0, 0.4, 0] },
  low: { pos: [3.0, 0.22, 3.7], target: [0, 0.5, 0.4] },
};

const TALK = [
  '노면 온도는 측정할 수 없습니다. 여기는 풍동이니까요.',
  '부스트는 B 키로도 점화할 수 있습니다.',
  '랠리 모드에서 서스펜션 암이 얼마나 늘어나는지 지켜보세요.',
  '기류 표시를 켜면 공기가 차체를 어떻게 타고 넘는지 보입니다.',
  '에어로 모드에서는 사이드 핀이 포드 밖으로 나옵니다.',
  '캐노피를 열면 제가 있는 대시보드가 보입니다.',
];

// ------------------------------------------------------------------ 상태

const state = {
  mode: 'circuit', canopy: false, airbrake: false, boost: 'idle',
  markers: true, airflow: false, autoRotate: false, selected: null, hovered: null,
};
const anim = { ride: 0, rwMain: 0, rwFlap: 0, fFlap: 0, fin: 0, canopy: 0, airbrake: 0, charge: 0, power: 0, spin: 0, talk: 0 };
const nodes = {};
const base = {};
const startTime = performance.now();

// ------------------------------------------------------------------ 렌더러 / 장면

const stage = $('stage');
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
} catch (err) {
  failLoad('이 브라우저에서 WebGL을 시작하지 못했습니다. 하드웨어 가속을 켠 최신 브라우저에서 열어 주세요.');
  throw err;
}
const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.75);
renderer.setPixelRatio(pixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.localClippingEnabled = true;
stage.append(renderer.domElement);

const scene = new THREE.Scene();
scene.background = gradientTexture();
scene.fog = new THREE.Fog(0x06080c, 11, 30);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.5;

const camera = new THREE.PerspectiveCamera(36, 1, 0.05, 80);
camera.position.set(8.5, 0.7, 8.0);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 1.1;
controls.maxDistance = 16;
controls.maxPolarAngle = Math.PI * 0.497;
controls.target.set(0, 0.45, 0);
controls.autoRotateSpeed = 0.7;

scene.add(new THREE.HemisphereLight(0x8aa4ff, 0x0b0d12, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 2.0);
key.position.set(4, 8, 5);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 1, far: 20 });
key.shadow.bias = -0.0004;
key.shadow.normalBias = 0.02;
scene.add(key);
const rim = new THREE.DirectionalLight(0x6f8cff, 1.6);
rim.position.set(-5, 3, -6);
scene.add(rim);
const fill = new THREE.DirectionalLight(0xffe3c4, 0.5);
fill.position.set(-4, 2, 4);
scene.add(fill);

const floor = buildFloor();

// 후처리: 호버 윤곽(흰색), 선택 윤곽(빨강), 부스트 불꽃용 블룸
const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 }));
composer.addPass(new RenderPass(scene, camera));
const hoverPass = new OutlinePass(new THREE.Vector2(1, 1), scene, camera);
Object.assign(hoverPass, { edgeStrength: 2.4, edgeGlow: 0, edgeThickness: 1, pulsePeriod: 0 });
hoverPass.visibleEdgeColor.set(0xcfdcff);
hoverPass.hiddenEdgeColor.set(0x1d2a44);
composer.addPass(hoverPass);
const selPass = new OutlinePass(new THREE.Vector2(1, 1), scene, camera);
Object.assign(selPass, { edgeStrength: 4, edgeGlow: 0.5, edgeThickness: 1.4, pulsePeriod: reduceMotion ? 0 : 2.6 });
selPass.visibleEdgeColor.set(0xff3d4a);
selPass.hiddenEdgeColor.set(0x4a0f16);
composer.addPass(selPass);
// 임계값을 높게 둬서 도장 반사광은 번지지 않고 발광체(노즐·불꽃·램프)만 번지게 한다.
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.45, 0.32, 3.2);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// 패널·설명 카드가 덮지 않는 빈 영역의 가운데에 차가 오도록 투영 중심을 옮긴다.
// 카드가 열리고 닫힐 때는 매 프레임 부드럽게 따라간다.
const viewOff = { x: 0, y: 0, tx: 0, ty: 0 };
const rectOf = (sel) => document.querySelector(sel)?.getBoundingClientRect();
function viewOffsetTarget(w, h) {
  const deck = rectOf('.deck');
  if (!deck || !deck.width) return [0, 0];
  const card = document.getElementById('card');
  const cardRect = card && !card.hidden ? card.getBoundingClientRect() : null;
  if (w > 860) {
    const left = cardRect ? cardRect.right : 0;
    return [w / 2 - (left + deck.left) / 2, 0];
  }
  const top = rectOf('.views').bottom;
  const bottom = cardRect ? cardRect.top : rectOf('.console').top;
  return [0, h / 2 - (top + bottom) / 2];
}
function applyViewOffset() {
  const w = stage.clientWidth, h = stage.clientHeight;
  const dx = viewOff.x, dy = viewOff.y;
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) camera.clearViewOffset();
  else camera.setViewOffset(w + 2 * Math.abs(dx), h + 2 * Math.abs(dy), dx > 0 ? 2 * dx : 0, dy > 0 ? 2 * dy : 0, w, h);
  camera.updateProjectionMatrix();
}
function retargetViewOffset(immediate = false) {
  [viewOff.tx, viewOff.ty] = viewOffsetTarget(stage.clientWidth, stage.clientHeight);
  if (immediate) { viewOff.x = viewOff.tx; viewOff.y = viewOff.ty; applyViewOffset(); }
}
function stepViewOffset(dt) {
  if (Math.abs(viewOff.x - viewOff.tx) < 0.3 && Math.abs(viewOff.y - viewOff.ty) < 0.3) return;
  const k = 1 - Math.exp(-7 * dt);
  viewOff.x += (viewOff.tx - viewOff.x) * k;
  viewOff.y += (viewOff.ty - viewOff.y) * k;
  applyViewOffset();
}
// 차가 들어갈 빈 영역이 좁으면 카메라를 그만큼 뒤로 뺀다.
let fit = 1;
function fitScale(w, h) {
  const deck = rectOf('.deck');
  const freeW = w > 860 && deck?.width ? deck.left : w;
  const freeH = w > 860 ? h : h * 0.62;
  return THREE.MathUtils.clamp(1.55 / (freeW / freeH), 1, 1.9);
}
function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  fit = fitScale(w, h);
  renderer.setSize(w, h, false);
  composer.setPixelRatio(pixelRatio);
  composer.setSize(w, h);
  camera.aspect = w / h;
  camera.fov = w < 700 ? 46 : 36;
  retargetViewOffset(true);
}
window.addEventListener('resize', resize);
document.fonts?.ready.then(resize);
resize();

// ------------------------------------------------------------------ 재질

const MATS = {
  paint_white: new THREE.MeshPhysicalMaterial({ color: 0xe2e7ef, roughness: 0.32, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.06 }),
  paint_blue: new THREE.MeshPhysicalMaterial({ color: 0x1d40b4, roughness: 0.3, metalness: 0.15, clearcoat: 1, clearcoatRoughness: 0.06 }),
  paint_red: new THREE.MeshPhysicalMaterial({ color: 0xd3202c, roughness: 0.3, metalness: 0.05, clearcoat: 1, clearcoatRoughness: 0.06 }),
  paint_yellow: new THREE.MeshPhysicalMaterial({ color: 0xf1bb2b, roughness: 0.35, metalness: 0.05, clearcoat: 0.8 }),
  carbon: new THREE.MeshPhysicalMaterial({ color: 0x16181d, roughness: 0.42, metalness: 0.25, clearcoat: 0.6, clearcoatRoughness: 0.2 }),
  tire: new THREE.MeshStandardMaterial({ color: 0x141414, roughness: 0.9 }),
  rim: new THREE.MeshStandardMaterial({ color: 0xd0d5dd, roughness: 0.22, metalness: 1 }),
  metal_dark: new THREE.MeshStandardMaterial({ color: 0x3b3f45, roughness: 0.35, metalness: 1 }),
  glass: new THREE.MeshPhysicalMaterial({
    color: 0x0c1622, roughness: 0.04, metalness: 0, clearcoat: 1, transparent: true, opacity: 0.34,
    side: THREE.DoubleSide, depthWrite: false, envMapIntensity: 1.6,
  }),
  glow_boost: new THREE.MeshBasicMaterial({ color: 0x0b1a2a, toneMapped: false }),
  ai_screen: new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }),
  lamp_red: new THREE.MeshBasicMaterial({ color: 0x5a0a0a, toneMapped: false }),
  seat: new THREE.MeshStandardMaterial({ color: 0x252a33, roughness: 0.85 }),
  intake: new THREE.MeshStandardMaterial({ color: 0x050608, roughness: 0.7 }),
};

// ------------------------------------------------------------------ 모델 로드

const car = new THREE.Group();
scene.add(car);
const pickables = [];
const partMeshes = {}; // part id -> 윤곽선을 그릴 메시 목록
const regionOverlays = {}; // 차체 한 덩어리를 나눠 쓰는 파츠(노즈·콕핏·엔진)용 강조 오버레이
const markers = {};
let airflow = null;

// 모델은 버퍼를 base64로 내장한 glTF JSON이다. GLTFLoader에 그대로 주면 내장 버퍼를 data: URI로
// 다시 fetch하는데, 아티팩트 CSP는 그 요청을 막을 수 있다. 그래서 직접 받아 메모리에서 GLB로 조립한다.
async function fetchModel(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const total = Number(res.headers.get('content-length')) || 0;
  let bytes;
  if (res.body && total) {
    const reader = res.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      $('loader-bar').style.width = `${Math.min(90, Math.round((got / total) * 90))}%`;
    }
    bytes = new Uint8Array(got);
    let o = 0;
    for (const c of chunks) { bytes.set(c, o); o += c.length; }
  } else {
    bytes = new Uint8Array(await res.arrayBuffer());
  }
  const doc = JSON.parse(new TextDecoder().decode(bytes));
  const buf = doc.buffers?.[0];
  const m = /^data:[^,]*;base64,(.*)$/s.exec(buf?.uri || '');
  if (!m || doc.buffers.length !== 1) throw new Error('버퍼가 하나 내장된 glTF가 아닙니다.');
  const raw = atob(m[1]);
  const bin = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bin[i] = raw.charCodeAt(i);
  delete buf.uri;
  return toGlb(doc, bin);
}
function toGlb(doc, bin) {
  const json = new TextEncoder().encode(JSON.stringify(doc));
  const jsonPad = (4 - (json.length % 4)) % 4, binPad = (4 - (bin.length % 4)) % 4;
  const total = 12 + 8 + json.length + jsonPad + 8 + bin.length + binPad;
  const out = new Uint8Array(total);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); // 'glTF'
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, json.length + jsonPad, true);
  dv.setUint32(16, 0x4e4f534a, true); // 'JSON'
  out.set(json, 20);
  out.fill(0x20, 20 + json.length, 20 + json.length + jsonPad);
  const o = 20 + json.length + jsonPad;
  dv.setUint32(o, bin.length + binPad, true);
  dv.setUint32(o + 4, 0x004e4942, true); // 'BIN\0'
  out.set(bin, o + 8);
  return out.buffer;
}

fetchModel('asurada.gltf.json')
  .then((glb) => new GLTFLoader().parseAsync(glb, ''))
  .then((gltf) => {
    setupCar(gltf.scene);
    $('loader-bar').style.width = '100%';
    $('loader').classList.add('done');
    boot();
  })
  .catch((err) => {
    console.error(err);
    failLoad(`차체 데이터(asurada.gltf.json)를 불러오지 못했습니다. ${err?.message || err}`);
  });

function failLoad(message) {
  $('loader').classList.add('error');
  $('loader-text').textContent = message;
}

function partOf(obj, point) {
  for (let o = obj; o; o = o.parent) {
    const p = o.userData?.part;
    if (!p) continue;
    if (p !== 'body') return p;
    const local = nodes.chassis.worldToLocal(point.clone());
    return local.z > 0.95 ? 'nose' : local.z < -0.55 ? 'engine' : 'cockpit';
  }
  return null;
}

function setupCar(root) {
  car.add(root);
  root.traverse((o) => {
    if (o.name) nodes[o.name] = o;
    if (!o.isMesh) return;
    const m = MATS[o.material.name];
    if (m) o.material = m;
    o.castShadow = !['glass', 'glow_boost', 'ai_screen'].includes(o.material.name);
    o.receiveShadow = true;
    if (o.material === MATS.glass) o.renderOrder = 10;
    pickables.push(o);
  });
  if (!nodes.chassis) throw new Error('GLB에 chassis 노드가 없습니다.');
  for (const o of Object.values(nodes)) base[o.name] = { pos: o.position.clone(), rot: o.rotation.clone() };

  // 파츠별 윤곽선 대상. 차체(body)를 나눠 쓰는 파츠는 오버레이로 강조한다.
  for (const mesh of pickables) {
    let p = null;
    for (let o = mesh; o && !p; o = o.parent) p = o.userData?.part || null;
    if (!p || p === 'body') continue;
    (partMeshes[p] ||= []).push(mesh);
  }
  buildRegionOverlays();
  prepareScreen();
  buildSuspension();
  buildFlames();
  buildMarkers();
  buildDecals();
}

function buildRegionOverlays() {
  const body = nodes.body;
  if (!body) return;
  const regions = {
    nose: [new THREE.Plane(V(0, 0, 1), -0.95)],
    cockpit: [new THREE.Plane(V(0, 0, 1), 0.55), new THREE.Plane(V(0, 0, -1), 0.95)],
    engine: [new THREE.Plane(V(0, 0, -1), -0.55)],
  };
  const meshes = [];
  body.traverse((o) => { if (o.isMesh) meshes.push(o); });
  for (const [id, planes] of Object.entries(regions)) {
    const mat = new THREE.MeshBasicMaterial({
      color: 0xff3d4a, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false,
      clippingPlanes: planes, polygonOffset: true, polygonOffsetFactor: -2, toneMapped: false,
    });
    for (const m of meshes) {
      const ov = new THREE.Mesh(m.geometry, mat);
      ov.raycast = () => {};
      ov.renderOrder = 5;
      m.add(ov);
    }
    regionOverlays[id] = mat;
  }
}

// AI 화면: 캔버스에 상태와 음성 파형을 그려 대시보드 화면에 입힌다.
const screenCanvas = document.createElement('canvas');
screenCanvas.width = 256; screenCanvas.height = 96;
const screenTex = new THREE.CanvasTexture(screenCanvas);
screenTex.colorSpace = THREE.SRGBColorSpace;
function prepareScreen() {
  const mesh = nodes.ai_screen?.isMesh ? nodes.ai_screen : null;
  if (!mesh) return;
  const g = mesh.geometry;
  g.computeBoundingBox();
  const { min, max } = g.boundingBox;
  const pos = g.attributes.position;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    uv[i * 2] = (max.x - pos.getX(i)) / (max.x - min.x);
    uv[i * 2 + 1] = (pos.getY(i) - min.y) / (max.y - min.y);
  }
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  MATS.ai_screen.map = screenTex;
  MATS.ai_screen.needsUpdate = true;
}
function drawScreen(t) {
  const c = screenCanvas.getContext('2d');
  const w = screenCanvas.width, h = screenCanvas.height;
  c.fillStyle = '#031018';
  c.fillRect(0, 0, w, h);
  c.fillStyle = state.boost === 'on' ? '#ff6b75' : '#54e4ff';
  c.font = '600 15px "IBM Plex Mono", monospace';
  c.fillText('ASURADA', 10, 20);
  c.font = '500 12px "IBM Plex Mono", monospace';
  c.textAlign = 'right';
  c.fillText(state.boost === 'on' ? 'BOOST' : state.mode.toUpperCase(), w - 10, 20);
  c.textAlign = 'left';
  const bars = 28;
  for (let i = 0; i < bars; i++) {
    const amp = 0.12 + anim.talk * (0.5 + 0.5 * Math.sin(t * 18 + i * 1.7) * Math.sin(t * 7.3 + i * 0.6));
    const bh = Math.max(2, amp * 50);
    c.fillRect(10 + i * 8.4, 62 - bh / 2, 5, bh);
  }
  screenTex.needsUpdate = true;
}

// 서스펜션: 차체 쪽 장착점은 섀시를 따라 움직이고 바퀴 쪽은 고정이라, 매 프레임 막대를 다시 놓는다.
const rods = [];
function buildSuspension() {
  const geo = new THREE.CylinderGeometry(0.011, 0.011, 1, 8, 1);
  geo.translate(0, 0.5, 0);
  const axles = [
    { z: 1.86, hubX: 0.558, hubLo: 0.215, hubHi: 0.325, lo: [0.15, 0.22, 0.14], hi: [0.14, 0.36, 0.10] },
    { z: 1.20, hubX: 0.598, hubLo: 0.215, hubHi: 0.325, lo: [0.24, 0.24, 0.14], hi: [0.22, 0.40, 0.10] },
    { z: -1.50, hubX: 0.71, hubLo: 0.28, hubHi: 0.44, lo: [0.26, 0.25, 0.18], hi: [0.26, 0.42, 0.12] },
  ];
  const group = new THREE.Group();
  group.userData.part = 'suspension';
  car.add(group);
  for (const a of axles) {
    for (const s of [1, -1]) {
      for (const [hubY, inner] of [[a.hubLo, a.lo], [a.hubHi, a.hi]]) {
        for (const dz of [inner[2], -inner[2]]) {
          const mesh = new THREE.Mesh(geo, MATS.paint_yellow);
          mesh.castShadow = true;
          group.add(mesh);
          rods.push({ mesh, hub: V(s * a.hubX, hubY, a.z), inner: V(s * inner[0], inner[1], a.z + dz) });
          pickables.push(mesh);
          (partMeshes.suspension ||= []).push(mesh);
        }
      }
    }
  }
}
const _up = V(0, 1, 0), _a = new THREE.Vector3(), _d = new THREE.Vector3();
function updateSuspension() {
  const lift = nodes.chassis ? nodes.chassis.position.y - base.chassis.pos.y : 0;
  for (const r of rods) {
    _a.copy(r.inner).setY(r.inner.y + lift);
    _d.subVectors(_a, r.hub);
    const len = _d.length();
    r.mesh.position.copy(r.hub);
    r.mesh.quaternion.setFromUnitVectors(_up, _d.divideScalar(len));
    r.mesh.scale.set(1, len, 1);
  }
}

// 부스트 불꽃: 노즐마다 바깥 불꽃과 안쪽 코어 원뿔 두 겹 + 점광원
const flames = [];
const flameUniforms = { uTime: { value: 0 }, uPower: { value: 0 } };
function flameMaterial(core, edge, diamonds, gain, fall) {
  return new THREE.ShaderMaterial({
    uniforms: { ...flameUniforms, uCore: { value: new THREE.Color(core) }, uEdge: { value: new THREE.Color(edge) }, uDiamonds: { value: diamonds }, uGain: { value: gain }, uFall: { value: fall } },
    vertexShader: /* glsl */`
      varying vec2 vUv; varying vec3 vN; varying vec3 vV;
      void main() {
        vUv = uv;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform float uTime, uPower, uDiamonds, uGain, uFall; uniform vec3 uCore, uEdge;
      varying vec2 vUv; varying vec3 vN; varying vec3 vV;
      void main() {
        // 보간 오차로 along이 1을 살짝 넘으면 pow(음수)가 NaN이 되고, 블룸이 그걸 화면 전체로 번지게 한다.
        float along = clamp(vUv.y, 0.0, 1.0);
        float facing = pow(clamp(abs(dot(normalize(vN), normalize(vV))), 0.0, 1.0), 1.4);
        float flick = 0.82 + 0.18 * sin(uTime * 53.0 + vUv.x * 31.0) * sin(uTime * 37.0 + along * 11.0);
        float shock = mix(1.0, 0.55 + 0.9 * smoothstep(0.55, 1.0, 0.5 + 0.5 * sin(along * 30.0 - uTime * 6.0)), uDiamonds);
        float a = facing * pow(max(1.0 - along, 0.0), uFall) * uPower * flick * shock;
        vec3 col = mix(uEdge, uCore, clamp(facing * (1.0 - along) * 1.3, 0.0, 1.0));
        gl_FragColor = vec4(col * uGain * a, a);
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, toneMapped: false,
  });
}
function buildFlames() {
  const outerGeo = new THREE.ConeGeometry(0.14, 1, 32, 12, true);
  outerGeo.translate(0, 0.5, 0).rotateX(-Math.PI / 2);
  const outerMat = flameMaterial(0x9fd2ff, 0x1f45d8, 0.0, 1.1, 0.9);
  const coreMat = flameMaterial(0xffffff, 0x8adcff, 1.0, 2.4, 1.5);
  for (const side of ['L', 'R']) {
    const piv = nodes[`piv_pod_${side}`];
    if (!piv) continue;
    const exit = V(0, 0, -0.83); // 노즐 출구 (포드 피벗 기준)
    const outer = new THREE.Mesh(outerGeo, outerMat);
    const core = new THREE.Mesh(outerGeo, coreMat);
    outer.position.copy(exit); core.position.copy(exit);
    outer.renderOrder = core.renderOrder = 20;
    const light = new THREE.PointLight(0x9cc8ff, 0, 3, 2);
    light.position.set(0, 0, -1.1);
    piv.add(outer, core, light);
    outer.visible = core.visible = false;
    flames.push({ outer, core, light, seed: side === 'L' ? 0 : 1.7 });
  }
}

// 파츠 마커: 3D 위치를 화면에 투영해 버튼을 띄운다.
const markerLayer = $('markers');
function buildMarkers() {
  for (const p of PARTS) {
    const info = PART_BY_ID[p.id];
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'marker';
    b.textContent = String(info.no).padStart(2, '0');
    b.dataset.label = info.name;
    b.setAttribute('aria-label', `${info.no}. ${info.name}`);
    b.addEventListener('click', (e) => { e.stopPropagation(); selectPart(p.id, { fly: true }); });
    markerLayer.append(b);
    markers[p.id] = { el: b, local: V(...p.anchor), space: p.space, world: new THREE.Vector3(), occluded: false };
  }
}
const _proj = new THREE.Vector3();
const occRay = new THREE.Raycaster();
let occIndex = 0;
function updateMarkers() {
  if (!state.markers) return;
  const w = stage.clientWidth, h = stage.clientHeight;
  const ids = Object.keys(markers);
  const lift = nodes.chassis ? nodes.chassis.position.y - base.chassis.pos.y : 0;
  ids.forEach((id, i) => {
    const m = markers[id];
    m.world.copy(m.local);
    if (m.space === 'chassis') m.world.y += lift;
    _proj.copy(m.world).project(camera);
    const visible = _proj.z < 1 && Math.abs(_proj.x) < 1.1 && Math.abs(_proj.y) < 1.1;
    m.el.style.visibility = visible ? 'visible' : 'hidden';
    if (!visible) return;
    m.el.style.transform = `translate(${((_proj.x + 1) / 2) * w}px, ${((1 - _proj.y) / 2) * h}px)`;
    if (i === occIndex % ids.length) {
      const dir = _d.subVectors(m.world, camera.position);
      const dist = dir.length();
      occRay.set(camera.position, dir.divideScalar(dist));
      occRay.far = dist - 0.06;
      const hit = occRay.intersectObjects(pickables, false).find((x) => x.object.material !== MATS.glass);
      const occluded = Boolean(hit);
      if (occluded !== m.occluded) { m.occluded = occluded; m.el.dataset.occluded = String(occluded); }
    }
  });
  occIndex++;
}

// 데칼: 캔버스로 만든 리버리 문자를 차체 곡면에 투영한다.
async function buildDecals() {
  try {
    await Promise.race([
      Promise.all([document.fonts.load('italic 800 120px Saira'), document.fonts.load('600 15px "IBM Plex Mono"')]),
      new Promise((r) => setTimeout(r, 2500)),
    ]);
  } catch { /* 폰트를 못 받아도 대체 글꼴로 그린다 */ }
  scene.updateMatrixWorld(true);
  const lift = nodes.chassis.position.y - base.chassis.pos.y; // 폰트를 기다리는 동안 모드가 바뀌었을 수 있다
  const word = (color, stripe) => canvasTexture(1024, 200, (c, w, h) => {
    c.font = 'italic 800 150px Saira, "Arial Narrow", Arial, sans-serif';
    c.fillStyle = color;
    c.textBaseline = 'alphabetic';
    c.fillText('ASURADA', 18, 150);
    if (stripe) { c.fillStyle = stripe; c.fillRect(30, 168, w - 120, 14); }
  });
  const roundel = canvasTexture(256, 256, (c, w) => {
    c.fillStyle = '#ffffff';
    c.beginPath(); c.arc(w / 2, w / 2, 118, 0, Math.PI * 2); c.fill();
    c.lineWidth = 14; c.strokeStyle = '#1d40b4';
    c.beginPath(); c.arc(w / 2, w / 2, 108, 0, Math.PI * 2); c.stroke();
    c.fillStyle = '#10151f';
    c.font = 'italic 800 132px Saira, "Arial Narrow", Arial, sans-serif';
    c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText('01', w / 2 - 4, w / 2 + 6);
  });
  const blueWord = word('#1d40b4', '#d3202c');
  const whiteWord = word('#f4f6fa', null);
  const H = Math.PI / 2;
  const jobs = [
    ['sidepod_L', blueWord, V(0.79, 0.27, -0.28), new THREE.Euler(0, H, 0), V(0.64, 0.125, 0.3)],
    ['sidepod_R', blueWord, V(-0.79, 0.27, -0.28), new THREE.Euler(0, -H, 0), V(0.64, 0.125, 0.3)],
    ['body', roundel, V(0, 0.46, 1.62), new THREE.Euler(-H, 0, Math.PI), V(0.21, 0.21, 0.3)],
    ['boost_pod_L', roundel, V(0.53, 0.70, -1.32), new THREE.Euler(0, H, 0), V(0.2, 0.2, 0.2)],
    ['boost_pod_R', roundel, V(-0.53, 0.70, -1.32), new THREE.Euler(0, -H, 0), V(0.2, 0.2, 0.2)],
    ['rear_wing', whiteWord, V(0.575, 0.80, -2.19), new THREE.Euler(0, H, 0), V(0.46, 0.09, 0.08)],
    ['rear_wing', whiteWord, V(-0.575, 0.80, -2.19), new THREE.Euler(0, -H, 0), V(0.46, 0.09, 0.08)],
  ];
  for (const [name, tex, pos, rot, size] of jobs) {
    const target = nodes[name];
    if (!target) continue;
    pos.y += lift;
    const mat = new THREE.MeshPhysicalMaterial({
      map: tex, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4,
      roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.06,
    });
    target.traverse((m) => {
      if (!m.isMesh || m.parent?.isMesh) return;
      const geo = new DecalGeometry(m, pos, rot, size);
      if (!geo.attributes.position || geo.attributes.position.count === 0) return;
      const decal = new THREE.Mesh(geo, mat);
      decal.receiveShadow = true;
      decal.renderOrder = 2;
      scene.add(decal);
      target.attach(decal);
      decal.raycast = () => {};
    });
  }
}

function canvasTexture(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return t;
}

function gradientTexture() {
  const c = document.createElement('canvas');
  c.width = 4; c.height = 512;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, '#0b1222');
  grad.addColorStop(0.55, '#070a10');
  grad.addColorStop(1, '#050608');
  g.fillStyle = grad;
  g.fillRect(0, 0, 4, 512);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// 바닥: 풍동 격자 + 차 밑 롤링 로드 벨트 + 조명 웅덩이
function buildFloor() {
  const grid = canvasTexture(512, 512, (c, w) => {
    c.fillStyle = '#0c1018'; c.fillRect(0, 0, w, w);
    c.strokeStyle = '#151c29'; c.lineWidth = 2;
    for (let i = 1; i < 4; i++) { const p = (i * w) / 4; c.beginPath(); c.moveTo(p, 0); c.lineTo(p, w); c.moveTo(0, p); c.lineTo(w, p); c.stroke(); }
    c.strokeStyle = '#212c40'; c.lineWidth = 3;
    c.strokeRect(0, 0, w, w);
  });
  grid.wrapS = grid.wrapT = THREE.RepeatWrapping;
  grid.repeat.set(30, 30);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshStandardMaterial({ map: grid, roughness: 0.82, metalness: 0.1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const beltTex = canvasTexture(64, 256, (c, w, h) => {
    c.fillStyle = '#10151e'; c.fillRect(0, 0, w, h);
    c.fillStyle = '#1a2230';
    for (let y = 0; y < h; y += 32) c.fillRect(0, y, w, 3);
  });
  beltTex.wrapS = beltTex.wrapT = THREE.RepeatWrapping;
  beltTex.repeat.set(1, 9);
  const belt = new THREE.Mesh(new THREE.PlaneGeometry(2.3, 9), new THREE.MeshStandardMaterial({ map: beltTex, roughness: 0.6, metalness: 0.2 }));
  belt.rotation.x = -Math.PI / 2;
  belt.position.y = 0.002;
  belt.receiveShadow = true;
  scene.add(belt);
  const edgeMat = new THREE.MeshBasicMaterial({ color: 0x2a3e70 });
  for (const s of [1, -1]) {
    const edge = new THREE.Mesh(new THREE.PlaneGeometry(0.03, 9), edgeMat);
    edge.rotation.x = -Math.PI / 2;
    edge.position.set(s * 1.165, 0.003, 0);
    scene.add(edge);
  }

  const pool = new THREE.Mesh(new THREE.PlaneGeometry(10, 10), new THREE.MeshBasicMaterial({
    map: canvasTexture(256, 256, (c, w) => {
      const g = c.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
      g.addColorStop(0, 'rgba(90,120,200,0.55)'); g.addColorStop(1, 'rgba(90,120,200,0)');
      c.fillStyle = g; c.fillRect(0, 0, w, w);
    }),
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.35,
  }));
  pool.rotation.x = -Math.PI / 2;
  pool.position.y = 0.004;
  scene.add(pool);
  return { beltTex };
}

// ------------------------------------------------------------------ 기류 표시
// 위에서 내려다본 높이 맵을 한 번 렌더해, 공기 선이 차체 윗면을 타고 넘게 한다.
function buildAirflow() {
  const W = 128, Hh = 320, X = 1.6, Z = 3.2;
  const rt = new THREE.WebGLRenderTarget(W, Hh);
  const ortho = new THREE.OrthographicCamera(-X, X, Z, -Z, 0.1, 10);
  ortho.position.set(0, 5, 0);
  ortho.up.set(0, 0, -1);
  ortho.lookAt(0, 0, 0);
  const heightMat = new THREE.ShaderMaterial({
    vertexShader: 'varying float vY; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vY = w.y; gl_Position = projectionMatrix * viewMatrix * w; }',
    fragmentShader: 'varying float vY; void main(){ float h = clamp(vY / 2.0, 0.0, 1.0) * 255.0; gl_FragColor = vec4(floor(h) / 255.0, fract(h), 0.0, 1.0); }',
    side: THREE.DoubleSide,
  });
  const hidden = [];
  scene.traverse((o) => { if ((o.isMesh || o.isLine || o.isPoints) && !pickables.includes(o) && o.visible) { hidden.push(o); o.visible = false; } });
  // 움직이는 파츠(캐노피·윙·핀·에어 브레이크·차고)를 기본 자세로 돌려놓고 찍은 뒤 되돌린다.
  const saved = Object.entries(base).map(([name, b]) => {
    const n = nodes[name];
    const keep = { n, pos: n.position.clone(), rot: n.rotation.clone() };
    if (name === 'chassis' || name.startsWith('piv_')) { n.position.copy(b.pos); n.rotation.copy(b.rot); }
    return keep;
  });
  scene.updateMatrixWorld(true);
  const bg = scene.background, fog = scene.fog;
  scene.background = null; scene.fog = null; scene.overrideMaterial = heightMat;
  renderer.setRenderTarget(rt);
  renderer.setClearColor(0x000000, 1);
  renderer.clear();
  renderer.render(scene, ortho);
  const px = new Uint8Array(W * Hh * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, W, Hh, px);
  renderer.setRenderTarget(null);
  scene.background = bg; scene.fog = fog; scene.overrideMaterial = null;
  for (const k of saved) { k.n.position.copy(k.pos); k.n.rotation.copy(k.rot); }
  hidden.forEach((o) => { o.visible = true; });
  rt.dispose();

  const height = (x, z) => {
    const i = Math.floor(((x + X) / (2 * X)) * W);
    const j = Math.floor(((Z - z) / (2 * Z)) * Hh); // 이미지 아래쪽이 +Z
    if (i < 0 || i >= W || j < 0 || j >= Hh) return 0;
    const k = (j * W + i) * 4;
    return ((px[k] + px[k + 1] / 255) / 255) * 2.0;
  };

  const pos = [], s = [], lifted = [];
  const zs = [];
  for (let z = 4.2; z >= -5.2; z -= 0.08) zs.push(z);
  for (let x = -1.17; x <= 1.17 + 1e-6; x += 0.09) {
    for (const y0 of [0.3, 0.62]) {
      const ys = zs.map((z) => {
        let e = 0;
        for (let d = 0; d <= 0.6; d += 0.04) e = Math.max(e, height(x, z - d) - 0.5 * d);
        for (let d = 0; d <= 1.3; d += 0.04) e = Math.max(e, height(x, z + d) - 0.22 * d);
        return Math.max(y0, e + 0.05);
      });
      const smooth = ys.map((_, i) => {
        let acc = 0, n = 0;
        for (let k = -3; k <= 3; k++) { const v = ys[i + k]; if (v !== undefined) { acc += v; n++; } }
        return acc / n;
      });
      for (let i = 0; i < zs.length - 1; i++) {
        for (const k of [i, i + 1]) {
          pos.push(x, smooth[k], zs[k]);
          s.push(k / (zs.length - 1));
          lifted.push(Math.min(1, Math.max(0, (smooth[k] - y0) / 0.15)));
        }
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('aS', new THREE.Float32BufferAttribute(s, 1));
  geo.setAttribute('aLift', new THREE.Float32BufferAttribute(lifted, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uRide: { value: 0 }, uAlpha: { value: 0 } },
    vertexShader: /* glsl */`
      attribute float aS; attribute float aLift; uniform float uRide;
      varying float vS; varying float vLift;
      void main() {
        vS = aS; vLift = aLift;
        vec3 p = position; p.y += uRide * aLift;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      }`,
    fragmentShader: /* glsl */`
      uniform float uTime, uAlpha; varying float vS; varying float vLift;
      void main() {
        float d = fract(vS * 7.0 + uTime);
        float pulse = smoothstep(0.0, 0.12, d) * smoothstep(1.0, 0.35, d);
        float ends = smoothstep(0.0, 0.08, vS) * smoothstep(1.0, 0.85, vS);
        vec3 col = mix(vec3(0.25, 0.5, 1.0), vec3(0.8, 0.97, 1.0), vLift);
        float a = (0.18 + 0.82 * pulse) * ends * uAlpha * (0.45 + 0.55 * vLift);
        gl_FragColor = vec4(col * a, a);
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
  });
  const lines = new THREE.LineSegments(geo, mat);
  lines.frustumCulled = false;
  scene.add(lines);
  return { lines, mat, phase: 0 };
}

// ------------------------------------------------------------------ UI

const hud = document.querySelector('.hud');
const ui = {
  views: [...document.querySelectorAll('[data-view]')],
  modes: [...document.querySelectorAll('[data-mode]')],
  boost: $('boost'), boostText: $('boost-text'), boostMeter: $('boost-meter'),
  canopy: $('sw-canopy'), airbrake: $('sw-airbrake'), markersSw: $('sw-markers'), airflowSw: $('sw-airflow'),
  auto: $('autorotate'), card: $('card'), hint: $('hint'), log: $('console-log'), console: $('console'), tip: $('tip'),
};

ui.views.forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
ui.modes.forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
ui.boost.addEventListener('click', () => toggleBoost());
ui.canopy.addEventListener('click', () => toggleCanopy());
ui.airbrake.addEventListener('click', () => toggleAirbrake());
ui.markersSw.addEventListener('click', () => { state.markers = !state.markers; markerLayer.hidden = !state.markers; refresh(); });
ui.airflowSw.addEventListener('click', () => toggleAirflow());
ui.auto.addEventListener('click', () => { state.autoRotate = !state.autoRotate; controls.autoRotate = state.autoRotate; refresh(); });
$('card-close').addEventListener('click', () => clearSelection());
$('card-prev').addEventListener('click', () => stepPart(-1));
$('card-next').addEventListener('click', () => stepPart(1));

window.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === 'b') toggleBoost();
  else if (k === 'c') toggleCanopy();
  else if (k === 'a') toggleAirbrake();
  else if (k === 'm') ui.markersSw.click();
  else if (k === 'f') toggleAirflow();
  else if (k === '1') setMode('circuit');
  else if (k === '2') setMode('aero');
  else if (k === '3') setMode('rally');
  else if (k === 'escape') clearSelection();
  else if (k === 'arrowright' && state.selected) stepPart(1);
  else if (k === 'arrowleft' && state.selected) stepPart(-1);
  else return;
  e.preventDefault();
});

function setMode(mode) {
  if (!MODES[mode] || mode === state.mode) return;
  state.mode = mode;
  say(MODES[mode].say);
  refresh();
}

function toggleCanopy(force) {
  const next = force ?? !state.canopy;
  if (next === state.canopy) return;
  if (next && state.boost === 'on') { say('부스트 중에는 캐노피를 열 수 없습니다.', 'warn'); return; }
  state.canopy = next;
  say(next ? '캐노피 개방. 탑승을 기다리고 있습니다.' : '캐노피 잠금 확인.');
  refresh();
}

function toggleAirbrake(force) {
  const next = force ?? !state.airbrake;
  if (next === state.airbrake) return;
  state.airbrake = next;
  say(next ? '에어 브레이크 전개. 감속 저항 증가.' : '에어 브레이크 수납.');
  refresh();
}

let boostTimer = null;
function toggleBoost() {
  clearTimeout(boostTimer);
  if (state.boost === 'idle' || state.boost === 'cooling') {
    if (state.canopy) { state.canopy = false; say('캐노피가 열려 있어 먼저 닫습니다.', 'warn'); }
    state.boost = 'charging';
    say('부스트 압력 상승… 점화까지 1.2초.', 'boost');
    boostTimer = setTimeout(() => {
      state.boost = 'on';
      say('부스트 점화! 출력 최대.', 'boost');
      refresh();
    }, 1200);
  } else {
    state.boost = 'cooling';
    say('부스트 종료. 노즐 냉각 중.');
    boostTimer = setTimeout(() => { state.boost = 'idle'; refresh(); }, 1600);
  }
  refresh();
}

function toggleAirflow() {
  state.airflow = !state.airflow;
  if (state.airflow && !airflow && nodes.chassis) airflow = buildAirflow();
  if (state.airflow) say('기류 표시를 켭니다. 선이 밝을수록 차체를 타고 넘는 공기입니다.');
  refresh();
}

let fly = null;
function flyTo(pos, target, duration = 1.1, scale = fit) {
  const t1 = V(...target);
  const p1 = V(...pos).sub(t1).multiplyScalar(scale).add(t1);
  fly = { p0: camera.position.clone(), t0: controls.target.clone(), p1, t1, t: 0, duration: reduceMotion ? 0.01 : duration };
}
controls.addEventListener('start', () => { fly = null; });

function setView(name) {
  const v = VIEWS[name];
  if (!v) return;
  flyTo(v.pos, v.target);
  ui.views.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === name)));
}

function selectPart(id, { fly: doFly = false } = {}) {
  const p = PART_BY_ID[id];
  if (!p) return;
  state.selected = id;
  selPass.selectedObjects = partMeshes[id] || [];
  if (doFly) {
    const lift = MODES[state.mode].ride;
    const pos = [...p.cam.pos], target = [...p.cam.target];
    if (p.space === 'chassis') { pos[1] += lift; target[1] += lift; }
    flyTo(pos, target, 1.1, Math.min(fit * 1.45, 2.4));
    ui.views.forEach((b) => b.setAttribute('aria-pressed', 'false'));
  }
  $('card-num').textContent = String(p.no).padStart(2, '0');
  $('card-title').textContent = p.name;
  $('card-en').textContent = p.en;
  $('card-desc').textContent = p.desc;
  const dl = $('card-facts');
  dl.replaceChildren(...p.facts.flatMap(([k, v]) => {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    return [dt, dd];
  }));
  dl.hidden = p.facts.length === 0;
  $('card-pos').textContent = `${p.no} / ${PARTS.length}`;
  ui.card.hidden = false;
  ui.hint.hidden = true;
  hud.classList.add('has-card');
  requestAnimationFrame(() => retargetViewOffset());
  for (const [pid, m] of Object.entries(markers)) m.el.dataset.active = String(pid === id);
  renderActions();
}

function clearSelection() {
  state.selected = null;
  selPass.selectedObjects = [];
  ui.card.hidden = true;
  hud.classList.remove('has-card');
  for (const m of Object.values(markers)) m.el.dataset.active = 'false';
  retargetViewOffset();
}

function stepPart(dir) {
  const i = state.selected ? PARTS.findIndex((p) => p.id === state.selected) : -1;
  const next = PARTS[(i + dir + PARTS.length) % PARTS.length];
  selectPart(next.id, { fly: true });
}

const ACTIONS = {
  canopy: () => [state.canopy ? '캐노피 닫기' : '캐노피 열기', () => toggleCanopy()],
  airbrake: () => [state.airbrake ? '에어 브레이크 접기' : '에어 브레이크 펴기', () => toggleAirbrake()],
  boost: () => [state.boost === 'on' || state.boost === 'charging' ? '부스트 끄기' : '부스트 점화', () => toggleBoost()],
  talk: () => ['아스라다에게 말 걸기', () => say(TALK[Math.floor(Math.random() * TALK.length)])],
  'mode:aero': () => (state.mode === 'aero' ? null : ['에어로 모드로', () => setMode('aero')]),
  'mode:rally': () => (state.mode === 'rally' ? null : ['랠리 모드로', () => setMode('rally')]),
  'mode:circuit': () => (state.mode === 'circuit' ? null : ['서킷 모드로', () => setMode('circuit')]),
};
function renderActions() {
  const box = $('card-actions');
  if (!state.selected) return;
  const items = PART_BY_ID[state.selected].actions.map((a) => ACTIONS[a]?.()).filter(Boolean);
  box.replaceChildren(...items.map(([label, run]) => {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = label;
    b.addEventListener('click', run);
    return b;
  }));
  box.hidden = items.length === 0;
}

const BOOST_TEXT = { idle: '대기 중 · 눌러서 점화', charging: '압력 충전 중…', on: '점화 중 · 눌러서 끄기', cooling: '노즐 냉각 중' };
const BOOST_CHIP = { idle: '대기', charging: '충전', on: '점화', cooling: '냉각' };
function refresh() {
  ui.modes.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)));
  $('mode-note').textContent = MODES[state.mode].note;
  ui.boost.dataset.state = state.boost;
  ui.boost.setAttribute('aria-pressed', String(state.boost === 'on'));
  ui.boostText.textContent = BOOST_TEXT[state.boost];
  ui.canopy.setAttribute('aria-checked', String(state.canopy));
  $('sw-canopy-sub').textContent = state.canopy ? '열림' : '닫힘';
  ui.airbrake.setAttribute('aria-checked', String(state.airbrake));
  $('sw-airbrake-sub').textContent = state.airbrake ? '펼침' : '접힘';
  ui.markersSw.setAttribute('aria-checked', String(state.markers));
  ui.airflowSw.setAttribute('aria-checked', String(state.airflow));
  ui.auto.setAttribute('aria-pressed', String(state.autoRotate));
  $('st-mode').textContent = MODES[state.mode].name;
  const mm = Math.round(MODES[state.mode].ride * 1000);
  $('st-ride').textContent = mm === 0 ? '±0 mm' : `${mm > 0 ? '+' : '−'}${Math.abs(mm)} mm`;
  $('st-canopy').textContent = state.canopy ? '열림' : '닫힘';
  $('st-boost').textContent = BOOST_CHIP[state.boost];
  $('st-boost-chip').dataset.hot = String(state.boost === 'on');
  renderActions();
}

// 아스라다 콘솔
let typing = null;
function stamp() {
  const s = (performance.now() - startTime) / 1000;
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}
function finishTyping() {
  if (!typing) return;
  clearInterval(typing.timer);
  typing.span.textContent = typing.text;
  typing = null;
  ui.console.classList.remove('talking');
}
function say(text, tone = '') {
  finishTyping();
  const li = document.createElement('li');
  if (tone) li.dataset.tone = tone;
  const time = document.createElement('time');
  time.textContent = stamp();
  const span = document.createElement('span');
  li.append(time, span);
  ui.log.append(li);
  while (ui.log.children.length > 4) ui.log.firstElementChild.remove();
  anim.talk = 1;
  if (reduceMotion) { span.textContent = text; return; }
  ui.console.classList.add('talking');
  let i = 0;
  typing = { span, text, timer: setInterval(() => { i++; span.textContent = text.slice(0, i); if (i >= text.length) finishTyping(); }, 26) };
}

// 포인터: 짧게 누르면 선택, 드래그는 카메라 회전
const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let down = null;
function pick(ev) {
  const r = renderer.domElement.getBoundingClientRect();
  ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const hit = ray.intersectObjects(pickables, false)[0];
  return hit ? partOf(hit.object, hit.point) : null;
}
renderer.domElement.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; });
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!down) return;
  const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
  const quick = performance.now() - down.t < 600;
  down = null;
  if (moved > 6 || !quick || !nodes.chassis) return;
  const id = pick(e);
  if (id) selectPart(id, { fly: true });
  else clearSelection();
});
let hoverEv = null;
renderer.domElement.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse' && !down) hoverEv = e; });
renderer.domElement.addEventListener('pointerleave', () => { hoverEv = null; setHover(null); });
function setHover(id, ev) {
  if (id !== state.hovered) {
    state.hovered = id;
    hoverPass.selectedObjects = id && id !== state.selected ? partMeshes[id] || [] : [];
    renderer.domElement.style.cursor = id ? 'pointer' : 'grab';
    ui.tip.textContent = id ? `${String(PART_BY_ID[id].no).padStart(2, '0')} · ${PART_BY_ID[id].name}` : '';
  }
  ui.tip.style.opacity = id ? '1' : '0';
  if (id && ev) ui.tip.style.transform = `translate(${ev.clientX + 14}px, ${ev.clientY + 12}px)`;
}

// ------------------------------------------------------------------ 루프

function damp(cur, target, rate, dt) { return cur + (target - cur) * (1 - Math.exp(-rate * dt)); }
function setRot(name, axis, value) { const n = nodes[name]; if (n) n.rotation[axis] = base[name].rot[axis] + value; }
function setPos(name, axis, value) { const n = nodes[name]; if (n) n.position[axis] = base[name].pos[axis] + value; }

const clock = new THREE.Clock();
let elapsed = 0, screenAcc = 0;
const shake = new THREE.Vector3();
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.05);
  elapsed += dt;

  if (nodes.chassis) {
    const m = MODES[state.mode];
    anim.ride = damp(anim.ride, m.ride, 3.2, dt);
    anim.rwMain = damp(anim.rwMain, m.rwMain, 4, dt);
    anim.rwFlap = damp(anim.rwFlap, m.rwFlap, 4, dt);
    anim.fFlap = damp(anim.fFlap, m.fFlap, 4, dt);
    anim.fin = damp(anim.fin, m.fin, 3.5, dt);
    anim.canopy = damp(anim.canopy, state.canopy ? 1.0 : 0, 2.6, dt);
    anim.airbrake = damp(anim.airbrake, state.airbrake ? -1.0 : 0, 4, dt);
    if (state.boost === 'charging') anim.charge = Math.min(1, anim.charge + dt / 1.2);
    else if (state.boost === 'on') anim.charge = 1;
    else anim.charge = Math.max(0, anim.charge - dt / 1.2);
    anim.power = damp(anim.power, state.boost === 'on' ? 1 : 0, state.boost === 'on' ? 6 : 2.2, dt);
    anim.spin = damp(anim.spin, state.boost === 'on' ? 34 : 0, state.boost === 'on' ? 0.9 : 0.6, dt);
    anim.talk = Math.max(0, anim.talk - dt * (typing ? 0 : 0.8));

    setPos('chassis', 'y', anim.ride);
    setRot('piv_canopy', 'x', anim.canopy);
    setRot('piv_airbrake_L', 'x', anim.airbrake);
    setRot('piv_airbrake_R', 'x', anim.airbrake);
    setRot('piv_rw_main', 'x', anim.rwMain);
    setRot('piv_rw_flap', 'x', anim.rwFlap);
    setRot('piv_front_flap_L', 'x', anim.fFlap);
    setRot('piv_front_flap_R', 'x', anim.fFlap);
    setPos('piv_fin_L', 'x', anim.fin);
    setPos('piv_fin_R', 'x', -anim.fin);
    setPos('piv_pod_L', 'z', -0.07 * anim.power);
    setPos('piv_pod_R', 'z', -0.07 * anim.power);
    for (const n of ['wheel_F1L', 'wheel_F1R', 'wheel_F2L', 'wheel_F2R']) if (nodes[n]) nodes[n].rotation.x += anim.spin * (0.36 / 0.27) * dt;
    for (const n of ['wheel_RL', 'wheel_RR']) if (nodes[n]) nodes[n].rotation.x += anim.spin * dt;
    floor.beltTex.offset.y -= anim.spin * 0.36 * dt * (9 / 9);

    updateSuspension();

    // 부스트 연출
    flameUniforms.uTime.value = elapsed;
    flameUniforms.uPower.value = anim.power;
    const flick = 0.85 + 0.15 * Math.sin(elapsed * 41) * Math.sin(elapsed * 23);
    for (const f of flames) {
      const on = anim.power > 0.01;
      f.outer.visible = f.core.visible = on;
      const len = anim.power * (1.6 + 0.2 * Math.sin(elapsed * 29 + f.seed));
      f.outer.scale.set(1, 1, Math.max(0.001, len));
      f.core.scale.set(0.55, 0.55, Math.max(0.001, len * 0.62));
      f.light.intensity = anim.power * 3 * flick;
    }
    const glow = Math.max(anim.charge * 0.45, anim.power);
    MATS.glow_boost.color.setRGB(0.04 + 4.5 * glow * flick, 0.10 + 6.5 * glow * flick, 0.16 + 9.0 * glow * flick);
    const blink = state.boost === 'on' ? (Math.sin(elapsed * 22) > 0 ? 1 : 0.1) : 0.25;
    MATS.lamp_red.color.setRGB(0.35 + 7.0 * blink, 0.04, 0.04);
    bloom.strength = 0.45 + 0.2 * anim.power;
    ui.boostMeter.style.strokeDashoffset = String(125.66 * (1 - Math.max(anim.charge, anim.power)));

    // 강조 오버레이(차체를 나눠 쓰는 파츠)
    const pulse = reduceMotion ? 1 : 0.75 + 0.25 * Math.sin(elapsed * 2.4);
    for (const [id, mat] of Object.entries(regionOverlays)) {
      const target = state.selected === id ? 0.2 * pulse : state.hovered === id ? 0.09 : 0;
      mat.opacity = damp(mat.opacity, target, 10, dt);
      mat.visible = mat.opacity > 0.002;
    }

    screenAcc += dt;
    if (screenAcc > 1 / 15) { screenAcc = 0; drawScreen(elapsed); }

    if (airflow) {
      airflow.phase += dt * (0.35 + 1.4 * anim.power);
      airflow.mat.uniforms.uTime.value = airflow.phase;
      airflow.mat.uniforms.uRide.value = anim.ride;
      airflow.mat.uniforms.uAlpha.value = damp(airflow.mat.uniforms.uAlpha.value, state.airflow ? 1 : 0, 5, dt);
      airflow.lines.visible = airflow.mat.uniforms.uAlpha.value > 0.01;
    }
  }

  if (fly) {
    fly.t = Math.min(1, fly.t + dt / fly.duration);
    const e = fly.t < 0.5 ? 4 * fly.t ** 3 : 1 - (-2 * fly.t + 2) ** 3 / 2;
    camera.position.lerpVectors(fly.p0, fly.p1, e);
    controls.target.lerpVectors(fly.t0, fly.t1, e);
    if (fly.t >= 1) fly = null;
  }
  controls.update();
  stepViewOffset(dt);

  if (hoverEv && nodes.chassis) { setHover(pick(hoverEv), hoverEv); hoverEv = null; }
  updateMarkers();

  const amp = reduceMotion ? 0 : 0.006 * anim.power;
  shake.set((Math.random() - 0.5) * amp, (Math.random() - 0.5) * amp, (Math.random() - 0.5) * amp);
  camera.position.add(shake);
  composer.render();
  camera.position.sub(shake);
});

function boot() {
  refresh();
  setView('hero');
  fly.duration = reduceMotion ? 0.01 : 1.8;
  say('시스템 기동. 사이버 시스템 아스라다, 온라인.');
  setTimeout(() => say('차체를 누르면 그 파츠의 데이터를 보여 드립니다.'), 1900);
}
