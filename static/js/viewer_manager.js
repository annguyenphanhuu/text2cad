class DualViewerManager {
    constructor() {
        this.objViewer = null;
        this.stepViewer = null;
        this.isInitialized = false;
        this.loadingStates = {
            obj: { loading: false, loaded: false, error: null },
            step: { loading: false, loaded: false, error: null }
        };
        this.progressCallbacks = [];
        // Track currently loading paths to prevent duplicates
        this.currentLoadingPaths = {
            obj: null,
            step: null
        };
    }

    async initialize() {
        if (this.isInitialized) return;

        this.initObjViewer();
        this.initStepViewer();

        this.isInitialized = true;
        console.log('✅ Dual viewer system initialized');
    }

    initObjViewer() {
        if (typeof ModelViewer3D !== 'undefined') {
            try {
                this.objViewer = new ModelViewer3D("viewer-3d-container");
                console.log('✅ OBJ Viewer Initialized');
            } catch (e) {
                console.error('❌ Failed to initialize OBJ Viewer:', e);
            }
        }
    }

    initStepViewer() {
        if (typeof StepModelViewer3D === 'undefined') {
            console.error('❌ StepModelViewer3D class not found');
            return;
        }

        try {
            this.stepViewer = new StepModelViewer3D("step-viewer-3d");
            // Expose globally for the viewer's own control listeners
            window.stepViewer = this.stepViewer;
            console.log('✅ STEP Viewer Initialized');
        } catch (e) {
            console.error('❌ Failed to initialize STEP Viewer:', e);
        }
    }

    // Add progress callback for real-time updates
    onProgress(callback) {
        this.progressCallbacks.push(callback);
    }

    // Notify all progress callbacks
    notifyProgress(type, state) {
        this.progressCallbacks.forEach(callback => {
            try {
                callback(type, state);
            } catch (e) {
                console.error('Progress callback error:', e);
            }
        });
    }

    // Show viewer panel immediately when content is ready
    showViewerPanel(type) {
        const panelId = type === 'obj' ? 'obj-viewer-panel' : 'step-viewer-panel';
        const panel = document.getElementById(panelId);
        if (panel) {
            panel.classList.remove('hidden');
            console.log(`✅ [DualViewerManager] ${type.toUpperCase()} viewer panel shown`);
        }
    }

    // Progressive loading - load each viewer independently
    async loadModelsProgressively(objPath, stepPath) {
        console.log(`[DualViewerManager] Starting progressive loading: OBJ='${objPath}', STEP='${stepPath}'`);

        const loadingTasks = [];

        // ═══════════════════════════════════════════════════════════
        // DUPLICATE PREVENTION - Skip if same file is already loading
        // ═══════════════════════════════════════════════════════════
        let skipOBJ = false;
        let skipSTEP = false;

        if (objPath && this.currentLoadingPaths.obj === objPath && this.loadingStates.obj.loading) {
            console.warn(`[DualViewerManager] ⚠️ Skipping duplicate OBJ load: ${objPath} (already loading)`);
            skipOBJ = true;
        }

        if (stepPath && this.currentLoadingPaths.step === stepPath && this.loadingStates.step.loading) {
            console.warn(`[DualViewerManager] ⚠️ Skipping duplicate STEP load: ${stepPath} (already loading)`);
            skipSTEP = true;
        }

        // If both are duplicates, return current states
        if (skipOBJ && skipSTEP) {
            console.log('[DualViewerManager] All models are already loading, returning current states');
            return {
                obj: this.loadingStates.obj.loaded,
                step: this.loadingStates.step.loaded
            };
        }

        // Reset loading states only for models being loaded
        if (objPath && !skipOBJ) {
            this.loadingStates.obj = { loading: false, loaded: false, error: null };
        }
        if (stepPath && !skipSTEP) {
            this.loadingStates.step = { loading: false, loaded: false, error: null };
        }

        // Load OBJ model independently
        if (objPath && !skipOBJ && this.objViewer && typeof this.objViewer.loadOBJFile === 'function') {
            this.loadingStates.obj.loading = true;
            this.currentLoadingPaths.obj = objPath; // Track loading path
            this.notifyProgress('obj', { ...this.loadingStates.obj, status: 'Loading OBJ model...' });

            loadingTasks.push(this.loadObjModel(objPath));
        } else if (objPath && !skipOBJ) {
            console.error('[DualViewerManager] OBJ viewer or loadOBJFile method is not available.');
            this.loadingStates.obj.error = 'OBJ viewer not available';
            this.currentLoadingPaths.obj = null; // Clear path on error
            this.notifyProgress('obj', this.loadingStates.obj);
        }

        // Load STEP model independently
        if (stepPath && !skipSTEP && this.stepViewer && typeof this.stepViewer.loadModel === 'function') {
            this.loadingStates.step.loading = true;
            this.currentLoadingPaths.step = stepPath; // Track loading path
            this.notifyProgress('step', { ...this.loadingStates.step, status: 'Loading STEP model...' });

            loadingTasks.push(this.loadStepModel(stepPath));
        } else if (stepPath && !skipSTEP) {
            console.error('[DualViewerManager] STEP viewer or loadModel method is not available.');
            this.loadingStates.step.error = 'STEP viewer not available';
            this.currentLoadingPaths.step = null; // Clear path on error
            this.notifyProgress('step', this.loadingStates.step);
        }

        if (loadingTasks.length === 0) {
            console.warn('[DualViewerManager] No models to load.');
            return { obj: false, step: false };
        }

        // Wait for all tasks to complete (but don't block individual viewers)
        await Promise.allSettled(loadingTasks);

        console.log('✅ [DualViewerManager] Progressive loading completed');
        return {
            obj: this.loadingStates.obj.loaded,
            step: this.loadingStates.step.loaded
        };
    }

    // Load OBJ model with immediate feedback
    async loadObjModel(objPath) {
        try {
            console.log(`[DualViewerManager] Loading OBJ model: ${objPath}`);

            const success = await this.objViewer.loadOBJFile(objPath);

            if (!success) {
                throw new Error('Failed to load OBJ model');
            }

            this.loadingStates.obj = { loading: false, loaded: true, error: null };
            this.notifyProgress('obj', { ...this.loadingStates.obj, status: 'OBJ model loaded successfully' });
            this.showViewerPanel('obj');
            console.log('✅ [DualViewerManager] OBJ model loaded and displayed');

            return success;
        } catch (error) {
            console.error('❌ [DualViewerManager] Error loading OBJ model:', error);
            this.loadingStates.obj = { loading: false, loaded: false, error: error.message };
            this.notifyProgress('obj', this.loadingStates.obj);
            return false;
        } finally {
            // Clear loading path when done (success or error) - prevents duplicate loads
            this.currentLoadingPaths.obj = null;
        }
    }

    // Load STEP model with immediate feedback
    async loadStepModel(stepPath) {
        try {
            console.log(`[DualViewerManager] Loading STEP model: ${stepPath}`);

            const success = await this.stepViewer.loadModel(stepPath);

            if (!success) {
                throw new Error('Failed to load STEP model');
            }

            this.loadingStates.step = { loading: false, loaded: true, error: null };
            this.notifyProgress('step', { ...this.loadingStates.step, status: 'STEP model loaded successfully' });
            this.showViewerPanel('step');
            console.log('✅ [DualViewerManager] STEP model loaded and displayed');

            return success;
        } catch (error) {
            console.error('❌ [DualViewerManager] Error loading STEP model:', error);
            this.loadingStates.step = { loading: false, loaded: false, error: error.message };
            this.notifyProgress('step', this.loadingStates.step);
            return false;
        } finally {
            // Clear loading path when done (success or error) - prevents duplicate loads
            this.currentLoadingPaths.step = null;
        }
    }

    // Legacy method for backward compatibility
    async loadModels(objPath, stepPath) {
        console.log('[DualViewerManager] Using legacy loadModels - redirecting to progressive loading');
        return this.loadModelsProgressively(objPath, stepPath);
    }

    // Get current loading status
    getLoadingStatus() {
        return {
            obj: { ...this.loadingStates.obj },
            step: { ...this.loadingStates.step },
            anyLoading: this.loadingStates.obj.loading || this.loadingStates.step.loading,
            allLoaded: this.loadingStates.obj.loaded && this.loadingStates.step.loaded
        };
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const dualViewer = new DualViewerManager();
    dualViewer.initialize();

    // Set up progress indicators
    const setupProgressIndicators = () => {
        // Create progress indicators for each viewer
        const createProgressIndicator = (containerId, type) => {
            const container = document.getElementById(containerId);
            if (!container) return null;

            const progressDiv = document.createElement('div');
            progressDiv.id = `${type}-loading-progress`;
            progressDiv.className = 'viewer-loading-progress hidden';
            progressDiv.innerHTML = `
                <div class="progress-content">
                    <div class="spinner"></div>
                    <div class="progress-text">Loading ${type.toUpperCase()} model...</div>
                    <div class="progress-bar">
                        <div class="progress-fill"></div>
                    </div>
                </div>
            `;

            container.appendChild(progressDiv);
            return progressDiv;
        };

        createProgressIndicator('viewer-3d-container', 'obj');
        createProgressIndicator('step-viewer-3d', 'step');
    };

    // Set up progress callback
    dualViewer.onProgress((type, state) => {
        const progressElement = document.getElementById(`${type}-loading-progress`);
        const progressText = progressElement?.querySelector('.progress-text');

        if (progressElement && progressText) {
            if (state.loading) {
                progressElement.classList.remove('hidden');
                progressText.textContent = state.status || `Loading ${type.toUpperCase()} model...`;
            } else if (state.loaded) {
                progressText.textContent = state.status || `${type.toUpperCase()} model loaded successfully`;
                setTimeout(() => {
                    progressElement.classList.add('hidden');
                }, 1500);
            } else if (state.error) {
                progressText.textContent = `Error: ${state.error}`;
                progressElement.classList.add('error');
                setTimeout(() => {
                    progressElement.classList.add('hidden');
                    progressElement.classList.remove('error');
                }, 3000);
            }
        }

        // Update global loading state
        const status = dualViewer.getLoadingStatus();
        console.log(`[Progress] ${type}: ${JSON.stringify(state)}`);
        console.log(`[Status] Overall: ${JSON.stringify(status)}`);
    });

    // Initialize progress indicators
    setupProgressIndicators();

    // Enhanced global function with progressive loading
    window.handleCADGenerationResponse = function (response) {
        console.log('[handleCADGenerationResponse] Processing response:', response);

        // Support both obj_path/obj_export and step_path/step_export for backward compatibility
        const objPath = response.obj_path || response.obj_export;
        const stepPath = response.step_path || response.step_export;

        if (objPath || stepPath) {
            console.log('[handleCADGenerationResponse] 🎯 Model paths found, loading 3D viewers...');
            // Start progressive loading immediately
            dualViewer.loadModelsProgressively(objPath, stepPath)
                .then(results => {
                    console.log('✅ [handleCADGenerationResponse] Progressive loading completed:', results);

                    // Show success notification
                    if (results.obj || results.step) {
                        const notification = document.createElement('div');
                        notification.className = 'loading-notification success';
                        notification.innerHTML = `
                            <i class="fas fa-check-circle"></i>
                            3D models loaded successfully!
                            ${results.obj ? 'OBJ ✓' : ''}
                            ${results.step ? 'STEP ✓' : ''}
                        `;
                        document.body.appendChild(notification);

                        setTimeout(() => {
                            notification.remove();
                        }, 3000);
                    }
                })
                .catch(error => {
                    console.error('❌ [handleCADGenerationResponse] Progressive loading failed:', error);
                });
        } else {
            // Check if this is expected (questions/missing info) or unexpected (error)
            const hasQuestions = response.chat_response && (
                response.chat_response.includes('?') ||
                response.chat_response.toLowerCase().includes('please') ||
                response.chat_response.toLowerCase().includes('suggestion')
            );

            if (hasQuestions) {
                console.log('[handleCADGenerationResponse] ℹ️ No model paths (expected - asking for more information)');
            } else {
                console.warn('[handleCADGenerationResponse] ⚠️ No model paths provided (unexpected):', {
                    obj_path: response.obj_path,
                    obj_export: response.obj_export,
                    step_path: response.step_path,
                    step_export: response.step_export,
                    chat_response_preview: response.chat_response ? response.chat_response.substring(0, 100) : 'N/A'
                });
            }
        }
    };

    // Add CSS styles for progress indicators
    const addProgressStyles = () => {
        if (document.getElementById('viewer-progress-styles')) return;

        const style = document.createElement('style');
        style.id = 'viewer-progress-styles';
        style.textContent = `
            .viewer-loading-progress {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(0, 0, 0, 0.8);
                color: white;
                padding: 20px;
                border-radius: 8px;
                text-align: center;
                z-index: 1000;
                min-width: 200px;
            }

            .viewer-loading-progress.hidden {
                display: none;
            }

            .viewer-loading-progress.error {
                background: rgba(220, 53, 69, 0.9);
            }

            .progress-content .spinner {
                width: 30px;
                height: 30px;
                border: 3px solid rgba(255, 255, 255, 0.3);
                border-top: 3px solid white;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin: 0 auto 10px;
            }

            .progress-text {
                font-size: 14px;
                margin-bottom: 10px;
            }

            .progress-bar {
                width: 100%;
                height: 4px;
                background: rgba(255, 255, 255, 0.3);
                border-radius: 2px;
                overflow: hidden;
            }

            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #4CAF50, #45a049);
                width: 0%;
                animation: progressPulse 2s ease-in-out infinite;
            }

            .loading-notification {
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 15px 20px;
                border-radius: 8px;
                color: white;
                font-size: 14px;
                z-index: 10000;
                animation: slideInRight 0.3s ease-out;
            }

            .loading-notification.success {
                background: linear-gradient(135deg, #4CAF50, #45a049);
            }

            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }

            @keyframes progressPulse {
                0%, 100% { width: 0%; }
                50% { width: 100%; }
            }

            @keyframes slideInRight {
                from {
                    opacity: 0;
                    transform: translateX(100%);
                }
                to {
                    opacity: 1;
                    transform: translateX(0);
                }
            }
        `;
        document.head.appendChild(style);
    };

    // Initialize styles
    addProgressStyles();

    // Expose the dual viewer globally for debugging
    window.dualViewer = dualViewer;
});
