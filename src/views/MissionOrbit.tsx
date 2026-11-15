import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Pause, Play, RotateCcw } from 'lucide-react';
import * as THREE from 'three';
import '../orbit.css';

type OrbitControls = {
  rotate: (horizontal: number, vertical: number) => void;
  reset: () => void;
  redraw: () => void;
};

type SceneStatus = 'loading' | 'ready' | 'unavailable';

const INITIAL_AZIMUTH = 0;
const INITIAL_POLAR = 1.18;
const PLANET_RADIUS = 1.22;

// A deterministic, continuous 3D noise field makes the planet seamless at its texture edge.
function lattice(x: number, y: number, z: number) {
  let value = Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(z, 2147483647);
  value = Math.imul(value ^ (value >>> 13), 1274126177);
  return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function noise(x: number, y: number, z: number) {
  const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z);
  const smooth = (n: number) => n * n * (3 - 2 * n);
  const fx = smooth(x - ix), fy = smooth(y - iy), fz = smooth(z - iz);
  const mix = (a: number, b: number, t: number) => a + (b - a) * t;
  const low = mix(
    mix(lattice(ix, iy, iz), lattice(ix + 1, iy, iz), fx),
    mix(lattice(ix, iy + 1, iz), lattice(ix + 1, iy + 1, iz), fx), fy,
  );
  const high = mix(
    mix(lattice(ix, iy, iz + 1), lattice(ix + 1, iy, iz + 1), fx),
    mix(lattice(ix, iy + 1, iz + 1), lattice(ix + 1, iy + 1, iz + 1), fx), fy,
  );
  return mix(low, high, fz);
}

function terrain(x: number, y: number, z: number) {
  return noise(x * 2.8 + 4, y * 2.8, z * 2.8) * 0.52
    + noise(x * 7.2, y * 7.2 + 8, z * 7.2) * 0.27
    + noise(x * 16.0 + 3, y * 16.0, z * 16.0) * 0.14
    + noise(x * 35.0, y * 35.0, z * 35.0 + 5) * 0.07;
}

const CRATERS = [
  { x: 0.72, y: 0.18, z: 0.67, r: 0.22 },
  { x: -0.13, y: 0.48, z: 0.87, r: 0.16 },
  { x: -0.61, y: -0.32, z: 0.73, r: 0.12 },
  { x: 0.42, y: -0.64, z: 0.64, r: 0.11 },
  { x: -0.80, y: 0.31, z: -0.51, r: 0.18 },
  { x: 0.21, y: -0.19, z: -0.96, r: 0.15 },
].map(c => ({ ...c, direction: new THREE.Vector3(c.x, c.y, c.z).normalize() }));

function craterRelief(x: number, y: number, z: number) {
  let relief = 0;
  for (const crater of CRATERS) {
    const dot = x * crater.direction.x + y * crater.direction.y + z * crater.direction.z;
    // Chord distance is a useful approximation for these small illustrative features.
    if (dot < 1 - crater.r * crater.r * 1.2) continue;
    const distance = Math.sqrt(Math.max(0, 2 - 2 * dot)) / crater.r;
    if (distance < 0.78) relief -= 0.10 * (1 - distance * distance);
    else if (distance < 1.15) relief += 0.055 * (1 - Math.abs(distance - 0.95) / 0.2);
  }
  return relief;
}

function makePlanetTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 256;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const image = context.createImageData(canvas.width, canvas.height);
  for (let y = 0; y < canvas.height; y++) {
    const latitude = Math.PI * (y / (canvas.height - 1));
    const sy = Math.cos(latitude), latitudeRadius = Math.sin(latitude);
    for (let x = 0; x < canvas.width; x++) {
      const longitude = (x / (canvas.width - 1)) * Math.PI * 2;
      const sx = -Math.cos(longitude) * latitudeRadius, sz = Math.sin(longitude) * latitudeRadius;
      const height = terrain(sx, sy, sz) + craterRelief(sx, sy, sz);
      const fine = noise(sx * 48 + 11, sy * 48, sz * 48);
      const dark = Math.max(0, Math.min(1, (height - 0.28) / 0.42));
      const dust = Math.max(0, Math.min(1, (height - 0.48) / 0.30));
      const ice = Math.max(0, (Math.abs(sy) - 0.91) / 0.09) * (0.35 + fine * 0.30);
      const grain = (fine - 0.5) * 13;
      const offset = (y * canvas.width + x) * 4;
      image.data[offset] = 91 + dark * 90 + dust * 27 + grain + ice * 54;
      image.data[offset + 1] = 48 + dark * 44 + dust * 27 + grain * 0.6 + ice * 68;
      image.data[offset + 2] = 43 + dark * 31 + dust * 21 + grain * 0.5 + ice * 70;
      image.data[offset + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 2;
  return texture;
}

function makePlanet() {
  const group = new THREE.Group();
  const geometry = new THREE.SphereGeometry(PLANET_RADIUS, 96, 64);
  const positions = geometry.attributes.position;
  const point = new THREE.Vector3();
  for (let i = 0; i < positions.count; i++) {
    point.fromBufferAttribute(positions, i).normalize();
    const relief = (terrain(point.x, point.y, point.z) - 0.5) * 0.11
      + craterRelief(point.x, point.y, point.z) * 0.12;
    point.multiplyScalar(PLANET_RADIUS + relief);
    positions.setXYZ(i, point.x, point.y, point.z);
  }
  geometry.computeVertexNormals();
  const texture = makePlanetTexture();
  const material = new THREE.MeshStandardMaterial({
    color: texture ? 0xffffff : 0xb56d50,
    map: texture,
    roughness: 0.91,
    metalness: 0.02,
  });
  group.add(new THREE.Mesh(geometry, material));

  // A subtle additive fresnel shell gives the limb its atmospheric light.
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(PLANET_RADIUS * 1.045, 64, 48),
    new THREE.ShaderMaterial({
      uniforms: {},
      vertexShader: `varying vec3 vNormal; varying vec3 vView;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vNormal = normalize(normalMatrix * normal); vView = normalize(-mv.xyz);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying vec3 vNormal; varying vec3 vView;
        void main() { float rim = pow(1.0 - max(0.0, dot(normalize(vNormal), normalize(vView))), 2.6);
          gl_FragColor = vec4(0.24, 0.60, 0.90, rim * 0.42); }`,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  group.add(atmosphere);
  return group;
}

function orbitLine(radiusX: number, radiusZ: number, color: number, opacity: number, dashed = false) {
  const points: THREE.Vector3[] = [];
  for (let i = 0; i <= 240; i++) {
    const angle = i / 240 * Math.PI * 2;
    points.push(new THREE.Vector3(Math.cos(angle) * radiusX, 0, Math.sin(angle) * radiusZ));
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.06, gapSize: 0.09, depthWrite: false })
    : new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
  const line = new THREE.Line(geometry, material);
  if (dashed) line.computeLineDistances();
  return line;
}

function makeShip() {
  const ship = new THREE.Group();
  const metal = new THREE.MeshStandardMaterial({ color: 0xdcebf0, metalness: 0.55, roughness: 0.35 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x1e4d60, metalness: 0.45, roughness: 0.45 });
  const bright = new THREE.MeshBasicMaterial({ color: 0x88f4ff });
  const gold = new THREE.MeshStandardMaterial({ color: 0xe8ae67, metalness: 0.4, roughness: 0.45 });
  const hull = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.08, 0.32, 10), metal);
  hull.rotation.z = Math.PI / 2;
  ship.add(hull);
  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.16, 10), metal);
  nose.rotation.z = -Math.PI / 2;
  nose.position.x = 0.24;
  ship.add(nose);
  const engine = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.055, 0.12, 10), gold);
  engine.rotation.z = Math.PI / 2;
  engine.position.x = -0.21;
  ship.add(engine);
  const light = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 8), bright);
  light.position.x = -0.28;
  ship.add(light);
  for (const side of [-1, 1]) {
    const wing = new THREE.Mesh(new THREE.BoxGeometry(0.20, 0.025, 0.30), dark);
    wing.position.z = side * 0.23;
    ship.add(wing);
    const spar = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.035, 0.39), metal);
    spar.position.z = side * 0.19;
    ship.add(spar);
    const wingEdge = new THREE.Mesh(new THREE.BoxGeometry(0.21, 0.03, 0.015), bright);
    wingEdge.position.z = side * 0.38;
    ship.add(wingEdge);
  }
  return ship;
}

