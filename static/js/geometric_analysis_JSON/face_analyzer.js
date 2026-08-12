/**
 * Enhanced Face Analyzer for Tessellated JSON Data
 *
 * Advanced geometric analysis with cylindrical face and fillet detection
 * Ported from SimpleFaceAnalyzer.js OBJ implementation
 */
class FaceAnalyzer {
    constructor() {
        this.tolerance = 0.1;
        this.cylindricalThreshold = 0.4;
        this.filletThreshold = 0.4;

        // OPTIMIZED THRESHOLDS AND WEIGHTS (ported from OBJ analyzer)
        this.thresholds = {
            // Aspect ratio thresholds
            highAspectRatio: 8.0,        // Strong cylinder indicator
            extremeAspectRatio: 12.0,    // Very strong cylinder indicator
            lowAspectRatio: 4.5,         // More flexible fillet threshold

            // Vertex count thresholds
            highVertexCount: 500,        // Dense tessellation (often cylinders)
            lowVertexCount: 100,         // Sparse tessellation (often fillets)

            // Dimension pattern thresholds
            compactnessThreshold: 0.15,  // smallest/largest ratio
            mediumPositionThreshold: 0.85, // (medium-smallest)/(largest-smallest)
            dimensionSpreadThreshold: 0.6, // (largest-smallest)/largest

            // Scoring weights
            primaryDiscriminatorWeight: 0.6,
            secondaryDiscriminatorWeight: 0.25,
            metadataBoostWeight: 0.4
        };

        console.log('🔥 Enhanced FaceAnalyzer initialized with cylindrical/fillet detection');
    }

    /**
     * Determine face orientation based on face normal vector AND position relative to optimal center
     * Maps to FreeCAD Z-up coordinate system labels
     * 🔥 ENHANCED: Now shape-aware - considers shape_type and optimal center
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} options - Optional parameters
     * @param {string} options.shapeType - Shape type (l-shape, tube, box, etc.)
     * @param {THREE.Vector3} options.optimalCenter - Optimal center point for the shape
     * @returns {string} Orientation label (TOP/FRONT/BOTTOM/LEFT/RIGHT/REAR)
     */
    getFaceOrientation(vertices, options = {}) {
        if (!vertices || vertices.length < 3) {
            return 'Unknown';
        }

        // Calculate face normal using first 3 vertices
        const v1 = vertices[0];
        const v2 = vertices[1];
        const v3 = vertices[2];

        // Calculate two edge vectors
        const edge1 = new THREE.Vector3().subVectors(v2, v1);
        const edge2 = new THREE.Vector3().subVectors(v3, v1);

        // Calculate normal via cross product
        const normal = new THREE.Vector3().crossVectors(edge1, edge2).normalize();

        // Calculate face center (average of all vertices)
        const faceCenter = new THREE.Vector3();
        vertices.forEach(v => faceCenter.add(v));
        faceCenter.divideScalar(vertices.length);

        // 🔥 NEW: If we have shape type and optimal center, calculate relative orientation
        if (options.shapeType && options.optimalCenter) {
            return this.getRelativeOrientation(normal, faceCenter, options.optimalCenter, options.shapeType);
        }

        // Fallback: Absolute orientation based on world coordinates
        return this.getAbsoluteOrientation(normal);
    }

    /**
     * Get absolute orientation based on face normal (original logic)
     * @param {THREE.Vector3} normal - Face normal vector
     * @returns {string} Orientation label
     */
    getAbsoluteOrientation(normal) {
        // Determine primary axis (X, Y, or Z)
        const absX = Math.abs(normal.x);
        const absY = Math.abs(normal.y);
        const absZ = Math.abs(normal.z);

        // FreeCAD Z-up mapping:
        // X-axis = LEFT/RIGHT (Red)
        // Y-axis = FRONT/REAR (Green)
        // Z-axis = TOP/BOTTOM (Blue)

        if (absZ > absX && absZ > absY) {
            // Z-axis dominant
            return normal.z > 0 ? 'TOP' : 'BOTTOM';
        } else if (absY > absX && absY > absZ) {
            // Y-axis dominant
            // FRONT is -Y, REAR is +Y (FreeCAD convention)
            return normal.y > 0 ? 'REAR' : 'FRONT';
        } else {
            // X-axis dominant
            return normal.x > 0 ? 'RIGHT' : 'LEFT';
        }
    }

    /**
     * 🔥 NEW: Get orientation relative to optimal center (shape-aware)
     * @param {THREE.Vector3} normal - Face normal vector
     * @param {THREE.Vector3} faceCenter - Center of the face
     * @param {THREE.Vector3} optimalCenter - Optimal center of the shape
     * @param {string} shapeType - Shape type (l-shape, tube, box, etc.)
     * @returns {string} Orientation label
     */
    getRelativeOrientation(normal, faceCenter, optimalCenter, shapeType) {
        // Use position relative to optimal center to determine orientation
        // This is shape-aware: a face BELOW the center = BOTTOM, ABOVE = TOP
        const toFace = new THREE.Vector3().subVectors(faceCenter, optimalCenter);

        // Determine primary axis based on normal
        const absX = Math.abs(normal.x);
        const absY = Math.abs(normal.y);
        const absZ = Math.abs(normal.z);

        // For each axis, check if face is on positive or negative side of center
        if (absZ > absX && absZ > absY) {
            // Z-axis dominant - check if face is above or below center
            return toFace.z > 0 ? 'TOP' : 'BOTTOM';
        } else if (absY > absX && absY > absZ) {
            // Y-axis dominant - check if face is in front or behind center
            return toFace.y > 0 ? 'REAR' : 'FRONT';
        } else {
            // X-axis dominant - check if face is left or right of center
            return toFace.x > 0 ? 'RIGHT' : 'LEFT';
        }
    }

    /**
     * 🔥 ENHANCED: Analyze with feature metadata support (countersink, chamfer, etc.)
     * This method prioritizes feature metadata over geometry analysis for higher accuracy
     * @param {THREE.Vector3[]} vertices - An array of THREE.Vector3 objects.
     * @param {object} faceIdInfo - Face metadata from OnShape JSON
     * @param {Array} features - Array of features from JSON (countersink, chamfer, etc.)
     * @param {object} shapeOptions - Shape-aware options for orientation calculation
     * @param {string} shapeOptions.shapeType - Shape type (l-shape, tube, box, etc.)
     * @param {THREE.Vector3} shapeOptions.optimalCenter - Optimal center point
     * @returns {object} Comprehensive analysis results
     */
    analyzeWithFeatures(vertices, faceIdInfo = {}, features = [], shapeOptions = {}) {
        if (!vertices || vertices.length < 3) {
            return { error: 'Insufficient vertices for analysis.', faceIdInfo };
        }

        // Calculate face orientation with shape-aware options
        const faceOrientation = this.getFaceOrientation(vertices, shapeOptions);

        // 🔥 PRIORITY 1: JSON features (oblong, countersink) ALWAYS override geometry analysis
        // This ensures metadata-driven features take precedence over heuristic detection
        const matchedFeature = this.findMatchingFeature(faceIdInfo.realFaceId, features);

        if (matchedFeature) {
            console.log(`✅ [JSON OVERRIDE] Found ${matchedFeature.type} feature - IGNORING geometry analysis`);
            const result = this.analyzeFeature(matchedFeature, vertices, faceIdInfo);
            // Add orientation to result
            result.faceOrientation = faceOrientation;
            return result;
        }

        // PRIORITY 2: Fallback to geometry-based analysis ONLY if no JSON feature found
        // Pass features array to analyze for context (to detect if cylindrical surface is a hole)
        const result = this.analyze(vertices, faceIdInfo, features);
        // Add orientation to result
        result.faceOrientation = faceOrientation;
        return result;
    }

    /**
     * Find matching feature for a given face ID
     * @param {string} faceId - Face ID to match
     * @param {Array} features - Array of features
     * @returns {object|null} Matched feature or null
     */
    findMatchingFeature(faceId, features) {
        if (!features || !Array.isArray(features) || !faceId) {
            // console.log(`[DEBUG] findMatchingFeature: Invalid input - faceId=${faceId}, features=${features?.length || 0}`);
            return null;
        }

        // console.log(`[DEBUG] findMatchingFeature: Searching for faceId="${faceId}" in ${features.length} features`);

        const matched = features.find(feature => {
            // 🔥 SPECIAL HANDLING FOR BENDING: Check inner.face_ids and outer.face_ids
            if (feature.type === 'bending') {
                const innerFaceIds = feature.inner?.face_ids || [];
                const outerFaceIds = feature.outer?.face_ids || [];
                const allBendingFaceIds = [...innerFaceIds, ...outerFaceIds];
                const includes = allBendingFaceIds.includes(faceId);

                // console.log(`[DEBUG]   - ${feature.type}: inner=${innerFaceIds.join(', ')}, outer=${outerFaceIds.join(', ')} | includes="${faceId}"? ${includes}`);
                return includes;
            }

            // Regular feature matching (countersink, oblong, hole, etc.)
            const hasFaceIds = feature.face_ids && Array.isArray(feature.face_ids);
            const includes = hasFaceIds && feature.face_ids.includes(faceId);

            // Removed verbose logging
            // if (hasFaceIds) {
            //     console.log(`[DEBUG]   - ${feature.type}: face_ids=${feature.face_ids.join(', ')} | includes="${faceId}"? ${includes}`);
            // }

            return includes;
        });

        if (matched) {
            console.log(`✅ Found ${matched.type} feature for face ${faceId}`);
        }
        // Only log if no match (for debugging)
        // else {
        //     console.log(`❌ No match found for faceId="${faceId}"`);
        // }

        return matched;
    }

