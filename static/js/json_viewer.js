/**
 * JSON 3D Viewer
 * Supports FreeCAD & OnShape JSON formats
 * Refactored to match OBJ viewer architecture
 */

class JsonModelViewer3D {
    constructor(containerId, options = {}) {
        this.FaceAnalyzer = options.FaceAnalyzer; // Inject FaceAnalyzer dependency
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.currentModel = null;
        this.modelGroup = null;
        this.jsonData = null; // Store the loaded JSON data
        this.mouseX = 0;
        this.mouseY = 0;
        this.isMouseDown = false;
        this.raycaster = null;
        this.mouse = null;
        this.selectedFace = null;
        this.originalMaterials = new Map();
        this.isWireframe = false;
        this.highlightMesh = null; // Store highlighted mesh for face selection\r
        this.highlightMeshes = []; // 🔥 Store multiple highlighted meshes for multi-face features (countersink)
        this.faceTooltip = null; // Store face tooltip element
        this.flashTimeoutId = null; // Store timeout ID for flash effect
        this.contextMenu = null; // Store context menu element
        this.contextMenuFaceInfo = null; // Store face info for context menu actions
        this.contextMenuTimer = null; // Store timer for auto-hiding the context menu
        this.faceAnalyzer = null; // Injected dependency for face analysis
        this.edgeAnalyzer = null; // Edge analyzer for Ctrl+Click edge selection
        this.highlightAutoHideTimer = null; // Timer for auto-hiding highlights
        this.edgePanelAutoHideTimer = null; // Timer for auto-hiding edge panel
        this.currentEdgeInfo = null; // Store current edge info for panel actions
        this.currentHighlight = null; // Store current highlight (for edge highlights)
        this.currentAnalysisResult = null; // Store current analysis result for copy function
        this.features = []; // 🔥 NEW: Store features array (countersink, chamfer, etc.) from JSON
        this.threadLabel = null; // 🆕 Store thread label sprite for threaded holes
        this._debugDotVisible = false; // Track debug dot state (toggled by UI button)

        // ViewCube navigation properties
        this.viewCubeScene = null;
        this.viewCubeCamera = null;
        this.viewCubeRenderer = null;
        this.viewCubeContainer = null;
        this.viewCube = null;
        this.isViewCubeVisible = false; // Hidden by default, show after model loads
        this.viewCubeFaces = {}; // Store face meshes for click detection
        this.viewCubeRaycaster = new THREE.Raycaster();
        this.viewCubeMouse = new THREE.Vector2();

        this.modelInfo = {
            vertices: 0,
            faces: 0,
            objects: 0,
            format: 'Unknown',
            fileName: ''
        };

        this.init();
    }

    init() {
        console.log('🎯 Initializing JSON 3D Viewer...');

        // Check if THREE is available
        if (typeof THREE === 'undefined') {
            console.error('THREE.js not loaded for JSON viewer');
            this.showStatus('THREE.js failed to load', 'error');
            return;
        }

        // Initialize FaceAnalyzer dependency
        if (typeof FaceAnalyzer !== 'undefined') {
            this.faceAnalyzer = new FaceAnalyzer();
            console.log('✅ FaceAnalyzer dependency injected successfully');
        } else {
            console.warn('⚠️ FaceAnalyzer class not found. Analysis will be disabled.');
        }

        // Initialize EdgeAnalyzer for edge selection
        this.initializeEdgeAnalyzer();

        const container = document.getElementById('json-viewer-3d');
        if (!container) {
            console.error('JSON viewer container not found');
            return;
        }


        const placeholder = container.querySelector('.viewer-placeholder');
        if (placeholder) placeholder.remove();
        const existingCanvas = container.querySelector('canvas');
        if (existingCanvas) existingCanvas.remove();

        // Initialize model group
        this.modelGroup = new THREE.Group();

        // Initialize raycaster and mouse
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();

        // Scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0xf0f0f0); // Match OBJ viewer background

        // Camera - match legacy viewer settings
        const containerRect = container.getBoundingClientRect();
        this.camera = new THREE.PerspectiveCamera(45, containerRect.width / containerRect.height, 0.1, 1000);
        this.camera.position.set(5, 5, 5); // Match legacy viewer initial position
        this.camera.lookAt(0, 0, 0);

        // Renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
        this.renderer.setSize(containerRect.width, containerRect.height);
        container.appendChild(this.renderer.domElement);

        // Lights (Matched to OBJ viewer)
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
        this.scene.add(ambientLight);

        const directionalLight1 = new THREE.DirectionalLight(0xffffff, 0.5);
        directionalLight1.position.set(10, 10, 10);
        this.scene.add(directionalLight1);

        const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.35);
        directionalLight2.position.set(-10, 5, -5);
        this.scene.add(directionalLight2);

        const directionalLight3 = new THREE.DirectionalLight(0xffffff, 0.25);
        directionalLight3.position.set(5, -10, -7);
        this.scene.add(directionalLight3);

        // Add model group
        this.scene.add(this.modelGroup);

        // Controls (Matched to OBJ viewer)
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

        // Start animation loop
        this.animate();

        // Handle resize
        window.addEventListener('resize', () => this.onWindowResize());

        // Set up event listeners for controls and face selection
        this.setupEventListeners();
        this.setupFaceSelectionListeners();

        // Setup ViewCube navigation
        this.setupViewCube();

