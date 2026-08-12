/**
 * Selection Manager for ChatGPT-like Face and Edge Selection Interface
 * Manages face and edge selections as compact chips above chat input
 */

class SelectionManager {
    constructor() {
        this.selectedFaces = new Map();
        this.selectedEdges = new Map();
        this.chipContainer = null;
        this.chatInput = null;
        this.contextModal = null;
        this.autoClearAfterSend = true; // NEW: Auto-clear feature flag
        this.init();
    }

    init() {
        console.log('🎯 Initializing Face Selection Manager');
        
        // Find chat input and create chip container
        this.chatInput = document.querySelector('#user-input, .chat-input, textarea[placeholder*="message"]');
        if (!this.chatInput) {
            console.error('❌ Chat input not found');
            return;
        }

        // Create chip container above chat input
        this.createChipContainer();
        
        // Set up global reference for 3D viewers
        window.selectionManager = this;
        // Backward compatibility
        window.faceSelectionManager = this;
        
        console.log('✅ Face Selection Manager initialized');
    }

    createChipContainer() {
        // Find the chat input container
        const chatForm = this.chatInput.closest('form') || this.chatInput.parentElement;
        
        // Create chip container
        this.chipContainer = document.createElement('div');
        this.chipContainer.id = 'face-selection-chips';
        this.chipContainer.className = 'face-selection-chips-container';
        this.chipContainer.style.display = 'none'; // Hidden by default
        
        // Insert before the chat input
        chatForm.insertBefore(this.chipContainer, this.chatInput.parentElement);
        
        console.log('✅ Chip container created');
    }

    addFaceSelection(faceId, fullContext, displayName = null) {
        console.log('🎯 Adding face selection:', faceId);
        
        // Create display name
        const chipDisplayName = displayName || `Face ID[${faceId}]`;
        
        // Store face selection data
        const selectionData = {
            faceId: faceId,
            displayName: chipDisplayName,
            fullContext: fullContext,
            timestamp: Date.now()
        };
        
        this.selectedFaces.set(faceId, selectionData);
        
        // Create and add chip
        this.createFaceChip(faceId, chipDisplayName);
        
        // Show chip container
        this.chipContainer.style.display = 'block';
        
        console.log('✅ Face selection added:', chipDisplayName);
    }