    /**
     * Analyze a known feature (countersink, chamfer, etc.)
     * @param {object} feature - Feature metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeFeature(feature, vertices, faceIdInfo) {
        const featureType = feature.type.toLowerCase();

        console.log(`🎯 [FEATURE ANALYSIS] Analyzing ${featureType} feature`);

        // Calculate bounding box for geometry info
        const bbox = this.calculateBoundingBox(vertices);

        switch (featureType) {
            case 'countersink':
                return this.analyzeCountersink(feature, vertices, bbox, faceIdInfo);

            case 'chamfer':
                return this.analyzeChamfer(feature, vertices, bbox, faceIdInfo);

            case 'hole':
                // Check if this is a threaded hole
                // Priority: subtype === 'threaded' > is_threaded flag > thread field
                // This ensures holes with subtype='threaded' but thread=null are still detected
                const isThreadedHole = feature.subtype === 'threaded' ||
                    feature.is_threaded === true ||
                    (feature.thread && feature.thread !== null);

                if (isThreadedHole) {
                    return this.analyzeThreadedHole(feature, vertices, bbox, faceIdInfo);
                }

                // ⚠️ WARNING: Single-face holes might be artifacts from oblong detection
                // If this hole has only 1 face and there are oblongs in the model,
                // it's likely a duplicate that should have been removed by deduplication
                const faceCount = feature.face_ids ? feature.face_ids.length : 0;
                if (faceCount === 1) {
                    console.warn(`⚠️ [SUSPICIOUS] Single-face hole detected - might be part of an oblong. Regenerate JSON with latest step_converter!`);
                }

                // Regular hole - fall through to geometry analysis
                console.log('📍 Regular (non-threaded) hole detected, using geometry analysis');
                // Pass features array for context (though we already know it's a hole from feature)
                return this.analyze(vertices, faceIdInfo, [feature]);

            case 'oblong':
                return this.analyzeOblong(feature, vertices, bbox, faceIdInfo);

            case 'rectangular':
                return this.analyzeRectangularFace(feature, vertices, bbox, faceIdInfo);

            case 'square':
                return this.analyzeSquareFace(feature, vertices, bbox, faceIdInfo);

            case 'square_hole':
            case 'rectangular_hole':
                return this.analyzeBoxHole(feature, vertices, bbox, faceIdInfo);

            case 'bending':
                return this.analyzeBending(feature, vertices, bbox, faceIdInfo);

            default:
                console.warn(`⚠️ Unknown feature type: ${featureType}, falling back to geometry analysis`);
                // Pass features array for context
                return this.analyze(vertices, faceIdInfo, [feature]);
        }
    }

    /**
     * Analyze countersink feature
     * Countersink consists of: conical surface (cone) + cylindrical hole (cylinder)
     * @param {object} feature - Countersink metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeCountersink(feature, vertices, bbox, faceIdInfo) {
        console.log('🔩 [COUNTERSINK] Analyzing countersink feature:', feature);

        // Extract countersink parameters
        const diameterTop = feature.diameter_top || 0;
        const diameterBottom = feature.diameter_bottom || 0;
        const angle = feature.angle || 90;
        const depth = feature.depth || 0;
        const position = feature.position || { x: 0, y: 0, z: 0 };
        const axis = feature.axis || { x: 0, y: 0, z: -1 };

        // Determine sub-type: cone or cylinder
        // This is a simplified heuristic - you may need to enhance this
        const subType = this.detectCountersinkSubType(vertices, bbox, diameterTop, diameterBottom);

        return {
            type: 'countersink',
            subType: subType, // 'cone' or 'cylinder'
            shape: `Countersink (${subType})`,
            confidence: 0.95, // High confidence from feature metadata
            calculatedProperties: {
                'Surface': subType === 'cone' ? 'Conical' : 'Cylindrical',
                'Top Diameter': `${diameterTop.toFixed(2)} mm`,
                'Bottom Diameter': `${diameterBottom.toFixed(2)} mm`,
                'Angle': `${angle.toFixed(1)}°`,
                'Depth': `${depth.toFixed(2)} mm`,
                'Position': `(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)})`
            },
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo
        };
    }

    /**
     * Detect countersink sub-type (cone vs cylinder)
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {number} diameterTop - Top diameter
     * @param {number} diameterBottom - Bottom diameter
     * @returns {string} 'cone' or 'cylinder'
     */
    detectCountersinkSubType(vertices, bbox, diameterTop, diameterBottom) {
        // Method 1: Check if diameter changes (cone has different top/bottom)
        const diameterDiff = Math.abs(diameterTop - diameterBottom);

        if (diameterDiff > 0.1) {
            // Significant diameter difference suggests conical surface
            return 'cone';
        }

        // Method 2: Analyze geometry aspect ratio
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const aspectRatio = dims[0] / dims[2];

        // Cylindrical holes typically have high aspect ratio (length >> diameter)
        if (aspectRatio > 3.0) {
            return 'cylinder';
        }

        // Method 3: Vertex distribution analysis
        // Conical surfaces tend to have more vertices for the taper
        if (vertices.length > 100) {
            return 'cone';
        }

        // Default: assume cylinder for small features
        return 'cylinder';
    }

    /**
     * Analyze chamfer feature (placeholder for future implementation)
     * @param {object} feature - Chamfer metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeChamfer(feature, vertices, bbox, faceIdInfo) {
        console.log('📐 [CHAMFER] Analyzing chamfer feature:', feature);

        return {
            type: 'chamfer',
            shape: 'Chamfer',
            confidence: 0.95,
            calculatedProperties: {
                'Feature Type': 'Chamfer',
                'Details': 'Chamfer analysis coming soon...',
                'Vertex Count': vertices.length
            },
            geometry: {
                bbox,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo
        };
    }

    /**
     * Analyze threaded hole feature (M3, M4, M5, etc.)
     * Threaded holes can be blind or through holes with thread specifications
     * @param {object} feature - Threaded hole metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeThreadedHole(feature, vertices, bbox, faceIdInfo) {
        console.log('🔩 [THREADED_HOLE] Analyzing threaded hole feature:', feature);

        // Extract threaded hole parameters
        // Handle both M-size (e.g., "M6") and radius-based threaded holes (thread=null)
        const threadType = feature.thread || null;
        const threadDisplay = threadType || 'Radius-based'; // Show "Radius-based" if no M-size
        const isThreaded = feature.is_threaded === true || feature.subtype === 'threaded';
        const threadConfidence = feature.thread_confidence || 0.0;
        const subtype = feature.subtype || 'unknown'; // 'blind', 'through', or 'threaded'
        const diameter = feature.diameter || feature.radius * 2 || 0;
        const radius = feature.radius || diameter / 2 || 0;
        const depth = feature.depth || 0;
        const position = feature.position || { x: 0, y: 0, z: 0 };
        const axis = feature.axis || { x: 0, y: 0, z: -1 };
        const faceCount = feature.face_count || (feature.face_ids ? feature.face_ids.length : 0);

        // Determine display name based on subtype and thread type
        let displayName = 'Threaded Hole';
        if (subtype === 'blind') {
            displayName = `Threaded Blind Hole (${threadDisplay})`;
        } else if (subtype === 'through') {
            displayName = `Threaded Through Hole (${threadDisplay})`;
        } else if (subtype === 'threaded') {
            // Generic threaded hole (when subtype is just 'threaded')
            displayName = `Threaded Hole (${threadDisplay})`;
        } else {
            displayName = `Threaded Hole (${threadDisplay})`;
        }

        // Build properties object - simplified for UI
        const properties = {
            'Thread Type': threadDisplay,
            'Hole Type': subtype.charAt(0).toUpperCase() + subtype.slice(1),
            'Diameter': `${diameter.toFixed(2)} mm`,
            'Position': `(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)})`
        };

        // Add depth for blind holes
        if (subtype === 'blind' && depth > 0) {
            properties['Depth'] = `${depth.toFixed(2)} mm`;
        }

        return {
            type: 'threaded_hole',
            subType: subtype,
            shape: displayName,
            confidence: threadConfidence > 0 ? threadConfidence : 0.95, // High confidence from metadata
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo,
            // Additional threaded hole specific data
            threadInfo: {
                threadType,
                isThreaded,
                confidence: threadConfidence
            }
        };
    }

    /**
     * Analyze oblong feature (slotted hole with rounded ends)
     * Oblongs consist of: 2 planar walls + 4 cylindrical rounded corners
     * @param {object} feature - Oblong metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeOblong(feature, vertices, bbox, faceIdInfo) {
        console.log('🔲 [OBLONG] Analyzing oblong feature:', feature);

        // Extract oblong parameters
        const length = feature.length || 0;
        const width = feature.width || 0;
        const height = feature.height || 0;
        const filletRadius = feature.fillet_radius || width / 2;
        const straightLength = feature.straight_length || (length - width);
        const position = feature.position || feature.center || { x: 0, y: 0, z: 0 };
        const corner = feature.corner || { x: 0, y: 0, z: 0 };
        const direction = feature.direction || { x: 0, y: 0, z: 1 };
        const faceCount = feature.face_ids ? feature.face_ids.length : 0;
        const faceBreakdown = feature.face_breakdown || {};
        const units = feature.units || 'mm';

        // Determine sub-type based on face type
        let subType = 'unknown';
        const surfaceType = faceIdInfo.surfaceType || '';

        if (surfaceType.includes('Plane')) {
            subType = 'wall';  // Planar wall face
        } else if (surfaceType.includes('Cylinder')) {
            subType = 'rounded_end';  // Cylindrical corner
        }

        // Build properties object - simplified for UI
        const properties = {
            'Length': `${straightLength.toFixed(2)} ${units}`,
            'Width': `${width.toFixed(2)} ${units}`,
            'Position': `(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)})`
        };

        return {
            type: 'oblong',
            subType: subType,
            shape: `Oblong Slot (${subType === 'wall' ? 'Wall' : 'Rounded End'})`,
            confidence: feature.confidence || 0.95,
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo,
            // Additional oblong-specific data
            oblongInfo: {
                length,
                width,
                height,
                filletRadius,
                straightLength,
                faceBreakdown
            }
        };
    }

    /**
     * Analyze square/rectangular through-cut metadata.
     * Box holes are planar-wall features: 2 X walls + 2 Y walls.
     * @param {object} feature - Square/rectangular hole metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeBoxHole(feature, vertices, bbox, faceIdInfo) {
        console.log('[BOX_HOLE] Analyzing box hole feature:', feature);

        const units = feature.units || 'mm';
        const featureType = (feature.type || '').toLowerCase();
        const isSquare = featureType === 'square_hole';
        const width = feature.width || feature.side || 0;
        const length = feature.length || feature.side || width;
        const depth = feature.depth || feature.height || 0;
        const position = feature.position || feature.center || { x: 0, y: 0, z: 0 };
        const corner = feature.corner || null;
        const faceIds = feature.face_ids || [];
        const faceBreakdown = feature.face_breakdown || {};
        const clickedFaceId = faceIdInfo.realFaceId || '';
        const clickedFaceIndex = faceIds.indexOf(clickedFaceId);
        const displayName = isSquare ? 'Square Hole' : 'Rectangular Hole';
        const wallType = this.detectBoxHoleWallType(bbox);

        const properties = {
            'Feature Type': displayName,
            'Wall': wallType,
            'Width': `${width.toFixed(2)} ${units}`,
            'Length': `${length.toFixed(2)} ${units}`,
            'Depth': `${depth.toFixed(2)} ${units}`,
            'Center': `(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)})`,
            'Face Count': faceIds.length,
            'Face Index': clickedFaceIndex >= 0 ? `${clickedFaceIndex + 1} of ${faceIds.length}` : 'Unknown'
        };

        if (corner) {
            properties['Corner'] = `(${corner.x.toFixed(2)}, ${corner.y.toFixed(2)}, ${corner.z.toFixed(2)})`;
        }

        if (feature.is_open_cutout !== undefined) {
            properties['Open Cutout'] = feature.is_open_cutout ? 'Yes' : 'No';
        }

        if (faceBreakdown.total !== undefined) {
            properties['Planar Walls'] = `${faceBreakdown.planar_walls || faceBreakdown.total || faceIds.length}`;
        }

        return {
            type: featureType,
            subType: 'through',
            shape: displayName,
            confidence: feature.confidence || 0.95,
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: false,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo,
            face_ids: faceIds,
            boxHoleInfo: {
                width,
                length,
                depth,
                position,
                corner,
                faceBreakdown,
                wallType
            }
        };
    }

    analyzeRectangularFace(feature, vertices, bbox, faceIdInfo) {
        const length = feature.length || 0;
        const width = feature.width || 0;
        const area = feature.area || (length * width);
        const localFrame = feature.local_frame || {};
        const relatedBending = feature.related_bending || null;

        const properties = {
            'Shape Type': 'Rectangular Face',
            'Length': `${length.toFixed(2)} mm`,
            'Width': `${width.toFixed(2)} mm`,
            'Area': `${area.toFixed(2)} mm²`,
            'Vertex Count': vertices.length,
            'Measurement': localFrame.measurement === 'oriented_face_plane' ? 'Local face plane' : 'Global axes'
        };

        if (relatedBending?.bend_angle !== undefined && relatedBending?.bend_angle !== null) {
            properties['Related Bend Angle'] = `${Number(relatedBending.bend_angle).toFixed(1)}°`;
        }

        return {
            type: 'rectangular',
            shape: 'Rectangular Face',
            confidence: 0.95,
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length,
                localFrame
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo
        };
    }

    analyzeSquareFace(feature, vertices, bbox, faceIdInfo) {
        const side = feature.side || 0;
        const area = feature.area || (side * side);
        const localFrame = feature.local_frame || {};
        const relatedBending = feature.related_bending || null;

        const properties = {
            'Shape Type': 'Square Face',
            'Side': `${side.toFixed(2)} mm`,
            'Area': `${area.toFixed(2)} mm²`,
            'Vertex Count': vertices.length,
            'Measurement': localFrame.measurement === 'oriented_face_plane' ? 'Local face plane' : 'Global axes'
        };

        if (relatedBending?.bend_angle !== undefined && relatedBending?.bend_angle !== null) {
            properties['Related Bend Angle'] = `${Number(relatedBending.bend_angle).toFixed(1)}°`;
        }

        return {
            type: 'square',
            shape: 'Square Face',
            confidence: 0.95,
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length,
                localFrame
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo
        };
    }

    /**
     * Infer which wall of a square/rectangular hole was clicked.
     * A wall with near-zero X thickness is an X wall; near-zero Y thickness is a Y wall.
     */
    detectBoxHoleWallType(bbox) {
        const size = bbox.size || {};
        const x = Math.abs(size.x || 0);
        const y = Math.abs(size.y || 0);
        const z = Math.abs(size.z || 0);
        const tolerance = 0.05;

        if (x <= tolerance && y > tolerance) {
            return 'X Wall';
        }

        if (y <= tolerance && x > tolerance) {
            return 'Y Wall';
        }

        const wallDims = [
            { axis: 'X Wall', value: x },
            { axis: 'Y Wall', value: y },
            { axis: 'Z Wall', value: z }
        ].sort((a, b) => a.value - b.value);

        return wallDims[0]?.axis || 'Planar Wall';
    }

