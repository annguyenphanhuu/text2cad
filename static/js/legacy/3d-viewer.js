/**
 * 3D Model Viewer using Three.js
 * Handles loading and displaying OBJ files in the web interface
 * Fixed to rotate around model center like CAD software
 */

class ModelViewer3D {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.currentModel = null;
        this.isWireframe = false;
        this.modelCenter = new THREE.Vector3(0, 0, 0);
        this.modelBoundingBox = null;
        this.selectedMesh = null;
        this.originalColor = null;
        this.originalModelSize = null; // Store original model size before scaling
        this.modelScaleFactor = 1; // Store the scale factor applied
        this.selectedFaceInfo = null; // Store information about the currently selected face
        this.isSelectFaceMode = false; // Track if we're in face selection mode
        this.isSelectEdgeMode = false; // Track if we're in edge selection mode
        this.highlightEdgeMesh = null; // Store the highlighted edge mesh
        this.selectedEdgeInfo = null; // Store information about selected edge
        this.originalSourceUrl = null; // Store the original source URL of the loaded model

        // Auto-detection algorithms disabled - simple mesh selection only

        this.modelInfo = {
            vertices: 0,
            faces: 0,
            fileName: ''
        };
        this.controlsInfo = {
            left: "Left click: Rotate/Orbit",
            wheel: "Wheel: Zoom in/out",
            middle: "Middle click or Shift+Left: Pan view"
        };

        // Coordinate axis compass properties
        this.compassScene = null;
        this.compassCamera = null;
        this.compassRenderer = null;
        this.compassContainer = null;
        this.axisHelper = null;
        this.lastCameraMatrix = new THREE.Matrix4(); // Track camera changes for optimization
        this.isCompassVisible = false; // Default to hidden

        // Face tooltip
        this.faceTooltip = null;
        this.tooltipTimeout = null;

        // Edge tooltip
        this.edgeTooltip = null;
        this.edgeTooltipTimeout = null;