        this.showStatus('JSON Viewer ready', 'success');
        console.log('✅ JSON 3D Viewer initialized successfully');
    }

    animate() {
        requestAnimationFrame(() => this.animate());

        if (this.controls) {
            this.controls.update(); // Required for damping
        }

        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }

        // Render ViewCube in sync with main camera
        if (this.viewCube && this.isViewCubeVisible && this.viewCubeRenderer) {
            // Sync ViewCube rotation using inverse quaternion (mirror effect)
            // When camera rotates left, ViewCube rotates right (like looking in a mirror)
            this.viewCube.quaternion.copy(this.camera.quaternion.clone().invert());

            // Render ViewCube
            this.viewCubeRenderer.render(this.viewCubeScene, this.viewCubeCamera);
        }
    }

    onWindowResize() {
        if (!this.camera || !this.renderer) return;

        const container = document.getElementById('json-viewer-3d');
        if (!container) return;

        const containerRect = container.getBoundingClientRect();
        this.camera.aspect = containerRect.width / containerRect.height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(containerRect.width, containerRect.height);
    }

    showStatus(message) {
        console.log(`JSON Viewer: ${message}`);
        // You can add a status display here if needed
    }

    showLoading(show) {
        const loadingEl = document.getElementById('json-viewer-loading');
        if (loadingEl) {
            loadingEl.style.display = show ? 'block' : 'none';
        }
    }

    /**
     * Setup ViewCube navigation widget
     */
    setupViewCube() {
        console.log('🎯 Setting up ViewCube navigation...');

        // Create ViewCube container
        this.viewCubeContainer = document.createElement('div');
        this.viewCubeContainer.id = 'json-viewcube';
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

        // Add hover effect
        this.viewCubeContainer.addEventListener('mouseenter', () => {
            this.viewCubeContainer.style.transform = 'scale(1.05)';
            this.viewCubeContainer.style.boxShadow = '0 8px 25px rgba(0, 0, 0, 0.3)';
        });

        this.viewCubeContainer.addEventListener('mouseleave', () => {
            this.viewCubeContainer.style.transform = 'scale(1)';
            this.viewCubeContainer.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.2)';
        });

        // Add to viewer container (append to same parent as renderer for proper overlay)
        const viewerContainer = document.getElementById('json-viewer-3d');
        if (viewerContainer) {
            // Append ViewCube to the same container as the renderer for proper positioning
            viewerContainer.appendChild(this.viewCubeContainer);
            console.log('✅ ViewCube container added to viewer');
        } else {
            console.error('❌ JSON viewer container not found');
            return;
        }

        // Create ViewCube scene
        this.viewCubeScene = new THREE.Scene();
        this.viewCubeScene.background = null; // Transparent

        // Add lighting
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
        this.viewCubeScene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(1, 1, 1);
        this.viewCubeScene.add(directionalLight);

        // Create ViewCube camera
        this.viewCubeCamera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
        this.viewCubeCamera.position.set(0, 0, 3.5);
        this.viewCubeCamera.lookAt(0, 0, 0);

        // Create ViewCube renderer
        this.viewCubeRenderer = new THREE.WebGLRenderer({
            antialias: true,
            alpha: true
        });
        this.viewCubeRenderer.setSize(150, 150);
        this.viewCubeRenderer.setClearColor(0x000000, 0); // Transparent
        this.viewCubeContainer.appendChild(this.viewCubeRenderer.domElement);

        // Create the ViewCube
        this.createViewCube();

        // Setup click handler
        this.viewCubeContainer.addEventListener('click', (event) => this.onViewCubeClick(event));

        console.log('✅ ViewCube setup complete');
    }

    /**
     * Create the ViewCube mesh with labeled faces
     */
    createViewCube() {
        console.log('🎨 Creating ViewCube mesh...');

        // Create cube geometry
        const geometry = new THREE.BoxGeometry(1.2, 1.2, 1.2);

        // Create materials for each face with labels
        // FreeCAD Z-up mapping: Z=TOP/BOTTOM (blue), Y=FRONT/REAR (green), X=LEFT/RIGHT (red)
        // FRONT is -Y, REAR is +Y
        const materials = [
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('RIGHT', '#ffe0e0') }),  // X+ (Red)
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('LEFT', '#ffe0e0') }),   // X- (Red)
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('REAR', '#e0ffe0') }),   // Y+ (Green)
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('FRONT', '#e0ffe0') }),  // Y- (Green)
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('TOP', '#e0e0ff') }),    // Z+ (Blue)
            new THREE.MeshBasicMaterial({ map: this.createFaceTexture('BOTTOM', '#e0e0ff') })  // Z- (Blue)
        ];

        // Create cube mesh
        this.viewCube = new THREE.Mesh(geometry, materials);
        this.viewCubeScene.add(this.viewCube);

        // Add coordinate axes lines
        this.addCoordinateAxes();

        console.log('✅ ViewCube mesh created');
    }

    /**
     * Create texture for a cube face with label
     */
    createFaceTexture(label, color) {
        const canvas = document.createElement('canvas');
        canvas.width = 256;
        canvas.height = 256;
        const ctx = canvas.getContext('2d');

        // Background with subtle gradient
        const gradient = ctx.createLinearGradient(0, 0, 256, 256);
        gradient.addColorStop(0, color);
        gradient.addColorStop(1, '#f5f5f5');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 256, 256);

        // Border
        ctx.strokeStyle = '#999';
        ctx.lineWidth = 4;
        ctx.strokeRect(0, 0, 256, 256);

        // Text
        ctx.fillStyle = '#000';
        ctx.font = 'Bold 48px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, 128, 128);

        return new THREE.CanvasTexture(canvas);
    }

    /**
     * Add small coordinate axes to ViewCube
     */
    addCoordinateAxes() {
        const axisLength = 0.8;
        const axisRadius = 0.015;

        // X-axis (Red)
        const xGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const xMaterial = new THREE.MeshBasicMaterial({ color: 0xff3333 });
        const xAxis = new THREE.Mesh(xGeometry, xMaterial);
        xAxis.rotation.z = -Math.PI / 2;
        xAxis.position.x = axisLength / 2 + 0.6;
        this.viewCube.add(xAxis);

        // Add X label
        const xLabel = this.createAxisLabel('X', 0xff3333);
        xLabel.position.set(axisLength + 0.8, 0, 0);
        this.viewCube.add(xLabel);

        // Y-axis (Green)
        const yGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const yMaterial = new THREE.MeshBasicMaterial({ color: 0x33ff33 });
        const yAxis = new THREE.Mesh(yGeometry, yMaterial);
        yAxis.position.y = axisLength / 2 + 0.6;
        this.viewCube.add(yAxis);

        // Add Y label
        const yLabel = this.createAxisLabel('Y', 0x33ff33);
        yLabel.position.set(0, axisLength + 0.8, 0);
        this.viewCube.add(yLabel);

        // Z-axis (Blue)
        const zGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const zMaterial = new THREE.MeshBasicMaterial({ color: 0x3333ff });
        const zAxis = new THREE.Mesh(zGeometry, zMaterial);
        zAxis.rotation.x = Math.PI / 2;
        zAxis.position.z = axisLength / 2 + 0.6;
        this.viewCube.add(zAxis);

        // Add Z label
        const zLabel = this.createAxisLabel('Z', 0x3333ff);
        zLabel.position.set(0, 0, axisLength + 0.8);
        this.viewCube.add(zLabel);
    }

    /**
     * Create axis label sprite
     */
    createAxisLabel(text, color) {
        const canvas = document.createElement('canvas');
        canvas.width = 128;
        canvas.height = 128;
        const ctx = canvas.getContext('2d');

        // Draw text
        ctx.fillStyle = `#${color.toString(16).padStart(6, '0')}`;
        ctx.font = 'Bold 80px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, 64, 64);

        const texture = new THREE.CanvasTexture(canvas);
        const spriteMaterial = new THREE.SpriteMaterial({ map: texture });
        const sprite = new THREE.Sprite(spriteMaterial);
        sprite.scale.set(0.3, 0.3, 1);

        return sprite;
    }

    /**
     * Handle ViewCube click to snap to standard views
     */
    onViewCubeClick(event) {
        console.log('🎯 ViewCube clicked');

        // Calculate mouse position in ViewCube renderer
        const rect = this.viewCubeRenderer.domElement.getBoundingClientRect();
        this.viewCubeMouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this.viewCubeMouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        // Raycast to detect which face was clicked
        this.viewCubeRaycaster.setFromCamera(this.viewCubeMouse, this.viewCubeCamera);
        const intersects = this.viewCubeRaycaster.intersectObject(this.viewCube);

        if (intersects.length > 0) {
            const faceIndex = Math.floor(intersects[0].faceIndex / 2); // Each face has 2 triangles
            console.log('🎯 Clicked face index:', faceIndex);

            // Snap camera to corresponding view
            this.snapToView(faceIndex);
        }
    }

    /**
     * Snap camera to standard view based on face index
     * 🔥 FIXED: Uses world bounding box (after transform) for correct camera positioning
     */
    snapToView(faceIndex) {
        if (!this.currentModel) {
            console.warn('⚠️ No model loaded, cannot snap to view');
            return;
        }

        // 🔥 FIXED: Use WORLD bounding box (current actual model position in scene)
        // NOT the cached pre-transform bounding box (modelBoundingBox)
        const worldBox = this.modelWorldBoundingBox || new THREE.Box3().setFromObject(this.modelGroup);
        const size = worldBox.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const distance = maxDim * 2.5;

        // 🔥 FIXED: Get world-space center (geometric center of current model position)
        const worldCenter = worldBox.getCenter(new THREE.Vector3());

        const viewNames = ['RIGHT', 'LEFT', 'TOP', 'BOTTOM', 'FRONT', 'REAR'];
        console.log(`📐 Snapping to ${viewNames[faceIndex]} view, worldCenter:`, worldCenter, 'distance:', distance);

        let targetPosition;
        switch (faceIndex) {
            case 0: // RIGHT (X+)
                targetPosition = new THREE.Vector3(worldCenter.x + distance, worldCenter.y, worldCenter.z);
                break;
            case 1: // LEFT (X-)
                targetPosition = new THREE.Vector3(worldCenter.x - distance, worldCenter.y, worldCenter.z);
                break;
            case 2: // TOP (Y+) — in Three.js Y is up in ViewCube, Z is up in FreeCAD world
                // TOP = camera looks DOWN from above = camera positioned at +Z (FreeCAD Z-up)
                targetPosition = new THREE.Vector3(worldCenter.x, worldCenter.y + distance, worldCenter.z);
                break;
            case 3: // BOTTOM (Y-)
                targetPosition = new THREE.Vector3(worldCenter.x, worldCenter.y - distance, worldCenter.z);
                break;
            case 4: // FRONT (Z+)
                targetPosition = new THREE.Vector3(worldCenter.x, worldCenter.y, worldCenter.z + distance);
                break;
            case 5: // REAR (Z-)
                targetPosition = new THREE.Vector3(worldCenter.x, worldCenter.y, worldCenter.z - distance);
                break;
            default:
                console.warn('⚠️ Unknown face index:', faceIndex);
                return;
        }

        // Animate camera transition
        this.animateCameraTo(targetPosition, worldCenter);
    }

    /**
     * Calculate optimal center based on shape type
     * Handles complex shapes like L-shape, tube, capot, coffert
     * 🔥 NOTE: This uses the ORIGINAL (pre-transform) bounding box for shape analysis
     * (vertex-based calculations, quadrant analysis etc. use original coordinates)
     * The result is used by FaceAnalyzer for face orientation — NOT for camera positioning.
     * For camera positioning, use modelWorldBoundingBox.
     */
    getOptimalCenter() {
        console.log(`🔍 [GET_OPTIMAL_CENTER] Called. jsonData exists: ${!!this.jsonData}, shape_type: ${this.jsonData?.shape_type || 'null'}`);

        // 🔥 Use the ORIGINAL (pre-transform) bounding box for shape-specific analysis
        // (vertex positions in analyzeWithFeatures are in original coordinates)
        const box = this.modelBoundingBox || new THREE.Box3().setFromObject(this.modelGroup);
        if (!this.modelBoundingBox) {
            console.warn('⚠️ modelBoundingBox not cached, calculating on-the-fly');
        } else {
            console.log(`✅ Using cached modelBoundingBox (original coords):`, {
                min: this.modelBoundingBox.min,
                max: this.modelBoundingBox.max,
                size: this.modelBoundingBox.getSize(new THREE.Vector3())
            });
        }

        const geometricCenter = box.getCenter(new THREE.Vector3());

        // If JSON has shape_type metadata, use shape-specific logic
        if (this.jsonData && this.jsonData.shape_type) {
            const shapeType = this.jsonData.shape_type.toLowerCase();
            console.log(`🎯 Calculating optimal center for shape type: ${shapeType}`);

            const center = this.getCenterByShapeType(shapeType, box);
            if (center) {
                console.log(`✅ Using shape-specific center:`, center);
                return center;
            }
        }

        // Fallback: Calculate mass center for complex shapes
        const massCenter = this.calculateMassCenter();
        if (massCenter) {
            console.log(`✅ Using calculated mass center:`, massCenter);
            return massCenter;
        }

        // Final fallback: Use geometric center
        console.log(`ℹ️ Using geometric center (fallback):`, geometricCenter);
        return geometricCenter;
    }

    /**
     * 🔴 DEBUG: Show a red dot at the optimal center position in world space
     * Used for debugging Face Orientation center calculation
     * NOTE: getOptimalCenter() already returns WORLD coordinates
     * (because findLShapeCorner/findTubeCenter/findUShapeCenter use child.matrixWorld internally)
     */
    showDebugCenterDot() {
        if (!this.currentModel) {
            console.warn('⚠️ [DEBUG_DOT] No model loaded');
            return;
        }

        // Remove existing debug dot
        this.hideDebugCenterDot();

        // Ensure matrixWorld is up to date before any vertex transforms
        this.modelGroup.updateWorldMatrix(true, true);

        // getOptimalCenter() returns world-space coordinates
        // (because all vertex-based methods apply child.matrixWorld internally)
        const worldPos = this.getOptimalCenter();
        console.log(`🔴 [DEBUG_DOT] Optimal center (world coords):`, worldPos);

        // Create a red sphere to mark the optimal center
        const geometry = new THREE.SphereGeometry(0.08, 16, 16);
        const material = new THREE.MeshBasicMaterial({
            color: 0xff0000,
            depthTest: false  // Always render on top
        });
        const dot = new THREE.Mesh(geometry, material);
        dot.position.copy(worldPos);
        dot.name = '__debug_center_dot__';
        dot.renderOrder = 999;  // Render on top
        this.scene.add(dot);

        // Also show world bounding box center (green) for comparison
        // This is what ViewCube uses for camera positioning
        if (this.modelWorldBoundingBox) {
            const worldBoxCenter = this.modelWorldBoundingBox.getCenter(new THREE.Vector3());
            const geomGeo = new THREE.SphereGeometry(0.05, 8, 8);
            const geomMat = new THREE.MeshBasicMaterial({ color: 0x00ff00, depthTest: false });
            const geomDot = new THREE.Mesh(geomGeo, geomMat);
            geomDot.position.copy(worldBoxCenter);
            geomDot.name = '__debug_geom_center_dot__';
            geomDot.renderOrder = 998;
            this.scene.add(geomDot);
            console.log(`🟢 [DEBUG_DOT] World geometric center (green):`, worldBoxCenter);
        }

        const shapeType = this.jsonData?.shape_type || 'unknown';
        console.log(`🔴 [DEBUG_DOT] Red dot = Optimal Center (shape: ${shapeType})`);
        console.log(`🟢 [DEBUG_DOT] Green dot = World Geometric Center (for ViewCube camera)`);
        this.showStatus(`🔴 Red=OptimalCenter (${shapeType})  🟢 Green=WorldCenter (ViewCube)`, 'success');
    }

    /**
     * 🔴 DEBUG: Remove debug center dots
     */
    hideDebugCenterDot() {
        const existingDot = this.scene.getObjectByName('__debug_center_dot__');
        if (existingDot) {
            existingDot.geometry.dispose();
            existingDot.material.dispose();
            this.scene.remove(existingDot);
        }
        const existingGeomDot = this.scene.getObjectByName('__debug_geom_center_dot__');
        if (existingGeomDot) {
            existingGeomDot.geometry.dispose();
            existingGeomDot.material.dispose();
            this.scene.remove(existingGeomDot);
        }
    }

    /**
     * Get center based on shape type metadata
     * 🔥 ENHANCED: L-shape uses quadrant analysis to find actual corner
     */
    getCenterByShapeType(shapeType, box) {
        const size = box.getSize(new THREE.Vector3());
        const min = box.min;
        const max = box.max;

        switch (shapeType) {
            case 'l-shape':
            case 'l_shape':
            case 'lshape':
                // L-shape: bend usually runs along Y, so the profile is in XZ.
                return this.findLShapeCorner(box);

            case 'tube':
            case 'tube_carre':
            case 'tube_rectangular':
            case 'tube_rond':
            case 'tube_circular':
                // 🔥 FIXED: Tube with tenons/features needs vertex-based center
                // Geometric center gets skewed by asymmetric features (tenons, flanges, etc.)
                // Use vertex-based calculation for accurate orientation
                console.log(`🎯 [TUBE] Using vertex-based center calculation`);
                return this.findTubeCenter(box);

            case 'capot':
            case 'u-shape':
            case 'u_shape':
            case 'ushape':
            case 'coffret':
            case 'coffer':
                // 🔥 ENHANCED: U-shape/Capot/Coffret use vertex-based center
                // Calculate average of bottom + side wall vertices
                console.log(`🎯 [U-SHAPE] Using vertex-based center calculation`);
                return this.findUShapeCenter(box);

            case 'box':
            case 'rectangle':
            case 'rectangular':
            default:
                // For simple shapes, geometric center is fine
                return null; // Will use mass center or geometric center
        }
    }

    /**
     * 🔥 NEW: Get all model vertices in world coordinates
     * Helper method for vertex-based center calculations
     * @returns {THREE.Vector3[]} Array of all vertices in world space
     */
    getAllModelVertices() {
        const vertices = [];

        if (!this.modelGroup) {
            console.warn('⚠️ [GET_VERTICES] modelGroup not available');
            return vertices;
        }

        this.modelGroup.traverse((child) => {
            if (child.isMesh && child.geometry) {
                const positions = child.geometry.attributes.position;
                if (positions) {
                    for (let i = 0; i < positions.count; i++) {
                        const vertex = new THREE.Vector3();
                        vertex.fromBufferAttribute(positions, i);
                        // Transform to world coordinates
                        vertex.applyMatrix4(child.matrixWorld);
                        vertices.push(vertex);
                    }
                }
            }
        });

        console.log(`📊 [GET_VERTICES] Collected ${vertices.length} vertices`);
        return vertices;
    }

    /**
     * 🔥 NEW: Find U-shape center using vertex-based calculation
     * Algorithm:
     * 1. Get all vertices from model
     * 2. Separate bottom vertices (near min.z) from side vertices (higher up)
     * 3. Calculate average of bottom + side vertices
     * 4. Result: Center balanced between base and legs
     * 
     * @param {THREE.Box3} box - Bounding box of the model
     * @returns {THREE.Vector3} Optimal center for U-shape
     */
    findUShapeCenter(box) {
        const vertices = this.getAllModelVertices();

        if (vertices.length === 0) {
            console.warn('⚠️ [U-SHAPE] No vertices found, using geometric center');
            return box.getCenter(new THREE.Vector3());
        }

        const min = box.min;
        const max = box.max;
        const size = box.getSize(new THREE.Vector3());
        const threshold = size.z * 0.1;  // 10% tolerance for bottom detection

        console.log(`🔍 [U-SHAPE] Analyzing ${vertices.length} vertices with threshold ${threshold.toFixed(2)}`);

        // Separate bottom and side vertices
        const bottomVertices = [];
        const sideVertices = [];

        vertices.forEach(v => {
            if (Math.abs(v.z - min.z) < threshold) {
                bottomVertices.push(v);  // Near bottom
            } else if (v.z > min.z + threshold) {
                sideVertices.push(v);    // Side walls
            }
        });

        console.log(`📊 [U-SHAPE] Bottom vertices: ${bottomVertices.length}, Side vertices: ${sideVertices.length}`);

        // Calculate average of bottom + side vertices
        const allRelevantVertices = [...bottomVertices, ...sideVertices];

        if (allRelevantVertices.length === 0) {
            console.warn('⚠️ [U-SHAPE] No relevant vertices found, using geometric center');
            return box.getCenter(new THREE.Vector3());
        }

        const center = new THREE.Vector3();
        allRelevantVertices.forEach(v => center.add(v));
        center.divideScalar(allRelevantVertices.length);

        console.log(`✅ [U-SHAPE] Calculated vertex-based center:`, center);
        console.log(`📐 [U-SHAPE] Center height ratio: ${((center.z - min.z) / size.z * 100).toFixed(1)}%`);

        return center;
    }

    /**
     * 🔥 NEW: Find tube center using vertex-based calculation
     * Algorithm:
     * 1. Get all vertices from model
     * 2. Calculate average of ALL vertices (tubes can have tenons/flanges)
     * 3. Result: True center of mass, not skewed by asymmetric features
     * 
     * Why not geometric center?
     * - Tubes with tenons/flanges are NOT symmetric
     * - Geometric center gets skewed by these features
     * - Vertex-based center is more accurate for orientation
     * 
     * @param {THREE.Box3} box - Bounding box of the model
     * @returns {THREE.Vector3} Optimal center for tube
     */
    findTubeCenter(box) {
        const vertices = this.getAllModelVertices();

        if (vertices.length === 0) {
            console.warn('⚠️ [TUBE] No vertices found, using geometric center');
            return box.getCenter(new THREE.Vector3());
        }

        console.log(`🔍 [TUBE] Analyzing ${vertices.length} vertices`);

        // Calculate average of ALL vertices
        const center = new THREE.Vector3();
        vertices.forEach(v => center.add(v));
        center.divideScalar(vertices.length);

        const min = box.min;
        const size = box.getSize(new THREE.Vector3());

        console.log(`✅ [TUBE] Calculated vertex-based center:`, center);
        console.log(`📐 [TUBE] Center position: X=${((center.x - min.x) / size.x * 100).toFixed(1)}%, Y=${((center.y - min.y) / size.y * 100).toFixed(1)}%, Z=${((center.z - min.z) / size.z * 100).toFixed(1)}%`);

        return center;
    }

    /**
     * 🔥 NEW: Find L-shape corner using quadrant analysis
     * Algorithm:
     * 1. Detect the profile plane. Sheet-metal L-shapes usually bend along Y,
     *    so the meaningful profile is XZ, not XY.
     * 2. Divide that profile plane into 4 quadrants.
     * 2. Count vertices in each quadrant
     * 3. Corner = quadrant with HIGHEST vertex density
     * 4. Return center of that quadrant
     * 
     * @param {THREE.Box3} box - Bounding box of the model (world coordinates)
     * @returns {THREE.Vector3} Corner position
     */
    findLShapeCorner(box) {
        const min = box.min;
        const max = box.max;
        const mid = new THREE.Vector3(
            (min.x + max.x) / 2,
            (min.y + max.y) / 2,
            (min.z + max.z) / 2
        );

        console.log(`🔍 [CORNER] Bounding box: min=${JSON.stringify(min)}, max=${JSON.stringify(max)}`);

        const size = box.getSize(new THREE.Vector3());
        const profilePlane = this.getBendProfilePlane() || (size.z > size.y * 0.5 ? 'XZ' : 'XY');
        console.log(`🔍 [CORNER] Using ${profilePlane} profile plane for L-shape center`);

        const profileAxes = this.getProfileAxes(profilePlane);
        const axisA = profileAxes.axisA;
        const axisB = profileAxes.axisB;
        const minA = min[axisA];
        const maxA = max[axisA];
        const midA = mid[axisA];
        const minB = min[axisB];
        const maxB = max[axisB];
        const midB = mid[axisB];

        // Define 4 quadrants in the selected profile plane
        const quadrants = {
            'bottom-left': { minA, maxA: midA, minB, maxB: midB, count: 0 },
            'bottom-right': { minA: midA, maxA, minB, maxB: midB, count: 0 },
            'top-left': { minA, maxA: midA, minB: midB, maxB, count: 0 },
            'top-right': { minA: midA, maxA, minB: midB, maxB, count: 0 }
        };

        let totalVertices = 0;

        // Count vertices in each quadrant
        this.modelGroup.traverse((child) => {
            if (child.isMesh && child.geometry) {
                const positions = child.geometry.attributes.position;
                if (!positions) return;

                const vertexCount = positions.count;
                totalVertices += vertexCount;

                // Create a temporary vector for coordinate transformation
                const vertex = new THREE.Vector3();

                for (let i = 0; i < vertexCount; i++) {
                    // Get vertex in local coordinates
                    vertex.set(
                        positions.getX(i),
                        positions.getY(i),
                        positions.getZ(i)
                    );

                    // Transform to world coordinates
                    vertex.applyMatrix4(child.matrixWorld);

                    const a = vertex[axisA];
                    const b = vertex[axisB];

                    // Check which quadrant this vertex belongs to
                    if (a >= minA && a <= midA && b >= minB && b <= midB) {
                        quadrants['bottom-left'].count++;
                    } else if (a >= midA && a <= maxA && b >= minB && b <= midB) {
                        quadrants['bottom-right'].count++;
                    } else if (a >= minA && a <= midA && b >= midB && b <= maxB) {
                        quadrants['top-left'].count++;
                    } else if (a >= midA && a <= maxA && b >= midB && b <= maxB) {
                        quadrants['top-right'].count++;
                    }
                }
            }
        });

        console.log(`🔍 [CORNER] Total vertices processed: ${totalVertices}`);

        // Find quadrant with highest vertex count (this is the corner)
        let maxCount = 0;
        let cornerQuadrant = null;
        for (const [name, quad] of Object.entries(quadrants)) {
            console.log(`📊 Quadrant ${name}: ${quad.count} vertices (${(quad.count / totalVertices * 100).toFixed(1)}%)`);
            if (quad.count > maxCount) {
                maxCount = quad.count;
                cornerQuadrant = { name, ...quad };
            }
        }

        if (!cornerQuadrant || maxCount === 0) {
            console.warn('⚠️ Could not find L-shape corner, using fallback');
            return new THREE.Vector3(
                min.x + (max.x - min.x) * 0.35,
                mid.y,
                min.z + (max.z - min.z) * 0.35
            );
        }

        // Calculate center of the corner quadrant
        const cornerCenter = new THREE.Vector3(mid.x, mid.y, mid.z);
        cornerCenter[axisA] = (cornerQuadrant.minA + cornerQuadrant.maxA) / 2;
        cornerCenter[axisB] = (cornerQuadrant.minB + cornerQuadrant.maxB) / 2;

        console.log(`✅ L-shape corner found in ${cornerQuadrant.name} quadrant (${maxCount} vertices, ${(maxCount / totalVertices * 100).toFixed(1)}%)`);
        console.log(`📍 Corner center:`, cornerCenter);

        return cornerCenter;
    }

    getBendProfilePlane() {
        const bendingFeature = (this.features || []).find(feature => feature?.type === 'bending');
        if (!bendingFeature) {
            return null;
        }

        if (['XY', 'XZ', 'YZ'].includes(bendingFeature.profile_plane)) {
            return bendingFeature.profile_plane;
        }

        const axis = bendingFeature.bend_axis;
        if (!axis) {
            return null;
        }

        const components = {
            x: Math.abs(Number(axis.x) || 0),
            y: Math.abs(Number(axis.y) || 0),
            z: Math.abs(Number(axis.z) || 0)
        };
        const dominant = Object.entries(components).sort((a, b) => b[1] - a[1])[0];
        if (!dominant || dominant[1] < 0.85) {
            return null;
        }

        return {
            x: 'YZ',
            y: 'XZ',
            z: 'XY'
        }[dominant[0]];
    }

    getProfileAxes(profilePlane) {
        switch (profilePlane) {
            case 'YZ':
                return { axisA: 'y', axisB: 'z' };
            case 'XY':
                return { axisA: 'x', axisB: 'y' };
            case 'XZ':
            default:
                return { axisA: 'x', axisB: 'z' };
        }
    }

    /**
     * Calculate mass center (centroid) based on all vertices
     * More accurate for complex shapes
     */
    calculateMassCenter() {
        let sumX = 0, sumY = 0, sumZ = 0;
        let totalVertices = 0;

        this.modelGroup.traverse((child) => {
            if (child.isMesh && child.geometry) {
                const positions = child.geometry.attributes.position;
                if (!positions) return;

                const vertexCount = positions.count;

                for (let i = 0; i < vertexCount; i++) {
                    sumX += positions.getX(i);
                    sumY += positions.getY(i);
                    sumZ += positions.getZ(i);
                }

                totalVertices += vertexCount;
            }
        });

        if (totalVertices === 0) {
            console.warn('⚠️ No vertices found for mass center calculation');
            return null;
        }

        return new THREE.Vector3(
            sumX / totalVertices,
            sumY / totalVertices,
            sumZ / totalVertices
        );
    }

    /**
     * Animate camera to target position
     */
    animateCameraTo(targetPosition, targetLookAt) {
        const startPosition = this.camera.position.clone();
        const startLookAt = this.controls.target.clone();
        const duration = 300; // ms
        const startTime = Date.now();

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease-out cubic
            const eased = 1 - Math.pow(1 - progress, 3);

            // Interpolate position
            this.camera.position.lerpVectors(startPosition, targetPosition, eased);

            // Interpolate look-at target
            this.controls.target.lerpVectors(startLookAt, targetLookAt, eased);

            // Update camera
            this.camera.lookAt(this.controls.target);
            this.controls.update();

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        animate();
    }

    /**
     * Toggle ViewCube visibility
     */
    toggleViewCube() {
        this.isViewCubeVisible = !this.isViewCubeVisible;

        if (this.viewCubeContainer) {
            this.viewCubeContainer.style.display = this.isViewCubeVisible ? 'block' : 'none';
        }

        console.log(`🎯 ViewCube ${this.isViewCubeVisible ? 'shown' : 'hidden'}`);
    }

    /**
     * Reset camera to isometric view
     */
    resetCamera() {
        if (!this.currentModel) {
            // Default position if no model
            this.camera.position.set(5, 5, 5);
            this.camera.lookAt(0, 0, 0);
            this.controls.target.set(0, 0, 0);
            this.controls.update();
            return;
        }

        // Get model bounds
        const box = new THREE.Box3().setFromObject(this.modelGroup);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);

        // Position camera in isometric view
        const distance = maxDim * 2.5;
        this.camera.position.set(
            center.x + distance * 0.7,
            center.y + distance * 0.7,
            center.z + distance * 0.7
        );

        // Look at model center
        this.camera.lookAt(center);
        this.controls.target.copy(center);
        this.controls.update();

        console.log('📷 Camera reset to isometric view');
    }


    // Main loadModel method to match OBJ viewer interface
    async loadModel(jsonPath, fileName = '') {
        try {
            console.log('🔄 Loading JSON model:', jsonPath);
            this.showLoading(true);
            this.hideControls();

            // Store filename
            this.modelInfo.fileName = fileName || jsonPath.split('/').pop() || 'model.json';

            // Use the JSON viewer API endpoint
            let apiPath = jsonPath;
            if (!jsonPath.startsWith('/api/json-viewer/')) {
                // Extract relative path from full download URL if present
                if (jsonPath.includes('/download/')) {
                    // Extract path after /download/ (e.g., "outputs/json/2025-11-14/file.json")
                    const relativePath = jsonPath.split('/download/')[1];
                    apiPath = `/api/json-viewer/${relativePath}`;
                    console.log('📍 Extracted relative path from download URL:', relativePath);
                } else if (jsonPath.startsWith('http://') || jsonPath.startsWith('https://')) {
                    // If it's a full URL without /download/, try to extract the path
                    try {
                        const url = new URL(jsonPath);
                        // Remove leading slash from pathname
                        apiPath = `/api/json-viewer${url.pathname}`;
                        console.log('📍 Extracted path from full URL:', url.pathname);
                    } catch (e) {
                        console.warn('⚠️ Failed to parse URL, using as-is:', jsonPath);
                        apiPath = `/api/json-viewer/${jsonPath}`;
                    }
                } else if (jsonPath.startsWith('/')) {
                    // Relative path starting with /
                    apiPath = `/api/json-viewer${jsonPath}`;
                } else {
                    // Relative path without leading /
                    apiPath = `/api/json-viewer/${jsonPath}`;
                }
            }

            console.log('🌐 Final API path:', apiPath);

            // Fetch JSON file through the API
            const response = await fetch(apiPath);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const jsonData = await response.json();

            // Load the JSON data
            this.loadJsonData(jsonData);

            return this.currentModel;
        } catch (error) {
            console.error('❌ Error loading JSON model:', error);
            this.showStatus(`Error: ${error.message}`, 'error');
            this.showLoading(false);
            throw error;
        }
    }

    // JSON file loading function for file input
    loadJsonFile(file) {
        this.showLoading(true);
        this.showStatus('Loading JSON model...', 'success');

        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const jsonData = JSON.parse(e.target.result);
                this.loadJsonData(jsonData);
            } catch (error) {
                console.error('JSON parse error:', error);
                this.showStatus('Invalid JSON file', 'error');
                this.showLoading(false);
            }
        };
        reader.readAsText(file);
    }

    // Load JSON model data function
    loadJsonData(jsonData) {
        try {
            console.log('🔄 [LOAD_JSON] Starting JSON data loading...');

            // Clear existing model
            this.clearModel();

            let totalVertices = 0;
            let totalFaces = 0;
            let objectCount = 0;
            let format = 'Unknown';

            // Store the JSON data for later use
            this.jsonData = jsonData;

            // 🔥 NEW: Load features array (countersink, chamfer, etc.) if available
            if (jsonData.features && Array.isArray(jsonData.features)) {
                this.features = jsonData.features;
                console.log(`✅ [FEATURES] Loaded ${this.features.length} features:`,
                    this.features.map(f => `${f.type} (${f.face_ids?.length || 0} faces)`).join(', '));
            } else {
                this.features = [];
                console.log('ℹ️ [FEATURES] No features array found in JSON');
            }

            // 🔥 DEBUG: Log shape_type
            if (jsonData.shape_type) {
                console.log(`🎯 [SHAPE_TYPE] Loaded shape_type: "${jsonData.shape_type}"`);
            } else {
                console.log(`⚠️ [SHAPE_TYPE] No shape_type found in JSON`);
            }

            console.log('📊 [LOAD_JSON] JSON data structure:', {
                hasShapes: !!(jsonData.shapes && Array.isArray(jsonData.shapes)),
                hasObjects: !!(jsonData.objects && Array.isArray(jsonData.objects)),
                hasFaces: !!(jsonData.faces && jsonData.faces.bodies),
                hasFeatures: this.features.length > 0
            });

            if (jsonData.shapes && Array.isArray(jsonData.shapes)) {
                // New semantic JSON format
                format = 'Semantic JSON';
                objectCount = jsonData.shapes.length;

                jsonData.shapes.forEach((shape, index) => {
                    const mesh = this.createMeshFromSemantic(shape, index);
                    if (mesh) {
                        this.modelGroup.add(mesh);
                        // These counts are approximations for semantic shapes
                        totalVertices += mesh.geometry.attributes.position.count;
                        totalFaces += mesh.geometry.index.count / 3;
                    }
                });

            } else if (jsonData.objects && Array.isArray(jsonData.objects)) {
                // FreeCAD JSON format
                format = 'FreeCAD JSON';
                objectCount = jsonData.objects.length;

                jsonData.objects.forEach((obj, index) => {
                    if (obj.vertices && obj.facets) {
                        const mesh = this.createMeshFromFreeCAD(obj, index);
                        if (mesh) {
                            mesh.userData.format = 'freecad';
                            mesh.userData.objectIndex = index;
                            this.modelGroup.add(mesh);
                            totalVertices += obj.vertices.length;
                            totalFaces += obj.facets.length;
                        }
                    }
                });

            } else if (jsonData.faces && jsonData.faces.bodies) {
                // OnShape JSON format
                format = 'OnShape JSON';

                jsonData.faces.bodies.forEach((body, bodyIndex) => {
                    if (body.faces) {
                        body.faces.forEach((face, faceIndex) => {
                            if (face.facets) {
                                const mesh = this.createMeshFromOnShape(face, bodyIndex, faceIndex);
                                if (mesh) {
                                    mesh.userData.format = 'onshape';
                                    mesh.userData.bodyIndex = bodyIndex;
                                    mesh.userData.faceIndex = faceIndex;
                                    mesh.userData.realFaceId = face.id || `face_${faceIndex}`;
                                    this.modelGroup.add(mesh);
                                    objectCount++;

                                    // Count vertices and faces
                                    face.facets.forEach(facet => {
                                        if (facet.vertices) {
                                            totalVertices += facet.vertices.length;
                                            totalFaces += Math.max(0, facet.vertices.length - 2);
                                        }
                                    });
                                }
                            }
                        });
                    }
                });
            }

            if (this.modelGroup.children.length === 0) {
                throw new Error('No valid geometry found');
            }

            console.log('✅ [LOAD_JSON] Model meshes created:', this.modelGroup.children.length);

            // Store current model reference
            this.currentModel = this.modelGroup;

            // Ensure modelGroup is visible before centering
            this.modelGroup.visible = true;
            console.log('👁️ [LOAD_JSON] ModelGroup visibility set to:', this.modelGroup.visible);

            // 🔥 Cache bounding box BEFORE centerModel() — used for shape analysis (L-shape quadrant, tube center, etc.)
            // This is the ORIGINAL coordinate space of the model data
            this.modelBoundingBox = new THREE.Box3().setFromObject(this.modelGroup);
            console.log(`📦 [BBOX] Cached ORIGINAL model bounding box (before centering):`, {
                min: this.modelBoundingBox.min,
                max: this.modelBoundingBox.max,
                size: this.modelBoundingBox.getSize(new THREE.Vector3())
            });

            // Center and scale model
            this.centerModel();

            // 🔥 CRITICAL FIX: Cache world bounding box AFTER centerModel() transforms the model!
            // This is the ACTUAL world-space bounding box used for camera positioning (ViewCube, snapToView)
            this.modelWorldBoundingBox = new THREE.Box3().setFromObject(this.modelGroup);
            console.log(`📦 [WORLD_BBOX] Cached WORLD model bounding box (after centering):`, {
                min: this.modelWorldBoundingBox.min,
                max: this.modelWorldBoundingBox.max,
                size: this.modelWorldBoundingBox.getSize(new THREE.Vector3())
            });

            // Double-check visibility after centering
            this.modelGroup.visible = true;
            console.log('👁️ [LOAD_JSON] ModelGroup visibility after centering:', this.modelGroup.visible);

            // Update info
            this.updateModelInfo(totalVertices, totalFaces, objectCount, format);

            // Show controls
            this.showControls();
            this.showInfo();

            this.showLoading(false);
            this.showStatus('JSON model loaded successfully!', 'success');

            console.log('🎉 [LOAD_JSON] JSON model loading completed successfully');

            // Show ViewCube after successful model load
            if (this.viewCubeContainer) {
                this.isViewCubeVisible = true;
                this.viewCubeContainer.style.display = 'block';
                console.log('🎯 [VIEWCUBE] ViewCube shown after model load');

                const vcBtn = document.getElementById('json-toggle-viewcube-btn');
                vcBtn?.classList.add('btn-active');
                if (vcBtn) vcBtn.title = 'Turn off ViewCube (currently ON)';
            }

            // Force refresh to ensure visibility (fix for second load issue)
            setTimeout(() => {
                this.forceRefreshModel();
            }, 100);


            // window.jsonModelViewer.showCenterDebug() + hideCenterDebug() — console access 

        } catch (error) {
            console.error('JSON load error:', error);
            this.showStatus(`Error: ${error.message}`, 'error');
            this.showLoading(false);
        }
    }

    createMeshFromFreeCAD(obj, index) {
        try {
            const geometry = new THREE.BufferGeometry();

            // Vertices
            const vertices = [];
            obj.vertices.forEach(v => {
                vertices.push(v[0], v[1], v[2]);
            });

            // Faces
            const indices = [];
            const faceIds = []; // Add face IDs array for face selection
            obj.facets.forEach((face, facetIndex) => {
                if (face.length >= 3) {
                    // Triangle
                    indices.push(face[0], face[1], face[2]);
                    faceIds.push(`freecad_obj${index}_facet${facetIndex}`);
                    // Quad -> second triangle
                    if (face.length === 4) {
                        indices.push(face[0], face[2], face[3]);
                        faceIds.push(`freecad_obj${index}_facet${facetIndex}_tri2`);
                    }
                }
            });

            geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
            geometry.setIndex(indices);
            geometry.computeVertexNormals();

            const material = new THREE.MeshLambertMaterial({
                color: obj.color || '#888888',
                side: THREE.DoubleSide
            });

            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = `freecad_object_${index}`;

            // Add face IDs and metadata for face selection
            mesh.userData.faceIds = faceIds;
            mesh.userData.objectIndex = index;
            mesh.userData.format = 'freecad';

            return mesh;

        } catch (error) {
            console.error('FreeCAD mesh error:', error);
            return null;
        }
    }

    createMeshFromOnShape(face, bodyIndex, faceIndex) {
        try {
            const geometry = new THREE.BufferGeometry();
            const vertices = [];
            const indices = [];
            let vertexCount = 0;

            // Create face IDs array for face selection
            const faceIds = [];
            const realFaceId = face.id || `face_${faceIndex}`;

            console.log('Processing OnShape face:', faceIndex, 'Real ID:', realFaceId);

            let triangleIndex = 0;
            face.facets.forEach((facet, facetIndex) => {
                if (facet.vertices && facet.vertices.length >= 3) {
                    const startIndex = vertexCount;

                    // Add vertices
                    facet.vertices.forEach(v => {
                        vertices.push(v.x, v.y, v.z);
                        vertexCount++;
                    });

                    // Create triangles and corresponding face IDs
                    for (let i = 1; i < facet.vertices.length - 1; i++) {
                        indices.push(startIndex, startIndex + i, startIndex + i + 1);
                        // Use real face ID from OnShape for face selection
                        faceIds.push(`${realFaceId}_facet${facetIndex}_tri${i - 1}`);
                        triangleIndex++;
                    }
                }
            });

            if (vertices.length === 0) return null;

            geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
            geometry.setIndex(indices);
            geometry.computeVertexNormals();

            const material = new THREE.MeshLambertMaterial({
                color: '#888888',
                side: THREE.DoubleSide
            });

            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = `onshape_body${bodyIndex}_face${faceIndex}`;

            // Add face IDs and metadata for face selection
            mesh.userData.faceIds = faceIds;
            mesh.userData.realFaceId = realFaceId;
            mesh.userData.bodyIndex = bodyIndex;
            mesh.userData.faceIndex = faceIndex;
            mesh.userData.format = 'onshape';

            return mesh;

        } catch (error) {
            console.error('OnShape mesh error:', error);
            return null;
        }
    }

    createMeshFromSemantic(shape, index) {
        try {
            let geometry;
            const material = new THREE.MeshLambertMaterial({
                color: shape.color || '#888888',
                side: THREE.DoubleSide
            });

            switch (shape.type.toLowerCase()) {
                case 'cylinder':
                    geometry = new THREE.CylinderGeometry(shape.radius, shape.radius, shape.height, 32);
                    break;
                case 'box':
                case 'rectangle':
                    geometry = new THREE.BoxGeometry(shape.length, shape.width, shape.height || 1);
                    break;
                default:
                    console.warn(`Unsupported shape type: ${shape.type}`);
                    return null;
            }

            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = shape.name || `${shape.type}_${index}`;

            // Store the original shape data for analysis
            mesh.userData.shapeInfo = shape;

            return mesh;

        } catch (error) {
            console.error('Semantic mesh error:', error);
            return null;
        }
    }

    clearModel() {
        if (!this.modelGroup) return;

        console.log('🧹 [CLEAR_MODEL] Starting model cleanup...');

        // Hide ViewCube when clearing model
        if (this.viewCubeContainer) {
            this.isViewCubeVisible = false;
            this.viewCubeContainer.style.display = 'none';
            console.log('🎯 [VIEWCUBE] ViewCube hidden on model clear');
        }

        // Clear face selection
        this.clearFaceSelection();

        // Clear original materials map
        this.originalMaterials.clear();

        // Clear all children and dispose resources properly
        while (this.modelGroup.children.length > 0) {
            const child = this.modelGroup.children[0];
            this.modelGroup.remove(child);

            // Dispose geometry
            if (child.geometry) {
                child.geometry.dispose();
            }

            // Dispose material (handle both single material and material arrays)
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(material => material.dispose());
                } else {
                    child.material.dispose();
                }
            }
        }

        // Reset modelGroup properties to default state
        this.modelGroup.position.set(0, 0, 0);
        this.modelGroup.rotation.set(0, 0, 0);
        this.modelGroup.scale.set(1, 1, 1);
        this.modelGroup.visible = true;

        // Clear current model reference
        this.currentModel = null;

        // Clear bounding box caches
        this.modelBoundingBox = null;
        this.modelWorldBoundingBox = null;

        // Remove debug dots if any
        this.hideDebugCenterDot();

        console.log('✅ [CLEAR_MODEL] Model cleanup completed');
    }

    centerModel() {
        if (!this.modelGroup || this.modelGroup.children.length === 0) {
            console.warn('⚠️ [CENTER_MODEL] No model to center');
            return;
        }

        console.log('🎯 [CENTER_MODEL] Starting model centering...');

        const box = new THREE.Box3().setFromObject(this.modelGroup);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());

        console.log('📏 [CENTER_MODEL] Model bounds:', {
            center: `(${center.x.toFixed(2)}, ${center.y.toFixed(2)}, ${center.z.toFixed(2)})`,
            size: `(${size.x.toFixed(2)}, ${size.y.toFixed(2)}, ${size.z.toFixed(2)})`
        });

        // Center the model
        this.modelGroup.position.sub(center);

        // Adjust Y position so model sits on the grid (fix for sinking issue)
        const minY = box.min.y - center.y; // Relative to new position
        if (minY < 0) {
            this.modelGroup.position.y -= minY - 0.01; // Lift slightly above ground
        }

        // Scale the model to fit the view with validation
        const maxDim = Math.max(size.x, size.y, size.z);
        if (maxDim > 0) {
            const scale = 5 / maxDim;

            // Validate scale to prevent invisible models
            if (scale <= 0 || scale > 1000) {
                console.warn('⚠️ [CENTER_MODEL] Invalid scale detected:', scale, 'using default scale');
                this.modelGroup.scale.setScalar(1);
            } else {
                this.modelGroup.scale.setScalar(scale);
                console.log('📐 [CENTER_MODEL] Applied scale:', scale);
            }
        } else {
            console.warn('⚠️ [CENTER_MODEL] Model has zero dimensions, using default scale');
            this.modelGroup.scale.setScalar(1);
        }

        // Ensure modelGroup is visible
        this.modelGroup.visible = true;

        // Calculate visual center after positioning and scaling
        const visualCenter = new THREE.Vector3(
            0,
            this.modelGroup.position.y + (size.y * this.modelGroup.scale.x) / 2,
            0
        );

        console.log('🎯 [CENTER_MODEL] Visual center:', `(${visualCenter.x.toFixed(2)}, ${visualCenter.y.toFixed(2)}, ${visualCenter.z.toFixed(2)})`);

        // Update the controls to orbit around the visual center
        this.controls.target.copy(visualCenter);
        this.resetCamera();

        console.log('✅ [CENTER_MODEL] Model centering completed');
    }

    updateModelInfo(vertices, faces, objects, format) {
        // Update model info object
        this.modelInfo.vertices = vertices;
        this.modelInfo.faces = faces;
        this.modelInfo.objects = objects;
        this.modelInfo.format = format;

        // Update DOM elements with null checks
        const verticesEl = document.getElementById('json-vertices-count');
        if (verticesEl) verticesEl.textContent = vertices;

        const facesEl = document.getElementById('json-faces-count');
        if (facesEl) facesEl.textContent = faces;

        const objectsEl = document.getElementById('json-objects-count');
        if (objectsEl) objectsEl.textContent = objects;

        const formatEl = document.getElementById('json-format-type');
        if (formatEl) formatEl.textContent = format;

        const infoTextEl = document.getElementById('json-model-info-text');
        if (infoTextEl) {
            infoTextEl.textContent = `${format} - ${vertices} vertices, ${faces} faces, ${objects} objects`;
        }

        // 🆕 Update geometry information if available
        this.updateGeometryInfo();

        // Log the info if DOM elements are not available
        console.log(`JSON Model Info: ${format} - ${vertices} vertices, ${faces} faces, ${objects} objects`);
    }

    // 🆕 Update geometry information from JSON data
    updateGeometryInfo() {
        const geometrySection = document.getElementById('json-geometry-info');

        // Check if geometry data exists in the loaded JSON
        if (!this.jsonData || !this.jsonData.geometry) {
            // Hide geometry section if no data
            if (geometrySection) {
                geometrySection.classList.add('hidden');
            }
            return;
        }

        const geometry = this.jsonData.geometry;

        // Show geometry section
        if (geometrySection) {
            geometrySection.classList.remove('hidden');
        }

        // Update surface area (prefer cm² for better readability)
        const surfaceAreaEl = document.getElementById('json-surface-area');
        if (surfaceAreaEl && geometry.surface_area) {
            const cm2 = geometry.surface_area.cm2 || (geometry.surface_area.mm2 / 100);
            surfaceAreaEl.textContent = `${cm2.toFixed(2)} cm²`;
        }

        // Update volume (prefer cm³ for better readability)
        const volumeEl = document.getElementById('json-volume');
        if (volumeEl && geometry.volume) {
            const cm3 = geometry.volume.cm3 || (geometry.volume.mm3 / 1000);
            volumeEl.textContent = `${cm3.toFixed(2)} cm³`;
        }

        // Update mass (prefer grams for small parts, kg for large parts)
        const massEl = document.getElementById('json-mass');
        if (massEl && geometry.mass) {
            const grams = geometry.mass.grams || (geometry.mass.kg * 1000);
            if (grams >= 1000) {
                const kg = geometry.mass.kg || (grams / 1000);
                massEl.textContent = `${kg.toFixed(3)} kg`;
            } else {
                massEl.textContent = `${grams.toFixed(2)} g`;
            }
        }

        // Update material
        const materialEl = document.getElementById('json-material');
        if (materialEl && geometry.material) {
            materialEl.textContent = geometry.material;
        }

        console.log('✅ [GEOMETRY] Updated geometry info:', {
            surface_area: geometry.surface_area,
            volume: geometry.volume,
            mass: geometry.mass,
            material: geometry.material
        });
    }

    // This function is now obsolete as its logic has been merged into the new event listener.
    // onModelClick(event) { ... }

    // Get face ID from mesh userData - extracted from index.html demo
    getFaceId(mesh, faceIndex) {
        // Get face ID from mesh userData
        if (mesh.userData && mesh.userData.faceIds && mesh.userData.faceIds[faceIndex]) {
            return mesh.userData.faceIds[faceIndex];
        }

        // Fallback: create ID from mesh name and face index
        const meshName = mesh.name || 'mesh';
        return `${meshName}_face_${faceIndex}`;
    }

    // 🔥 Helper: Find feature containing the given face_id
    findFeatureByFaceId(faceId) {
        if (!this.features || !Array.isArray(this.features)) return null;

        return this.features.find(feature =>
            feature.face_ids &&
            Array.isArray(feature.face_ids) &&
            feature.face_ids.includes(faceId)
        );
    }

    // 🔥 Helper: Get all meshes for given face IDs
    getMeshesByFaceIds(faceIds) {
        if (!faceIds || !Array.isArray(faceIds)) return [];

        const meshes = [];
        this.modelGroup.children.forEach(child => {
            if (child.userData && child.userData.realFaceId) {
                if (faceIds.includes(child.userData.realFaceId)) {
                    meshes.push(child);
                }
            }
        });
        return meshes;
    }

    // Create face highlight - enhanced for multi-face features (countersink) and threaded holes
    createFaceHighlight(intersection, mesh) {
        const realFaceId = mesh.userData.realFaceId;

        // 🔥 Check if this face belongs to a feature with multiple faces
        const feature = this.findFeatureByFaceId(realFaceId);

        // Use consistent gold color for all highlights
        const highlightColor = 0xFFD700; // Gold for all features

        if (feature && feature.face_ids && feature.face_ids.length > 1) {
            // Multi-face feature (e.g., countersink with cone + hole, or threaded hole)
            console.log(`🔥 [HIGHLIGHT] Multi-face feature detected: ${feature.type} with ${feature.face_ids.length} faces`);
            console.log(`🔥 [HIGHLIGHT] Face IDs:`, feature.face_ids);

            // Get all meshes for this feature
            const featureMeshes = this.getMeshesByFaceIds(feature.face_ids);
            console.log(`🔥 [HIGHLIGHT] Found ${featureMeshes.length} meshes to highlight`);

            // Store highlight meshes in an array
            if (!this.highlightMeshes) {
                this.highlightMeshes = [];
            }

            // Create highlight for each mesh
            featureMeshes.forEach((targetMesh, index) => {
                const geometry = targetMesh.geometry.clone();

                const highlightMaterial = new THREE.MeshBasicMaterial({
                    color: highlightColor, // Gold color for all features
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: 0.6,
                    depthTest: false
                });

                const highlightMesh = new THREE.Mesh(geometry, highlightMaterial);

                // Copy transformation
                highlightMesh.position.copy(targetMesh.position);
                highlightMesh.rotation.copy(targetMesh.rotation);
                highlightMesh.scale.copy(targetMesh.scale);
                highlightMesh.applyMatrix4(targetMesh.matrixWorld);

                // Add small offset to prevent z-fighting
                // Use the intersection normal for the clicked face, estimate for others
                if (targetMesh === mesh && intersection.face) {
                    const normal = intersection.face.normal.clone().transformDirection(mesh.matrixWorld);
                    normal.multiplyScalar(0.01);
                    highlightMesh.position.add(normal);
                } else {
                    // Small offset for non-clicked faces
                    highlightMesh.position.y += 0.01;
                }

                this.scene.add(highlightMesh);
                this.highlightMeshes.push(highlightMesh);

                console.log(`✅ [HIGHLIGHT] Added highlight ${index + 1}/${featureMeshes.length} for face ${targetMesh.userData.realFaceId}`);
            });

            // Flash all highlights
            this.highlightMeshes.forEach(hm => this.flashHighlightedFace(hm));



        } else {
            // Single-face feature (normal hole, fillet, etc.)
            console.log(`🔵 [HIGHLIGHT] Single-face feature detected`);

            const geometry = mesh.geometry.clone();

            const highlightMaterial = new THREE.MeshBasicMaterial({
                color: highlightColor, // Gold color for all features
                side: THREE.DoubleSide,
                transparent: true,
                opacity: 0.6,
                depthTest: false
            });

            this.highlightMesh = new THREE.Mesh(geometry, highlightMaterial);

            // Copy the mesh's transformation
            this.highlightMesh.position.copy(mesh.position);
            this.highlightMesh.rotation.copy(mesh.rotation);
            this.highlightMesh.scale.copy(mesh.scale);
            this.highlightMesh.applyMatrix4(mesh.matrixWorld);

            // Add small offset to prevent z-fighting
            const face = intersection.face;
            const normal = face.normal.clone().transformDirection(mesh.matrixWorld);
            normal.multiplyScalar(0.01);
            this.highlightMesh.position.add(normal);

            // Add to scene
            this.scene.add(this.highlightMesh);

            // Flash the highlight for immediate feedback
            this.flashHighlightedFace(this.highlightMesh);


        }
    }

    // Clear face highlight - enhanced for multi-face features and thread labels
    clearFaceHighlight() {
        // Clear single highlight mesh
        if (this.highlightMesh) {
            this.scene.remove(this.highlightMesh);
            this.highlightMesh = null;
        }

        // 🔥 Clear multiple highlight meshes (for countersink)
        if (this.highlightMeshes && Array.isArray(this.highlightMeshes)) {
            this.highlightMeshes.forEach(hm => {
                this.scene.remove(hm);
            });
            this.highlightMeshes = [];
        }

        // 🆕 Clear thread label
        if (this.threadLabel) {
            this.scene.remove(this.threadLabel);
            this.threadLabel = null;
        }

        // Clear any ongoing flash effect
        if (this.flashTimeoutId) {
            clearTimeout(this.flashTimeoutId);
            this.flashTimeoutId = null;
        }
    }

    // 🆕 Create thread label sprite for threaded holes
    createThreadLabel(position, threadType) {
        try {
            // Clear existing label
            if (this.threadLabel) {
                this.scene.remove(this.threadLabel);
                this.threadLabel = null;
            }

            // Create canvas for text
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            canvas.width = 256;
            canvas.height = 128;

            // Draw background
            context.fillStyle = 'rgba(255, 140, 0, 0.9)'; // Orange background
            context.roundRect(10, 10, canvas.width - 20, canvas.height - 20, 10);
            context.fill();

            // Draw border
            context.strokeStyle = '#FFFFFF';
            context.lineWidth = 3;
            context.roundRect(10, 10, canvas.width - 20, canvas.height - 20, 10);
            context.stroke();

            // Draw text
            context.fillStyle = '#FFFFFF';
            context.font = 'Bold 48px Arial';
            context.textAlign = 'center';
            context.textBaseline = 'middle';
            context.fillText(threadType, canvas.width / 2, canvas.height / 2);

            // Create texture from canvas
            const texture = new THREE.CanvasTexture(canvas);
            texture.needsUpdate = true;

            // Create sprite material
            const spriteMaterial = new THREE.SpriteMaterial({
                map: texture,
                transparent: true,
                depthTest: false,
                depthWrite: false
            });

            // Create sprite
            const sprite = new THREE.Sprite(spriteMaterial);

            // Position sprite above the hole
            sprite.position.set(
                position.x,
                position.y,
                position.z + 0.5 // Offset above the hole
            );

            // Scale sprite (adjust as needed)
            sprite.scale.set(0.8, 0.4, 1);

            // Add to scene
            this.scene.add(sprite);
            this.threadLabel = sprite;

            console.log(`🔩 [LABEL] Created thread label: ${threadType} at position (${position.x}, ${position.y}, ${position.z})`);

        } catch (error) {
            console.error('❌ [LABEL] Error creating thread label:', error);
        }
    }

    // Flash highlight effect for immediate user feedback
    flashHighlightedFace(highlightMesh) {
        if (!highlightMesh) return;

        const flashMaterial = highlightMesh.material.clone();
        flashMaterial.color.setHex(0xFFFF00); // Bright yellow for flash
        flashMaterial.opacity = 0.9;

        highlightMesh.material = flashMaterial;

        // Revert to the standard highlight after a short delay
        this.flashTimeoutId = setTimeout(() => {
            if (highlightMesh) { // Check if highlightMesh still exists
                const standardMaterial = highlightMesh.material.clone();
                standardMaterial.color.setHex(0xFFD700); // Standard gold
                standardMaterial.opacity = 0.6;
                highlightMesh.material = standardMaterial;
            }
        }, 150); // Flash duration
    }

    // Show face tooltip - matching OBJ viewer behavior
    showFaceTooltip(event, faceInfo) {
        if (!faceInfo) return;

        console.log('🎯 Creating face tooltip with info:', faceInfo);

        // Remove existing tooltip
        this.hideFaceTooltip();

        // Create simple tooltip content matching OBJ viewer style
        let tooltipContent = `
            <div style="position: absolute; background: rgba(0,0,0,0.8); color: white; padding: 8px; border-radius: 4px; font-size: 12px; z-index: 1000; pointer-events: none; border: 1px solid #FFD700;">
                <b>Face ID: ${faceInfo.faceId}</b><br>
        `;

        // Add real face ID if available (OnShape format)
        if (faceInfo.realFaceId && faceInfo.realFaceId !== faceInfo.faceId) {
            tooltipContent += `Real Face ID: ${faceInfo.realFaceId}<br>`;
        }

        // Add mesh name and click point
        tooltipContent += `
                Mesh: ${faceInfo.meshName}<br>
                Click Point: (${faceInfo.clickPoint.x.toFixed(2)}, ${faceInfo.clickPoint.y.toFixed(2)}, ${faceInfo.clickPoint.z.toFixed(2)})
            </div>
        `;

        // Remove existing tooltip
        const existingTooltip = document.getElementById('json-face-tooltip');
        if (existingTooltip) {
            existingTooltip.remove();
        }

        // Create new tooltip
        const tooltip = document.createElement('div');
        tooltip.id = 'json-face-tooltip';
        tooltip.innerHTML = tooltipContent;

        // Position tooltip near mouse cursor, using viewport coordinates
        const viewerContainer = document.getElementById('json-viewer-3d');
        if (!viewerContainer) {
            console.error('JSON viewer container not found');
            return;
        }

        const viewerRect = viewerContainer.getBoundingClientRect();
        const mouseX = event.clientX - viewerRect.left;
        const mouseY = event.clientY - viewerRect.top;

        // Position tooltip to the right of cursor with offset to avoid overlap
        tooltip.style.left = (mouseX + 15) + 'px';
        tooltip.style.top = (mouseY - 15) + 'px';

        // Handle edge cases - prevent tooltip from going off-screen
        if (mouseX + 15 + 200 > viewerRect.width) { // Assume max tooltip width of 200px
            tooltip.style.left = (mouseX - 215) + 'px'; // Position to the left instead
        }
        if (mouseY - 15 < 0) {
            tooltip.style.top = (mouseY + 15) + 'px'; // Position below cursor instead
        }

        console.log('🎯 Tooltip positioned at:', {
            left: tooltip.style.left,
            top: tooltip.style.top,
            mouseX,
            mouseY,
            viewerRect
        });

        // Add to viewer container
        viewerContainer.appendChild(tooltip);
        console.log('🎯 Tooltip added to container');

        // Store tooltip reference for cleanup
        this.faceTooltip = tooltip;

        // Auto hide after 3 seconds (matching OBJ viewer timing)
        setTimeout(() => {
            if (tooltip && tooltip.parentNode) {
                tooltip.remove();
                this.faceTooltip = null;
                console.log('🎯 Tooltip auto-removed after 3 seconds');
            }
        }, 3000);
    }

    // Hide face tooltip
    hideFaceTooltip() {
        const existingTooltip = document.getElementById('json-face-tooltip');
        if (existingTooltip) {
            existingTooltip.remove();
        }
        this.faceTooltip = null;
    }

    // Handle right-click on model for context menu
    onModelRightClick(event) {
        console.log('🎯 JSON Viewer onModelRightClick triggered');

        if (!this.modelGroup) return;

        // Calculate mouse position in normalized device coordinates
        const rect = this.renderer.domElement.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2(x, y);
        raycaster.setFromCamera(mouse, this.camera);

        // Find intersections with model group children
        const intersects = raycaster.intersectObjects(this.modelGroup.children, true);

        if (intersects.length > 0) {
            const intersection = intersects[0];
            const mesh = intersection.object;


            if (!intersection.face || !mesh.geometry || !mesh.geometry.attributes.position) {
                console.warn("Intersected object or face is missing required data for context menu.");
                return;
            }

            // NEW: Check if this is a semantic shape
            if (mesh.userData.shapeInfo) {
                // Instantiate the analyzer and analyze the shape
                const analyzer = new GeometricAnalyzerJSON();
                const analysisResult = analyzer.analyze(mesh.userData.shapeInfo);

                // Show the new analysis panel
                this.showAnalysisPanel(event, analysisResult);

            } else {
                // --- New Face-Centric Analysis for Tessellated Formats ---
                if (!this.faceAnalyzer) {
                    console.error('FaceAnalyzer dependency was not injected into the viewer.');
                    return;
                }

                const realFaceId = mesh.userData.realFaceId;
                if (!realFaceId) {
                    console.error('No realFaceId found on the clicked mesh.');
                    return;
                }

                // 1. Gather all vertices for the selected logical face
                const faceVertices = this.getVerticesForFace(realFaceId);

                // 2. Analyze the collected vertices
                const faceIdInfo = {
                    realFaceId: mesh.userData.realFaceId,
                    faceIndex: mesh.userData.faceIndex,
                    bodyIndex: mesh.userData.bodyIndex
                };

                // 🔥 NEW: Use feature-aware analysis (countersink, chamfer, etc.)
                // Calculate optimal center for shape-aware orientation
                const box = new THREE.Box3().setFromObject(this.modelGroup);
                const optimalCenter = this.getOptimalCenter();
                const shapeType = this.jsonData?.shape_type || null;

                console.log(`🎯 [ORIENTATION] Using shape_type="${shapeType}" and optimalCenter:`, optimalCenter);

                const analysisResult = this.faceAnalyzer.analyzeWithFeatures(
                    faceVertices,
                    faceIdInfo,
                    this.features, // Pass loaded features array
                    {
                        shapeType: shapeType,
                        optimalCenter: optimalCenter
                    }
                );

                console.log('✅ [ANALYSIS] Result:', analysisResult.type,
                    analysisResult.subType ? `(${analysisResult.subType})` : '',
                    `- confidence: ${analysisResult.confidence}`);


                // 3. Display the results
                this.showAnalysisPanel(event, analysisResult);

                // 4. Highlight the selected face
                this.clearFaceHighlight(); // Clear previous highlight
                console.log(`🎨 [HIGHLIGHT] About to highlight face ${realFaceId}`);
                this.createFaceHighlight(intersection, mesh);

                // 5. Store intersection data for bounding box calculation
                this.selectedFace = {
                    mesh: mesh,
                    intersection: intersection,
                    geometry: mesh.geometry,
                    realFaceId: realFaceId
                };
            }
        } else {
            // Right-clicked on empty space - hide analysis panel
            this.hideAnalysisPanel();
            console.log('🎯 Right-clicked on empty space');
        }
    }



    showAnalysisPanel(event, analysisResult) {
        if (!analysisResult) return;

        console.log('📊 Creating analysis panel with result:', analysisResult);

        this.hideFaceTooltip();
        this.hideAnalysisPanel(); // Hide any existing panel first

        // --- Unify data from semantic and tessellated analysis ---
        let shapeType = 'Unknown';
        let properties = {};
        let title = 'Geometric Analysis';
        let error = analysisResult.error;
        let faceIdInfo = analysisResult.faceIdInfo || {};
        let faceOrientation = analysisResult.faceOrientation || 'Unknown';  // Get orientation

        if (analysisResult.calculatedProperties) { // Semantic JSON result
            shapeType = analysisResult.shape || analysisResult.type;
            properties = { ...analysisResult.parameters, ...analysisResult.calculatedProperties };
        } else if (analysisResult.detailedAnalysis) { // Tessellated mesh result
            if (analysisResult.success) {
                shapeType = analysisResult.detailedAnalysis.type;
                properties = analysisResult.detailedAnalysis.properties;
            } else {
                error = analysisResult.error;
            }
        }

        // --- Create Panel HTML ---
        let panelHTML = `<div class="json-analysis-panel-header">
                            <h4 class="json-analysis-panel-title">
                                <i class="fas fa-ruler-combined"></i> ${title}
                            </h4>
                            ${faceIdInfo.realFaceId ? `<div class="json-analysis-panel-subtitle">ID: ${faceIdInfo.realFaceId}</div>` : ''}
                            <button class="json-analysis-panel-close" onclick="window.jsonViewer.hideAnalysisPanel()">&times;</button>
                         </div>
                         <div class="json-analysis-panel-body">`;

        if (error) {
            panelHTML += `<div class="json-analysis-panel-item error"><strong>Error:</strong> ${error}</div>`;
        } else {
            panelHTML += `<div class="json-analysis-panel-item">
                            <span class="json-analysis-panel-label">Shape Type:</span>
                            <span class="json-analysis-panel-value">${shapeType}</span>
                         </div>`;

            // ✅ NEW: Face Orientation with color coding
            const orientationClass = `face-orientation-${faceOrientation.toLowerCase()}`;
            panelHTML += `<div class="json-analysis-panel-item">
                            <span class="json-analysis-panel-label">Face Orientation:</span>
                            <span class="json-analysis-panel-value ${orientationClass}">${faceOrientation}</span>
                         </div>`;

            panelHTML += `<div class="json-analysis-panel-subtitle">Properties</div>`;

            const hiddenPropertyKeys = new Set([
                'Length Axis',
                'Width Axis',
                'Normal',
                'Bend Axis',
                'Bend Profile Plane',
                'Bend Relation',
                'Bend Match Confidence',
                'axis'
            ]);

            // 🔥 Format properties with special handling for position and dimensions
            for (const [key, value] of Object.entries(properties)) {
                let formattedValue = value;

                // Skip position - we'll display it separately. Hide technical axis/vector metadata.
                if (key === 'position' || hiddenPropertyKeys.has(key)) continue;

                // Round numeric values to 1 decimal place
                if (typeof value === 'number') {
                    formattedValue = value.toFixed(1);
                } else if (typeof value === 'string') {
                    // Round only plain numeric strings. Keep values with units such as "10.00 mm".
                    if (/^-?\d+(\.\d+)?$/.test(value.trim())) {
                        const num = parseFloat(value);
                        formattedValue = num.toFixed(1);
                    }
                }

                panelHTML += `<div class="json-analysis-panel-item">
                                <span class="json-analysis-panel-label">${key}:</span>
                                <span class="json-analysis-panel-value">${formattedValue}</span>
                             </div>`;
            }

            // 🔥 Display position if available
            if (properties.position) {
                const pos = properties.position;
                const posStr = `(${(pos.x || 0).toFixed(1)}, ${(pos.y || 0).toFixed(1)}, ${(pos.z || 0).toFixed(1)})`;
                panelHTML += `<div class="json-analysis-panel-item">
                                <span class="json-analysis-panel-label">Position:</span>
                                <span class="json-analysis-panel-value">${posStr}</span>
                             </div>`;
            }

            // Add face selection button (Copy Full Details removed - chip system provides same functionality)
            panelHTML += `<div class="json-analysis-panel-actions">
                            <button class="json-select-face-btn" onclick="window.jsonViewer.selectFaceForChat()">
                                <i class="fas fa-plus-circle"></i> Select Face for Chat
                            </button>
                          </div>`;
        }
        panelHTML += `</div>`;

        // --- Create and Position Panel Element ---
        const panel = document.createElement('div');
        panel.id = 'json-analysis-panel';
        panel.className = 'json-analysis-panel';
        panel.innerHTML = panelHTML;

        const viewerContainer = document.getElementById('json-viewer-3d');
        if (!viewerContainer) return;

        // Enhanced positioning logic for fullscreen compatibility
        const isFullscreen = document.fullscreenElement !== null;
        const viewerRect = viewerContainer.getBoundingClientRect();

        let mouseX, mouseY;

        if (isFullscreen) {
            // In fullscreen, use event coordinates directly
            mouseX = event.clientX;
            mouseY = event.clientY;

            // Ensure panel stays within screen bounds
            const maxX = window.innerWidth - 320; // Panel width ~300px + margin
            const maxY = window.innerHeight - 400; // Panel height ~350px + margin

            mouseX = Math.min(mouseX + 20, maxX);
            mouseY = Math.min(mouseY + 20, maxY);
        } else {
            // Normal mode: relative to container
            mouseX = event.clientX - viewerRect.left + 20;
            mouseY = event.clientY - viewerRect.top + 20;

            // Ensure panel stays within container bounds
            const maxX = viewerRect.width - 320;
            const maxY = viewerRect.height - 400;

            mouseX = Math.min(mouseX, maxX);
            mouseY = Math.min(mouseY, maxY);
        }

        panel.style.left = `${mouseX}px`;
        panel.style.top = `${mouseY}px`;

        // Enhanced z-index for fullscreen mode
        if (isFullscreen) {
            panel.style.zIndex = '10000';
        }

        viewerContainer.appendChild(panel);
        setTimeout(() => panel.classList.add('show'), 10);

        // Store current analysis result for copy function
        this.currentAnalysisResult = analysisResult;
    }

    hideAnalysisPanel() {
        const panel = document.getElementById('json-analysis-panel');
        if (panel) {
            panel.classList.remove('show');
            setTimeout(() => panel.remove(), 300);
        }
    }



    clearFaceSelection() {
        // Clear highlight mesh
        this.clearFaceHighlight();

        // Clear edge highlight
        if (this.currentHighlight) {
            this.scene.remove(this.currentHighlight);
            this.currentHighlight = null;
        }

        // Hide tooltip
        this.hideFaceTooltip();

        // Hide analysis panel
        this.hideAnalysisPanel();

        // Hide edge analysis panel
        const edgePanel = document.getElementById('json-edge-analysis-panel');
        if (edgePanel) {
            edgePanel.remove();
        }

        // Clear selected face info
        this.selectedFace = null;

        // Clear highlight auto-hide timer
        if (this.highlightAutoHideTimer) {
            clearTimeout(this.highlightAutoHideTimer);
            this.highlightAutoHideTimer = null;
        }
        // Clear edge panel auto-hide timer
        if (this.edgePanelAutoHideTimer) {
            clearTimeout(this.edgePanelAutoHideTimer);
            this.edgePanelAutoHideTimer = null;
        }
    }

    // Initialize EdgeAnalyzer like OBJ viewer
    async initializeEdgeAnalyzer() {
        try {
            console.log('🔧 [INIT] Initializing EdgeAnalyzer for JSON viewer');

            // Load EdgeAnalyzer script if not already loaded
            if (!window.EdgeAnalyzer) {
                console.log('🔧 [INIT] Loading EdgeAnalyzer script...');
                await this.loadScript('./static/js/geometric_analysis_OBJ/EdgeAnalyzer.js');
            }

            // Check if EdgeAnalyzer is available
            if (window.EdgeAnalyzer) {
                this.edgeAnalyzer = new window.EdgeAnalyzer();
                console.log('✅ [INIT] EdgeAnalyzer initialized successfully');
            } else {
                throw new Error('EdgeAnalyzer not available after script load');
            }
        } catch (error) {
            console.error('❌ [INIT] Failed to initialize EdgeAnalyzer:', error);
            this.edgeAnalyzer = null;
        }
    }

    // Load script dynamically
    loadScript(src) {
        return new Promise((resolve, reject) => {
            // Check if script already exists
            const existingScript = document.querySelector(`script[src="${src}"]`);
            if (existingScript) {
                resolve();
                return;
            }

            const script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    // Create simple edge highlight (already in world coordinates)
    createSimpleEdgeHighlight(edge) {
        console.log('✨ [SIMPLE_HIGHLIGHT] Creating simple edge highlight');

        // Create line geometry for the edge
        const geometry = new THREE.BufferGeometry().setFromPoints([edge.start, edge.end]);
        const material = new THREE.LineBasicMaterial({
            color: 0xff0000, // Red color for edge highlight
            linewidth: 5,
            transparent: true,
            opacity: 0.9
        });

        const edgeLine = new THREE.Line(geometry, material);
        edgeLine.name = 'simple_edge_highlight';

        this.scene.add(edgeLine);
        this.currentHighlight = edgeLine;

        console.log('✅ [SIMPLE_HIGHLIGHT] Edge highlighted');
    }

    // Schedule highlight auto-hide after specified time
    scheduleHighlightAutoHide(delayMs) {
        console.log(`⏰ [AUTO_HIDE] Scheduling highlight auto-hide in ${delayMs}ms`);

        // Clear any existing timer
        if (this.highlightAutoHideTimer) {
            clearTimeout(this.highlightAutoHideTimer);
        }

        // Set new timer
        this.highlightAutoHideTimer = setTimeout(() => {
            console.log('⏰ [AUTO_HIDE] Auto-hiding highlight');
            if (this.currentHighlight) {
                this.scene.remove(this.currentHighlight);
                this.currentHighlight = null;
                console.log('✅ [AUTO_HIDE] Highlight auto-hidden');
            }
            this.highlightAutoHideTimer = null;
        }, delayMs);
    }

    // Schedule edge panel auto-hide after specified time
    scheduleEdgePanelAutoHide(delayMs) {
        console.log(`⏰ [EDGE_PANEL_AUTO_HIDE] Scheduling panel auto-hide in ${delayMs}ms`);

        // Clear any existing timer
        if (this.edgePanelAutoHideTimer) {
            clearTimeout(this.edgePanelAutoHideTimer);
        }

        // Set new timer
        this.edgePanelAutoHideTimer = setTimeout(() => {
            console.log('⏰ [EDGE_PANEL_AUTO_HIDE] Auto-hiding edge panel');
            const panel = document.getElementById('json-edge-analysis-panel');
            if (panel) {
                panel.remove();
                console.log('✅ [EDGE_PANEL_AUTO_HIDE] Edge panel auto-hidden');
            }
            this.edgePanelAutoHideTimer = null;
        }, delayMs);
    }

    // Handle edge calculation using EdgeAnalyzer (like OBJ viewer)
    async handleEdgeCalculationWithAnalyzer(event, intersection) {
        console.log('🎯 [EDGE_CALC] === USING EDGE ANALYZER (OBJ Style) ===');

        if (!this.edgeAnalyzer) {
            console.error('❌ [EDGE_CALC] EdgeAnalyzer not available');
            return;
        }

        const mesh = intersection.object;
        const face = intersection.face;
        const clickWorldPoint = intersection.point;
        const geometry = mesh.geometry;

        // Convert click point to local space (like OBJ viewer)
        const clickLocalPoint = clickWorldPoint.clone();
        mesh.worldToLocal(clickLocalPoint);

        console.log('🔍 [EDGE_CALC] Click points:', {
            world: `(${clickWorldPoint.x.toFixed(2)}, ${clickWorldPoint.y.toFixed(2)}, ${clickWorldPoint.z.toFixed(2)})`,
            local: `(${clickLocalPoint.x.toFixed(2)}, ${clickLocalPoint.y.toFixed(2)}, ${clickLocalPoint.z.toFixed(2)})`
        });

        console.log('🔍 [EDGE_CALC] Finding real edges...');

        // Find real edges using EdgeAnalyzer
        const realEdges = this.edgeAnalyzer.findRealEdges(geometry);

        if (!realEdges || realEdges.length === 0) {
            console.error('❌ [EDGE_CALC] No real edges found');
            return;
        }

        console.log(`🔍 [EDGE_CALC] Found ${realEdges.length} real edges`);

        // Find closest edge in clicked triangle
        const closestEdge = this.edgeAnalyzer.findClosestEdgeInTriangle(
            face, geometry, clickLocalPoint, realEdges
        );

        if (!closestEdge) {
            console.error('❌ [EDGE_CALC] No edge found near click point');
            return;
        }

        // Calculate edge properties in both coordinate systems
        const edgeLocalStart = closestEdge.start;
        const edgeLocalEnd = closestEdge.end;
        const edgeLocalLength = edgeLocalStart.distanceTo(edgeLocalEnd);

        // Transform to world coordinates for highlight
        const edgeWorldStart = edgeLocalStart.clone().applyMatrix4(mesh.matrixWorld);
        const edgeWorldEnd = edgeLocalEnd.clone().applyMatrix4(mesh.matrixWorld);

        console.log('🔍 [EDGE_CALC] Edge found:', {
            local: `Start(${edgeLocalStart.x.toFixed(2)}, ${edgeLocalStart.y.toFixed(2)}, ${edgeLocalStart.z.toFixed(2)}) End(${edgeLocalEnd.x.toFixed(2)}, ${edgeLocalEnd.y.toFixed(2)}, ${edgeLocalEnd.z.toFixed(2)})`,
            world: `Start(${edgeWorldStart.x.toFixed(2)}, ${edgeWorldStart.y.toFixed(2)}, ${edgeWorldStart.z.toFixed(2)}) End(${edgeWorldEnd.x.toFixed(2)}, ${edgeWorldEnd.y.toFixed(2)}, ${edgeWorldEnd.z.toFixed(2)})`,
            length: edgeLocalLength.toFixed(2)
        });

        // Use local coordinates for chatbot (correct values)
        const chatbotStart = edgeLocalStart;
        const chatbotEnd = edgeLocalEnd;
        const chatbotLength = edgeLocalLength;

        // Use world coordinates for highlight (correct rendering position)
        const highlightStart = edgeWorldStart;
        const highlightEnd = edgeWorldEnd;

        console.log('🎯 [EDGE_CALC] Final decision:');
        console.log('  - Chatbot coordinates (local):', `Start(${chatbotStart.x.toFixed(1)}, ${chatbotStart.y.toFixed(1)}, ${chatbotStart.z.toFixed(1)}) End(${chatbotEnd.x.toFixed(1)}, ${chatbotEnd.y.toFixed(1)}, ${chatbotEnd.z.toFixed(1)})`);
        console.log('  - Highlight coordinates (world):', `Start(${highlightStart.x.toFixed(1)}, ${highlightStart.y.toFixed(1)}, ${highlightStart.z.toFixed(1)}) End(${highlightEnd.x.toFixed(1)}, ${highlightEnd.y.toFixed(1)}, ${highlightEnd.z.toFixed(1)})`);

        // Highlight the edge with auto-hide after 5 seconds (use world coordinates)
        this.clearFaceHighlight();
        this.createSimpleEdgeHighlight({ start: highlightStart, end: highlightEnd });
        this.scheduleHighlightAutoHide(5000); // 5 seconds

        // Show Edge Analysis Panel (use local coordinates for display)
        const edgeAnalysisResult = {
            type: 'Edge Analysis',
            edge: { start: chatbotStart, end: chatbotEnd },
            length: chatbotLength,
            start: chatbotStart,
            end: chatbotEnd,
            clickPoint: clickWorldPoint
        };

        console.log('📊 [EDGE_CALC] Calling showEdgeAnalysisPanel with:', edgeAnalysisResult);
        this.showEdgeAnalysisPanel(event, edgeAnalysisResult);

        // NOTE: Ctrl+Click only selects edge and shows panel - user must click "Select Edge for Chat" button to copy

        console.log('✅ [EDGE_CALC] Edge calculation with EdgeAnalyzer completed');
    }



    // Show Edge Analysis Panel similar to Geometric Analysis
    showEdgeAnalysisPanel(event, edgeAnalysisResult) {
        console.log('📊 [EDGE_PANEL] Creating edge analysis panel');

        // Hide any existing panels
        this.hideAnalysisPanel();
        const existingEdgePanel = document.getElementById('json-edge-analysis-panel');
        if (existingEdgePanel) {
            existingEdgePanel.remove();
        }

        const { start, end, length } = edgeAnalysisResult;

        // Format coordinates
        const startCoords = `(${start.x.toFixed(1)}, ${start.y.toFixed(1)}, ${start.z.toFixed(1)})`;
        const endCoords = `(${end.x.toFixed(1)}, ${end.y.toFixed(1)}, ${end.z.toFixed(1)})`;
        const edgeLength = length.toFixed(1);

        // Create panel HTML
        const panelHTML = `
            <div class="json-analysis-panel-header">
                <h4 class="json-analysis-panel-title"><i class="fas fa-ruler"></i> 📏 Edge Analysis</h4>
                <button class="json-analysis-panel-close" onclick="document.getElementById('json-edge-analysis-panel').remove()">&times;</button>
            </div>
            <div class="json-analysis-panel-body">
                <div class="json-analysis-panel-item">
                    <span class="json-analysis-panel-label">Edge Length:</span>
                    <span class="json-analysis-panel-value">${edgeLength} units</span>
                </div>
                <div class="json-analysis-panel-item">
                    <span class="json-analysis-panel-label">Start Point:</span>
                    <span class="json-analysis-panel-value">${startCoords}</span>
                </div>
                <div class="json-analysis-panel-item">
                    <span class="json-analysis-panel-label">End Point:</span>
                    <span class="json-analysis-panel-value">${endCoords}</span>
                </div>
                <div class="json-analysis-panel-actions">
                    <button class="json-select-edge-btn" onclick="window.jsonViewer.selectEdgeForChat()">
                        <i class="fas fa-plus-circle"></i> Select Edge for Chat
                    </button>
                </div>
            </div>
        `;

        // Create and position panel
        const panel = document.createElement('div');
        panel.id = 'json-edge-analysis-panel';
        panel.className = 'json-analysis-panel';
        panel.innerHTML = panelHTML;

        // Position panel
        const viewerContainer = document.getElementById('json-viewer-3d');
        if (!viewerContainer) return;

        const viewerRect = viewerContainer.getBoundingClientRect();
        const mouseX = Math.min(event.clientX - viewerRect.left + 20, viewerRect.width - 320);
        const mouseY = Math.min(event.clientY - viewerRect.top + 20, viewerRect.height - 400);

        panel.style.left = `${mouseX}px`;
        panel.style.top = `${mouseY}px`;

        viewerContainer.appendChild(panel);
        setTimeout(() => panel.classList.add('show'), 10);

        // Store current panel reference
        this.currentAnalysisPanel = panel;

        // Store edge info for copy function
        this.currentEdgeInfo = {
            length: edgeLength,
            start: startCoords,
            end: endCoords,
            coordinates: `Start${startCoords} End${endCoords} Length(${edgeLength})`
        };

        // Store current edge analysis result for selection
        this.currentEdgeAnalysisResult = edgeAnalysisResult;

        // Auto-hide panel after 5 seconds
        this.scheduleEdgePanelAutoHide(5000);

        console.log('✅ [EDGE_PANEL] Edge analysis panel created');
    }

    // NEW: Select edge for chat using ChatGPT-like chip interface
    selectEdgeForChat() {
        console.log('🎯 [SELECT_EDGE] Selecting edge for chat interface');

        if (!this.currentEdgeAnalysisResult) {
            console.error('❌ [SELECT_EDGE] No edge analysis result available');
            return;
        }

        // Check if selection manager is available
        if (!window.selectionManager && !window.faceSelectionManager) {
            console.error('❌ [SELECT_EDGE] Selection manager not available');
            return;
        }

        const manager = window.selectionManager || window.faceSelectionManager;

        // Generate edge ID from analysis result
        const { start, end, length } = this.currentEdgeAnalysisResult;
        const edgeId = `edge_${start.x.toFixed(0)}_${start.y.toFixed(0)}_${start.z.toFixed(0)}_${end.x.toFixed(0)}_${end.y.toFixed(0)}_${end.z.toFixed(0)}`;

        // Use selection manager to add as chip
        manager.onEdgeSelected(edgeId, this.currentEdgeAnalysisResult);

        // Hide the edge analysis panel after selection
        const edgePanel = document.getElementById('json-edge-analysis-panel');
        if (edgePanel) {
            edgePanel.remove();
        }

        // Clear edge highlight when selecting edge for chat
        this.clearFaceHighlight();
        console.log('🎯 [SELECT_EDGE] Cleared edge highlight after selection');

        console.log('✅ [SELECT_EDGE] Edge selected for chat:', edgeId);
    }



    // Copy edge coordinates to chatbot
    copyEdgeToChatbot(edgeText) {
        console.log('📋 [COPY_EDGE] Copying edge to chatbot:', edgeText);

        // Clear highlight when copying edge
        this.clearFaceHighlight();
        console.log('🎯 [COPY_EDGE] Cleared highlight after copying edge');

        // Find chatbot input
        const chatInput = document.querySelector('#chat-input, .chat-input, input[placeholder*="message"], textarea[placeholder*="message"]');

        if (chatInput) {
            // Set the text
            chatInput.value = edgeText;

            // Trigger input events to notify any listeners
            chatInput.dispatchEvent(new Event('input', { bubbles: true }));
            chatInput.dispatchEvent(new Event('change', { bubbles: true }));

            // Focus the input
            chatInput.focus();

            console.log('✅ [COPY_EDGE] Edge coordinates copied to chatbot successfully');
        } else {
            console.error('❌ [COPY_EDGE] Chatbot input not found');

            // Fallback: copy to clipboard
            navigator.clipboard.writeText(edgeText).then(() => {
                console.log('✅ [COPY_EDGE] Edge coordinates copied to clipboard as fallback');
            }).catch(err => {
                console.error('❌ [COPY_EDGE] Failed to copy to clipboard:', err);
            });
        }
    }

    // Copy face bounding box to chatbot (LEGACY: Used as fallback when chip selection system unavailable)
    copyFaceBoundingBoxToChatbot() {
        console.log('📋 [COPY_FACE] Copying face bounding box to chatbot');

        if (!this.currentAnalysisResult) {
            console.error('❌ [COPY_FACE] No analysis result available');
            return;
        }

        // Debug: Log the analysis result structure
        console.log('🔍 [COPY_FACE] Current analysis result:', this.currentAnalysisResult);

        // Get face ID
        let faceId = 'unknown';
        if (this.currentAnalysisResult.faceIdInfo && this.currentAnalysisResult.faceIdInfo.realFaceId) {
            faceId = this.currentAnalysisResult.faceIdInfo.realFaceId;
        } else if (this.selectedFace && this.selectedFace.realFaceId) {
            faceId = this.selectedFace.realFaceId;
        }

        // Get vertices for the specific face
        const faceVertices = this.getVerticesForFace(faceId);

        // Calculate bounding box from analysis result
        let boundingBoxText = '';

        if (this.currentAnalysisResult.calculatedProperties) {
            // Semantic JSON result - use calculated properties
            const props = this.currentAnalysisResult.calculatedProperties;
            if (props.boundingBox) {
                const bbox = props.boundingBox;
                boundingBoxText = `Face Bounding Box: Min(${bbox.min.x.toFixed(1)}, ${bbox.min.y.toFixed(1)}, ${bbox.min.z.toFixed(1)}) Max(${bbox.max.x.toFixed(1)}, ${bbox.max.y.toFixed(1)}, ${bbox.max.z.toFixed(1)}) Size(${bbox.size.x.toFixed(1)}, ${bbox.size.y.toFixed(1)}, ${bbox.size.z.toFixed(1)})`;
            }
        } else if (this.currentAnalysisResult.detailedAnalysis && this.currentAnalysisResult.detailedAnalysis.properties) {
            // Tessellated mesh result - use detailed analysis properties
            const props = this.currentAnalysisResult.detailedAnalysis.properties;
            if (props.boundingBox) {
                const bbox = props.boundingBox;
                boundingBoxText = `Face Bounding Box: Min(${bbox.min.x.toFixed(1)}, ${bbox.min.y.toFixed(1)}, ${bbox.min.z.toFixed(1)}) Max(${bbox.max.x.toFixed(1)}, ${bbox.max.y.toFixed(1)}, ${bbox.max.z.toFixed(1)}) Size(${bbox.size.x.toFixed(1)}, ${bbox.size.y.toFixed(1)}, ${bbox.size.z.toFixed(1)})`;
            }
        }

        if (!boundingBoxText) {
            // Fallback: Calculate bounding box from current face selection
            console.log('🔍 [COPY_FACE] No bounding box in analysis result, calculating from face data');

            // Try to get bounding box from the selected face
            if (this.selectedFace && this.selectedFace.realFaceId) {
                console.log('🔍 [COPY_FACE] Calculating bounding box from face vertices');

                if (faceVertices && faceVertices.length > 0) {
                    // Calculate bounding box from face vertices
                    const bbox = new THREE.Box3();
                    faceVertices.forEach(vertex => {
                        bbox.expandByPoint(new THREE.Vector3(vertex.x, vertex.y, vertex.z));
                    });

                    const size = bbox.getSize(new THREE.Vector3());

                    // Enhanced face identification format
                    boundingBoxText = this.generateEnhancedFaceIdentification(
                        this.selectedFace.realFaceId,
                        bbox,
                        size,
                        faceVertices,
                        this.currentAnalysisResult
                    );
                    console.log('✅ [COPY_FACE] Generated enhanced face identification');
                } else {
                    // Fallback to mesh geometry
                    const geometry = this.selectedFace.geometry;
                    let bbox;

                    // Use proper method to get bounding box from geometry
                    if (geometry.boundingBox) {
                        bbox = geometry.boundingBox.clone();
                    } else {
                        geometry.computeBoundingBox();
                        bbox = geometry.boundingBox ? geometry.boundingBox.clone() : new THREE.Box3();
                    }

                    const size = bbox.getSize(new THREE.Vector3());

                    // Enhanced face identification format (fallback)
                    boundingBoxText = this.generateEnhancedFaceIdentification(
                        this.selectedFace.realFaceId,
                        bbox,
                        size,
                        null, // No face vertices available
                        this.currentAnalysisResult
                    );
                    console.log('✅ [COPY_FACE] Generated enhanced face identification (fallback)');
                }
            } else {
                // Final fallback: use generic message
                boundingBoxText = `Face Bounding Box: Analysis data not available`;
                console.warn('⚠️ [COPY_FACE] No bounding box data found, using fallback');
            }
        }

        // Find chatbot input
        const chatInput = document.querySelector('#chat-input, .chat-input, input[placeholder*="message"], textarea[placeholder*="message"]');

        if (chatInput) {
            // Set the text
            chatInput.value = boundingBoxText;

            // Trigger input events to notify any listeners
            chatInput.dispatchEvent(new Event('input', { bubbles: true }));
            chatInput.dispatchEvent(new Event('change', { bubbles: true }));

            // Focus the input
            chatInput.focus();

            console.log('✅ [COPY_FACE] Face bounding box copied to chatbot successfully');
        } else {
            console.error('❌ [COPY_FACE] Chatbot input not found');

            // Fallback: copy to clipboard
            navigator.clipboard.writeText(boundingBoxText).then(() => {
                console.log('✅ [COPY_FACE] Face bounding box copied to clipboard as fallback');
            }).catch(err => {
                console.error('❌ [COPY_FACE] Failed to copy to clipboard:', err);
            });
        }
    }

    // NEW: Select face for chat using ChatGPT-like chip interface
    selectFaceForChat() {
        console.log('🎯 [SELECT_FACE] Selecting face for chat interface');

        if (!this.currentAnalysisResult) {
            console.error('❌ [SELECT_FACE] No analysis result available');
            return;
        }

        // Check if selection manager is available
        if (!window.selectionManager && !window.faceSelectionManager) {
            console.error('❌ [SELECT_FACE] Selection manager not available');
            // Fallback to old behavior
            this.copyFaceBoundingBoxToChatbot();
            return;
        }

        const manager = window.selectionManager || window.faceSelectionManager;

        // Get face ID from current analysis
        let faceId = 'unknown';
        if (this.currentAnalysisResult.faceIdInfo && this.currentAnalysisResult.faceIdInfo.realFaceId) {
            faceId = this.currentAnalysisResult.faceIdInfo.realFaceId;
        } else if (this.selectedFace && this.selectedFace.realFaceId) {
            faceId = this.selectedFace.realFaceId;
        }

        // Get vertices for the specific face
        const faceVertices = this.getVerticesForFace(faceId);

        // IMPORTANT: Generate the SAME context as "Copy Full Details" button
        let fullContext = '';

        // Try to get the same bounding box data as copyFaceBoundingBoxToChatbot()
        if (this.currentAnalysisResult.calculatedProperties) {
            // Semantic JSON result - use calculated properties
            const props = this.currentAnalysisResult.calculatedProperties;
            if (props.boundingBox) {
                const bbox = props.boundingBox;
                fullContext = `Face Selection: ID[${faceId}] Type[${this.currentAnalysisResult.type || 'Unknown Face'}] Position[center(${((bbox.min.x + bbox.max.x) / 2).toFixed(1)}, ${((bbox.min.y + bbox.max.y) / 2).toFixed(1)}, ${((bbox.min.z + bbox.max.z) / 2).toFixed(1)})] BBox[Min(${bbox.min.x.toFixed(1)}, ${bbox.min.y.toFixed(1)}, ${bbox.min.z.toFixed(1)}) Max(${bbox.max.x.toFixed(1)}, ${bbox.max.y.toFixed(1)}, ${bbox.max.z.toFixed(1)}) Size(${bbox.size.x.toFixed(1)}, ${bbox.size.y.toFixed(1)}, ${bbox.size.z.toFixed(1)})] Geometry[basic] Context[calculated]`;
            }
        } else if (this.currentAnalysisResult.detailedAnalysis && this.currentAnalysisResult.detailedAnalysis.properties) {
            // Tessellated mesh result - use detailed analysis properties
            const props = this.currentAnalysisResult.detailedAnalysis.properties;
            if (props.boundingBox) {
                const bbox = props.boundingBox;
                fullContext = `Face Selection: ID[${faceId}] Type[${this.currentAnalysisResult.detailedAnalysis.type || 'Unknown Face'}] Position[center(${((bbox.min.x + bbox.max.x) / 2).toFixed(1)}, ${((bbox.min.y + bbox.max.y) / 2).toFixed(1)}, ${((bbox.min.z + bbox.max.z) / 2).toFixed(1)})] BBox[Min(${bbox.min.x.toFixed(1)}, ${bbox.min.y.toFixed(1)}, ${bbox.min.z.toFixed(1)}) Max(${bbox.max.x.toFixed(1)}, ${bbox.max.y.toFixed(1)}, ${bbox.max.z.toFixed(1)}) Size(${bbox.size.x.toFixed(1)}, ${bbox.size.y.toFixed(1)}, ${bbox.size.z.toFixed(1)})] Geometry[detailed] Context[analyzed]`;
            }
        }

        // If no context from analysis result, use the SAME fallback as copyFaceBoundingBoxToChatbot()
        if (!fullContext && this.selectedFace && this.selectedFace.realFaceId) {
            console.log('🔍 [SELECT_FACE] Using same fallback as Copy Full Details');

            if (faceVertices && faceVertices.length > 0) {
                // Calculate bounding box from face vertices (SAME as Copy Full Details)
                const bbox = new THREE.Box3();
                faceVertices.forEach(vertex => {
                    bbox.expandByPoint(new THREE.Vector3(vertex.x, vertex.y, vertex.z));
                });

                const size = bbox.getSize(new THREE.Vector3());

                // Use the SAME enhanced face identification as Copy Full Details
                fullContext = this.generateEnhancedFaceIdentification(
                    this.selectedFace.realFaceId,
                    bbox,
                    size,
                    faceVertices,
                    this.currentAnalysisResult
                );
                console.log('✅ [SELECT_FACE] Generated same context as Copy Full Details');
            }
        }

        // If still no context, create a basic one
        if (!fullContext) {
            fullContext = `Face Selection: ID[${faceId}] Type[Unknown Face] Position[unknown] BBox[Min(0.0, 0.0, 0.0) Max(0.0, 0.0, 0.0) Size(0.0, 0.0, 0.0)] Geometry[unknown] Context[unavailable]`;
        }

        // Add face selection with the SAME context as Copy Full Details
        manager.addFaceSelection(faceId, fullContext);

        // Hide the analysis panel after selection
        this.hideAnalysisPanel();

        console.log('✅ [SELECT_FACE] Face selected for chat with same context as Copy Full Details:', faceId);
    }

    // Generate enhanced face identification format for AI processing
    generateEnhancedFaceIdentification(faceId, bbox, size, faceVertices, analysisResult) {
        console.log('🔍 [ENHANCED_ID] Generating enhanced face identification');

        // 1. Face ID and basic info
        const faceIdStr = faceId || 'unknown';

        // 2. Shape type from analysis result
        let shapeType = 'unknown';
        let confidence = 0;
        if (analysisResult) {
            if (analysisResult.calculatedProperties) {
                shapeType = analysisResult.type || 'unknown';
            } else if (analysisResult.detailedAnalysis) {
                shapeType = analysisResult.detailedAnalysis.type || 'unknown';
                confidence = analysisResult.detailedAnalysis.confidence || 0;
            }
        }

        // 3. Spatial context analysis
        const spatialContext = this.analyzeSpatialContext(bbox, size);

        // 4. Geometric properties
        const geometricProps = this.analyzeGeometricProperties(bbox, size, faceVertices);

        // 5. Relationship context
        const relationshipInfo = this.analyzeRelationshipContext(bbox, faceId);

        // 6. Generate comprehensive face identification
        const enhancedId = `Face Selection: ID[${faceIdStr}] Type[${shapeType}${confidence > 0 ? ` (${(confidence * 100).toFixed(0)}%)` : ''}] Position[${spatialContext}] BBox[Min(${bbox.min.x.toFixed(1)}, ${bbox.min.y.toFixed(1)}, ${bbox.min.z.toFixed(1)}) Max(${bbox.max.x.toFixed(1)}, ${bbox.max.y.toFixed(1)}, ${bbox.max.z.toFixed(1)}) Size(${size.x.toFixed(1)}, ${size.y.toFixed(1)}, ${size.z.toFixed(1)})] Geometry[${geometricProps}] Context[${relationshipInfo}]`;

        console.log('✅ [ENHANCED_ID] Generated:', enhancedId);
        return enhancedId;
    }

    // Analyze spatial context of the face
    analyzeSpatialContext(bbox, size) {
        const center = bbox.getCenter(new THREE.Vector3());
        const modelBounds = this.getModelBounds();

        if (!modelBounds) {
            return `center(${center.x.toFixed(1)}, ${center.y.toFixed(1)}, ${center.z.toFixed(1)})`;
        }

        // Determine position relative to model
        const modelCenter = modelBounds.getCenter(new THREE.Vector3());
        const modelSize = modelBounds.getSize(new THREE.Vector3());

        // Relative position analysis
        const relX = (center.x - modelCenter.x) / (modelSize.x / 2);
        const relY = (center.y - modelCenter.y) / (modelSize.y / 2);
        const relZ = (center.z - modelCenter.z) / (modelSize.z / 2);

        // Generate position descriptors
        const posX = Math.abs(relX) > 0.7 ? (relX > 0 ? 'right' : 'left') : 'center';
        const posY = Math.abs(relY) > 0.7 ? (relY > 0 ? 'back' : 'front') : 'middle';
        const posZ = Math.abs(relZ) > 0.7 ? (relZ > 0 ? 'top' : 'bottom') : 'middle';

        return `${posZ}-${posY}-${posX} face, center(${center.x.toFixed(1)}, ${center.y.toFixed(1)}, ${center.z.toFixed(1)})`;
    }

    // Analyze geometric properties
    analyzeGeometricProperties(bbox, size, faceVertices) {
        const props = [];

        // Dimension analysis
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const aspectRatio = dims[0] / dims[2];

        if (aspectRatio > 10) {
            props.push('elongated');
        } else if (aspectRatio < 1.5) {
            props.push('square-like');
        } else {
            props.push('rectangular');
        }

        // Size classification
        const volume = size.x * size.y * size.z;
        if (volume > 1000) {
            props.push('large');
        } else if (volume < 10) {
            props.push('small');
        } else {
            props.push('medium');
        }

        // Vertex count if available
        if (faceVertices) {
            props.push(`${faceVertices.length}vertices`);
        }

        return props.join(', ');
    }

    // Analyze relationship context with other faces/features
    analyzeRelationshipContext(bbox, faceId) {
        const context = [];

        // Model bounds relationship
        const modelBounds = this.getModelBounds();
        if (modelBounds) {
            const tolerance = 0.1;

            // Check if face is on model boundary
            if (Math.abs(bbox.min.x - modelBounds.min.x) < tolerance ||
                Math.abs(bbox.max.x - modelBounds.max.x) < tolerance) {
                context.push('X-boundary');
            }
            if (Math.abs(bbox.min.y - modelBounds.min.y) < tolerance ||
                Math.abs(bbox.max.y - modelBounds.max.y) < tolerance) {
                context.push('Y-boundary');
            }
            if (Math.abs(bbox.min.z - modelBounds.min.z) < tolerance ||
                Math.abs(bbox.max.z - modelBounds.max.z) < tolerance) {
                context.push('Z-boundary');
            }
        }

        // Face orientation
        const size = bbox.getSize(new THREE.Vector3());
        const dims = [
            { axis: 'X', size: size.x },
            { axis: 'Y', size: size.y },
            { axis: 'Z', size: size.z }
        ].sort((a, b) => a.size - b.size);

        // Determine primary orientation
        if (dims[0].size < 0.1) {
            context.push(`${dims[0].axis}-aligned plane`);
        }

        return context.length > 0 ? context.join(', ') : 'internal face';
    }

    // Get model bounds for spatial analysis
    getModelBounds() {
        if (!this.modelGroup || this.modelGroup.children.length === 0) {
            return null;
        }

        const bbox = new THREE.Box3();
        this.modelGroup.children.forEach(child => {
            if (child.geometry) {
                // Use setFromObject for mesh objects or compute from geometry
                if (child.isMesh) {
                    const childBox = new THREE.Box3().setFromObject(child);
                    bbox.union(childBox);
                } else {
                    // Fallback: compute from geometry vertices
                    child.geometry.computeBoundingBox();
                    if (child.geometry.boundingBox) {
                        bbox.union(child.geometry.boundingBox);
                    }
                }
            }
        });

        return bbox.isEmpty() ? null : bbox;
    }

    // Debug function to check model state
    debugModelState() {
        console.log('🔍 [DEBUG_MODEL] Model state check:');
        console.log('  - ModelGroup exists:', !!this.modelGroup);
        console.log('  - ModelGroup visible:', this.modelGroup?.visible);
        console.log('  - ModelGroup children count:', this.modelGroup?.children.length);
        console.log('  - ModelGroup position:', this.modelGroup?.position);
        console.log('  - ModelGroup scale:', this.modelGroup?.scale);
        console.log('  - ModelGroup rotation:', this.modelGroup?.rotation);
        console.log('  - Current model reference:', !!this.currentModel);
        console.log('  - Scene children count:', this.scene?.children.length);
        console.log('  - Camera position:', this.camera?.position);
        console.log('  - Controls target:', this.controls?.target);

        if (this.modelGroup && this.modelGroup.children.length > 0) {
            this.modelGroup.children.forEach((child, index) => {
                console.log(`  - Child ${index}:`, {
                    name: child.name,
                    visible: child.visible,
                    position: child.position,
                    scale: child.scale,
                    hasGeometry: !!child.geometry,
                    hasMaterial: !!child.material
                });
            });
        }
    }

    // Force refresh model visibility
    forceRefreshModel() {
        console.log('🔄 [FORCE_REFRESH] Forcing model refresh...');

        if (!this.modelGroup) {
            console.error('❌ [FORCE_REFRESH] No modelGroup found');
            return;
        }

        // Ensure modelGroup is visible
        this.modelGroup.visible = true;

        // Reset scale if it's invalid
        const scale = this.modelGroup.scale;
        if (scale.x <= 0 || scale.y <= 0 || scale.z <= 0 ||
            scale.x > 1000 || scale.y > 1000 || scale.z > 1000) {
            console.warn('⚠️ [FORCE_REFRESH] Invalid scale detected, resetting to 1');
            this.modelGroup.scale.setScalar(1);
        }

        // Ensure all children are visible
        this.modelGroup.children.forEach(child => {
            if (child.isMesh) {
                child.visible = true;
                if (child.material) {
                    child.material.visible = true;
                    child.material.transparent = false;
                    child.material.opacity = 1.0;
                }
            }
        });

        // Force camera update
        if (this.controls) {
            this.controls.update();
        }

        // Force render
        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }

        console.log('✅ [FORCE_REFRESH] Model refresh completed');
        this.debugModelState();
    }

    // Control functions to match OBJ viewer interface
    resetCamera() {
        if (this.modelGroup && this.modelGroup.children.length > 0) {
            const box = new THREE.Box3().setFromObject(this.modelGroup);
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);

            // Position camera in isometric view like CAD software
            const distance = maxDim * 2.5;
            this.camera.position.set(
                center.x + distance * 0.7,
                center.y + distance * 0.7,
                center.z + distance * 0.7
            );

            // Look at and orbit around the visual center of the model
            this.camera.lookAt(center);
            this.controls.target.copy(center);

        } else {
            // Default camera position if no model
            this.camera.position.set(10, 10, 10);
            this.camera.lookAt(0, 0, 0);
            this.controls.target.set(0, 0, 0);
        }

        if (this.controls) {
            this.controls.update();
        }
        this.showStatus('JSON view reset', 'success');
    }


    toggleFullscreen() {
        // Use the same approach as OBJ viewer - target the 3D container
        const container = this.container;
        if (!container) {
            console.error('Fullscreen container not found');
            return;
        }

        if (!document.fullscreenElement) {
            // Request fullscreen
            container.requestFullscreen().then(() => {
                this.onWindowResize(); // Ensure the viewer resizes correctly
            }).catch(err => {
                console.error(`Error attempting to enable full-screen mode: ${err.message}`, err);
            });
        } else {
            // Exit fullscreen
            if (document.exitFullscreen) {
                document.exitFullscreen();
            }
        }
    }

    setupEventListeners() {
        // Fullscreen button - match OBJ viewer pattern
        document.getElementById('fullscreen-json-viewer-btn')?.addEventListener('click', () => {
            this.toggleFullscreen();
        });

        // ViewCube toggle button (controls panel)
        document.getElementById('json-toggle-viewcube-btn')?.addEventListener('click', () => {
            this.toggleViewCube();
            const btn = document.getElementById('json-toggle-viewcube-btn');
            if (btn) {
                btn.classList.toggle('btn-active', this.isViewCubeVisible);
                btn.title = this.isViewCubeVisible ? 'Turn off ViewCube (currently ON)' : 'Turn on ViewCube Navigation';
            }
            console.log(`🏙️ [UI] ViewCube: ${this.isViewCubeVisible ? 'ON ✅' : 'OFF ⭕'}`);
        });

        // Debug Dot toggle button ( controls panel)
        document.getElementById('json-toggle-debug-dot-btn')?.addEventListener('click', () => {
            const btn = document.getElementById('json-toggle-debug-dot-btn');
            this._debugDotVisible = !this._debugDotVisible;
            if (this._debugDotVisible) {
                this.showDebugCenterDot();
                btn?.classList.add('btn-danger-active');
                if (btn) btn.title = 'Turn off Debug Dots (currently ON 🔴)';
                console.log('🔴 [UI] Debug dots ON');
            } else {
                this.hideDebugCenterDot();
                btn?.classList.remove('btn-danger-active');
                if (btn) btn.title = 'Turn on Debug Center Dots';
                console.log('⚫ [UI] Debug dots OFF');
            }
        });
    }

    setupFaceSelectionListeners() {
        if (!this.renderer) {
            console.error('Renderer not available for face selection setup');
            return;
        }

        const canvas = this.renderer.domElement;
        let isDragging = false;
        let startPointerPos = { x: 0, y: 0 };

        // Prevent the default context menu on the canvas
        canvas.addEventListener('contextmenu', e => e.preventDefault());

        canvas.addEventListener('pointerdown', (e) => {
            isDragging = false;
            startPointerPos.x = e.clientX;
            startPointerPos.y = e.clientY;
        });

        canvas.addEventListener('pointermove', (e) => {
            // If the mouse is not pressed, do nothing
            if (e.buttons === 0) return;
            // Check if the pointer has moved enough to be considered a drag
            if (Math.abs(e.clientX - startPointerPos.x) > 5 || Math.abs(e.clientY - startPointerPos.y) > 5) {
                isDragging = true;
            }
        });

        canvas.addEventListener('pointerup', (e) => {
            if (isDragging) {
                console.log('🎯 Drag detected, ignoring click.');
                return; // It was a drag, not a click, so do nothing.
            }

            // This is a valid click, find out what was clicked on
            const rect = this.renderer.domElement.getBoundingClientRect();
            const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
            const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
            this.raycaster.setFromCamera({ x, y }, this.camera);

            const intersects = this.raycaster.intersectObjects(this.modelGroup.children, true);

            if (intersects.length > 0) {
                // A face was clicked
                if (e.ctrlKey && e.button === 0) {
                    // Ctrl+Left click - edge calculation using EdgeAnalyzer
                    console.log('🎯 [POINTER_UP] Ctrl+Left click on face - calculate edge length with EdgeAnalyzer');
                    this.handleEdgeCalculationWithAnalyzer(e, intersects[0]);
                } else if (e.button === 0 || e.button === 2) { // Left or Right click
                    console.log(`🎯 ${e.button === 0 ? 'Left' : 'Right'} click on face detected.`);
                    this.onModelRightClick(e); // Use the analysis panel for both clicks
                }
            } else {
                // Empty space was clicked
                console.log('🎯 Click on empty space, clearing selection.');
                this.clearFaceSelection();
            }
        });

        // Global listener to close the context menu if a click happens outside of it
        document.addEventListener('click', (e) => {
            if (!canvas.contains(e.target) && !e.target.closest('.json-analysis-panel')) {
                this.hideAnalysisPanel();
                // Also hide edge panel
                const edgePanel = document.getElementById('json-edge-analysis-panel');
                if (edgePanel) {
                    edgePanel.remove();
                }
            }
        }, true); // Use capture phase to be safe

        console.log('✅ Face selection listeners set up successfully');
    }

    toggleWireframe() {
        if (!this.modelGroup) return;

        this.isWireframe = !this.isWireframe;

        // Use traverse() to handle all nested meshes, including nested groups
        this.modelGroup.traverse((child) => {
            if (child.isMesh && child.material) {
                // Handle both array material and single material
                if (Array.isArray(child.material)) {
                    child.material.forEach(mat => {
                        mat.wireframe = this.isWireframe;
                        mat.needsUpdate = true;
                    });
                } else {
                    child.material.wireframe = this.isWireframe;
                    child.material.needsUpdate = true;
                }
            }
        });

        // Update visual state of the button
        const btn = document.getElementById('json-wireframe-toggle-btn');
        if (btn) {
            btn.classList.toggle('btn-active', this.isWireframe);
            btn.title = this.isWireframe ? 'Turn off Wireframe (currently ON)' : 'Turn on Wireframe';
        }

        this.showStatus(this.isWireframe ? 'Wireframe ON' : 'Wireframe OFF', 'success');
        console.log(`🔵 [WIREFRAME] ${this.isWireframe ? 'ON' : 'OFF'}`);
    }

    showColorPicker() {
        const color = prompt('Enter hex color (e.g., #ff0000):');
        if (color && this.modelGroup) {
            this.modelGroup.children.forEach(child => {
                if (child.isMesh && child.material) {
                    child.material.color.setHex(color.replace('#', '0x'));
                }
            });
            this.showStatus('Color changed', 'success');
        }
    }

    takeScreenshot() {
        if (!this.renderer) return;

        const link = document.createElement('a');
        link.download = 'json_model_screenshot.png';
        link.href = this.renderer.domElement.toDataURL();
        link.click();
        this.showStatus('Screenshot saved', 'success');
    }

    // UI control methods to match OBJ viewer interface
    showControls() {
        document.getElementById('json-viewer-controls')?.classList.remove('hidden');
    }

    hideControls() {
        document.getElementById('json-viewer-controls')?.classList.add('hidden');
    }

    showInfo() {
        document.getElementById('json-viewer-info')?.classList.remove('hidden');
    }

    getVerticesForFace(realFaceId) {
        const vertices = [];
        if (!this.jsonData || !this.jsonData.faces || !this.jsonData.faces.bodies) {
            return vertices;
        }

        // Find the correct face within the stored JSON data
        for (const body of this.jsonData.faces.bodies) {
            for (const face of body.faces) {
                if (face.id === realFaceId) {
                    // Collect all vertices from all facets of this face
                    for (const facet of face.facets) {
                        for (const v of facet.vertices) {
                            vertices.push(new THREE.Vector3(v.x, v.y, v.z));
                        }
                    }
                    return vertices; // Face found and vertices collected
                }
            }
        }

        return vertices; // Return empty if face not found
    }

}

