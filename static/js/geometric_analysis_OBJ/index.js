/**
 * Ana_Geo - Geometric analysis system
 * Entry point and integration with 3D viewer
 */

class AnaGeo {
    constructor() {
        this.geometryAnalyzer = null;
        this.isInitialized = false;
        this.is3DViewerIntegrated = false;
        this.analysisHistory = [];
        this.lastMousePosition = { x: 0, y: 0 };
        this.tooltipTimeout = null;

        // Edge selection properties
        this.isEdgeMode = false;
        this.selectedEdgeInfo = null;
        this.highlightEdgeMesh = null;

        // Face selection properties
        this.isFaceMode = false;
        this.selectedFaceInfo = null;
        this.highlightFaceMesh = null;

        console.log('🚀 AnaGeo system created (not initialized yet)...');
        // DO NOT auto-initialize - wait for OBJ load
    }

    /**
     * Initialize system
     */
    async init() {
        try {
            console.log('⏳ Initializing Ana_Geo after OBJ load...');

            // Check dependencies without timeout
            if (!this.checkDependenciesSync()) {
                console.error('❌ Dependencies not available for Ana_Geo initialization');
                return false;
            }

            // Initialize analyzer
            try {
                this.geometryAnalyzer = new GeometryAnalyzer();
                console.log('✅ GeometryAnalyzer initialized successfully');
            } catch (error) {
                console.error('❌ Failed to initialize GeometryAnalyzer:', error);
                this.geometryAnalyzer = null;
                return false;
            }

            // Integrate with 3D viewer
            this.integrateWith3DViewer();

            // Setup UI
            this.setupUI();

            // Setup global mouse tracking
            this.setupMouseTracking();

            this.isInitialized = true;
            console.log('✅ AnaGeo system initialized successfully');
            return true;

        } catch (error) {
            console.error('❌ Failed to initialize AnaGeo:', error);
            return false;
        }
    }

    /**
     * Check dependencies synchronously (no timeout)
     */
    checkDependenciesSync() {
        // Check THREE.js
        if (typeof THREE === 'undefined') {
            console.error('❌ THREE.js not available');
            return false;
        }

        // Check required classes
        const requiredClasses = [
            'GeometryAnalyzer',
            'SimpleFaceAnalyzer',
            'EdgeAnalyzer',
            'GeometryUtils'
        ];

        const missingClasses = requiredClasses.filter(className =>
            typeof window[className] === 'undefined'
        );

        if (missingClasses.length > 0) {
            console.error(`❌ Missing classes: ${missingClasses.join(', ')}`);
            return false;
        }

        console.log('✅ All dependencies available');
        return true;
    }



    // waitForDependencies method removed - using synchronous check instead

    /**
     * Integrate with the current 3D viewer
     */
    integrateWith3DViewer() {
        // Check if the 3D viewer exists (check both possible names)
        const viewer = window.modelViewer || window.viewer3D;

        if (!viewer) {
            console.warn('⚠️ 3D viewer not found (checked modelViewer and viewer3D), will retry in 1 second...');
            // Retry after 1 second
            setTimeout(() => {
                this.integrateWith3DViewer();
            }, 1000);
            return;
        }

        console.log('🔍 Found 3D viewer:', viewer);
        console.log('🔍 Viewer type:', viewer.constructor.name);

        // For the current 3D viewer, we don't need to override click handler
        // because we've already integrated directly in the 3d-viewer.js file
        console.log('🔗 Ana_Geo integration ready - 3D viewer will call Ana_Geo on mesh clicks');

        // Set flag to indicate integration is complete
        this.is3DViewerIntegrated = true;

        console.log('🔗 Integrated with 3D viewer successfully');
    }

    /**
     * Extend mesh click handler to add geometry analysis
     */
    extendMeshClickHandler(viewer) {
        console.log('🔗 Extending mesh click handler...');
        console.log('🔍 Original onModelClick:', typeof viewer.onModelClick);

        // Save the original click handler if it exists
        const originalOnModelClick = viewer.onModelClick.bind(viewer);

        // Add direct event listener for Ctrl+Click detection
        const viewerElement = viewer.container.querySelector('.viewer-3d');
        if (viewerElement) {
            console.log('🔗 Adding direct Ctrl+Click listener to viewer element');

            // Store current click state for coordination
            this.lastClickEvent = null;
            this.lastClickTime = 0;

            viewerElement.addEventListener('click', (event) => {
                console.log('🎯 Direct click listener triggered');
                console.log('🎯 Direct event keys:', {
                    ctrlKey: event.ctrlKey,
                    altKey: event.altKey,
                    shiftKey: event.shiftKey,
                    metaKey: event.metaKey
                });

                // Store for coordination with onModelClick
                this.lastClickEvent = {
                    ctrlKey: event.ctrlKey,
                    altKey: event.altKey,
                    shiftKey: event.shiftKey,
                    metaKey: event.metaKey,
                    type: event.type,
                    button: event.button,
                    clientX: event.clientX,
                    clientY: event.clientY,
                    timestamp: Date.now()
                };
                this.lastClickTime = Date.now();
            });
        }

        // Create an enhanced click handler
        viewer.onModelClick = (event) => {
            console.log('🎯 Enhanced click handler triggered with event:', event);
            console.log('🎯 Event type:', event.type, 'Button:', event.button);
            console.log('🎯 Event keys:', {
                ctrlKey: event.ctrlKey,
                altKey: event.altKey,
                shiftKey: event.shiftKey,
                metaKey: event.metaKey
            });

            // Use direct click event if available and recent (within 200ms)
            let eventToUse = event;
            if (this.lastClickEvent && (Date.now() - this.lastClickTime) < 200) {
                console.log('🎯 Using direct click event for better key detection');
                eventToUse = this.lastClickEvent;
            }

            // Preserve event properties for setTimeout
            const eventProps = {
                ctrlKey: eventToUse.ctrlKey,
                altKey: eventToUse.altKey,
                shiftKey: eventToUse.shiftKey,
                metaKey: eventToUse.metaKey,
                type: eventToUse.type,
                button: eventToUse.button || 0,
                clientX: eventToUse.clientX,
                clientY: eventToUse.clientY
            };

            // Call the original handler first
            originalOnModelClick(event);

            // Add geometry analysis with a small delay to ensure the original handler completes
            setTimeout(() => {
                console.log('⏰ Calling handleMeshClick after delay');
                console.log('⏰ Preserved event props:', eventProps);
                this.handleMeshClick(eventProps, viewer);
            }, 100);
        };

        console.log('✅ Click handler extended successfully');
        console.log('🔍 New onModelClick:', typeof viewer.onModelClick);
    }

