#Example a circular tube Ø88.9x3 with a length of 900 mm, with one end cut at 45° and the other at 90°. It also requires two fish-mouth (saddle) openings: one cutting through the top surface only at 90° (vertical), positioned 300 mm from the 45° end; and one through-cut at 45° angle, positioned at the center of the length. Additionally, there is a simple D20 through-hole drilled vertically at 600 mm from the 45° end.
# Shape type: tube

"""
This script generates a general circular steel tube incorporating multiple typical machining operations:
- Material: Steel
- Outer Diameter: 88.9 mm
- Length: 900 mm
- Wall Thickness: 3 mm
- Features:
  1. Angled cuts: 45° at start (Y=0), 90° (straight) at end (Y=length)
  2. Fish-mouth opening (ONE SIDE ONLY, 90° vertical from top) at 300 mm from the 45° end
     → Accommodates a Ø50 mm intersecting tube, cuts top wall only (drills -Z from top)
  3. Fish-mouth opening (THROUGH, 45° angle in Y-Z plane) at center of tube length
     → Accommodates a Ø50 mm intersecting tube, penetrates both top and bottom walls
  4. Simple circular through-hole Ø20 mm drilled vertically (Z-axis) at 600 mm from start

Coordinate system:
  - Y-axis: length (longitudinal direction, tube extends from Y=0 to Y=length)
  - X-Z plane: positive circular cross-section
  - Final tube bounding box: X=0..outer_diameter, Z=0..outer_diameter, Y=0..length
  - Final tube centerline: (outer_diameter/2, Y, outer_diameter/2)
"""

# -*- coding: utf-8 -*-

# ============================================================================
# IMPORTS AND DEPENDENCIES
# ============================================================================
import os
import sys
import math
import FreeCAD as App
import Part
import Mesh
import Import
import MeshPart

# MANDATORY PATH SETUP - NEVER SKIP THIS
script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from FreeCadUtil import *

# ============================================================================
# 1. DOCUMENT INITIALIZATION
# ============================================================================
doc = App.newDocument("GeneratedModel")
sanitized_title = "Tube_Circulaire_Generique_88_9x3_L900_Cut45_Fishmouths_Hole_MixedRectSlots_AngledHoles"
doc.Label = sanitized_title

# ============================================================================
# 2. CREATE CIRCULAR TUBE
# ============================================================================
# Main tube dimensions
length         = 900.0    # Tube length (mm) — along Y-axis
outer_diameter = 88.9     # Outer diameter (mm)
thickness      = 3.0      # Wall thickness (mm)
inner_diameter = outer_diameter - 2 * thickness
outer_radius   = outer_diameter / 2.0
center_x       = outer_radius
center_z       = outer_radius

# Part.makeCircularTube(length, outer_diameter, thickness)
#
# Coordinate system:
#   - Y-axis: length (longitudinal direction, tube extends from Y=0 to Y=length)
#   - X-Z plane: positive circular cross-section after setup translation
#   - Final tube centerline: (outer_diameter/2, Y, outer_diameter/2)
#
# Construction method:
#   1. Create outer cylinder: makeCylinder(outer_radius, length) along Y-axis at (0,0,0)
#   2. Create inner cylinder: makeCylinder(inner_radius, length) along Y-axis at (0,0,0)
#      where inner_diameter = outer_diameter - 2*thickness, inner_radius = inner_diameter/2
#   3. Boolean cut: outer - inner → hollow tube
#
# Resulting shape:
#   - Hollow cylinder extending from Y=0 to Y=length
#   - Circular cross-section in X-Z plane with outer_diameter and wall thickness
tube_shape = Part.makeCircularTube(length, outer_diameter, thickness)

# ============================================================================
# 3. APPLY ANGLED CUTS
# ============================================================================
# Cut angles:
#   cut_angle_1_deg: angle at the START end (Y=0) — the 45° bevel end
#   cut_angle_2_deg: angle at the END   (Y=length) — straight 90° cut (value = 0.0)
#
# Convention: 0.0 = straight/90° cut (skipped), 45.0 = 45° bevel
cut_angle_1_deg = 45.0   # 45° bevel at start (Y=0)
cut_angle_2_deg = 0.0    # 90° straight cut at end (Y=length)

tube_shape = create_circular_tube_angled_cuts(
    tube_shape,
    length,
    outer_diameter,
    cut_angle_1_deg,   # start cut
    cut_angle_2_deg    # end cut
)

