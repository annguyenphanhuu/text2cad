#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Mesh Analyzer Tool
Analyzes individual mesh files exported from FreeCAD scripts
Provides hole detection, feature analysis, and visualization
"""

import os
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional

def analyze_mesh_directory(mesh_dir: str, analysis_file: str = None) -> Dict:
    """
    Analyze all mesh files in a directory
    
    Args:
        mesh_dir: Directory containing mesh files
        analysis_file: Optional JSON file with face analysis data
        
    Returns:
        Dictionary with analysis results
    """
    mesh_dir = Path(mesh_dir)
    if not mesh_dir.exists():
        raise FileNotFoundError(f"Mesh directory not found: {mesh_dir}")
    
    # Load existing analysis if available
    analysis_data = {}
    if analysis_file and os.path.exists(analysis_file):
        with open(analysis_file, 'r', encoding='utf-8') as f:
            analysis_data = json.load(f)
    
    # Find all OBJ files
    obj_files = list(mesh_dir.glob("*.obj"))
    
    results = {
        "mesh_directory": str(mesh_dir),
        "total_meshes": len(obj_files),
        "meshes": {},
        "holes_detected": [],
        "large_faces": [],
        "small_faces": [],
        "summary": {}
    }
    
    print(f"Analyzing {len(obj_files)} mesh files in {mesh_dir}")
    print("=" * 60)
    
    for obj_file in sorted(obj_files):
        mesh_name = obj_file.stem
        file_size = obj_file.stat().st_size
        
        # Get analysis data if available
        face_data = None
        for component in ["base_flange", "bend_wall", "combined_bracket"]:
            if component in analysis_data:
                for face_name, face_info in analysis_data[component].items():
                    if face_name == mesh_name:
                        face_data = face_info
                        break
        
        mesh_info = {
            "file": str(obj_file),
            "file_size": file_size,
            "type": face_data.get("type", "unknown") if face_data else "unknown",
            "area": face_data.get("area", 0) if face_data else 0,
            "center": face_data.get("center", [0, 0, 0]) if face_data else [0, 0, 0],
            "is_hole": face_data.get("is_hole", False) if face_data else False,
            "surface_type": face_data.get("surface_type", "unknown") if face_data else "unknown"
        }
        
        results["meshes"][mesh_name] = mesh_info
        
        # Categorize meshes
        if mesh_info["is_hole"]:
            results["holes_detected"].append(mesh_name)
        elif mesh_info["area"] > 5000:
            results["large_faces"].append(mesh_name)
        elif mesh_info["area"] < 100:
            results["small_faces"].append(mesh_name)
        
        # Print analysis
        status = "🔴 HOLE" if mesh_info["is_hole"] else "🟢 FACE"
        print(f"{status} {mesh_name:20s} | {mesh_info['type']:12s} | Area: {mesh_info['area']:8.2f} | Size: {file_size:6d} bytes")
    
    # Generate summary
    results["summary"] = {
        "holes_count": len(results["holes_detected"]),
        "large_faces_count": len(results["large_faces"]),
        "small_faces_count": len(results["small_faces"]),
        "total_area": sum(mesh["area"] for mesh in results["meshes"].values()),
        "avg_area": sum(mesh["area"] for mesh in results["meshes"].values()) / len(results["meshes"]) if results["meshes"] else 0
    }
    
    print("=" * 60)
    print(f"Summary:")
    print(f"  Total meshes: {results['total_meshes']}")
    print(f"  Holes detected: {results['summary']['holes_count']}")
    print(f"  Large faces: {results['summary']['large_faces_count']}")
    print(f"  Small faces: {results['summary']['small_faces_count']}")
    print(f"  Total area: {results['summary']['total_area']:.2f}")
    print(f"  Average area: {results['summary']['avg_area']:.2f}")
    
    return results

def generate_html_report(analysis_results: Dict, output_file: str):
    """
    Generate HTML report for mesh analysis
    """
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mesh Analysis Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .summary-card {{ background: #f8f9fa; padding: 15px; border-radius: 6px; text-align: center; }}
        .summary-card h3 {{ margin: 0 0 10px 0; color: #495057; }}
        .summary-card .value {{ font-size: 24px; font-weight: bold; color: #007bff; }}
        .mesh-table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        .mesh-table th, .mesh-table td {{ padding: 10px; text-align: left; border-bottom: 1px solid #dee2e6; }}
        .mesh-table th {{ background-color: #f8f9fa; font-weight: bold; }}
        .hole {{ background-color: #f8d7da; }}
        .large-face {{ background-color: #d4edda; }}
        .small-face {{ background-color: #fff3cd; }}
        .type-badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
        .type-planar {{ background-color: #007bff; color: white; }}
        .type-cylindrical {{ background-color: #28a745; color: white; }}
        .type-hole {{ background-color: #dc3545; color: white; }}
        .type-unknown {{ background-color: #6c757d; color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 Mesh Analysis Report</h1>
            <p>Analysis of individual mesh files from CAD export</p>
            <p><strong>Directory:</strong> {analysis_results['mesh_directory']}</p>
        </div>
        
        <div class="summary">
            <div class="summary-card">
                <h3>Total Meshes</h3>
                <div class="value">{analysis_results['total_meshes']}</div>
            </div>
            <div class="summary-card">
                <h3>Holes Detected</h3>
                <div class="value">{analysis_results['summary']['holes_count']}</div>
            </div>
            <div class="summary-card">
                <h3>Large Faces</h3>
                <div class="value">{analysis_results['summary']['large_faces_count']}</div>
            </div>
            <div class="summary-card">
                <h3>Total Area</h3>
                <div class="value">{analysis_results['summary']['total_area']:.1f}</div>
            </div>
        </div>
        
        <h2>📋 Mesh Details</h2>
        <table class="mesh-table">
            <thead>
                <tr>
                    <th>Mesh Name</th>
                    <th>Type</th>
                    <th>Area</th>
                    <th>Center (X, Y, Z)</th>
                    <th>File Size</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
"""
    
    for mesh_name, mesh_info in analysis_results["meshes"].items():
        row_class = ""
        if mesh_info["is_hole"]:
            row_class = "hole"
        elif mesh_info["area"] > 5000:
            row_class = "large-face"
        elif mesh_info["area"] < 100:
            row_class = "small-face"
        
        type_class = f"type-{mesh_info['type'].replace('_', '-')}"
        center_str = f"({mesh_info['center'][0]:.1f}, {mesh_info['center'][1]:.1f}, {mesh_info['center'][2]:.1f})"
        status = "🔴 HOLE" if mesh_info["is_hole"] else "🟢 FACE"
        
        html_content += f"""
                <tr class="{row_class}">
                    <td><strong>{mesh_name}</strong></td>
                    <td><span class="type-badge {type_class}">{mesh_info['type']}</span></td>
                    <td>{mesh_info['area']:.2f}</td>
                    <td>{center_str}</td>
                    <td>{mesh_info['file_size']} bytes</td>
                    <td>{status}</td>
                </tr>
"""
    
    html_content += """
            </tbody>
        </table>
        
        <div style="margin-top: 30px; padding: 15px; background-color: #e9ecef; border-radius: 6px;">
            <h3>🔧 Usage Tips</h3>
            <ul>
                <li><strong>Holes:</strong> Small cylindrical faces that might represent holes or cutouts</li>
                <li><strong>Large Faces:</strong> Main surfaces of the part (area > 5000)</li>
                <li><strong>Small Faces:</strong> Detail features or edges (area < 100)</li>
                <li><strong>Individual Meshes:</strong> Each face exported as separate OBJ file for detailed analysis</li>
            </ul>
        </div>
    </div>
</body>
</html>
"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"HTML report generated: {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Analyze individual mesh files from CAD export")
    parser.add_argument("mesh_dir", help="Directory containing mesh files")
    parser.add_argument("--analysis", help="JSON file with face analysis data")
    parser.add_argument("--output", help="Output file for analysis results (JSON)")
    parser.add_argument("--html", help="Generate HTML report")
    
    args = parser.parse_args()
    
    try:
        # Analyze meshes
        results = analyze_mesh_directory(args.mesh_dir, args.analysis)
        
        # Save results to JSON
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"Analysis results saved to: {args.output}")
        
        # Generate HTML report
        if args.html:
            generate_html_report(results, args.html)
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
