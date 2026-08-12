#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for individual mesh export functionality
Creates a simple part with holes and exports each face as separate mesh
"""

import os
import sys
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def create_test_part_with_holes():
    """
    Create a test part with holes to demonstrate mesh export functionality
    """
    try:
        import FreeCAD as App
        import Part
        import MeshPart
        import Import
    except ImportError:
        print("Error: FreeCAD not available")
        return False
    
    print("Creating test part with holes...")
    
    # Create new document
    doc = App.newDocument("TestPartWithHoles")
    
    # Create base plate (100x50x5 mm)
    base_shape = Part.makeBox(100, 50, 5)
    base_obj = doc.addObject("Part::Feature", "BasePlate")
    base_obj.Shape = base_shape
    base_obj.Label = "Base Plate"
    
    # Create holes
    holes = []
    
    # Large hole (diameter 10mm) at (25, 25)
    hole1 = Part.makeCylinder(5, 5, App.Vector(25, 25, 0))
    holes.append(hole1)
    
    # Small hole (diameter 6mm) at (75, 25)
    hole2 = Part.makeCylinder(3, 5, App.Vector(75, 25, 0))
    holes.append(hole2)
    
    # Rectangular slot (20x5mm) at (50, 15)
    slot = Part.makeBox(20, 5, 5, App.Vector(40, 12.5, 0))
    holes.append(slot)
    
    # Cut holes from base
    current_shape = base_shape
    for hole in holes:
        current_shape = current_shape.cut(hole)
    
    # Create final part
    final_obj = doc.addObject("Part::Feature", "PartWithHoles")
    final_obj.Shape = current_shape
    final_obj.Label = "Part with Holes"
    
    doc.recompute()
    
    # Export with individual mesh analysis
    output_dir = project_root / "outputs" / "test_meshes"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    mesh_dir = output_dir / "individual_meshes"
    mesh_dir.mkdir(exist_ok=True)
    
    print(f"Exporting to: {output_dir}")
    
    # Export STEP file
    step_path = output_dir / "test_part_with_holes.step"
    Import.export([final_obj], str(step_path))
    print(f"STEP exported: {step_path}")
    
    # Analyze and export individual faces
    face_info = analyze_and_export_faces(final_obj.Shape, "test_part", str(mesh_dir))
    
    # Create analysis report
    analysis_results = {
        "model_info": {
            "title": "Test Part with Holes",
            "description": "Demo part showing hole detection capabilities",
            "dimensions": "100x50x5 mm",
            "holes": [
                {"type": "circular", "diameter": 10, "position": [25, 25]},
                {"type": "circular", "diameter": 6, "position": [75, 25]},
                {"type": "rectangular", "size": [20, 5], "position": [50, 15]}
            ]
        },
        "face_analysis": face_info,
        "summary": {
            "total_faces": len(face_info),
            "holes_detected": sum(1 for info in face_info.values() if info.get("is_hole", False)),
            "planar_faces": sum(1 for info in face_info.values() if info.get("type") == "planar"),
            "cylindrical_faces": sum(1 for info in face_info.values() if info.get("type") == "cylindrical")
        }
    }
    
    # Save analysis
    analysis_path = output_dir / "test_analysis.json"
    with open(analysis_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, indent=2, ensure_ascii=False)
    
    print(f"Analysis saved: {analysis_path}")
    
    # Export combined OBJ
    combined_mesh = MeshPart.meshFromShape(
        Shape=final_obj.Shape,
        LinearDeflection=0.01,
        AngularDeflection=0.05,
        Relative=False
    )
    
    obj_path = output_dir / "test_part_with_holes.obj"
    combined_mesh.write(str(obj_path))
    print(f"Combined OBJ exported: {obj_path}")
    
    print("\n=== Test Results ===")
    print(f"Total faces: {analysis_results['summary']['total_faces']}")
    print(f"Holes detected: {analysis_results['summary']['holes_detected']}")
    print(f"Planar faces: {analysis_results['summary']['planar_faces']}")
    print(f"Cylindrical faces: {analysis_results['summary']['cylindrical_faces']}")
    
    return True

def analyze_and_export_faces(shape, base_name, output_dir):
    """
    Analyze shape faces and export each as separate mesh
    """
    try:
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.TopoDS import topods
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
        from OCC.Core.GProp import GProp_GProps
        from OCC.Core.BRepGProp import brepgprop_SurfaceProperties
        occ_available = True
    except ImportError:
        print("Warning: OpenCascade not available, using basic face extraction")
        occ_available = False
    
    import MeshPart
    
    face_info = {}
    face_count = 0
    
    if occ_available:
        # Use OpenCascade for detailed analysis
        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
        
        while face_explorer.More():
            face = topods.Face(face_explorer.Current())
            face_name = f"{base_name}_face_{face_count:02d}"
            
            # Analyze face geometry
            surface_adaptor = BRepAdaptor_Surface(face)
            surface_type = surface_adaptor.GetType()
            
            # Calculate face properties
            props = GProp_GProps()
            brepgprop_SurfaceProperties(face, props)
            area = props.Mass()
            center = props.CentreOfMass()
            
            # Determine face type
            face_type = "unknown"
            is_hole = False
            
            if surface_type == GeomAbs_Plane:
                face_type = "planar"
            elif surface_type == GeomAbs_Cylinder:
                face_type = "cylindrical"
                # Check if it's a hole (small cylindrical face)
                if area < 200:  # Threshold for hole detection
                    is_hole = True
                    face_type = "hole"
            
            # Store face information
            face_info[face_name] = {
                "type": face_type,
                "area": area,
                "center": [center.X(), center.Y(), center.Z()],
                "is_hole": is_hole,
                "surface_type": str(surface_type)
            }
            
            # Create individual mesh for this face
            try:
                mesh = MeshPart.meshFromShape(
                    Shape=face,
                    LinearDeflection=0.01,
                    AngularDeflection=0.1,
                    Relative=False
                )
                
                face_mesh_path = os.path.join(output_dir, f"{face_name}.obj")
                mesh.write(face_mesh_path)
                
                face_info[face_name]["mesh_file"] = face_mesh_path
                print(f"  Face {face_count:2d}: {face_type:12s} | Area: {area:8.2f} | File: {face_name}.obj")
                
            except Exception as e:
                print(f"  Warning: Could not mesh face {face_count}: {e}")
                face_info[face_name]["mesh_file"] = None
            
            face_explorer.Next()
            face_count += 1
    else:
        # Fallback: Use FreeCAD's basic face extraction
        faces = shape.Faces
        for i, face in enumerate(faces):
            face_name = f"{base_name}_face_{i:02d}"
            
            # Basic analysis without OpenCascade
            area = face.Area
            center = face.CenterOfMass
            
            # Simple hole detection based on area
            is_hole = area < 200  # Threshold for hole detection
            
            face_info[face_name] = {
                "type": "planar" if area > 200 else "possible_hole",
                "area": area,
                "center": [center.x, center.y, center.z],
                "is_hole": is_hole,
                "surface_type": "unknown"
            }
            
            try:
                mesh = MeshPart.meshFromShape(
                    Shape=face,
                    LinearDeflection=0.01,
                    AngularDeflection=0.1,
                    Relative=False
                )
                
                face_mesh_path = os.path.join(output_dir, f"{face_name}.obj")
                mesh.write(face_mesh_path)
                
                face_info[face_name]["mesh_file"] = face_mesh_path
                print(f"  Face {i:2d}: Area: {area:8.2f} | File: {face_name}.obj")
                
            except Exception as e:
                print(f"  Warning: Could not mesh face {i}: {e}")
                face_info[face_name]["mesh_file"] = None
    
    return face_info

def main():
    print("=== Testing Individual Mesh Export ===")
    
    if create_test_part_with_holes():
        print("\n✅ Test completed successfully!")
        print("\nNext steps:")
        print("1. Check outputs/test_meshes/ for individual mesh files")
        print("2. Run: python tools/mesh_analyzer.py outputs/test_meshes/individual_meshes --analysis outputs/test_meshes/test_analysis.json --html outputs/test_meshes/report.html")
        print("3. Open report.html in browser to view analysis")
    else:
        print("\n❌ Test failed!")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