        this.init();
        this.setupEventListeners();
    }

    init() {
        // Create scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0xf0f0f0);

        // Create camera
        this.camera = new THREE.PerspectiveCamera(
            45,
            this.container.clientWidth / this.container.clientHeight,
            0.1,
            1000
        );
        this.camera.position.set(5, 5, 5);

        // Create renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

        // Add renderer to container
        const viewerElement = this.container.querySelector('.viewer-3d');
        viewerElement.appendChild(this.renderer.domElement);

        // Create controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.1;
        this.controls.rotateSpeed = 0.7;
        this.controls.panSpeed = 0.7;
        this.controls.zoomSpeed = 1.2;
        this.controls.screenSpacePanning = true;
        this.controls.enableZoom = true;
        this.controls.enablePan = true;

        // CAD-like controls configuration
        this.controls.mouseButtons = {
            LEFT: THREE.MOUSE.ROTATE,       // Left click: rotate/orbit
            MIDDLE: THREE.MOUSE.PAN,        // Middle click: pan
            RIGHT: THREE.MOUSE.PAN          // Right click: pan (alternative)
        };

        // Enable Shift + Left click for panning (common CAD behavior)
        this.controls.keyPanSpeed = 10.0;
        this.controls.keys = {
            LEFT: "ArrowLeft",
            UP: "ArrowUp",
            RIGHT: "ArrowRight",
            BOTTOM: "ArrowDown"
        };

        // Add lights
        this.setupLighting();

        // Setup coordinate axis compass
        this.setupCoordinateCompass();

        // Face and edge tooltips removed - using simple mesh selection only

        // Initialize simple mesh selection manager (auto-detection disabled)
        this.initializeSelectionManager();

        // Start render loop
        this.animate();

        // Handle window resize
        window.addEventListener('resize', () => this.onWindowResize());
    }

    /**
     * Initialize unified selection manager
     */
    initializeSelectionManager() {
        // DISABLED: Auto-detection algorithms removed for simple mesh selection
        console.log('🔧 SelectionManager disabled - using simple mesh selection only');

        // Set selectionManager to null to ensure simple triangle selection is used
        this.selectionManager = null;

        // Expose to global for debugging
        window.viewer3D = this;
    }

    /**
     * Debug method to update angle thresholds (DISABLED - simple triangle selection only)
     */
    setAngleThreshold(threshold) {
        console.log(`🔧 Angle threshold setting disabled - simple mesh selection mode active`);
    }

    /**
     * Debug method to check 3D viewer state and dependencies
     */
    checkViewerState() {
        console.log('🔍 3D Viewer State Check (Simple Mesh Selection Mode):');
        console.log('- THREE.js loaded:', typeof THREE !== 'undefined');
        console.log('- THREE.OBJLoader loaded:', typeof THREE !== 'undefined' && typeof THREE.OBJLoader !== 'undefined');
        console.log('- THREE.OrbitControls loaded:', typeof THREE !== 'undefined' && typeof THREE.OrbitControls !== 'undefined');
        console.log('- Scene initialized:', !!this.scene);
        console.log('- Camera initialized:', !!this.camera);
        console.log('- Renderer initialized:', !!this.renderer);
        console.log('- Controls initialized:', !!this.controls);
        console.log('- Current model loaded:', !!this.currentModel);
        console.log('- Auto-detection algorithms: DISABLED');

        return {
            threeJs: typeof THREE !== 'undefined',
            objLoader: typeof THREE !== 'undefined' && typeof THREE.OBJLoader !== 'undefined',
            orbitControls: typeof THREE !== 'undefined' && typeof THREE.OrbitControls !== 'undefined',
            viewerInitialized: !!(this.scene && this.camera && this.renderer && this.controls),
            currentModel: !!this.currentModel,
            simpleMeshMode: true
        };
    }

    /**
     * Clear all highlights from legacy and new systems
     */
    clearAllHighlights() {
        // Clear legacy highlights
        if (this.highlightFaceMesh) {
            this.scene.remove(this.highlightFaceMesh);
            this.highlightFaceMesh = null;
        }

        if (this.highlightEdgeMesh) {
            this.scene.remove(this.highlightEdgeMesh);
            this.highlightEdgeMesh = null;
        }

        if (this.highlightMeshMesh) {
            this.scene.remove(this.highlightMeshMesh);
            this.highlightMeshMesh = null;
        }

        // Auto-detection algorithms disabled - only simple mesh selection used
    }







    setupLighting() {

        const ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
        this.scene.add(ambientLight);

        const directionalLight1 = new THREE.DirectionalLight(0xffffff, 0.5);
        directionalLight1.position.set(10, 10, 10);
        directionalLight1.castShadow = true;
        directionalLight1.shadow.mapSize.width = 2048;
        directionalLight1.shadow.mapSize.height = 2048;
        this.scene.add(directionalLight1);

        const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.35);
        directionalLight2.position.set(-10, 5, -5);
        this.scene.add(directionalLight2);

        const directionalLight3 = new THREE.DirectionalLight(0xffffff, 0.25);
        directionalLight3.position.set(5, -10, -7);
        this.scene.add(directionalLight3);
    }

    setupCoordinateCompass() {
        // Create compass container
        this.compassContainer = document.createElement('div');
        this.compassContainer.id = 'coordinate-compass';
        this.compassContainer.title = 'XYZ Coordinate Compass\nRed: X-axis, Green: Y-axis, Blue: Z-axis\nRotates with 3D model orientation';
        this.compassContainer.style.cssText = `
            position: absolute;
            bottom: 10px;
            right: 20px;
            width: 120px;
            height: 120px;
            z-index: 1000;
            pointer-events: auto;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.95);
            border: 2px solid rgba(0, 0, 0, 0.1);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(10px);
            cursor: help;
            transition: all 0.2s ease;
        `;

        // Add hover effect
        this.compassContainer.addEventListener('mouseenter', () => {
            this.compassContainer.style.transform = 'scale(1.05)';
            this.compassContainer.style.boxShadow = '0 8px 25px rgba(0, 0, 0, 0.3)';
        });

        this.compassContainer.addEventListener('mouseleave', () => {
            this.compassContainer.style.transform = 'scale(1)';
            this.compassContainer.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.2)';
        });

        // Add compass to the viewer container
        const viewerElement = this.container.querySelector('.viewer-3d');
        viewerElement.appendChild(this.compassContainer);

        // Initially hide the compass
        this.compassContainer.style.display = 'none';

        // Add compass label
        const compassLabel = document.createElement('div');
        compassLabel.textContent = 'XYZ';
        compassLabel.style.cssText = `
            position: absolute;
            bottom: -5px;
            right: 45px;
            font-size: 10px;
            font-weight: bold;
            color: rgba(0, 0, 0, 0.6);
            background: rgba(255, 255, 255, 0.8);
            padding: 2px 4px;
            border-radius: 3px;
            pointer-events: none;
        `;
        this.compassContainer.appendChild(compassLabel);

        // Create compass scene
        this.compassScene = new THREE.Scene();
        this.compassScene.background = null; // Transparent background

        // Add lighting to compass scene
        const compassAmbientLight = new THREE.AmbientLight(0xffffff, 0.6);
        this.compassScene.add(compassAmbientLight);

        const compassDirectionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        compassDirectionalLight.position.set(1, 1, 1);
        this.compassScene.add(compassDirectionalLight);

        // Create compass camera
        this.compassCamera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
        this.compassCamera.position.set(0, 0, 3.5);
        this.compassCamera.lookAt(0, 0, 0); // Look at center

        // Create compass renderer
        this.compassRenderer = new THREE.WebGLRenderer({
            antialias: true,
            alpha: true // Enable transparency
        });
        this.compassRenderer.setSize(120, 120);
        this.compassRenderer.setClearColor(0x000000, 0); // Transparent background
        this.compassContainer.appendChild(this.compassRenderer.domElement);

        // Create coordinate axes
        this.createCoordinateAxes();
    }

    createCoordinateAxes() {
        // Create axis group
        this.axisHelper = new THREE.Group();

        // Axis parameters - shorter axes to fit better in compass
        const axisLength = 1.0;
        const arrowLength = 0.2;
        const arrowRadius = 0.05;
        const axisRadius = 0.02;

        // X-axis (Red) - from center to positive X
        const xAxisGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const xAxisMaterial = new THREE.MeshBasicMaterial({ color: 0xff3333 });
        const xAxis = new THREE.Mesh(xAxisGeometry, xAxisMaterial);
        xAxis.rotation.z = -Math.PI / 2;
        xAxis.position.x = axisLength / 2;
        this.axisHelper.add(xAxis);

        // X-axis arrow
        const xArrowGeometry = new THREE.ConeGeometry(arrowRadius, arrowLength, 8);
        const xArrow = new THREE.Mesh(xArrowGeometry, xAxisMaterial);
        xArrow.rotation.z = -Math.PI / 2;
        xArrow.position.x = axisLength + arrowLength / 2;
        this.axisHelper.add(xArrow);

        // Y-axis (Green) - from center to positive Y
        const yAxisGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const yAxisMaterial = new THREE.MeshBasicMaterial({ color: 0x33ff33 });
        const yAxis = new THREE.Mesh(yAxisGeometry, yAxisMaterial);
        yAxis.position.y = axisLength / 2;
        this.axisHelper.add(yAxis);

        // Y-axis arrow
        const yArrowGeometry = new THREE.ConeGeometry(arrowRadius, arrowLength, 8);
        const yArrow = new THREE.Mesh(yArrowGeometry, yAxisMaterial);
        yArrow.position.y = axisLength + arrowLength / 2;
        this.axisHelper.add(yArrow);

        // Z-axis (Blue) - from center to positive Z
        const zAxisGeometry = new THREE.CylinderGeometry(axisRadius, axisRadius, axisLength, 8);
        const zAxisMaterial = new THREE.MeshBasicMaterial({ color: 0x3333ff });
        const zAxis = new THREE.Mesh(zAxisGeometry, zAxisMaterial);
        zAxis.rotation.x = Math.PI / 2;
        zAxis.position.z = axisLength / 2;
        this.axisHelper.add(zAxis);

        // Z-axis arrow
        const zArrowGeometry = new THREE.ConeGeometry(arrowRadius, arrowLength, 8);
        const zArrow = new THREE.Mesh(zArrowGeometry, zAxisMaterial);
        zArrow.rotation.x = Math.PI / 2;
        zArrow.position.z = axisLength + arrowLength / 2;
        this.axisHelper.add(zArrow);

        // Add center sphere to show origin
        const centerGeometry = new THREE.SphereGeometry(0.04, 8, 8);
        const centerMaterial = new THREE.MeshBasicMaterial({ color: 0x666666 });
        const centerSphere = new THREE.Mesh(centerGeometry, centerMaterial);
        this.axisHelper.add(centerSphere);

        // Add axis labels
        this.addAxisLabels();

        // Add to compass scene
        this.compassScene.add(this.axisHelper);
    }

    addAxisLabels() {
        // Function to create text texture - each label gets its own canvas
        const createTextTexture = (text, color) => {
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            canvas.width = 64;
            canvas.height = 64;

            context.clearRect(0, 0, 64, 64);
            context.font = 'Bold 28px Arial';
            context.fillStyle = color;
            context.textAlign = 'center';
            context.textBaseline = 'middle';
            context.fillText(text, 32, 32);

            const texture = new THREE.CanvasTexture(canvas);
            texture.needsUpdate = true;
            return texture;
        };

        // Calculate label positions based on axis length + arrow length
        const labelDistance = 1.0 + 0.2 + 0.25; // axisLength + arrowLength + offset

        // X label (Red)
        const xTexture = createTextTexture('X', '#ff3333');
        const xLabelMaterial = new THREE.SpriteMaterial({ map: xTexture });
        const xLabel = new THREE.Sprite(xLabelMaterial);
        xLabel.position.set(labelDistance, 0, 0);
        xLabel.scale.set(0.4, 0.4, 1);
        this.axisHelper.add(xLabel);

        // Y label (Green)
        const yTexture = createTextTexture('Y', '#33ff33');
        const yLabelMaterial = new THREE.SpriteMaterial({ map: yTexture });
        const yLabel = new THREE.Sprite(yLabelMaterial);
        yLabel.position.set(0, labelDistance, 0);
        yLabel.scale.set(0.4, 0.4, 1);
        this.axisHelper.add(yLabel);

        // Z label (Blue)
        const zTexture = createTextTexture('Z', '#3333ff');
        const zLabelMaterial = new THREE.SpriteMaterial({ map: zTexture });
        const zLabel = new THREE.Sprite(zLabelMaterial);
        zLabel.position.set(0, 0, labelDistance);
        zLabel.scale.set(0.4, 0.4, 1);
        this.axisHelper.add(zLabel);
    }

    setupEventListeners() {
        // Reset camera button
        document.getElementById('reset-camera-btn')?.addEventListener('click', () => {
            this.resetCamera();
        });

        // Wireframe toggle button
        document.getElementById('wireframe-toggle-btn')?.addEventListener('click', () => {
            this.toggleWireframe();
        });

        // Download OBJ button
        document.getElementById('download-obj-btn')?.addEventListener('click', () => {
            this.downloadCurrentModel();
        });

        // Fullscreen button
        document.getElementById('fullscreen-viewer-btn')?.addEventListener('click', () => {
            this.toggleFullscreen();
        });

        // Code toggle button
        document.getElementById('toggle-code-btn')?.addEventListener('click', () => {
            this.toggleCodeOutput();
        });

        // Select Edge button - integrated with Ana_Geo system
        document.getElementById('select-edge-btn')?.addEventListener('click', () => {
            // Ensure Ana_Geo is ready
            if (window.ensureAnaGeoReady && window.ensureAnaGeoReady()) {
                if (window.anaGeo && window.anaGeo.toggleEdgeMode) {
                    window.anaGeo.toggleEdgeMode();
                } else {
                    console.warn('Ana_Geo system initializing, please try again in a moment');
                }
            } else {
                console.warn('Ana_Geo system not available for edge selection');
            }
        });

        // Select Face button - integrated with Ana_Geo system
        document.getElementById('select-face-btn')?.addEventListener('click', () => {
            // Ensure Ana_Geo is ready
            if (window.ensureAnaGeoReady && window.ensureAnaGeoReady()) {
                if (window.anaGeo && window.anaGeo.toggleFaceMode) {
                    window.anaGeo.toggleFaceMode();
                } else {
                    console.warn('Ana_Geo system initializing, please try again in a moment');
                }
            } else {
                console.warn('Ana_Geo system not available for face selection');
            }
        });

        // Legacy Select Curved Surface button - functionality moved to automatic detection
        document.getElementById('select-curved-surface-btn')?.addEventListener('click', () => {
            console.log('Legacy curved surface button clicked - curved surfaces now detected automatically');
            // Curved surface selection is now handled automatically by SelectionManager
        });

        // Toggle Compass button
        document.getElementById('toggle-compass-btn')?.addEventListener('click', () => {
            this.toggleCompass();
        });

        const viewerElement = this.container.querySelector('.viewer-3d');
        if (viewerElement) {
            viewerElement.addEventListener('pointerdown', this.onModelClick.bind(this));
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', (event) => {
            // Ctrl+E for edge selection
            if (event.ctrlKey && event.key === 'e') {
                event.preventDefault();
                // Ensure Ana_Geo is ready
                if (window.ensureAnaGeoReady && window.ensureAnaGeoReady()) {
                    if (window.anaGeo && window.anaGeo.toggleEdgeMode) {
                        window.anaGeo.toggleEdgeMode();
                    }
                }
            }

            // Ctrl+F for face selection
            if (event.ctrlKey && event.key === 'f') {
                event.preventDefault();
                // Ensure Ana_Geo is ready
                if (window.ensureAnaGeoReady && window.ensureAnaGeoReady()) {
                    if (window.anaGeo && window.anaGeo.toggleFaceMode) {
                        window.anaGeo.toggleFaceMode();
                    }
                }
            }
        });

    }

    async loadOBJFile(objUrl, fileName = '') {
        try {
            console.log('[3D-VIEWER] Starting loadOBJFile with:', { objUrl, fileName });

            // Check THREE.js dependencies first
            if (typeof THREE === 'undefined') {
                console.error('❌ THREE.js is not loaded');
                this.showError('THREE.js library not loaded');
                return null;
            }

            if (typeof THREE.OBJLoader === 'undefined') {
                console.error('❌ THREE.OBJLoader is not loaded');
                this.showError('OBJ Loader not available');
                return null;
            }

            // Store the original source URL
            this.originalSourceUrl = objUrl;

            this.showLoading(true);
            this.hideControls();

            // Clear all highlights first to prevent conflicts
            this.clearAllHighlights();

            // Clear Ana_Geo selections if available
            if (window.anaGeo) {
                window.anaGeo.clearEdgeSelection();
                window.anaGeo.clearFaceSelection();
            }

            // Remove and dispose previous model properly
            if (this.currentModel) {
                console.log('[3D-VIEWER] Removing and disposing previous model from scene');
                this.disposeModel(this.currentModel);
                this.scene.remove(this.currentModel);
                this.currentModel = null;
            }

            // Clear renderer to prevent visual artifacts
            if (this.renderer) {
                this.renderer.clear();
                console.log('[3D-VIEWER] Renderer cleared');
            }

            // Validate URL
            if (!objUrl) {
                console.error('[3D-VIEWER] No URL provided');
                this.hideLoading();
                this.showError('Invalid OBJ URL');
                return null;
            }

            // ═══════════════════════════════════════════════════════════
            // OPTIMIZED: Removed unnecessary HEAD request
            // THREE.OBJLoader will handle file loading and errors directly
            // This eliminates duplicate API calls (HEAD + GET → only GET via loader)
            // ═══════════════════════════════════════════════════════════
            
            // Prepare URL - make sure it doesn't have double slashes
            let finalUrl = objUrl;
            if (finalUrl.startsWith('/api/3d-viewer/')) {
                // URL is already properly formatted
            } else if (finalUrl.startsWith('/')) {
                finalUrl = `/api/3d-viewer${finalUrl}`;
            } else {
                finalUrl = `/api/3d-viewer/${finalUrl}`;
            }

            console.log(`[3D-VIEWER] Loading OBJ directly (no pre-check): ${finalUrl}`);

            // Load OBJ file directly - THREE.OBJLoader handles errors internally
            const loader = new THREE.OBJLoader();
            console.log(`[3D-VIEWER] Starting OBJ load with THREE.OBJLoader: ${finalUrl}`);

            return new Promise((resolve, reject) => {
                // Try loading with main URL first
                const tryLoad = (urlToLoad, isFallback = false) => {
                    loader.load(
                        urlToLoad,
                        (object) => {
                        console.log(`[3D-VIEWER] OBJ loaded successfully from: ${urlToLoad}`);

                        try {
                            // Validate the loaded object
                            if (!object) {
                                throw new Error('Loaded object is null or undefined');
                            }

                            console.log('[3D-VIEWER] Object validation passed, processing...');
                            this.currentModel = object;

                            // Apply default material with debug logging
                            let meshCount = 0;
                            object.traverse((child) => {
                                if (child.isMesh) {
                                    meshCount++;

                                    // Debug: Check if mesh has geometry
                                    if (!child.geometry) {
                                        console.warn(`[3D-VIEWER] Mesh ${meshCount} has no geometry!`);
                                        return;
                                    }

                                    // Create material with explicit visibility settings
                                    child.material = new THREE.MeshLambertMaterial({
                                        color: 0x888888,
                                        side: THREE.DoubleSide,
                                        transparent: false,
                                        opacity: 1.0,
                                        visible: true
                                    });

                                    child.castShadow = true;
                                    child.receiveShadow = true;
                                    child.visible = true; // Ensure mesh is visible

                                    console.log(`[3D-VIEWER] Mesh ${meshCount}: geometry=${!!child.geometry}, material=${!!child.material}, visible=${child.visible}`);
                                }
                            });

                            console.log(`[3D-VIEWER] Applied materials to ${meshCount} meshes`);

                            // Add to scene
                            this.scene.add(object);
                            console.log('[3D-VIEWER] Object added to scene');

                            // Debug: Check scene contents
                            console.log('[3D-VIEWER] Scene children count:', this.scene.children.length);
                            this.scene.children.forEach((child, index) => {
                                console.log(`[3D-VIEWER] Scene child ${index}:`, {
                                    type: child.type,
                                    name: child.name,
                                    visible: child.visible,
                                    position: child.position?.clone(),
                                    scale: child.scale?.clone()
                                });
                            });

                            // Center and scale the model - FIXED VERSION
                            console.log('[3D-VIEWER] Model before centering:', {
                                position: object.position.clone(),
                                scale: object.scale.clone(),
                                visible: object.visible
                            });

                            this.centerAndScaleModel(object);

                            console.log('[3D-VIEWER] Model after centering:', {
                                position: object.position.clone(),
                                scale: object.scale.clone(),
                                visible: object.visible
                            });
                            console.log('[3D-VIEWER] Model centered and scaled');

                            // Update model info
                            this.updateModelInfo(object, fileName);
                            console.log('[3D-VIEWER] Model info updated');

                            // Show controls and info
                            this.showControls();
                            this.showInfo();
                            this.hideLoading();
                            console.log('[3D-VIEWER] UI updated');

                            // Update control info with correct orbit center
                            this.updateControlsInfoDisplay();

                            console.log('[3D-VIEWER] ✅ Model fully processed and added to scene');

                            // Initialize Ana_Geo system now that OBJ is loaded
                            if (typeof window.initAnaGeoWhenReady === 'function') {
                                console.log('[3D-VIEWER] 🚀 Initializing Ana_Geo after successful OBJ load...');
                                window.initAnaGeoWhenReady();
                            } else {
                                console.log('[3D-VIEWER] ℹ️ Ana_Geo initialization function not available');
                            }

                            resolve(object);

                        } catch (processingError) {
                            console.error('[3D-VIEWER] ❌ Error processing loaded object:', processingError);
                            this.hideLoading();
                            this.showError(`Error processing 3D model: ${processingError.message}`);
                            reject(processingError);
                        }
                    },
                    (progress) => {
                        const percent = progress.loaded / progress.total * 100;
                        console.log(`[3D-VIEWER] Loading progress: ${percent.toFixed(1)}%`);
                    },
                        (error) => {
                            console.error(`[3D-VIEWER] Error loading OBJ from ${urlToLoad}:`, error);
                            
                            // If main URL failed and we haven't tried fallbacks, try them
                            if (!isFallback && fileName) {
                                console.log('[3D-VIEWER] Main URL failed, trying fallback paths...');
                                const todayDate = new Date().toISOString().slice(0, 10).replace(/-/g, '-');
                                const fallbackPaths = [
                                    `/api/3d-viewer/outputs/obj/${todayDate}/${fileName}`,
                                    `/api/3d-viewer/outputs/obj/latest/${fileName}`,
                                    `/api/3d-viewer/download/outputs/obj/${todayDate}/${fileName}`
                                ];

                                let fallbackTried = false;
                                for (const fallbackPath of fallbackPaths) {
                                    console.log(`[3D-VIEWER] Trying fallback: ${fallbackPath}`);
                                    tryLoad(fallbackPath, true);
                                    fallbackTried = true;
                                    break; // Try first fallback only
                                }

                                if (!fallbackTried) {
                                    // No fallbacks available, show error
                                    this.hideLoading();
                                    this.showError(`Failed to load 3D model: ${error.message || 'File not found'}`);
                                    reject(error);
                                }
                            } else {
                                // Fallback also failed or no fallbacks, show error
                                this.hideLoading();
                                this.showError(`Failed to load 3D model: ${error.message || 'Unknown error'}`);
                                reject(error);
                            }
                        }
                    );
                };

                // Start loading with main URL
                tryLoad(finalUrl, false);
            });
        } catch (error) {
            console.error('[3D-VIEWER] Error in loadOBJFile:', error);
            this.hideLoading();
            this.showError(`Error: ${error.message || 'Unknown error'}`);
            throw error;
        }
    }

    centerAndScaleModel(object) {
        // Calculate bounding box BEFORE any transformations
        const box = new THREE.Box3().setFromObject(object);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());

        console.log('[3D-VIEWER] Original bounding box:', {
            min: box.min.clone(),
            max: box.max.clone(),
            center: center.clone(),
            size: size.clone()
        });

        // Store the ORIGINAL model size and center for later calculations
        this.originalModelSize = size.clone();
        this.modelCenter = center.clone();
        this.modelBoundingBox = box.clone();

        // Move model so its center is at origin
        console.log('[3D-VIEWER] Moving model center from', center.clone(), 'to origin');
        object.position.sub(center);
        console.log('[3D-VIEWER] Model position after centering:', object.position.clone());

        // Adjust Y position so model sits on the grid
        const minY = box.min.y - center.y; // Relative to new position
        console.log('[3D-VIEWER] MinY relative to new position:', minY);

        if (minY < 0) {
            const yAdjustment = -minY + 0.01;
            console.log('[3D-VIEWER] Adjusting Y position by:', yAdjustment);
            object.position.y -= minY - 0.01; // Lift slightly above
            // Update model center to reflect this adjustment
            this.modelCenter.y += (-minY + 0.01);
        }

        console.log('[3D-VIEWER] Final position before scaling:', object.position.clone());

        // Scale the model to fit nicely in view
        const maxDim = Math.max(size.x, size.y, size.z);
        const scale = 5 / maxDim;
        console.log('[3D-VIEWER] Calculated scale factor:', scale, 'for maxDim:', maxDim);

        this.modelScaleFactor = scale; // Store the scale factor
        object.scale.setScalar(scale);
        console.log('[3D-VIEWER] Applied scale:', object.scale.clone());

        // Scale the model center accordingly
        this.modelCenter.multiplyScalar(scale);

        // After positioning and scaling, the visual center is at (0, y_offset, 0)
        // Set orbit controls to rotate around the visual center of the scaled model
        const visualCenter = new THREE.Vector3(0, object.position.y + (size.y * scale) / 2, 0);
        console.log('[3D-VIEWER] Setting orbit target to:', visualCenter.clone());
        this.controls.target.copy(visualCenter);

        // Position camera for good initial view
        console.log('[3D-VIEWER] Resetting camera...');
        this.resetCamera();

        // Force controls update
        this.controls.update();

        console.log('[3D-VIEWER] Final model state:', {
            position: object.position.clone(),
            scale: object.scale.clone(),
            visible: object.visible,
            orbitTarget: this.controls.target.clone()
        });
    }

    updateModelInfo(object, fileName) {
        let vertices = 0;
        let triangleFaces = 0;
        let actualFaces = 0;

        object.traverse((child) => {
            if (child.isMesh && child.geometry) {
                if (child.geometry.attributes.position) {
                    vertices += child.geometry.attributes.position.count;
                }

                // Count triangular faces
                let childTriangleFaces = 0;
                if (child.geometry.index) {
                    childTriangleFaces = child.geometry.index.count / 3;
                } else if (child.geometry.attributes.position) {
                    childTriangleFaces = child.geometry.attributes.position.count / 3;
                }
                triangleFaces += childTriangleFaces;

                // Actual faces calculation removed - using simple mesh selection only
                actualFaces += childTriangleFaces; // Use triangle count as face count for simplicity
            }
        });

        this.modelInfo = {
            vertices: Math.floor(vertices),
            faces: Math.floor(triangleFaces), // Keep triangle count for reference
            actualFaces: Math.floor(actualFaces), // Real face count
            fileName: fileName || 'model.obj'
        };

        this.updateInfoDisplay();
    }

    // calculateActualFaces() removed - not needed for group-based OBJ



    // findActualFace() removed - not needed for group-based OBJ

    // createFaceHighlight() removed - not needed for group-based OBJ

    createMeshHighlight(intersection, object) {
        // Clone the entire mesh geometry for highlighting
        const geometry = object.geometry.clone();

        // Create highlight material with blue color for mesh highlight
        const highlightMaterial = new THREE.MeshBasicMaterial({
            color: 0x0088ff, // Blue color for mesh highlight
            side: THREE.DoubleSide,
            transparent: true,
            opacity: 0.3, // Lower opacity to see through the highlight
            depthTest: false
        });

        // Create mesh for the highlighted entire mesh
        this.highlightMeshMesh = new THREE.Mesh(geometry, highlightMaterial);

        // Copy the object's transformation
        this.highlightMeshMesh.position.copy(object.position);
        this.highlightMeshMesh.rotation.copy(object.rotation);
        this.highlightMeshMesh.scale.copy(object.scale);

        // Apply object's transformation matrix
        this.highlightMeshMesh.applyMatrix4(object.matrixWorld);

        // Add small offset to prevent z-fighting
        const face = intersection.face;
        const normal = face.normal.clone().transformDirection(object.matrixWorld);
        normal.multiplyScalar(0.01); // Slightly larger offset for mesh highlight
        this.highlightMeshMesh.position.add(normal);

        // Add to scene
        this.scene.add(this.highlightMeshMesh);
    }

    getMeshTriangleCount(object) {
        const geometry = object.geometry;
        if (!geometry) return 0;

        if (geometry.index) {
            // Indexed geometry
            return geometry.index.count / 3;
        } else {
            // Non-indexed geometry
            const positions = geometry.attributes.position;
            return positions ? positions.count / 3 : 0;
        }
    }

    getMeshVertexCount(object) {
        const geometry = object.geometry;
        if (!geometry) return 0;

        const positions = geometry.attributes.position;
        return positions ? positions.count : 0;
    }

    // calculateFaceBounds() and calculateFaceArea() removed - not needed for group-based OBJ

    // calculateOriginalFaceDimensions() removed - not needed for group-based OBJ

    calculateOriginalFaceBounds(triangles) {
        // Calculate original face bounds directly from geometry (no scaling applied)
        const faceBounds = new THREE.Box3();

        // Calculate bounds from triangle vertices in their original geometry space
        triangles.forEach(triangle => {
            triangle.vertices.forEach(vertex => {
                faceBounds.expandByPoint(vertex);
            });
        });

        // Use geometry bounds directly - these are the original model bounds
        // The triangle vertices from geometry.attributes.position are in the original
        // unscaled space as loaded from the OBJ file
        const originalMinX = faceBounds.min.x;
        const originalMaxX = faceBounds.max.x;
        const originalMinY = faceBounds.min.y;
        const originalMaxY = faceBounds.max.y;
        const originalMinZ = faceBounds.min.z;
        const originalMaxZ = faceBounds.max.z;

        const sizeX = originalMaxX - originalMinX;
        const sizeY = originalMaxY - originalMinY;
        const sizeZ = originalMaxZ - originalMinZ;

        // For debugging: log the bounds calculation
        console.log('Original Face bounds calculation:', {
            geometryBounds: {
                min: { x: faceBounds.min.x, y: faceBounds.min.y, z: faceBounds.min.z },
                max: { x: faceBounds.max.x, y: faceBounds.max.y, z: faceBounds.max.z }
            },
            originalBounds: {
                min: { x: originalMinX, y: originalMinY, z: originalMinZ },
                max: { x: originalMaxX, y: originalMaxY, z: originalMaxZ }
            },
            originalSizes: { x: sizeX, y: sizeY, z: sizeZ }
        });

        return {
            minX: originalMinX,
            maxX: originalMaxX,
            minY: originalMinY,
            maxY: originalMaxY,
            minZ: originalMinZ,
            maxZ: originalMaxZ,
            sizeX: sizeX,
            sizeY: sizeY,
            sizeZ: sizeZ
        };
    }

    updateInfoDisplay() {
        const infoElement = document.getElementById('model-info-text');
        if (infoElement) {
            this.originalInfoContent = `
                <div class="occ-style-info">
                    <strong>${this.modelInfo.fileName}</strong>
                    <table class="mt-1 text-xs w-full">
                        <tr>
                            <td class="pr-2">Vertices:</td>
                            <td>${this.modelInfo.vertices.toLocaleString()}</td>
                        </tr>
                        <tr>
                            <td class="pr-2">Faces:</td>
                            <td>${this.modelInfo.actualFaces ? this.modelInfo.actualFaces.toLocaleString() : this.modelInfo.faces.toLocaleString()}</td>
                        </tr>
                        <tr>
                            <td class="pr-2">Triangles:</td>
                            <td class="text-gray-500">${this.modelInfo.faces.toLocaleString()}</td>
                        </tr>
                    </table>

                </div>
            `;

            infoElement.innerHTML = this.originalInfoContent;
        }
    }

    resetCamera() {
        if (this.currentModel) {
            // Get the current bounding box of the positioned and scaled model
            const box = new THREE.Box3().setFromObject(this.currentModel);
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
            this.camera.position.set(15, 10, 15);
            this.camera.lookAt(0, 0, 0);
            this.controls.target.set(0, 0, 0);
        }

        this.controls.update();
    }

    toggleWireframe() {
        if (!this.currentModel) return;

        this.isWireframe = !this.isWireframe;

        this.currentModel.traverse((child) => {
            if (child.isMesh) {
                child.material.wireframe = this.isWireframe;
            }
        });

        // Update button appearance
        const btn = document.getElementById('wireframe-toggle-btn');
        if (btn) {
            btn.style.background = this.isWireframe ? 'rgba(59, 130, 246, 0.9)' : 'rgba(255, 255, 255, 0.9)';
            btn.style.color = this.isWireframe ? 'white' : '#374151';
        }
    }

    toggleCompass() {
        this.isCompassVisible = !this.isCompassVisible;

        if (this.compassContainer) {
            this.compassContainer.style.display = this.isCompassVisible ? 'block' : 'none';
        }

        // Update button appearance
        const btn = document.getElementById('toggle-compass-btn');
        if (btn) {
            btn.style.background = this.isCompassVisible ? 'rgba(34, 197, 94, 0.9)' : 'rgba(255, 255, 255, 0.9)';
            btn.style.color = this.isCompassVisible ? 'white' : '#374151';

            // Update button text/icon
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = this.isCompassVisible ? 'fas fa-compass' : 'far fa-compass';
            }
        }
    }

    downloadCurrentModel() {
        // Download the originally loaded OBJ file
        if (this.originalSourceUrl) {
            const link = document.createElement('a');
            link.href = this.originalSourceUrl;
            link.download = this.modelInfo.fileName || 'model.obj';
            link.click();
            console.log(`Downloading model from original source: ${this.originalSourceUrl}`);
        } else if (this.modelInfo.fileName) {
            // Fallback to previous behavior if originalSourceUrl is not available
            console.warn('Original source URL not available, using fallback path');
            const downloadUrl = `/download/outputs/obj/${this.modelInfo.fileName}`;
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = this.modelInfo.fileName;
            link.click();
        } else {
            console.error('No model filename available for download');
        }
    }

    toggleFullscreen() {
        // Implementation for fullscreen mode
        const container = this.container;
        if (!document.fullscreenElement) {
            container.requestFullscreen().then(() => {
                this.onWindowResize();
            });
        } else {
            document.exitFullscreen();
        }
    }

    toggleCodeOutput() {
        const container = document.getElementById('code-output-container');
        const icon = document.querySelector('#toggle-code-btn i.fa-chevron-down');

        if (container.classList.contains('hidden')) {
            container.classList.remove('hidden');
            icon.style.transform = 'rotate(180deg)';
        } else {
            container.classList.add('hidden');
            icon.style.transform = 'rotate(0deg)';
        }
    }

    showLoading(show = true) {
        const loading = document.getElementById('viewer-loading');
        const placeholder = this.container.querySelector('.viewer-placeholder');

        if (show) {
            loading?.classList.remove('hidden');
            placeholder?.classList.add('hidden');
        } else {
            loading?.classList.add('hidden');
        }
    }

    hideLoading() {
        this.showLoading(false);
    }

    showControls() {
        console.log('Showing controls...');
        console.log('Showing controls...');
        document.getElementById('viewer-controls')?.classList.remove('hidden');
        document.getElementById('fullscreen-viewer-btn')?.classList.remove('hidden');
        document.getElementById('select-face-btn')?.classList.remove('hidden');
        document.getElementById('select-edge-btn')?.classList.remove('hidden');

        // Debug curved surface button
        const curvedBtn = document.getElementById('select-curved-surface-btn');
        console.log('Curved surface button:', curvedBtn);
        curvedBtn?.classList.remove('hidden');

        document.getElementById('toggle-compass-btn')?.classList.remove('hidden');
    }

    hideControls() {
        document.getElementById('viewer-controls')?.classList.add('hidden');
        document.getElementById('fullscreen-viewer-btn')?.classList.add('hidden');
        document.getElementById('select-face-btn')?.classList.add('hidden');
        document.getElementById('select-edge-btn')?.classList.add('hidden');
        document.getElementById('select-curved-surface-btn')?.classList.add('hidden');
        document.getElementById('select-curved-surface-btn')?.classList.add('hidden');
        document.getElementById('toggle-compass-btn')?.classList.add('hidden');

        // Reset select face mode when hiding controls
        if (this.isSelectFaceMode) {
            this.isSelectFaceMode = false;
            document.getElementById('select-face-btn')?.classList.remove('select-face-active');
        }

        // Reset select edge mode when hiding controls
        if (this.isSelectEdgeMode) {
            this.isSelectEdgeMode = false;
            document.getElementById('select-edge-btn')?.classList.remove('select-edge-active');
        }

        // Reset select curved surface mode when hiding controls
        if (this.isSelectCurvedSurfaceMode) {
            this.isSelectCurvedSurfaceMode = false;
            document.getElementById('select-curved-surface-btn')?.classList.remove('select-curved-surface-active');
        }

        // Reset select curved surface mode when hiding controls
        if (this.isSelectCurvedSurfaceMode) {
            this.isSelectCurvedSurfaceMode = false;
            document.getElementById('select-curved-surface-btn')?.classList.remove('select-curved-surface-active');
        }

        // Hide compass when controls are hidden
        if (this.isCompassVisible) {
            this.isCompassVisible = false;
            if (this.compassContainer) {
                this.compassContainer.style.display = 'none';
            }
        }
    }

    showInfo() {
        document.getElementById('viewer-info')?.classList.remove('hidden');
    }

    showError(message) {
        console.error(`[3D-VIEWER] Error: ${message}`);
        const viewerElement = this.container.querySelector('.viewer-3d');
        const placeholder = viewerElement.querySelector('.viewer-placeholder');

        if (placeholder) {
            placeholder.innerHTML = `
                <i class="fas fa-exclamation-triangle text-red-500"></i>
                <p class="text-sm text-red-100">${message}</p>
                <p class="text-xs opacity-75 mt-1">Check console for details</p>
            `;
            placeholder.classList.remove('hidden');
        }

        this.hideLoading();

        // Show a notification if possible
        if (window.showNotification) {
            window.showNotification(message, 'error', 5000);
        }
    }

    onWindowResize() {
        if (!this.camera || !this.renderer) return;

        this.camera.aspect = this.container.clientWidth / this.container.clientHeight;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);

        // Compass doesn't need resizing as it has fixed dimensions
        // but we ensure it stays in the correct position
        if (this.compassRenderer) {
            this.compassRenderer.setSize(120, 120);
        }
    }

    animate() {
        requestAnimationFrame(() => this.animate());

        if (this.controls) {
            this.controls.update();
        }

        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }

        // Update and render coordinate compass
        this.updateCoordinateCompass();
    }

    updateCoordinateCompass() {
        if (!this.compassRenderer || !this.compassScene || !this.compassCamera || !this.axisHelper || !this.isCompassVisible) {
            return;
        }

        // Check if camera has changed to optimize performance
        const currentCameraMatrix = new THREE.Matrix4();
        currentCameraMatrix.extractRotation(this.camera.matrixWorld);

        // Only update if camera rotation has changed
        if (!currentCameraMatrix.equals(this.lastCameraMatrix)) {
            // Synchronize compass orientation with main camera
            // The compass should show the world coordinate system orientation
            // relative to the current camera view

            // Create the inverse rotation matrix to show world axes relative to camera
            const inverseMatrix = new THREE.Matrix4();
            inverseMatrix.copy(currentCameraMatrix).invert();

            // Apply the inverse rotation to the axis helper
            this.axisHelper.rotation.setFromRotationMatrix(inverseMatrix);

            // Store current matrix for next comparison
            this.lastCameraMatrix.copy(currentCameraMatrix);
        }

        // Always render the compass (it's lightweight)
        this.compassRenderer.render(this.compassScene, this.compassCamera);
    }

    cleanupCompass() {
        if (this.compassContainer && this.compassContainer.parentNode) {
            this.compassContainer.parentNode.removeChild(this.compassContainer);
        }

        if (this.compassRenderer) {
            this.compassRenderer.dispose();
        }

        if (this.compassScene) {
            // Clean up compass scene objects
            while (this.compassScene.children.length > 0) {
                const child = this.compassScene.children[0];
                this.compassScene.remove(child);
                if (child.geometry) child.geometry.dispose();
                if (child.material) {
                    if (Array.isArray(child.material)) {
                        child.material.forEach(material => material.dispose());
                    } else {
                        child.material.dispose();
                    }
                }
            }
        }

        this.compassScene = null;
        this.compassCamera = null;
        this.compassRenderer = null;
        this.compassContainer = null;
        this.axisHelper = null;
    }

    // Public method to load a model from URL
    async loadModel(objUrl, fileName) {
        try {
            // Store the original source URL
            this.originalSourceUrl = objUrl;

            await this.loadOBJFile(objUrl, fileName);
            return true;
        } catch (error) {
            console.error('[3D-VIEWER] Failed to load model:', error);
            this.showError(`Error: ${error.message || 'Unknown error'}`);
            return false;
        }
    }

    /**
     * Properly dispose of a 3D model to prevent memory leaks
     */
    disposeModel(model) {
        if (!model) return;

        console.log('[3D-VIEWER] Disposing model to prevent memory leaks');

        model.traverse((child) => {
            if (child.isMesh) {
                // Dispose geometry
                if (child.geometry) {
                    child.geometry.dispose();
                }

                // Dispose material(s)
                if (child.material) {
                    if (Array.isArray(child.material)) {
                        child.material.forEach(material => {
                            if (material.map) material.map.dispose();
                            if (material.lightMap) material.lightMap.dispose();
                            if (material.bumpMap) material.bumpMap.dispose();
                            if (material.normalMap) material.normalMap.dispose();
                            if (material.specularMap) material.specularMap.dispose();
                            if (material.envMap) material.envMap.dispose();
                            material.dispose();
                        });
                    } else {
                        if (child.material.map) child.material.map.dispose();
                        if (child.material.lightMap) child.material.lightMap.dispose();
                        if (child.material.bumpMap) child.material.bumpMap.dispose();
                        if (child.material.normalMap) child.material.normalMap.dispose();
                        if (child.material.specularMap) child.material.specularMap.dispose();
                        if (child.material.envMap) child.material.envMap.dispose();
                        child.material.dispose();
                    }
                }
            }
        });

        // Force garbage collection if available (helps with memory cleanup)
        if (window.gc && typeof window.gc === 'function') {
            try {
                window.gc();
                console.log('[3D-VIEWER] Forced garbage collection');
            } catch (e) {
                // Ignore errors - gc() might not be available
            }
        }
    }

    // Clear the current model
    clearModel() {
        if (this.currentModel) {
            this.disposeModel(this.currentModel);
            this.scene.remove(this.currentModel);
            this.currentModel = null;

            // Reset model center
            this.modelCenter.set(0, 0, 0);
            this.modelBoundingBox = null;
        }

        // Clear curved surface detection data - now handled by SelectionManager

        this.hideControls();

        // Update info to show just controls
        const infoElement = document.getElementById('model-info-text');
        if (infoElement) {
            infoElement.innerHTML = `
                <strong>No model loaded</strong><br>
                <hr class="my-1 border-gray-300">
                <div class="text-xs mt-1">
                    ${this.controlsInfo.left}<br>
                    ${this.controlsInfo.wheel}<br>
                    ${this.controlsInfo.middle}
                </div>
            `;
        }

        // Keep the info panel visible to show controls help
        document.getElementById('viewer-info')?.classList.remove('hidden');

        const placeholder = this.container.querySelector('.viewer-placeholder');
        if (placeholder) {
            placeholder.innerHTML = `
                <i class="fas fa-cube"></i>
                <p class="text-sm">3D model will appear here</p>
                <p class="text-xs opacity-75 mt-1">Generate a model to see the preview</p>
            `;
            placeholder.classList.remove('hidden');
        }
    }

    // Set standard camera views
    setStandardView(viewType) {
        if (!this.currentModel) return;

        // Get current model center and size
        const box = new THREE.Box3().setFromObject(this.currentModel);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) * 2;

        switch (viewType) {
            case 'top':
                this.camera.position.set(center.x, center.y + maxDim, center.z);
                break;
            case 'bottom':
                this.camera.position.set(center.x, center.y - maxDim, center.z);
                break;
            case 'front':
                this.camera.position.set(center.x, center.y, center.z + maxDim);
                break;
            case 'back':
                this.camera.position.set(center.x, center.y, center.z - maxDim);
                break;
            case 'left':
                this.camera.position.set(center.x - maxDim, center.y, center.z);
                break;
            case 'right':
                this.camera.position.set(center.x + maxDim, center.y, center.z);
                break;
            default:
                return;
        }

        // Look at model center
        this.camera.lookAt(center);
        this.controls.target.copy(center);
        this.controls.update();
    }

    // Helper method to update controls info display
    updateControlsInfoDisplay() {
        if (!this.currentModel) return;

        const box = new THREE.Box3().setFromObject(this.currentModel);
        const center = box.getCenter(new THREE.Vector3());

        const infoText = `
            <div class="text-xs mt-1">
                <b>Controls:</b><br>
                ${this.controlsInfo.left}<br>
                ${this.controlsInfo.wheel}<br>
                ${this.controlsInfo.middle}<br>
                <span style="color: #ffcc00;">Orbit around: ${center.x.toFixed(2)}, ${center.y.toFixed(2)}, ${center.z.toFixed(2)}</span><br>
                <span style="color: #45b6fe;">Ctrl + click: Select edge</span>
            </div>
        `;

        // Append to model info if exists
        const infoElement = document.getElementById('model-info-text');
        if (infoElement && infoElement.innerHTML) {
            const currentInfo = infoElement.innerHTML;
            if (!currentInfo.includes('Controls:')) { // Check for English "Controls:"
                infoElement.innerHTML += `<hr class="my-1 border-gray-300">` + infoText;
            }
        }
    }

    onModelClick(event) {
        console.log('🎯 3D-Viewer onModelClick triggered');
        console.log('🎯 Event from 3D viewer:', {
            type: event.type,
            button: event.button,
            ctrlKey: event.ctrlKey,
            altKey: event.altKey,
            shiftKey: event.shiftKey,
            metaKey: event.metaKey
        });

        if (!this.currentModel || event.button !== 0) return;

        // Use renderer.domElement for consistent coordinate calculation relative to the canvas
        const rect = this.renderer.domElement.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2(x, y);
        raycaster.setFromCamera(mouse, this.camera);

        const intersects = raycaster.intersectObject(this.currentModel, true);

        // Clear any ongoing flash from a previous selection/state
        if (this.flashIntervalId) {
            clearInterval(this.flashIntervalId);
            this.flashIntervalId = null;
        }

        // Check if we're in edge mode first to avoid unnecessary clearing
        const isEdgeToggleMode = window.anaGeo && window.anaGeo.isEdgeMode;
        const isCtrlPressed = event && (event.ctrlKey || event.originalEvent?.ctrlKey);
        const isEdgeMode = isEdgeToggleMode || isCtrlPressed;

        // Clear all previous highlights (but we'll clear again in edge mode for safety)
        if (!isEdgeMode) {
            this.clearAllHighlights();
        }

        if (intersects.length > 0) {
            const intersection = intersects[0];
            const clickedTriangle = intersection.face;
            const object = intersection.object; // The intersected THREE.Mesh

            if (!clickedTriangle || !object.geometry || !object.geometry.attributes.position) {
                console.warn("Intersected object or face is missing required data for selection.");
                this.updateInfoDisplay();
                return;
            }

            // Edge selection removed - using simple mesh selection only

            // SIMPLIFIED MESH SELECTION - Highlight entire mesh when clicked
            console.log('🎯 Simple mesh selection mode');

            // Edge mode already checked above, reuse the values

            console.log('🔍 Edge mode check:', {
                isEdgeToggleMode,
                isCtrlPressed,
                isEdgeMode,
                eventCtrlKey: event?.ctrlKey,
                originalEventCtrlKey: event?.originalEvent?.ctrlKey
            });

            // Clear existing highlights first (especially important for edge mode)
            if (isEdgeMode) {
                console.log('🔗 Edge mode active - clearing existing highlights first');
                this.clearAllHighlights();
            }

            if (!isEdgeMode) {
                // Create highlight for the entire mesh (only if not in edge mode)
                this.createMeshHighlight(intersection, object);
                console.log('✅ Face highlight created');
            } else {
                console.log('🔗 Edge mode active - skipping face highlight');
            }

            // Store selected mesh info
            this.selectedMeshInfo = {
                meshName: object.name || 'Unnamed Mesh',
                clickPoint: intersection.point,
                clickNormal: intersection.face.normal,
                triangleIndex: intersection.faceIndex,
                object: object,
                totalTriangles: this.getMeshTriangleCount(object),
                totalVertices: this.getMeshVertexCount(object)
            };

            // Clear any previous face/triangle info
            this.selectedFaceInfo = null;
            this.selectedTriangleInfo = null;

            // Update info display with mesh information
            this.updateInfoDisplay();

            // Show mesh tooltip
            this.showMeshTooltip(event, this.selectedMeshInfo);

            // Trigger Ana_Geo geometry analysis if available
            if (typeof window.anaGeo !== 'undefined' && window.anaGeo.isInitialized) {
                console.log('🔍 Triggering Ana_Geo geometry analysis...');

                // Create viewer object for Ana_Geo
                const viewerForAnaGeo = {
                    selectedMeshInfo: this.selectedMeshInfo,
                    renderer: this.renderer,
                    camera: this.camera,
                    scene: this.scene,
                    currentModel: this.currentModel,
                    highlightMeshMesh: this.highlightMeshMesh,
                    clearHighlight: this.clearAllHighlights?.bind(this)
                };

                // Call Ana_Geo handleMeshClick to respect edge/face mode
                window.anaGeo.handleMeshClick(event, viewerForAnaGeo);
            } else {
                console.warn('⚠️ Ana_Geo system not available or not initialized');
            }

            // Simple mesh info display
            const selectedMeshInfoText = `
                <div class="p-2 bg-blue-600 text-white rounded text-xs mt-2 border border-blue-700">
                    <b>Selected Mesh: ${this.selectedMeshInfo.meshName}</b><br>
                    Total Triangles: ${this.selectedMeshInfo.totalTriangles}<br>
                    Total Vertices: ${this.selectedMeshInfo.totalVertices}<br>
                    Click Point: (${this.selectedMeshInfo.clickPoint.x.toFixed(2)}, ${this.selectedMeshInfo.clickPoint.y.toFixed(2)}, ${this.selectedMeshInfo.clickPoint.z.toFixed(2)})<br>
                    Click Normal: (${this.selectedMeshInfo.clickNormal.x.toFixed(2)}, ${this.selectedMeshInfo.clickNormal.y.toFixed(2)}, ${this.selectedMeshInfo.clickNormal.z.toFixed(2)})
                </div>
            `;

            const infoElement = document.getElementById('model-info-text');
            if (infoElement) {
                // Ensure originalInfoContent is up-to-date
                if (!this.originalInfoContent || !this.originalInfoContent.includes(this.modelInfo.fileName) || this.modelInfo.fileName === '') {
                    this.updateInfoDisplay();
                }
                infoElement.innerHTML = this.originalInfoContent + selectedMeshInfoText;
            }

        } else {
            // Clicked on empty space, restore original info display
            this.updateInfoDisplay();
            // Hide any existing mesh tooltips
            const existingTooltip = document.getElementById('mesh-tooltip');
            if (existingTooltip) {
                existingTooltip.remove();
            }
        }
    }

    // findRealEdges() removed - not needed for group-based OBJ

    // addEdgeInfo() removed - not needed for group-based OBJ

    // handleEdgeSelection() removed - not needed for group-based OBJ
















    // findConnectedTriangles() removed - not needed for group-based OBJ

    // calculateApproximateRadius() removed - not needed for group-based OBJ

    // calculateTriangleGroupArea() removed - not needed for group-based OBJ

    // calculateAverageNormal() removed - not needed for group-based OBJ







    // pointToLineDistance() removed - not needed for group-based OBJ

    // createEdgeHighlight() removed - not needed for group-based OBJ

    // flashHighlightedEdge() removed - not needed for group-based OBJ

    // flashHighlightedFace() removed - not needed for group-based OBJ

    // toggleSelectEdgeMode() removed - not needed for group-based OBJ

    // showSelectEdgeModeMessage() and hideSelectEdgeModeMessage() removed - not needed for group-based OBJ

    // pasteEdgeCoordinatesToChat() removed - not needed for group-based OBJ

    // toggleSelectFaceMode() removed - not needed for group-based OBJ

    // showSelectFaceModeMessage() and hideSelectFaceModeMessage() removed - not needed for group-based OBJ

    // pasteFaceBoundingBoxToChat() removed - not needed for group-based OBJ

    // Setup grid
    setupGrid() {
        // Grid removed - method empty but kept for compatibility
    }

    // setupFaceTooltip() removed - not needed for group-based OBJ

    // showFaceTooltip() and hideFaceTooltip() removed - not needed for group-based OBJ

    // Show mesh tooltip
    showMeshTooltip(event, meshInfo) {
        if (!meshInfo) return;

        // Create simple tooltip for mesh
        const tooltipContent = `
            <div style="position: absolute; background: rgba(0,0,0,0.8); color: white; padding: 8px; border-radius: 4px; font-size: 12px; z-index: 1000; pointer-events: none;">
                <b>Mesh: ${meshInfo.meshName}</b><br>
                Triangles: ${meshInfo.totalTriangles}<br>
                Vertices: ${meshInfo.totalVertices}<br>
                Click Point: (${meshInfo.clickPoint.x.toFixed(2)}, ${meshInfo.clickPoint.y.toFixed(2)}, ${meshInfo.clickPoint.z.toFixed(2)})
            </div>
        `;

        // Remove existing mesh tooltip
        const existingTooltip = document.getElementById('mesh-tooltip');
        if (existingTooltip) {
            existingTooltip.remove();
        }

        // Create new tooltip
        const tooltip = document.createElement('div');
        tooltip.id = 'mesh-tooltip';
        tooltip.innerHTML = tooltipContent;

        // Position tooltip
        const rect = this.renderer.domElement.getBoundingClientRect();
        const viewerRect = this.container.querySelector('.viewer-3d').getBoundingClientRect();

        const mouseX = event.clientX - viewerRect.left;
        const mouseY = event.clientY - viewerRect.top;

        tooltip.style.left = (mouseX + 15) + 'px';
        tooltip.style.top = (mouseY - 15) + 'px';

        // Add to container
        this.container.appendChild(tooltip);

        // Auto hide after 5 seconds
        setTimeout(() => {
            if (tooltip && tooltip.parentNode) {
                tooltip.remove();
            }
        }, 5000);
    }

    // setupEdgeTooltip() removed - not needed for group-based OBJ

    // showEdgeTooltip() and hideEdgeTooltip() removed - not needed for group-based OBJ







}



// Export for use in main.js
window.ModelViewer3D = ModelViewer3D;
