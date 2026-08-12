/**
 * Simple Face Analyzer - Simple analysis based on triangles and vertices
 * Focus on analyzing clicked face (triangles: 2, vertices: 6)
 */
class SimpleFaceAnalyzer {
    constructor() {
        console.log('🔍 SimpleFaceAnalyzer initializing...');
        try {
            this.utils = new GeometryUtils();
            console.log('✅ GeometryUtils initialized in SimpleFaceAnalyzer');
        } catch (error) {
            console.error('❌ Failed to initialize GeometryUtils in SimpleFaceAnalyzer:', error);
            this.utils = null;
        }
    }

    /**
     * Analyze face based on triangles and vertices
     * @param {Object} meshInfo - {triangles: 2, vertices: 6}
     * @param {THREE.Mesh} mesh - Three.js mesh object
     * @param {THREE.Vector3} clickPoint - Click point
     * @returns {Object} Analysis result
     */
    analyzeFace(meshInfo, mesh, clickPoint) {
        console.log(`🔍 Analyzing face: ${meshInfo.triangles} triangles, ${meshInfo.vertices} vertices`);

        try {
            // Extract vertices from geometry
            const vertices = this.extractVertices(mesh.geometry);
            console.log(`📊 Extracted ${vertices.length} vertices`);

            // Analyze shape based on pattern
            const shapeAnalysis = this.analyzeShapePattern(meshInfo.triangles, meshInfo.vertices, vertices, meshInfo);

            // Calculate dimensions
            const dimensions = this.calculateDimensions(vertices);

            // Calculate properties
            const properties = this.calculateProperties(shapeAnalysis, dimensions);
            
            return {
                success: true,
                shapeType: shapeAnalysis.type,
                confidence: shapeAnalysis.confidence,
                analysis: {
                    triangles: meshInfo.triangles,
                    vertices: meshInfo.vertices,
                    shapePattern: shapeAnalysis.pattern,
                    geometry: shapeAnalysis.geometry
                },
                dimensions: {
                    length: dimensions.length.toFixed(2),
                    width: dimensions.width.toFixed(2),
                    height: dimensions.height.toFixed(2)
                },
                properties,
                timestamp: new Date().toISOString()
            };
            
        } catch (error) {
            console.error('❌ Error analyzing face:', error);
            return {
                success: false,
                error: error.message,
                shapeType: 'unknown'
            };
        }
    }

    /**
     * Extract vertices from geometry
     */
    extractVertices(geometry) {
        const vertices = [];
        
        if (geometry.attributes.position) {
            const positions = geometry.attributes.position;
            for (let i = 0; i < positions.count; i++) {
                vertices.push(new THREE.Vector3(
                    positions.getX(i),
                    positions.getY(i),
                    positions.getZ(i)
                ));
            }
        }
        
        return vertices;
    }

    /**
     * Analyze pattern based on triangles and vertices
     */
    analyzeShapePattern(triangles, vertices, vertexPositions, meshInfo = null) {
        console.log(`🎯 Analyzing pattern: ${triangles} triangles, ${vertices} vertices`);

        // Case: 2 triangles, 6 vertices (simple faces)
        if (triangles === 2 && vertices === 6) {
            return this.analyze2Triangles6Vertices(vertexPositions);
        }

        // Case: 1 triangle, 3 vertices
        if (triangles === 1 && vertices === 3) {
            return {
                type: 'triangle',
                pattern: 'single_triangle',
                confidence: 0.9,
                geometry: {
                    bbox: this.calculateBoundingBox(vertexPositions),
                    isFlat: true
                }
            };
        }

        // NEW: High-polygon geometry analysis (14+ triangles, 42+ vertices)
        if (triangles >= 4 && vertices >= 12) {
            console.log('🔍 Analyzing high-polygon geometry...');
            return this.analyzeHighPolygonGeometry(triangles, vertices, vertexPositions, meshInfo);
        }

        // Medium complexity geometry (3-13 triangles)
        if (triangles >= 3 && triangles <= 13) {
            console.log('🔍 Analyzing medium-complexity geometry...');
            return this.analyzeMediumComplexityGeometry(triangles, vertices, vertexPositions);
        }

        // Default case for other patterns - now with basic geometric analysis
        console.log('🔍 Analyzing with fallback geometric analysis...');
        return this.analyzeFallbackGeometry(triangles, vertices, vertexPositions);
    }

    /**
     * Analyze 2 triangles, 6 vertices (most common case)
     */
    analyze2Triangles6Vertices(vertices) {
        console.log('🔍 Analyzing 2 triangles, 6 vertices pattern...');

        if (vertices.length !== 6) {
            console.warn(`⚠️ Expected 6 vertices, got ${vertices.length}`);
        }

        // Calculate bounding box
        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;

        // Analyze dimension ratios
        const ratios = this.analyzeDimensionRatios(size);

        // Check coplanarity (all vertices on same plane?)
        const planarity = this.checkPlanarity(vertices);

        // Analyze shape
        let shapeType, confidence, pattern;
        
        if (planarity.isCoplanar) {
            // Flat surface analysis
            if (ratios.isFlat) {
                // Very thin → rectangular face
                shapeType = 'rectangular';
                pattern = 'flat_rectangular_face';
                confidence = 0.9;
            } else if (ratios.isSquare) {
                // Square proportions → square face
                shapeType = 'square';
                pattern = 'square_face';
                confidence = 0.85;
            } else {
                // Normal proportions → rectangular face
                shapeType = 'rectangular';
                pattern = 'rectangular_face';
                confidence = 0.8;
            }
        } else {
            // Curved surface analysis
            if (this.looksLikeCylindricalSurface(vertices, bbox)) {
                shapeType = 'hole';
                pattern = 'hole_surface_segment';
                confidence = 0.75;
            } else {
                shapeType = 'curved';
                pattern = 'curved_surface';
                confidence = 0.6;
            }
        }
        
        console.log(`✅ Detected: ${shapeType} (${pattern}) - confidence: ${confidence}`);
        
        return {
            type: shapeType,
            pattern,
            confidence,
            geometry: {
                bbox,
                ratios,
                planarity,
                vertices: vertices, // Pass vertices for parameter calculation
                isFlat: planarity.isCoplanar && ratios.isFlat,
                isCurved: !planarity.isCoplanar
            }
        };
    }

    /**
     * Calculate bounding box
     */
    calculateBoundingBox(vertices) {
        if (vertices.length === 0) return null;

        const min = vertices[0].clone();
        const max = vertices[0].clone();

        for (const vertex of vertices) {
            min.min(vertex);
            max.max(vertex);
        }

        const size = max.clone().sub(min);
        const center = min.clone().add(max).multiplyScalar(0.5);

        return { min, max, size, center };
    }

    /**
     * Analyze dimension ratios
     */
    analyzeDimensionRatios(size) {
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        const aspectRatio = largest / smallest;
        const isFlat = smallest < largest * 0.1;
        const isSquare = Math.abs(largest - medium) < largest * 0.1;

        return {
            largest,
            medium,
            smallest,
            aspectRatio,
            isFlat,
            isSquare
        };
    }

    /**
     * Check coplanarity (all vertices on same plane?)
     */
    checkPlanarity(vertices) {
        if (vertices.length < 4) {
            return { isCoplanar: true, confidence: 1.0 };
        }

        // Calculate normal from first 3 vertices
        const v1 = vertices[1].clone().sub(vertices[0]);
        const v2 = vertices[2].clone().sub(vertices[0]);
        const normal = v1.cross(v2).normalize();

        // Check remaining vertices
        let maxDeviation = 0;
        for (let i = 3; i < vertices.length; i++) {
            const v = vertices[i].clone().sub(vertices[0]);
            const deviation = Math.abs(v.dot(normal));
            maxDeviation = Math.max(maxDeviation, deviation);
        }

        const tolerance = 0.01;
        const isCoplanar = maxDeviation < tolerance;
        const confidence = isCoplanar ? 1.0 - (maxDeviation / tolerance) : 0.0;

        return { isCoplanar, confidence, maxDeviation, normal };
    }

