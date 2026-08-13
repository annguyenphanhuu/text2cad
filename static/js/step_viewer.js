/**
 * STEP 3D Viewer
 *
 * Renders STEP (.step/.stp) files directly in the browser. The B-rep is
 * tessellated client-side by occt-import-js (OpenCascade compiled to WASM,
 * vendored under /static/vendor/occt), so no server-side mesh conversion step
 * is involved.
 *
 * Coordinates are used exactly as they come out of the STEP file — no axis
 * remapping — which matches how the OBJ viewer treats its input.
 */

// Where the vendored OpenCascade build lives.
const OCCT_SCRIPT_URL = '/static/vendor/occt/occt-import-js.js';
const OCCT_WASM_URL = '/static/vendor/occt/occt-import-js.wasm';

// Tessellation quality. bounding_box_ratio keeps the triangle budget roughly
// constant regardless of how large the part is.
const OCCT_READ_PARAMS = {
    linearUnit: 'millimeter',
    linearDeflectionType: 'bounding_box_ratio',
    linearDeflection: 0.001,
    angularDeflection: 0.5
};

/**
 * Load and instantiate the OpenCascade WASM module.
 *
 * The wasm payload is ~7.6 MB, so this is deferred until the first model is
 * actually loaded and then memoised for the lifetime of the page.
 */
let _occtPromise = null;
function getOcct() {
    if (_occtPromise) return _occtPromise;

    _occtPromise = new Promise((resolve, reject) => {
        const start = (factory) => {
            factory({ locateFile: () => OCCT_WASM_URL }).then(resolve, reject);
        };

        if (typeof occtimportjs !== 'undefined') {
            start(occtimportjs);
            return;
        }

        const script = document.createElement('script');
        script.src = OCCT_SCRIPT_URL;
        script.onload = () => {
            if (typeof occtimportjs === 'undefined') {
                reject(new Error('occt-import-js loaded but did not register itself'));
                return;
            }
            start(occtimportjs);
        };
        script.onerror = () => reject(new Error(`Failed to load ${OCCT_SCRIPT_URL}`));
        document.head.appendChild(script);
    }).catch((err) => {
        // Let a later load retry from scratch rather than caching the failure.
        _occtPromise = null;
        throw err;
    });

    return _occtPromise;
}

class StepModelViewer3D {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.currentModel = null;
        this.modelGroup = null;
        this.isWireframe = false;
        this.edgeGroup = null;
        this.showEdges = true;

        // ViewCube navigation properties
        this.viewCubeScene = null;
        this.viewCubeCamera = null;
        this.viewCubeRenderer = null;
        this.viewCubeContainer = null;
        this.viewCube = null;
        this.isViewCubeVisible = false; // Hidden by default, shown after a model loads
        this.viewCubeRaycaster = new THREE.Raycaster();
        this.viewCubeMouse = new THREE.Vector2();

        this.modelInfo = {
            vertices: 0,
            triangles: 0,
            solids: 0,
            fileName: ''
        };

