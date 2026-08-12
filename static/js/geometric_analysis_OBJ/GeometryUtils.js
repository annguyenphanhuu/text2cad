/**
 * GeometryUtils - Utility functions for geometric analysis
 * Contains calculation and utility functions
 */

class GeometryUtils {
    constructor() {
        console.log('🔧 GeometryUtils initialized');

        // Unit conversion settings
        this.unitSettings = {
            // Auto-detect or manually set conversion
            inputUnit: 'auto',
            outputUnit: 'mm',
            conversionFactor: 1 // Will be auto-detected
        };
    }





    /**
     * Convert dimension to output units
     */
    convertDimension(value) {
        return value * this.unitSettings.conversionFactor;
    }

    /**
     * Convert size object to output units with improved detection
     */
    convertSize(size) {
        // Enhanced auto-detection with better thresholds
        if (this.unitSettings.inputUnit === 'auto') {
            const largest = Math.max(size.x, size.y, size.z);
            const smallest = Math.min(size.x, size.y, size.z);
            const range = largest - smallest;

            // Improved detection logic
            if (largest < 0.01) {
                // Micro-scale, likely millimeters stored as meters
                this.unitSettings.conversionFactor = 1000;
                this.unitSettings.inputUnit = 'meters';
            } else if (largest < 0.1 && range > 0.001) {
                // Small scale with reasonable variation, likely meters
                this.unitSettings.conversionFactor = 1000;
                this.unitSettings.inputUnit = 'meters';
            } else if (largest < 1 && largest > 0.1) {
                // Medium scale, likely centimeters
                this.unitSettings.conversionFactor = 10;
                this.unitSettings.inputUnit = 'cm';
            } else if (largest > 1000) {
                // Very large values, likely already in mm or micrometers
                this.unitSettings.conversionFactor = 1;
                this.unitSettings.inputUnit = 'mm';
            } else {
                // Default to mm
                this.unitSettings.conversionFactor = 1;
                this.unitSettings.inputUnit = 'mm';
            }

            console.log(`📏 Enhanced auto-detection: ${this.unitSettings.inputUnit} → mm (factor: ${this.unitSettings.conversionFactor})`);
            console.log(`📐 Size analysis: largest=${largest.toFixed(6)}, smallest=${smallest.toFixed(6)}, range=${range.toFixed(6)}`);
        }

        return {
            x: this.convertDimension(size.x),
            y: this.convertDimension(size.y),
            z: this.convertDimension(size.z)
        };
    }



    /**
     * Calculate the distance between 2 points
     */
    distance(point1, point2) {
        return Math.sqrt(
            Math.pow(point2.x - point1.x, 2) +
            Math.pow(point2.y - point1.y, 2) +
            Math.pow(point2.z - point1.z, 2)
        );
    }

    /**
     * Calculate the center of a set of points
     */
    calculateCentroid(points) {
        const centroid = { x: 0, y: 0, z: 0 };
        
        points.forEach(point => {
            centroid.x += point.x;
            centroid.y += point.y;
            centroid.z += point.z;
        });
        
        centroid.x /= points.length;
        centroid.y /= points.length;
        centroid.z /= points.length;
        
        return centroid;
    }

    /**
     * Calculate the bounding box from a set of points
     */
    calculateBoundingBox(points) {
        if (points.length === 0) return null;
        
        let min = { x: points[0].x, y: points[0].y, z: points[0].z };
        let max = { x: points[0].x, y: points[0].y, z: points[0].z };
        
        points.forEach(point => {
            min.x = Math.min(min.x, point.x);
            min.y = Math.min(min.y, point.y);
            min.z = Math.min(min.z, point.z);
            
            max.x = Math.max(max.x, point.x);
            max.y = Math.max(max.y, point.y);
            max.z = Math.max(max.z, point.z);
        });
        
        return {
            min,
            max,
            size: {
                x: max.x - min.x,
                y: max.y - min.y,
                z: max.z - min.z
            },
            center: {
                x: (min.x + max.x) / 2,
                y: (min.y + max.y) / 2,
                z: (min.z + max.z) / 2
            }
        };
    }

    /**
     * Check if a point is inside a bounding box
     */
    isPointInBoundingBox(point, boundingBox, tolerance = 0) {
        return point.x >= (boundingBox.min.x - tolerance) &&
               point.x <= (boundingBox.max.x + tolerance) &&
               point.y >= (boundingBox.min.y - tolerance) &&
               point.y <= (boundingBox.max.y + tolerance) &&
               point.z >= (boundingBox.min.z - tolerance) &&
               point.z <= (boundingBox.max.z + tolerance);
    }