# POSITIVE COORDINATE CONVENTION FOR CIRCULAR TUBE
# The FreeCAD helper creates the circular tube centered at X=0, Z=0.
# After angled end cuts, translate it to positive coordinates:
#   X = 0..outer_diameter
#   Z = 0..outer_diameter
#   Y = 0..length
#   tube centerline = (center_x, Y, center_z)
#
# IMPORTANT:
#   Feature pnt/base coordinates should be positive from this point onward.
#   For circular tubes, cut direction is NOT chosen only by global positive axes.
#   It must point inward from the outside surface toward the tube centerline.
tube_shape.translate(App.Vector(center_x, 0.0, center_z))

# ============================================================================
# 4. FISH-MOUTH OPENING — ONE SIDE ONLY, 90° (VERTICAL FROM TOP)
# ============================================================================
# Cuts through TOP wall only (does NOT penetrate bottom wall).
# The intersecting cylinder is driven downward along -Z from the top of the tube.
#
# Parameters:
#   fish1_diameter   : diameter of the intersecting tube to accommodate
#   fish1_position_y : distance along tube axis from Y=0 (the 45° cut end)
#
# The cylinder base is placed above the tube top (Z > outer_diameter/2),
# and drilled downward (-Z) by outer_diameter/2 + thickness + extra → top wall only.
#
fish1_diameter    = 50.0                  # Ø50 mm intersecting tube
fish1_radius      = fish1_diameter / 2.0
fish1_position_y  = 300.0                 # 300 mm from the 45° end (Y=0)

# Cut depth = from top of tube down to center → penetrates top wall only
fish1_cut_depth   = outer_diameter / 2.0 + thickness + 2.0
fish1_base        = App.Vector(center_x, fish1_position_y, center_z + outer_radius + 2.0)
fish1_cylinder    = Part.makeCylinder(
    fish1_radius,
    fish1_cut_depth,
    fish1_base,
    App.Vector(0, 0, -1)   # direction: downward (−Z)
)
tube_shape = tube_shape.cut(fish1_cylinder)

# ============================================================================
# 5. FISH-MOUTH OPENING — THROUGH (BOTH SIDES), 45° ANGLE IN Y-Z PLANE
# ============================================================================
# Creates a through opening that penetrates both top and bottom walls.
# The intersecting cylinder is initially built along the Y-axis, then rotated
# around the X-axis to achieve the 45° angle in the Y-Z plane, and finally
# translated to the desired position along the tube.
#
# Parameters:
#   fish2_diameter   : diameter of the intersecting tube to accommodate
#   fish2_position_y : Y position (center of the opening along the main tube)
#   fish2_angle_deg  : angle of intersection in the Y-Z plane (degrees)
#                      0°  = parallel to Y-axis (no opening)
#                      90° = vertical (same as fish1 but through)
#                      45° = 45° diagonal — standard through-cut case
#
fish2_diameter   = 50.0                   # Ø50 mm intersecting tube
fish2_radius     = fish2_diameter / 2.0
fish2_position_y = length / 2.0           # At center of tube length (450 mm)
fish2_angle_deg  = 45.0                   # 45° in the Y-Z plane

# Penetration length: generous to ensure full cut through both walls at angle
fish2_pen_length = outer_diameter * 3.0

# Build cylinder along Y-axis, rotate around X-axis by fish2_angle_deg
fish2_cylinder = Part.makeCylinder(
    fish2_radius,
    fish2_pen_length,
    App.Vector(center_x, fish2_position_y - fish2_pen_length / 2.0, center_z),
    App.Vector(0, 1, 0)   # initial direction: along Y
)
fish2_cylinder.rotate(
    App.Vector(center_x, fish2_position_y, center_z),
    App.Vector(1, 0, 0),    # rotation axis: X
    -fish2_angle_deg
)
tube_shape = tube_shape.cut(fish2_cylinder)

# ============================================================================
# 6. SIMPLE CIRCULAR THROUGH-HOLE (DRILLED ALONG Z-AXIS)
# ============================================================================
# A full through-hole drilled from top to bottom of the tube.
# The drill starts above the tube (Z > outer_diameter/2) and goes downward (−Z),
# with enough depth to exit the bottom wall completely.
#
# Parameters:
#   hole_diameter  : drill diameter
#   hole_position_y: position along tube axis from Y=0 (the 45° end)
#   hole_position_x: lateral position (0 = center of the circular cross-section in X)
#
hole_diameter   = 20.0                    # Ø20 mm through-hole
hole_radius     = hole_diameter / 2.0
hole_position_y = 600.0                   # 600 mm from the 45° end (Y=0)
hole_position_x = center_x                # centered on X (tube centerline)