    /**
     * Handle clicks on the mesh to analyze geometry
     */
    handleMeshClick(event, viewer) {
        console.log('🔍 Ana_Geo handleMeshClick triggered');

        try {
            // Check if in edge mode (either toggle mode or Ctrl+Click)
            if (this.isEdgeMode || event.ctrlKey) {
                console.log('🔗 Edge selection mode active - handling edge selection');
                this.handleEdgeClick(event, viewer);
                return;
            }

            // NEW: Check if in face mode
            if (this.isFaceMode) {
                console.log('🔲 Face selection mode active - handling face selection');
                this.handleFaceClick(event, viewer);
                return;
            }

            // Normal click - handle face analysis
            console.log('👆 Normal click - handling face analysis');

            // Use information from selectedMeshInfo if available (from the original handler)
            if (viewer.selectedMeshInfo) {
                console.log('📊 Using selectedMeshInfo from viewer:', viewer.selectedMeshInfo);

                const meshInfo = {
                    name: viewer.selectedMeshInfo.meshName,
                    triangles: viewer.selectedMeshInfo.totalTriangles,
                    vertices: viewer.selectedMeshInfo.totalVertices
                };

                const mesh = viewer.selectedMeshInfo.object;
                const clickPoint = viewer.selectedMeshInfo.clickPoint;

                // Save the mouse position for the tooltip
                this.lastMousePosition = {
                    x: event.clientX,
                    y: event.clientY
                };

                // Perform geometric analysis
                this.analyzeMeshGeometry(meshInfo, mesh, clickPoint, event);
                return;
            }

            // Fallback: perform own raycasting
            console.log('🔄 Fallback: performing own raycasting');
            const raycaster = new THREE.Raycaster();
            const mouse = new THREE.Vector2();

            // Calculate mouse position
            const rect = viewer.renderer.domElement.getBoundingClientRect();
            mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

            raycaster.setFromCamera(mouse, viewer.camera);

            if (!viewer.currentModel) return;

            const intersects = raycaster.intersectObject(viewer.currentModel, true);

            if (intersects.length > 0) {
                const intersection = intersects[0];
                const mesh = intersection.object;
                const clickPoint = intersection.point;

                // Create mesh info from the intersection
                const meshInfo = {
                    name: mesh.name || mesh.parent?.name || 'Unknown',
                    triangles: this.getTriangleCount(mesh),
                    vertices: this.getVertexCount(mesh)
                };

                
                this.lastMousePosition = {
                    x: event.clientX,
                    y: event.clientY
                };

                
                this.analyzeMeshGeometry(meshInfo, mesh, clickPoint, event);
            }

        } catch (error) {
            console.error('❌ Error in mesh click handler:', error);
        }
    }

    /**
     * Analyze the geometry of the mesh
     */
    analyzeMeshGeometry(meshInfo, mesh, clickPoint, event) {
        console.log('🔍 Starting geometry analysis for:', meshInfo.name);
        console.log('📊 Mesh info:', meshInfo);
        console.log('🎯 Mesh object:', mesh);
        console.log('📍 Click point:', clickPoint);

        try {
            // Check the geometry analyzer
            if (!this.geometryAnalyzer) {
                throw new Error('GeometryAnalyzer not initialized');
            }

            console.log('🔧 GeometryAnalyzer available, starting analysis...');

            // Perform the analysis
            const analysisResult = this.geometryAnalyzer.analyzeMesh(meshInfo, mesh, clickPoint);

            console.log('✅ Analysis result:', analysisResult);

            // Save to history
            this.analysisHistory.push({
                id: this.generateAnalysisId(),
                timestamp: new Date().toISOString(),
                meshInfo,
                result: analysisResult
            });

            // Display the result in a tooltip instead of a fixed panel
            this.showTooltip(analysisResult, event || this.lastMousePosition);

            // Log for debugging
            console.log('📊 Analysis completed successfully');

        } catch (error) {
            console.error('❌ Error analyzing mesh geometry:', error);
            console.error('❌ Error stack:', error.stack);
            this.showErrorTooltip('Unable to analyze geometry: ' + error.message, event || this.lastMousePosition);
        }
    }

    /**
     * Display a tooltip with the analysis results
     */
    showTooltip(analysisResult, mousePosition) {
        console.log('🎯 Showing analysis tooltip');

        // Remove the old tooltip if it exists
        this.hideTooltip();

        // Create the tooltip content
        const tooltipContent = this.createTooltipContent(analysisResult);

        // Create the tooltip element
        const tooltip = document.createElement('div');
        tooltip.id = 'geometry-analysis-tooltip';
        tooltip.className = 'geometry-tooltip';
        tooltip.innerHTML = tooltipContent;

        // Find the 3D viewer container to append the tooltip to
        const viewer3DContainer = this.find3DViewerContainer();
        if (viewer3DContainer) {
            // Make sure the container has a relative position so that the absolute tooltip works correctly
            if (getComputedStyle(viewer3DContainer).position === 'static') {
                viewer3DContainer.style.position = 'relative';
            }
            viewer3DContainer.appendChild(tooltip);
            console.log('🎯 Tooltip appended to 3D viewer container');
        } else {
            // Fallback: append to the body if the container is not found
            document.body.appendChild(tooltip);
            console.warn('⚠️ 3D viewer container not found, appending tooltip to body');
        }

        // Calculate the position
        const position = this.calculateTooltipPosition(mousePosition);

        // Apply the position
        tooltip.style.left = position.x + 'px';
        tooltip.style.top = position.y + 'px';

        // Display with an animation
        setTimeout(() => {
            tooltip.classList.add('show');
        }, 10);

        // Automatically hide after 10 seconds
        this.tooltipTimeout = setTimeout(() => {
            this.hideTooltip();
        }, 10000);
    }

    /**
     * Display an error tooltip
     */
    showErrorTooltip(message, mousePosition) {
        console.log('❌ Showing error tooltip');

        this.hideTooltip();

        const tooltip = document.createElement('div');
        tooltip.id = 'geometry-analysis-tooltip';
        tooltip.className = 'geometry-tooltip error';
        tooltip.innerHTML = `
            <div class="tooltip-header error">
                <span class="tooltip-icon">❌</span>
                <span class="tooltip-title">Analysis Error</span>
                <button class="tooltip-close" onclick="window.anaGeo.hideTooltip()">×</button>
            </div>
            <div class="tooltip-content">
                <p>${message}</p>
            </div>
        `;

        //
        const viewer3DContainer = this.find3DViewerContainer();
        if (viewer3DContainer) {
            
            if (getComputedStyle(viewer3DContainer).position === 'static') {
                viewer3DContainer.style.position = 'relative';
            }
            viewer3DContainer.appendChild(tooltip);
            console.log('🎯 Error tooltip appended to 3D viewer container');
        } else {
            // Fallb
            document.body.appendChild(tooltip);
            console.warn('⚠️ 3D viewer container not found, appending error tooltip to body');
        }

        const position = this.calculateTooltipPosition(mousePosition);
        tooltip.style.left = position.x + 'px';
        tooltip.style.top = position.y + 'px';

        setTimeout(() => tooltip.classList.add('show'), 10);

        this.tooltipTimeout = setTimeout(() => {
            this.hideTooltip();
        }, 5000);
    }

    /**
     * Find the 3D viewer container
     */
    find3DViewerContainer() {
        // Try common selectors for the 3D viewer container
        const selectors = [
            '.viewer-3d',           // C
            '#viewer-3d',           // 
            '.model-viewer',        //
            '#model-viewer',        //
            '[data-viewer="3d"]',   // Data attribute
            'canvas[data-engine="three"]' 
        ];

        for (const selector of selectors) {
            const element = document.querySelector(selector);
            if (element) {
                console.log(`🔍 Found 3D viewer container with selector: ${selector}`);
                return element.closest('.viewer-container') || element.parentElement || element;
            }
        }

        // Try to find it through the Three.js renderer if it exists
        const viewer = window.viewer3D || window.modelViewer;
        if (viewer && viewer.renderer && viewer.renderer.domElement) {
            const canvas = viewer.renderer.domElement;
            console.log('🔍 Found 3D viewer container via Three.js renderer');
            return canvas.closest('.viewer-container') || canvas.parentElement || canvas;
        }

        console.warn('⚠️ Could not find 3D viewer container');
        return null;
    }