    createFaceChip(faceId, displayName) {
        // Remove existing chip for this face if it exists
        this.removeFaceChip(faceId);
        
        // Create chip element
        const chip = document.createElement('div');
        chip.className = 'face-selection-chip';
        chip.dataset.faceId = faceId;
        chip.dataset.selectionType = 'face';

        // Generate hover tooltip summary
        const summary = this.generateContextSummary('face', faceId);

        chip.innerHTML = `
            <i class="fas fa-cube"></i>
            <span class="chip-text">${displayName}</span>
            <i class="fas fa-info-circle context-info-icon" title="Click to view technical details"></i>
            <button class="remove-chip" title="Remove face selection">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Add hover tooltip with summary
        chip.title = `${summary}\n\nClick for detailed technical context`;
        chip.style.cursor = 'pointer';
        
        // Add click functionality for context display
        chip.addEventListener('click', (e) => {
            // Don't trigger if clicking remove button or info icon
            if (e.target.closest('.remove-chip') || e.target.closest('.context-info-icon')) {
                return;
            }
            e.preventDefault();
            this.showContextModal('face', faceId);
        });

        // Add remove functionality
        const removeBtn = chip.querySelector('.remove-chip');
        removeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation(); // Prevent triggering chip click
            this.removeFaceSelection(faceId);
        });

        // Add info icon click functionality
        const infoIcon = chip.querySelector('.context-info-icon');
        infoIcon.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.showContextModal('face', faceId);
        });

        // Add to container
        this.chipContainer.appendChild(chip);
    }

    removeFaceSelection(faceId) {
        console.log('🗑️ Removing face selection:', faceId);
        
        // Remove from data
        this.selectedFaces.delete(faceId);
        
        // Remove chip from DOM
        this.removeFaceChip(faceId);
        
        // Hide container if no selections
        if (this.selectedFaces.size === 0) {
            this.chipContainer.style.display = 'none';
        }
        
        console.log('✅ Face selection removed');
    }

    removeFaceChip(faceId) {
        const existingChip = this.chipContainer.querySelector(`[data-face-id="${faceId}"]`);
        if (existingChip) {
            existingChip.remove();
        }
    }

    // NEW: Add edge selection functionality
    addEdgeSelection(edgeId, fullContext, displayName = null) {
        console.log('🎯 Adding edge selection:', edgeId);

        // Create display name
        const chipDisplayName = displayName || `Edge ID[${edgeId}]`;

        // Store edge selection data
        const selectionData = {
            edgeId: edgeId,
            displayName: chipDisplayName,
            fullContext: fullContext,
            timestamp: Date.now()
        };

        this.selectedEdges.set(edgeId, selectionData);

        // Create and add chip
        this.createEdgeChip(edgeId, chipDisplayName);

        // Show chip container
        this.chipContainer.style.display = 'block';

        console.log('✅ Edge selection added:', chipDisplayName);
    }

    createEdgeChip(edgeId, displayName) {
        // Remove existing chip for this edge if it exists
        this.removeEdgeChip(edgeId);

        // Create chip element
        const chip = document.createElement('div');
        chip.className = 'face-selection-chip edge-selection-chip';
        chip.dataset.edgeId = edgeId;
        chip.dataset.selectionType = 'edge';

        // Generate hover tooltip summary
        const summary = this.generateContextSummary('edge', edgeId);

        chip.innerHTML = `
            <i class="fas fa-minus"></i>
            <span class="chip-text">${displayName}</span>
            <i class="fas fa-info-circle context-info-icon" title="Click to view technical details"></i>
            <button class="remove-chip" title="Remove edge selection">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Add hover tooltip with summary
        chip.title = `${summary}\n\nClick for detailed technical context`;
        chip.style.cursor = 'pointer';

        // Add click functionality for context display
        chip.addEventListener('click', (e) => {
            // Don't trigger if clicking remove button or info icon
            if (e.target.closest('.remove-chip') || e.target.closest('.context-info-icon')) {
                return;
            }
            e.preventDefault();
            this.showContextModal('edge', edgeId);
        });

        // Add remove functionality
        const removeBtn = chip.querySelector('.remove-chip');
        removeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation(); // Prevent triggering chip click
            this.removeEdgeSelection(edgeId);
        });

        // Add info icon click functionality
        const infoIcon = chip.querySelector('.context-info-icon');
        infoIcon.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.showContextModal('edge', edgeId);
        });

        // Add to container
        this.chipContainer.appendChild(chip);
    }

    removeEdgeSelection(edgeId) {
        console.log('🗑️ Removing edge selection:', edgeId);

        // Remove from data
        this.selectedEdges.delete(edgeId);

        // Remove chip from DOM
        this.removeEdgeChip(edgeId);

        // Hide container if no selections
        if (this.selectedFaces.size === 0 && this.selectedEdges.size === 0) {
            this.chipContainer.style.display = 'none';
        }

        console.log('✅ Edge selection removed');
    }

    removeEdgeChip(edgeId) {
        const existingChip = this.chipContainer.querySelector(`[data-edge-id="${edgeId}"]`);
        if (existingChip) {
            existingChip.remove();
        }
    }

    clearAllSelections() {
        console.log('🧹 Clearing all selections');

        this.selectedFaces.clear();
        this.selectedEdges.clear();
        this.chipContainer.innerHTML = '';
        this.chipContainer.style.display = 'none';

        console.log('✅ All selections cleared');
    }

    getSelectedFaces() {
        return Array.from(this.selectedFaces.values());
    }

    getSelectedEdges() {
        return Array.from(this.selectedEdges.values());
    }

    getAllSelections() {
        return {
            faces: this.getSelectedFaces(),
            edges: this.getSelectedEdges()
        };
    }

    hasSelections() {
        return this.selectedFaces.size > 0 || this.selectedEdges.size > 0;
    }

    // Generate context string for AI processing
    generateContextString() {
        if (!this.hasSelections()) {
            return '';
        }

        const contexts = [];

        // Add face contexts
        Array.from(this.selectedFaces.values()).forEach(selection => {
            contexts.push(`[FACE_CONTEXT: ${selection.fullContext}]`);
        });

        // Add edge contexts
        Array.from(this.selectedEdges.values()).forEach(selection => {
            contexts.push(`[EDGE_CONTEXT: ${selection.fullContext}]`);
        });

        return ' ' + contexts.join(' ');
    }

    // Hook into chat submission to inject context
    interceptChatSubmission(originalMessage) {
        if (!this.hasSelections()) {
            return originalMessage;
        }

        const contextString = this.generateContextString();
        const enhancedMessage = originalMessage + contextString;

        console.log('🔄 Injecting selection context into message');
        console.log('Original:', originalMessage);
        console.log('Enhanced:', enhancedMessage);

        // NEW: Auto-clear selections after successful submission
        if (this.autoClearAfterSend) {
            // Use setTimeout to allow the message to be processed first
            setTimeout(() => {
                this.clearAllSelectionsWithAnimation();
            }, 100);
        }

        return enhancedMessage;
    }

    // Method for 3D viewers to call when face is selected
    onFaceSelected(faceId, analysisResult) {
        console.log('🎯 Face selected from 3D viewer:', faceId);

        // Generate full context string (similar to current system)
        const fullContext = this.generateFullContextFromAnalysis(faceId, analysisResult);

        // Add as chip
        this.addFaceSelection(faceId, fullContext);

        // Focus chat input for user convenience
        if (this.chatInput) {
            this.chatInput.focus();
        }
    }

    // NEW: Method for 3D viewers to call when edge is selected
    onEdgeSelected(edgeId, edgeAnalysisResult) {
        console.log('🎯 Edge selected from 3D viewer:', edgeId);

        // Generate full context string for edge
        const fullContext = this.generateFullContextFromEdgeAnalysis(edgeId, edgeAnalysisResult);

        // Add as chip
        this.addEdgeSelection(edgeId, fullContext);

        // Focus chat input for user convenience
        if (this.chatInput) {
            this.chatInput.focus();
        }
    }

    generateFullContextFromAnalysis(faceId, analysisResult) {
        console.log('🔍 [FACE_CONTEXT] Generating face context from analysis (Updated format)');

        const { faceIdInfo, detailedAnalysis } = analysisResult;
        const realFaceId = faceIdInfo?.realFaceId || faceId;

        // Extract analysis data
        const rawFaceType = detailedAnalysis?.type || 'Unknown Face';
        const faceType = String(rawFaceType).toLowerCase().includes('cylindrical') ? 'Hole' : rawFaceType;
        const properties = detailedAnalysis?.properties || {};
        const boundingBox = properties.boundingBox || {};

        // Format bounding box information
        const min = boundingBox.min || { x: 0, y: 0, z: 0 };
        const max = boundingBox.max || { x: 0, y: 0, z: 0 };
        const size = boundingBox.size || { x: 0, y: 0, z: 0 };

        // Calculate center point
        const center = {
            x: (min.x + max.x) / 2,
            y: (min.y + max.y) / 2,
            z: (min.z + max.z) / 2
        };

        // Determine face position description (enhanced logic)
        let positionDesc = '';

        // Vertical position
        if (center.z > 20) positionDesc += 'top-';
        else if (center.z < -20) positionDesc += 'bottom-';

        // Depth position
        if (center.y > 20) positionDesc += 'back-';
        else if (center.y < -20) positionDesc += 'front-';

        // Horizontal position
        if (center.x > 20) positionDesc += 'right';
        else if (center.x < -20) positionDesc += 'left';
        else positionDesc += 'center';

        // Clean up position description
        positionDesc = positionDesc.replace(/^-+|-+$/g, '').replace(/-+/g, '-');
        if (!positionDesc) positionDesc = 'center';
        positionDesc += ' face';

        // Determine geometry characteristics
        const area = Math.abs(size.x * size.y + size.y * size.z + size.x * size.z) * 2;
        let geometryDesc = area > 2000 ? 'large' : area > 500 ? 'medium' : 'small';

        // Determine shape characteristics
        const maxDim = Math.max(size.x, size.y, size.z);
        const minDim = Math.min(size.x || 0.1, size.y || 0.1, size.z || 0.1);
        const aspectRatio = maxDim / minDim;

        if (aspectRatio > 4) geometryDesc = 'elongated, ' + geometryDesc;
        else if (aspectRatio < 1.5) geometryDesc = 'square, ' + geometryDesc;

        // Estimate vertex count (more realistic)
        let estimatedVertices = 4; // minimum for a face
        if (faceType.includes('Hole')) estimatedVertices = Math.max(8, Math.floor(area / 50));
        else if (faceType.includes('Planar')) estimatedVertices = Math.max(4, Math.floor(area / 100));
        else estimatedVertices = Math.max(6, Math.floor(area / 75));

        geometryDesc += `, ${estimatedVertices}vertices`;

        // Determine orientation context (enhanced)
        let orientationContext = 'general surface';
        const tolerance = 1.0;

        if (Math.abs(size.x) < tolerance) orientationContext = 'X-aligned plane';
        else if (Math.abs(size.y) < tolerance) orientationContext = 'Y-aligned plane';
        else if (Math.abs(size.z) < tolerance) orientationContext = 'Z-aligned plane';
        else if (faceType.includes('Hole')) orientationContext = 'hole surface';
        else if (faceType.includes('Spherical')) orientationContext = 'spherical surface';

        // Build context parts in the desired format
        const contextParts = [
            `ID[${realFaceId}]`,
            `Type[${faceType}]`,
            `Position[${positionDesc}, center(${center.x.toFixed(1)}, ${center.y.toFixed(1)}, ${center.z.toFixed(1)})]`,
            `BBox[Min(${min.x.toFixed(1)}, ${min.y.toFixed(1)}, ${min.z.toFixed(1)}) Max(${max.x.toFixed(1)}, ${max.y.toFixed(1)}, ${max.z.toFixed(1)}) Size(${size.x.toFixed(1)}, ${size.y.toFixed(1)}, ${size.z.toFixed(1)})]`,
            `Geometry[${geometryDesc}]`,
            `Context[${orientationContext}]`
        ];

        return contextParts.join(' ');
    }

    // NEW: Generate full context from edge analysis result
    generateFullContextFromEdgeAnalysis(edgeId, edgeAnalysisResult) {
        console.log('🔍 [EDGE_CONTEXT] Generating edge context from analysis');

        const { start, end, length, type } = edgeAnalysisResult;

        // Format coordinates
        const startCoords = `(${start.x.toFixed(1)}, ${start.y.toFixed(1)}, ${start.z.toFixed(1)})`;
        const endCoords = `(${end.x.toFixed(1)}, ${end.y.toFixed(1)}, ${end.z.toFixed(1)})`;
        const edgeLength = length.toFixed(1);

        // Calculate edge properties
        const edgeVector = {
            x: end.x - start.x,
            y: end.y - start.y,
            z: end.z - start.z
        };

        // Determine edge orientation
        let orientation = 'diagonal';
        const tolerance = 0.1;
        if (Math.abs(edgeVector.x) < tolerance && Math.abs(edgeVector.y) < tolerance) {
            orientation = 'Z-aligned';
        } else if (Math.abs(edgeVector.x) < tolerance && Math.abs(edgeVector.z) < tolerance) {
            orientation = 'Y-aligned';
        } else if (Math.abs(edgeVector.y) < tolerance && Math.abs(edgeVector.z) < tolerance) {
            orientation = 'X-aligned';
        }

        // Generate comprehensive edge context
        const contextParts = [
            `ID[${edgeId}]`,
            `Type[${type || 'Linear Edge'}]`,
            `Start${startCoords}`,
            `End${endCoords}`,
            `Length[${edgeLength}]`,
            `Orientation[${orientation}]`
        ];

        return contextParts.join(' ');
    }

    // NEW: Generate context summary for hover tooltips (Updated for new format)
    generateContextSummary(type, id) {
        if (type === 'face') {
            const selection = this.selectedFaces.get(id);
            if (!selection) return 'Face details unavailable';

            // Extract key info from full context for summary
            const context = selection.fullContext;

            // Parse the "Face Selection:" format
            const typeMatch = context.match(/Type\[([^\]]+)\]/);
            const positionMatch = context.match(/Position\[([^\]]+)\]/);
            const sizeMatch = context.match(/Size\(([^)]+)\)/);
            const geometryMatch = context.match(/Geometry\[([^\]]+)\]/);
            const contextMatch = context.match(/Context\[([^\]]+)\]/);

            const faceType = typeMatch ? typeMatch[1].replace(/\s*\(\d+%\)/, '') : 'Unknown'; // Remove confidence percentage
            const position = positionMatch ? positionMatch[1] : 'unknown position';
            const size = sizeMatch ? sizeMatch[1] : 'unknown size';
            const geometry = geometryMatch ? geometryMatch[1] : '';
            const contextInfo = contextMatch ? contextMatch[1] : '';

            // Create concise summary
            let summary = `${faceType}`;

            // Add position if meaningful
            if (position !== 'unknown position' && !position.includes('center(')) {
                // Extract just the position part before center coordinates
                const positionPart = position.split(',')[0] || position;
                if (positionPart && positionPart !== 'center') {
                    summary += ` at ${positionPart}`;
                }
            }

            // Add dimensions if available
            if (size !== 'unknown size') {
                const dimensions = size.split(', ');
                const nonZeroDims = dimensions.filter(d => parseFloat(d) > 0.1);
                if (nonZeroDims.length > 0) {
                    summary += `, ${nonZeroDims.join('×')}mm`;
                }
            }

            // Add context info if meaningful
            if (contextInfo && contextInfo !== 'general surface' && contextInfo !== 'internal face' && contextInfo !== 'calculated' && contextInfo !== 'analyzed') {
                summary += `, ${contextInfo}`;
            }

            return summary;
        } else if (type === 'edge') {
            const selection = this.selectedEdges.get(id);
            if (!selection) return 'Edge details unavailable';

            // Extract key info from full context for summary
            const context = selection.fullContext;
            const typeMatch = context.match(/Type\[([^\]]+)\]/);
            const lengthMatch = context.match(/Length\[([^\]]+)\]/);
            const orientationMatch = context.match(/Orientation\[([^\]]+)\]/);

            const edgeType = typeMatch ? typeMatch[1] : 'Unknown';
            const length = lengthMatch ? lengthMatch[1] : 'Unknown length';
            const orientation = orientationMatch ? orientationMatch[1] : '';

            return `${edgeType}, ${length}mm${orientation ? ', ' + orientation : ''}`;
        }

        return 'Details unavailable';
    }

    // NEW: Show context modal with detailed technical information
    showContextModal(type, id) {
        console.log(`🔍 Showing context modal for ${type}:`, id);

        let selection, contextData;
        if (type === 'face') {
            selection = this.selectedFaces.get(id);
            contextData = selection ? selection.fullContext : 'Context unavailable';
        } else if (type === 'edge') {
            selection = this.selectedEdges.get(id);
            contextData = selection ? selection.fullContext : 'Context unavailable';
        }

        if (!selection) {
            console.error('❌ Selection not found for context modal');
            return;
        }

        // Create modal if it doesn't exist
        if (!this.contextModal) {
            this.createContextModal();
        }

        // Update modal content
        const modalTitle = document.getElementById('context-modal-title');
        const modalContent = document.getElementById('context-modal-content');
        const modalSummary = document.getElementById('context-modal-summary');

        modalTitle.textContent = `${type.charAt(0).toUpperCase() + type.slice(1)} Technical Context`;
        modalSummary.textContent = this.generateContextSummary(type, id);
        modalContent.textContent = contextData;

        // Show modal
        this.contextModal.style.display = 'flex';
        document.body.style.overflow = 'hidden'; // Prevent background scrolling

        // Focus on close button for accessibility
        const closeBtn = document.getElementById('context-modal-close');
        if (closeBtn) closeBtn.focus();
    }

    // NEW: Create context modal element
    createContextModal() {
        const modal = document.createElement('div');
        modal.id = 'selection-context-modal';
        modal.className = 'context-modal-overlay';

        modal.innerHTML = `
            <div class="context-modal-content">
                <div class="context-modal-header">
                    <h3 id="context-modal-title">Technical Context</h3>
                    <button id="context-modal-close" class="context-modal-close" aria-label="Close modal">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="context-modal-body">
                    <div class="context-summary">
                        <h4><i class="fas fa-eye"></i> What You See:</h4>
                        <p id="context-modal-summary" class="summary-text"></p>
                    </div>
                    <div class="context-details">
                        <h4><i class="fas fa-robot"></i> What AI Receives:</h4>
                        <pre id="context-modal-content" class="technical-content"></pre>
                    </div>
                </div>
                <div class="context-modal-footer">
                    <p class="context-note">
                        <i class="fas fa-info-circle"></i>
                        This technical context is automatically included when you send messages to the AI.
                    </p>
                </div>
            </div>
        `;

        // Add event listeners
        const closeBtn = modal.querySelector('#context-modal-close');
        const overlay = modal;

        closeBtn.addEventListener('click', () => this.hideContextModal());
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.hideContextModal();
        });

        // Keyboard support
        modal.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.hideContextModal();
        });

        document.body.appendChild(modal);
        this.contextModal = modal;
    }

    // NEW: Hide context modal
    hideContextModal() {
        if (this.contextModal) {
            this.contextModal.style.display = 'none';
            document.body.style.overflow = ''; // Restore scrolling
        }
    }

    // NEW: Clear selections with smooth animation
    clearAllSelectionsWithAnimation() {
        console.log('🧹 Clearing all selections with animation');

        if (!this.hasSelections()) return;

        // Add fade-out animation to all chips
        const chips = this.chipContainer.querySelectorAll('.face-selection-chip');
        chips.forEach((chip, index) => {
            chip.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            chip.style.opacity = '0';
            chip.style.transform = 'translateY(-10px)';

            // Remove chip after animation
            setTimeout(() => {
                if (chip.parentNode) {
                    chip.remove();
                }
            }, 300 + (index * 50)); // Stagger the removal
        });

        // Clear data and hide container after all animations
        setTimeout(() => {
            this.selectedFaces.clear();
            this.selectedEdges.clear();
            this.chipContainer.innerHTML = '';
            this.chipContainer.style.display = 'none';

            // Show brief confirmation
            this.showClearConfirmation();

            console.log('✅ All selections cleared with animation');
        }, 300 + (chips.length * 50) + 100);
    }

    // NEW: Show brief confirmation of clearing
    showClearConfirmation() {
        // Create temporary notification
        const notification = document.createElement('div');
        notification.className = 'clear-confirmation';
        notification.innerHTML = `
            <i class="fas fa-check-circle"></i>
            <span>Selections cleared</span>
        `;

        // Position near chip container
        if (this.chipContainer && this.chipContainer.parentNode) {
            this.chipContainer.parentNode.insertBefore(notification, this.chipContainer);
        } else {
            document.body.appendChild(notification);
        }

        // Auto-remove after 2 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                notification.style.opacity = '0';
                setTimeout(() => {
                    if (notification.parentNode) {
                        notification.remove();
                    }
                }, 300);
            }
        }, 2000);
    }

    // NEW: Toggle auto-clear feature
    setAutoClearAfterSend(enabled) {
        this.autoClearAfterSend = enabled;
        console.log(`🔄 Auto-clear after send: ${enabled ? 'enabled' : 'disabled'}`);
    }
}

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (!window.selectionManager) {
        window.selectionManager = new SelectionManager();
        // Backward compatibility
        window.faceSelectionManager = window.selectionManager;
    }
});

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SelectionManager;
}
