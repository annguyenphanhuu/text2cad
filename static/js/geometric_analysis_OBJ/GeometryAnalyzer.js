/**
 * GeometryAnalyzer - Geometric analysis system for OBJ groups
 * Analyzes basic shapes from mesh information in OBJ files
 */

class GeometryAnalyzer {
    constructor() {
        // Initialize simple face analyzer
        try {
            this.faceAnalyzer = new SimpleFaceAnalyzer();
            console.log('✅ SimpleFaceAnalyzer initialized');
        } catch (error) {
            console.error('❌ Failed to initialize SimpleFaceAnalyzer:', error);
            this.faceAnalyzer = null;
        }

        try {
            this.utils = new GeometryUtils();
            console.log('✅ GeometryUtils initialized');
        } catch (error) {
            console.error('❌ Failed to initialize GeometryUtils:', error);
            this.utils = null;
        }

        // Initialize edge analyzer
        try {
            this.edgeAnalyzer = new EdgeAnalyzer(this.utils);
            console.log('✅ EdgeAnalyzer initialized');
        } catch (error) {
            console.error('❌ Failed to initialize EdgeAnalyzer:', error);
            this.edgeAnalyzer = null;
        }

        console.log('🔧 GeometryAnalyzer initialized');
    }

    /**
     * Analyze selected mesh from OBJ group
     * @param {Object} meshInfo - Mesh info from 3D viewer
     * @param {THREE.Mesh} mesh - Three.js mesh object
     * @param {THREE.Vector3} clickPoint - Click point
     * @returns {Object} Geometric analysis result
     */
    analyzeMesh(meshInfo, mesh, clickPoint) {
        console.log(`🔍 Analyzing face: ${meshInfo.triangles} triangles, ${meshInfo.vertices} vertices`);

        try {
            // Check if required components are available
            if (!this.faceAnalyzer) {
                throw new Error('SimpleFaceAnalyzer not available');
            }

            // Extract basic info from mesh
            const basicInfo = this.extractBasicInfo(meshInfo, mesh, clickPoint);

            // Analyze face simply
            const faceAnalysis = this.faceAnalyzer.analyzeFace(meshInfo, mesh, clickPoint);

            if (!faceAnalysis.success) {
                throw new Error(faceAnalysis.error || 'Face analysis failed');
            }

            return {
                success: true,
                basicInfo,
                shapeType: faceAnalysis.shapeType,
                detailedAnalysis: {
                    type: faceAnalysis.shapeType.charAt(0).toUpperCase() + faceAnalysis.shapeType.slice(1),
                    shapeType: faceAnalysis.shapeType,
                    parameters: faceAnalysis.analysis,
                    dimensions: faceAnalysis.dimensions,
                    properties: faceAnalysis.properties,
                    confidence: faceAnalysis.confidence
                },
                timestamp: new Date().toISOString()
            };

        } catch (error) {
            console.error('❌ Error analyzing mesh:', error);
            return {
                success: false,
                error: error.message,
                basicInfo: this.extractBasicInfo(meshInfo, mesh, clickPoint),
                shapeType: 'unknown'
            };
        }
    }

    /**
     * Analyze selected edge from OBJ geometry
     * @param {Object} edgeInfo - Edge info from edge selection
     * @param {THREE.Mesh} mesh - Three.js mesh object
     * @param {THREE.Vector3} clickPoint - Click point
     * @returns {Object} Edge analysis result
     */
    analyzeEdge(edgeInfo, mesh, clickPoint) {
        console.log(`🔍 Analyzing edge: ${edgeInfo.id}`);

        try {
            // Check if required components are available
            if (!this.edgeAnalyzer) {
                throw new Error('EdgeAnalyzer not available');
            }

            // Extract basic info from mesh (reuse existing method)
            const basicInfo = this.extractBasicInfo({
                name: `${edgeInfo.id}`,
                triangles: 'N/A',
                vertices: 'N/A'
            }, mesh, clickPoint);

            // Analyze edge properties
            const edgeAnalysis = this.edgeAnalyzer.analyzeEdge(edgeInfo, mesh);

            if (!edgeAnalysis.success) {
                throw new Error(edgeAnalysis.error || 'Edge analysis failed');
            }

            return {
                success: true,
                basicInfo,
                edgeType: edgeAnalysis.edgeType,
                detailedAnalysis: {
                    type: 'Edge',
                    edgeType: edgeAnalysis.edgeType,
                    parameters: edgeAnalysis.analysis,
                    dimensions: edgeAnalysis.dimensions,
                    properties: edgeAnalysis.properties,
                    confidence: edgeAnalysis.confidence
                },
                timestamp: new Date().toISOString()
            };

        } catch (error) {
            console.error('❌ Error analyzing edge:', error);
            return {
                success: false,
                error: error.message,
                basicInfo: this.extractBasicInfo({
                    name: edgeInfo?.id || 'Unknown Edge',
                    triangles: 'N/A',
                    vertices: 'N/A'
                }, mesh, clickPoint),
                edgeType: 'unknown'
            };
        }
    }