# NOTE: "outer_diameter + 4.0" is used here because this hole drills through BOTH walls of the tube.
# For standard single-wall drilling, always use "hole_depth = thickness + 2.0".
hole_depth = outer_diameter + 4.0          # enough to pass entirely through tube
hole_base  = App.Vector(hole_position_x, hole_position_y, center_z + outer_radius + 2.0)
hole_cyl   = Part.makeCylinder(
    hole_radius,
    hole_depth,
    hole_base,
    App.Vector(0, 0, -1)   # direction: downward (−Z)
)
tube_shape = tube_shape.cut(hole_cyl)

# ============================================================================
# 7. CIRCULAR TUBE CUT DIRECTIONS - CARDINAL DIRECTIONS ONLY
# ============================================================================
# IMPORTANT FOR CHATBOT / CODE GENERATION:
#   Do NOT create angled / diagonal / arbitrary-angle holes on circular tubes
#   in this example set. They are intentionally out of scope for now.
#
# Supported circular tube directions:
#   TOP    face only: start above Z-max and cut inward toward -Z
#   BOTTOM face only: start below Z-min and cut inward toward +Z
#   RIGHT  face only: start outside X-max and cut inward toward -X
#   LEFT   face only: start outside X-min and cut inward toward +X
#
# Placement rule:
#   If the user says "de part et d'autre" of a feature on a circular tube
#   surface and does NOT explicitly say "around the circumference", "angle",
#   or "radial pattern", place the added features along the tube length axis Y,
#   keeping the same supported face direction as the reference feature.
#
# Do NOT place those features at X offsets on the curved top surface. If X is
# offset from center_x on a circular tube, the cutter would need an angled radial
# direction, which this example intentionally does not support.

# ============================================================================
# 8. RECTANGULAR CUTOUTS (BOX CUTTERS: 3 VERTICAL + 2 HORIZONTAL)
# ============================================================================
# Same idea as the circular hole above:
#   - circular hole    -> cylinder cutter
#   - rectangular hole -> box cutter
#
# Part.makeBox grows in +X, +Y and +Z:
#   - vertical cuts use Z depth, with base_z = top_z - cut_depth
#   - horizontal cuts use X depth, with base_x = side_x - cut_depth

# --- Vertical rectangular cutout 1: TOP WALL ONLY, drilled downward along -Z ---
rect1_len_y    = 60.0                    # slot length along tube axis Y
rect1_width_x  = 25.0                    # slot width across tube X
rect1_depth    = thickness + 2.0         # one-wall cut only
rect1_center_y = 680.0                   # position along tube length
rect1_center_x = center_x                # centered across tube centerline
rect1_top_z    = center_z + outer_radius + 2.0

rect1_box = Part.makeBox(
    rect1_width_x,
    rect1_len_y,
    rect1_depth,
    App.Vector(
        rect1_center_x - rect1_width_x / 2.0,
        rect1_center_y - rect1_len_y / 2.0,
        rect1_top_z - rect1_depth
    )
)
tube_shape = tube_shape.cut(rect1_box)

# --- Vertical rectangular cutout 2: THROUGH BOTH WALLS, drilled downward along -Z ---
rect2_len_y    = 45.0                    # slot length along tube axis Y
rect2_width_x  = 18.0                    # slot width across tube X
rect2_depth    = outer_diameter          # through-cut volume, positive Z span 0..outer_diameter
rect2_center_y = 750.0                   # position along tube length
rect2_center_x = center_x                # centered across tube centerline
rect2_top_z    = outer_diameter

rect2_box = Part.makeBox(
    rect2_width_x,
    rect2_len_y,
    rect2_depth,
    App.Vector(
        rect2_center_x - rect2_width_x / 2.0,
        rect2_center_y - rect2_len_y / 2.0,
        rect2_top_z - rect2_depth
    )
)
tube_shape = tube_shape.cut(rect2_box)

