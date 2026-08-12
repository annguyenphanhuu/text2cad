/**
 * EdgeAnalyzer - Edge analysis system for OBJ geometry
 * Analyzes edges from mesh geometry and provides edge-specific analysis
 */

class EdgeAnalyzer {
    constructor(utils) {
        this.utils = utils; // Reuse GeometryUtils
        console.log('🔧 EdgeAnalyzer initialized');
    }

    /**
     * Find real edges from geometry (from old commit, optimized)
     * @param {THREE.BufferGeometry} geometry - Three.js geometry
     * @returns {Set} Set of real edge keys
     */
    findRealEdges(geometry) {
        const positions = geometry.attributes.position.array;
        const indices = geometry.index ? geometry.index.array : null;
        
        // Map to count occurrences of each edge
        const edgeMap = new Map();
        const edgeTriangleMap = new Map(); // Store triangles for each edge
        
        const triangleCount = indices ? indices.length / 3 : positions.length / 9;
        console.log(`🔍 Analyzing ${triangleCount} triangles for real edges`);
        
        for (let i = 0; i < triangleCount; i++) {
            let a, b, c;

            if (indices) {
                a = indices[i * 3];
                b = indices[i * 3 + 1];
                c = indices[i * 3 + 2];
            } else {
                a = i * 3;
                b = i * 3 + 1;
                c = i * 3 + 2;
            }

            // Calculate normal vector of the triangle
            const va = new THREE.Vector3(
                positions[a * 3], positions[a * 3 + 1], positions[a * 3 + 2]
            );
            const vb = new THREE.Vector3(
                positions[b * 3], positions[b * 3 + 1], positions[b * 3 + 2]
            );
            const vc = new THREE.Vector3(
                positions[c * 3], positions[c * 3 + 1], positions[c * 3 + 2]
            );

            const edge1 = new THREE.Vector3().subVectors(vb, va);
            const edge2 = new THREE.Vector3().subVectors(vc, va);
            const normal = new THREE.Vector3().crossVectors(edge1, edge2).normalize();

            // Store edges in increasing order of vertex index
            this.addEdgeInfo(edgeMap, edgeTriangleMap, Math.min(a, b), Math.max(a, b), i, normal);
            this.addEdgeInfo(edgeMap, edgeTriangleMap, Math.min(b, c), Math.max(b, c), i, normal);
            this.addEdgeInfo(edgeMap, edgeTriangleMap, Math.min(c, a), Math.max(c, a), i, normal);
        }
        
        // Find the real edges (boundary edges or edges between different planes)
        const realEdges = new Set();
        let realEdgeCount = 0;
        let internalEdgeCount = 0;
        
        edgeMap.forEach((count, edgeKey) => {
            const triangleInfos = edgeTriangleMap.get(edgeKey) || [];

            // If only 1 triangle contains this edge, it is a boundary edge
            if (triangleInfos.length === 1) {
                realEdges.add(edgeKey);
                realEdgeCount++;
                return;
            }

            // If there are 2 triangles, check the angle between normal vectors
            if (triangleInfos.length === 2) {
                const normal1 = triangleInfos[0].normal;
                const normal2 = triangleInfos[1].normal;

                // Calculate the angle between normal vectors
                const angleBetweenNormals = normal1.angleTo(normal2) * (180 / Math.PI);

                // If the angle is greater than the threshold, this is a real edge
                if (angleBetweenNormals > 10) {  // 10 degree threshold
                    realEdges.add(edgeKey);
                    realEdgeCount++;
                } else {
                    internalEdgeCount++;
                }
            } else {
                // Edge is shared by more than 2 triangles, usually an internal edge
                internalEdgeCount++;
            }
        });
        
        console.log(`📊 Found ${realEdgeCount} real edges, ${internalEdgeCount} internal edges`);
        return realEdges;
    }

    /**
     * Helper method to add edge info to maps
     */
    addEdgeInfo(edgeMap, edgeTriangleMap, v1, v2, triangleIndex, normal) {
        const edgeKey = `${v1}-${v2}`;
        // Count occurrences
        if (!edgeMap.has(edgeKey)) {
            edgeMap.set(edgeKey, 1);
        } else {
            edgeMap.set(edgeKey, edgeMap.get(edgeKey) + 1);
        }

        // Store triangle information
        if (!edgeTriangleMap.has(edgeKey)) {
            edgeTriangleMap.set(edgeKey, []);
        }
        edgeTriangleMap.get(edgeKey).push({
            triangleIndex: triangleIndex,
            normal: normal
        });
    }

