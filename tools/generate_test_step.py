#!/usr/bin/env python3
"""
Generate test STEP files with circular features for testing OpenCascade.js circle detection
"""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import FreeCAD
    import Part
    import Draft
except ImportError:
    print("FreeCAD not found. Please install FreeCAD or run this script in FreeCAD environment.")
    sys.exit(1)

def create_simple_plate_with_holes():
    """Create a simple plate with circular holes for testing"""
    
    # Create new document
    doc = FreeCAD.newDocument("TestPlate")
    
    # Create base plate (100x80x5mm)
    base_plate = Part.makeBox(100, 80, 5)
    base_obj = doc.addObject("Part::Feature", "BasePlate")
    base_obj.Shape = base_plate
    
    # Create circular holes
    holes = []
    
    # Large hole (radius 15mm) at center
    hole1 = Part.makeCylinder(15, 10, FreeCAD.Vector(50, 40, -2.5))
    holes.append(hole1)
    
    # Medium holes (radius 8mm) 
    hole2 = Part.makeCylinder(8, 10, FreeCAD.Vector(25, 20, -2.5))
    hole3 = Part.makeCylinder(8, 10, FreeCAD.Vector(75, 20, -2.5))
    hole4 = Part.makeCylinder(8, 10, FreeCAD.Vector(25, 60, -2.5))
    hole5 = Part.makeCylinder(8, 10, FreeCAD.Vector(75, 60, -2.5))
    holes.extend([hole2, hole3, hole4, hole5])
    
    # Small holes (radius 3mm)
    hole6 = Part.makeCylinder(3, 10, FreeCAD.Vector(15, 15, -2.5))
    hole7 = Part.makeCylinder(3, 10, FreeCAD.Vector(85, 15, -2.5))
    hole8 = Part.makeCylinder(3, 10, FreeCAD.Vector(15, 65, -2.5))
    hole9 = Part.makeCylinder(3, 10, FreeCAD.Vector(85, 65, -2.5))
    holes.extend([hole6, hole7, hole8, hole9])
    
    # Cut holes from base plate
    result_shape = base_plate
    for hole in holes:
        result_shape = result_shape.cut(hole)
    
    # Create final object
    final_obj = doc.addObject("Part::Feature", "PlateWithHoles")
    final_obj.Shape = result_shape
    
    # Export to STEP
    output_path = os.path.join(os.path.dirname(__file__), "..", "static", "test-files", "test_plate_with_holes.step")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Export using Part module
    Part.export([final_obj], output_path)
    
    print(f"Test STEP file created: {output_path}")
    print("Features:")
    print("- Base plate: 100x80x5mm")
    print("- 1 large hole: radius 15mm")
    print("- 4 medium holes: radius 8mm each")
    print("- 4 small holes: radius 3mm each")
    print("Total: 9 circular features to detect")
    
    # Close document
    FreeCAD.closeDocument(doc.Name)
    
    return output_path

def create_cylinder_with_features():
    """Create a cylinder with various circular features"""
    
    doc = FreeCAD.newDocument("TestCylinder")
    
    # Main cylinder (radius 30mm, height 50mm)
    main_cylinder = Part.makeCylinder(30, 50)
    main_obj = doc.addObject("Part::Feature", "MainCylinder")
    main_obj.Shape = main_cylinder
    
    # Create circular groove (radius 25mm, depth 5mm)
    groove = Part.makeCylinder(25, 5, FreeCAD.Vector(0, 0, 20))
    inner_groove = Part.makeCylinder(20, 5, FreeCAD.Vector(0, 0, 20))
    groove_ring = groove.cut(inner_groove)
    
    # Create central hole (radius 10mm)
    central_hole = Part.makeCylinder(10, 60, FreeCAD.Vector(0, 0, -5))
    
    # Create side holes (radius 5mm)
    side_holes = []
    for angle in [0, 60, 120, 180, 240, 300]:  # 6 holes around circumference
        x = 22 * FreeCAD.Units.Quantity(f"cos({angle}°)").Value
        y = 22 * FreeCAD.Units.Quantity(f"sin({angle}°)").Value
        hole = Part.makeCylinder(5, 40, FreeCAD.Vector(x, y, 5))
        side_holes.append(hole)
    
    # Combine operations
    result_shape = main_cylinder
    result_shape = result_shape.cut(central_hole)
    for hole in side_holes:
        result_shape = result_shape.cut(hole)
    
    # Create final object
    final_obj = doc.addObject("Part::Feature", "CylinderWithFeatures")
    final_obj.Shape = result_shape
    
    # Export to STEP
    output_path = os.path.join(os.path.dirname(__file__), "..", "static", "test-files", "test_cylinder_with_features.step")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    Part.export([final_obj], output_path)
    
    print(f"Test STEP file created: {output_path}")
    print("Features:")
    print("- Main cylinder: radius 30mm, height 50mm")
    print("- Central hole: radius 10mm")
    print("- 6 side holes: radius 5mm each")
    print("- Circular edges from cylinder surfaces")
    print("Total: Multiple circular features to detect")
    
    FreeCAD.closeDocument(doc.Name)
    
    return output_path

def create_simple_circle_test():
    """Create a very simple test with just circles"""
    
    doc = FreeCAD.newDocument("SimpleCircleTest")
    
    # Create a simple disk
    disk = Part.makeCylinder(20, 2)  # radius 20mm, thickness 2mm
    disk_obj = doc.addObject("Part::Feature", "Disk")
    disk_obj.Shape = disk
    
    # Create a ring
    outer_ring = Part.makeCylinder(15, 3, FreeCAD.Vector(50, 0, 0))
    inner_ring = Part.makeCylinder(10, 3, FreeCAD.Vector(50, 0, 0))
    ring = outer_ring.cut(inner_ring)
    ring_obj = doc.addObject("Part::Feature", "Ring")
    ring_obj.Shape = ring
    
    # Export to STEP
    output_path = os.path.join(os.path.dirname(__file__), "..", "static", "test-files", "simple_circles.step")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    Part.export([disk_obj, ring_obj], output_path)
    
    print(f"Simple test STEP file created: {output_path}")
    print("Features:")
    print("- Disk: radius 20mm")
    print("- Ring: outer radius 15mm, inner radius 10mm")
    print("Total: Multiple circular edges to detect")
    
    FreeCAD.closeDocument(doc.Name)
    
    return output_path

if __name__ == "__main__":
    print("Generating test STEP files for OpenCascade.js circle detection...")
    
    try:
        # Create test files
        create_simple_circle_test()
        create_simple_plate_with_holes()
        create_cylinder_with_features()
        
        print("\nAll test files generated successfully!")
        print("You can now test them with the OpenCascade.js STEP viewer.")
        
    except Exception as e:
        print(f"Error generating test files: {e}")
        import traceback
        traceback.print_exc()