# --- Vertical rectangular cutout 3: TOP WALL ONLY, drilled downward along -Z ---
rect3_len_y    = 35.0                    # slot length along tube axis Y
rect3_width_x  = 14.0                    # slot width across tube X
rect3_depth    = thickness + 2.0         # one-wall cut only
rect3_center_y = 820.0                   # position along tube length
rect3_center_x = center_x                # centered across tube centerline
rect3_top_z    = center_z + outer_radius + 2.0

rect3_box = Part.makeBox(
    rect3_width_x,
    rect3_len_y,
    rect3_depth,
    App.Vector(
        rect3_center_x - rect3_width_x / 2.0,
        rect3_center_y - rect3_len_y / 2.0,
        rect3_top_z - rect3_depth
    )
)
tube_shape = tube_shape.cut(rect3_box)

# --- Horizontal rectangular cutout 4: RIGHT WALL ONLY, drilled inward along -X ---
rect4_len_y     = 50.0                   # slot length along tube axis Y
rect4_height_z  = 18.0                   # slot height along Z
rect4_depth     = thickness + 2.0        # one-wall cut only
rect4_center_y  = 160.0                  # position along tube length
rect4_center_z  = center_z               # centered vertically on circular section
rect4_side_x    = center_x + outer_radius + 2.0

rect4_box = Part.makeBox(
    rect4_depth,
    rect4_len_y,
    rect4_height_z,
    App.Vector(
        rect4_side_x - rect4_depth,
        rect4_center_y - rect4_len_y / 2.0,
        rect4_center_z - rect4_height_z / 2.0
    )
)
tube_shape = tube_shape.cut(rect4_box)

# --- Horizontal rectangular cutout 5: THROUGH CUT across X, positive X span ---
rect5_len_y     = 40.0                   # slot length along tube axis Y
rect5_height_z  = 15.0                   # slot height along Z
rect5_depth     = outer_diameter         # through-cut volume, positive X span 0..outer_diameter
rect5_center_y  = 865.0                  # position along tube length
rect5_center_z  = center_z               # centered vertically on circular section
rect5_side_x    = outer_diameter

rect5_box = Part.makeBox(
    rect5_depth,
    rect5_len_y,
    rect5_height_z,
    App.Vector(
        rect5_side_x - rect5_depth,
        rect5_center_y - rect5_len_y / 2.0,
        rect5_center_z - rect5_height_z / 2.0
    )
)
tube_shape = tube_shape.cut(rect5_box)

# ============================================================================
# 9. FINALIZE OBJECT IN DOCUMENT
# ============================================================================
tube_obj = doc.addObject("Part::Feature", "circular_tube_generique")
tube_obj.Shape = tube_shape
doc.recompute()

# ============================================================================
# 10. EXPORT STEP AND OBJ
# ============================================================================
output_dir_abs = f"/app/storage/{sanitized_title}/output"
os.makedirs(output_dir_abs, exist_ok=True)

# STEP export
step_path = os.path.join(output_dir_abs, f"{sanitized_title}.step")
Import.export([tube_obj], step_path)

# ============================================================
# GEOMETRY ANALYSIS
# ============================================================
from FreeCadUtil.GeometryAnalyzer import analyze_geometry, export_geometry_to_json

# Analyze final shape geometry
geometry_result = analyze_geometry(tube_shape, material="{mapped_material}")

# Export geometry data to JSON
geometry_json = os.path.join(output_dir_abs, f"{sanitized_title}_geometry.json")
export_geometry_to_json(
    tube_shape,
    geometry_json,
    material="{mapped_material}",
    additional_info={
        "part_name": sanitized_title,
    }
)

# OBJ export with separate meshes for each face
obj_path = os.path.join(output_dir_abs, f"{sanitized_title}.obj")

mesh_objects = []
face_counter = 1

for part in [tube_obj]:
    shape = part.Shape
    part_name = part.Label

    for i, face in enumerate(shape.Faces):
        face_mesh = MeshPart.meshFromShape(
            Shape=face,
            LinearDeflection=0.1,
            AngularDeflection=0.523599
        )
        mesh_obj = doc.addObject("Mesh::Feature", f"Face_{face_counter:02d}")
        mesh_obj.Mesh = face_mesh
        mesh_obj.Label = f"{part_name}_Face_{i+1:02d}"
        mesh_objects.append(mesh_obj)
        face_counter += 1

doc.recompute()
Mesh.export(mesh_objects, obj_path)