    /**
     * Calculate the angle between 2 vectors
     */
    angleBetweenVectors(v1, v2) {
        const dot = v1.x * v2.x + v1.y * v2.y + v1.z * v2.z;
        const mag1 = Math.sqrt(v1.x * v1.x + v1.y * v1.y + v1.z * v1.z);
        const mag2 = Math.sqrt(v2.x * v2.x + v2.y * v2.y + v2.z * v2.z);
        
        const cosAngle = dot / (mag1 * mag2);
        return Math.acos(Math.max(-1, Math.min(1, cosAngle))); //
    }

    /**
     * Normalize a vector
     */
    normalizeVector(vector) {
        const magnitude = Math.sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z);
        if (magnitude === 0) return { x: 0, y: 0, z: 0 };
        
        return {
            x: vector.x / magnitude,
            y: vector.y / magnitude,
            z: vector.z / magnitude
        };
    }

    /**
     * Calculate the cross product of 2 vectors
     */
    crossProduct(v1, v2) {
        return {
            x: v1.y * v2.z - v1.z * v2.y,
            y: v1.z * v2.x - v1.x * v2.z,
            z: v1.x * v2.y - v1.y * v2.x
        };
    }

    /**
     * Calculate the dot product of 2 vectors
     */
    dotProduct(v1, v2) {
        return v1.x * v2.x + v1.y * v2.y + v1.z * v2.z;
    }

    /**
     * Calculate the distance from a point to a plane
     */
    distancePointToPlane(point, planePoint, planeNormal) {
        const normalizedNormal = this.normalizeVector(planeNormal);
        const vectorToPoint = {
            x: point.x - planePoint.x,
            y: point.y - planePoint.y,
            z: point.z - planePoint.z
        };
        
        return Math.abs(this.dotProduct(vectorToPoint, normalizedNormal));
    }

    /**
     * Check if the points are coplanar
     */
    arePointsCoplanar(points, tolerance = 0.01) {
        if (points.length < 4) return true;
        
        // Get the first 3 points to create a reference plane
        const p1 = points[0];
        const p2 = points[1];
        const p3 = points[2];
        
        // Calculate the normal vector of the plane
        const v1 = { x: p2.x - p1.x, y: p2.y - p1.y, z: p2.z - p1.z };
        const v2 = { x: p3.x - p1.x, y: p3.y - p1.y, z: p3.z - p1.z };
        const normal = this.crossProduct(v1, v2);
        
        // Check the remaining points
        for (let i = 3; i < points.length; i++) {
            const distance = this.distancePointToPlane(points[i], p1, normal);
            if (distance > tolerance) {
                return false;
            }
        }
        
        return true;
    }

    /**
     * Calculate the area of a triangle from 3 points
     */
    triangleArea(p1, p2, p3) {
        const v1 = { x: p2.x - p1.x, y: p2.y - p1.y, z: p2.z - p1.z };
        const v2 = { x: p3.x - p1.x, y: p3.y - p1.y, z: p3.z - p1.z };
        const cross = this.crossProduct(v1, v2);
        const magnitude = Math.sqrt(cross.x * cross.x + cross.y * cross.y + cross.z * cross.z);
        
        return magnitude / 2;
    }

    /**
     * Round a number to a certain precision
     */
    roundToPrecision(number, precision = 2) {
        const factor = Math.pow(10, precision);
        return Math.round(number * factor) / factor;
    }

    /**
     * Format a number for display
     */
    formatNumber(number, precision = 2, unit = '') {
        const rounded = this.roundToPrecision(number, precision);
        return `${rounded.toLocaleString()}${unit ? ' ' + unit : ''}`;
    }

    /**
     * Convert angle units
     */
    radiansToDegrees(radians) {
        return radians * (180 / Math.PI);
    }

    degreesToRadians(degrees) {
        return degrees * (Math.PI / 180);
    }

    /**
     * Calculate basic statistics for an array of numbers
     */
    calculateStatistics(numbers) {
        if (numbers.length === 0) return null;
        
        const sorted = [...numbers].sort((a, b) => a - b);
        const sum = numbers.reduce((a, b) => a + b, 0);
        const mean = sum / numbers.length;
        
        const variance = numbers.reduce((sum, num) => sum + Math.pow(num - mean, 2), 0) / numbers.length;
        const standardDeviation = Math.sqrt(variance);
        
        return {
            count: numbers.length,
            min: sorted[0],
            max: sorted[sorted.length - 1],
            mean,
            median: sorted[Math.floor(sorted.length / 2)],
            variance,
            standardDeviation,
            range: sorted[sorted.length - 1] - sorted[0]
        };
    }

    /**
     * Create a unique ID for the analysis
     */
    generateAnalysisId() {
        return 'analysis_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }


}


window.GeometryUtils = GeometryUtils;