function makeStars() {
  // A local seeded generator gives a stable, sparse star field without downloaded assets.
  let seed = 82317;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const positions: number[] = [], colors: number[] = [];
  for (let i = 0; i < 170; i++) {
    positions.push((random() - 0.5) * 22, (random() - 0.5) * 13, -3 - random() * 7);
    const strength = 0.35 + random() * 0.65;
    colors.push(strength * 0.67, strength * 0.82, strength);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  return new THREE.Points(geometry, new THREE.PointsMaterial({ size: 0.045, vertexColors: true, transparent: true, opacity: 0.78, depthWrite: false }));
}

function disposeScene(scene: THREE.Scene) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  scene.traverse(object => {
    const renderable = object as THREE.Mesh;
    if (renderable.geometry) geometries.add(renderable.geometry);
    if (renderable.material) {
      const entries = Array.isArray(renderable.material) ? renderable.material : [renderable.material];
      entries.forEach(material => {
        materials.add(material);
        Object.values(material).forEach(value => { if (value instanceof THREE.Texture) textures.add(value); });
      });
    }
  });
  textures.forEach(texture => texture.dispose());
  materials.forEach(material => material.dispose());
  geometries.forEach(geometry => geometry.dispose());
}

/** A conceptual, self-contained visualization. It intentionally consumes no mission data. */
export function MissionOrbit() {
  const hostRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const pausedRef = useRef(false);
  const [paused, setPaused] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [status, setStatus] = useState<SceneStatus>('loading');
  const headingId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    setReducedMotion(Boolean(motionQuery?.matches));
    let renderer: THREE.WebGLRenderer | null = null;
    const scene = new THREE.Scene();
    let frame: number | null = null;
    let previousTime: number | null = null;
    let inView = false;
    let hasSize = false;
    let disposed = false;
    let lost = false;
    let azimuth = INITIAL_AZIMUTH;
    let polar = INITIAL_POLAR;
    let phase = 0.55;
    let resizeObserver: ResizeObserver | undefined;
    let intersectionObserver: IntersectionObserver | undefined;
    let cleanupExtra = () => {};
    let cleaned = false;
    const cancelFrame = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      previousTime = null;
    };
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      disposed = true;
      cancelFrame();
      controlsRef.current = null;
      resizeObserver?.disconnect();
      intersectionObserver?.disconnect();
      cleanupExtra();
      disposeScene(scene);
      if (renderer) {
        renderer.dispose();
        renderer.domElement.remove();
      }
    };

    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.12;
      renderer.domElement.setAttribute('aria-hidden', 'true');
      host.appendChild(renderer.domElement);
      const canvas = renderer.domElement;
      const camera = new THREE.PerspectiveCamera(37, 1, 0.1, 100);
      const world = new THREE.Group();
      scene.add(world);
      scene.add(makeStars());
      scene.add(new THREE.AmbientLight(0x9ebfd5, 1.0));
      const sun = new THREE.DirectionalLight(0xffc49a, 3.2);
      sun.position.set(-3, 4, 5);
      scene.add(sun);
      const fill = new THREE.DirectionalLight(0x599dff, 1.35);
      fill.position.set(4, -1, -3);
      scene.add(fill);
      const planet = makePlanet();
      planet.rotation.z = -0.12;
      world.add(planet);

      const primaryOrbit = new THREE.Group();
      primaryOrbit.rotation.set(0.16, 0.05, -0.33);
      primaryOrbit.add(orbitLine(2.18, 2.18, 0x79dce9, 0.74));
      const ship = makeShip();
      primaryOrbit.add(ship);
      world.add(primaryOrbit);
      const outerOrbit = orbitLine(2.75, 2.75, 0x9e8df3, 0.45, true);
      outerOrbit.rotation.set(-0.35, 0.12, 0.27);
      world.add(outerOrbit);
      const innerOrbit = orbitLine(1.76, 1.76, 0xe7ad71, 0.34);
      innerOrbit.rotation.set(0.52, 0, 0.58);
      world.add(innerOrbit);
      const marker = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffc67c }));
      marker.position.set(-1.9, 0, 1.44);
      primaryOrbit.add(marker);
      const markerHalo = new THREE.Mesh(new THREE.SphereGeometry(0.095, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffb762, transparent: true, opacity: 0.15, depthWrite: false }));
      marker.add(markerHalo);

      const drawable = () => !disposed && !lost && inView && !document.hidden && hasSize;
      const moving = () => !pausedRef.current && !motionQuery?.matches;
      const draw = (time: number) => {
        frame = null;
        if (!drawable() || !renderer) return;
        if (moving()) {
          const delta = previousTime === null ? 0 : Math.min((time - previousTime) / 1000, 0.05);
          previousTime = time;
          planet.rotation.y += delta * 0.055;
          phase += delta * 0.12;
        } else {
          previousTime = null;
        }
        ship.position.set(Math.cos(phase) * 2.18, 0, Math.sin(phase) * 2.18);
        ship.rotation.y = -phase - Math.PI / 2;
        const distance = 6.9;
        camera.position.set(distance * Math.sin(polar) * Math.sin(azimuth), distance * Math.cos(polar), distance * Math.sin(polar) * Math.cos(azimuth));
        camera.lookAt(0, 0, 0);
        renderer.render(scene, camera);
        if (moving()) schedule();
      };
      const schedule = () => {
        if (drawable() && frame === null) frame = window.requestAnimationFrame(draw);
      };
      const resize = () => {
        if (!renderer) return;
        const width = host.clientWidth, height = host.clientHeight;
        hasSize = width > 0 && height > 0;
        if (!hasSize) { cancelFrame(); return; }
        const aspect = width / height;
        camera.aspect = aspect;
        camera.updateProjectionMatrix();
        world.position.x = aspect > 1.8 ? 1.12 : aspect > 1.2 ? 0.55 : 0;
        world.position.y = aspect > 1.2 ? -0.05 : -0.35;
        renderer.setSize(width, height, false);
        schedule();
      };
      const updateVisibility = () => {
        if (!drawable()) cancelFrame();
        else schedule();
      };
      const onMotionChange = () => {
        setReducedMotion(Boolean(motionQuery?.matches));
        cancelFrame();
        schedule();
      };
      const onContextLost = (event: Event) => {
        event.preventDefault();
        lost = true;
        cleanup();
        setStatus('unavailable');
      };
      let dragging = false;
      let pointerX = 0, pointerY = 0;
      const onPointerDown = (event: PointerEvent) => {
        if (event.button !== 0) return;
        dragging = true;
        pointerX = event.clientX;
        pointerY = event.clientY;
        canvas.setPointerCapture(event.pointerId);
        canvas.classList.add('is-dragging');
      };
      const onPointerMove = (event: PointerEvent) => {
        if (!dragging) return;
        azimuth -= (event.clientX - pointerX) * 0.006;
        polar = Math.max(0.5, Math.min(2.2, polar + (event.clientY - pointerY) * 0.006));
        pointerX = event.clientX;
        pointerY = event.clientY;
        schedule();
      };
      const onPointerEnd = () => { dragging = false; canvas.classList.remove('is-dragging'); };
      const checkBounds = () => {
        const rect = host.getBoundingClientRect();
        inView = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
        updateVisibility();
      };
      controlsRef.current = {
        rotate: (horizontal, vertical) => {
          azimuth += horizontal;
          polar = Math.max(0.5, Math.min(2.2, polar + vertical));
          schedule();
        },
        reset: () => { azimuth = INITIAL_AZIMUTH; polar = INITIAL_POLAR; schedule(); },
        redraw: () => { cancelFrame(); schedule(); },
      };
      cleanupExtra = () => {
        window.removeEventListener('resize', resize);
        window.removeEventListener('resize', checkBounds);
        window.removeEventListener('scroll', checkBounds);
        document.removeEventListener('visibilitychange', updateVisibility);
        motionQuery?.removeEventListener?.('change', onMotionChange);
        canvas.removeEventListener('webglcontextlost', onContextLost);
        canvas.removeEventListener('pointerdown', onPointerDown);
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('pointerup', onPointerEnd);
        canvas.removeEventListener('pointercancel', onPointerEnd);
        canvas.removeEventListener('lostpointercapture', onPointerEnd);
      };
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(host);
      } else {
        window.addEventListener('resize', resize);
      }
      if (typeof IntersectionObserver !== 'undefined') {
        intersectionObserver = new IntersectionObserver(entries => {
          inView = entries[0]?.isIntersecting ?? false;
          updateVisibility();
        });
        intersectionObserver.observe(host);
      } else {
        window.addEventListener('scroll', checkBounds, { passive: true });
        window.addEventListener('resize', checkBounds);
        checkBounds();
      }
      document.addEventListener('visibilitychange', updateVisibility);
      motionQuery?.addEventListener?.('change', onMotionChange);
      canvas.addEventListener('webglcontextlost', onContextLost);
      canvas.addEventListener('pointerdown', onPointerDown);
      canvas.addEventListener('pointermove', onPointerMove);
      canvas.addEventListener('pointerup', onPointerEnd);
      canvas.addEventListener('pointercancel', onPointerEnd);
      canvas.addEventListener('lostpointercapture', onPointerEnd);
      resize();
      setStatus('ready');
    } catch {
      cleanup();
      setStatus('unavailable');
    }
    return cleanup;
  }, []);

  const togglePause = () => {
    const next = !pausedRef.current;
    pausedRef.current = next;
    setPaused(next);
    controlsRef.current?.redraw();
  };
  const available = status === 'ready';
  const motionLabel = status === 'unavailable' ? '3D UNAVAILABLE' : reducedMotion ? 'MOTION REDUCED' : paused ? 'PAUSED' : 'ILLUSTRATIVE MODE';

  return <section className="mission-orbit" aria-labelledby={headingId} aria-describedby={descriptionId}>
    <div className="mission-orbit__header">
      <div className="mission-orbit__heading">
        <span className="mission-orbit__heading-icon" aria-hidden="true"><span /></span>
        <div><span className="mission-orbit__eyebrow">MISSION VISUALIZATION <i>//</i> 01</span><h2 id={headingId}>Mars transit</h2></div>
      </div>
      <div className="mission-orbit__badge"><span /> SIMULATED <i>/</i> ILLUSTRATIVE</div>
    </div>
    <p id={descriptionId} className="mission-orbit__sr-only">An illustrative 3D view of a Mars-like planet, orbital paths, markers and a stylized spacecraft. It is not scientific or actual flight data. Use the viewpoint buttons to rotate or tilt the scene, or drag the scene with a pointer. Animation stops when paused, off-screen, the tab is hidden, or reduced motion is preferred.</p>
    <div className="mission-orbit__stage">
      <div className="mission-orbit__canvas" ref={hostRef} />
      <div className="mission-orbit__stage-shade" aria-hidden="true" />
      <div className="mission-orbit__copy">
        <span className="mission-orbit__overline"><b /> A DIFFERENT PERSPECTIVE</span>
        <h3>Beyond the<br /><em>dashboard.</em></h3>
        <p>An imagined view of a Mars-like world and spacecraft. Not live or scientific flight data.</p>
        <div className="mission-orbit__copy-foot"><span /> CONCEPTUAL SCENE</div>
      </div>
      <div className="mission-orbit__stage-index" aria-hidden="true"><span>VIEW 01</span><strong>ORBITAL STUDY</strong></div>
      {status === 'unavailable' && <div className="mission-orbit__fallback" role="status"><strong>3D view unavailable</strong><span>WebGL is unavailable or disabled. The rest of the dashboard is unaffected.</span></div>}
      {status === 'loading' && <div className="mission-orbit__loading" aria-hidden="true">PREPARING SCENE</div>}
      <div className="mission-orbit__stage-bottom" aria-hidden="true"><span>NOT TO SCALE</span><span>DRAG TO EXPLORE <b>↗</b></span></div>
    </div>
    <div className="mission-orbit__footer">
      <div className="mission-orbit__legend" aria-label="Illustration legend"><span><i className="mission-orbit__dot mission-orbit__dot--amber" /> WORLD</span><span><i className="mission-orbit__dot mission-orbit__dot--cyan" /> PATH</span><span><i className="mission-orbit__dot mission-orbit__dot--violet" /> ALT. PATH</span></div>
      <div className="mission-orbit__controls" aria-label="Visualization controls">
        <span className="mission-orbit__control-label">VIEW</span>
        <div className="mission-orbit__direction-group" role="group" aria-label="Adjust viewpoint">
          <button type="button" aria-label="Rotate viewpoint left" title="Rotate left" disabled={!available} onClick={() => controlsRef.current?.rotate(-0.25, 0)}><ChevronLeft size={15} /></button>
          <button type="button" aria-label="Rotate viewpoint right" title="Rotate right" disabled={!available} onClick={() => controlsRef.current?.rotate(0.25, 0)}><ChevronRight size={15} /></button>
          <button type="button" aria-label="Tilt viewpoint up" title="Tilt up" disabled={!available} onClick={() => controlsRef.current?.rotate(0, -0.2)}><ChevronUp size={15} /></button>
          <button type="button" aria-label="Tilt viewpoint down" title="Tilt down" disabled={!available} onClick={() => controlsRef.current?.rotate(0, 0.2)}><ChevronDown size={15} /></button>
        </div>
        <button type="button" className="mission-orbit__action" disabled={!available} onClick={() => controlsRef.current?.reset()}><RotateCcw size={13} /> Reset<span className="mission-orbit__sr-only"> viewpoint</span></button>
        <button type="button" className="mission-orbit__action mission-orbit__action--pause" disabled={!available || reducedMotion} onClick={togglePause} title={reducedMotion ? 'Animation is disabled by your reduced-motion preference' : undefined}>{paused || reducedMotion ? <Play size={13} /> : <Pause size={13} />} {paused || reducedMotion ? 'Resume' : 'Pause'}</button>
      </div>
      <div className={`mission-orbit__motion${reducedMotion ? ' mission-orbit__motion--reduced' : ''}`}><span /> {motionLabel}</div>
    </div>
  </section>;
}