// Global functions for backward compatibility and initialization



function initJsonViewer() {
    // This function is called by the DualViewerManager
    // The actual initialization is now handled by the JsonModelViewer3D constructor
    console.log('✅ JSON Viewer initialization function called (compatibility mode)');
}

function loadJsonModel(jsonData) {
    // This function is called by the DualViewerManager
    // Forward to the global instance if it exists



    if (window.jsonViewer && window.jsonViewer.loadJsonData) {
        window.jsonViewer.loadJsonData(jsonData);
    } else {
        console.warn('JSON viewer instance not available');
    }
}

// Event listeners for JSON viewer controls
document.addEventListener('DOMContentLoaded', function () {
    // Load JSON button
    document.getElementById('load-json-btn')?.addEventListener('click', () => {
        document.getElementById('json-file-input').click();
    });

    // JSON file input change
    document.getElementById('json-file-input')?.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file && file.name.toLowerCase().endsWith('.json')) {
            if (window.jsonViewer && window.jsonViewer.loadJsonFile) {
                window.jsonViewer.loadJsonFile(file);
            }
        } else {
            console.error('Please select a JSON file');
        }
    });

    // JSON viewer controls
    document.getElementById('json-reset-camera-btn')?.addEventListener('click', () => {
        if (window.jsonViewer && window.jsonViewer.resetCamera) {
            window.jsonViewer.resetCamera();
        }
    });

    // Wireframe: a single listener, visual feedback is handled inside toggleWireframe()
    document.getElementById('json-wireframe-toggle-btn')?.addEventListener('click', () => {
        if (window.jsonViewer && window.jsonViewer.toggleWireframe) {
            window.jsonViewer.toggleWireframe();
        }
    });

    document.getElementById('json-color-picker-btn')?.addEventListener('click', () => {
        if (window.jsonViewer && window.jsonViewer.showColorPicker) {
            window.jsonViewer.showColorPicker();
        }
    });

    document.getElementById('json-screenshot-btn')?.addEventListener('click', () => {
        if (window.jsonViewer && window.jsonViewer.takeScreenshot) {
            window.jsonViewer.takeScreenshot();
        }
    });

    // json-select-face-btn: activates face selection mode (currently using direct click, this button is just an indicator)
    document.getElementById('json-select-face-btn')?.addEventListener('click', () => {
        console.log('👆 [UI] Face selection is always active — click any face on the model');
        // Show instruction tooltip
        if (window.jsonViewer) {
            window.jsonViewer.showStatus('Click on any face on the model to select it', 'success');
        }
    });

    // Wireframe visual feedback is handled inside toggleWireframe() — no need for a second listener

});