    /**
     * Analyze high-polygon geometry (14+ triangles, 42+ vertices)
     * This handles complex curved surfaces like cylindrical faces and fillets
     */
    analyzeHighPolygonGeometry(triangles, vertices, vertexPositions, meshInfo = null) {
        console.log(`🔍 High-polygon analysis: ${triangles} triangles, ${vertices} vertices`);

        // Calculate bounding box and basic properties
        const bbox = this.calculateBoundingBox(vertexPositions);
        const ratios = this.analyzeDimensionRatios(bbox.size);
        const planarity = this.checkPlanarity(vertexPositions);

        // Advanced geometric analysis for high-poly surfaces
        const curvatureAnalysis = this.analyzeSurfaceCurvature(vertexPositions, bbox);
        const surfaceType = this.classifyHighPolySurface(vertexPositions, bbox, curvatureAnalysis, meshInfo);

        let shapeType, confidence, pattern;

        // Enhanced classification based on fillet vs cylindrical analysis
        if (surfaceType.isCylindrical) {
            shapeType = 'hole';
            pattern = 'high_poly_hole_surface';
            confidence = surfaceType.confidence;
        } else if (surfaceType.isFillet) {
            shapeType = 'fillet';
            pattern = 'high_poly_fillet_surface';
            confidence = surfaceType.confidence;
        } else if (surfaceType.isCurved) {
            shapeType = 'curved';
            pattern = 'high_poly_curved_surface';
            confidence = surfaceType.confidence;
        } else if (planarity.isCoplanar) {
            // High-poly but flat surface (detailed rectangular face)
            shapeType = 'rectangular';
            pattern = 'high_poly_rectangular_face';
            confidence = 0.8;
        } else {
            // Complex surface - analyze further
            shapeType = 'complex';
            pattern = 'high_poly_complex_surface';
            confidence = 0.6;
        }

        console.log(`✅ High-poly detected: ${shapeType} (${pattern}) - confidence: ${confidence}`);

        return {
            type: shapeType,
            pattern,
            confidence,
            geometry: {
                bbox,
                ratios,
                planarity,
                curvatureAnalysis,
                surfaceType,
                vertices: vertexPositions,
                isHighPoly: true,
                triangleCount: triangles,
                vertexCount: vertices
            }
        };
    }

    /**
     * Analyze medium-complexity geometry (3-13 triangles)
     */
    analyzeMediumComplexityGeometry(triangles, vertices, vertexPositions) {
        console.log(`🔍 Medium-complexity analysis: ${triangles} triangles, ${vertices} vertices`);

        const bbox = this.calculateBoundingBox(vertexPositions);
        const ratios = this.analyzeDimensionRatios(bbox.size);
        const planarity = this.checkPlanarity(vertexPositions);

        let shapeType, confidence, pattern;

        if (planarity.isCoplanar) {
            // Flat surface with medium detail
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
            // Curved surface with medium detail
            if (this.looksLikeCylindricalSurface(vertexPositions, bbox)) {
                shapeType = 'hole';
                pattern = 'medium_poly_hole_surface';
                confidence = 0.7;
            } else {
                shapeType = 'curved';
                pattern = 'medium_poly_curved_surface';
                confidence = 0.6;
            }
        }

        console.log(`✅ Medium-poly detected: ${shapeType} (${pattern}) - confidence: ${confidence}`);

        return {
            type: shapeType,
            pattern,
            confidence,
            geometry: {
                bbox,
                ratios,
                planarity,
                vertices: vertexPositions,
                isMediumPoly: true,
                triangleCount: triangles,
                vertexCount: vertices
            }
        };
    }

    /**
     * Fallback geometric analysis for unrecognized patterns
     */
    analyzeFallbackGeometry(triangles, vertices, vertexPositions) {
        console.log(`🔍 Fallback analysis: ${triangles} triangles, ${vertices} vertices`);

        const bbox = this.calculateBoundingBox(vertexPositions);
        const ratios = this.analyzeDimensionRatios(bbox.size);
        const planarity = this.checkPlanarity(vertexPositions);

        // Perform basic geometric analysis instead of returning generic
        let shapeType, confidence, pattern;

        if (planarity.isCoplanar) {
            // Flat surface
            if (ratios.isFlat) {
                shapeType = 'rectangular';
                pattern = 'flat_surface';
                confidence = 0.6;
            } else if (ratios.isSquare) {
                shapeType = 'square';
                pattern = 'square_surface';
                confidence = 0.6;
            } else {
                shapeType = 'rectangular';
                pattern = 'rectangular_surface';
                confidence = 0.6;
            }
        } else {
            // Non-planar surface - try to classify
            if (this.looksLikeCylindricalSurface(vertexPositions, bbox)) {
                shapeType = 'hole';
                pattern = 'hole_surface';
                confidence = 0.5;
            } else {
                shapeType = 'curved';
                pattern = 'curved_surface';
                confidence = 0.5;
            }
        }

        console.log(`✅ Fallback detected: ${shapeType} (${pattern}) - confidence: ${confidence}`);

        return {
            type: shapeType,
            pattern,
            confidence,
            geometry: {
                bbox,
                ratios,
                planarity,
                vertices: vertexPositions,
                isFallback: true,
                triangleCount: triangles,
                vertexCount: vertices
            }
        };
    }

    /**
     * Check if looks like cylindrical surface
     */
    looksLikeCylindricalSurface(vertices, bbox) {
        // Simple heuristic: if 2 dimensions are similar, 1 dimension different
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // Cylindrical: 2 dims similar (diameter), 1 different (height)
        const ratio1 = medium / largest;
        const ratio2 = smallest / medium;

        return ratio1 > 0.8 && ratio2 < 0.8;
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
     * Classify high-polygon surface type with enhanced fillet vs cylindrical detection
     */
    classifyHighPolySurface(vertices, bbox, curvatureAnalysis, meshInfo = null) {
        console.log('🔍 Classifying high-poly surface type...');

        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // Enhanced analysis for fillet vs cylindrical detection
        const groupName = meshInfo ? (meshInfo.groupName || meshInfo.name || '') : '';
        const filletVsCylindricalAnalysis = this.analyzeFilletVsCylindrical(vertices, bbox, curvatureAnalysis, groupName);

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
                result.isCurved = true; // Fillet is a type of curved surface
                result.confidence = filletVsCylindricalAnalysis.confidence;
                result.detectionMethod = 'enhanced_fillet';
                console.log('🌊 Enhanced detection: Fillet surface');
            } else if (curvatureAnalysis.curvatureIntensity > 0.3) {
                // High curvature but not specifically cylindrical or fillet
                result.isCurved = true;
                result.confidence = 0.7;
                result.detectionMethod = 'general_curved';
                console.log('🌊 Classified as general curved surface');
            } else {
                // Low curvature
                result.isCurved = true;
                result.confidence = 0.6;
                result.detectionMethod = 'low_curvature';
                console.log('🌊 Classified as lightly curved surface');
            }
        } else {
            // Flat surface with high polygon count
            result.isFlat = true;
            result.confidence = 0.7;
            result.detectionMethod = 'flat_high_poly';
            console.log('📄 Classified as flat high-poly surface');
        }

