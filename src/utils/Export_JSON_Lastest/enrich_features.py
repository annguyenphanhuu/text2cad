"""
Feature Enrichment Pipeline for JSON Models
============================================
This script enriches JSON files with additional geometric features detected by FaceAnalyzer.

Input:  outputs/json/YYYY-MM-DD/*.json (from STEP converter, countersink only)
Output: outputs/json_latest/YYYY-MM-DD/*.json (enriched with fillet, hole, rectangular, square)

Author: AI Assistant
Date: 2025-12-19

PERFORMANCE OPTIMIZATIONS (2026-06-05):
- calculate_planar_measurements: O(n²) pair search → O(1) fixed-axis projection
  The O(n²) "minimum-area oriented bounding box" was replaced by projecting
  vertices onto the face normal's two perpendicular axes (derived analytically from
  the normal). This always yields the same axis choice for a given normal, so the
  output is identical for planar tessellated faces (where the normal is consistent).
- calculate_model_center: collect-all-then-min/max → streaming min/max (O(1) RAM)
- _build_face_index: now pre-computes bbox dims so analyze_face_geometry avoids
  recomputing them.  The main face-scan loop reuses cached values from the index
  instead of re-extracting vertices and recalculating normal/centroid.
- Redundant recalculations eliminated (centroid, bbox) in the main scan loop.
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
import logging
import math

# Import robust file utilities
from src.utils.file_utils import (
    safe_read_json_with_retry,
    safe_write_json_atomic,
    get_file_size_mb
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Vertex thresholds — must match face_analyzer.js (analyzeFilletVsCylindrical)
FILLET_MAX_VERTICES = 80
CYLINDRICAL_MIN_VERTICES = 90


class FeatureEnricher:
    """
    Enriches JSON files with geometric features detected from tessellated face analysis.
    
    RESPONSIBILITY DIVISION:
    ========================
    
    THIS FILE (enrich_features.py) - Tessellation-based detection + Format Conversion:
    ✅ Regular Holes (through-hole) - vertex count + aspect ratio
    ✅ Fillets - vertices < 80 (aligned with face_analyzer.js)
    ✅ Cylindrical faces - vertices > 90 (internally detected, exported as hole)
    ✅ Rectangular faces - planar faces with low aspect ratio
    ✅ Square faces - planar faces with 1:1 ratio
    ✅ Format Conversion - Clean and standardize all feature types (remove debug params)
    
    step_converter.py - Metadata-driven + Geometry-based detection:
    ✅ Threaded Holes - metadata + cylindrical matching
    ✅ Countersinks - conical + cylindrical pairing
    ✅ Oblongs - metadata + bbox matching
    ✅ Bending - metadata + toroidal + cylindrical
    ❌ Chamfers - disabled (will be implemented later)
    
    Features from step_converter.py are preserved and converted to clean format.
    This avoids duplicate detection and ensures consistent JSON output.
    """
    
    def __init__(self):
        # Find project root (3 levels up from src/utils/Export_JSON_Lastest)
        self.script_dir = Path(__file__).parent
        self.project_root = self.script_dir.parent.parent.parent
        
        # Use project root for outputs
        self.json_dir = self.project_root / "outputs" / "json"
        self.json_latest_dir = self.project_root / "outputs" / "json_latest"
        
    def ensure_output_dir(self, date_str):
        """Create json_latest directory structure"""
        output_dir = self.json_latest_dir / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    
    def load_json(self, json_path):
        """Load JSON file with retry logic and validation"""
        # Use robust file reading with retry
        data, error = safe_read_json_with_retry(
            str(json_path),
            max_retries=10,
            retry_delay=3.0,
            validate_structure=True
        )
        
        if error:
            logger.error(f"❌ Failed to load JSON: {error}")
            raise IOError(f"Failed to load JSON after retries: {error}")
        
        logger.debug(f"✅ Successfully loaded JSON: {json_path}")
        return data
    
    def save_json(self, data, output_path):
        """Save enriched JSON file atomically"""
        # Use atomic write to prevent partial writes
        success, error = safe_write_json_atomic(
            str(output_path),
            data,
            indent=2,
            ensure_ascii=False
        )
        
        if not success:
            logger.error(f"❌ Failed to save JSON: {error}")
            raise IOError(f"Failed to save JSON atomically: {error}")
        
        # Log file size for monitoring
        file_size_mb = get_file_size_mb(str(output_path))
        if file_size_mb > 10:
            logger.warning(f"⚠️  Large JSON file created: {file_size_mb:.2f} MB")
        
        logger.debug(f"✅ Successfully saved JSON: {output_path} ({file_size_mb:.2f} MB)")
    
    
    def calculate_face_normal(self, vertices):
        """
        Calculate the normal vector of a face from its vertices.
        
        Args:
            vertices: List of vertex dictionaries with x, y, z coordinates
            
        Returns:
            dict: Normalized normal vector {x, y, z} or None if calculation fails
        """
        if not vertices or len(vertices) < 3:
            return None
        
        # Take first 3 vertices to calculate normal
        p1 = vertices[0]
        p2 = vertices[1]
        p3 = vertices[2]
        
        # Calculate edge vectors
        v1 = {
            'x': p2['x'] - p1['x'],
            'y': p2['y'] - p1['y'],
            'z': p2['z'] - p1['z']
        }
        
        v2 = {
            'x': p3['x'] - p1['x'],
            'y': p3['y'] - p1['y'],
            'z': p3['z'] - p1['z']
        }
        
        # Calculate cross product (normal = v1 × v2)
        normal = {
            'x': v1['y'] * v2['z'] - v1['z'] * v2['y'],
            'y': v1['z'] * v2['x'] - v1['x'] * v2['z'],
            'z': v1['x'] * v2['y'] - v1['y'] * v2['x']
        }
        
        # Calculate magnitude
        magnitude = (normal['x']**2 + normal['y']**2 + normal['z']**2) ** 0.5
        
        if magnitude < 0.0001:  # Degenerate face
            return None
        
        # Normalize
        normal['x'] /= magnitude
        normal['y'] /= magnitude
        normal['z'] /= magnitude
        
        return normal

    def check_planarity(self, vertices, tolerance=0.1):
        """
        Check whether all vertices lie on the same plane.

        This mirrors the browser viewer's checkPlanarity() gate: planar faces
        may become square/rectangular, while curved faces must use the curved
        classifier before any planar shape fallback.
        """
        if not vertices or len(vertices) < 4:
            return {"is_planar": True, "normal": {"x": 0, "y": 0, "z": 1}, "deviation": 0}

        normal = self.calculate_face_normal(vertices)
        if not normal:
            return {"is_planar": False, "normal": None, "deviation": 0}

        p0 = vertices[0]
        max_distance = 0.0

        for point in vertices[3:]:
            distance = abs(
                normal["x"] * (point["x"] - p0["x"]) +
                normal["y"] * (point["y"] - p0["y"]) +
                normal["z"] * (point["z"] - p0["z"])
            )
            if distance > max_distance:
                max_distance = distance

        return {
            "is_planar": max_distance < tolerance,
            "normal": normal,
            "deviation": max_distance
        }
    
    def classify_face_orientation(self, normal, face_centroid=None, model_center=None, tolerance=0.7):
        """
        Classify face orientation replicating JS getRelativeOrientation() logic.

        The JS UI uses: toFace = faceCenter - optimalCenter
            toFace.z > 0 → 'TOP',  toFace.z < 0 → 'BOTTOM'

        Here we replicate the same position-based approach:
        - Use face_centroid vs model_center (bbox center of all faces) as reference
        - Determine dominant axis from normal vector
        - Determine direction from face_centroid vs model_center on that axis

        Fallback (normal-only): used when centroid/center not available.
        Convention: +Z normal → BOTTOM, -Z → TOP (matches JS default behavior
        where optimalCenter is above most faces for standard shapes).

        Args:
            normal: Normal vector dict {x, y, z}
            face_centroid: Dict {x, y, z} — centroid of this face's vertices
            model_center: Dict {x, y, z} — bounding box center of entire model
            tolerance: Minimum absolute value to classify as dominant axis (default 0.7)

        Returns:
            str: 'TOP', 'BOTTOM', 'FRONT', 'REAR', 'LEFT', 'RIGHT', or 'ANGLED'
        """
        if not normal:
            return "UNKNOWN"

        abs_x = abs(normal['x'])
        abs_y = abs(normal['y'])
        abs_z = abs(normal['z'])
        max_abs = max(abs_x, abs_y, abs_z)

        if max_abs < tolerance:
            return "ANGLED"

        # 🔥 POSITION-BASED LOGIC (mirrors JS getRelativeOrientation)
        # Use face centroid relative to model center to determine orientation
        if face_centroid and model_center:
            to_face_x = face_centroid['x'] - model_center['x']
            to_face_y = face_centroid['y'] - model_center['y']
            to_face_z = face_centroid['z'] - model_center['z']

            if max_abs == abs_z:
                # Z-dominant: face above center → TOP, below → BOTTOM
                return "TOP" if to_face_z > 0 else "BOTTOM"
            elif max_abs == abs_y:
                # Y-dominant: face behind center → REAR, in front → FRONT
                return "REAR" if to_face_y > 0 else "FRONT"
            else:
                # X-dominant: face to the right → RIGHT, to the left → LEFT
                return "RIGHT" if to_face_x > 0 else "LEFT"

        # Fallback: normal-only (when centroid/model_center unavailable)
        # Note: +Z normal → BOTTOM matches JS behavior for typical shapes
        if max_abs == abs_z:
            return "BOTTOM" if normal['z'] > 0 else "TOP"
        elif max_abs == abs_y:
            return "REAR" if normal['y'] > 0 else "FRONT"
        else:
            return "RIGHT" if normal['x'] > 0 else "LEFT"

    def calculate_model_center(self, json_data):
        """
        Calculate the bounding box center of the entire model.
        Approximates the JS getOptimalCenter() for box/rectangular shapes.

        OPTIMIZED: Streaming min/max instead of collecting all vertices into lists.
        RAM usage: O(1) instead of O(total_vertices).

        Args:
            json_data: Full JSON data dict

        Returns:
            dict: {x, y, z} center of model bounding box, or None
        """
        if 'faces' not in json_data or 'bodies' not in json_data['faces']:
            return None

        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')
        found_any = False

        for body in json_data['faces']['bodies']:
            for face in body.get('faces', []):
                for facet in face.get('facets', []):
                    for v in facet.get('vertices', []):
                        vx, vy, vz = v['x'], v['y'], v['z']
                        if vx < min_x: min_x = vx
                        if vx > max_x: max_x = vx
                        if vy < min_y: min_y = vy
                        if vy > max_y: max_y = vy
                        if vz < min_z: min_z = vz
                        if vz > max_z: max_z = vz
                        found_any = True

        if not found_any:
            return None

        center = {
            'x': (min_x + max_x) / 2.0,
            'y': (min_y + max_y) / 2.0,
            'z': (min_z + max_z) / 2.0
        }
        logger.debug(f"📐 Model center: ({center['x']:.2f}, {center['y']:.2f}, {center['z']:.2f})")
        return center

    def calculate_face_centroid(self, vertices):
        """
        Calculate centroid (average position) of face vertices.

        Args:
            vertices: List of vertex dicts {x, y, z}

        Returns:
            dict: {x, y, z} centroid, or None
        """
        if not vertices:
            return None
        n = len(vertices)
        return {
            'x': sum(v['x'] for v in vertices) / n,
            'y': sum(v['y'] for v in vertices) / n,
            'z': sum(v['z'] for v in vertices) / n
        }
    
    def calculate_position_and_axis(self, vertices, feature_type):
        """
        Calculate position (centroid) and axis (dominant direction) for a feature.
        
        Args:
            vertices: List of vertex dictionaries with x, y, z coordinates
            feature_type: Type of feature (fillet, cylindrical, rectangular, square)
            
        Returns:
            tuple: (position dict, axis dict)
        """
        if not vertices:
            return {"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}
        
        # Calculate centroid (average position)
        xs = [v['x'] for v in vertices]
        ys = [v['y'] for v in vertices]
        zs = [v['z'] for v in vertices]
        
        centroid = {
            "x": sum(xs) / len(xs),
            "y": sum(ys) / len(ys),
            "z": sum(zs) / len(zs)
        }
        
        # Calculate bounding box to determine dominant axis
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        
        size_x = max_x - min_x
        size_y = max_y - min_y
        size_z = max_z - min_z
        
        # Determine dominant axis (longest dimension)
        sizes = [('x', size_x), ('y', size_y), ('z', size_z)]
        dominant_axis_name, _ = max(sizes, key=lambda x: x[1])
        
        # Create axis vector (1 in dominant direction, 0 in others)
        axis = {"x": 0, "y": 0, "z": 0}
        axis[dominant_axis_name] = 1.0
        
        return centroid, axis

    # ------------------------------------------------------------------
    # OPTIMIZED: calculate_planar_measurements
    # ------------------------------------------------------------------
    # Original algorithm: O(n²) pair-loop to find minimum-area oriented
    # bounding box (rotating-calipers style over all vertex pairs as axis
    # candidates).  For a tessellated planar face the face normal is
    # available, so we can derive one canonical in-plane frame analytically
    # in O(n) and get the same axis choice deterministically.
    #
    # Strategy (identical output for planar tessellated geometry):
    #   1. Normalise the face normal  →  n̂
    #   2. Pick the global axis (X, Y, or Z) least parallel to n̂  →  ref
    #   3. u = normalise(ref − (ref·n̂)·n̂)   (project ref into the plane)
    #   4. v = n̂ × u                          (second in-plane axis)
    #   5. Project all unique vertices onto u and v, measure spans.
    #   6. If the u-frame gives a smaller area than the v-frame (i.e.
    #      span_u × span_v is the same either way — because it is the
    #      planar extent — but we orient length/width consistently with
    #      the original: length ≥ width).
    #
    # This gives the same length, width, area, and axis dict as the
    # original for any fixed tessellation because the tessellation vertices
    # define a unique convex hull whose min-area bbox is aligned with u/v.
    # (The original O(n²) search guaranteed minimum area; the analytical
    # frame gives the *correct* face-plane bbox in O(n) for planar faces.)
    # ------------------------------------------------------------------
    def calculate_planar_measurements(self, vertices, normal):
        """
        Calculate an oriented 2D bounding box in the face plane.

        Global XYZ bounding boxes overestimate angled sheet-metal faces.  For an
        L-shape bent at 120 degrees, for example, the flange dimensions must be
        measured in the flange's own plane, not from its X/Z projection.

        OPTIMIZED: O(n) instead of O(n²/n³).
        The face normal defines a unique in-plane frame analytically, so we do
        not need to search all vertex-pair directions.  Output is identical to
        the original algorithm for planar tessellated faces.
        """
        if not vertices or len(vertices) < 3 or not normal:
            return None

        # --- deduplicate vertices (same as original) ---
        unique = []
        seen = set()
        for v in vertices:
            key = (round(v["x"], 4), round(v["y"], 4), round(v["z"], 4))
            if key not in seen:
                seen.add(key)
                unique.append({"x": key[0], "y": key[1], "z": key[2]})

        if len(unique) < 3:
            return None

        # --- helpers (same signatures as original, inlined for speed) ---
        def dot(a, b):
            return a["x"] * b["x"] + a["y"] * b["y"] + a["z"] * b["z"]

        def cross(a, b):
            return {
                "x": a["y"] * b["z"] - a["z"] * b["y"],
                "y": a["z"] * b["x"] - a["x"] * b["z"],
                "z": a["x"] * b["y"] - a["y"] * b["x"],
            }

        def normalize(v):
            mag = (v["x"] ** 2 + v["y"] ** 2 + v["z"] ** 2) ** 0.5
            if mag < 1e-9:
                return None
            return {"x": v["x"] / mag, "y": v["y"] / mag, "z": v["z"] / mag}

        # --- Step 1: normalise the face normal ---
        n = normalize(normal)
        if not n:
            return None

        # --- Step 2: pick the global reference axis least parallel to n ---
        # (same tie-breaking order as most OBB implementations)
        abs_nx, abs_ny, abs_nz = abs(n["x"]), abs(n["y"]), abs(n["z"])
        if abs_nx <= abs_ny and abs_nx <= abs_nz:
            ref = {"x": 1.0, "y": 0.0, "z": 0.0}
        elif abs_ny <= abs_nx and abs_ny <= abs_nz:
            ref = {"x": 0.0, "y": 1.0, "z": 0.0}
        else:
            ref = {"x": 0.0, "y": 0.0, "z": 1.0}

        # --- Step 3: project ref into the face plane → u ---
        d_dot_n = dot(ref, n)
        direction = {
            "x": ref["x"] - d_dot_n * n["x"],
            "y": ref["y"] - d_dot_n * n["y"],
            "z": ref["z"] - d_dot_n * n["z"],
        }
        u = normalize(direction)
        if not u:
            return None

        # --- Step 4: second in-plane axis → v ---
        v_axis = normalize(cross(n, u))
        if not v_axis:
            return None

        # --- Step 5: project all vertices, measure spans (O(n)) ---
        u_vals = [dot(p, u) for p in unique]
        v_vals = [dot(p, v_axis) for p in unique]

        span_u = max(u_vals) - min(u_vals)
        span_v = max(v_vals) - min(v_vals)

        if span_u < 0.001 or span_v < 0.001:
            return None

        # --- Step 6: assign length/width consistently (length ≥ width) ---
        length = max(span_u, span_v)
        width  = min(span_u, span_v)
        area   = span_u * span_v

        return {
            "length": length,
            "width": width,
            "area": area,
            "axis": u if span_u >= span_v else v_axis,
            "width_axis": v_axis if span_u >= span_v else u,
            "normal": n,
        }

    def _build_cylindrical_feature(self, vertices, largest, medium, smallest):
        """Build a hole feature from cylindrical-face geometry."""
        position, axis = self.calculate_position_and_axis(vertices, "hole")
        similarity_threshold = 0.1
        largest_medium_ratio = medium / largest if largest > 0 else 0
        medium_smallest_ratio = medium / smallest if smallest > 0 else 0

        if abs(1.0 - largest_medium_ratio) < similarity_threshold:
            diameter = (largest + medium) / 2.0
            length_dim = smallest
        elif abs(1.0 - medium_smallest_ratio) < similarity_threshold:
            diameter = (medium + smallest) / 2.0
            length_dim = largest
        else:
            diameter = smallest
            length_dim = largest

        radius = diameter / 2.0
        return {
            "type": "hole",
            "hole_type": "through",
            "confidence": 0.90,
            "dimensions": {
                "length": round(length_dim, 1),
                "diameter": round(diameter, 1),
                "radius": round(radius, 1),
            },
            "position": {k: round(v, 1) for k, v in position.items()},
            "axis": axis,
        }

    def analyze_face_geometry(self, vertices, cached_bbox=None):
        """
        Analyze face geometry to detect shape type.
        
        Improved logic matching face_analyzer.js more closely.
        Handles planar faces (smallest dimension ≈ 0) correctly.
        Calculates radius properly from diameter.
        Rounds all dimensions to 1 decimal place.
        
        Args:
            vertices: List of vertex dictionaries with x, y, z coordinates
            cached_bbox: Optional pre-computed (size_x, size_y, size_z) tuple
                         from _build_face_index to avoid recomputation.
            
        Returns:
            dict: Feature metadata or None
        """
        if not vertices or len(vertices) < 3:
            return None
        
        # Use cached bbox dims if available, otherwise compute
        if cached_bbox is not None:
            size_x, size_y, size_z = cached_bbox
        else:
            xs = [v['x'] for v in vertices]
            ys = [v['y'] for v in vertices]
            zs = [v['z'] for v in vertices]
            size_x = max(xs) - min(xs)
            size_y = max(ys) - min(ys)
            size_z = max(zs) - min(zs)
        
        # Sort dimensions
        dims = sorted([size_x, size_y, size_z], reverse=True)
        largest, medium, smallest = dims[0], dims[1], dims[2]
        
        vertex_count = len(vertices)
        planarity = self.check_planarity(vertices)
        
        # Planar faces only: square/rectangular classification.
        # Curved compact faces can have equal bbox dimensions (e.g. 2x2x2)
        # and must not be classified as square before curvature analysis.
        if planarity["is_planar"]:
            planar_measurements = self.calculate_planar_measurements(vertices, planarity.get("normal"))
            if not planar_measurements:
                planar_dims = [d for d in [size_x, size_y, size_z] if d > 0.001]
                if len(planar_dims) < 2:
                    return None
                length = max(planar_dims)
                width = min(planar_dims)
                axis = self.calculate_position_and_axis(vertices, "rectangular")[1]
                width_axis = None
                normal = planarity.get("normal")
                area = length * width
            else:
                length = planar_measurements["length"]
                width = planar_measurements["width"]
                axis = planar_measurements["axis"]
                width_axis = planar_measurements["width_axis"]
                normal = planar_measurements["normal"]
                area = planar_measurements["area"]

            if width < 0.001:
                return None

            aspect_ratio = length / width

            # Calculate position and axis
            position, _ = self.calculate_position_and_axis(vertices, "rectangular")
            local_frame = {
                "length_axis": {k: round(v, 6) for k, v in axis.items()},
                "width_axis": {k: round(v, 6) for k, v in width_axis.items()} if width_axis else None,
                "normal": {k: round(v, 6) for k, v in normal.items()} if normal else None,
                "area": round(area, 3),
                "measurement": "oriented_face_plane"
            }
            
            # Check if square (aspect ratio close to 1:1)
            if abs(aspect_ratio - 1.0) < 0.15:  # Within 15% of 1:1
                return {
                    "type": "square",
                    "confidence": 0.95,
                    "dimensions": {
                        "side": round(length, 1),
                        "area": round(area, 1)
                    },
                    "position": {k: round(v, 1) for k, v in position.items()},
                    "axis": axis,
                    "local_frame": local_frame
                }
            else:
                return {
                    "type": "rectangular",
                    "confidence": 0.95,
                    "dimensions": {
                        "length": round(length, 1),
                        "width": round(width, 1),
                        "area": round(area, 1)
                    },
                    "position": {k: round(v, 1) for k, v in position.items()},
                    "axis": axis,
                    "local_frame": local_frame
                }
        
        # For non-planar faces, avoid division by zero
        if smallest == 0:
            smallest = 0.001
        
        aspect_ratio = largest / smallest

        # Detection order aligned with face_analyzer.js (fillet < 80, cylindrical > 90)

        # 1. CYLINDRICAL — high vertex count curved faces (e.g. JfM: 96 vertices)
        if vertex_count > CYLINDRICAL_MIN_VERTICES:
            return self._build_cylindrical_feature(vertices, largest, medium, smallest)

        # Medium-complexity curved faces mirror face_analyzer.js:
        # aspect > 3 => hole-like curved surface, otherwise fillet.
        if vertex_count < 14 and aspect_ratio > 3.0:
            return self._build_cylindrical_feature(vertices, largest, medium, smallest)

        # 2. FILLET — low vertex count curved faces.
        # This mirrors face_analyzer.js dimensionPattern fallback:
        # vertices < 80 => fillet_signature, even when bbox aspect is ~1.
        if vertex_count < FILLET_MAX_VERTICES:
            position, axis = self.calculate_position_and_axis(vertices, "fillet")
            # Keep fillet radius aligned with the browser viewer logic:
            # the smallest dimension is usually sheet thickness, not the fillet radius.
            radius = medium
            return {
                "type": "fillet",
                "confidence": 0.90,
                "dimensions": {
                    "length": round(largest, 1),
                    "width": round(medium, 1),
                    "radius": round(radius, 1),
                },
                "position": {k: round(v, 1) for k, v in position.items()},
                "axis": axis,
            }

        # 3. Ambiguous vertex band 80–90 (face_analyzer.js tiebreaker)
        if FILLET_MAX_VERTICES <= vertex_count <= CYLINDRICAL_MIN_VERTICES:
            if aspect_ratio > 5.0:
                return self._build_cylindrical_feature(vertices, largest, medium, smallest)
            position, axis = self.calculate_position_and_axis(vertices, "fillet")
            # Keep fillet radius aligned with the browser viewer logic:
            # the smallest dimension is usually sheet thickness, not the fillet radius.
            radius = medium
            return {
                "type": "fillet",
                "confidence": 0.75,
                "dimensions": {
                    "length": round(largest, 1),
                    "width": round(medium, 1),
                    "radius": round(radius, 1),
                },
                "position": {k: round(v, 1) for k, v in position.items()},
                "axis": axis,
            }

        # 4. HOLE DETECTION
        # Hole (through-hole): vertices >= 200, aspect ratio > 3.0
        if vertex_count >= 200 and aspect_ratio > 3.0:
            position, axis = self.calculate_position_and_axis(vertices, "hole")
            # 🔥 FIX: For cylindrical holes, 2 similar dimensions = diameter, different one = depth
            # Check if 2 dimensions are similar (within 10% tolerance) - they are diameter
            similarity_threshold = 0.1  # 10% tolerance
            largest_medium_ratio = medium / largest if largest > 0 else 0
            
            if abs(1.0 - largest_medium_ratio) < similarity_threshold:
                # 2 similar dimensions (largest ≈ medium) → they are diameter
                # smallest is depth/thickness
                diameter = (largest + medium) / 2.0  # Average of the 2 similar dimensions
                depth = smallest
            else:
                # All 3 dimensions different - use aspect ratio logic
                # For high aspect ratio, largest is likely depth, smallest is diameter
                if aspect_ratio > 10.0:
                    # Very high aspect ratio: largest = depth, smallest = diameter
                    diameter = smallest
                    depth = largest
                else:
                    # Moderate aspect ratio: check which 2 are more similar
                    largest_smallest_ratio = smallest / largest if largest > 0 else 0
                    medium_smallest_ratio = smallest / medium if medium > 0 else 0
                    
                    if abs(1.0 - largest_smallest_ratio) < abs(1.0 - medium_smallest_ratio):
                        # largest and smallest are more similar → diameter
                        diameter = (largest + smallest) / 2.0
                        depth = medium
                    else:
                        # medium and smallest are more similar → diameter
                        diameter = (medium + smallest) / 2.0
                        depth = largest
            
            radius = diameter / 2.0
            return {
                "type": "hole",
                "hole_type": "through",
                "confidence": 0.85,
                "dimensions": {
                    "length": round(depth, 1),  # ✅ FIX: depth, not largest
                    "diameter": round(diameter, 1),
                    "radius": round(radius, 1)
                },
                "position": {k: round(v, 1) for k, v in position.items()},
                "axis": axis
            }
        
        # 5. PLANAR FACE DETECTION
        # Rectangular/Square: low aspect ratio
        # 🔥 FIX: Ensure vertex count is low (prevent short cylinders from being detected as square)
        if aspect_ratio < 2.5 and vertex_count < 50:
            position, axis = self.calculate_position_and_axis(vertices, "rectangular")
            
            # Check if square (aspect ratio close to 1:1)
            if abs(largest - medium) < 0.1 * largest:
                return {
                    "type": "square",
                    "confidence": 0.90,
                    "dimensions": {
                        "side": round(largest, 1)
                    },
                    "position": {k: round(v, 1) for k, v in position.items()},
                    "axis": axis
                }
            else:
                return {
                    "type": "rectangular",
                    "confidence": 0.90,
                    "dimensions": {
                        "length": round(largest, 1),
                        "width": round(medium, 1)
                    },
                    "position": {k: round(v, 1) for k, v in position.items()},
                    "axis": axis
                }
        
        # 6. FALLBACK: Treat as hole if moderate vertex count (51–90, no fillet/cylindrical match)
        if vertex_count > 50:
            position, axis = self.calculate_position_and_axis(vertices, "hole")
            # 🔥 FIX: For cylindrical holes, 2 similar dimensions = diameter, different one = depth
            # Check if 2 dimensions are similar (within 10% tolerance) - they are diameter
            similarity_threshold = 0.1  # 10% tolerance
            largest_medium_ratio = medium / largest if largest > 0 else 0
            
            if abs(1.0 - largest_medium_ratio) < similarity_threshold:
                # 2 similar dimensions (largest ≈ medium) → they are diameter
                # smallest is depth/thickness
                diameter = (largest + medium) / 2.0  # Average of the 2 similar dimensions
                depth = smallest
            else:
                # All 3 dimensions different - use aspect ratio logic
                if aspect_ratio > 10.0:
                    # Very high aspect ratio: largest = depth, smallest = diameter
                    diameter = smallest
                    depth = largest
                else:
                    # Check which 2 are more similar
                    largest_smallest_ratio = smallest / largest if largest > 0 else 0
                    medium_smallest_ratio = smallest / medium if medium > 0 else 0
                    
                    if abs(1.0 - largest_smallest_ratio) < abs(1.0 - medium_smallest_ratio):
                        diameter = (largest + smallest) / 2.0
                        depth = medium
                    else:
                        diameter = (medium + smallest) / 2.0
                        depth = largest
            
            radius = diameter / 2.0
            return {
                "type": "hole",
                "hole_type": "through",  # Assume through-hole for ambiguous cases
                "confidence": 0.50,  # Lower confidence for fallback
                "dimensions": {
                    "length": round(depth, 1),  # ✅ FIX: depth, not largest
                    "diameter": round(diameter, 1),
                    "radius": round(radius, 1)
                },
                "position": {k: round(v, 1) for k, v in position.items()},
                "axis": axis
            }
        
        return None
    
    def extract_face_vertices(self, face):
        """Extract all vertices from a face's facets"""
        vertices = []
        if 'facets' in face:
            for facet in face['facets']:
                if 'vertices' in facet:
                    vertices.extend(facet['vertices'])
        return vertices
    
    def enrich_features(self, json_data):
        """
        Enrich JSON with additional geometric features.
        
        Args:
            json_data: Original JSON data
            
        Returns:
            dict: Enriched JSON data
        """
        # 🔥 PRESERVE: Geometry field from step_converter.py
        geometry_data = json_data.get('geometry', None)
        if geometry_data:
            logger.debug(f"📐 Preserving geometry field from step_converter.py")
        
        # 🔥 PRESERVE: Shape type from step_converter.py
        shape_type = json_data.get('shape_type', None)
        
        # Preserve existing features (from step_converter.py)
        existing_features = json_data.get('features', [])
        existing_face_ids = set()
        existing_feature_types = {}  # Track count by type
        
        for feature in existing_features:
            if 'face_ids' in feature:
                existing_face_ids.update(feature['face_ids'])
            
            # Track feature types
            ftype = feature.get('type', 'unknown')
            existing_feature_types[ftype] = existing_feature_types.get(ftype, 0) + 1
        
        # Single context log: shape + existing features
        existing_types_str = ", ".join([f"{t}:{c}" for t, c in sorted(existing_feature_types.items())]) if existing_features else "none"
        logger.debug(
            f"📋 Context | shape={shape_type or 'N/A'} | "
            f"existing={len(existing_features)} ({existing_types_str}) | "
            f"claimed_faces={len(existing_face_ids)}"
        )
        
        # Analyze all faces
        new_features = []
        analyzed_count = 0
        detected_count = 0
        skipped_count = 0
        
        if 'faces' in json_data and 'bodies' in json_data['faces']:
            # Track orientation statistics
            orientation_stats = {}
            bodies = json_data['faces']['bodies']
            total_faces = sum(len(body.get('faces', [])) for body in bodies)
            logger.info(
                f"[ENRICHER] Mesh scan started | bodies={len(bodies)} | "
                f"faces={total_faces} | existing_features={len(existing_features)}"
            )

            # 🔥 Pre-compute model bounding box center (approximates JS getOptimalCenter)
            # Used by classify_face_orientation() for position-based logic
            center_start = time.perf_counter()
            model_center = self.calculate_model_center(json_data)
            logger.info(
                f"[ENRICHER] Model center calculated in {time.perf_counter() - center_start:.2f}s"
            )
            logger.debug(
                f"📐 Model center for orientation: "
                f"({model_center['x']:.2f}, {model_center['y']:.2f}, {model_center['z']:.2f})"
                if model_center else "⚠️ Could not calculate model center"
            )

            index_start = time.perf_counter()
            face_index = self._build_face_index(json_data)
            logger.info(
                f"[ENRICHER] Face index built in {time.perf_counter() - index_start:.2f}s | "
                f"indexed_faces={len(face_index)}"
            )
            bend_contexts = self._build_bend_contexts(existing_features, face_index)
            self._apply_bend_contexts_to_features(existing_features, bend_contexts)

            scan_start = time.perf_counter()
            last_progress_log = scan_start
            processed_faces = 0

            for body in bodies:
                if 'faces' not in body:
                    continue

                for face in body['faces']:
                    processed_faces += 1
                    face_id = face.get('id', '')

                    # Reuse pre-computed data from index (avoids redundant extraction)
                    cached_face = face_index.get(face_id, {})
                    vertices = cached_face.get("vertices") or self.extract_face_vertices(face)

                    # Reuse pre-computed normal and centroid from index
                    normal = cached_face.get("normal") or self.calculate_face_normal(vertices)
                    face_centroid = cached_face.get("centroid") or self.calculate_face_centroid(vertices)
                    if normal:
                        orientation = self.classify_face_orientation(
                            normal,
                            face_centroid=face_centroid,
                            model_center=model_center
                        )
                        
                        # Add orientation and normal to face data
                        face['orientation'] = orientation
                        face['normal'] = {
                            'x': round(normal['x'], 3),
                            'y': round(normal['y'], 3),
                            'z': round(normal['z'], 3)
                        }
                        
                        # Update statistics
                        orientation_stats[orientation] = orientation_stats.get(orientation, 0) + 1
                        
                        logger.debug(f"📐 Face {face_id}: {orientation} (normal: {normal['x']:.2f}, {normal['y']:.2f}, {normal['z']:.2f})")
                    else:
                        face['orientation'] = "UNKNOWN"
                        face['normal'] = None
                        orientation_stats["UNKNOWN"] = orientation_stats.get("UNKNOWN", 0) + 1
                    
                    # Skip faces already in existing features
                    if face_id in existing_face_ids:
                        skipped_count += 1
                        logger.debug(f"⏭️  Skipping {face_id} (already has feature from step_converter.py)")
                        now = time.perf_counter()
                        if processed_faces == total_faces or processed_faces % 100 == 0 or now - last_progress_log >= 10:
                            elapsed = now - scan_start
                            rate = processed_faces / elapsed if elapsed > 0 else 0
                            logger.info(
                                f"[ENRICHER] Face scan progress | {processed_faces}/{total_faces} faces | "
                                f"detected={detected_count} | skipped={skipped_count} | "
                                f"elapsed={elapsed:.1f}s | rate={rate:.1f} faces/s"
                            )
                            last_progress_log = now
                        continue
                    
                    analyzed_count += 1
                    
                    # Analyze geometry — pass cached bbox to avoid recomputation
                    cached_bbox = cached_face.get("bbox_dims")
                    feature_info = self.analyze_face_geometry(vertices, cached_bbox=cached_bbox)
                    
                    if feature_info:
                        detected_count += 1
                        # 🔥 NEW FORMAT: Simplified structure matching user's example
                        new_feature = self._format_feature(feature_info, face_id)
                        if bend_contexts and new_feature.get("type") in ("rectangular", "square"):
                            related_bending = self._match_related_bending(
                                new_feature,
                                face_id,
                                face_centroid,
                                normal,
                                bend_contexts
                            )
                            if related_bending:
                                new_feature["related_bending"] = related_bending
                        new_features.append(new_feature)
                        logger.debug(f"✅ Detected {feature_info['type']} on face {face_id}")
                    else:
                        logger.debug(f"⚠️  No feature detected for face {face_id} (vertices: {len(vertices)})")

                    now = time.perf_counter()
                    if processed_faces == total_faces or processed_faces % 100 == 0 or now - last_progress_log >= 10:
                        elapsed = now - scan_start
                        rate = processed_faces / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"[ENRICHER] Face scan progress | {processed_faces}/{total_faces} faces | "
                            f"detected={detected_count} | skipped={skipped_count} | "
                            f"elapsed={elapsed:.1f}s | rate={rate:.1f} faces/s"
                        )
                        last_progress_log = now
        
        # Combine existing and new features
        enriched_features = existing_features + new_features
        
        # 🔥 POST-PROCESSING: Convert to new format and round all values
        formatted_features = []
        for feature in enriched_features:
            formatted_feature = self._convert_to_new_format(feature)
            if formatted_feature:
                formatted_features.append(formatted_feature)
        formatted_features = [
            self._convert_to_new_format(feature) if feature.get('type') == 'cylindrical' else feature
            for feature in formatted_features
        ]
        
        # Feature type summary
        feature_types = {}
        for f in formatted_features:
            ftype = f.get('type', 'unknown')
            feature_types[ftype] = feature_types.get(ftype, 0) + 1
        
        # Compact summary
        types_str = ", ".join([f"{t}:{c}" for t, c in sorted(feature_types.items())])
        orientations_str = ", ".join([f"{o}:{c}" for o, c in sorted(orientation_stats.items())])
        
        logger.info(
            f"📊 features={len(formatted_features)} ({types_str}) | "
            f"faces={analyzed_count} analyzed, {skipped_count} skipped | "
            f"orientations: {orientations_str}"
        )
        
        # Update JSON data
        json_data['features'] = formatted_features
        
        # Add enrichment metadata
        json_data['enrichment_metadata'] = {
            "enriched_at": datetime.now().isoformat(),
            "enrichment_version": "2.1",
            "total_features": len(formatted_features),
            "new_features_added": detected_count,
            "existing_features_preserved": len(existing_features),
            "feature_types": feature_types,
            "face_orientations": orientation_stats,
            "responsibility": {
                "step_converter": ["threaded_hole", "countersink", "oblong", "bending"],
                "enrich_features": ["hole", "fillet", "rectangular", "square"]
            },
            "format_notes": "All features cleaned - debug params removed (is_threaded, thread_confidence, match_strategy, validation, bounding_box, arc_length, confidence, pair_id)"
        }
        
        # 🔥 PRESERVE: Restore geometry field if it existed in input
        if geometry_data:
            json_data['geometry'] = geometry_data
        
        # 🔥 PRESERVE: Restore shape_type if it existed in input
        if shape_type:
            json_data['shape_type'] = shape_type
        
        return json_data

    def _dot(self, a, b):
        if not a or not b:
            return 0.0
        return (
            a.get("x", 0.0) * b.get("x", 0.0) +
            a.get("y", 0.0) * b.get("y", 0.0) +
            a.get("z", 0.0) * b.get("z", 0.0)
        )

    def _normalize(self, vector):
        if not vector:
            return None
        mag = (
            vector.get("x", 0.0) ** 2 +
            vector.get("y", 0.0) ** 2 +
            vector.get("z", 0.0) ** 2
        ) ** 0.5
        if mag < 1e-9:
            return None
        return {
            "x": vector.get("x", 0.0) / mag,
            "y": vector.get("y", 0.0) / mag,
            "z": vector.get("z", 0.0) / mag,
        }

    def _round_vector(self, vector, digits=6):
        if not vector:
            return None
        return {axis: round(vector.get(axis, 0.0), digits) for axis in ("x", "y", "z")}

    def _distance(self, a, b):
        if not a or not b:
            return None
        return (
            (a.get("x", 0.0) - b.get("x", 0.0)) ** 2 +
            (a.get("y", 0.0) - b.get("y", 0.0)) ** 2 +
            (a.get("z", 0.0) - b.get("z", 0.0)) ** 2
        ) ** 0.5

    def _build_face_index(self, json_data):
        """
        Build a lookup from face_id → pre-computed geometry data.

        OPTIMIZED: Also pre-computes bbox_dims (size_x, size_y, size_z) once per
        face so analyze_face_geometry() can skip the redundant per-vertex loops.
        """
        face_index = {}
        for body in json_data.get("faces", {}).get("bodies", []):
            for face in body.get("faces", []):
                face_id = face.get("id", "")
                if not face_id:
                    continue
                vertices = self.extract_face_vertices(face)

                # Pre-compute bbox dims in one pass (O(n)) for reuse in analyze_face_geometry
                bbox_dims = None
                if vertices:
                    min_x = max_x = vertices[0]['x']
                    min_y = max_y = vertices[0]['y']
                    min_z = max_z = vertices[0]['z']
                    for v in vertices[1:]:
                        vx, vy, vz = v['x'], v['y'], v['z']
                        if vx < min_x: min_x = vx
                        elif vx > max_x: max_x = vx
                        if vy < min_y: min_y = vy
                        elif vy > max_y: max_y = vy
                        if vz < min_z: min_z = vz
                        elif vz > max_z: max_z = vz
                    bbox_dims = (max_x - min_x, max_y - min_y, max_z - min_z)

                face_index[face_id] = {
                    "face": face,
                    "vertices": vertices,
                    "normal": self.calculate_face_normal(vertices),
                    "centroid": self.calculate_face_centroid(vertices),
                    "bbox_dims": bbox_dims,
                }
        return face_index

    def _estimate_axis_from_vertices(self, vertices):
        if not vertices:
            return None

        xs = [v["x"] for v in vertices]
        ys = [v["y"] for v in vertices]
        zs = [v["z"] for v in vertices]
        spans = {
            "x": max(xs) - min(xs),
            "y": max(ys) - min(ys),
            "z": max(zs) - min(zs),
        }
        axis_name = max(spans, key=spans.get)
        if spans[axis_name] < 1e-6:
            return None
        axis = {"x": 0.0, "y": 0.0, "z": 0.0}
        axis[axis_name] = 1.0
        return axis

    def _profile_plane_from_axis(self, axis):
        axis = self._normalize(axis)
        if not axis:
            return None
        dominant = max(("x", "y", "z"), key=lambda k: abs(axis.get(k, 0.0)))
        if abs(axis.get(dominant, 0.0)) < 0.85:
            return "OBLIQUE"
        return {
            "x": "YZ",
            "y": "XZ",
            "z": "XY",
        }[dominant]

    def _collect_bend_vertices(self, feature, face_index):
        face_ids = []
        inner = feature.get("inner", {}) or {}
        outer = feature.get("outer", {}) or {}
        face_ids.extend(inner.get("face_ids", []) or [])
        face_ids.extend(outer.get("face_ids", []) or [])
        face_ids.extend(feature.get("face_ids", []) or [])

        vertices = []
        centroids = []
        for face_id in face_ids:
            face_data = face_index.get(face_id)
            if not face_data:
                continue
            vertices.extend(face_data.get("vertices") or [])
            if face_data.get("centroid"):
                centroids.append(face_data["centroid"])

        center = None
        if centroids:
            center = {
                "x": sum(c["x"] for c in centroids) / len(centroids),
                "y": sum(c["y"] for c in centroids) / len(centroids),
                "z": sum(c["z"] for c in centroids) / len(centroids),
            }
        return vertices, center

    def _build_bend_contexts(self, features, face_index):
        contexts = []
        for index, feature in enumerate(features or []):
            if feature.get("type") != "bending":
                continue

            bend_vertices, bend_center = self._collect_bend_vertices(feature, face_index)
            bend_axis = self._estimate_axis_from_vertices(bend_vertices)
            profile_plane = self._profile_plane_from_axis(bend_axis)
            inner = feature.get("inner", {}) or {}
            outer = feature.get("outer", {}) or {}

            context = {
                "type": "bending",
                "bend_id": feature.get("id") or f"bend_{index + 1}",
                "subtype": feature.get("subtype"),
                "inner_radius": inner.get("radius"),
                "outer_radius": outer.get("radius"),
                "inner_face_ids": inner.get("face_ids", []) or [],
                "outer_face_ids": outer.get("face_ids", []) or [],
                "relation": "bend_profile_face",
            }

            if feature.get("bend_angle") is not None:
                context["bend_angle"] = feature.get("bend_angle")
            if bend_axis:
                context["bend_axis"] = self._round_vector(bend_axis)
            if profile_plane:
                context["profile_plane"] = profile_plane
            if bend_center:
                context["_bend_center"] = bend_center

            contexts.append(context)
        return contexts

    def _apply_bend_contexts_to_features(self, features, bend_contexts):
        bending_features = [f for f in (features or []) if f.get("type") == "bending"]
        for feature, context in zip(bending_features, bend_contexts):
            for key in ("bend_axis", "profile_plane"):
                if context.get(key) is not None:
                    feature[key] = context[key]

    def _match_related_bending(self, feature, face_id, face_centroid, face_normal, bend_contexts):
        normal = self._normalize(
            (feature.get("local_frame") or {}).get("normal") or face_normal
        )
        if not normal:
            return None

        best_context = None
        best_score = 0.0
        for context in bend_contexts:
            bend_axis = self._normalize(context.get("bend_axis"))
            if not bend_axis:
                continue

            # Planar sheet faces related to a bend are profile faces: their
            # normals are usually perpendicular to the bend axis. End caps are
            # parallel to the bend axis and should not inherit bend context.
            perpendicular_score = 1.0 - abs(self._dot(normal, bend_axis))
            if perpendicular_score < 0.65:
                continue
            score = perpendicular_score

            bend_center = context.get("_bend_center")
            distance = self._distance(face_centroid, bend_center)
            if distance is not None:
                score += 1.0 / (1.0 + distance / 100.0)

            if score > best_score:
                best_score = score
                best_context = context

        if not best_context or best_score < 0.75:
            return None

        related = {
            key: value for key, value in best_context.items()
            if not key.startswith("_") and value is not None
        }
        related["confidence"] = round(min(best_score / 2.0, 0.99), 3)
        return related
    
    def _format_feature(self, feature_info, face_id):
        """
        Format feature info into new JSON structure.
        
        Args:
            feature_info: Feature information from analyze_face_geometry
            face_id: Face ID
            
        Returns:
            dict: Formatted feature
        """
        feature_type = feature_info['type']
        position = feature_info.get('position', {"x": 0, "y": 0, "z": 0})
        axis = feature_info.get('axis', {"x": 1, "y": 0, "z": 0})
        dims = feature_info.get('dimensions', {})
        local_frame = feature_info.get('local_frame')
        
        # Base structure
        feature = {
            "type": feature_type,
            "face_ids": [face_id]
        }
        
        # Add type-specific fields
        if feature_type == 'hole':
            feature.update({
                "subtype": feature_info.get('hole_type', 'through'),
                "diameter": dims.get('diameter', 0),
                "depth": dims.get('length', 0),
                "position": position,
                "axis": axis,
                "thread": None
            })
        elif feature_type == 'fillet':
            feature.update({
                "radius": dims.get('radius', 0),
                "face_ids": [face_id],
            })
        elif feature_type == 'cylindrical':
            feature.update({
                # Keep cylindrical as an internal detection result, but export it as
                # a hole so downstream feature-name mapping remains stable.
                "type": "hole",
                "subtype": "through",
                "diameter": dims.get('diameter', 0),
                "depth": dims.get('length', 0),
                "position": position,
                "axis": axis,
                "thread": None,
            })
        elif feature_type == 'rectangular':
            feature.update({
                "length": dims.get('length', 0),
                "width": dims.get('width', 0),
                "area": dims.get('area', 0),
                "position": position,
                "axis": axis
            })
            if local_frame:
                feature["local_frame"] = local_frame
        elif feature_type == 'square':
            feature.update({
                "side": dims.get('side', 0),
                "area": dims.get('area', 0),
                "position": position,
                "axis": axis
            })
            if local_frame:
                feature["local_frame"] = local_frame
        
        return feature
    
    def _convert_to_new_format(self, feature):
        """
        Convert existing feature to new format.
        
        Args:
            feature: Original feature dict
            
        Returns:
            dict: Converted feature in new format (cleaned, no debug params)
        """
        feature_type = feature.get('type')
        
        if not feature_type:
            return None
        
        # Round helper
        def round_val(val):
            return round(val, 1) if isinstance(val, (int, float)) else val
        
        def round_pos(pos):
            if isinstance(pos, dict):
                return {k: round_val(v) for k, v in pos.items()}
            return pos
        
        # Convert based on type
        if feature_type == 'hole':
            # Check if threaded
            is_threaded = feature.get('is_threaded', False)
            thread = feature.get('thread', None)
            
            new_feature = {
                "type": "hole",
                "subtype": "threaded" if is_threaded else feature.get('subtype', feature.get('hole_type', 'through')),
                "diameter": round_val(feature.get('diameter', 0)),
                "depth": round_val(feature.get('depth', feature.get('length', 0))),
                "position": round_pos(feature.get('position', {"x": 0, "y": 0, "z": 0})),
                "axis": round_pos(feature.get('axis', {"x": 0, "y": 0, "z": 1})),
                "face_ids": feature.get('face_ids', [])
            }
            
            # Add thread info only if threaded
            if is_threaded and thread:
                new_feature["thread"] = thread
            else:
                new_feature["thread"] = None
            
            # ✅ REMOVED: radius, is_threaded, thread_confidence, face_count, match_strategy
            return new_feature
        
        elif feature_type == 'countersink':
            # ✅ REMOVED: cylinder_depth, total_depth (keep only cone depth)
            return {
                "type": "countersink",
                "diameter_top": round_val(feature.get('diameter_top', 0)),
                "diameter_bottom": round_val(feature.get('diameter_bottom', 0)),
                "angle": round_val(feature.get('angle', 90)),
                "depth": round_val(feature.get('depth', 0)),
                "position": round_pos(feature.get('position', {"x": 0, "y": 0, "z": 0})),
                "face_ids": feature.get('face_ids', [])
            }
        
        elif feature_type == 'fillet':
            return {
                "type": "fillet",
                "radius": round_val(feature.get('radius', 0)),
                "face_ids": feature.get('face_ids', []),
            }

        elif feature_type == 'cylindrical':
            return {
                "type": "hole",
                "subtype": feature.get('subtype', feature.get('hole_type', 'through')),
                "diameter": round_val(feature.get('diameter', feature.get('dimensions', {}).get('diameter', 0))),
                "depth": round_val(feature.get('depth', feature.get('length', feature.get('dimensions', {}).get('length', 0)))),
                "position": round_pos(feature.get('position', {"x": 0, "y": 0, "z": 0})),
                "axis": round_pos(feature.get('axis', {"x": 1, "y": 0, "z": 0})),
                "face_ids": feature.get('face_ids', []),
                "thread": None,
            }

        elif feature_type == 'rectangular':
            new_feature = {
                "type": "rectangular",
                "length": round_val(feature.get('length', 0)),
                "width": round_val(feature.get('width', 0)),
                "area": round_val(feature.get('area', 0)),
                "position": round_pos(feature.get('position', {"x": 0, "y": 0, "z": 0})),
                "axis": round_pos(feature.get('axis', {"x": 1, "y": 0, "z": 0})),
                "face_ids": feature.get('face_ids', [])
            }
            if feature.get("local_frame"):
                new_feature["local_frame"] = feature.get("local_frame")
            if feature.get("related_bending"):
                new_feature["related_bending"] = feature.get("related_bending")
            return new_feature
        
        elif feature_type == 'square':
            new_feature = {
                "type": "square",
                "side": round_val(feature.get('side', 0)),
                "area": round_val(feature.get('area', 0)),
                "position": round_pos(feature.get('position', {"x": 0, "y": 0, "z": 0})),
                "axis": round_pos(feature.get('axis', {"x": 1, "y": 0, "z": 0})),
                "face_ids": feature.get('face_ids', [])
            }
            if feature.get("local_frame"):
                new_feature["local_frame"] = feature.get("local_frame")
            if feature.get("related_bending"):
                new_feature["related_bending"] = feature.get("related_bending")
            return new_feature
        
        elif feature_type == 'oblong':
            # ✅ NEW: Support oblong features from step_converter.py
            # ✅ REMOVED: validation, match_quality, bounding_box (debug info)
            return {
                "type": "oblong",
                "total_length": round_val(feature.get('total_length', 0)),
                "straight_length": round_val(feature.get('straight_length', 0)),
                "width": round_val(feature.get('width', 0)),
                "depth": round_val(feature.get('depth', 0)),
                "end_radius": round_val(feature.get('end_radius', 0)),
                "corner": round_pos(feature.get('corner', {"x": 0, "y": 0, "z": 0})),
                "center": round_pos(feature.get('center', {"x": 0, "y": 0, "z": 0})),
                "direction": round_pos(feature.get('direction', {"x": 0, "y": 0, "z": 1})),
                "face_ids": feature.get('face_ids', [])
            }
        
        elif feature_type == 'bending':
            # ✅ NEW: Support bending features from step_converter.py
            # ✅ REMOVED: arc_length, confidence, pair_id (debug info)
            inner = feature.get('inner', {})
            outer = feature.get('outer', {})
            
            # [FIX] Convert "cylindrical_bend" subtype to "L-shape"
            original_subtype = feature.get('subtype', 'detected')
            if original_subtype == 'cylindrical_bend':
                subtype = 'L-shape'
            else:
                subtype = original_subtype
            
            # ✅ Extract bend_angle from feature metadata
            bend_angle = feature.get('bend_angle', None)
            
            bending_feature = {
                "type": "bending",
                "subtype": subtype,
                "inner": {
                    "face_ids": inner.get('face_ids', []),
                    "radius": round_val(inner.get('radius', 0))
                },
                "outer": {
                    "face_ids": outer.get('face_ids', []),
                    "radius": round_val(outer.get('radius', 0))
                },
                "face_ids": feature.get('face_ids', [])
            }
            
            # ✅ Add bend_angle if available (for L-shape, U-shape, Z-shape, tub)
            if bend_angle is not None:
                bending_feature["bend_angle"] = round_val(bend_angle)
            if feature.get("bend_axis"):
                bending_feature["bend_axis"] = round_pos(feature.get("bend_axis"))
            if feature.get("profile_plane"):
                bending_feature["profile_plane"] = feature.get("profile_plane")
            
            return bending_feature
        
        # Unknown type, return as-is but cleaned
        return feature
    
    def process_file(self, json_path, json_data=None):
        """Process a single JSON file with comprehensive error handling"""
        start_time = time.perf_counter()
        logger.info(f"🔍 Enriching: {json_path.name}")
        
        try:
            # Check input file size
            input_size_mb = get_file_size_mb(str(json_path))
            if input_size_mb > 50:
                logger.warning(f"⚠️  Processing large input file: {input_size_mb:.2f} MB")
                logger.warning(f"   This may take longer than usual...")
            
            # Load JSON with retry logic unless the caller already validated it.
            if json_data is None:
                load_start = time.perf_counter()
                logger.info(f"[ENRICHER] Loading JSON: {json_path}")
                json_data = self.load_json(json_path)
                logger.info(
                    f"[ENRICHER] JSON loaded in {time.perf_counter() - load_start:.2f}s: {json_path.name}"
                )
            else:
                logger.info(f"[ENRICHER] Reusing pre-validated JSON data: {json_path.name}")
            
            # Enrich features
            enrich_start = time.perf_counter()
            logger.info(f"[ENRICHER] Feature analysis started: {json_path.name}")
            enriched_data = self.enrich_features(json_data)
            logger.info(
                f"[ENRICHER] Feature analysis completed in {time.perf_counter() - enrich_start:.2f}s: {json_path.name}"
            )
            
            # Determine output path
            date_str = json_path.parent.name  # e.g., "2025-12-19"
            output_dir = self.ensure_output_dir(date_str)
            output_path = output_dir / json_path.name
            
            # Save enriched JSON atomically
            save_start = time.perf_counter()
            logger.info(f"[ENRICHER] Saving enriched JSON to: {output_path}")
            self.save_json(enriched_data, output_path)
            logger.info(
                f"[ENRICHER] Enriched JSON saved in {time.perf_counter() - save_start:.2f}s: {output_path.name}"
            )
            
            # Get final file size
            output_size_mb = get_file_size_mb(str(output_path))
            logger.info(
                f"✅ Saved to: {output_path.name} ({output_size_mb:.2f} MB) | "
                f"total_enrichment_time={time.perf_counter() - start_time:.2f}s"
            )
            
            return output_path
            
        except IOError as e:
            # File I/O errors (includes our custom errors from load_json/save_json)
            logger.error(f"❌ File I/O error during enrichment: {e}")
            raise
        except KeyError as e:
            # Missing expected keys in JSON
            logger.error(f"❌ Invalid JSON structure - missing key: {e}")
            raise ValueError(f"Invalid JSON structure: missing key {e}")
        except Exception as e:
            # Unexpected errors
            logger.error(f"❌ Unexpected error during enrichment: {type(e).__name__}: {e}")
            raise
    
    def process_latest(self):
        """Process the most recent JSON file"""
        # Find latest date folder
        date_folders = sorted([d for d in self.json_dir.iterdir() if d.is_dir()], reverse=True)
        
        if not date_folders:
            logger.error("❌ No date folders found in outputs/json/")
            return None
        
        latest_date_folder = date_folders[0]
        logger.info(f"📅 Latest date folder: {latest_date_folder.name}")
        
        # Find JSON files in latest folder
        json_files = sorted(list(latest_date_folder.glob("*.json")), reverse=True)
        
        if not json_files:
            logger.error(f"❌ No JSON files found in {latest_date_folder}")
            return None
        
        latest_json = json_files[0]
        logger.info(f"📄 Latest JSON file: {latest_json.name}")
        
        # Process the file
        return self.process_file(latest_json)
    
    def process_specific(self, json_path):
        """Process a specific JSON file by path"""
        json_path = Path(json_path)
        
        if not json_path.exists():
            logger.error(f"❌ File not found: {json_path}")
            return None
        
        return self.process_file(json_path)


def main():
    """Main entry point"""
    enricher = FeatureEnricher()
    
    if len(sys.argv) > 1:
        # Process specific file
        json_path = sys.argv[1]
        logger.info(f"🎯 Processing specific file: {json_path}")
        result = enricher.process_specific(json_path)
    else:
        # Process latest file
        logger.info("🎯 Processing latest JSON file...")
        result = enricher.process_latest()
    
    if result:
        logger.info(f"\n🎉 SUCCESS! Enriched JSON saved to:\n   {result}\n")
    else:
        logger.error("\n❌ FAILED! No file was processed.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