// Global JSON viewer object for external access
window.jsonModelViewer = {
    hideFaceTooltip: () => {
        const tooltip = document.getElementById('json-face-tooltip');
        if (tooltip) tooltip.classList.add('hidden');
    },
    clearSelection: () => {
        if (window.jsonViewer && window.jsonViewer.clearFaceSelection) {
            window.jsonViewer.clearFaceSelection();
        }
    },
    resetView: () => {
        if (window.jsonViewer && window.jsonViewer.resetCamera) {
            window.jsonViewer.resetCamera();
        }
    },
    toggleWireframe: () => {
        if (window.jsonViewer && window.jsonViewer.toggleWireframe) {
            window.jsonViewer.toggleWireframe();
        }
    },
    // Debug functions for troubleshooting
    debugModel: () => {
        if (window.jsonViewer && window.jsonViewer.debugModelState) {
            window.jsonViewer.debugModelState();
        }
    },
    forceRefresh: () => {
        if (window.jsonViewer && window.jsonViewer.forceRefreshModel) {
            window.jsonViewer.forceRefreshModel();
        }
    },
    // 🔴 Debug: Show center dots for Face Orientation debugging
    showCenterDebug: () => {
        if (window.jsonViewer && window.jsonViewer.showDebugCenterDot) {
            window.jsonViewer.showDebugCenterDot();
        } else {
            console.warn('⚠️ No model loaded or showDebugCenterDot not available');
        }
    },
    hideCenterDebug: () => {
        if (window.jsonViewer && window.jsonViewer.hideDebugCenterDot) {
            window.jsonViewer.hideDebugCenterDot();
        }
    }
};