        this.init();
    }

    init() {
        console.log('🎯 Initializing STEP 3D Viewer...');

        if (typeof THREE === 'undefined') {
            console.error('THREE.js not loaded for STEP viewer');
            this.showStatus('THREE.js failed to load');
            return;
        }

        const container = document.getElementById('step-viewer-3d');
        if (!container) {
            console.error('STEP viewer container not found');
            return;
        }

        const placeholder = container.querySelector('.viewer-placeholder');
        if (placeholder) placeholder.remove();
        const existingCanvas = container.querySelector('canvas');
        if (existingCanvas) existingCanvas.remove();

        this.modelGroup = new THREE.Group();
        this.edgeGroup = new THREE.Group();

        // Scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0xf0f0f0); // Match OBJ viewer background

        // Camera
        const containerRect = container.getBoundingClientRect();
        this.camera = new THREE.PerspectiveCamera(45, containerRect.width / containerRect.height, 0.1, 1000);
        this.camera.position.set(5, 5, 5);
        this.camera.lookAt(0, 0, 0);

        // Renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
        this.renderer.setSize(containerRect.width, containerRect.height);
        container.appendChild(this.renderer.domElement);

        // Lights (matched to OBJ viewer)
        this.scene.add(new THREE.AmbientLight(0xffffff, 0.65));

        const directionalLight1 = new THREE.DirectionalLight(0xffffff, 0.5);
        directionalLight1.position.set(10, 10, 10);
        this.scene.add(directionalLight1);

        const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.35);
        directionalLight2.position.set(-10, 5, -5);
        this.scene.add(directionalLight2);

        const directionalLight3 = new THREE.DirectionalLight(0xffffff, 0.25);
        directionalLight3.position.set(5, -10, -7);
        this.scene.add(directionalLight3);

        this.modelGroup.add(this.edgeGroup);
        this.scene.add(this.modelGroup);

        // Controls (matched to OBJ viewer)
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.1;
        this.controls.rotateSpeed = 0.7;
        this.controls.panSpeed = 0.7;
        this.controls.zoomSpeed = 1.2;
        this.controls.screenSpacePanning = true;
        this.controls.mouseButtons = {
            LEFT: THREE.MOUSE.ROTATE,
            MIDDLE: THREE.MOUSE.PAN,
            RIGHT: THREE.MOUSE.PAN
        };

        this.animate();

        window.addEventListener('resize', () => this.onWindowResize());

        this.setupEventListeners();
        this.setupViewCube();

        this.showStatus('STEP Viewer ready');
        console.log('✅ STEP 3D Viewer initialized successfully');
    }

    animate() {
        requestAnimationFrame(() => this.animate());

        if (this.controls) {
            this.controls.update(); // Required for damping
        }

        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }

        // Render ViewCube in sync with the main camera
        if (this.viewCube && this.isViewCubeVisible && this.viewCubeRenderer) {
            // Inverse quaternion gives the mirror effect: when the camera rotates
            // left the cube rotates right.
            this.viewCube.quaternion.copy(this.camera.quaternion.clone().invert());
            this.viewCubeRenderer.render(this.viewCubeScene, this.viewCubeCamera);
        }
    }

    onWindowResize() {
        if (!this.camera || !this.renderer) return;

        const container = document.getElementById('step-viewer-3d');
        if (!container) return;

        const containerRect = container.getBoundingClientRect();
        this.camera.aspect = containerRect.width / containerRect.height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(containerRect.width, containerRect.height);
    }

    showStatus(message) {
        console.log(`STEP Viewer: ${message}`);
    }

    showLoading(show, message) {
        const loadingEl = document.getElementById('step-viewer-loading');
        if (loadingEl) {
            loadingEl.style.display = show ? 'block' : 'none';
            const textEl = loadingEl.querySelector('.viewer-loading-text');
            if (textEl && message) textEl.textContent = message;
        }
    }

    // ────────────────────────────────────────────────────────────
    // Model loading
    // ────────────────────────────────────────────────────────────

    /**
     * Resolve whatever path shape the backend handed us into a URL the
     * step-viewer endpoint can serve.
     */
    resolveStepUrl(stepPath) {
        if (stepPath.startsWith('/api/step-viewer/')) return stepPath;

        if (stepPath.includes('/download/')) {
            return `/api/step-viewer/${stepPath.split('/download/')[1]}`;
        }

        if (stepPath.startsWith('http://') || stepPath.startsWith('https://')) {
            try {
                return `/api/step-viewer${new URL(stepPath).pathname}`;
            } catch (e) {
                console.warn('⚠️ Failed to parse URL, using as-is:', stepPath);
                return `/api/step-viewer/${stepPath}`;
            }
        }

        return stepPath.startsWith('/')
            ? `/api/step-viewer${stepPath}`
            : `/api/step-viewer/${stepPath}`;
    }

    /** Main entry point, mirrors the OBJ viewer's loadModel interface. */
    async loadModel(stepPath, fileName = '') {
        try {
            console.log('🔄 Loading STEP model:', stepPath);
            this.showLoading(true, 'Downloading STEP file...');
            this.hideControls();

            this.modelInfo.fileName = fileName || stepPath.split('/').pop() || 'model.step';

            const apiPath = this.resolveStepUrl(stepPath);
            console.log('🌐 Final API path:', apiPath);

            const response = await fetch(apiPath);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const buffer = await response.arrayBuffer();
            await this.loadStepBuffer(buffer);

            return this.currentModel;
        } catch (error) {
            console.error('❌ Error loading STEP model:', error);
            this.showStatus(`Error: ${error.message}`);
            this.showLoading(false);
            throw error;
        }
    }

    /** Load a STEP file picked from the local file input. */
    loadStepFile(file) {
        this.modelInfo.fileName = file.name;
        this.showLoading(true, 'Reading STEP file...');

        const reader = new FileReader();
        reader.onload = (e) => {
            this.loadStepBuffer(e.target.result).catch((error) => {
                console.error('❌ Error loading STEP file:', error);
                this.showStatus(`Error: ${error.message}`);
                this.showLoading(false);
            });
        };
        reader.onerror = () => {
            this.showStatus('Failed to read file');
            this.showLoading(false);
        };
        reader.readAsArrayBuffer(file);
    }

    /** Tessellate a STEP payload with OpenCascade and build the Three.js scene. */
    async loadStepBuffer(arrayBuffer) {
        this.showLoading(true, 'Tessellating (first load downloads ~7 MB)...');

        const occt = await getOcct();

        this.showLoading(true, 'Tessellating STEP geometry...');
        const result = occt.ReadStepFile(new Uint8Array(arrayBuffer), OCCT_READ_PARAMS);

        if (!result || !result.success) {
            throw new Error('OpenCascade could not read this STEP file');
        }
        if (!result.meshes || result.meshes.length === 0) {
            throw new Error('STEP file contains no renderable solids');
        }

        this.clearModel();

        let totalVertices = 0;
        let totalTriangles = 0;

        result.meshes.forEach((occtMesh, index) => {
            const mesh = this.createMeshFromOcct(occtMesh, index);
            if (!mesh) return;

            this.modelGroup.add(mesh);
            totalVertices += mesh.geometry.attributes.position.count;
            totalTriangles += mesh.geometry.index
                ? mesh.geometry.index.count / 3
                : mesh.geometry.attributes.position.count / 3;

            this.addEdgeOverlay(mesh);
        });

        if (this.modelGroup.children.length <= 1) { // only edgeGroup
            throw new Error('STEP file produced no usable meshes');
        }

        this.currentModel = this.modelGroup;

        this.centerModel();
        this.updateModelInfo(totalVertices, Math.round(totalTriangles), result.meshes.length);
        this.updateGeometryInfo();

        this.showLoading(false);
        this.showControls();
        this.showInfo();

        // The ViewCube is only useful once there is something to orient against.
        this.isViewCubeVisible = true;
        if (this.viewCubeContainer) this.viewCubeContainer.style.display = 'block';
        const cubeBtn = document.getElementById('step-toggle-viewcube-btn');
        if (cubeBtn) cubeBtn.classList.add('btn-active');

        console.log(`✅ STEP model loaded: ${result.meshes.length} solids, ${totalVertices} vertices, ${Math.round(totalTriangles)} triangles`);
        return this.currentModel;
    }

    /**
     * Convert one occt-import-js mesh into a THREE.Mesh.
     *
     * occt hands back flat Float64 arrays for position/normal and a flat index
     * array, which map straight onto BufferGeometry attributes.
     */
    createMeshFromOcct(occtMesh, index) {
        try {
            const positions = occtMesh.attributes?.position?.array;
            if (!positions || positions.length === 0) return null;

            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));

            const normals = occtMesh.attributes?.normal?.array;
            if (normals && normals.length === positions.length) {
                geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
            }

            if (occtMesh.index?.array) {
                geometry.setIndex(Array.from(occtMesh.index.array));
            }

            if (!normals || normals.length !== positions.length) {
                geometry.computeVertexNormals();
            }

            // occt colours are normalised RGB triples; fall back to the neutral
            // grey the OBJ viewer uses.
            const material = new THREE.MeshLambertMaterial({
                color: occtMesh.color
                    ? new THREE.Color(occtMesh.color[0], occtMesh.color[1], occtMesh.color[2])
                    : 0x888888,
                side: THREE.DoubleSide
            });

            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = occtMesh.name || `step_solid_${index}`;
            return mesh;
        } catch (error) {
            console.error('STEP mesh error:', error);
            return null;
        }
    }

    /**
     * Draw the sharp edges of a solid so the part reads as CAD geometry rather
     * than a smooth blob. 20° keeps bend radii smooth while outlining faces.
     */
    addEdgeOverlay(mesh) {
        try {
            const edges = new THREE.EdgesGeometry(mesh.geometry, 20);
            const line = new THREE.LineSegments(
                edges,
                new THREE.LineBasicMaterial({ color: 0x333333 })
            );
            line.visible = this.showEdges;
            this.edgeGroup.add(line);
        } catch (error) {
            console.warn('Could not build edge overlay:', error);
        }
    }

    clearModel() {
        if (!this.modelGroup) return;

        if (this.viewCubeContainer) {
            this.isViewCubeVisible = false;
            this.viewCubeContainer.style.display = 'none';
        }

        const dispose = (child) => {
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(m => m.dispose());
                } else {
                    child.material.dispose();
                }
            }
        };

        while (this.edgeGroup.children.length > 0) {
            const child = this.edgeGroup.children[0];
            this.edgeGroup.remove(child);
            dispose(child);
        }

        while (this.modelGroup.children.length > 0) {
            const child = this.modelGroup.children[0];
            this.modelGroup.remove(child);
            if (child !== this.edgeGroup) dispose(child);
        }

        // edgeGroup is a permanent child of modelGroup
        this.modelGroup.add(this.edgeGroup);

        this.modelGroup.position.set(0, 0, 0);
        this.modelGroup.rotation.set(0, 0, 0);
        this.modelGroup.scale.set(1, 1, 1);
        this.modelGroup.visible = true;

        this.currentModel = null;
    }

    centerModel() {
        if (!this.modelGroup || this.modelGroup.children.length === 0) {
            console.warn('⚠️ [CENTER_MODEL] No model to center');
            return;
        }

        const size = new THREE.Box3().setFromObject(this.modelGroup).getSize(new THREE.Vector3());

        // Normalise to a fixed on-screen size so the camera framing below works
        // the same for a 10 mm bracket and a 2 m panel.
        const maxDim = Math.max(size.x, size.y, size.z);
        if (maxDim > 0) {
            this.modelGroup.scale.setScalar(5 / maxDim);
        } else {
            console.warn('⚠️ [CENTER_MODEL] Model has zero dimensions, using default scale');
            this.modelGroup.scale.setScalar(1);
        }

        // Recenter on the origin. This has to be measured AFTER scaling: the
        // group's transform scales about its own origin before translating, so
        // subtracting the unscaled centre would leave the model off by
        // centre * (scale - 1) on every axis that wasn't already centred.
        this.modelGroup.updateMatrixWorld(true);
        const scaledCenter = new THREE.Box3().setFromObject(this.modelGroup).getCenter(new THREE.Vector3());
        this.modelGroup.position.sub(scaledCenter);

        this.modelGroup.visible = true;
        this.resetCamera();
    }

    // ────────────────────────────────────────────────────────────
    // Info panels
    // ────────────────────────────────────────────────────────────

    updateModelInfo(vertices, triangles, solids) {
        this.modelInfo.vertices = vertices;
        this.modelInfo.triangles = triangles;
        this.modelInfo.solids = solids;

        const set = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };

        set('step-vertices-count', vertices);
        set('step-triangles-count', triangles);
        set('step-solids-count', solids);
        set('step-model-filename', this.modelInfo.fileName);
        set('step-model-info-text', `STEP - ${vertices} vertices, ${triangles} triangles, ${solids} solids`);

        console.log(`STEP Model Info: ${vertices} vertices, ${triangles} triangles, ${solids} solids`);
    }

    /**
     * Surface area and volume, measured off the tessellated mesh.
     *
     * These are computed in model units (mm) from the pre-scale geometry, so
     * they are independent of the display scaling applied in centerModel().
     * Volume uses the signed-tetrahedron sum, which is exact for a closed
     * polyhedron; both values carry the tessellation error of the mesh.
     */
    updateGeometryInfo() {
        const section = document.getElementById('step-geometry-info');
        if (!this.currentModel) {
            section?.classList.add('hidden');
            return;
        }

        let areaMm2 = 0;
        let volumeMm3 = 0;

        const a = new THREE.Vector3();
        const b = new THREE.Vector3();
        const c = new THREE.Vector3();
        const ab = new THREE.Vector3();
        const ac = new THREE.Vector3();
        const cross = new THREE.Vector3();

        this.modelGroup.children.forEach((child) => {
            if (!child.isMesh) return;

            const position = child.geometry.attributes.position;
            const index = child.geometry.index;
            const triangleCount = index ? index.count / 3 : position.count / 3;

            for (let i = 0; i < triangleCount; i++) {
                const i0 = index ? index.getX(i * 3) : i * 3;
                const i1 = index ? index.getX(i * 3 + 1) : i * 3 + 1;
                const i2 = index ? index.getX(i * 3 + 2) : i * 3 + 2;

                a.fromBufferAttribute(position, i0);
                b.fromBufferAttribute(position, i1);
                c.fromBufferAttribute(position, i2);

                ab.subVectors(b, a);
                ac.subVectors(c, a);
                cross.crossVectors(ab, ac);

                areaMm2 += cross.length() / 2;
                volumeMm3 += a.dot(cross) / 6;
            }
        });

        volumeMm3 = Math.abs(volumeMm3);

        if (areaMm2 <= 0) {
            section?.classList.add('hidden');
            return;
        }

        section?.classList.remove('hidden');

        const areaEl = document.getElementById('step-surface-area');
        if (areaEl) areaEl.textContent = `${(areaMm2 / 100).toFixed(2)} cm²`;

        const volumeEl = document.getElementById('step-volume');
        if (volumeEl) volumeEl.textContent = `${(volumeMm3 / 1000).toFixed(2)} cm³`;
    }

    // ────────────────────────────────────────────────────────────
    // ViewCube navigation
    // ────────────────────────────────────────────────────────────

    setupViewCube() {
        this.viewCubeContainer = document.createElement('div');
        this.viewCubeContainer.id = 'step-viewcube';
        this.viewCubeContainer.title = 'ViewCube Navigation\nClick faces to snap to standard views\nRed: X-axis, Green: Y-axis, Blue: Z-axis';
        this.viewCubeContainer.style.cssText = `
            position: absolute;
            top: 10px;
            right: 20px;
            width: 150px;
            height: 150px;
            z-index: 1000;
            pointer-events: auto;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.95);
            border: 2px solid rgba(0, 0, 0, 0.1);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(10px);
            cursor: pointer;
            transition: all 0.2s ease;
            display: none;
        `;

        this.viewCubeContainer.addEventListener('mouseenter', () => {
            this.viewCubeContainer.style.transform = 'scale(1.05)';
            this.viewCubeContainer.style.boxShadow = '0 8px 25px rgba(0, 0, 0, 0.3)';
        });

        this.viewCubeContainer.addEventListener('mouseleave', () => {
            this.viewCubeContainer.style.transform = 'scale(1)';
            this.viewCubeContainer.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.2)';
        });

        const viewerContainer = document.getElementById('step-viewer-3d');
        if (!viewerContainer) {
            console.error('❌ STEP viewer container not found');
            return;
        }
        viewerContainer.appendChild(this.viewCubeContainer);

        this.viewCubeScene = new THREE.Scene();
        this.viewCubeScene.background = null; // Transparent

        this.viewCubeScene.add(new THREE.AmbientLight(0xffffff, 0.6));
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(1, 1, 1);
        this.viewCubeScene.add(directionalLight);

        this.viewCubeCamera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
        this.viewCubeCamera.position.set(0, 0, 3.5);
        this.viewCubeCamera.lookAt(0, 0, 0);

        this.viewCubeRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.viewCubeRenderer.setSize(150, 150);
        this.viewCubeRenderer.setClearColor(0x000000, 0); // Transparent
        this.viewCubeContainer.appendChild(this.viewCubeRenderer.domElement);

        this.createViewCube();

        this.viewCubeContainer.addEventListener('click', (event) => this.onViewCubeClick(event));
    }

    createViewCube() {
        const geometry = new THREE.BoxGeometry(1.2, 1.2, 1.2);

        // FreeCAD Z-up mapping: Z=TOP/BOTTOM (blue), Y=FRONT/REAR (green), X=LEFT/RIGHT (red)
        const materials = [
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('RIGHT', '#ffe0e0') }),  // X+
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('LEFT', '#ffe0e0') }),   // X-
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('REAR', '#e0ffe0') }),   // Y+
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('FRONT', '#e0ffe0') }),  // Y-
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('TOP', '#e0e0ff') }),    // Z+
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('BOTTOM', '#e0e0ff') })  // Z-
        ];

        this.viewCube = new THREE.Mesh(geometry, materials);
        this.viewCubeScene.add(this.viewCube);

        this.addCoordinateAxes();
    }

    createFaceTexture(label, color) {
        const canvas = document.createElement('canvas');
        canvas.width = 256;
        canvas.height = 256;
        const ctx = canvas.getContext('2d');

        const gradient = ctx.createLinearGradient(0, 0, 256, 256);
        gradient.addColorStop(0, color);
        gradient.addColorStop(1, '#f5f5f5');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 256, 256);

        ctx.strokeStyle = '#999';
        ctx.lineWidth = 4;
        ctx.strokeRect(0, 0, 256, 256);

        ctx.fillStyle = '#000';
        ctx.font = 'Bold 48px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, 128, 128);

        return new THREE.CanvasTexture(canvas);
    }

    addCoordinateAxes() {
        const axisLength = 0.8;
        const axisRadius = 0.015;

        const addAxis = (color, orient, position, labelPosition, labelText) => {
            const mesh = new THREE.Mesh(
                new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8),
                new THREE.MeshBasicMaterial({ color })
            );
            orient(mesh);
            mesh.position.copy(position);
            this.viewCube.add(mesh);

            const label = this.createAxisLabel(labelText, color);
            label.position.copy(labelPosition);
            this.viewCube.add(label);
        };

        const offset = axisLength / 2 + 0.6;
        const labelOffset = axisLength + 0.8;

        addAxis(0xff3333, m => { m.rotation.z = -Math.PI / 2; },
            new THREE.Vector3(offset, 0, 0), new THREE.Vector3(labelOffset, 0, 0), 'X');
        addAxis(0x33ff33, () => { },
            new THREE.Vector3(0, offset, 0), new THREE.Vector3(0, labelOffset, 0), 'Y');
        addAxis(0x3333ff, m => { m.rotation.x = Math.PI / 2; },
            new THREE.Vector3(0, 0, offset), new THREE.Vector3(0, 0, labelOffset), 'Z');
    }

    createAxisLabel(text, color) {
        const canvas = document.createElement('canvas');
        canvas.width = 128;
        canvas.height = 128;
        const ctx = canvas.getContext('2d');

        ctx.fillStyle = `#${color.toString(16).padStart(6, '0')}`;
        ctx.font = 'Bold 80px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, 64, 64);

        const sprite = new THREE.Sprite(
            new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas) })
        );
        sprite.scale.set(0.3, 0.3, 1);
        return sprite;
    }

    onViewCubeClick(event) {
        const rect = this.viewCubeRenderer.domElement.getBoundingClientRect();
        this.viewCubeMouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this.viewCubeMouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        this.viewCubeRaycaster.setFromCamera(this.viewCubeMouse, this.viewCubeCamera);
        const intersects = this.viewCubeRaycaster.intersectObject(this.viewCube);

        if (intersects.length > 0) {
            this.snapToView(Math.floor(intersects[0].faceIndex / 2)); // 2 triangles per cube face
        }
    }

    snapToView(faceIndex) {
        if (!this.currentModel) {
            console.warn('⚠️ No model loaded, cannot snap to view');
            return;
        }

        const worldBox = new THREE.Box3().setFromObject(this.modelGroup);
        const size = worldBox.getSize(new THREE.Vector3());
        const distance = Math.max(size.x, size.y, size.z) * 2.5;
        const worldCenter = worldBox.getCenter(new THREE.Vector3());

        const offsets = [
            [distance, 0, 0],   // 0 RIGHT  (X+)
            [-distance, 0, 0],  // 1 LEFT   (X-)
            [0, distance, 0],   // 2 REAR   (Y+)
            [0, -distance, 0],  // 3 FRONT  (Y-)
            [0, 0, distance],   // 4 TOP    (Z+)
            [0, 0, -distance]   // 5 BOTTOM (Z-)
        ];

        const offset = offsets[faceIndex];
        if (!offset) {
            console.warn('⚠️ Unknown face index:', faceIndex);
            return;
        }

        this.animateCameraTo(
            new THREE.Vector3(
                worldCenter.x + offset[0],
                worldCenter.y + offset[1],
                worldCenter.z + offset[2]
            ),
            worldCenter
        );
    }

    animateCameraTo(targetPosition, targetLookAt) {
        const startPosition = this.camera.position.clone();
        const startLookAt = this.controls.target.clone();
        const duration = 300; // ms
        const startTime = Date.now();

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3); // Ease-out cubic

            this.camera.position.lerpVectors(startPosition, targetPosition, eased);
            this.controls.target.lerpVectors(startLookAt, targetLookAt, eased);

            this.camera.lookAt(this.controls.target);
            this.controls.update();

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        animate();
    }

    toggleViewCube() {
        this.isViewCubeVisible = !this.isViewCubeVisible;

        if (this.viewCubeContainer) {
            this.viewCubeContainer.style.display = this.isViewCubeVisible ? 'block' : 'none';
        }
    }

    // ────────────────────────────────────────────────────────────
    // Viewer controls
    // ────────────────────────────────────────────────────────────

    resetCamera() {
        if (this.modelGroup && this.modelGroup.children.length > 0) {
            const box = new THREE.Box3().setFromObject(this.modelGroup);
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            const distance = Math.max(size.x, size.y, size.z) * 2.5;

            // Isometric view, like CAD software
            this.camera.position.set(
                center.x + distance * 0.7,
                center.y + distance * 0.7,
                center.z + distance * 0.7
            );
            this.camera.lookAt(center);
            this.controls.target.copy(center);
        } else {
            this.camera.position.set(10, 10, 10);
            this.camera.lookAt(0, 0, 0);
            this.controls.target.set(0, 0, 0);
        }

        this.controls?.update();
        this.showStatus('STEP view reset');
    }

    toggleFullscreen() {
        const container = this.container;
        if (!container) {
            console.error('Fullscreen container not found');
            return;
        }

        if (!document.fullscreenElement) {
            container.requestFullscreen().then(() => {
                this.onWindowResize();
            }).catch(err => {
                console.error(`Error attempting to enable full-screen mode: ${err.message}`, err);
            });
        } else if (document.exitFullscreen) {
            document.exitFullscreen();
        }
    }

    toggleWireframe() {
        if (!this.modelGroup) return;

        this.isWireframe = !this.isWireframe;

        this.modelGroup.traverse((child) => {
            if (child.isMesh && child.material) {
                const materials = Array.isArray(child.material) ? child.material : [child.material];
                materials.forEach(mat => {
                    mat.wireframe = this.isWireframe;
                    mat.needsUpdate = true;
                });
            }
        });

        // The edge overlay only adds noise on top of a wireframe.
        this.edgeGroup.visible = this.showEdges && !this.isWireframe;

        const btn = document.getElementById('step-wireframe-toggle-btn');
        if (btn) {
            btn.classList.toggle('btn-active', this.isWireframe);
            btn.title = this.isWireframe ? 'Turn off Wireframe (currently ON)' : 'Turn on Wireframe';
        }

        this.showStatus(this.isWireframe ? 'Wireframe ON' : 'Wireframe OFF');
    }

    toggleEdges() {
        this.showEdges = !this.showEdges;
        this.edgeGroup.visible = this.showEdges && !this.isWireframe;

        const btn = document.getElementById('step-edges-toggle-btn');
        if (btn) {
            btn.classList.toggle('btn-active', this.showEdges);
            btn.title = this.showEdges ? 'Hide model edges (currently ON)' : 'Show model edges';
        }

        this.showStatus(this.showEdges ? 'Edges ON' : 'Edges OFF');
    }

    showColorPicker() {
        const color = prompt('Enter hex color (e.g., #ff0000):');
        if (!color || !this.modelGroup) return;

        const parsed = new THREE.Color(color);
        this.modelGroup.children.forEach(child => {
            if (child.isMesh && child.material) {
                child.material.color.copy(parsed);
            }
        });
        this.showStatus('Color changed');
    }

    takeScreenshot() {
        if (!this.renderer) return;

        const link = document.createElement('a');
        link.download = 'step_model_screenshot.png';
        link.href = this.renderer.domElement.toDataURL();
        link.click();
        this.showStatus('Screenshot saved');
    }

    showControls() {
        document.getElementById('step-viewer-controls')?.classList.remove('hidden');
    }

    hideControls() {
        document.getElementById('step-viewer-controls')?.classList.add('hidden');
    }

    showInfo() {
        document.getElementById('step-viewer-info')?.classList.remove('hidden');
    }

    setupEventListeners() {
        document.getElementById('fullscreen-step-viewer-btn')?.addEventListener('click', () => {
            this.toggleFullscreen();
        });

        document.getElementById('step-toggle-viewcube-btn')?.addEventListener('click', () => {
            this.toggleViewCube();
            const btn = document.getElementById('step-toggle-viewcube-btn');
            if (btn) {
                btn.classList.toggle('btn-active', this.isViewCubeVisible);
                btn.title = this.isViewCubeVisible ? 'Turn off ViewCube (currently ON)' : 'Turn on ViewCube Navigation';
            }
        });
    }
}

// ────────────────────────────────────────────────────────────────
// Wiring for the viewer's UI controls
// ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('load-step-btn')?.addEventListener('click', () => {
        document.getElementById('step-file-input').click();
    });

    document.getElementById('step-file-input')?.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const name = file.name.toLowerCase();
        if (name.endsWith('.step') || name.endsWith('.stp')) {
            window.stepViewer?.loadStepFile(file);
        } else {
            console.error('Please select a STEP file (.step or .stp)');
        }
    });

    document.getElementById('step-reset-camera-btn')?.addEventListener('click', () => {
        window.stepViewer?.resetCamera();
    });

    document.getElementById('step-wireframe-toggle-btn')?.addEventListener('click', () => {
        window.stepViewer?.toggleWireframe();
    });

    document.getElementById('step-edges-toggle-btn')?.addEventListener('click', () => {
        window.stepViewer?.toggleEdges();
    });

    document.getElementById('step-color-picker-btn')?.addEventListener('click', () => {
        window.stepViewer?.showColorPicker();
    });

    document.getElementById('step-screenshot-btn')?.addEventListener('click', () => {
        window.stepViewer?.takeScreenshot();
    });
});