    /**
     * Analyze bending feature (sheet metal bend with inner/outer surfaces)
     * Bending consists of: inner cylindrical surface + outer cylindrical surface
     * @param {object} feature - Bending metadata
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @returns {object} Analysis result
     */
    analyzeBending(feature, vertices, bbox, faceIdInfo) {
        // console.log('🔧 [BENDING] Analyzing bending feature:', feature);

        // Extract bending parameters
        const subtype = feature.subtype || 'detected';
        const arcLength = feature.arc_length || 0;
        const confidence = feature.confidence || 0.85;
        const pairId = feature.pair_id || '';
        const bendAngle = feature.bend_angle || null; // ✅ Extract bend_angle from feature

        // Extract inner and outer data
        const inner = feature.inner || {};
        const outer = feature.outer || {};

        const innerFaceIds = inner.face_ids || [];
        const outerFaceIds = outer.face_ids || [];
        const innerRadius = inner.radius || 0;
        const outerRadius = outer.radius || 0;

        // Determine which face is being clicked (inner or outer)
        const clickedFaceId = faceIdInfo.realFaceId || '';
        let isInner = innerFaceIds.includes(clickedFaceId);
        let isOuter = outerFaceIds.includes(clickedFaceId);

        // If not found in either, default to inner
        if (!isInner && !isOuter) {
            console.warn(`⚠️ Face ${clickedFaceId} not found in inner or outer, defaulting to inner`);
            isInner = true;
        }

        const currentSide = isInner ? 'inner' : 'outer';
        const currentRadius = isInner ? innerRadius : outerRadius;
        const currentFaceIds = isInner ? innerFaceIds : outerFaceIds;
        const oppositeSide = isInner ? 'outer' : 'inner';
        const oppositeRadius = isInner ? outerRadius : innerRadius;

        console.log(`🔧 Bending: ${currentSide} surface (R=${currentRadius}mm) for face ${clickedFaceId}`);

        // Build properties object - simplified for UI
        const properties = {
            'Surface': currentSide.charAt(0).toUpperCase() + currentSide.slice(1),
            'Radius': `${currentRadius.toFixed(2)} mm`,
            'Opposite Radius': `${oppositeRadius.toFixed(2)} mm`,
            'Arc Length': `${arcLength.toFixed(2)} mm`
        };

        // ✅ Add bend_angle to properties if available
        if (bendAngle !== null && bendAngle !== undefined) {
            properties['Bend Angle'] = `${bendAngle.toFixed(1)}°`;
        }

        return {
            type: 'bending',
            subType: currentSide, // 'inner' or 'outer'
            shape: `Bending (${currentSide} surface)`,
            confidence: confidence,
            calculatedProperties: properties,
            geometry: {
                bbox,
                isHighPoly: vertices.length > 14,
                vertexCount: vertices.length
            },
            featureMetadata: feature,
            source: 'feature_metadata',
            faceIdInfo,
            // 🔥 IMPORTANT: Only highlight the current face (inner OR outer, not both)
            face_ids: currentFaceIds,  // Only the clicked face for highlighting
            // Additional bending-specific data
            bendingInfo: {
                subtype,
                inner: {
                    faceIds: innerFaceIds,
                    radius: innerRadius
                },
                outer: {
                    faceIds: outerFaceIds,
                    radius: outerRadius
                },
                arcLength,
                bendAngle, // ✅ Include bend_angle in bendingInfo
                pairId,
                currentSide,
                currentRadius
            }
        };
    }

    /**
     * Main analysis pipeline - enhanced with advanced geometric detection
     * @param {THREE.Vector3[]} vertices - An array of THREE.Vector3 objects.
     * @param {object} faceIdInfo - Face metadata from OnShape JSON
     * @param {Array} features - Optional features array for context (to detect holes)
     * @returns {object} Comprehensive analysis results
     */
    analyze(vertices, faceIdInfo = {}, features = []) {
        if (!vertices || vertices.length < 3) {
            return { error: 'Insufficient vertices for analysis.', faceIdInfo };
        }

        console.log(`🔍 Analyzing face ${faceIdInfo.realFaceId || 'unknown'} with ${vertices.length} vertices`);

        // Calculate face orientation
        const faceOrientation = this.getFaceOrientation(vertices);

        // Calculate bounding box for all analysis
        const bbox = this.calculateBoundingBox(vertices);
        const planarityResult = this.checkPlanarity(vertices);

        let analysisResult;
        if (planarityResult.isPlanar) {
            analysisResult = this.analyzePlanarShape(vertices, planarityResult.normal, bbox);
        } else {
            // Enhanced curved shape analysis with cylindrical/fillet detection
            // Pass features array for context (to detect if cylindrical surface is a hole)
            analysisResult = this.analyzeAdvancedCurvedShape(vertices, bbox, faceIdInfo, features);
        }

        analysisResult.faceIdInfo = faceIdInfo;
        analysisResult.faceOrientation = faceOrientation;  // Add orientation
        return analysisResult;
    }

    /**
     * Enhanced curved shape analysis with cylindrical/fillet detection
     * @param {THREE.Vector3[]} vertices - Array of vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @param {Array} features - Optional features array for context
     * @returns {object} Analysis results
     */
    analyzeAdvancedCurvedShape(vertices, bbox, faceIdInfo, features = []) {
        console.log(`🔍 Advanced curved analysis: ${vertices.length} vertices`);

        // Apply high-polygon analysis if sufficient vertices
        if (vertices.length >= 14) {
            return this.analyzeHighPolygonGeometry(vertices, bbox, faceIdInfo, features);
        } else {
            return this.analyzeMediumComplexityGeometry(vertices, bbox, faceIdInfo, features);
        }
    }

    /**
     * Checks if a set of vertices lie on a single plane.
     * @param {THREE.Vector3[]} vertices - An array of vertices.
     * @returns {{isPlanar: boolean, normal: THREE.Vector3, deviation: number}}
     */
    checkPlanarity(vertices, tolerance = 0.1) {
        if (vertices.length < 4) {
            return { isPlanar: true, normal: new THREE.Vector3(0, 0, 1), deviation: 0 };
        }

        // Create a plane from the first three non-collinear points
        const plane = new THREE.Plane();
        plane.setFromCoplanarPoints(vertices[0], vertices[1], vertices[2]);

        let maxDistance = 0;
        for (let i = 3; i < vertices.length; i++) {
            const distance = Math.abs(plane.distanceToPoint(vertices[i]));
            if (distance > maxDistance) {
                maxDistance = distance;
            }
        }

        return {
            isPlanar: maxDistance < tolerance,
            normal: plane.normal,
            deviation: maxDistance
        };
    }