    /**
     * Find closest edge to click point from a triangle
     * @param {THREE.Face3} clickedFace - Clicked triangle face
     * @param {THREE.BufferGeometry} geometry - Geometry
     * @param {THREE.Vector3} clickPoint - Click point in local space
     * @param {Set} realEdges - Set of real edge keys
     * @returns {Object|null} Closest edge info or null
     */
    findClosestEdgeInTriangle(clickedFace, geometry, clickPoint, realEdges) {
        const positions = geometry.attributes.position.array;
        
        // Get face vertices
        const a = new THREE.Vector3(
            positions[clickedFace.a * 3],
            positions[clickedFace.a * 3 + 1],
            positions[clickedFace.a * 3 + 2]
        );
        
        const b = new THREE.Vector3(
            positions[clickedFace.b * 3],
            positions[clickedFace.b * 3 + 1],
            positions[clickedFace.b * 3 + 2]
        );
        
        const c = new THREE.Vector3(
            positions[clickedFace.c * 3],
            positions[clickedFace.c * 3 + 1],
            positions[clickedFace.c * 3 + 2]
        );
        
      
        const edges = [];
        
      
        const edgeAB = `${Math.min(clickedFace.a, clickedFace.b)}-${Math.max(clickedFace.a, clickedFace.b)}`;
        if (realEdges.has(edgeAB)) {
            edges.push({ 
                start: a, 
                end: b, 
                distance: this.pointToLineDistance(clickPoint, a, b),
                id: `Edge_${clickedFace.a}_${clickedFace.b}`,
                vertexIndices: [clickedFace.a, clickedFace.b],
                type: 'real'
            });
        }
        
        const edgeBC = `${Math.min(clickedFace.b, clickedFace.c)}-${Math.max(clickedFace.b, clickedFace.c)}`;
        if (realEdges.has(edgeBC)) {
            edges.push({ 
                start: b, 
                end: c, 
                distance: this.pointToLineDistance(clickPoint, b, c),
                id: `Edge_${clickedFace.b}_${clickedFace.c}`,
                vertexIndices: [clickedFace.b, clickedFace.c],
                type: 'real'
            });
        }
        
       
        const edgeCA = `${Math.min(clickedFace.c, clickedFace.a)}-${Math.max(clickedFace.c, clickedFace.a)}`;
        if (realEdges.has(edgeCA)) {
            edges.push({ 
                start: c, 
                end: a, 
                distance: this.pointToLineDistance(clickPoint, c, a),
                id: `Edge_${clickedFace.c}_${clickedFace.a}`,
                vertexIndices: [clickedFace.c, clickedFace.a],
                type: 'real'
            });
        }
        
        if (edges.length === 0) {
            console.warn("No real edges found in clicked triangle");
            return null;
        }
        
       
        edges.sort((e1, e2) => e1.distance - e2.distance);
        return edges[0];
    }

    /**
     * Calculate distance from point to line segment (reuse logic from old commit)
     */
    pointToLineDistance(point, lineStart, lineEnd) {
        const line = new THREE.Line3(lineStart, lineEnd);
        const closestPoint = new THREE.Vector3();
        line.closestPointToPoint(point, true, closestPoint);
        return point.distanceTo(closestPoint);
    }

    /**
     * Analyze edge properties
     * @param {Object} edge - Edge object with start/end points
     * @param {THREE.Mesh} mesh - Mesh object
     * @returns {Object} Edge analysis result
     */
    analyzeEdge(edge, mesh) {
        try {
            // Calculate edge length
            const length = this.utils.distance(edge.start, edge.end);
            const convertedLength = this.utils.convertDimension(length);

            // Calculate edge direction vector
            const direction = {
                x: edge.end.x - edge.start.x,
                y: edge.end.y - edge.start.y,
                z: edge.end.z - edge.start.z
            };
            const normalizedDirection = this.utils.normalizeVector(direction);

            // Determine edge orientation
            const orientation = this.determineEdgeOrientation(normalizedDirection);

            // Calculate edge properties
            const properties = {
                'Length': this.utils.formatNumber(convertedLength, 2, 'mm'),
                'Direction': `(${normalizedDirection.x.toFixed(3)}, ${normalizedDirection.y.toFixed(3)}, ${normalizedDirection.z.toFixed(3)})`,
                'Orientation': orientation,
                'Type': edge.type || 'straight'
            };

            return {
                success: true,
                edgeType: 'straight',
                confidence: 0.95,
                analysis: {
                    id: edge.id,
                    length: convertedLength,
                    direction: normalizedDirection,
                    orientation: orientation
                },
                dimensions: {
                    length: this.utils.formatNumber(convertedLength, 2),
                    start: edge.start,
                    end: edge.end
                },
                properties,
                timestamp: new Date().toISOString()
            };

        } catch (error) {
            console.error('❌ Error analyzing edge:', error);
            return {
                success: false,
                error: error.message,
                edgeType: 'unknown'
            };
        }
    }

    /**
     * Determine edge orientation (horizontal, vertical, diagonal)
     */
    determineEdgeOrientation(direction) {
        const absX = Math.abs(direction.x);
        const absY = Math.abs(direction.y);
        const absZ = Math.abs(direction.z);

        const threshold = 0.1; // Tolerance for considering direction as zero

        if (absX > 0.9 && absY < threshold && absZ < threshold) {
            return 'Horizontal (X-axis)';
        } else if (absY > 0.9 && absX < threshold && absZ < threshold) {
            return 'Horizontal (Y-axis)';
        } else if (absZ > 0.9 && absX < threshold && absY < threshold) {
            return 'Vertical (Z-axis)';
        } else {
            return 'Diagonal';
        }
    }

    /**
     * Create edge coordinates text for chat
     */
    formatEdgeForChat(edge) {
        const start = edge.start;
        const end = edge.end;
        
        // Convert to mm if utils available
        if (this.utils && this.utils.convertDimension) {
            const startConverted = {
                x: this.utils.convertDimension(start.x),
                y: this.utils.convertDimension(start.y),
                z: this.utils.convertDimension(start.z)
            };
            const endConverted = {
                x: this.utils.convertDimension(end.x),
                y: this.utils.convertDimension(end.y),
                z: this.utils.convertDimension(end.z)
            };
            
            return `Edge Coordinates: Start(${startConverted.x.toFixed(1)}, ${startConverted.y.toFixed(1)}, ${startConverted.z.toFixed(1)}) End(${endConverted.x.toFixed(1)}, ${endConverted.y.toFixed(1)}, ${endConverted.z.toFixed(1)})`;
        } else {
            return `Edge Coordinates: Start(${start.x.toFixed(1)}, ${start.y.toFixed(1)}, ${start.z.toFixed(1)}) End(${end.x.toFixed(1)}, ${end.y.toFixed(1)}, ${end.z.toFixed(1)})`;
        }
    }
}


window.EdgeAnalyzer = EdgeAnalyzer;