    /**
     * Extract basic info from mesh
     */
    extractBasicInfo(meshInfo, mesh, clickPoint) {
        // Validate mesh object
        if (!mesh || !mesh.geometry) {
            throw new Error('Invalid mesh object: missing geometry');
        }

        // Check if mesh has required THREE.js methods
        if (typeof mesh.updateMatrixWorld !== 'function') {
            console.warn('⚠️ Mesh missing updateMatrixWorld method, ensuring matrix is updated');
            if (mesh.matrixWorld) {
                mesh.matrixWorld.identity();
            }
        }

        const geometry = mesh.geometry;
        let boundingBox, size, center;

        try {
            boundingBox = new THREE.Box3().setFromObject(mesh);
            size = boundingBox.getSize(new THREE.Vector3());
            center = boundingBox.getCenter(new THREE.Vector3());
        } catch (error) {
            console.warn('⚠️ Failed to get bounding box from mesh, using geometry bounds instead');
            // Fallback: compute bounding box from geometry
            if (geometry.boundingBox) {
                boundingBox = geometry.boundingBox.clone();
            } else {
                geometry.computeBoundingBox();
                boundingBox = geometry.boundingBox.clone();
            }
            size = boundingBox.getSize(new THREE.Vector3());
            center = boundingBox.getCenter(new THREE.Vector3());
        }

        // Convert dimensions to mm (assuming input is in meters)
        const convertedSize = this.utils.convertSize(size);
        const convertedCenter = {
            x: this.utils.convertDimension(center.x),
            y: this.utils.convertDimension(center.y),
            z: this.utils.convertDimension(center.z)
        };

        console.log(`📏 Original size: ${size.x.toFixed(3)} × ${size.y.toFixed(3)} × ${size.z.toFixed(3)}`);
        console.log(`📏 Converted size (mm): ${convertedSize.x.toFixed(2)} × ${convertedSize.y.toFixed(2)} × ${convertedSize.z.toFixed(2)}`);

        
        return {
            groupName: meshInfo.name || 'Unknown',
            triangles: meshInfo.triangles || 0,
            vertices: meshInfo.vertices || 0,
            clickPoint: clickPoint ? {
                x: this.utils.convertDimension(clickPoint.x).toFixed(2),
                y: this.utils.convertDimension(clickPoint.y).toFixed(2),
                z: this.utils.convertDimension(clickPoint.z).toFixed(2)
            } : null,
            boundingBox: {
                min: {
                    x: this.utils.convertDimension(boundingBox.min.x),
                    y: this.utils.convertDimension(boundingBox.min.y),
                    z: this.utils.convertDimension(boundingBox.min.z)
                },
                max: {
                    x: this.utils.convertDimension(boundingBox.max.x),
                    y: this.utils.convertDimension(boundingBox.max.y),
                    z: this.utils.convertDimension(boundingBox.max.z)
                },
                size: convertedSize,
                center: convertedCenter
            }
        };
    }



    /**
     * Create a detailed analysis report
     */
    generateReport(analysisResult) {
        if (!analysisResult.success) {
            return `
                <div class="analysis-report error">
                    <h3>❌ Analysis Error</h3>
                    <p>${analysisResult.error}</p>
                </div>
            `;
        }

        const { basicInfo, shapeType, detailedAnalysis } = analysisResult;
        
        return `
            <div class="analysis-report">
                <h3>📊 Geometric Analysis: ${basicInfo.groupName}</h3>

                <div class="basic-info">
                    <h4>Basic Information:</h4>
                    <ul>
                        <li><strong>Type:</strong> ${detailedAnalysis.type}</li>
                        <li><strong>Triangles:</strong> ${basicInfo.triangles}</li>
                        <li><strong>Vertices:</strong> ${basicInfo.vertices}</li>
                        ${basicInfo.clickPoint ? `<li><strong>Click Point:</strong> (${basicInfo.clickPoint.x}, ${basicInfo.clickPoint.y}, ${basicInfo.clickPoint.z})</li>` : ''}
                    </ul>
                </div>

                <div class="detailed-analysis">
                    <h4>Detailed Analysis:</h4>
                    <ul>
                        ${Object.entries(detailedAnalysis.properties || {}).map(([key, value]) =>
                            `<li><strong>${key}:</strong> ${value}</li>`
                        ).join('')}
                    </ul>
                </div>

                <div class="dimensions">
                    <h4>Dimensions:</h4>
                    <p>${detailedAnalysis.dimensions ?
                        `${detailedAnalysis.dimensions.width} × ${detailedAnalysis.dimensions.height} × ${detailedAnalysis.dimensions.depth}`
                        : 'N/A'}</p>
                </div>
            </div>
        `;
    }
}


window.GeometryAnalyzer = GeometryAnalyzer;