    /**
     * Enhanced planar shape analysis
     * @param {THREE.Vector3[]} vertices - Array of vertices
     * @param {THREE.Vector3} normal - Normal vector of the plane
     * @param {object} bbox - Bounding box
     * @returns {object} Analysis results
     */
    analyzePlanarShape(vertices, normal, bbox) {
        const size = bbox.size;
        const dimensions = [size.x, size.y, size.z].filter(d => d > 0.001);
        const length = Math.max(...dimensions);
        const width = Math.min(...dimensions);

        // Determine if square or rectangular
        const aspectRatio = length / width;
        const isSquare = Math.abs(aspectRatio - 1.0) < 0.1; // Within 10% of 1:1 ratio

        const shapeType = isSquare ? 'square' : 'rectangular';
        const shapeLabel = isSquare ? 'Square Face' : 'Rectangular Face';

        return {
            type: shapeType,
            shape: shapeLabel,
            confidence: 0.9,
            calculatedProperties: {
                'Shape Type': shapeLabel,
                'Length': `${length.toFixed(2)} mm`,
                'Width': `${width.toFixed(2)} mm`,
                'Area': `${(length * width).toFixed(2)} mm²`,
                'Aspect Ratio': `${aspectRatio.toFixed(2)}`,
                'Vertex Count': vertices.length
            },
            geometry: {
                bbox,
                normal,
                isHighPoly: vertices.length > 14,
                aspectRatio
            }
        };
    }

    /**
     * High-polygon geometry analysis (14+ triangles, 42+ vertices)
     * Enhanced cylindrical and fillet detection
     * @param {THREE.Vector3[]} vertices - Array of vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @param {Array} features - Optional features array for context
     */
    analyzeHighPolygonGeometry(vertices, bbox, faceIdInfo, features = []) {
        const triangles = Math.floor(vertices.length / 3);
        console.log(`🔍 High-polygon analysis: ~${triangles} triangles, ${vertices.length} vertices`);

        // Calculate ratios and planarity
        const ratios = this.analyzeDimensionRatios(bbox.size);
        const planarity = this.checkPlanarity(vertices);

        // Advanced geometric analysis
        const curvatureAnalysis = this.analyzeSurfaceCurvature(vertices, bbox);
        const surfaceType = this.classifyHighPolySurface(vertices, bbox, curvatureAnalysis, faceIdInfo);

        let shapeType, confidence, pattern;

        // Classification based on enhanced analysis
        if (surfaceType.isCylindrical) {
            shapeType = 'hole';
            pattern = 'high_poly_hole_surface';
            confidence = surfaceType.confidence;
        } else if (surfaceType.isFillet) {
            shapeType = 'fillet';
            pattern = 'high_poly_fillet_surface';
            confidence = surfaceType.confidence;
        } else if (surfaceType.isCurved) {
            // Treat curved cylindrical surfaces as holes with lower confidence
            shapeType = 'hole';
            pattern = 'high_poly_hole_surface';
            confidence = Math.max(surfaceType.confidence * 0.8, 0.6); // Reduce confidence slightly
            console.log('🔵 Curved surface treated as hole');
        } else if (planarity.isPlanar) {
            shapeType = 'rectangular';
            pattern = 'high_poly_rectangular_face';
            confidence = 0.8;
        } else {
            // Complex curved surfaces are exported/displayed as holes.
            shapeType = 'hole';
            pattern = 'high_poly_hole_surface';
            confidence = 0.5;
            console.log('🔵 Complex surface treated as hole');
        }

        console.log(`✅ High-poly detected: ${shapeType} (${pattern}) - confidence: ${confidence}`);

        return {
            type: shapeType,
            shape: this.getShapeDisplayName(shapeType),
            pattern,
            confidence,
            calculatedProperties: this.calculateAdvancedProperties(shapeType, vertices, bbox, { faceIdInfo, features }),
            geometry: {
                bbox,
                ratios,
                planarity,
                curvatureAnalysis,
                surfaceType,
                isHighPoly: true,
                triangleCount: triangles,
                vertexCount: vertices.length
            }
        };
    }

    /**
     * Medium-complexity geometry analysis (3-13 triangles)
     * @param {THREE.Vector3[]} vertices - Array of vertices
     * @param {object} bbox - Bounding box
     * @param {object} faceIdInfo - Face metadata
     * @param {Array} features - Optional features array for context
     */
    analyzeMediumComplexityGeometry(vertices, bbox, faceIdInfo = {}, features = []) {
        console.log(`🔍 Medium-complexity analysis: ${vertices.length} vertices`);

        const ratios = this.analyzeDimensionRatios(bbox.size);
        const planarity = this.checkPlanarity(vertices);

        let shapeType, confidence, pattern;

        if (planarity.isPlanar) {
            if (ratios.isSquare) {
                shapeType = 'square';
                pattern = 'medium_poly_square_face';
                confidence = 0.8;
            } else {
                shapeType = 'rectangular';
                pattern = 'medium_poly_rectangular_face';
                confidence = 0.8;
            }
        } else {
            // Basic curved analysis for medium complexity
            const size = bbox.size;
            const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
            const aspectRatio = dims[0] / dims[2];

            if (aspectRatio > 3.0) {
                shapeType = 'hole';
                pattern = 'medium_poly_hole_surface';
                confidence = 0.6;
            } else {
                // Treat as hole instead of curved
                shapeType = 'hole';
                pattern = 'medium_poly_hole_surface';
                confidence = 0.5;
            }
        }

        return {
            type: shapeType,
            shape: this.getShapeDisplayName(shapeType),
            pattern,
            confidence,
            calculatedProperties: this.calculateAdvancedProperties(shapeType, vertices, bbox, { faceIdInfo, features }),
            geometry: {
                bbox,
                ratios,
                planarity,
                isHighPoly: false,
                vertexCount: vertices.length
            }
        };
    }

    /**
     * Classify high-polygon surface type with enhanced fillet vs cylindrical detection
     */
    classifyHighPolySurface(vertices, bbox, curvatureAnalysis, faceIdInfo = {}) {
        console.log('🔍 Classifying high-poly surface type...');


        const filletVsCylindricalAnalysis = this.analyzeFilletVsCylindrical(vertices, bbox, curvatureAnalysis, faceIdInfo);

        let result = {
            isCylindrical: false,
            isFillet: false,
            isCurved: false,
            isFlat: false,
            confidence: 0.5,
            detectionMethod: 'basic',
            filletVsCylindricalAnalysis
        };

        if (curvatureAnalysis.hasCurvature) {
            // Use enhanced fillet vs cylindrical detection
            if (filletVsCylindricalAnalysis.isCylindrical) {
                result.isCylindrical = true;
                result.confidence = filletVsCylindricalAnalysis.confidence;
                result.detectionMethod = 'enhanced_cylindrical';
                console.log('🔵 Enhanced detection: Cylindrical surface');
            } else if (filletVsCylindricalAnalysis.isFillet) {
                result.isFillet = true;
                result.confidence = filletVsCylindricalAnalysis.confidence;
                result.detectionMethod = 'enhanced_fillet';
                console.log('🟡 Enhanced detection: Fillet surface');
            } else {
                result.isCurved = true;
                result.confidence = 0.7;
                result.detectionMethod = 'basic_curved';
                console.log('🟢 Basic detection: Curved surface');
            }
        } else {
            result.isFlat = true;
            result.confidence = 0.8;
            result.detectionMethod = 'planar';
            console.log('⚪ Detection: Flat surface');
        }

        return result;
    }

    /**
     * 🔥 ENHANCED FILLET VS CYLINDRICAL DETECTION ALGORITHM
     * Ported from SimpleFaceAnalyzer.js with JSON-specific enhancements
     */
    analyzeFilletVsCylindrical(vertices, bbox, faceIdInfo = {}) {
        console.log('🔥 Enhanced Fillet vs Cylindrical Analysis...');

        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // JSON-specific metadata analysis
        const faceId = faceIdInfo.realFaceId || '';
        const hasFilletIndicator = this.detectFilletFromMetadata(faceId, faceIdInfo);
        const hasCylinderIndicator = this.detectCylinderFromMetadata(faceId, faceIdInfo);

        // Apply the same scoring system as OBJ format
        const aspectRatio = largest / smallest;
        const mediumRatio = medium / smallest;

        console.log(`📏 Dimensions: L=${largest.toFixed(2)}, M=${medium.toFixed(2)}, S=${smallest.toFixed(2)}`);
        console.log(`📊 Ratios: Aspect=${aspectRatio.toFixed(2)}, Medium=${mediumRatio.toFixed(2)}`);
        console.log(`🔍 Enhanced Analysis: vertices=${vertices.length}, face=${faceId}`);

        // Enhanced geometric analysis
        const curvatureDirections = this.analyzeCurvatureDirections(vertices);
        const surfaceContinuity = this.analyzeSurfaceContinuity(vertices, bbox);
        const vertexDistribution = this.analyzeVertexDistribution(vertices, bbox);

        // ENHANCED DECISION ALGORITHM WITH BETTER LOGIC
        let cylindricalScore = 0;
        let filletScore = 0;
        let reasoning = [];

        // PRIMARY DISCRIMINATOR: Triangle/Vertex count pattern
        const triangleCount = vertices.length / 3;
        const vertexDensity = vertices.length / (largest * medium * smallest);

        console.log(`📊 Geometry Stats: Triangles≈${triangleCount.toFixed(0)}, VertexDensity=${vertexDensity.toFixed(3)}`);

        // **NEW SIMPLIFIED RULE**: Clear vertex count thresholds
        // Fillet: < 80 vertices (smaller, more compact curved surfaces)
        // Cylindrical: > 90 vertices (larger, elongated surfaces)

        if (vertices.length < 80) {
            filletScore += 0.8; // Strong fillet indicator
            reasoning.push(`FILLET SIGNATURE: Low vertex count (${vertices.length} < 80)`);
        } else if (vertices.length > 90) {
            cylindricalScore += 0.8; // Strong cylindrical indicator
            reasoning.push(`CYLINDRICAL SIGNATURE: High vertex count (${vertices.length} > 90)`);
        } else {
            // Ambiguous range 80-90: use aspect ratio as tiebreaker
            if (aspectRatio > 5.0) {
                cylindricalScore += 0.4;
                reasoning.push(`Ambiguous vertex count (${vertices.length}), high aspect ratio (${aspectRatio.toFixed(1)}) suggests cylindrical`);
            } else {
                filletScore += 0.4;
                reasoning.push(`Ambiguous vertex count (${vertices.length}), low aspect ratio (${aspectRatio.toFixed(1)}) suggests fillet`);
            }
        }

        return this.calculateFinalScores(cylindricalScore, filletScore, reasoning, aspectRatio, mediumRatio,
            curvatureDirections, surfaceContinuity, vertexDistribution,
            hasFilletIndicator, hasCylinderIndicator, vertices, dims, faceIdInfo);
    }