    /**
     * Hide the tooltip
     */
    hideTooltip() {
        const tooltip = document.getElementById('geometry-analysis-tooltip');
        if (tooltip) {
            tooltip.classList.remove('show');
            setTimeout(() => {
                if (tooltip.parentNode) {
                    tooltip.parentNode.removeChild(tooltip);
                }
            }, 300);
        }

        if (this.tooltipTimeout) {
            clearTimeout(this.tooltipTimeout);
            this.tooltipTimeout = null;
        }
    }

    /**
     * Create the tooltip content
     */
    createTooltipContent(analysisResult) {
        if (!analysisResult.success) {
            return `
                <div class="tooltip-header error">
                    <span class="tooltip-icon">❌</span>
                    <span class="tooltip-title">Analysis Error</span>
                    <button class="tooltip-close" onclick="window.anaGeo.hideTooltip()">×</button>
                </div>
                <div class="tooltip-content">
                    <p>${analysisResult.error}</p>
                </div>
            `;
        }

        const { basicInfo, shapeType, detailedAnalysis } = analysisResult;

        // Create icon based on shape type
        let shapeIcon = '📊';
        if (shapeType === 'cylinder') shapeIcon = '🔵';
        else if (shapeType === 'rectangle') shapeIcon = '⬜';
        else if (shapeType === 'face') shapeIcon = '📄';

        return `
            <div class="tooltip-header">
                <span class="tooltip-icon">${shapeIcon}</span>
                <span class="tooltip-title">${basicInfo.groupName}</span>
                <button class="tooltip-close" onclick="window.anaGeo.hideTooltip()">×</button>
            </div>
            <div class="tooltip-content">
                <div class="tooltip-section">
                    <div class="section-title">🔍 Shape Type</div>
                    <div class="section-value">${detailedAnalysis.type}</div>
                </div>

                <div class="tooltip-section">
                    <div class="section-title">📏 Dimensions (mm)</div>
                    <div class="properties-grid">
                        ${Object.entries(detailedAnalysis.properties || {}).slice(0, 4).map(([key, value]) =>
                            `<div class="property-item">
                                <span class="property-label">${key}:</span>
                                <span class="property-value">${value}</span>
                            </div>`
                        ).join('')}
                    </div>
                </div>

                <div class="tooltip-section">
                    <div class="section-title">📊 Mesh Details</div>
                    <div class="mesh-details">
                        <span class="detail-item">Triangles: ${basicInfo.triangles}</span>
                        <span class="detail-item">Vertices: ${basicInfo.vertices}</span>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Calculate the tooltip position
     */
    calculateTooltipPosition(mousePosition) {
        const offset = 15;
        const padding = 10;

        // Find the 3D viewer container to calculate the relative position
        const viewer3DContainer = this.find3DViewerContainer();

        let containerWidth, containerHeight, containerRect;

        if (viewer3DContainer) {
            // Calculate relative to the 3D viewer container
            containerRect = viewer3DContainer.getBoundingClientRect();
            containerWidth = containerRect.width;
            containerHeight = containerRect.height;

            // Convert mouse position from viewport coordinates to container coordinates
            let x = mousePosition.x - containerRect.left + offset;
            let y = mousePosition.y - containerRect.top + offset;

            console.log(`🎯 Tooltip position relative to container: (${x}, ${y})`);

            // Get the tooltip size (estimated)
            const tooltipWidth = 320;
            const tooltipHeight = 250;

            // Check and adjust if the tooltip goes outside the container
            if (x + tooltipWidth > containerWidth - padding) {
                x = (mousePosition.x - containerRect.left) - tooltipWidth - offset;
            }

            if (y + tooltipHeight > containerHeight - padding) {
                y = (mousePosition.y - containerRect.top) - tooltipHeight - offset;
            }

            // Make sure it's not negative
            x = Math.max(padding, x);
            y = Math.max(padding, y);

            return { x, y };
        } else {
            // Fallback: calculate based on the viewport as before
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            const tooltipWidth = 320;
            const tooltipHeight = 250;

            let x = mousePosition.x + offset;
            let y = mousePosition.y + offset;

            if (x + tooltipWidth > viewportWidth - padding) {
                x = mousePosition.x - tooltipWidth - offset;
            }

            if (y + tooltipHeight > viewportHeight - padding) {
                y = mousePosition.y - tooltipHeight - offset;
            }

            x = Math.max(padding, x);
            y = Math.max(padding, y);

            return { x, y };
        }
    }

    /**
     * Apply CSS styles for the analysis display
     */
    applyAnalysisStyles() {
        if (document.getElementById('ana-geo-styles')) return;
        
        const styles = document.createElement('style');
        styles.id = 'ana-geo-styles';
        styles.textContent = `
            .analysis-report h3 {
                margin: 0 0 10px 0;
                color: #333;
                font-size: 14px;
                border-bottom: 2px solid #007bff;
                padding-bottom: 5px;
            }
            
            .analysis-report h4 {
                margin: 10px 0 5px 0;
                color: #555;
                font-size: 12px;
            }
            
            .analysis-report ul {
                margin: 5px 0;
                padding-left: 15px;
            }
            
            .analysis-report li {
                margin: 2px 0;
                color: #666;
            }
            
            .analysis-report .error {
                color: #dc3545;
                background: #f8d7da;
                padding: 10px;
                border-radius: 4px;
                border: 1px solid #f5c6cb;
            }
            
            .basic-info, .detailed-analysis, .dimensions {
                margin: 10px 0;
                padding: 8px;
                background: #f8f9fa;
                border-radius: 4px;
                border-left: 3px solid #007bff;
            }
        `;
        
        document.head.appendChild(styles);
    }

    /**
     * Display an error
     */
    displayError(message) {
        const errorHTML = `
            <div class="analysis-report error">
                <h3>❌ Geometry Analysis Error</h3>
                <p>${message}</p>
            </div>
        `;
        
        let container = document.getElementById('geometry-analysis-panel');
        if (!container) {
            container = this.createAnalysisPanel();
        }
        
        container.innerHTML = errorHTML;
        container.style.display = 'block';
    }



    generateAnalysisId() {
        return 'analysis_' + Date.now() + '_' + Math.random().toString(36).substring(2, 11);
    }

    /**
     * Get triangle count from mesh geometry
     */
    getTriangleCount(mesh) {
        const geometry = mesh.geometry;
        if (!geometry) return 0;

        if (geometry.index) {
            return geometry.index.count / 3;
        } else {
            const positions = geometry.attributes.position;
            return positions ? positions.count / 3 : 0;
        }
    }

    /**
     * Get vertex count from mesh geometry
     */
    getVertexCount(mesh) {
        const geometry = mesh.geometry;
        if (!geometry) return 0;

        const positions = geometry.attributes.position;
        return positions ? positions.count : 0;
    }





    /**
     * Setup UI controls
     */
    setupUI() {
        // UI setup complete - using tooltip display instead of panels
        console.log('✅ UI setup complete');

        // Initialize edge button state
        this.updateEdgeButtonState();
    }

    /**
     * Setup global mouse tracking
     */
    setupMouseTracking() {
        document.addEventListener('mousemove', (event) => {
            this.lastMousePosition = {
                x: event.clientX,
                y: event.clientY
            };
        });

        // Add click listener to hide tooltip when clicking elsewhere
        document.addEventListener('click', (event) => {
            // Check if click is outside the 3D viewer
            const viewer3D = document.querySelector('.viewer-3d');
            if (viewer3D && !viewer3D.contains(event.target)) {
                this.hideTooltip();

                // Also clear edge selection if clicking outside viewer (but not on edge button)
                const edgeBtn = document.getElementById('select-edge-btn');
                if (this.selectedEdgeInfo && !this.isEdgeMode &&
                    event.target !== edgeBtn && !edgeBtn?.contains(event.target)) {
                    console.log('🔗 Clearing edge selection due to outside click');
                    this.clearEdgeSelection();
                }

                // Also clear face selection if clicking outside viewer (but not on face button)
                const faceBtn = document.getElementById('select-face-btn');
                if (this.selectedFaceInfo && !this.isFaceMode &&
                    event.target !== faceBtn && !faceBtn?.contains(event.target)) {
                    console.log('🔲 Clearing face selection due to outside click');
                    this.clearFaceSelection();
                }
            }
        });

        console.log('🖱️ Mouse tracking setup completed');
    }




    /**
     * Toggle edge selection mode
     */
    toggleEdgeMode() {
        // Special case: if edge mode is currently off but we have a selected edge,
        // clear the selection instead of enabling edge mode
        if (!this.isEdgeMode && this.selectedEdgeInfo) {
            console.log('🔗 Clearing existing edge selection');
            this.forceClearEdgeSelection();
            return;
        }

        this.isEdgeMode = !this.isEdgeMode;
        const selectEdgeBtn = document.getElementById('select-edge-btn');

        if (this.isEdgeMode) {
            // Enable edge mode
            selectEdgeBtn?.classList.add('select-edge-active');
            console.log('🔗 Edge selection mode enabled');
            this.showEdgeModeMessage();
        } else {
            // Disable edge mode
            selectEdgeBtn?.classList.remove('select-edge-active');
            console.log('🔗 Edge selection mode disabled');
            this.hideEdgeModeMessage();

            // Only clear edge selection if no edge is currently selected
            // This preserves the highlight when edge mode is auto-disabled after selection
            if (!this.selectedEdgeInfo) {
                this.clearEdgeSelection();
            }
        }
    }

    /**
     * Toggle face selection mode
     */
    toggleFaceMode() {
        // Special case: if face mode is currently off but we have a selected face,
        // clear the selection instead of enabling face mode
        if (!this.isFaceMode && this.selectedFaceInfo) {
            console.log('🔲 Clearing existing face selection');
            this.forceClearFaceSelection();
            return;
        }

        this.isFaceMode = !this.isFaceMode;
        const selectFaceBtn = document.getElementById('select-face-btn');

        if (this.isFaceMode) {
            // Enable face mode
            selectFaceBtn?.classList.add('select-face-active');
            console.log('🔲 Face selection mode enabled');
            this.showFaceModeMessage();
        } else {
            // Disable face mode
            selectFaceBtn?.classList.remove('select-face-active');
            console.log('🔲 Face selection mode disabled');
            this.hideFaceModeMessage();

            // Only clear face selection if no face is currently selected
            // This preserves the highlight when face mode is auto-disabled after selection
            if (!this.selectedFaceInfo) {
                this.clearFaceSelection();
            }
        }
    }

    /**
     * Force clear edge selection (for manual clearing)
     */
    forceClearEdgeSelection() {
        this.clearEdgeSelection();
        // Also disable edge mode if it's active
        if (this.isEdgeMode) {
            this.isEdgeMode = false;
            const selectEdgeBtn = document.getElementById('select-edge-btn');
            selectEdgeBtn?.classList.remove('select-edge-active');
            this.hideEdgeModeMessage();
        }
        // Update button state
        this.updateEdgeButtonState();
    }

    /**
     * Force clear face selection (for manual clearing)
     */
    forceClearFaceSelection() {
        this.clearFaceSelection();
        // Also disable face mode if it's active
        if (this.isFaceMode) {
            this.isFaceMode = false;
            const selectFaceBtn = document.getElementById('select-face-btn');
            selectFaceBtn?.classList.remove('select-face-active');
            this.hideFaceModeMessage();
        }
        // Update button state
        this.updateFaceButtonState();
    }

    /**
     * Update edge button visual state
     */
    updateEdgeButtonState() {
        const selectEdgeBtn = document.getElementById('select-edge-btn');
        if (!selectEdgeBtn) return;

        if (this.selectedEdgeInfo) {
            // Edge is selected - show selected state
            selectEdgeBtn.classList.add('edge-selected');
            selectEdgeBtn.title = 'Click to clear edge selection';
        } else {
            // No edge selected - show normal state
            selectEdgeBtn.classList.remove('edge-selected');
            selectEdgeBtn.title = 'Select edge mode';
        }
    }

    /**
     * Update face button visual state
     */
    updateFaceButtonState() {
        const selectFaceBtn = document.getElementById('select-face-btn');
        if (!selectFaceBtn) return;

        if (this.selectedFaceInfo) {
            // Face is selected - show selected state
            selectFaceBtn.classList.add('face-selected');
            selectFaceBtn.title = 'Click to clear face selection';
        } else {
            // No face selected - show normal state
            selectFaceBtn.classList.remove('face-selected');
            selectFaceBtn.title = 'Select face mode';
        }
    }

    /**
     * Handle edge click
     */
    handleEdgeClick(event, viewer) {
        console.log('🔗 Handling edge click');

        try {
            // Clear any face highlights from 3D viewer
            this.clearFaceHighlights(viewer);

            if (!viewer.selectedMeshInfo) {
                console.warn('No mesh selected for edge analysis');
                return;
            }

            const mesh = viewer.selectedMeshInfo.object;
            const clickedFace = viewer.selectedMeshInfo.triangleIndex;
            const clickPoint = viewer.selectedMeshInfo.clickPoint.clone();

            // Convert click point to local space
            mesh.worldToLocal(clickPoint);

            // Get geometry and find real edges
            const geometry = mesh.geometry;
            if (!this.geometryAnalyzer.edgeAnalyzer) {
                console.error('EdgeAnalyzer not available');
                return;
            }

            const realEdges = this.geometryAnalyzer.edgeAnalyzer.findRealEdges(geometry);

            // Create face object for edge finding
            const face = {
                a: clickedFace * 3,
                b: clickedFace * 3 + 1,
                c: clickedFace * 3 + 2
            };

            // Find closest edge in clicked triangle
            const closestEdge = this.geometryAnalyzer.edgeAnalyzer.findClosestEdgeInTriangle(
                face, geometry, clickPoint, realEdges
            );

            if (!closestEdge) {
                console.warn('No real edge found in clicked triangle');
                return;
            }

            // Store selected edge info
            this.selectedEdgeInfo = {
                ...closestEdge,
                startWorld: closestEdge.start.clone().applyMatrix4(mesh.matrixWorld),
                endWorld: closestEdge.end.clone().applyMatrix4(mesh.matrixWorld)
            };

            // Create edge highlight
            this.createEdgeHighlight(closestEdge, mesh);

            // Analyze edge and show tooltip
            this.analyzeEdgeGeometry(closestEdge, mesh, event);

            // Update button visual state to show edge is selected
            this.updateEdgeButtonState();

            // If in edge mode, paste to chat and exit mode
            this.pasteEdgeCoordinatesToChat();
            this.toggleEdgeMode();

        } catch (error) {
            console.error('❌ Error handling edge click:', error);
        }
    }

    /**
     * Handle face click
     */
    handleFaceClick(event, viewer) {
        console.log('🔲 Face selection mode - handling face click');

        try {
            // Clear any edge highlights from 3D viewer
            this.clearEdgeHighlights(viewer);

            if (viewer.selectedMeshInfo) {
                const mesh = viewer.selectedMeshInfo.object;
                const clickPoint = viewer.selectedMeshInfo.clickPoint;

                const meshInfo = {
                    name: viewer.selectedMeshInfo.meshName,
                    triangles: viewer.selectedMeshInfo.totalTriangles,
                    vertices: viewer.selectedMeshInfo.totalVertices
                };

                console.log('🔍 Face analysis input:', { meshInfo, mesh, clickPoint });
                console.log('🔍 GeometryAnalyzer available:', !!this.geometryAnalyzer);
                console.log('🔍 FaceAnalyzer available:', !!this.geometryAnalyzer?.faceAnalyzer);

                // Check if geometryAnalyzer and faceAnalyzer are available
                if (!this.geometryAnalyzer) {
                    console.error('❌ GeometryAnalyzer not available');
                    return;
                }

                if (!this.geometryAnalyzer.faceAnalyzer) {
                    console.error('❌ FaceAnalyzer not available');
                    return;
                }

                const faceAnalysis = this.geometryAnalyzer.analyzeMesh(
                    meshInfo, mesh, clickPoint
                );

                console.log('🔍 Face analysis result:', faceAnalysis);

                // Extract bounding box from analysis result
                let bbox = null;
                if (faceAnalysis.success && faceAnalysis.detailedAnalysis && faceAnalysis.detailedAnalysis.parameters) {
                    // Try to get bbox from parameters.analysis.geometry.bbox
                    const params = faceAnalysis.detailedAnalysis.parameters;
                    console.log('🔍 Analysis parameters:', params);
                    if (params.analysis && params.analysis.geometry && params.analysis.geometry.bbox) {
                        bbox = params.analysis.geometry.bbox;
                        console.log('🔍 Found bbox in analysis:', bbox);
                    }
                }

                // Debug: also check other possible locations for bbox
                if (!bbox && faceAnalysis.detailedAnalysis) {
                    console.log('🔍 Searching for bbox in other locations...');
                    console.log('🔍 detailedAnalysis structure:', Object.keys(faceAnalysis.detailedAnalysis));
                }

                if (bbox) {
                    // Store selected face info
                    this.selectedFaceInfo = {
                        mesh: mesh,
                        bbox: bbox,
                        analysis: faceAnalysis,
                        clickPoint: clickPoint,
                        meshInfo: meshInfo
                    };

                    // Create face highlight (optional)
                    this.createFaceHighlight(mesh, bbox);

                    // Update button visual state to show face is selected
                    this.updateFaceButtonState();

                    // If in face mode, paste bounding box to chat and exit mode
                    this.pasteFaceBoundingBoxToChat();
                    this.toggleFaceMode();

                    console.log('✅ Face bounding box copied to chat');
                } else {
                    console.warn('❌ Failed to get bounding box from analysis, trying direct calculation...');

                    // Fallback 1: Calculate bounding box directly from mesh geometry
                    let directBbox = this.calculateMeshBoundingBox(mesh);

                    // Fallback 2: Calculate from vertices like SimpleFaceAnalyzer
                    if (!directBbox) {
                        console.log('🔄 Trying vertex-based calculation...');
                        directBbox = this.calculateBoundingBoxFromVertices(mesh.geometry);
                    }

                    if (directBbox) {
                        console.log('🔄 Using direct bounding box calculation:', directBbox);

                        this.selectedFaceInfo = {
                            mesh: mesh,
                            bbox: directBbox,
                            analysis: faceAnalysis,
                            clickPoint: clickPoint,
                            meshInfo: meshInfo
                        };

                        this.createFaceHighlight(mesh, directBbox);
                        this.updateFaceButtonState();
                        this.pasteFaceBoundingBoxToChat();
                        this.toggleFaceMode();

                        console.log('✅ Face bounding box copied to chat (direct calculation)');
                    } else {
                        console.error('❌ Failed to calculate bounding box');
                    }
                }
            } else {
                console.warn('No mesh selected for face analysis');
            }

        } catch (error) {
            console.error('❌ Error handling face click:', error);
        }
    }

    /**
     * Calculate bounding box directly from mesh geometry (fallback method)
     * Uses local space coordinates like edge selection for consistency
     */
    calculateMeshBoundingBox(mesh) {
        try {
            if (!mesh || !mesh.geometry) {
                console.warn('Invalid mesh for bounding box calculation');
                return null;
            }

            // Compute bounding box in LOCAL SPACE (like edge selection)
            mesh.geometry.computeBoundingBox();
            const box = mesh.geometry.boundingBox;

            if (!box) {
                console.warn('Failed to compute bounding box');
                return null;
            }

            // Use LOCAL coordinates (no transform) for consistency with edge selection
            const min = box.min.clone();
            const max = box.max.clone();
            const size = max.clone().sub(min);
            const center = min.clone().add(max).multiplyScalar(0.5);

            console.log('🔍 Local bounding box:', { min, max, size, center });

            return { min, max, size, center };

        } catch (error) {
            console.error('❌ Error calculating mesh bounding box:', error);
            return null;
        }
    }

    /**
     * Calculate bounding box from vertices (like SimpleFaceAnalyzer)
     */
    calculateBoundingBoxFromVertices(geometry) {
        try {
            if (!geometry || !geometry.attributes.position) {
                console.warn('Invalid geometry for vertex-based bounding box calculation');
                return null;
            }

            const positions = geometry.attributes.position;
            const vertices = [];

            // Extract vertices (local coordinates)
            for (let i = 0; i < positions.count; i++) {
                vertices.push(new THREE.Vector3(
                    positions.getX(i),
                    positions.getY(i),
                    positions.getZ(i)
                ));
            }

            if (vertices.length === 0) {
                console.warn('No vertices found');
                return null;
            }

            // Calculate bounding box like SimpleFaceAnalyzer
            const min = vertices[0].clone();
            const max = vertices[0].clone();

            for (const vertex of vertices) {
                min.min(vertex);
                max.max(vertex);
            }

            const size = max.clone().sub(min);
            const center = min.clone().add(max).multiplyScalar(0.5);

            console.log('🔍 Vertex-based bounding box:', { min, max, size, center });

            return { min, max, size, center };

        } catch (error) {
            console.error('❌ Error calculating vertex-based bounding box:', error);
            return null;
        }
    }

    /**
     * Show edge mode message
     */
    showEdgeModeMessage() {
        console.log('🔗 Edge selection mode active - click on edges to select');
    }

    /**
     * Hide edge mode message
     */
    hideEdgeModeMessage() {
        console.log('🔗 Edge selection mode deactivated');
    }

    /**
     * Show face mode message
     */
    showFaceModeMessage() {
        console.log('🔲 Face selection mode active - click on faces to select');
    }

    /**
     * Hide face mode message
     */
    hideFaceModeMessage() {
        console.log('🔲 Face selection mode deactivated');
    }

    /**
     * Clear edge selection
     */
    clearEdgeSelection() {
        if (this.highlightEdgeMesh) {
            const viewer = window.viewer3D || window.modelViewer;
            if (viewer && viewer.scene) {
                viewer.scene.remove(this.highlightEdgeMesh);
            }
            this.highlightEdgeMesh = null;
        }
        this.selectedEdgeInfo = null;
        // Update button state when clearing selection
        this.updateEdgeButtonState();
    }

    /**
     * Clear only edge highlight mesh (keep selectedEdgeInfo)
     */
    clearEdgeHighlight() {
        if (this.highlightEdgeMesh) {
            const viewer = window.viewer3D || window.modelViewer;
            if (viewer && viewer.scene) {
                viewer.scene.remove(this.highlightEdgeMesh);
            }
            this.highlightEdgeMesh = null;
        }
        // Don't clear selectedEdgeInfo - keep it for copy functionality
    }

    /**
     * Clear face selection
     */
    clearFaceSelection() {
        if (this.highlightFaceMesh) {
            const viewer = window.viewer3D || window.modelViewer;
            if (viewer && viewer.scene) {
                viewer.scene.remove(this.highlightFaceMesh);
            }
            this.highlightFaceMesh = null;
        }
        this.selectedFaceInfo = null;
        // Update button state when clearing selection
        this.updateFaceButtonState();
    }

    /**
     * Clear only face highlight mesh (keep selectedFaceInfo)
     */
    clearFaceHighlight() {
        if (this.highlightFaceMesh) {
            const viewer = window.viewer3D || window.modelViewer;
            if (viewer && viewer.scene) {
                viewer.scene.remove(this.highlightFaceMesh);
            }
            this.highlightFaceMesh = null;
        }
        // Don't clear selectedFaceInfo - keep it for copy functionality
    }

    /**
     * Create edge highlight
     */
    createEdgeHighlight(edge, mesh) {
        console.log('🔗 Creating edge highlight');

        // Remove previous highlight if exists (but keep selectedEdgeInfo)
        this.clearEdgeHighlight();

        try {
            // Create line geometry for the edge
            const geometry = new THREE.BufferGeometry();
            const vertices = new Float32Array([
                edge.start.x, edge.start.y, edge.start.z,
                edge.end.x, edge.end.y, edge.end.z
            ]);

            geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));

            // Create line material
            const material = new THREE.LineBasicMaterial({
                color: 0xffdd00, // Yellow
                linewidth: 3,
                depthTest: false
            });

            // Create line mesh
            this.highlightEdgeMesh = new THREE.Line(geometry, material);

            // Apply mesh transformation
            this.highlightEdgeMesh.applyMatrix4(mesh.matrixWorld);

            // Add to scene - find the Three.js scene
            const viewer = window.modelViewer || window.viewer3D;
            if (viewer && viewer.scene) {
                viewer.scene.add(this.highlightEdgeMesh);
                console.log('✅ Edge highlight added to scene');
            } else {
                console.warn('⚠️ Could not find Three.js scene to add edge highlight');
            }

        } catch (error) {
            console.error('❌ Error creating edge highlight:', error);
        }
    }

    /**
     * Create face highlight
     */
    createFaceHighlight(mesh, bbox) {
        console.log('🔲 Creating face highlight');

        // Remove previous highlight if exists (but keep selectedFaceInfo)
        this.clearFaceHighlight();

        try {
            // Create wireframe box geometry for the face bounding box
            const boxGeometry = new THREE.BoxGeometry(
                bbox.size.x,
                bbox.size.y,
                bbox.size.z
            );

            // Create wireframe material
            const wireframeMaterial = new THREE.MeshBasicMaterial({
                color: 0x00ff00,
                wireframe: true,
                transparent: true,
                opacity: 0.8
            });

            // Create wireframe mesh
            this.highlightFaceMesh = new THREE.Mesh(boxGeometry, wireframeMaterial);

            // Position the wireframe at the center of the bounding box
            this.highlightFaceMesh.position.copy(bbox.center);

            // Add to scene
            const viewer = window.viewer3D || window.modelViewer;
            if (viewer && viewer.scene) {
                viewer.scene.add(this.highlightFaceMesh);
                console.log('✅ Face highlight added to scene');
            }

        } catch (error) {
            console.error('❌ Error creating face highlight:', error);
        }
    }

    /**
     * Analyze edge geometry and show tooltip
     */
    analyzeEdgeGeometry(edge, mesh, event) {
        console.log('🔗 Analyzing edge geometry');

        try {
            if (!this.geometryAnalyzer || !this.geometryAnalyzer.edgeAnalyzer) {
                console.warn('⚠️ EdgeAnalyzer not available');
                return;
            }

            // Calculate edge length
            const length = edge.start.distanceTo(edge.end);

            // Calculate edge direction vector
            const direction = new THREE.Vector3().subVectors(edge.end, edge.start).normalize();

            // Create edge analysis result
            const edgeAnalysis = {
                success: true,
                basicInfo: {
                    edgeId: edge.id || 'Unknown Edge',
                    length: length.toFixed(2),
                    direction: {
                        x: direction.x.toFixed(3),
                        y: direction.y.toFixed(3),
                        z: direction.z.toFixed(3)
                    }
                },
                coordinates: {
                    start: {
                        x: edge.start.x.toFixed(2),
                        y: edge.start.y.toFixed(2),
                        z: edge.start.z.toFixed(2)
                    },
                    end: {
                        x: edge.end.x.toFixed(2),
                        y: edge.end.y.toFixed(2),
                        z: edge.end.z.toFixed(2)
                    }
                },
                type: 'edge'
            };

            // Show edge tooltip
            this.showEdgeTooltip(edgeAnalysis, event);

            console.log('✅ Edge analysis completed:', edgeAnalysis);

        } catch (error) {
            console.error('❌ Error analyzing edge geometry:', error);
        }
    }

    /**
     * Show edge analysis tooltip
     */
    showEdgeTooltip(edgeAnalysis, event) {
        console.log('🔗 Showing edge tooltip');

        // Hide any existing tooltip
        this.hideTooltip();

        // Create edge tooltip content
        const tooltipContent = this.createEdgeTooltipContent(edgeAnalysis);

        // Create tooltip element
        const tooltip = document.createElement('div');
        tooltip.id = 'geometry-analysis-tooltip';
        tooltip.className = 'geometry-tooltip edge-tooltip';
        tooltip.innerHTML = tooltipContent;

        // Find 3D viewer container
        const viewer3DContainer = this.find3DViewerContainer();
        if (viewer3DContainer) {
            if (getComputedStyle(viewer3DContainer).position === 'static') {
                viewer3DContainer.style.position = 'relative';
            }
            viewer3DContainer.appendChild(tooltip);
        } else {
            document.body.appendChild(tooltip);
        }

        // Calculate position
        const mousePosition = event ? { x: event.clientX, y: event.clientY } : this.lastMousePosition;
        const position = this.calculateTooltipPosition(mousePosition);

        // Set position
        tooltip.style.left = position.x + 'px';
        tooltip.style.top = position.y + 'px';

        // Show with animation
        setTimeout(() => {
            tooltip.classList.add('show');
        }, 10);

        // Auto-hide after 10 seconds
        this.tooltipTimeout = setTimeout(() => {
            this.hideTooltip();
        }, 10000);
    }

    /**
     * Clear face highlights from 3D viewer
     */
    clearFaceHighlights(viewer) {
        console.log('🔗 Clearing face highlights');
        console.log('🔍 Debug viewer object:', {
            viewer: !!viewer,
            highlightMeshMesh: !!viewer?.highlightMeshMesh,
            scene: !!viewer?.scene,
            sceneValue: viewer?.scene,
            sceneType: typeof viewer?.scene,
            viewerType: viewer?.constructor?.name
        });

        try {
            // Clear 3D viewer face highlights
            if (viewer && typeof viewer.clearHighlight === 'function') {
                viewer.clearHighlight();
                console.log('✅ Called viewer.clearHighlight()');
            }

            // Clear specific highlightMeshMesh from 3D viewer
            if (viewer && viewer.highlightMeshMesh) {
                console.log('🗑️ Removing highlightMeshMesh from scene');

                // Remove from scene
                if (viewer.scene && viewer.highlightMeshMesh.parent) {
                    viewer.scene.remove(viewer.highlightMeshMesh);
                }

                // Dispose geometry and material to prevent memory leaks
                if (viewer.highlightMeshMesh.geometry) {
                    viewer.highlightMeshMesh.geometry.dispose();
                }
                if (viewer.highlightMeshMesh.material) {
                    viewer.highlightMeshMesh.material.dispose();
                }

                // Clear reference
                viewer.highlightMeshMesh = null;
                console.log('✅ highlightMeshMesh cleared');
            } else {
                console.log('⚠️ No highlightMeshMesh found to clear');
            }

            // Clear any mesh material highlights
            if (viewer && viewer.selectedMeshInfo && viewer.selectedMeshInfo.object) {
                const mesh = viewer.selectedMeshInfo.object;
                if (mesh.material) {
                    // Reset material to original state
                    if (mesh.material.emissive) {
                        mesh.material.emissive.setHex(0x000000);
                    }
                    // Don't change the original color - keep the mesh's original appearance
                    // The white highlight was caused by setting color to 0xffffff
                    // Instead, restore original color if we stored it, or leave it unchanged
                    if (mesh.userData && mesh.userData.originalColor) {
                        mesh.material.color.setHex(mesh.userData.originalColor);
                    }
                    // If no original color stored, don't change the color at all
                }
            }

            // Clear any global viewer highlights
            const globalViewer = window.modelViewer || window.viewer3D;
            if (globalViewer && typeof globalViewer.clearHighlight === 'function') {
                globalViewer.clearHighlight();
            }

            // Also clear from global viewer if different
            if (globalViewer && globalViewer !== viewer && globalViewer.highlightMeshMesh) {
                console.log('🗑️ Removing highlightMeshMesh from global viewer');

                if (globalViewer.scene && globalViewer.highlightMeshMesh.parent) {
                    globalViewer.scene.remove(globalViewer.highlightMeshMesh);
                }

                if (globalViewer.highlightMeshMesh.geometry) {
                    globalViewer.highlightMeshMesh.geometry.dispose();
                }
                if (globalViewer.highlightMeshMesh.material) {
                    globalViewer.highlightMeshMesh.material.dispose();
                }

                globalViewer.highlightMeshMesh = null;
            }

            // Debug: Check for any remaining highlight objects in scene
            const scene = viewer?.scene || globalViewer?.scene;
            if (scene) {
                const highlights = [];
                scene.traverse((child) => {
                    if (child.material && child.material.color) {
                        const color = child.material.color.getHex();
                        if (color === 0x0088ff || color === 0x00aaff || color === 0x4488ff) {
                            highlights.push({
                                name: child.name || 'unnamed',
                                color: color.toString(16),
                                type: child.type,
                                material: child.material.type
                            });
                        }
                    }
                });

                if (highlights.length > 0) {
                    console.log('🔍 Found remaining blue highlights in scene:', highlights);

                    // Try to remove them
                    scene.traverse((child) => {
                        if (child.material && child.material.color) {
                            const color = child.material.color.getHex();
                            if (color === 0x0088ff || color === 0x00aaff || color === 0x4488ff) {
                                console.log('🗑️ Removing blue highlight:', child.name || 'unnamed');
                                scene.remove(child);
                                if (child.geometry) child.geometry.dispose();
                                if (child.material) child.material.dispose();
                            }
                        }
                    });
                } else {
                    console.log('✅ No blue highlights found in scene');
                }
            }

        } catch (error) {
            console.warn('⚠️ Error clearing face highlights:', error);
        }
    }

    /**
     * Clear edge highlights from 3D viewer
     */
    clearEdgeHighlights(viewer) {
        console.log('🔗 Clearing edge highlights');

        try {
            // Clear our own edge highlight mesh
            if (this.highlightEdgeMesh) {
                const scene = viewer?.scene || window.modelViewer?.scene || window.viewer3D?.scene;
                if (scene && this.highlightEdgeMesh.parent) {
                    scene.remove(this.highlightEdgeMesh);
                }

                if (this.highlightEdgeMesh.geometry) {
                    this.highlightEdgeMesh.geometry.dispose();
                }
                if (this.highlightEdgeMesh.material) {
                    this.highlightEdgeMesh.material.dispose();
                }

                this.highlightEdgeMesh = null;
                console.log('✅ Edge highlight mesh cleared');
            }

            // Clear any global viewer highlights
            const globalViewer = window.modelViewer || window.viewer3D;
            if (globalViewer && typeof globalViewer.clearHighlight === 'function') {
                globalViewer.clearHighlight();
            }

        } catch (error) {
            console.warn('⚠️ Error clearing edge highlights:', error);
        }
    }

    /**
     * Create edge tooltip content
     */
    createEdgeTooltipContent(edgeAnalysis) {
        const { basicInfo, coordinates } = edgeAnalysis;

        return `
            <div class="tooltip-header">
                <span class="tooltip-icon">🔗</span>
                <span class="tooltip-title">${basicInfo.edgeId}</span>
                <button class="tooltip-close" onclick="window.anaGeo.hideTooltip()">×</button>
            </div>
            <div class="tooltip-content">
                <div class="tooltip-section">
                    <div class="section-title">📏 Length</div>
                    <div class="section-value">${basicInfo.length} mm</div>
                </div>

                <div class="tooltip-section">
                    <div class="section-title">📍 Coordinates</div>
                    <div class="properties-grid">
                        <div class="property-item">
                            <span class="property-label">Start:</span>
                            <span class="property-value">(${coordinates.start.x}, ${coordinates.start.y}, ${coordinates.start.z})</span>
                        </div>
                        <div class="property-item">
                            <span class="property-label">End:</span>
                            <span class="property-value">(${coordinates.end.x}, ${coordinates.end.y}, ${coordinates.end.z})</span>
                        </div>
                    </div>
                </div>

                <div class="tooltip-section">
                    <div class="section-title">🧭 Direction</div>
                    <div class="section-value">(${basicInfo.direction.x}, ${basicInfo.direction.y}, ${basicInfo.direction.z})</div>
                </div>

                <div class="tooltip-section">
                    <button class="copy-coordinates-btn" onclick="window.anaGeo.copyEdgeCoordinates()">
                        📋 Copy Coordinates
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Copy edge coordinates to clipboard with custom format
     */
    copyEdgeCoordinates() {
        if (!this.selectedEdgeInfo) {
            console.warn('No edge selected to copy coordinates');
            return;
        }

        const start = this.selectedEdgeInfo.start;
        const end = this.selectedEdgeInfo.end;

        // Format coordinates as requested: "edges {( x,y,z)and (x,y,z)}"
        const coordinatesText = `edges {( ${start.x.toFixed(1)},${start.y.toFixed(1)},${start.z.toFixed(1)})and (${end.x.toFixed(1)},${end.y.toFixed(1)},${end.z.toFixed(1)})}`;

        // Copy to clipboard
        navigator.clipboard.writeText(coordinatesText).then(() => {
            console.log('✅ Edge coordinates copied to clipboard:', coordinatesText);

            // Show success feedback
            this.showCopyFeedback();
        }).catch(err => {
            console.error('❌ Failed to copy coordinates to clipboard:', err);

            // Fallback: try to select text for manual copy
            this.fallbackCopyToClipboard(coordinatesText);
        });
    }

    /**
     * Show visual feedback when coordinates are copied
     */
    showCopyFeedback() {
        const button = document.querySelector('.copy-coordinates-btn');
        if (button) {
            const originalText = button.innerHTML;
            button.innerHTML = '✅ Copied!';
            button.style.backgroundColor = '#10b981';

            setTimeout(() => {
                button.innerHTML = originalText;
                button.style.backgroundColor = '';
            }, 2000);
        }
    }

    /**
     * Fallback method for copying text when clipboard API fails
     */
    fallbackCopyToClipboard(text) {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();

        try {
            document.execCommand('copy');
            console.log('✅ Edge coordinates copied using fallback method');
            this.showCopyFeedback();
        } catch (err) {
            console.error('❌ Fallback copy also failed:', err);
        }

        document.body.removeChild(textArea);
    }

    /**
     * Paste edge coordinates to chat
     */
    pasteEdgeCoordinatesToChat() {
        if (!this.selectedEdgeInfo) {
            console.warn('No edge selected to paste to chat');
            return;
        }

        const edgeText = this.geometryAnalyzer.edgeAnalyzer.formatEdgeForChat(this.selectedEdgeInfo);

        // Get chat input element
        const userInput = document.getElementById('user-input');

        if (userInput) {
            const currentValue = userInput.value.trim();
            const newValue = currentValue ? `${currentValue} ${edgeText}` : edgeText;

            userInput.value = newValue;
            userInput.dispatchEvent(new Event('input'));
            userInput.focus();
            userInput.setSelectionRange(newValue.length, newValue.length);

            console.log('🔗 Edge coordinates pasted to chat:', edgeText);
        } else {
            console.error('Chat input element not found');
        }
    }

    /**
     * Paste face bounding box to chat
     */
    pasteFaceBoundingBoxToChat() {
        if (!this.selectedFaceInfo) {
            console.warn('No face selected to paste to chat');
            return;
        }

        const bbox = this.selectedFaceInfo.bbox;

       
        const bboxText = `face bbox X[${bbox.min.x.toFixed(3)}, ${bbox.max.x.toFixed(3)}], Y[${bbox.min.y.toFixed(3)}, ${bbox.max.y.toFixed(3)}], Z[${bbox.min.z.toFixed(3)}, ${bbox.max.z.toFixed(3)}]`;

        // Get chat input element
        const userInput = document.getElementById('user-input');

        if (userInput) {
            const currentValue = userInput.value.trim();
            const newValue = currentValue ? `${currentValue} ${bboxText}` : bboxText;

            userInput.value = newValue;
            userInput.dispatchEvent(new Event('input'));
            userInput.focus();
            userInput.setSelectionRange(newValue.length, newValue.length);

            console.log('🔲 Face bounding box pasted to chat:', bboxText);
        } else {
            console.error('Chat input element not found');
        }
    }
}



window.initAnaGeoWhenReady = async function() {
    console.log('🚀 Initializing Ana_Geo system after OBJ load...');

    try {
        // Wait for dependencies with robust retry mechanism
        const waitForDependencies = async () => {
            const maxRetries = 30; // Increased retries for slower connections
            const retryDelay = 200; // 200ms between retries
            const requiredClasses = ['SimpleFaceAnalyzer', 'EdgeAnalyzer', 'GeometryAnalyzer', 'GeometryUtils'];

            console.log('⏳ Waiting for Ana_Geo dependencies to load...');

            for (let i = 0; i < maxRetries; i++) {
                // Check if all required classes are available
                const missingClasses = requiredClasses.filter(className =>
                    typeof window[className] === 'undefined'
                );

                if (missingClasses.length === 0) {
                    console.log('✅ All Ana_Geo dependencies loaded successfully');
                    return true;
                }

                if (i % 5 === 0) { // Log every 5th attempt to avoid spam
                    console.log(`⏳ Still waiting for dependencies... (${i + 1}/${maxRetries})`);
                    console.log(`   Missing: ${missingClasses.join(', ')}`);
                }

                await new Promise(resolve => setTimeout(resolve, retryDelay));
            }

            console.error('❌ Ana_Geo dependencies failed to load after maximum retries');
            console.error(`   Missing classes: ${requiredClasses.filter(className =>
                typeof window[className] === 'undefined'
            ).join(', ')}`);
            return false;
        };

        // Wait for dependencies before proceeding
        const dependenciesReady = await waitForDependencies();
        if (!dependenciesReady) {
            console.error('❌ Cannot initialize Ana_Geo: dependencies not available');
            return false;
        }

        // Proceed with Ana_Geo initialization
        if (!window.anaGeo) {
            console.log('🔧 Creating new Ana_Geo instance...');
            // Create Ana_Geo instance
            window.anaGeo = new AnaGeo();

            // Initialize it manually
            const success = await window.anaGeo.init();

            if (success) {
                console.log('✅ Ana_Geo successfully initialized after OBJ load');
                return true;
            } else {
                console.error('❌ Ana_Geo initialization failed');
                window.anaGeo = null;
                return false;
            }
        } else {
            console.log('ℹ️ Ana_Geo instance already exists');

            // If not initialized, try to initialize
            if (!window.anaGeo.isInitialized) {
                console.log('🔄 Re-initializing existing Ana_Geo instance...');
                const success = await window.anaGeo.init();
                if (success) {
                    console.log('✅ Ana_Geo re-initialized successfully');
                    return true;
                } else {
                    console.error('❌ Ana_Geo re-initialization failed');
                    return false;
                }
            } else {
                console.log('ℹ️ Ana_Geo already initialized and ready');
                return true;
            }
        }
    } catch (error) {
        console.error('❌ Error in initAnaGeoWhenReady:', error);
        console.error('❌ Error stack:', error.stack);
        return false;
    }
};


window.ensureAnaGeoReady = function() {
    console.log('🔍 Ensuring Ana_Geo is ready...');

    // Check if Ana_Geo already exists and is initialized
    if (window.anaGeo && window.anaGeo.isInitialized) {
        console.log('✅ Ana_Geo already ready');
        return true;
    }

    // Check if dependencies are available
    const requiredClasses = ['SimpleFaceAnalyzer', 'EdgeAnalyzer', 'GeometryAnalyzer', 'GeometryUtils'];
    const missingClasses = requiredClasses.filter(className => typeof window[className] === 'undefined');

    if (missingClasses.length > 0) {
        console.warn(`⚠️ Ana_Geo dependencies not ready: ${missingClasses.join(', ')}`);
        return false;
    }

    // Create and initialize Ana_Geo if not exists
    if (!window.anaGeo) {
        console.log('🔧 Creating Ana_Geo instance...');
        window.anaGeo = new AnaGeo();
    }

    // Initialize if not initialized
    if (!window.anaGeo.isInitialized) {
        console.log('🔧 Initializing Ana_Geo...');
        window.anaGeo.init().then(success => {
            if (success) {
                console.log('✅ Ana_Geo initialized successfully');
            } else {
                console.error('❌ Ana_Geo initialization failed');
            }
        });
    }

    return true;
};

window.AnaGeo = AnaGeo;