        return result;
    }

    /**
     * 🔥 ENHANCED FILLET VS CYLINDRICAL DETECTION ALGORITHM
     *
     */
    analyzeFilletVsCylindrical(vertices, bbox, curvatureAnalysis, groupName = '') {
        console.log('🔥 Enhanced Fillet vs Cylindrical Analysis...');

        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // Use group naming context for better classification
        const hasFilletIndicator = (groupName.toLowerCase().includes('r') && !groupName.toLowerCase().includes('cylinder')) ||
                                  groupName.toLowerCase().includes('fillet') ||
                                  groupName.toLowerCase().includes('coin');
        const hasCylinderIndicator = groupName.toLowerCase().includes('cylinder') ||
                                    groupName.toLowerCase().includes('tube') ||
                                    groupName.toLowerCase().includes('pipe') ||
                                    (groupName.toLowerCase().includes('d') && groupName.toLowerCase().includes('h'));

        // 1. ASPECT RATIO ANALYSIS
        const aspectRatio = largest / smallest;
        const mediumRatio = medium / smallest;

        // Apply group naming context boost
        if (hasFilletIndicator) {
            console.log(`🏷️ Group name indicates fillet: "${groupName}"`);
        }
        if (hasCylinderIndicator) {
            console.log(`🏷️ Group name indicates cylinder: "${groupName}"`);
        }

        console.log(`📏 Dimensions: L=${largest.toFixed(2)}, M=${medium.toFixed(2)}, S=${smallest.toFixed(2)}`);
        console.log(`📊 Ratios: Aspect=${aspectRatio.toFixed(2)}, Medium=${mediumRatio.toFixed(2)}`);
        console.log(`🔍 Enhanced Analysis: vertices=${vertices.length}, triangles≈${(vertices.length/3).toFixed(0)}`);

        // 2. CURVATURE DIRECTION ANALYSIS
        const curvatureDirections = this.analyzeCurvatureDirections(vertices);
        console.log('🧭 Curvature Directions:', curvatureDirections);

        // 3. SURFACE CONTINUITY ANALYSIS
        const surfaceContinuity = this.analyzeSurfaceContinuity(vertices, bbox);
        console.log('🔗 Surface Continuity:', surfaceContinuity);

        // 4. VERTEX DISTRIBUTION PATTERN
        const vertexDistribution = this.analyzeVertexDistribution(vertices, bbox);
        console.log('📍 Vertex Distribution:', vertexDistribution);

        // 5. NEIGHBOR SURFACE CONTEXT (simulated for now)
        const neighborContext = this.analyzeNeighborContext(vertices, bbox);
        console.log('🏘️ Neighbor Context:', neighborContext);

        // ENHANCED DECISION ALGORITHM WITH BETTER LOGIC
        let isCylindrical = false;
        let isFillet = false;
        let confidence = 0.5;
        let reasoning = [];

        console.log(`🔍 Analysis Data:`, {
            aspectRatio: aspectRatio.toFixed(2),
            mediumRatio: mediumRatio.toFixed(2),
            hasAxisSymmetry: curvatureDirections.hasAxisSymmetry,
            isSingleAxis: curvatureDirections.isSingleAxis,
            isMultiDirectional: curvatureDirections.isMultiDirectional,
            isComplete: surfaceContinuity.isComplete,
            isTransition: surfaceContinuity.isTransition,
            isUniform: vertexDistribution.isUniform,
            isEdgeConcentrated: vertexDistribution.isEdgeConcentrated
        });

        // ENHANCED DETECTION BASED ON TRIANGLE/VERTEX COUNT AND GEOMETRY
        let cylindricalScore = 0;
        let filletScore = 0;

        // PRIMARY DISCRIMINATOR: Triangle/Vertex count pattern
        const triangleCount = vertices.length / 3; // Approximate triangle count
        const vertexDensity = vertices.length / (largest * medium * smallest); // Vertices per unit volume

        console.log(`📊 Geometry Stats: Triangles≈${triangleCount.toFixed(0)}, VertexDensity=${vertexDensity.toFixed(3)}`);

        // OPTIMIZED VERTEX COUNT ANALYSIS FOR CYLINDRICAL VS FILLET DISCRIMINATION
        const vertexCountAnalysis = this.analyzeVertexCountPattern(vertices.length, aspectRatio, mediumRatio);

        console.log(`🔢 Vertex analysis: count=${vertices.length}, pattern=${vertexCountAnalysis.pattern}`);

        // Apply vertex count scoring based on discriminative patterns
        if (vertexCountAnalysis.pattern === 'cylindrical_signature') {
            cylindricalScore += vertexCountAnalysis.confidence;
            reasoning.push(`Vertex pattern (${vertices.length}) matches cylindrical signature`);
        } else if (vertexCountAnalysis.pattern === 'fillet_signature') {
            filletScore += vertexCountAnalysis.confidence;
            reasoning.push(`Vertex pattern (${vertices.length}) matches fillet signature`);
        } else if (vertexCountAnalysis.pattern === 'ambiguous') {
            // Critical discrimination zone - use secondary analysis
            const secondaryAnalysis = this.performSecondaryDiscrimination(vertices, aspectRatio, mediumRatio);
            if (secondaryAnalysis.type === 'hole' || secondaryAnalysis.type === 'cylindrical') {
                cylindricalScore += secondaryAnalysis.confidence;
                reasoning.push(`Secondary analysis resolves ambiguity: ${secondaryAnalysis.reason}`);
            } else {
                filletScore += secondaryAnalysis.confidence;
                reasoning.push(`Secondary analysis resolves ambiguity: ${secondaryAnalysis.reason}`);
            }
        }

        // ENHANCED DIMENSION PATTERN ANALYSIS (Primary Discriminator)
        const dimensionAnalysis = this.analyzeDimensionPattern(largest, medium, smallest, aspectRatio, mediumRatio);

        // Apply dimension pattern scoring with higher weights
        if (dimensionAnalysis.pattern === 'cylindrical_signature') {
            cylindricalScore += 0.6; // Increased weight
            reasoning.push(`Strong cylindrical dimension signature: ${dimensionAnalysis.evidence}`);
        } else if (dimensionAnalysis.pattern === 'fillet_signature') {
            filletScore += 0.6; // Increased weight
            reasoning.push(`Strong fillet dimension signature: ${dimensionAnalysis.evidence}`);
        } else if (dimensionAnalysis.pattern === 'cylindrical_likely') {
            cylindricalScore += 0.4;
            reasoning.push(`Likely cylindrical: ${dimensionAnalysis.evidence}`);
        } else if (dimensionAnalysis.pattern === 'fillet_likely') {
            filletScore += 0.4;
            reasoning.push(`Likely fillet: ${dimensionAnalysis.evidence}`);
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
            // Medium dimension is significantly smaller than largest but not tiny
            filletScore += 0.3;
            reasoning.push(`Progressive dimension reduction (${largeToMediumRatio.toFixed(1)}) suggests fillet`);
        }

        // Large fillet: thickness much smaller than other dimensions
        if (smallest < Math.min(largest, medium) * 0.4) {
            filletScore += 0.2;
            reasoning.push(`Thin dimension (${smallest.toFixed(1)}) suggests fillet or edge feature`);
        }

        // Enhanced fillet detection for various sizes
        if (smallest < largest * 0.3) { // Increased from 0.2 to 0.3
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

        // Apply group naming context boost with priority for strong indicators
        if (hasCylinderIndicator && aspectRatio > 3.0) {
            // Strong cylinder indicator with typical proportions - force cylindrical
            cylindricalScore = 1.0; // Force cylindrical classification
            filletScore = 0.0; // Override any fillet score
            reasoning.push(`STRONG CYLINDER OVERRIDE: "${groupName}" with aspect=${aspectRatio.toFixed(1)}`);
        } else if (hasCylinderIndicator) {
            cylindricalScore += 0.5; // Strong boost for cylinder indicators
            reasoning.push(`Strong cylinder indicator: "${groupName}"`);
        } else if (hasFilletIndicator) {
            filletScore += 0.3;
            reasoning.push(`Group name boost: "${groupName}" indicates fillet`);
        }

        // ENHANCED DECISION BASED ON SCORES WITH IMPROVED CONFIDENCE
        console.log(`🎯 Final scores: Cylindrical=${cylindricalScore.toFixed(2)}, Fillet=${filletScore.toFixed(2)}`);
        console.log(`📝 Reasoning: ${reasoning.join('; ')}`);

        // Calculate confidence based on score difference and absolute values
        const scoreDifference = Math.abs(cylindricalScore - filletScore);
        const maxScore = Math.max(cylindricalScore, filletScore);

        if (cylindricalScore > filletScore && cylindricalScore > 0.4) {
            isCylindrical = true;
            // Enhanced confidence: higher when score difference is large and max score is high
            confidence = Math.min(0.6 + (cylindricalScore * 0.3) + (scoreDifference * 0.2), 0.95);
            reasoning.push(`Cylindrical wins with score ${cylindricalScore.toFixed(2)} (margin: ${scoreDifference.toFixed(2)})`);
        } else if (filletScore > cylindricalScore && filletScore > 0.4) {
            isFillet = true;
            // Enhanced confidence: higher when score difference is large and max score is high
            confidence = Math.min(0.6 + (filletScore * 0.3) + (scoreDifference * 0.2), 0.95);
            reasoning.push(`Fillet wins with score ${filletScore.toFixed(2)} (margin: ${scoreDifference.toFixed(2)})`);
        } else {
            // IMPROVED FALLBACK LOGIC
            const dimensionSpread = (largest - smallest) / largest;
            const mediumPosition = (medium - smallest) / (largest - smallest);

            console.log(`🔄 Fallback analysis: spread=${dimensionSpread.toFixed(2)}, mediumPos=${mediumPosition.toFixed(2)}, aspect=${aspectRatio.toFixed(1)}`);

            if (aspectRatio > 10.0 && mediumPosition > 0.9 && smallest < largest * 0.15 && !hasFilletIndicator) {
                // VERY extreme aspect + medium very close to largest + very thin → cylindrical (only if no fillet indicator)
                isCylindrical = true;
                confidence = 0.6;
                reasoning.push(`Fallback: Extreme aspect ratio (${aspectRatio.toFixed(1)}) with true cylindrical proportions`);
            } else if (hasFilletIndicator && aspectRatio > 5.0) {
                // Strong fillet indicator with high aspect ratio → fillet
                isFillet = true;
                confidence = 0.7;
                reasoning.push(`Fallback: Fillet indicator override for high aspect ratio (${aspectRatio.toFixed(1)})`);
            } else if (dimensionSpread > 0.6 && smallest < largest * 0.3) {
                // High spread + thin dimension → fillet
                isFillet = true;
                confidence = 0.6;
                reasoning.push(`Fallback: High dimension spread (${dimensionSpread.toFixed(2)}) suggests fillet`);
            } else if (aspectRatio < 2.0) {
                isFillet = true;
                confidence = 0.6;
                reasoning.push('Fallback: Low aspect ratio → fillet');
            } else {
                // Conservative default - changed from cylindrical to fillet
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
                mediumRatio,
                curvatureDirections,
                surfaceContinuity,
                vertexDistribution,
                neighborContext
            }
        };

        // Enhanced logging for debugging
        console.log(`🏆 ENHANCED FINAL SCORES:`);
        console.log(`   Cylindrical Score: ${cylindricalScore.toFixed(2)}`);
        console.log(`   Fillet Score: ${filletScore.toFixed(2)}`);
        console.log(`🎯 FINAL RESULT: ${isCylindrical ? 'CYLINDRICAL' : 'FILLET'} (confidence: ${confidence.toFixed(2)})`);
        console.log(`📊 Key Metrics: aspect=${aspectRatio.toFixed(1)}, medium=${mediumRatio.toFixed(1)}, vertices=${vertices.length}`);
        console.log(`💭 Reasoning: ${reasoning.join('; ')}`);

        return result;
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
            variationRatio: normalVariations.variationRatio,
            totalVariation: normalVariations.totalVariation
        };
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
            uniformityScore: isUniform ? 0.8 : 0.3,
            edgeConcentrationScore: isEdgeConcentrated ? 0.8 : 0.3
        };
    }

    /**
     * Analyze neighbor surface context (simulated)
     */
    analyzeNeighborContext(vertices, bbox) {
        console.log('🏘️ Analyzing neighbor context...');

        // For now, simulate neighbor analysis based on geometry characteristics
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;

        // Simulate: if very elongated, likely standalone cylindrical
        const isStandalone = largest / smallest > 4.0;

        // Simulate: if compact, likely connects surfaces (fillet)
        const connectsSurfaces = largest / smallest < 2.0;

        return {
            isStandalone,
            connectsSurfaces,
            standaloneScore: isStandalone ? 0.8 : 0.3,
            connectionScore: connectsSurfaces ? 0.8 : 0.3
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

        for (let i = 0; i < normals.length - 1; i++) {
            const curr = normals[i];
            const next = normals[i + 1];

            variations.x += Math.abs(curr.x - next.x);
            variations.y += Math.abs(curr.y - next.y);
            variations.z += Math.abs(curr.z - next.z);
        }

        // Find dominant direction
        const maxVariation = Math.max(variations.x, variations.y, variations.z);
        const minVariation = Math.min(variations.x, variations.y, variations.z);

        let dominantDirection = null;
        if (variations.x === maxVariation) dominantDirection = 'x';
        else if (variations.y === maxVariation) dominantDirection = 'y';
        else dominantDirection = 'z';

        const variationRatio = minVariation > 0 ? maxVariation / minVariation : 10.0;
        const totalVariation = variations.x + variations.y + variations.z;

        return {
            dominantDirection,
            variationRatio,
            totalVariation: totalVariation / normals.length,
            variations
        };
    }

    /**
     * Detect axis symmetry in vertex distribution
     */
    detectAxisSymmetry(vertices, normals) {
        // Simple heuristic: check if normals show consistent pattern
        if (normals.length < 4) return false;

        const normalVariations = this.calculateNormalVariation(normals);
        return normalVariations < 0.3; // Low variation suggests axis symmetry
    }

    /**
     * Check surface completeness (cylindrical characteristic)
     */
    checkSurfaceCompleteness(vertices) {
        // Simple heuristic: if vertices span significant range, likely complete
        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;
        const volume = size.x * size.y * size.z;

        return volume > 100; // Arbitrary threshold for "complete" surface
    }

    /**
     * Check transition characteristics (fillet characteristic)
     */
    checkTransitionCharacteristics(vertices, bbox) {
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;

        // Transition surfaces are typically more compact
        return largest / smallest < 3.0;
    }

    /**
     * Analyze surface boundaries
     */
    analyzeSurfaceBoundaries(vertices, bbox) {
        const size = bbox.size;

        return {
            hasOpenBoundaries: true, // Simplified
            boundaryLength: size.x + size.y + size.z,
            isEnclosed: false
        };
    }

    /**
     * Check uniform distribution (cylindrical characteristic)
     */
    checkUniformDistribution(vertices, bbox) {
        if (vertices.length < 4) return false;

        // Calculate center of mass
        const center = vertices.reduce((acc, v) => {
            acc.x += v.x;
            acc.y += v.y;
            acc.z += v.z;
            return acc;
        }, { x: 0, y: 0, z: 0 });

        center.x /= vertices.length;
        center.y /= vertices.length;
        center.z /= vertices.length;

        // Check if vertices are roughly equidistant from center
        const distances = vertices.map(v =>
            Math.sqrt((v.x - center.x)**2 + (v.y - center.y)**2 + (v.z - center.z)**2)
        );

        const avgDistance = distances.reduce((a, b) => a + b, 0) / distances.length;
        const variance = distances.reduce((acc, d) => acc + (d - avgDistance)**2, 0) / distances.length;
        const stdDev = Math.sqrt(variance);

        // Uniform if standard deviation is small relative to average
        return stdDev / avgDistance < 0.3;
    }

    /**
     * Check edge concentration (fillet characteristic)
     */
    checkEdgeConcentration(vertices, bbox) {
        // Check if vertices are concentrated near edges of bounding box
        const tolerance = 0.1;
        const size = bbox.size;
        const min = bbox.min;
        const max = bbox.max;

        let edgeVertices = 0;

        for (const vertex of vertices) {
            const nearMinX = Math.abs(vertex.x - min.x) < tolerance * size.x;
            const nearMaxX = Math.abs(vertex.x - max.x) < tolerance * size.x;
            const nearMinY = Math.abs(vertex.y - min.y) < tolerance * size.y;
            const nearMaxY = Math.abs(vertex.y - max.y) < tolerance * size.y;
            const nearMinZ = Math.abs(vertex.z - min.z) < tolerance * size.z;
            const nearMaxZ = Math.abs(vertex.z - max.z) < tolerance * size.z;

            if (nearMinX || nearMaxX || nearMinY || nearMaxY || nearMinZ || nearMaxZ) {
                edgeVertices++;
            }
        }

        return edgeVertices / vertices.length > 0.6; // More than 60% near edges
    }

    /**
     * Analyze distribution symmetry
     */
    analyzeDistributionSymmetry(vertices, bbox) {
        const center = bbox.center;

        // Check symmetry around center
        let symmetryScore = 0;

        for (const vertex of vertices) {
            const mirrored = {
                x: 2 * center.x - vertex.x,
                y: 2 * center.y - vertex.y,
                z: 2 * center.z - vertex.z
            };

            // Find closest vertex to mirrored position
            let minDistance = Infinity;
            for (const other of vertices) {
                const distance = Math.sqrt(
                    (other.x - mirrored.x)**2 +
                    (other.y - mirrored.y)**2 +
                    (other.z - mirrored.z)**2
                );
                minDistance = Math.min(minDistance, distance);
            }

            // If close match found, increase symmetry score
            if (minDistance < bbox.size.length() * 0.1) {
                symmetryScore++;
            }
        }

        return {
            score: symmetryScore / vertices.length,
            isSymmetric: symmetryScore / vertices.length > 0.7
        };
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
     * Enhanced curvature flow analysis for cylindrical vs fillet discrimination
     */
    analyzeCurvatureFlow(vertices, bbox) {
        console.log('🌊 Analyzing curvature flow patterns...');

        if (vertices.length < 6) return { flowType: 'insufficient_data', confidence: 0 };

        const normals = this.calculateVertexNormals(vertices);
        const curvatureFlow = this.calculateCurvatureFlow(vertices, normals);

        // Cylindrical surfaces have consistent curvature direction
        const cylindricalFlow = this.detectCylindricalFlow(curvatureFlow, bbox);

        // Fillet surfaces have transitional curvature flow
        const filletFlow = this.detectFilletFlow(curvatureFlow, bbox);

        return {
            flowType: cylindricalFlow.confidence > filletFlow.confidence ? 'cylindrical' : 'fillet',
            cylindricalConfidence: cylindricalFlow.confidence,
            filletConfidence: filletFlow.confidence,
            flowMetrics: curvatureFlow
        };
    }

    /**
     * Calculate curvature flow vectors
     */
    calculateCurvatureFlow(vertices, normals) {
        const flowVectors = [];

        for (let i = 1; i < vertices.length - 1; i++) {
            const prev = vertices[i - 1];
            const curr = vertices[i];
            const next = vertices[i + 1];

            // Calculate curvature vector
            const curvature = this.calculateDiscreteGaussianCurvature(prev, curr, next, normals[i]);
            flowVectors.push(curvature);
        }

        return {
            vectors: flowVectors,
            magnitude: this.calculateAverageCurvatureMagnitude(flowVectors),
            consistency: this.calculateFlowConsistency(flowVectors)
        };
    }

    /**
     * Calculate geometry complexity for adaptive thresholds
     */
    calculateGeometryComplexity(vertices, largest, medium, smallest) {
        const aspectRatio = largest / smallest;
        const vertexDensity = vertices.length / (largest * medium * smallest);
        const dimensionVariance = Math.abs(largest - medium) + Math.abs(medium - smallest);

        // Complexity score: higher for more complex geometries
        return (aspectRatio * 0.3) + (vertexDensity * 50) + (dimensionVariance * 0.2);
    }

    /**
     * Optimized vertex count pattern analysis for cylindrical vs fillet discrimination
     */
    analyzeVertexCountPattern(vertexCount, aspectRatio, mediumRatio) {
        // Critical discrimination zones based on mesh tessellation patterns

        // Zone 1: Clear cylindrical signature (high vertex count + high aspect ratio)
        if (vertexCount >= 150 && aspectRatio > 8.0) {
            return { pattern: 'cylindrical_signature', confidence: 0.8 };
        }

        // Zone 2: Clear fillet signature (medium vertex count + compact geometry)
        if (vertexCount >= 30 && vertexCount <= 80 && aspectRatio < 4.0) {
            return { pattern: 'fillet_signature', confidence: 0.7 };
        }

        // Zone 3: Critical discrimination zone (your mesh falls here: 102 vertices)
        if (vertexCount >= 80 && vertexCount <= 150) {
            // Use aspect ratio as primary discriminator
            if (aspectRatio > 10.0 && mediumRatio > 4.0) {
                return { pattern: 'cylindrical_signature', confidence: 0.6 };
            } else if (aspectRatio < 3.0) {
                return { pattern: 'fillet_signature', confidence: 0.6 };
            } else {
                return { pattern: 'ambiguous', confidence: 0.3 };
            }
        }

        // Zone 4: Low vertex count - likely simple geometry
        if (vertexCount < 30) {
            return { pattern: 'simple_geometry', confidence: 0.4 };
        }

        return { pattern: 'unknown', confidence: 0.2 };
    }

    /**
     * Enhanced dimension pattern analysis - Primary discriminator for cylindrical vs fillet
     */
    analyzeDimensionPattern(largest, medium, smallest, aspectRatio, mediumRatio) {
        console.log(`📐 Analyzing dimension pattern: L=${largest.toFixed(2)}, M=${medium.toFixed(2)}, S=${smallest.toFixed(2)}`);

        // Calculate discriminative metrics
        const dimensionSpread = (largest - smallest) / largest;
        const mediumPosition = (medium - smallest) / (largest - smallest);
        const compactness = smallest / largest;
        const mediumToLargestRatio = medium / largest;

        console.log(`📊 Metrics: spread=${dimensionSpread.toFixed(3)}, mediumPos=${mediumPosition.toFixed(3)}, compact=${compactness.toFixed(3)}`);

        // OPTIMIZED CYLINDRICAL SIGNATURES
        // Signature 1: True cylinder (L >> M ≈ S, high aspect ratio)
        if (aspectRatio > 12.0 && mediumPosition > 0.85 && mediumToLargestRatio > 0.8) {
            return {
                pattern: 'cylindrical_signature',
                evidence: `True cylinder: aspect=${aspectRatio.toFixed(1)}, mediumPos=${mediumPosition.toFixed(2)}`
            };
        }

        // Signature 2: Elongated cylinder (high aspect + medium close to smallest)
        if (aspectRatio > 8.0 && mediumRatio > 4.0 && compactness < 0.15) {
            return {
                pattern: 'cylindrical_signature',
                evidence: `Elongated cylinder: aspect=${aspectRatio.toFixed(1)}, mediumRatio=${mediumRatio.toFixed(1)}`
            };
        }

        // OPTIMIZED FILLET SIGNATURES
        // Signature 1: Classic fillet (progressive dimension reduction)
        if (aspectRatio > 2.0 && aspectRatio < 6.0 && mediumRatio > 1.5 && mediumRatio < 3.0 && compactness > 0.15 && compactness < 0.5) {
            return {
                pattern: 'fillet_signature',
                evidence: `Classic fillet: aspect=${aspectRatio.toFixed(1)}, mediumRatio=${mediumRatio.toFixed(1)}, compact=${compactness.toFixed(2)}`
            };
        }

        // Signature 2: Large fillet (moderate spread + thin profile)
        if (dimensionSpread > 0.6 && dimensionSpread < 0.85 && compactness < 0.3 && mediumPosition < 0.7) {
            return {
                pattern: 'fillet_signature',
                evidence: `Large fillet: spread=${dimensionSpread.toFixed(2)}, compact=${compactness.toFixed(2)}`
            };
        }

        // LIKELY PATTERNS (lower confidence)
        if (aspectRatio > 6.0 && mediumPosition > 0.6) {
            return {
                pattern: 'cylindrical_likely',
                evidence: `Likely cylindrical: aspect=${aspectRatio.toFixed(1)}, mediumPos=${mediumPosition.toFixed(2)}`
            };
        }

        if (aspectRatio < 4.0 && compactness > 0.2) {
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
     * Secondary discrimination analysis for ambiguous cases
     */
    performSecondaryDiscrimination(vertices, aspectRatio, mediumRatio) {
        console.log('🔍 Performing secondary discrimination analysis...');

        // Calculate additional discriminative features
        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // Feature 1: Dimension distribution pattern
        const dimensionSpread = (largest - smallest) / largest;
        const mediumPosition = (medium - smallest) / (largest - smallest);

        // Feature 2: Vertex distribution uniformity
        const distributionMetrics = this.analyzeVertexDistribution(vertices, bbox);

        // Feature 3: Surface curvature consistency
        const curvatureMetrics = this.analyzeSurfaceCurvature(vertices, bbox);

        let cylindricalEvidence = 0;
        let filletEvidence = 0;
        let reason = '';

        // Cylindrical evidence: high dimension spread + medium close to largest + uniform distribution
        if (dimensionSpread > 0.8 && mediumPosition > 0.8 && distributionMetrics.isUniform) {
            cylindricalEvidence += 0.4;
            reason += 'Cylindrical dimension pattern + uniform distribution; ';
        }

        // Fillet evidence: moderate spread + progressive dimensions + edge concentration
        if (dimensionSpread > 0.4 && dimensionSpread < 0.8 && mediumPosition < 0.7 && distributionMetrics.isEdgeConcentrated) {
            filletEvidence += 0.4;
            reason += 'Fillet dimension pattern + edge concentration; ';
        }

        // Curvature evidence
        if (curvatureMetrics.curvatureType === 'cylindrical') {
            cylindricalEvidence += 0.3;
            reason += 'Cylindrical curvature pattern; ';
        } else if (curvatureMetrics.curvatureType === 'smooth_curved') {
            filletEvidence += 0.3;
            reason += 'Smooth curved pattern (fillet-like); ';
        }

        const finalConfidence = Math.max(cylindricalEvidence, filletEvidence);
        const finalType = cylindricalEvidence > filletEvidence ? 'hole' : 'fillet';

        return {
            type: finalType,
            confidence: Math.min(finalConfidence, 0.6), // Cap confidence for secondary analysis
            reason: reason.trim()
        };
    }

    /**
     * Classify curvature type based on normal patterns
     */
    classifyCurvatureType(normals, bbox) {
        if (normals.length < 3) return 'unknown';

        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        // Analyze normal distribution patterns
        const normalVariations = [];
        for (let i = 0; i < normals.length - 1; i++) {
            const dot = normals[i].x * normals[i + 1].x +
                       normals[i].y * normals[i + 1].y +
                       normals[i].z * normals[i + 1].z;
            normalVariations.push(Math.acos(Math.max(-1, Math.min(1, dot))));
        }

        const avgVariation = normalVariations.reduce((a, b) => a + b, 0) / normalVariations.length;
        const maxVariation = Math.max(...normalVariations);

        // Classify based on variation patterns and geometry
        if (largest / smallest > 3 && avgVariation > 0.2) {
            return 'hole';
        } else if (maxVariation > 0.5) {
            return 'complex_curved';
        } else if (avgVariation > 0.1) {
            return 'smooth_curved';
        } else {
            return 'minimal_curvature';
        }
    }



    /**
     * Calculate dimensions
     */
    calculateDimensions(vertices) {
        const bbox = this.calculateBoundingBox(vertices);
        if (!bbox) return { length: 0, width: 0, height: 0 };

        const size = bbox.size;

        // Convert to mm (with fallback if utils not available)
        if (this.utils && this.utils.convertDimension) {
            return {
                length: this.utils.convertDimension(size.x),
                width: this.utils.convertDimension(size.y),
                height: this.utils.convertDimension(size.z)
            };
        } else {
            // Fallback: assume units are already in reasonable scale
            return {
                length: size.x,
                width: size.y,
                height: size.z
            };
        }
    }

    /**
     * Validation framework for cylindrical vs fillet detection
     */
    validateDetection(vertices, detectedType, confidence, reasoning) {
        console.log('🧪 Validating detection results...');

        const validationTests = {
            vertex_count_consistency: this.validateVertexCount(vertices.length, detectedType),
            dimension_consistency: this.validateDimensions(vertices, detectedType),
            geometric_consistency: this.validateGeometry(vertices, detectedType),
            confidence_threshold: confidence > 0.6
        };

        const passedTests = Object.values(validationTests).filter(test => test).length;
        const totalTests = Object.keys(validationTests).length;
        const validationScore = passedTests / totalTests;

        console.log(`🧪 Validation: ${passedTests}/${totalTests} tests passed (${(validationScore * 100).toFixed(1)}%)`);

        return {
            isValid: validationScore >= 0.75,
            validationScore,
            failedTests: Object.entries(validationTests)
                .filter(([_, passed]) => !passed)
                .map(([test, _]) => test),
            recommendation: validationScore < 0.5 ? 'requires_manual_review' : 'acceptable'
        };
    }

    /**
     * Test cases for mesh validation
     */
    validateVertexCount(vertexCount, detectedType) {
        // Test case patterns for your mesh size range
        const testCases = {
            cylindrical: {
                strong: vertexCount >= 150,
                likely: vertexCount >= 100 && vertexCount < 150,
                possible: vertexCount >= 80 && vertexCount < 100
            },
            fillet: {
                strong: vertexCount >= 30 && vertexCount <= 80,
                likely: vertexCount > 80 && vertexCount <= 120,
                possible: vertexCount > 120 && vertexCount <= 150
            }
        };

        return testCases[detectedType]?.strong ||
               testCases[detectedType]?.likely ||
               testCases[detectedType]?.possible || false;
    }

    /**
     * Calculate shape-specific properties using enhanced algorithms
     */
    calculateProperties(shapeAnalysis, dimensions) {
        const { type, geometry } = shapeAnalysis;

        // Base properties with specific shape names
        const shapeDisplayNames = {
            'rectangular': 'Rectangular',
            'square': 'Square',
            'hole': 'Hole',
            'cylindrical': 'Hole',
            'cylinder': 'Hole',
            'fillet': 'Fillet',
            'curved': 'Curved Surface',
            'plane': 'Planar Surface',
            'generic': 'Generic Shape'
        };

        let properties = {
            'Shape Type': shapeDisplayNames[type] || type.charAt(0).toUpperCase() + type.slice(1)
        };

        // Calculate shape-specific parameters using vertices
        try {
            switch(type) {
                case 'hole':
                case 'cylindrical':
                case 'cylinder':
                    const cylinderParams = this.calculateCylinderParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Radius': `${cylinderParams.radius.toFixed(2)} mm`,
                        'Diameter': `${cylinderParams.diameter.toFixed(2)} mm`,
                        'Depth': `${cylinderParams.height.toFixed(2)} mm`,
                        'Volume': `${cylinderParams.volume.toFixed(2)} mm³`,
                        'Surface Area': `${cylinderParams.surface_area.toFixed(2)} mm²`,
                        'Lateral Area': `${cylinderParams.lateral_area.toFixed(2)} mm²`,
                        'Base Area': `${cylinderParams.base_area.toFixed(2)} mm²`,
                        'Circumference': `${cylinderParams.circumference.toFixed(2)} mm`,
                        'Axis Direction': cylinderParams.axis_direction
                    };
                    break;

                case 'rectangular':
                    const rectParams = this.calculateRectangleParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Length': `${rectParams.length.toFixed(2)} mm`,
                        'Width': `${rectParams.width.toFixed(2)} mm`,
                        'Thickness': `${rectParams.thickness.toFixed(2)} mm`,
                        'Area': `${rectParams.area.toFixed(2)} mm²`,
                        'Perimeter': `${rectParams.perimeter.toFixed(2)} mm`,
                        'Aspect Ratio': `${rectParams.aspect_ratio.toFixed(2)}`,
                        'Diagonal': `${rectParams.diagonal.toFixed(2)} mm`
                    };
                    break;

                case 'square':
                    const squareParams = this.calculateRectangleParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Side Length': `${Math.max(squareParams.length, squareParams.width).toFixed(2)} mm`,
                        'Thickness': `${squareParams.thickness.toFixed(2)} mm`,
                        'Area': `${squareParams.area.toFixed(2)} mm²`,
                        'Perimeter': `${squareParams.perimeter.toFixed(2)} mm`,
                        'Diagonal': `${squareParams.diagonal.toFixed(2)} mm`
                    };
                    break;

                case 'plane':
                    const planeParams = this.calculateRectangleParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Length': `${planeParams.length.toFixed(2)} mm`,
                        'Width': `${planeParams.width.toFixed(2)} mm`,
                        'Thickness': `${planeParams.thickness.toFixed(2)} mm`,
                        'Area': `${planeParams.area.toFixed(2)} mm²`,
                        'Perimeter': `${planeParams.perimeter.toFixed(2)} mm`
                    };
                    break;

                case 'fillet':
                    const filletParams = this.calculateFilletParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Fillet Radius': `${filletParams.radius.toFixed(2)} mm`,
                        'Surface Area': `${filletParams.surface_area.toFixed(2)} mm²`,
                        'Arc Length': `${filletParams.arc_length.toFixed(2)} mm`,
                        'Transition Type': filletParams.transition_type,
                        'Edge Length': `${filletParams.edge_length.toFixed(2)} mm`
                    };
                    break;

                case 'curved':
                    const curvedParams = this.calculateCurvedParameters(geometry.vertices, geometry.bbox);
                    properties = {
                        ...properties,
                        'Curvature Radius': `${curvedParams.curvature_radius.toFixed(2)} mm`,
                        'Surface Area': `${curvedParams.surface_area.toFixed(2)} mm²`,
                        'Arc Length': `${curvedParams.arc_length.toFixed(2)} mm`,
                        'Curvature Type': curvedParams.curvature_type
                    };
                    break;

                default:
                    // Fallback to generic properties
                    properties = {
                        ...properties,
                        'Length': `${dimensions.length} mm`,
                        'Width': `${dimensions.width} mm`,
                        'Height': `${dimensions.height} mm`
                    };
            }
        } catch (error) {
            console.warn(`Error calculating ${type} parameters:`, error);
            // Fallback to basic properties
            properties = {
                ...properties,
                'Length': `${dimensions.length} mm`,
                'Width': `${dimensions.width} mm`,
                'Height': `${dimensions.height} mm`
            };
        }

        return properties;
    }

    /**
     * Calculate cylinder-specific parameters from vertices
     */
    calculateCylinderParameters(vertices, bbox) {
        console.log('🔵 Calculating cylinder parameters from vertices...');

        // Step 1: Detect cylinder axis using dimension analysis
        const axis = this.detectCylinderAxis(vertices, bbox);

        // Step 2: Calculate radius from perpendicular distances
        const radius = this.calculateCylinderRadius(vertices, axis);

        // Step 3: Calculate height along axis
        const height = this.calculateCylinderHeight(vertices, axis);

        // Step 4: Calculate derived properties
        const diameter = radius * 2;
        const volume = Math.PI * radius * radius * height;
        const surface_area = 2 * Math.PI * radius * (radius + height);
        const lateral_area = 2 * Math.PI * radius * height;
        const base_area = Math.PI * radius * radius;
        const circumference = 2 * Math.PI * radius;

        console.log(`🔵 Cylinder: R=${radius.toFixed(2)}, H=${height.toFixed(2)}, V=${volume.toFixed(2)}`);

        return {
            radius: radius,
            diameter: diameter,
            height: height,
            volume: volume,
            surface_area: surface_area,
            lateral_area: lateral_area,
            base_area: base_area,
            circumference: circumference,
            axis_direction: axis.direction
        };
    }

    /**
     * Detect cylinder axis from vertices and bounding box
     */
    detectCylinderAxis(vertices, bbox) {
        const size = bbox.size;
        const dims = [
            { value: size.x, axis: 'x' },
            { value: size.y, axis: 'y' },
            { value: size.z, axis: 'z' }
        ].sort((a, b) => b.value - a.value);

        // Cylinder axis is along the dimension that's most different
        // For cylinder: 2 similar dimensions (diameter), 1 different (height)
        const largest = dims[0];
        const medium = dims[1];
        const smallest = dims[2];

        if (largest.value / medium.value > 1.5) {
            // Height is the largest dimension
            return {
                direction: largest.axis,
                length: largest.value,
                center: bbox.center
            };
        } else {
            // Height is the smallest dimension (thin cylinder)
            return {
                direction: smallest.axis,
                length: smallest.value,
                center: bbox.center
            };
        }
    }

    /**
     * Calculate cylinder radius from vertices
     */
    calculateCylinderRadius(vertices, axis) {
        // For triangles=2, vertices=6 case, estimate radius from bounding box
        // This is a simplified approach for the current vertex pattern

        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;

        // Get the two dimensions perpendicular to axis
        let dim1, dim2;
        if (axis.direction === 'x') {
            dim1 = size.y;
            dim2 = size.z;
        } else if (axis.direction === 'y') {
            dim1 = size.x;
            dim2 = size.z;
        } else { // z-axis
            dim1 = size.x;
            dim2 = size.y;
        }

        // For cylinder: radius = diameter/2, where diameter is the smaller of the two similar dimensions
        const diameter = Math.min(dim1, dim2);
        return diameter / 2;
    }

    /**
     * Calculate cylinder height
     */
    calculateCylinderHeight(vertices, axis) {
        return axis.length;
    }

    /**
     * Calculate rectangle-specific parameters from vertices
     */
    calculateRectangleParameters(vertices, bbox) {
        console.log('⬜ Calculating rectangle parameters from vertices...');

        // Step 1: Identify rectangle dimensions from bounding box
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);

        // For rectangle: length > width > thickness
        const length = dims[0];
        const width = dims[1];
        const thickness = dims[2];

        // Step 2: Calculate derived properties
        const area = length * width;
        const perimeter = 2 * (length + width);
        const aspect_ratio = length / width;
        const diagonal = Math.sqrt(length * length + width * width);

        // Step 3: Determine if it's actually a square
        const isSquare = Math.abs(length - width) < length * 0.1;

        console.log(`⬜ Rectangle: L=${length.toFixed(2)}, W=${width.toFixed(2)}, A=${area.toFixed(2)}`);

        return {
            length: length,
            width: width,
            thickness: thickness,
            area: area,
            perimeter: perimeter,
            aspect_ratio: aspect_ratio,
            diagonal: diagonal,
            is_square: isSquare
        };
    }

    /**
     * Calculate fillet-specific parameters from vertices
     */
    calculateFilletParameters(vertices, bbox) {
        console.log('🌊 Calculating fillet parameters from vertices...');

        // Step 1: Estimate fillet radius from curvature
        const curvature = this.estimateCurvatureFromVertices(vertices);
        const radius = curvature.radius;

        // Step 2: Calculate fillet-specific properties
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium] = dims;

        // Step 3: Estimate edge length (length of the filleted edge)
        const edge_length = largest;

        // Step 4: Calculate surface area (approximation for fillet)
        const surface_area = this.estimateFilletSurfaceArea(radius, edge_length, medium);

        // Step 5: Estimate bend angle
        const bend_angle = this.estimateFilletBendAngle(vertices, bbox);

        // Step 6: Calculate arc length
        const arc_length = (bend_angle / 180) * Math.PI * radius;

        // Step 7: Determine transition type
        const transition_type = this.determineFilletTransitionType(vertices, bbox);

        console.log(`🌊 Fillet: R=${radius.toFixed(2)}, Edge=${edge_length.toFixed(2)}, Angle=${bend_angle.toFixed(1)}°`);

        return {
            radius: radius,
            surface_area: surface_area,
            bend_angle: bend_angle,
            arc_length: arc_length,
            edge_length: edge_length,
            transition_type: transition_type
        };
    }

    /**
     * Estimate fillet surface area
     */
    estimateFilletSurfaceArea(radius, edge_length, width) {
        // Approximate fillet surface area as quarter cylinder
        return Math.PI * radius * edge_length * 0.5; // Half of cylindrical surface
    }

    /**
     * Estimate fillet bend angle
     */
    estimateFilletBendAngle(vertices, bbox) {
        // Simple heuristic based on bounding box aspect ratio
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;

        const ratio = largest / smallest;

        // Typical fillet angles range from 45° to 135°
        if (ratio < 1.5) return 90.0; // Square fillet
        else if (ratio < 2.0) return 60.0; // Moderate fillet
        else return 45.0; // Sharp fillet
    }

    /**
     * Determine fillet transition type
     */
    determineFilletTransitionType(vertices, bbox) {
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, , smallest] = dims;

        const ratio = largest / smallest;

        if (ratio < 1.5) return 'Corner Fillet';
        else if (ratio < 3.0) return 'Edge Fillet';
        else return 'Transition Fillet';
    }

    /**
     * Calculate curved surface parameters from vertices
     */
    calculateCurvedParameters(vertices, bbox) {
        console.log('🌊 Calculating curved surface parameters from vertices...');

        // Step 1: Estimate curvature from vertex distribution
        const curvature = this.estimateCurvatureFromVertices(vertices);

        // Step 2: Calculate approximate surface area
        const surface_area = this.estimateCurvedSurfaceArea(vertices, bbox);

        // Step 3: Estimate bend characteristics
        const bend_analysis = this.analyzeBendCharacteristics(vertices);

        console.log(`🌊 Curved: R=${curvature.radius.toFixed(2)}, A=${surface_area.toFixed(2)}`);

        return {
            curvature_radius: curvature.radius,
            surface_area: surface_area,
            bend_angle: bend_analysis.angle,
            arc_length: bend_analysis.arc_length,
            curvature_type: curvature.type
        };
    }

    /**
     * Estimate curvature from vertices with improved fillet radius calculation
     */
    estimateCurvatureFromVertices(vertices) {
        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);
        const [largest, medium, smallest] = dims;

        console.log(`🌊 Curvature estimation: L=${largest.toFixed(2)}, M=${medium.toFixed(2)}, S=${smallest.toFixed(2)}`);

        // ENHANCED FILLET RADIUS ESTIMATION
        let radius;
        const aspectRatio = largest / smallest;
        const mediumRatio = medium / smallest;

        console.log(`🔍 Radius estimation: aspect=${aspectRatio.toFixed(1)}, mediumRatio=${mediumRatio.toFixed(1)}`);

        // Case 1: Corner fillet pattern (enhanced detection for various sizes)
        if (aspectRatio > 1.4 && smallest < largest * 0.7) {
            // Mathematical pattern analysis for corner fillet segments
            const mediumToLargestRatio = medium / largest;
            const expectedRatio = 0.5; // For corner fillets: medium ≈ largest/2
            const ratioDeviation = Math.abs(mediumToLargestRatio - expectedRatio);

            // Multiple radius estimation methods
            const radiusFromMedium = medium;
            const radiusFromLargest = largest / 2;
            const radiusFromScaling = medium * 2;
            const radiusFromGeometry = Math.sqrt(largest * medium);
            const radiusFromMathematical = largest; // Mathematical pattern: radius = largest

            console.log(`🔍 Mathematical analysis: medium/largest = ${mediumToLargestRatio.toFixed(3)}, deviation = ${ratioDeviation.toFixed(3)}`);
            console.log(`🔍 Corner fillet radius candidates:`);
            console.log(`   From medium: ${radiusFromMedium.toFixed(2)}mm`);
            console.log(`   From largest/2: ${radiusFromLargest.toFixed(2)}mm`);
            console.log(`   From scaling (2x): ${radiusFromScaling.toFixed(2)}mm`);
            console.log(`   From geometry: ${radiusFromGeometry.toFixed(2)}mm`);
            console.log(`   From mathematical pattern: ${radiusFromMathematical.toFixed(2)}mm`);

            // Enhanced radius selection with better R20 support
            if (ratioDeviation < 0.3) {
                // Mathematical pattern detected: medium ≈ largest/2
                radius = radiusFromMathematical; // radius = largest
                console.log(`🎯 Mathematical corner fillet pattern: radius = largest = ${radius.toFixed(2)}mm`);
                console.log(`📐 Pattern validation: medium(${medium}) ≈ radius/2(${(radius/2).toFixed(1)}), thickness(${smallest})`);
            } else if (ratioDeviation < 0.6 && medium === largest) {
                // Special case: medium = largest (square cross-section)
                radius = radiusFromMathematical; // radius = largest
                console.log(`🎯 Square cross-section corner fillet: radius = largest = ${radius.toFixed(2)}mm`);
                console.log(`📐 Square pattern: medium(${medium}) = largest(${largest}), thickness(${smallest})`);
            } else if (aspectRatio >= 1.4 && largest >= smallest * 1.4) {
                // Enhanced detection for medium-size fillets (like R20)
                radius = radiusFromMathematical; // radius = largest
                console.log(`🎯 Medium corner fillet: radius = largest = ${radius.toFixed(2)}mm`);
                console.log(`📐 Medium pattern: aspect=${aspectRatio.toFixed(1)}, L/S=${(largest/smallest).toFixed(1)}`);
            } else if (medium > smallest * 3 && aspectRatio > 7.0) {
                // Very high aspect ratio - use scaling method
                radius = radiusFromScaling;
                console.log(`🎯 Large corner fillet: radius from scaling (${radius.toFixed(2)}mm)`);
            } else if (medium > smallest * 1.5 && medium < largest * 0.8) {
                radius = radiusFromMedium; // Medium dimension represents radius
                console.log(`🎯 Standard corner fillet: radius from medium (${radius.toFixed(2)}mm)`);
            } else {
                radius = radiusFromLargest; // Fallback to half of largest
                console.log(`🎯 Corner fillet fallback: radius from largest/2 (${radius.toFixed(2)}mm)`);
            }
        }
        // Case 2: Edge fillet pattern (moderate aspect ratio)
        else if (aspectRatio > 3.0 && aspectRatio <= 6.0) {
            if (smallest < largest * 0.3) {
                radius = smallest; // Traditional small fillet
                console.log(`🎯 Edge fillet: radius from smallest dimension: ${radius.toFixed(2)}`);
            } else {
                radius = medium / 2;
                console.log(`🎯 Edge fillet: radius from medium/2: ${radius.toFixed(2)}`);
            }
        }
        // Case 3: Small fillet pattern (low aspect ratio)
        else {
            radius = smallest;
            console.log(`🎯 Small fillet: radius from smallest dimension: ${radius.toFixed(2)}`);
        }

        // Enhanced validation for realistic fillet sizes
        if (radius > largest * 2.0) {
            radius = largest; // Cap at largest dimension for very large estimates
            console.log(`🔧 Radius capped to largest dimension: ${radius.toFixed(2)}`);
        } else if (radius > largest * 1.5) {
            radius = largest; // For corner fillets, radius can equal largest dimension
            console.log(`🔧 Radius adjusted to largest dimension: ${radius.toFixed(2)}`);
        }

        // Enhanced curvature type classification
        let curvatureType;
        let filletSubtype = 'unknown';

        // Analyze triangle count and complexity
        const triangleCount = vertices.length / 3; // Approximate triangle count
        const isHighComplexity = triangleCount > 20;
        const isMediumComplexity = triangleCount > 10 && triangleCount <= 20;
        const isLowComplexity = triangleCount <= 10;

        console.log(`🔍 Complexity analysis: triangles≈${triangleCount.toFixed(0)}, complexity=${isHighComplexity ? 'HIGH' : isMediumComplexity ? 'MEDIUM' : 'LOW'}`);

        if (aspectRatio > 3.0 && isHighComplexity) {
            curvatureType = 'corner_fillet';
            filletSubtype = 'large_corner_fillet';
            console.log(`🎯 Detected: Large corner fillet (high complexity, high aspect ratio)`);
        } else if (aspectRatio > 2.0 && isMediumComplexity) {
            curvatureType = 'corner_fillet';
            filletSubtype = 'medium_corner_fillet';
            console.log(`🎯 Detected: Medium corner fillet`);
        } else if (aspectRatio > 1.4 && isLowComplexity) {
            curvatureType = 'edge_fillet';
            filletSubtype = 'simple_edge_fillet';
            console.log(`🎯 Detected: Simple edge fillet (low complexity)`);
        } else if (aspectRatio > 3.0) {
            curvatureType = 'edge_fillet';
            filletSubtype = 'complex_edge_fillet';
            console.log(`🎯 Detected: Complex edge fillet`);
        } else {
            curvatureType = 'small_fillet';
            filletSubtype = 'small_radius_fillet';
            console.log(`🎯 Detected: Small radius fillet`);
        }

        // Calculate confidence based on enhanced pattern recognition
        let confidence;
        if (filletSubtype === 'large_corner_fillet') {
            confidence = 0.95; // Very high confidence for large corner fillets
        } else if (filletSubtype === 'medium_corner_fillet') {
            confidence = 0.9; // High confidence for medium corner fillets
        } else if (filletSubtype === 'simple_edge_fillet') {
            confidence = 0.85; // Good confidence for simple edge fillets
        } else if (filletSubtype === 'complex_edge_fillet') {
            confidence = 0.8; // Good confidence for complex edge fillets
        } else {
            confidence = 0.7; // Moderate confidence for other cases
        }

        console.log(`🎯 Final radius estimation: ${radius.toFixed(2)}mm (${curvatureType}/${filletSubtype}, confidence: ${confidence.toFixed(2)})`);

        return {
            radius: radius,
            type: curvatureType,
            subtype: filletSubtype,
            confidence: confidence,
            triangleCount: triangleCount,
            complexity: isHighComplexity ? 'HIGH' : isMediumComplexity ? 'MEDIUM' : 'LOW'
        };
    }

    /**
     * Estimate curved surface area
     */
    estimateCurvedSurfaceArea(vertices, bbox) {
        // For triangles=2, vertices=6, approximate as rectangular surface
        const size = bbox.size;
        const dims = [size.x, size.y, size.z].sort((a, b) => b - a);

        // Surface area ≈ largest × medium dimensions
        return dims[0] * dims[1];
    }

    /**
     * Analyze bend characteristics
     */
    analyzeBendCharacteristics(vertices) {
        // Simple bend analysis for current vertex pattern
        const bbox = this.calculateBoundingBox(vertices);
        const size = bbox.size;

        // Estimate bend angle from dimension ratios
        const maxDim = Math.max(size.x, size.y, size.z);
        const minDim = Math.min(size.x, size.y, size.z);
        const ratio = maxDim / minDim;

        // Simple heuristic for bend angle
        const bend_angle = Math.min(90, ratio * 10);
        const arc_length = maxDim;

        return {
            angle: bend_angle,
            arc_length: arc_length
        };
    }


}


window.SimpleFaceAnalyzer = SimpleFaceAnalyzer;