    /**
     * Calculate bounding box for JSON vertices
     */
    calculateBoundingBox(vertices) {
        if (vertices.length === 0) return null;

        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

        vertices.forEach(v => {
            minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x);
            minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
            minZ = Math.min(minZ, v.z); maxZ = Math.max(maxZ, v.z);
        });

        return {
            min: { x: minX, y: minY, z: minZ },
            max: { x: maxX, y: maxY, z: maxZ },
            size: { x: maxX - minX, y: maxY - minY, z: maxZ - minZ }
        };
    }

    /**
     * Analyze dimension ratios for geometric classification
     */
    analyzeDimensionRatios(size) {
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        return {
            aspectRatio: largest / smallest,
            mediumRatio: medium / smallest,
            isSquare: Math.abs(largest - medium) < 0.1 * largest,
            isElongated: largest / smallest > 3.0,
            largest, medium, smallest
        };
    }

    /**
     * Analyze surface curvature for high-polygon geometry
     */
    analyzeSurfaceCurvature(vertices, bbox) {
        console.log('🌊 Analyzing surface curvature...');

        if (vertices.length < 4) {
            return { hasCurvature: false, curvatureType: 'insufficient_data' };
        }

        // Calculate vertex normals to detect curvature
        const normals = this.calculateVertexNormals(vertices);
        const normalVariation = this.calculateNormalVariation(normals);

        // Analyze curvature patterns
        const curvatureMetrics = {
            normalVariation: normalVariation,
            hasCurvature: normalVariation > 0.1, // Threshold for detecting curvature
            curvatureIntensity: Math.min(normalVariation * 10, 1.0),
            curvatureType: this.classifyCurvatureType(normals, bbox)
        };

        console.log(`🌊 Curvature analysis: variation=${normalVariation.toFixed(3)}, type=${curvatureMetrics.curvatureType}`);
        return curvatureMetrics;
    }

    /**
     * Calculate vertex normals for curvature analysis
     */
    calculateVertexNormals(vertices) {
        const normals = [];

        // For each vertex, calculate normal based on neighboring vertices
        for (let i = 0; i < vertices.length; i++) {
            const prev = vertices[(i - 1 + vertices.length) % vertices.length];
            const curr = vertices[i];
            const next = vertices[(i + 1) % vertices.length];

            // Calculate vectors
            const v1 = {
                x: prev.x - curr.x,
                y: prev.y - curr.y,
                z: prev.z - curr.z
            };
            const v2 = {
                x: next.x - curr.x,
                y: next.y - curr.y,
                z: next.z - curr.z
            };

            // Cross product for normal
            const normal = {
                x: v1.y * v2.z - v1.z * v2.y,
                y: v1.z * v2.x - v1.x * v2.z,
                z: v1.x * v2.y - v1.y * v2.x
            };

            // Normalize
            const length = Math.sqrt(normal.x * normal.x + normal.y * normal.y + normal.z * normal.z);
            if (length > 0) {
                normal.x /= length;
                normal.y /= length;
                normal.z /= length;
            }

            normals.push(normal);
        }

        return normals;
    }

    /**
     * Calculate variation in vertex normals
     */
    calculateNormalVariation(normals) {
        if (normals.length < 2) return 0;

        let totalVariation = 0;
        const baseNormal = normals[0];

        for (let i = 1; i < normals.length; i++) {
            const normal = normals[i];
            // Calculate dot product to measure angle difference
            const dotProduct = baseNormal.x * normal.x + baseNormal.y * normal.y + baseNormal.z * normal.z;
            const angle = Math.acos(Math.max(-1, Math.min(1, dotProduct)));
            totalVariation += angle;
        }

        return totalVariation / (normals.length - 1);
    }

    /**
     * Classify curvature type based on normal patterns
     */
    classifyCurvatureType(normals, bbox) {
        if (normals.length < 3) return 'insufficient_data';

        const variation = this.calculateNormalVariation(normals);

        if (variation < 0.1) return 'planar';
        if (variation < 0.5) return 'gentle_curve';
        if (variation < 1.0) return 'moderate_curve';
        return 'sharp_curve';
    }

    /**
     * Analyze curvature directions for fillet vs cylindrical detection
     */
    analyzeCurvatureDirections(vertices) {
        console.log('🧭 Analyzing curvature directions...');

        if (vertices.length < 4) {
            return {
                hasAxisSymmetry: false,
                isSingleAxis: false,
                isMultiDirectional: false,
                primaryAxis: null
            };
        }

        // Calculate normals and analyze their patterns
        const normals = this.calculateVertexNormals(vertices);
        const normalVariations = this.calculateNormalVariationsByDirection(normals);

        // Detect axis symmetry (cylindrical characteristic)
        const hasAxisSymmetry = this.detectAxisSymmetry(vertices, normals);

        // Detect single-axis curvature (cylindrical: curved in one direction, straight in another)
        const isSingleAxis = normalVariations.dominantDirection !== null &&
            normalVariations.variationRatio > 2.0;

        // Detect multi-directional curvature (fillet characteristic)
        const isMultiDirectional = normalVariations.variationRatio < 1.5 &&
            normalVariations.totalVariation > 0.2;

        return {
            hasAxisSymmetry,
            isSingleAxis,
            isMultiDirectional,
            primaryAxis: normalVariations.dominantDirection,
            variationRatio: normalVariations.variationRatio
        };
    }

    /**
     * Calculate normal variations by direction for curvature analysis
     */
    calculateNormalVariationsByDirection(normals) {
        if (normals.length < 3) {
            return {
                dominantDirection: null,
                variationRatio: 1.0,
                totalVariation: 0
            };
        }

        // Calculate variations in X, Y, Z directions
        const variations = { x: 0, y: 0, z: 0 };

        for (let i = 1; i < normals.length; i++) {
            variations.x += Math.abs(normals[i].x - normals[i - 1].x);
            variations.y += Math.abs(normals[i].y - normals[i - 1].y);
            variations.z += Math.abs(normals[i].z - normals[i - 1].z);
        }

        const maxVar = Math.max(variations.x, variations.y, variations.z);
        const minVar = Math.min(variations.x, variations.y, variations.z);
        const totalVar = variations.x + variations.y + variations.z;

        let dominantDirection = null;
        if (maxVar === variations.x) dominantDirection = 'x';
        else if (maxVar === variations.y) dominantDirection = 'y';
        else if (maxVar === variations.z) dominantDirection = 'z';

        return {
            dominantDirection,
            variationRatio: minVar > 0 ? maxVar / minVar : maxVar,
            totalVariation: totalVar,
            variations
        };
    }

    /**
     * Detect axis symmetry (cylindrical characteristic)
     */
    detectAxisSymmetry(vertices, normals) {
        // Simple heuristic: check if normals show consistent pattern
        if (normals.length < 4) return false;

        const normalVariations = this.calculateNormalVariation(normals);
        return normalVariations < 0.3; // Low variation suggests axis symmetry
    }

    /**
     * Analyze surface continuity for fillet vs cylindrical detection
     */
    analyzeSurfaceContinuity(vertices, bbox) {
        console.log('🔗 Analyzing surface continuity...');

        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;

        // Check if surface appears complete (cylindrical characteristic)
        const aspectRatio = largest / smallest;
        const isComplete = aspectRatio > 3.0 && this.checkSurfaceCompleteness(vertices);

        // Check if surface is transitional (fillet characteristic)
        const isTransition = aspectRatio < 2.5 && this.checkTransitionCharacteristics(vertices, bbox);

        // Check surface boundaries
        const boundaries = this.analyzeSurfaceBoundaries(vertices, bbox);

        return {
            isComplete,
            isTransition,
            boundaries,
            completenessScore: isComplete ? 0.8 : 0.3,
            transitionScore: isTransition ? 0.8 : 0.3
        };
    }

    /**
     * Analyze vertex distribution patterns
     */
    analyzeVertexDistribution(vertices, bbox) {
        console.log('📍 Analyzing vertex distribution...');

        // Check uniform distribution (cylindrical characteristic)
        const isUniform = this.checkUniformDistribution(vertices, bbox);

        // Check edge concentration (fillet characteristic)
        const isEdgeConcentrated = this.checkEdgeConcentration(vertices, bbox);

        // Analyze distribution symmetry
        const symmetry = this.analyzeDistributionSymmetry(vertices, bbox);

        return {
            isUniform,
            isEdgeConcentrated,
            symmetry,
            distributionScore: isUniform ? 0.8 : (isEdgeConcentrated ? 0.6 : 0.4)
        };
    }

    /**
     * Enhanced JSON-specific metadata analysis for feature detection
     */
    detectFilletFromMetadata(faceId, faceIdInfo) {
        // Analyze OnShape face ID patterns
        const idLower = faceId.toLowerCase();

        // Enhanced fillet patterns in OnShape IDs
        const filletPatterns = [
            /r\d+/,           // R5, R10, etc.
            /fillet/,         // Direct fillet naming
            /blend/,          // Blend operations
            /round/,          // Rounded features
            /chamfer/,        // Chamfer operations
            /edge\d+/,        // Edge features
            /transition/      // Transition surfaces
        ];

        return filletPatterns.some(pattern => pattern.test(idLower)) &&
            !idLower.includes('cylinder') && !idLower.includes('tube');
    }

    detectCylinderFromMetadata(faceId, faceIdInfo) {
        const idLower = faceId.toLowerCase();

        // Enhanced cylindrical patterns in OnShape IDs
        const cylinderPatterns = [
            /cylinder/,       // Direct cylinder naming
            /tube/,           // Tube features
            /pipe/,           // Pipe features
            /hole/,           // Hole features
            /shaft/,          // Shaft features
            /rod/,            // Rod features
            /d\d+.*h\d+/,     // Diameter-Height pattern (D20H50)
            /bore/            // Bore features
        ];

        return cylinderPatterns.some(pattern => pattern.test(idLower));
    }

    /**
     * Calculate final scores and make classification decision
     */
    calculateFinalScores(cylindricalScore, filletScore, reasoning, aspectRatio, mediumRatio,
        curvatureDirections, surfaceContinuity, vertexDistribution,
        hasFilletIndicator, hasCylinderIndicator, vertices, dims, faceIdInfo = {}) {

        const [largest, medium, smallest] = dims;

        // IMPROVED ASPECT RATIO ANALYSIS
        if (aspectRatio > 4.0) {
            const dimensionSpread = (largest - smallest) / largest;
            const mediumPosition = (medium - smallest) / (largest - smallest);

            if (mediumRatio > 3.0 && mediumPosition > 0.7) {
                // True cylindrical: medium dimension close to largest
                cylindricalScore += 0.4;
                reasoning.push(`Cylindrical pattern: medium dimension (${medium.toFixed(1)}) close to largest, position=${mediumPosition.toFixed(2)}`);
            } else if (mediumRatio > 2.0 && dimensionSpread > 0.7) {
                // Large fillet: significant dimension spread but medium not too close to largest
                filletScore += 0.3;
                reasoning.push(`Large fillet pattern: significant dimension spread (${dimensionSpread.toFixed(2)}), medium ratio=${mediumRatio.toFixed(1)}`);
            } else {
                // Small/medium fillet
                filletScore += 0.4;
                reasoning.push(`Fillet pattern: aspect=${aspectRatio.toFixed(1)}, medium=${mediumRatio.toFixed(1)}, spread=${dimensionSpread.toFixed(2)}`);
            }
        }

        // ENHANCED DIMENSION PATTERN ANALYSIS
        const dimSimilarity = Math.abs(medium - smallest) / largest;
        const largeToMediumRatio = largest / medium;

        // Cylindrical: 2 similar dimensions (diameter), 1 different (height)
        if (dimSimilarity < 0.1 && aspectRatio > 3.0) {
            cylindricalScore += 0.3;
            reasoning.push(`Two similar dimensions (${medium.toFixed(1)}, ${smallest.toFixed(1)}) suggest cylindrical`);
        }

        // Fillet: progressive dimension reduction
        if (largeToMediumRatio > 1.5 && largeToMediumRatio < 4.0) {
            filletScore += 0.3;
            reasoning.push(`Progressive dimension reduction (${largeToMediumRatio.toFixed(1)}) suggests fillet`);
        }

        // Enhanced fillet detection for various sizes
        if (smallest < largest * 0.3) {
            const filletSizeRatio = smallest / largest;
            if (filletSizeRatio < 0.15) {
                filletScore += 0.4; // Strong indicator for small fillets
                reasoning.push(`Small fillet ratio (${filletSizeRatio.toFixed(2)}) strongly suggests fillet`);
            } else {
                filletScore += 0.2; // Moderate indicator for large fillets
                reasoning.push(`Large fillet ratio (${filletSizeRatio.toFixed(2)}) suggests fillet`);
            }
        }

        // VERTEX DISTRIBUTION
        if (vertexDistribution.isUniform) {
            cylindricalScore += 0.2;
            reasoning.push('Uniform vertex distribution suggests cylindrical');
        }

        if (vertexDistribution.isEdgeConcentrated) {
            filletScore += 0.2;
            reasoning.push('Edge-concentrated vertices suggest fillet');
        }

        // SURFACE CHARACTERISTICS
        if (surfaceContinuity.isComplete) {
            cylindricalScore += 0.1;
            reasoning.push('Complete surface suggests cylindrical');
        }

        if (surfaceContinuity.isTransition) {
            filletScore += 0.2;
            reasoning.push('Transition surface suggests fillet');
        }

        // CURVATURE ANALYSIS
        if (curvatureDirections.isSingleAxis) {
            cylindricalScore += 0.2;
            reasoning.push('Single-axis curvature suggests cylindrical');
        }

        if (curvatureDirections.isMultiDirectional) {
            filletScore += 0.2;
            reasoning.push('Multi-directional curvature suggests fillet');
        }

        // ENHANCED JSON METADATA INTEGRATION
        // Use OnShape face IDs and other JSON metadata to boost confidence
        const metadataConfidence = this.analyzeJSONMetadata(faceIdInfo, aspectRatio, vertices.length);

        if (metadataConfidence.type === 'cylindrical_strong') {
            cylindricalScore += 0.8;
            reasoning.push(`METADATA BOOST: ${metadataConfidence.evidence}`);
        } else if (metadataConfidence.type === 'cylindrical_moderate') {
            cylindricalScore += 0.4;
            reasoning.push(`METADATA: ${metadataConfidence.evidence}`);
        } else if (metadataConfidence.type === 'fillet_strong') {
            filletScore += 0.6;
            reasoning.push(`METADATA BOOST: ${metadataConfidence.evidence}`);
        } else if (metadataConfidence.type === 'fillet_moderate') {
            filletScore += 0.3;
            reasoning.push(`METADATA: ${metadataConfidence.evidence}`);
        }

        // Legacy metadata handling for backward compatibility
        if (hasCylinderIndicator && aspectRatio > 3.0) {
            cylindricalScore = Math.max(cylindricalScore, 1.0);
            reasoning.push(`LEGACY OVERRIDE: face ID indicates cylinder with aspect=${aspectRatio.toFixed(1)}`);
        } else if (hasFilletIndicator) {
            filletScore = Math.max(filletScore, 0.6);
            reasoning.push(`LEGACY: Face ID indicates fillet`);
        }

        return this.makeFinalDecision(cylindricalScore, filletScore, reasoning, aspectRatio, vertices, dims);
    }

    /**
     * Enhanced dimension pattern analysis - Primary discriminator for cylindrical vs fillet
     */
    analyzeDimensionPattern(largest, medium, smallest, aspectRatio, mediumRatio, vertices) {
        console.log(`📐 Analyzing dimension pattern: L=${largest.toFixed(2)}, M=${medium.toFixed(2)}, S=${smallest.toFixed(2)}`);

        // Calculate discriminative metrics
        const dimensionSpread = (largest - smallest) / largest;
        const mediumPosition = (medium - smallest) / (largest - smallest);
        const compactness = smallest / largest;
        const mediumToLargestRatio = medium / largest;

        console.log(`📊 Metrics: spread=${dimensionSpread.toFixed(3)}, mediumPos=${mediumPosition.toFixed(3)}, compact=${compactness.toFixed(3)}`);

        // OPTIMIZED CYLINDRICAL SIGNATURES (using configurable thresholds)
        // Signature 1: True cylinder (L >> M ≈ S, high aspect ratio)
        if (aspectRatio > this.thresholds.extremeAspectRatio &&
            mediumPosition > this.thresholds.mediumPositionThreshold &&
            mediumToLargestRatio > 0.8) {
            return {
                pattern: 'cylindrical_signature',
                evidence: `True cylinder: aspect=${aspectRatio.toFixed(1)}, mediumPos=${mediumPosition.toFixed(2)}`
            };
        }

        // Signature 2: Elongated cylinder (high aspect + medium close to smallest)
        if (aspectRatio > this.thresholds.highAspectRatio &&
            mediumRatio > 4.0 &&
            compactness < this.thresholds.compactnessThreshold) {
            return {
                pattern: 'cylindrical_signature',
                evidence: `Elongated cylinder: aspect=${aspectRatio.toFixed(1)}, mediumRatio=${mediumRatio.toFixed(1)}`
            };
        }

        // OPTIMIZED FILLET SIGNATURES (using configurable thresholds)
        // Signature 1: Classic fillet (progressive dimension reduction)
        if (aspectRatio > 2.0 && aspectRatio < 6.0 &&
            mediumRatio > 1.5 && mediumRatio < 3.0 &&
            compactness > this.thresholds.compactnessThreshold && compactness < 0.5) {
            return {
                pattern: 'fillet_signature',
                evidence: `Classic fillet: aspect=${aspectRatio.toFixed(1)}, mediumRatio=${mediumRatio.toFixed(1)}, compact=${compactness.toFixed(2)}`
            };
        }

        // Signature 2: Large fillet (moderate spread + thin profile)
        if (dimensionSpread > this.thresholds.dimensionSpreadThreshold &&
            dimensionSpread < 0.85 &&
            compactness < 0.3 && mediumPosition < 0.7) {
            return {
                pattern: 'fillet_signature',
                evidence: `Large fillet: spread=${dimensionSpread.toFixed(2)}, compact=${compactness.toFixed(2)}`
            };
        }

        // LIKELY PATTERNS (lower confidence, using configurable thresholds)
        if (aspectRatio > 6.0 && mediumPosition > 0.6) {
            return {
                pattern: 'cylindrical_likely',
                evidence: `Likely cylindrical: aspect=${aspectRatio.toFixed(1)}, mediumPos=${mediumPosition.toFixed(2)}`
            };
        }

        // UPDATED LOGIC: Clear vertex count thresholds
        // Cylindrical: > 90 vertices
        if (vertices && vertices.length > 90) {
            return {
                pattern: 'cylindrical_signature',
                evidence: `Cylindrical: vertices=${vertices.length} > 90, aspect=${aspectRatio.toFixed(1)}`
            };
        }

        // Fillet: < 80 vertices
        if (vertices && vertices.length < 80) {
            return {
                pattern: 'fillet_signature',
                evidence: `Fillet: vertices=${vertices.length} < 80, aspect=${aspectRatio.toFixed(1)}`
            };
        }

        if (aspectRatio < this.thresholds.lowAspectRatio && compactness > 0.2) {
            return {
                pattern: 'fillet_likely',
                evidence: `Likely fillet: aspect=${aspectRatio.toFixed(1)}, compact=${compactness.toFixed(2)}`
            };
        }

        return {
            pattern: 'ambiguous',
            evidence: `Ambiguous pattern: aspect=${aspectRatio.toFixed(1)}, requires secondary analysis`
        };
    }

    /**
     * Make final classification decision based on scores
     */
    makeFinalDecision(cylindricalScore, filletScore, reasoning, aspectRatio, vertices, dims) {
        const [largest, medium, smallest] = dims;
        const mediumRatio = medium / smallest;

        console.log(`🎯 Final scores: Cylindrical=${cylindricalScore.toFixed(2)}, Fillet=${filletScore.toFixed(2)}`);
        console.log(`📝 Reasoning: ${reasoning.join('; ')}`);

        // PRIMARY DISCRIMINATOR: Dimension Pattern Analysis
        const dimensionPattern = this.analyzeDimensionPattern(largest, medium, smallest, aspectRatio, mediumRatio, vertices);
        console.log(`🔍 Dimension Pattern: ${dimensionPattern.pattern} - ${dimensionPattern.evidence}`);

        let isCylindrical = false;
        let isFillet = false;
        let confidence = 0.5;

        // Apply dimension pattern analysis as primary discriminator
        if (dimensionPattern.pattern === 'cylindrical_signature') {
            cylindricalScore += 0.6;
            confidence = 0.85;
            reasoning.push(`PRIMARY: ${dimensionPattern.evidence}`);
        } else if (dimensionPattern.pattern === 'fillet_signature') {
            filletScore += 0.6;
            confidence = 0.85;
            reasoning.push(`PRIMARY: ${dimensionPattern.evidence}`);
        } else if (dimensionPattern.pattern === 'cylindrical_likely') {
            cylindricalScore += 0.3;
            reasoning.push(`LIKELY: ${dimensionPattern.evidence}`);
        } else if (dimensionPattern.pattern === 'fillet_likely') {
            filletScore += 0.3;
            reasoning.push(`LIKELY: ${dimensionPattern.evidence}`);
        }

        // Enhanced confidence calculation based on score difference and absolute values
        const scoreDifference = Math.abs(cylindricalScore - filletScore);
        const maxScore = Math.max(cylindricalScore, filletScore);

        if (cylindricalScore > filletScore && cylindricalScore > 0.4) {
            isCylindrical = true;
            // Calibrated confidence: more sensitive to score difference, capped at 90% to reflect uncertainty.
            confidence = Math.max(confidence, Math.min(0.5 + (cylindricalScore * 0.2) + (scoreDifference * 0.4), 0.90));
            reasoning.push(`Cylindrical wins with score ${cylindricalScore.toFixed(2)} (margin: ${scoreDifference.toFixed(2)})`);
        } else if (filletScore > cylindricalScore && filletScore > 0.4) {
            isFillet = true;
            // Calibrated confidence: more sensitive to score difference, capped at 90% to reflect uncertainty.
            confidence = Math.max(confidence, Math.min(0.5 + (filletScore * 0.2) + (scoreDifference * 0.4), 0.90));
            reasoning.push(`Fillet wins with score ${filletScore.toFixed(2)} (margin: ${scoreDifference.toFixed(2)})`);
        } else {
            // SECONDARY DISCRIMINATORS (for ambiguous cases)
            if (dimensionPattern.pattern === 'ambiguous') {
                console.log('🤔 Ambiguous pattern, applying secondary discriminators...');

                // 1. Vertex Distribution Analysis
                const vertexDensity = vertices.length / this.calculateSurfaceArea(vertices);
                reasoning.push(`Secondary (Vertex): density=${vertexDensity.toFixed(2)}`);
                if (vertexDensity > 1.5 && aspectRatio < 5.0) {
                    filletScore += 0.2;
                    reasoning.push('High vertex density suggests fillet');
                } else if (vertexDensity < 0.8 && aspectRatio > 5.0) {
                    cylindricalScore += 0.2;
                    reasoning.push('Low vertex density suggests cylinder');
                }

                // 2. Curvature Consistency (simplified)
                const stdDev = this.calculateDimensionStandardDeviation(dims);
                reasoning.push(`Secondary (Curvature): stdDev=${stdDev.toFixed(3)}`);
                if (stdDev < 0.2 && aspectRatio > 4.0) {
                    cylindricalScore += 0.25;
                    reasoning.push('Low dimension variance suggests consistent curvature (cylinder)');
                } else if (stdDev > 0.3) {
                    filletScore += 0.25;
                    reasoning.push('High dimension variance suggests changing curvature (fillet)');
                }
            }

            // IMPROVED FALLBACK LOGIC
            const dimensionSpread = (largest - smallest) / largest;
            const mediumPosition = (medium - smallest) / (largest - smallest);

            console.log(`🔄 Fallback analysis: spread=${dimensionSpread.toFixed(2)}, mediumPos=${mediumPosition.toFixed(2)}, aspect=${aspectRatio.toFixed(1)}`);

            // Apply clear vertex count rules in fallback
            if (vertices.length > 90) {
                isCylindrical = true;
                confidence = 0.8; // High confidence for clear threshold
                reasoning.push(`Fallback: Vertex count (${vertices.length}) > 90 -> Cylindrical`);
            } else if (vertices.length < 80) {
                isFillet = true;
                confidence = 0.8; // High confidence for clear threshold
                reasoning.push(`Fallback: Vertex count (${vertices.length}) < 80 -> Fillet`);
            } else if (aspectRatio > 10.0 && mediumPosition > 0.9 && smallest < largest * 0.15) {
                isCylindrical = true;
                confidence = 0.6;
                reasoning.push(`Fallback: Extreme aspect ratio (${aspectRatio.toFixed(1)}) with true cylindrical proportions`);
            } else if (dimensionSpread > 0.6 && smallest < largest * 0.3) {
                isFillet = true;
                confidence = 0.6;
                reasoning.push(`Fallback: High dimension spread (${dimensionSpread.toFixed(2)}) suggests fillet`);
            } else if (aspectRatio < 2.0) {
                isFillet = true;
                confidence = 0.6;
                reasoning.push('Fallback: Low aspect ratio → fillet');
            } else {
                isFillet = true;
                confidence = 0.5;
                reasoning.push('Fallback: Conservative fillet classification');
            }
        }

        const result = {
            isCylindrical,
            isFillet,
            confidence,
            reasoning,
            scores: { cylindricalScore, filletScore },
            metrics: {
                aspectRatio,
                mediumRatio: medium / smallest,
                vertexCount: vertices.length
            }
        };

        console.log(`🏆 ENHANCED FINAL RESULT: ${isCylindrical ? 'CYLINDRICAL' : 'FILLET'} (confidence: ${confidence.toFixed(2)})`);
        console.log(`📊 Key Metrics: aspect=${aspectRatio.toFixed(1)}, vertices=${vertices.length}`);

        return result;
    }

    /**
     * Helper functions for geometric analysis
     */
    calculateSurfaceArea(vertices) {
        // Simplified surface area calculation for tessellated data
        if (vertices.length < 3) return 1.0;

        let totalArea = 0;
        // Approximate area by treating consecutive vertices as triangles
        for (let i = 0; i < vertices.length - 2; i += 3) {
            const v1 = vertices[i];
            const v2 = vertices[i + 1];
            const v3 = vertices[i + 2];

            // Calculate triangle area using cross product
            const edge1 = { x: v2.x - v1.x, y: v2.y - v1.y, z: v2.z - v1.z };
            const edge2 = { x: v3.x - v1.x, y: v3.y - v1.y, z: v3.z - v1.z };

            const cross = {
                x: edge1.y * edge2.z - edge1.z * edge2.y,
                y: edge1.z * edge2.x - edge1.x * edge2.z,
                z: edge1.x * edge2.y - edge1.y * edge2.x
            };

            const magnitude = Math.sqrt(cross.x * cross.x + cross.y * cross.y + cross.z * cross.z);
            totalArea += magnitude * 0.5;
        }

        return Math.max(totalArea, 1.0); // Avoid division by zero
    }

    calculateDimensionStandardDeviation(dims) {
        const mean = dims.reduce((sum, val) => sum + val, 0) / dims.length;
        const variance = dims.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / dims.length;
        return Math.sqrt(variance);
    }

    /**
     * Enhanced JSON metadata analysis for OnShape face IDs and other JSON-specific data
     */
    analyzeJSONMetadata(faceIdInfo, aspectRatio, vertexCount) {
        const faceId = faceIdInfo.realFaceId || '';
        const displayName = faceIdInfo.displayName || '';

        // Enhanced pattern matching for OnShape face IDs
        const cylinderPatterns = [
            /cylinder/i, /cylindrical/i, /tube/i, /pipe/i, /shaft/i, /rod/i,
            /hole/i, /bore/i, /round/i, /circular/i
        ];

        const filletPatterns = [
            /fillet/i, /blend/i, /round/i, /edge/i, /transition/i, /smooth/i,
            /chamfer/i, /corner/i, /radius/i
        ];

        // Check face ID patterns
        let cylinderMatch = cylinderPatterns.some(pattern => pattern.test(faceId) || pattern.test(displayName));
        let filletMatch = filletPatterns.some(pattern => pattern.test(faceId) || pattern.test(displayName));

        // Enhanced confidence scoring based on geometric consistency
        if (cylinderMatch && aspectRatio > 8.0 && vertexCount > 100) {
            return {
                type: 'cylindrical_strong',
                evidence: `Strong cylinder ID match with high aspect ratio (${aspectRatio.toFixed(1)}) and vertex count (${vertexCount})`
            };
        } else if (cylinderMatch && aspectRatio > 4.0) {
            return {
                type: 'cylindrical_moderate',
                evidence: `Cylinder ID match with moderate aspect ratio (${aspectRatio.toFixed(1)})`
            };
        } else if (filletMatch && aspectRatio < 6.0 && vertexCount > 50) {
            return {
                type: 'fillet_strong',
                evidence: `Strong fillet ID match with appropriate aspect ratio (${aspectRatio.toFixed(1)}) and vertex density`
            };
        } else if (filletMatch) {
            return {
                type: 'fillet_moderate',
                evidence: `Fillet ID match with aspect ratio (${aspectRatio.toFixed(1)})`
            };
        }

        return {
            type: 'neutral',
            evidence: 'No significant metadata indicators found'
        };
    }

    checkSurfaceCompleteness(vertices) {
        const bbox = this.calculateBoundingBox(vertices);
        const volume = bbox.size.x * bbox.size.y * bbox.size.z;
        return volume > 100; // Threshold for "complete" surface
    }

    checkTransitionCharacteristics(vertices, bbox) {
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;
        return largest / smallest < 3.0; // Compact surfaces are transitions
    }

    checkUniformDistribution(vertices, bbox) {
        const center = {
            x: (bbox.min.x + bbox.max.x) / 2,
            y: (bbox.min.y + bbox.max.y) / 2,
            z: (bbox.min.z + bbox.max.z) / 2
        };

        let avgDistance = 0;
        vertices.forEach(v => {
            const dist = Math.sqrt(
                Math.pow(v.x - center.x, 2) +
                Math.pow(v.y - center.y, 2) +
                Math.pow(v.z - center.z, 2)
            );
            avgDistance += dist;
        });
        avgDistance /= vertices.length;

        let variance = 0;
        vertices.forEach(v => {
            const dist = Math.sqrt(
                Math.pow(v.x - center.x, 2) +
                Math.pow(v.y - center.y, 2) +
                Math.pow(v.z - center.z, 2)
            );
            variance += Math.pow(dist - avgDistance, 2);
        });
        variance /= vertices.length;

        return variance / (avgDistance * avgDistance) < 0.3; // Low relative variance = uniform
    }

    checkEdgeConcentration(vertices, bbox) {
        // Simple heuristic: check if vertices are concentrated near bbox edges
        const tolerance = 0.1;
        let edgeCount = 0;

        vertices.forEach(v => {
            const nearXEdge = Math.abs(v.x - bbox.min.x) < tolerance || Math.abs(v.x - bbox.max.x) < tolerance;
            const nearYEdge = Math.abs(v.y - bbox.min.y) < tolerance || Math.abs(v.y - bbox.max.y) < tolerance;
            const nearZEdge = Math.abs(v.z - bbox.min.z) < tolerance || Math.abs(v.z - bbox.max.z) < tolerance;

            if (nearXEdge || nearYEdge || nearZEdge) {
                edgeCount++;
            }
        });

        return edgeCount / vertices.length > 0.6; // More than 60% near edges
    }

    analyzeDistributionSymmetry(vertices, bbox) {
        // Simple symmetry analysis
        const center = {
            x: (bbox.min.x + bbox.max.x) / 2,
            y: (bbox.min.y + bbox.max.y) / 2,
            z: (bbox.min.z + bbox.max.z) / 2
        };

        // Check distribution around center
        const quadrants = { pos: 0, neg: 0 };
        vertices.forEach(v => {
            const relX = v.x - center.x;
            if (relX > 0) quadrants.pos++;
            else quadrants.neg++;
        });

        const symmetryScore = 1 - Math.abs(quadrants.pos - quadrants.neg) / vertices.length;
        return {
            isSymmetric: symmetryScore > 0.7,
            symmetryScore
        };
    }

    analyzeSurfaceBoundaries(vertices, bbox) {
        // Analyze how vertices relate to bounding box boundaries
        const tolerance = 0.05;
        const boundaries = {
            minX: 0, maxX: 0, minY: 0, maxY: 0, minZ: 0, maxZ: 0
        };

        vertices.forEach(v => {
            if (Math.abs(v.x - bbox.min.x) < tolerance) boundaries.minX++;
            if (Math.abs(v.x - bbox.max.x) < tolerance) boundaries.maxX++;
            if (Math.abs(v.y - bbox.min.y) < tolerance) boundaries.minY++;
            if (Math.abs(v.y - bbox.max.y) < tolerance) boundaries.maxY++;
            if (Math.abs(v.z - bbox.min.z) < tolerance) boundaries.minZ++;
            if (Math.abs(v.z - bbox.max.z) < tolerance) boundaries.maxZ++;
        });

        return boundaries;
    }

    /**
     * Calculate advanced properties based on shape type
     * @param {string} shapeType - Type of shape (cylindrical, fillet, etc.)
     * @param {THREE.Vector3[]} vertices - Face vertices
     * @param {object} bbox - Bounding box
     * @param {object} context - Optional context (faceIdInfo, features, isHole)
     */
    calculateAdvancedProperties(shapeType, vertices, bbox, context = {}) {
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const faceIdInfo = context.faceIdInfo || {};
        const features = context.features || [];
        const faceId = faceIdInfo.realFaceId || '';

        switch (shapeType) {
            case 'hole':
            case 'cylindrical':
                // 🔥 FIX: For holes, use "Depth" instead of "Height"
                const [largest, medium, smallest] = dims;

                // 🔥 ALWAYS check geometry pattern first (more reliable than feature matching)
                // For cylindrical holes: 2 similar dimensions = diameter, different one = depth
                // This is a common pattern for through-holes in sheet metal
                const similarityThreshold = 0.1; // 10% tolerance
                const largestMediumRatio = medium / largest;
                const isHolePattern = Math.abs(1.0 - largestMediumRatio) < similarityThreshold &&
                    smallest < largest * 0.5; // Smallest is much smaller (depth << diameter)

                // Also check if detected from features
                const isHole = this.isHoleSurface(faceId, features, faceIdInfo);

                const isActuallyHole = isHole || isHolePattern;

                // Debug logging
                if (isHolePattern && !isHole) {
                    console.log(`🔍 [GEOMETRY PATTERN] Detected hole pattern for face ${faceId}: largest=${largest.toFixed(2)}, medium=${medium.toFixed(2)}, smallest=${smallest.toFixed(2)}`);
                }

                if (isActuallyHole) {
                    // 🔥 FIX: For cylindrical holes, 2 similar dimensions = diameter, different one = depth
                    // Check if 2 dimensions are similar (within 10% tolerance) - they are diameter
                    const similarityThreshold = 0.1; // 10% tolerance
                    const largestMediumRatio = medium / largest;
                    let diameter, depth, radius;

                    if (Math.abs(1.0 - largestMediumRatio) < similarityThreshold) {
                        // 2 similar dimensions (largest ≈ medium) → they are diameter
                        // smallest is depth/thickness
                        diameter = (largest + medium) / 2.0; // Average of the 2 similar dimensions
                        depth = smallest;
                    } else {
                        // All 3 dimensions different - use aspect ratio logic
                        const aspectRatio = largest / smallest;

                        if (aspectRatio > 10.0) {
                            // Very high aspect ratio: largest = depth, smallest = diameter
                            diameter = smallest;
                            depth = largest;
                        } else {
                            // Moderate aspect ratio: check which 2 are more similar
                            const largestSmallestRatio = smallest / largest;
                            const mediumSmallestRatio = smallest / medium;

                            if (Math.abs(1.0 - largestSmallestRatio) < Math.abs(1.0 - mediumSmallestRatio)) {
                                // largest and smallest are more similar → diameter
                                diameter = (largest + smallest) / 2.0;
                                depth = medium;
                            } else {
                                // medium and smallest are more similar → diameter
                                diameter = (medium + smallest) / 2.0;
                                depth = largest;
                            }
                        }
                    }

                    radius = diameter / 2.0;

                    return {
                        'Shape Type': 'Hole',
                        'Depth': `${depth.toFixed(2)} mm`,  // ✅ FIX: Correct depth calculation
                        'Radius': `${radius.toFixed(2)} mm`,
                        'Diameter': `${diameter.toFixed(2)} mm`,
                        'Vertex Count': vertices.length
                    };
                } else {
                    // For solid cylinders: use Height
                    // But first check if it might be a hole based on geometry pattern
                    // If 2 dimensions are similar and smallest is much smaller, it's likely a hole
                    const checkSimilarityThreshold = 0.1;
                    const checkLargestMediumRatio = medium / largest;
                    const mightBeHole = Math.abs(1.0 - checkLargestMediumRatio) < checkSimilarityThreshold &&
                        smallest < largest * 0.5;

                    if (mightBeHole) {
                        // Treat as hole even if not in features - geometry pattern suggests hole
                        const diameter = (largest + medium) / 2.0;
                        const depth = smallest;
                        const radius = diameter / 2.0;

                        return {
                            'Shape Type': 'Hole',
                            'Depth': `${depth.toFixed(2)} mm`,
                            'Radius': `${radius.toFixed(2)} mm`,
                            'Diameter': `${diameter.toFixed(2)} mm`,
                            'Vertex Count': vertices.length
                        };
                    }

                    // True solid cylinder: largest = height, medium/smallest = diameter
                    const height = largest;
                    const radius = Math.max(medium, smallest) / 2.0;

                    return {
                        'Shape Type': 'Hole',
                        'Depth': `${height.toFixed(2)} mm`,
                        'Radius': `${radius.toFixed(2)} mm`,
                        'Surface Area': `${(2 * Math.PI * radius * height).toFixed(2)} mm²`,
                        'Vertex Count': vertices.length
                    };
                }

            case 'fillet':
                // For fillet surfaces, radius is typically one of the larger dimensions
                // not the smallest (which might be thickness)
                const sortedDims = [...dims].sort((a, b) => b - a);
                const filletRadius = sortedDims[1]; // Second largest dimension
                const length = sortedDims[0]; // Largest dimension
                return {
                    'Shape Type': 'Fillet Surface',
                    'Fillet Radius': `${filletRadius.toFixed(2)} mm`,
                    'Length': `${length.toFixed(2)} mm`,
                    'Vertex Count': vertices.length
                };

            case 'rectangular':
                return {
                    'Shape Type': 'Rectangular Surface',
                    'Length': `${dims[0].toFixed(2)} mm`,
                    'Width': `${dims[1].toFixed(2)} mm`,
                    'Area': `${(dims[0] * dims[1]).toFixed(2)} mm²`,
                    'Vertex Count': vertices.length
                };

            default:
                return {
                    'Shape Type': 'Curved Surface',
                    'Dimensions': `${dims[0].toFixed(2)} × ${dims[1].toFixed(2)} × ${dims[2].toFixed(2)} mm`,
                    'Vertex Count': vertices.length
                };
        }
    }

    /**
     * Check if a cylindrical surface is part of a hole feature
     * @param {string} faceId - Face ID
     * @param {Array} features - Array of features
     * @param {object} faceIdInfo - Face metadata
     * @returns {boolean} True if this surface is part of a hole
     */
    isHoleSurface(faceId, features, faceIdInfo) {
        if (!features || !Array.isArray(features) || !faceId) {
            return false;
        }

        // Check if this face is part of a hole feature
        const holeFeature = features.find(feature => {
            if (feature.type === 'hole' || feature.type === 'square_hole' || feature.type === 'rectangular_hole') {
                const faceIds = feature.face_ids || [];
                return faceIds.includes(faceId);
            }
            return false;
        });

        if (holeFeature) {
            return true;
        }

        // Check face ID patterns for holes
        const idLower = faceId.toLowerCase();
        const holePatterns = [
            /hole/i,
            /bore/i,
            /drill/i,
            /tap/i,
            /thread/i
        ];

        return holePatterns.some(pattern => pattern.test(idLower));
    }

    /**
     * Get display name for shape type
     */
    getShapeDisplayName(shapeType) {
        const displayNames = {
            'hole': 'Hole',
            'cylindrical': 'Hole',
            'square_hole': 'Square Hole',
            'rectangular_hole': 'Rectangular Hole',
            'fillet': 'Fillet',
            'rectangular': 'Rectangular Face',
            'square': 'Square Face',
            'curved': 'Curved Face',
            'complex': 'Complex Surface'
        };
        return displayNames[shapeType] || 'Unknown Shape';
    }
}

// Make the enhanced class globally available for the viewer
window.FaceAnalyzer = FaceAnalyzer;

// Make the enhanced class globally available for the viewer
window.FaceAnalyzer = FaceAnalyzer;
