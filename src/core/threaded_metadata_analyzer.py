#!/usr/bin/env python3
"""
Threaded Holes & Oblongs Metadata Analyzer
Analyzes FreeCAD Python code to detect manufacturing features and generate metadata.

Detects:
- Threaded holes (M3, M4, M5, etc.)
- Oblongs (slotted holes created with Part.makeOblong)

Uses GPT-4o-mini for intelligent analysis of code comments and patterns.
"""

import os
import json
import re
import logging
import math
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv

try:
    from langchain_openai import ChatOpenAI
    from langchain.schema import HumanMessage, SystemMessage
    from langchain_community.callbacks import get_openai_callback
except ImportError:
    ChatOpenAI = None
    HumanMessage = None
    SystemMessage = None
    get_openai_callback = None

load_dotenv()
logger = logging.getLogger(__name__)


class FeatureMetadataAnalyzer:
    """
    Analyzes FreeCAD code to detect manufacturing features and generate metadata.
    
    Detects:
    - Threaded holes (tapped holes for screws)
    - Oblongs (slotted holes, filleted boxes)
    
    Uses a small LLM (GPT-4o-mini) to intelligently parse code comments,
    variable names, and patterns to identify features with their specifications.
    """
    
    
    
    def __init__(self, llm_model: str = "gpt-5-mini", cost_tracker=None):
        """
        Initialize the analyzer with LLM for intelligent code analysis.
        
        Args:
            llm_model: Model to use for code analysis (default: gpt-4o-mini)
            cost_tracker: Optional CostTracker instance to track OpenAI costs
        """
        self.llm_model = llm_model
        self.llm = None
        self.cost_tracker = cost_tracker
        
        # Initialize LLM if available
        if ChatOpenAI:
            try:
                self.llm = ChatOpenAI(
                    model=llm_model,
                    # temperature removed - gpt-5-mini only supports default (1)
                    api_key=os.getenv("OPENAI_API_KEY")
                )
                logger.debug(f"[FeatureAnalyzer] Initialized with model: {llm_model}")
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to initialize LLM: {e}")
                self.llm = None
        else:
            logger.warning("[FeatureAnalyzer] LangChain not available, using regex-only analysis")
    
    async def analyze_code(self, code_content: str, shape_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze FreeCAD code to detect manufacturing features.

        Note: callers must not invoke this for Perforated Sheet — that shape's metadata
        analysis is skipped entirely one level up (`file_manager.save_threaded_metadata_file_async`),
        since the LLM pass scales with hole count (confirmed to cause request timeouts on
        dense sheets) and the regex fallback cannot substitute for it either (the mandatory
        codegen path for that shape has no pattern the regex analyzers key off). `shape_type`
        is accepted here only for logging/downstream consumers, not to gate the LLM call.

        Args:
            code_content: Python code content to analyze
            shape_type: Shape type of the generated part, if known (informational only).

        Returns:
            Dictionary with metadata about features:
            {
                "threaded_holes": [...],
                "oblongs": [...],
                "bending_features": [...],
                "analysis_method": "llm" | "regex",
                "metadata": {...}
            }
        """
        logger.debug("[FeatureAnalyzer] Starting code analysis...")
        
        from datetime import datetime
        
        result = {
            "metadata_version": "2.0.0",
            "schema_version": "2024.1",
            "units": "mm",
            "coordinate_system": "right_handed_xyz",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "generator": {
                "name": "ThreadedMetadataAnalyzer",
                "version": "2.0.0"
            },
            "shape_type": None,  # 🔥 NEW: Shape type for ViewCube center calculation
            "threaded_holes": [],
            "oblongs": [],
            "bending_features": [],
            "countersinks": [],
            "box_holes": [],
            "analysis_method": "regex"
        }
        
        # Try LLM analysis first (more accurate for parameters)
        llm_oblongs = []
        llm_holes = []
        llm_bending = []
        llm_countersinks = []
        llm_box_holes = []
        llm_shape_type = None  # 🔥 NEW: Shape type from LLM
        
        llm_skipped_reason = None

        if self.llm:
            try:
                llm_result = await asyncio.wait_for(self._analyze_with_llm(code_content), timeout=120)
                llm_oblongs = llm_result.get('oblongs', [])
                llm_holes = llm_result.get('threaded_holes', [])
                llm_bending = llm_result.get('bending_features', [])
                llm_countersinks = llm_result.get('countersinks', [])
                llm_box_holes = llm_result.get('box_holes', [])
                llm_shape_type = llm_result.get('shape_type', None)  # 🔥 NEW: Extract shape type
                logger.debug(f"[FeatureAnalyzer] LLM analysis: holes={len(llm_holes)}, oblongs={len(llm_oblongs)}, bending={len(llm_bending)}, cs={len(llm_countersinks)}, box={len(llm_box_holes)}, shape={llm_shape_type}")
            except asyncio.TimeoutError:
                llm_skipped_reason = "timeout"
                logger.warning("[FeatureAnalyzer] LLM analysis timed out after 120s, using regex only")
            except Exception as e:
                llm_skipped_reason = "error"
                logger.warning(f"[FeatureAnalyzer] LLM analysis failed: {e}, will use regex only")
        
        # ✅ ALWAYS run regex analysis as backup (catches missed features)
        regex_oblongs = self._analyze_oblongs_with_regex(code_content)
        regex_holes = self._analyze_threaded_with_regex(code_content)
        regex_bending = self._analyze_bending_with_regex(code_content)
        regex_countersinks = self._analyze_countersinks_with_regex(code_content)
        regex_box_holes = self._analyze_box_holes_with_regex(code_content)
        logger.debug(f"[FeatureAnalyzer] Regex analysis: holes={len(regex_holes)}, oblongs={len(regex_oblongs)}, bending={len(regex_bending)}, cs={len(regex_countersinks)}, box={len(regex_box_holes)}")
        
        # 🎯 PHASE 1: Enrich regex oblongs with enhanced metadata
        for oblong in regex_oblongs:
            self._enrich_oblong(oblong, code_content)
        
        # ✅ MERGE LLM + Regex results (deduplicate by position)
        result['oblongs'] = self._merge_oblongs(llm_oblongs, regex_oblongs)
        result['threaded_holes'] = self._merge_holes(llm_holes, regex_holes)
        result['bending_features'] = llm_bending if llm_bending else regex_bending  # Bending: LLM takes priority, fallback to regex
        result['countersinks'] = llm_countersinks if llm_countersinks else regex_countersinks  # Countersinks: LLM takes priority, fallback to regex
        result['box_holes'] = llm_box_holes if llm_box_holes else regex_box_holes
        
        # 🔥 NEW: Merge shape_type (LLM priority, fallback to regex)
        result['shape_type'] = llm_shape_type if llm_shape_type else self._analyze_shape_type_with_regex(code_content)
        if result['shape_type']:
            logger.debug(f"[FeatureAnalyzer] shape_type={result['shape_type']}")
        
        if llm_skipped_reason == "timeout":
            result["analysis_method"] = "regex_timeout_fallback"
        else:
            result["analysis_method"] = "llm+regex" if self.llm else "regex"
        
        # ✅ VALIDATION: Check if LLM missed any features (skip if LLM wasn't actually run)
        if self.llm and not llm_skipped_reason and (llm_oblongs or llm_holes or llm_bending or regex_oblongs or regex_holes or regex_bending):
            oblongs_missed = len(result['oblongs']) - len(llm_oblongs)
            holes_missed = len(result['threaded_holes']) - len(llm_holes)
            bending_missed = len(result['bending_features']) - len(llm_bending)
            
            if oblongs_missed > 0:
                logger.warning(f"[VALIDATION] ⚠️  LLM missed {oblongs_missed} oblong(s)! Regex found {len(regex_oblongs)} total, LLM found {len(llm_oblongs)}")
                logger.warning(f"[VALIDATION] → Merged result has {len(result['oblongs'])} oblongs (LLM + Regex backup)")
            
            if holes_missed > 0:
                logger.warning(f"[VALIDATION] ⚠️  LLM missed {holes_missed} threaded hole(s)! Regex found {len(regex_holes)} total, LLM found {len(llm_holes)}")
                logger.warning(f"[VALIDATION] → Merged result has {len(result['threaded_holes'])} holes (LLM + Regex backup)")
            
            if bending_missed > 0:
                logger.warning(f"[VALIDATION] ⚠️  LLM missed {bending_missed} bending feature(s)! Regex found {len(regex_bending)} total, LLM found {len(llm_bending)}")
                logger.warning(f"[VALIDATION] → Using regex bending features")
            
            if oblongs_missed == 0 and holes_missed == 0 and bending_missed == 0 and (llm_oblongs or llm_holes or llm_bending):
                logger.debug(f"[VALIDATION] ✅ LLM detected all features correctly")
        
        logger.info(f"[FeatureAnalyzer] Final merged results: {len(result['threaded_holes'])} threaded holes, {len(result['oblongs'])} oblongs, {len(result['bending_features'])} bending features, {len(result['countersinks'])} countersinks, {len(result['box_holes'])} box holes")
        return result
    
    async def _analyze_with_llm(self, code_content: str) -> Dict[str, Any]:
        """
        Use LLM to intelligently analyze code for all features.
        """
        system_prompt = """You are an expert FreeCAD code analyzer specializing in detecting manufacturing features.

Your task is to analyze Python code and extract information about:
1. Threaded holes (tapped holes for screws)
2. Oblongs (slotted holes created with Part.makeOblong)
3. Bending features (L-shape, U-shape, Z-shape sheet metal bends)
4. Square/rectangular holes (box cutters created with Part.makeBox and cut from a base shape)

=== THREADED HOLES ===

CRITICAL INDICATORS (in priority order):
1. **hole_thread_size = "M..."** variable (HIGHEST PRIORITY - 95% confidence)
   Example: hole_thread_size = "M6" or hole_thread_size = "M8"
   
2. **tap_hole_diam** variable (HIGH PRIORITY - 85% confidence)
   Example: tap_hole_diam = 8.0
   
3. **Comments with thread mentions** (HIGH PRIORITY - 85% confidence)
   Examples: "# Threaded M4 holes", "# 4x M6 in corners", "# taraudé M8"
   
4. **Diameter matching + thread context** (MEDIUM - 70% confidence)
   Example: hole_diam = 6.0 AND comment mentions "M6" or "threaded"

AVOID FALSE POSITIVES:
- Regular cylindrical holes use: hole_diam, hole_diameter (without thread context)
- Non-threaded holes often noted as: "Ø5.2 holes", "D12 holes"
- If ONLY diameter is present without thread indicators → NOT threaded

=== OBLONGS ===

CRITICAL INDICATORS:
1. **Part.makeOblong() calls** (HIGHEST PRIORITY - 95% confidence)
   Pattern: Part.makeOblong(length, width, height, App.Vector(x, y, z), App.Vector(dx, dy, dz))
   
2. **Oblong/slot variable names** (HIGH PRIORITY - 85% confidence)
   Examples: slot_tool, oblong_tool, slot_total_len, slot_width
   
3. **Comments mentioning oblongs/slots** (HIGH PRIORITY - 85% confidence)
   Examples: "# Oblong slot", "# Slotted hole", "# lumière oblongue"

🔥 CRITICAL: LOOP DETECTION (VERY IMPORTANT!)
**MULTIPLE OBLONGS IN LOOPS** - MUST detect ALL iterations!

Patterns to recognize (code varies, understand INTENT):
1. **for-loop with range**:
   ```python
   for i in range(4):
       oblong = Part.makeOblong(...)
   ```
   → Creates 4 SEPARATE oblongs, return 4 entries!

2. **for-loop with list/variable**:
   ```python
   x_centers = [40, 110, 180, 250]  # or list comprehension
   for cx in x_centers:
       oblong = Part.makeOblong(...)
   ```
   → Count items in list → return N separate oblong entries!

3. **Explicit multiple calls** (no loop):
   ```python
   oblong1 = Part.makeOblong(...)
   oblong2 = Part.makeOblong(...)
   ```
   → Return N separate entries!

**HOW TO HANDLE LOOPS**:
- Count loop iterations (range(N), list length, etc.)
- For EACH iteration, return a SEPARATE oblong entry
- Calculate position for each iteration:
  * If `cx = base + i * spacing`: positions are [base, base+spacing, base+2*spacing, ...]
  * If explicit list: use actual values
- Each oblong gets same dimensions but DIFFERENT position

**EXAMPLE**:
Code:
```python
hole_count = 4
hole_spacing = 70.0
margin_x = 40.0
for i in range(hole_count):
    cx = margin_x + i * hole_spacing
    oblong = Part.makeOblong(10, 30, 5, App.Vector(cx, 20, -1), ...)
```

MUST return 4 oblongs:
```json
[
  {"corner": {"x": 35, "y": 5}, "width": 10, "straight_length": 20},
  {"corner": {"x": 105, "y": 5}, "width": 10, "straight_length": 20},  
  {"corner": {"x": 175, "y": 5}, "width": 10, "straight_length": 20},
  {"corner": {"x": 245, "y": 5}, "width": 10, "straight_length": 20}
]
```

❌ WRONG: Return only 1 oblong
✅ CORRECT: Return 4 separate oblongs with calculated positions

OBLONG PARAMETERS TO EXTRACT:
🔥 CRITICAL: Calculate corner position from center and dimensions!

**Corner Position Calculation**:
- If code has: `slot_corner_x = center_x - slot_total_length / 2.0`
  → Trace center_x value (e.g., center_x = plate_length / 2.0 = 25.0)
  → Calculate: slot_corner_x = 25.0 - 15.0/2.0 = 17.5
- If code has: `slot_corner_y = center_y - slot_width / 2.0`
  → Trace center_y value (e.g., center_y = plate_width / 2.0 = 25.0)
  → Calculate: slot_corner_y = 25.0 - 5.0/2.0 = 22.5
- **DO NOT** use intermediate variable names as values!
  ❌ WRONG: corner.x = "slot_corner_x" (this is a variable name, not a value!)
  ✅ CORRECT: corner.x = 17.5 (calculated from center_x - total_length/2)

**Other Parameters**:
- straight_length: Length of straight section (NOT total length!)
  * In code: slot_length, slot_straight_len, oblong_straight_len
  * This is the length EXCLUDING the rounded ends
- width: Width/diameter of rounded ends
  * In code: slot_width, oblong_width
- depth: Extrusion depth (NOT "height"!)
  * In code: slot_depth, oblong_depth, or calculated from sheet_thickness
- direction: App.Vector(dx, dy, dz) - extrusion direction vector

CALCULATION NOTES:
- If code has "slot_total_length = slot_length + slot_width":
  → Extract straight_length (NOT total_length)
- total_length will be calculated automatically: total_length = straight_length + width
- fillet_radius will be calculated: fillet_radius = width / 2

=== BENDING FEATURES (NEW) ===

CRITICAL INDICATORS (in priority order):
1. **Part.makeLShape() / makeUShape() / makeZShape() or makeCircularLShape() / makeCircularUShape() / makeCircularZShape() calls** (HIGHEST PRIORITY - 95% confidence)
   Pattern: Part.makeLShape(dim_x, dim_y, thickness, flange_height, bend_angle_deg, bend_radius)
   Pattern: Part.makeCircularLShape(diameter, thickness, offset_x, bend_angle_deg, bend_radius)
   
2. **SheetMetal workbench usage** (HIGH PRIORITY - 90% confidence)
   Pattern: SheetMetalCmd.SMBendWall(wall_obj, base_obj, [edge])
   Properties: wall_obj.radius = bend_radius, wall_obj.angle = bend_angle
   
3. **Comments mentioning bending** (HIGH PRIORITY - 85% confidence)
   Examples: "# L-shaped bracket", "# 90° bend", "# inner bend radius", "# U-shape channel", "# Circular L-bracket"

BENDING PARAMETERS TO EXTRACT:
- bend_type: "L_shape" | "U_shape" | "Z_shape" | "L_shape_circular" | "U_shape_circular" | "Z_shape_circular" | "custom"
- bend_radius: Inner radius of the bend (e.g., 1.5mm, 3.0mm)
- bend_angle: Angle in degrees (typically 90°, 180°)
- thickness: Sheet metal thickness
- dimensions: For rectangular: {dim_x, dim_y, flange_height}; For circular: {diameter, offset_x, offset_x_left, offset_x_right}
- bend_line_start: {x, y, z} - Start point of bend line (optional for circular)
- bend_line_end: {x, y, z} - End point of bend line (optional for circular)

BEND LINE CALCULATION:
For L-shape with dim_x=30, dim_y=103, bend_radius=3, thickness=1.5:
- Bend occurs at X = bend_radius + thickness = 4.5mm
- Bend line: start={x: 4.5, y: 0, z: 0}, end={x: 4.5, y: 103, z: 0}

For U-shape with 2 bends:
- Left bend at X = bend_radius + thickness
- Right bend at X = dim_x - (bend_radius + thickness)

AVOID FALSE POSITIVES:
- Fillets (Part.makeFillet) are NOT bending features
- Small radius fillets (< 1mm) on edges are decorative, not structural bends
- Bending is structural: connects large planar sections (plates/flanges)

=== COUNTERSINKS (NEW) ===

CRITICAL INDICATORS (in priority order):
1. **add_countersink_leg1() / add_countersink_leg2() helper functions** (HIGHEST PRIORITY - 95% confidence)
   Pattern: add_countersink_leg2(shape, hole_radius, cs_radius, cs_angle, hole_y, hole_z, ...)
   
2. **Part.makeCone() for countersink** (HIGH PRIORITY - 90% confidence)
   Pattern: Part.makeCone(R1, R2, height, ...) where R2 > R1
   Usually followed by .cut() operation
   
3. **Countersink variable names** (HIGH PRIORITY - 85% confidence)
   Examples: cs_hole_rad, cs_radius, cs_angle, cs_diam, countersink_angle
   
4. **Comments mentioning countersink** (MEDIUM PRIORITY - 75% confidence)
   Examples: "# Countersink holes", "# 90deg countersink", "# fraisage"

COUNTERSINK PARAMETERS TO EXTRACT:
- position: {x, y, z} - Center of countersink hole
- hole_diameter: Diameter of through hole (smaller diameter)
- cs_diameter: Diameter of countersink (larger diameter)
- cs_angle: Countersink angle in degrees (typically 90° or 120°)
- depth: Total depth or "through" for through holes
- confidence: 0.0-1.0
- source: "add_countersink_function" | "makeCone_call" | "variables" | "comment"

PARAMETER EXTRACTION NOTES:
- For add_countersink_leg2(shape, cs_hole_rad, cs_radius, cs_angle, hole_y, hole_z, ...):
  * hole_diameter = cs_hole_rad * 2
  * cs_diameter = cs_radius * 2
  * position.y = hole_y, position.z = hole_z
  * position.x calculated from leg geometry
  
- For Part.makeCone(R1, R2, height):
  * hole_diameter = R1 * 2 (smaller radius)
  * cs_diameter = R2 * 2 (larger radius)
  * cs_angle inferred from geometry or variables

AVOID FALSE POSITIVES:
- Regular cones used for other purposes (not countersinks)
- Chamfers (small bevels) are NOT countersinks
- Countersinks must have clear hole + conical depression pattern

=== SQUARE / RECTANGULAR HOLES (NEW) ===

CRITICAL INDICATORS:
1. **Part.makeBox() used as a cutter** (HIGHEST PRIORITY - 95% confidence)
   Pattern: cutter = Part.makeBox(width, length, depth, App.Vector(x, y, z), App.Vector(dx, dy, dz))
   The cutter must later appear in `.cut(cutter)` or be appended to a list/compound that is cut.

2. **Looped or compound cutters** (HIGHEST PRIORITY - 95% confidence)
   Pattern:
   ```python
   square_holes = []
   for row in range(n_rows):
       for col in range(n_cols):
           hole = Part.makeBox(...)
           square_holes.append(hole)
   holes_compound = Part.makeCompound(square_holes)
   final_shape = base_shape.cut(holes_compound)
   ```
   Return one box_holes entry for each actual iteration.

AVOID FALSE POSITIVES:
- Base sheets/plates are also Part.makeBox(), but are NOT holes.
- Do NOT return boxes named base_shape, base_plate_shape, plate, sheet, body, outer, inner unless they are clearly cut from another shape.
- Only boxes that are used as cutters should be returned.

PARAMETERS TO EXTRACT:
- type: "square_hole" if width == length, otherwise "rectangular_hole"
- corner: App.Vector position of the cutter
- width: X size of the cutter
- length: Y size of the cutter
- depth: Z/extrusion depth of the cutter
- center and bounding_box
- is_through_cut: true when depth is larger than or equal to sheet thickness or starts at/below the face
- is_open_cutout: true if the cutter touches the base part boundary (edge notch), false if fully inside

=== SHAPE TYPE DETECTION (NEW) ===

CRITICAL INDICATORS (in priority order):
1. **Comments with shape type** (HIGHEST PRIORITY - 95% confidence)
   Examples: "# Shape type: L-bracket", "# L-shaped bracket", "# U-shape channel", "# Tube carré"
   
2. **Variable names** (HIGH PRIORITY - 85% confidence)
   Examples: l_bracket_dim, u_channel_width, tube_diameter, tube_wall_thickness
   
3. **Function calls** (MEDIUM PRIORITY - 75% confidence)
   Examples: Part.makeLShape(), Part.makeUShape(), Part.makeBox()

SHAPE TYPES TO DETECT:
- "l-shape" | "l_shape" | "lshape" - L-shaped brackets (two perpendicular legs)
- "l-bracket-circular" - Circular plate with 1 bend
- "u-shape" | "u_shape" | "capot" - U-shaped channels (three sides)
- "u-shaped-circular" - Circular plate with 2 bends in same direction
- "z-shape" | "z_shape" - Z-shaped brackets
- "z-shaped-circular" - Circular plate with 2 bends in opposite directions
- "tube" | "tube_carre" | "tube_rond" - Hollow tubes (square or round cross-section)
- "coffert" | "coffer" - Coffert shapes (complex multi-leg structures)
- "box" | "rectangle" | "rectangular" - Simple rectangular boxes
- "sheet-circular" - Flat circular plate (disc)

DETECTION PATTERNS:
- L-shape: Look for "L-shaped", "L-bracket", "equerre", Part.makeLShape()
- L-bracket-Circular: Look for "L-bracket-Circular", Part.makeCircularLShape()
- Capot/U-shape: Look for "U-shaped", "U-channel", "capot", Part.makeUShape()
- U-shaped-Circular: Look for "U-shaped-Circular", Part.makeCircularUShape()
- Z-shaped-Circular: Look for "Z-shaped-Circular", Part.makeCircularZShape()
- Tube: Look for "tube", "hollow", "wall_thickness", makeBox + cut operations for hollow interior
- Coffert: Look for "coffert", "coffer", complex multi-leg geometry
- Box: Look for "box", "rectangle", Part.makeBox() without cuts
- Sheet-Circular: Look for "Sheet-Circular", Part.makeCylinder()

Return in JSON:
{
  "shape_type": "l-shape" | "l-bracket-circular" | "capot" | "u-shaped-circular" | "z-shaped-circular" | "tube" | "coffert" | "box" | "sheet-circular" | null,
  "threaded_holes": [...],
  "oblongs": [...],
  ...
}

Return ONLY a valid JSON object with this exact structure:
{
  "shape_type": "l-shape" | "l-bracket-circular" | "capot" | "u-shaped-circular" | "z-shaped-circular" | "tube" | "coffert" | "box" | "sheet-circular" | null,
  "threaded_holes": [
    {
      "position": {"x": 3.5, "y": 3.5, "z": 0},
      "diameter": 4.0,
      "thread_type": "M4",
      "depth": 4.0,
      "confidence": 0.95,
      "source": "hole_thread_size_variable" | "comment" | "tap_hole_diam" | "inferred"
    }
  ],
  "oblongs": [
    {
      "corner": {"x": 17.5, "y": 22.5, "z": -1.0},
      "straight_length": 10.0,
      "width": 5.0,
      "depth": 7.0,
      "direction": {"x": 0, "y": 0, "z": 1},
      "confidence": 0.95,
      "source": "makeOblong_call" | "variables" | "comment"
    }
  ],
  "bending_features": [
    {
      "bend_type": "L_shape",
      "bend_radius": 3.0,
      "bend_angle": 90,
      "thickness": 1.5,
      "dimensions": {"dim_x": 30, "dim_y": 103, "flange_height": 20},
      "bend_line_start": {"x": 4.5, "y": 0, "z": 0},
      "bend_line_end": {"x": 4.5, "y": 103, "z": 0},
      "confidence": 0.95,
      "source": "makeLShape_call" | "SheetMetal_workbench" | "comment"
    }
  ],
  "countersinks": [
    {
      "position": {"x": 3.0, "y": 10.5, "z": 8.0},
      "hole_diameter": 3.2,
      "cs_diameter": 6.0,
      "cs_angle": 90,
      "depth": "through",
      "confidence": 0.95,
      "source": "add_countersink_function" | "makeCone_call" | "variables" | "comment"
    }
  ],
  "box_holes": [
    {
      "type": "square_hole" | "rectangular_hole",
      "corner": {"x": 97.5, "y": 97.5, "z": 0},
      "center": {"x": 100, "y": 100, "z": 1},
      "width": 5.0,
      "length": 5.0,
      "depth": 4.0,
      "direction": {"x": 0, "y": 0, "z": 1},
      "bounding_box": {"min": {"x": 97.5, "y": 97.5, "z": 0}, "max": {"x": 102.5, "y": 102.5, "z": 4}},
      "is_through_cut": true,
      "is_open_cutout": false,
      "confidence": 0.95,
      "source": "Part.makeBox_cut"
    }
  ]
}

If no features found, return {"shape_type": null, "threaded_holes": [], "oblongs": [], "bending_features": [], "countersinks": [], "box_holes": []}
"""
        
        user_prompt = f"""Analyze this FreeCAD code and extract threaded holes, oblongs, and bending features:

```python
{code_content}
```

Return JSON only, no explanation."""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        
        # Invoke LLM with cost tracking (ASYNC)
        if get_openai_callback and self.cost_tracker:
            with get_openai_callback() as cb:
                response = await self.llm.ainvoke(messages)
                response_text = response.content.strip()
                
                # Track cost
                self.cost_tracker.add_chain_cost(
                    "metadata_analysis",
                    cb,
                    self.llm_model
                )
        else:
            # Fallback without cost tracking
            response = await self.llm.ainvoke(messages)
            response_text = response.content.strip()
        
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(1)
        
        # Parse JSON with robust validation
        try:
            result = json.loads(response_text)
            
            # ✅ VALIDATION 1: Test re-serialization (catches duplicate keys, invalid structure)
            try:
                json.dumps(result)
            except (TypeError, ValueError) as e:
                logger.error(f"[FeatureAnalyzer] LLM generated non-serializable JSON: {e}")
                raise ValueError(f"Corrupt JSON structure: {e}")
            
            # ✅ VALIDATION 2: Check required structure
            if not isinstance(result, dict):
                raise ValueError("Result must be a dictionary")
            
            # ✅ VALIDATION 3: Validate array fields
            for field in ['threaded_holes', 'oblongs', 'bending_features', 'countersinks', 'box_holes']:
                if field in result and not isinstance(result[field], list):
                    logger.error(f"[FeatureAnalyzer] Field '{field}' must be array, got {type(result[field])}")
                    raise ValueError(f"Invalid {field} structure")
            
            logger.debug("[FeatureAnalyzer] LLM JSON validation passed")
            
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"[FeatureAnalyzer] ❌ LLM generated invalid JSON: {e}")
            logger.warning(f"[FeatureAnalyzer] → Falling back to regex-only analysis")
            logger.debug(f"[FeatureAnalyzer] Raw response: {response_text[:500]}")
            
            # Return empty structure to trigger regex fallback in analyze_code()
            return {
                "threaded_holes": [],
                "oblongs": [],
                "bending_features": [],
                "countersinks": [],
                "box_holes": []
            }
        
        # Validate and enrich structure
        if "threaded_holes" not in result:
            result["threaded_holes"] = []
        if "oblongs" not in result:
            result["oblongs"] = []
        if "bending_features" not in result:
            result["bending_features"] = []
        if "countersinks" not in result:
            result["countersinks"] = []
        if "box_holes" not in result:
            result["box_holes"] = []
        
        # Enrich threaded holes - ensure diameter is always calculated
        for hole in result["threaded_holes"]:
            self._enrich_threaded_hole(hole)
        
        # Enrich oblongs with calculated fields
        for oblong in result["oblongs"]:
            self._enrich_oblong(oblong, code_content)
        
        # Enrich bending features with calculated fields
        for bending in result["bending_features"]:
            self._enrich_bending(bending, code_content)
        
        return result
    
    def _enrich_threaded_hole(self, hole: Dict) -> None:
        """
        Enrich threaded hole metadata to ensure diameter is always calculated.
        
        Priority:
        1. Use existing diameter if > 0
        2. Calculate from radius if > 0
        3. Calculate from thread_type (M5 -> 5.0mm diameter)
        4. If all fail, keep as is (will be handled by step_converter)
        
        Modifies hole dict in-place.
        
        Args:
            hole: Threaded hole dict (will be modified in-place)
        """
        diameter = hole.get('diameter', 0)
        radius = hole.get('radius', 0)
        thread_type = hole.get('thread_type')
        
        # Priority 1: Use existing diameter if valid
        if diameter and diameter > 0:
            # Ensure radius is also set
            if not radius or radius == 0:
                hole['radius'] = diameter / 2.0
            return
        
        # Priority 2: Calculate from radius
        if radius and radius > 0:
            hole['diameter'] = radius * 2.0
            return
        
        # Priority 3: Calculate from thread_type (e.g., M5 -> 5.0mm)
        if thread_type:
            calculated_diameter = self._thread_type_to_diameter(thread_type)
            if calculated_diameter > 0:
                hole['diameter'] = calculated_diameter
                hole['radius'] = calculated_diameter / 2.0
                logger.info(f"[_enrich_threaded_hole] Calculated diameter={calculated_diameter}mm from thread_type={thread_type}")
                return
        
        # If all fail, log warning but keep as is
        logger.warning(f"[_enrich_threaded_hole] Could not calculate diameter for hole at {hole.get('position', {})}")
    
    def _enrich_oblong(self, oblong: Dict, code_content: str) -> None:
        """
        Enrich oblong metadata from LLM with calculated fields.
        
        ✅ FIXED: No bbox adjustment - preserves original geometry for accurate face matching.
        
        Modifies oblong dict in-place to add:
        - total_length (calculated from straight_length + width)
        - fillet_radius (calculated from width / 2)
        - bounding_box (calculated from ORIGINAL corner + dimensions)
        - center (calculated from bounding_box)
        - expected_faces (static)
        - geometry_type (static)
        - is_through_cut (flag for negative Z)
        - cut_direction (extrusion direction)
        
        Args:
            oblong: Oblong dict from LLM (will be modified in-place)
            code_content: Source code for additional context
        """
        # Extract required fields
        straight_length = oblong.get('straight_length', 0)
        width = oblong.get('width', 0)
        depth = oblong.get('depth', 0)
        corner = oblong.get('corner', {})
        direction = oblong.get('direction', {'x': 0, 'y': 0, 'z': 1})
        
        logger.debug(f"[_enrich_oblong] Extracted: straight_length={straight_length}, width={width}, depth={depth}")
        logger.debug(f"[_enrich_oblong] corner={corner}, direction={direction}")
        
        if straight_length == 0 or width == 0:
            logger.warning(f"[_enrich_oblong] Missing straight_length or width, skipping enrichment")
            return
        
        if not corner or not isinstance(corner, dict):
            logger.warning(f"[_enrich_oblong] Invalid corner, skipping enrichment")
            return
        
        logger.debug(f"[_enrich_oblong] Starting enrichment...")
        
        # ✅ NO ADJUSTMENT - Keep original corner and depth for accurate face matching
        # The step_converter.py will handle bbox expansion if needed
        
        # Calculate derived fields
        total_length = straight_length + width
        fillet_radius = width / 2.0
        end_radius = width / 2.0
        
        logger.debug(f"[_enrich_oblong] Calculated: total_length={total_length}, fillet_radius={fillet_radius}")
        
        # Detect through-cut pattern (for metadata flags)
        is_through_cut = corner.get('z', 0) < 0
        
        # Determine cut direction
        if abs(direction.get('z', 0)) > 0.9:
            cut_direction = 'z_up'
        elif abs(direction.get('x', 0)) > 0.9:
            cut_direction = 'x_forward'
        elif abs(direction.get('y', 0)) > 0.9:
            cut_direction = 'y_forward'
        else:
            cut_direction = 'other'
        
        # Calculate bounding box based on direction
        # ✅ USE ORIGINAL corner.z and depth (no adjustment)
        # Bbox được tính từ pnt (corner) + dimensions (length, width, height)
        # Đây là bbox dự kiến từ metadata, sẽ được so sánh với bbox thực tế từ faces trong step_converter.py
        if abs(direction.get('z', 0)) > 0.9:  # Z-up extrusion (most common)
            # pnt (corner) là điểm bắt đầu, sau đó di chuyển theo trục XYZ
            # X: từ corner.x đến corner.x + total_length (có thêm end_radius ở 2 đầu)
            # Y: từ corner.y đến corner.y + width
            # Z: từ corner.z đến corner.z + depth
            bbox = {
                "min": {
                    "x": corner.get('x', 0) - end_radius,  # Mở rộng về trái cho rounded end
                    "y": corner.get('y', 0),               # Bắt đầu từ corner.y
                    "z": corner.get('z', 0)                 # ✅ Keep original Z (can be negative)
                },
                "max": {
                    "x": corner.get('x', 0) + total_length + end_radius,  # Mở rộng về phải cho rounded end
                    "y": corner.get('y', 0) + width,                       # Kết thúc tại corner.y + width
                    "z": corner.get('z', 0) + depth                        # ✅ Keep original depth
                }
            }
        elif abs(direction.get('x', 0)) > 0.9:  # X-direction extrusion
            bbox = {
                "min": {
                    "x": corner.get('x', 0),
                    "y": corner.get('y', 0) - end_radius,
                    "z": corner.get('z', 0)
                },
                "max": {
                    "x": corner.get('x', 0) + depth,
                    "y": corner.get('y', 0) + total_length + end_radius,
                    "z": corner.get('z', 0) + width
                }
            }
        else:  # Y-direction or other
            bbox = {
                "min": {
                    "x": corner.get('x', 0),
                    "y": corner.get('y', 0),
                    "z": corner.get('z', 0)
                },
                "max": {
                    "x": corner.get('x', 0) + width,
                    "y": corner.get('y', 0) + depth,
                    "z": corner.get('z', 0) + total_length
                }
            }
        
        # Calculate center from bbox
        calculated_center = {
            "x": (bbox['min']['x'] + bbox['max']['x']) / 2.0,
            "y": (bbox['min']['y'] + bbox['max']['y']) / 2.0,
            "z": (bbox['min']['z'] + bbox['max']['z']) / 2.0
        }
        
        # 🎯 PRESERVE EXISTING CENTER if already set by _parse_oblong_params
        # Only use calculated center if current center is (0,0,0) or invalid
        existing_center = oblong.get('center', {})
        if (existing_center.get('x', 0) != 0 or 
            existing_center.get('y', 0) != 0 or 
            existing_center.get('z', 0) != 0):
            # Keep existing center (from parsing with translate)
            center = existing_center
            logger.debug(f"[_enrich_oblong] Preserving existing center: {center}")
        else:
            # Use calculated center
            center = calculated_center
            logger.debug(f"[_enrich_oblong] Using calculated center: {center}")
        
        # Add calculated fields to oblong (in-place modification)
        # ✅ Store ORIGINAL depth (not adjusted)
        oblong['depth'] = round(depth, 3)
        oblong['total_length'] = round(total_length, 3)
        oblong['fillet_radius'] = round(fillet_radius, 3)
        
        # 🎯 PRESERVE ROTATED BBOX - Only set if not already set by transform logic
        if 'bounding_box' not in oblong or oblong['bounding_box'].get('min', {}).get('x', 0) == corner.get('x', 0) - end_radius:
            # Bbox not set or is original (not rotated) - safe to overwrite
            oblong['bounding_box'] = {
                "min": {k: round(v, 3) for k, v in bbox['min'].items()},
                "max": {k: round(v, 3) for k, v in bbox['max'].items()}
            }
            logger.debug(f"[_enrich_oblong] Set bbox: X=[{bbox['min']['x']:.1f}, {bbox['max']['x']:.1f}], Y=[{bbox['min']['y']:.1f}, {bbox['max']['y']:.1f}]")
        else:
            # Bbox already set (likely rotated) - preserve it!
            logger.debug(f"[_enrich_oblong] Preserving existing bbox (likely rotated)")
        
        oblong['center'] = {k: round(v, 3) for k, v in center.items()}
        oblong['expected_faces'] = {
            "planar": 2,
            "cylindrical": 4,
            "total": 6
        }
        oblong['geometry_type'] = "filleted_rectangle"
        
        # ✅ Add metadata flags for step_converter.py
        oblong['is_through_cut'] = is_through_cut
        oblong['cut_direction'] = cut_direction
        
        # 🎯 PHASE 1: ENHANCED METADATA FOR ACCURATE DETECTION
        # Based on makeOblong implementation analysis
        
        # 1. Fillet Edges Information
        # makeOblong fillets 4 vertical edges with length = height (depth)
        corner_x = corner.get('x', 0)
        corner_y = corner.get('y', 0)
        corner_z = corner.get('z', 0)
        
        oblong['fillet_edges'] = {
            'count': 4,
            'edge_length': round(depth, 3),  # Each edge has length = depth
            'edge_type': 'vertical',  # Edges parallel to extrusion direction
            'positions': [  # 4 corner positions (X, Y at Z-range)
                {'x': round(corner_x, 3), 'y': round(corner_y, 3), 'corner': 'bottom_left'},
                {'x': round(corner_x + total_length, 3), 'y': round(corner_y, 3), 'corner': 'bottom_right'},
                {'x': round(corner_x, 3), 'y': round(corner_y + width, 3), 'corner': 'top_left'},
                {'x': round(corner_x + total_length, 3), 'y': round(corner_y + width, 3), 'corner': 'top_right'}
            ]
        }
        
        # 2. Cylindrical Faces Detailed Information
        # 4 cylinders at corners, grouped by END (left/right)
        # CRITICAL: fillet_radius has epsilon: min(length, width) / 2.0 - 1e-6
        actual_fillet_radius = fillet_radius - 1e-6  # Match makeOblong implementation
        
        oblong['cylindrical_faces_info'] = {
            'count': 4,
            'radius': round(actual_fillet_radius, 6),  # Include epsilon precision
            'radius_nominal': round(fillet_radius, 3),  # Without epsilon
            'radius_tolerance': 0.5,  # Tolerance for matching (mm)
            'arc_length': round(depth, 3),  # Arc length = depth (extrusion height)
            'grouping': 'end_based',  # Group by left/right ends
            'left_end': {  # 2 cylinders at left end (X = corner_x)
                'x': round(corner_x, 3),
                'y_range': [round(corner_y, 3), round(corner_y + width, 3)],
                'z_center': round(corner_z + depth / 2.0, 3),
                'count': 2
            },
            'right_end': {  # 2 cylinders at right end (X = corner_x + total_length)
                'x': round(corner_x + total_length, 3),
                'y_range': [round(corner_y, 3), round(corner_y + width, 3)],
                'z_center': round(corner_z + depth / 2.0, 3),
                'count': 2
            },
            'end_separation': round(total_length, 3)  # Distance between left and right ends
        }
        
        # 3. Planar Faces Information
        # 2 planar faces (top and bottom) with filleted rectangle shape
        # Area = straight_section_area + rounded_ends_area
        import math
        straight_area = straight_length * width
        rounded_area = math.pi * (fillet_radius ** 2)  # Full circle area
        expected_planar_area = straight_area + rounded_area
        
        oblong['planar_faces_info'] = {
            'count': 2,
            'shape': 'filleted_rectangle',
            'area_expected': round(expected_planar_area, 3),
            'area_tolerance': 0.15,  # 15% tolerance
            'normal_direction': direction,
            'positions': [
                {
                    'z': round(corner_z, 3),
                    'type': 'bottom',
                    'normal': {'x': 0, 'y': 0, 'z': -1} if direction.get('z', 0) > 0 else direction
                },
                {
                    'z': round(corner_z + depth, 3),
                    'type': 'top',
                    'normal': {'x': 0, 'y': 0, 'z': 1} if direction.get('z', 0) > 0 else direction
                }
            ]
        }
        
        # 4. Geometric Pattern (for pattern-based validation)
        oblong['geometric_pattern'] = {
            'type': 'filleted_box',
            'straight_section': {
                'length': round(straight_length, 3),
                'width': round(width, 3),
                'bbox': {
                    'min': {
                        'x': round(corner_x + fillet_radius, 3),
                        'y': round(corner_y, 3),
                        'z': round(corner_z, 3)
                    },
                    'max': {
                        'x': round(corner_x + fillet_radius + straight_length, 3),
                        'y': round(corner_y + width, 3),
                        'z': round(corner_z + depth, 3)
                    }
                }
            },
            'left_end': {
                'center': {
                    'x': round(corner_x, 3),
                    'y': round(corner_y + width / 2.0, 3),
                    'z': round(corner_z + depth / 2.0, 3)
                },
                'radius': round(fillet_radius, 3),
                'type': 'semicircle'
            },
            'right_end': {
                'center': {
                    'x': round(corner_x + total_length, 3),
                    'y': round(corner_y + width / 2.0, 3),
                    'z': round(corner_z + depth / 2.0, 3)
                },
                'radius': round(fillet_radius, 3),
                'type': 'semicircle'
            }
        }
        
        # 5. Validation Hints for step_converter.py
        oblong['validation_hints'] = {
            'cylinder_grouping_method': 'end_based',  # Use end-based grouping
            'expected_distances': {
                'within_end': round(width, 3),  # Distance between 2 cylinders in same end
                'between_ends': round(total_length, 3),  # Distance between left and right ends
                'diagonal': round(math.sqrt(width**2 + total_length**2), 3)  # Diagonal distance
            },
            'bbox_filtering': {
                'enabled': True,
                'tolerance': round(max(total_length * 0.1, width * 0.1, 2.0), 3)  # Adaptive tolerance
            }
        }
        
        # 🎯 PHASE 1 ENHANCEMENT: DUAL-ORIENTATION METADATA
        # Generate metadata for BOTH horizontal and vertical orientations
        # This handles cases where oblongs are rotated 90° after creation
        
        oblong['orientations'] = {
            'horizontal': {
                'description': 'Original orientation (length along X, width along Y)',
                'bounding_box': {
                    'min': {'x': round(corner_x, 3), 'y': round(corner_y, 3), 'z': round(corner_z, 3)},
                    'max': {'x': round(corner_x + total_length, 3), 'y': round(corner_y + width, 3), 'z': round(corner_z + depth, 3)}
                },
                'cylinder_positions': {
                    'left_end': {
                        'x': round(corner_x + fillet_radius, 3), 
                        'y_range': [round(corner_y, 3), round(corner_y + width, 3)],
                        'z_center': round(corner_z + depth / 2.0, 3)
                    },
                    'right_end': {
                        'x': round(corner_x + total_length - fillet_radius, 3), 
                        'y_range': [round(corner_y, 3), round(corner_y + width, 3)],
                        'z_center': round(corner_z + depth / 2.0, 3)
                    }
                },
                'expected_dimensions': {'length_axis': 'x', 'width_axis': 'y', 'length': round(total_length, 3), 'width': round(width, 3)}
            },
            'vertical': {
                'description': 'Rotated 90° (length along Y, width along X)',
                'bounding_box': {
                    'min': {'x': round(corner_x, 3), 'y': round(corner_y, 3), 'z': round(corner_z, 3)},
                    'max': {'x': round(corner_x + width, 3), 'y': round(corner_y + total_length, 3), 'z': round(corner_z + depth, 3)}
                },
                'cylinder_positions': {
                    'left_end': {
                        'y': round(corner_y + fillet_radius, 3), 
                        'x_range': [round(corner_x, 3), round(corner_x + width, 3)],
                        'z_center': round(corner_z + depth / 2.0, 3)
                    },
                    'right_end': {
                        'y': round(corner_y + total_length - fillet_radius, 3), 
                        'x_range': [round(corner_x, 3), round(corner_x + width, 3)],
                        'z_center': round(corner_z + depth / 2.0, 3)
                    }
                },
                'expected_dimensions': {'length_axis': 'y', 'width_axis': 'x', 'length': round(total_length, 3), 'width': round(width, 3)}
            }
        }
        
        if is_through_cut:
            logger.info(f"[_enrich_oblong] Detected through-cut oblong: corner.z={corner.get('z')}, depth={depth}")
        
        logger.info(f"[_enrich_oblong] ✅ Enhanced metadata: {len(oblong.get('cylindrical_faces_info', {}).get('left_end', {}))} ends, "
                   f"area={expected_planar_area:.2f}mm², radius={actual_fillet_radius:.6f}mm")
        logger.debug(f"[_enrich_oblong] Enriched oblong: total_length={total_length}, fillet_radius={fillet_radius}")
    
    def _enrich_bending(self, bending: Dict, code_content: str) -> None:
        """
        Enrich bending metadata from LLM with calculated fields.
        
        Calculates:
        - outer_radius (inner_radius + thickness)
        - bend_line (start and end points)
        - expected_faces (inner toroidal + outer cylindrical)
        
        Args:
            bending: Bending dict from LLM (will be modified in-place)
            code_content: Source code for additional context
        """
        bend_radius = bending.get('bend_radius', 0)
        thickness = bending.get('thickness', 0)
        bend_type = bending.get('bend_type', 'custom')
        dimensions = bending.get('dimensions', {})
        
        if bend_radius == 0 or thickness == 0:
            logger.warning(f"[_enrich_bending] Missing bend_radius or thickness, skipping enrichment")
            return
        
        logger.debug(f"[_enrich_bending] Enriching {bend_type} bending...")
        
        # Calculate outer radius (critical for pairing)
        outer_radius = bend_radius + thickness
        
        # Calculate bend line if not provided
        if 'bend_line_start' not in bending or 'bend_line_end' not in bending:
            dim_x = dimensions.get('dim_x', 0)
            dim_y = dimensions.get('dim_y', 0)
            
            if bend_type == 'L_shape':
                # Single bend along Y-axis
                bend_x = bend_radius + thickness
                bending['bend_line_start'] = {'x': bend_x, 'y': 0, 'z': 0}
                bending['bend_line_end'] = {'x': bend_x, 'y': dim_y, 'z': 0}
                bending['bend_count'] = 1
                
            elif bend_type == 'U_shape':
                # Two parallel bends
                left_bend_x = bend_radius + thickness
                right_bend_x = dim_x - (bend_radius + thickness)
                
                bending['bend_lines'] = [
                    {
                        'start': {'x': left_bend_x, 'y': 0, 'z': 0},
                        'end': {'x': left_bend_x, 'y': dim_y, 'z': 0}
                    },
                    {
                        'start': {'x': right_bend_x, 'y': 0, 'z': 0},
                        'end': {'x': right_bend_x, 'y': dim_y, 'z': 0}
                    }
                ]
                bending['bend_count'] = 2
                
            elif bend_type == 'Z_shape':
                # Two opposite bends
                left_bend_x = bend_radius + thickness
                right_bend_x = dim_x - (bend_radius + thickness)
                
                bending['bend_lines'] = [
                    {
                        'start': {'x': left_bend_x, 'y': 0, 'z': 0},
                        'end': {'x': left_bend_x, 'y': dim_y, 'z': 0},
                        'direction': 'up'
                    },
                    {
                        'start': {'x': right_bend_x, 'y': 0, 'z': 0},
                        'end': {'x': right_bend_x, 'y': dim_y, 'z': 0},
                        'direction': 'down'
                    }
                ]
                bending['bend_count'] = 2
        
        # Add calculated fields
        bending['outer_radius'] = round(outer_radius, 3)
        bending['expected_faces'] = {
            'inner_toroidal': bending.get('bend_count', 1),
            'outer_cylindrical': bending.get('bend_count', 1),
            'total': bending.get('bend_count', 1) * 2
        }
        
        logger.debug(f"[_enrich_bending] Enriched: outer_radius={outer_radius}, bend_count={bending.get('bend_count', 1)}")
    
    def _analyze_threaded_with_regex(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Detect threaded holes by finding Part.makeThreaded() calls.
        
        [ENHANCED] Now supports loops that create multiple threaded holes.
        
        Simple pattern: Part.makeThreaded(radius, depth, position, direction)
        Thread size is extracted from comments (e.g., # M6, # M8 threaded hole)
        
        Supports both:
        - Inline vectors: Part.makeThreaded(2.5, 10, App.Vector(0,0,0), App.Vector(0,0,1))
        - Vector variables: Part.makeThreaded(radius, depth, pos_var, dir_var)
        - Loops: for (x, y) in positions_list: Part.makeThreaded(..., App.Vector(x, y, z), ...)
        """
        threaded_holes = []
        
        # 🎯 STEP 1: Detect threaded holes in loops FIRST (before single calls)
        loop_holes = self._detect_threaded_loop_patterns(code_content)
        threaded_holes.extend(loop_holes)
        
        # 🎯 STEP 2: Detect single threaded holes (skip those already in loops)
        # Pattern: Part.makeThreaded(radius, depth, position, direction)
        # Position and direction can be either App.Vector(...) or variable names
        # Handle multi-line calls - use a two-step approach:
        # 1. Find lines with "Part.makeThreaded" (not in comments)
        # 2. Extract full call including multi-line parameters
        
        # First, find all non-comment lines with Part.makeThreaded
        lines = code_content.split('\n')
        actual_calls = []  # List of (line_num, char_start, char_end)
        
        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith('#'):
                continue
            
            # Find Part.makeThreaded in this line
            if 'Part.makeThreaded' in line:
                # Find character position
                char_pos = 0
                for i in range(line_num - 1):
                    char_pos += len(lines[i]) + 1  # +1 for newline
                char_pos += line.find('Part.makeThreaded')
                
                # Find the closing parenthesis (handle multi-line)
                paren_count = 0
                found_open = False
                call_end = char_pos
                
                for i in range(char_pos, min(char_pos + 2000, len(code_content))):
                    if code_content[i] == '(':
                        paren_count += 1
                        found_open = True
                    elif code_content[i] == ')':
                        paren_count -= 1
                        if found_open and paren_count == 0:
                            call_end = i + 1
                            break
                
                if found_open:
                    actual_calls.append((line_num, char_pos, call_end))
        
        # Process actual calls
        for line_num, call_start, call_end in actual_calls:
            try:
                # Extract the full call text
                call_text = code_content[call_start:call_end]
                
                # Parse parameters manually by finding commas outside parentheses
                # This handles nested App.Vector(...) correctly
                def find_parameter_end(text, start_pos):
                    """Find the end of a parameter (comma or closing paren, handling nested parens)"""
                    paren_count = 0
                    i = start_pos
                    while i < len(text):
                        if text[i] == '(':
                            paren_count += 1
                        elif text[i] == ')':
                            paren_count -= 1
                        elif text[i] == ',' and paren_count == 0:
                            return i
                        elif text[i] == ')' and paren_count == 0:
                            return i
                        i += 1
                    return len(text)
                
                # Find opening parenthesis after "Part.makeThreaded"
                open_paren = call_text.find('(')
                if open_paren == -1:
                    continue
                
                # Extract parameters
                param_start = open_paren + 1
                
                # Parameter 1: radius
                param1_end = find_parameter_end(call_text, param_start)
                radius_expr = call_text[param_start:param1_end].strip()
                
                # Parameter 2: depth
                param2_start = param1_end + 1
                while param2_start < len(call_text) and call_text[param2_start] in ' \n\t':
                    param2_start += 1
                param2_end = find_parameter_end(call_text, param2_start)
                depth_expr = call_text[param2_start:param2_end].strip()
                
                # Parameter 3: position
                param3_start = param2_end + 1
                while param3_start < len(call_text) and call_text[param3_start] in ' \n\t':
                    param3_start += 1
                param3_end = find_parameter_end(call_text, param3_start)
                pos_expr = call_text[param3_start:param3_end].strip()
                
                # Parameter 4: direction
                param4_start = param3_end + 1
                while param4_start < len(call_text) and call_text[param4_start] in ' \n\t':
                    param4_start += 1
                # Find closing paren of Part.makeThreaded
                dir_end = call_text.rfind(')')
                if dir_end == -1:
                    dir_end = len(call_text)
                dir_expr = call_text[param4_start:dir_end].strip()
                
                # Check if this call is in a loop (skip if already handled)
                call_pos = call_start
                is_in_loop = False
                
                # Check if call is in loop body
                context_before = code_content[:call_pos]
                loop_header_pattern1 = r'for\s*\(([^,]+),\s*([^)]+)\)\s+in\s+([a-zA-Z_]\w*)\s*:'
                
                for loop_match in re.finditer(loop_header_pattern1, context_before):
                    loop_start = loop_match.end()
                    loop_body_start = code_content.find('\n', loop_start) + 1
                    if loop_body_start == 0:
                        continue
                    
                    # Find loop body end
                    loop_body_end = loop_body_start
                    indent_level = None
                    for i in range(loop_body_start, min(loop_body_start + 2000, len(code_content))):
                        if code_content[i] == '\n':
                            line_start = i + 1
                            if line_start >= len(code_content):
                                loop_body_end = len(code_content)
                                break
                            line_end = code_content.find('\n', line_start)
                            if line_end == -1:
                                line_end = len(code_content)
                            line = code_content[line_start:line_end]
                            
                            if not line.strip():
                                continue
                            
                            leading = len(line) - len(line.lstrip())
                            if indent_level is None:
                                indent_level = leading
                            
                            if leading <= indent_level and line.strip() and not (line.startswith(' ') or line.startswith('\t')):
                                loop_body_end = i
                                break
                    
                    if loop_body_start <= call_pos <= loop_body_end:
                        is_in_loop = True
                        logger.debug(f"[ThreadedAnalyzer] Skipping call in loop body (line {line_num})")
                        break
                
                if is_in_loop:
                    continue
                
                # Process this call (similar to old logic)
                match_pos = call_start  # For compatibility with rest of code
                
                # Track positions already found in loops to avoid duplicates
                loop_positions = {(h['position']['x'], h['position']['y']) for h in loop_holes}
                
                # Evaluate expressions
                radius = self._safe_eval(radius_expr.strip(), code_content)
                depth = self._safe_eval(depth_expr.strip(), code_content)
                
                # Parse position - handle both inline vectors and variables
                pos_expr_clean = pos_expr.strip()
                position = {'x': 0, 'y': 0, 'z': 0}
                
                if 'App.Vector' in pos_expr_clean:
                    # Inline vector: App.Vector(x, y, z) - handle nested parentheses correctly
                    # Find "App.Vector(" position
                    vec_start = pos_expr_clean.find('App.Vector(')
                    if vec_start != -1:
                        vec_paren_start = vec_start + len('App.Vector')
                        # Find matching closing paren (handle nested parentheses)
                        paren_count = 0
                        vec_end = -1
                        for j in range(vec_paren_start, len(pos_expr_clean)):
                            if pos_expr_clean[j] == '(':
                                paren_count += 1
                            elif pos_expr_clean[j] == ')':
                                paren_count -= 1
                                if paren_count == 0:
                                    vec_end = j + 1
                                    break
                        
                        if vec_end != -1:
                            # Extract content inside App.Vector(...)
                            vec_content = pos_expr_clean[vec_paren_start + 1:vec_end - 1]
                            
                            # Split by comma, handling nested parentheses correctly
                            pos_parts = []
                            current_part = ""
                            paren_count = 0
                            bracket_count = 0
                            
                            for char in vec_content:
                                if char == '(':
                                    paren_count += 1
                                    current_part += char
                                elif char == ')':
                                    paren_count -= 1
                                    current_part += char
                                elif char == '[':
                                    bracket_count += 1
                                    current_part += char
                                elif char == ']':
                                    bracket_count -= 1
                                    current_part += char
                                elif char == ',' and paren_count == 0 and bracket_count == 0:
                                    # This comma is a separator, not inside parentheses/brackets
                                    pos_parts.append(current_part.strip())
                                    current_part = ""
                                else:
                                    current_part += char
                            
                            # Add the last part
                            if current_part.strip():
                                pos_parts.append(current_part.strip())
                            
                            if len(pos_parts) >= 3:
                                x_val = self._safe_eval(pos_parts[0], code_content) if len(pos_parts) > 0 else 0
                                y_val = self._safe_eval(pos_parts[1], code_content) if len(pos_parts) > 1 else 0
                                z_val = self._safe_eval(pos_parts[2], code_content) if len(pos_parts) > 2 else 0
                                position = {'x': x_val, 'y': y_val, 'z': z_val}
                                logger.debug(f"[ThreadedAnalyzer] Parsed App.Vector({x_val}, {y_val}, {z_val}) from nested expression")
                            else:
                                logger.warning(f"[ThreadedAnalyzer] Could not parse App.Vector components: expected 3, got {len(pos_parts)}. Parts: {pos_parts}")
                        else:
                            logger.warning(f"[ThreadedAnalyzer] Could not find matching closing parenthesis for App.Vector in: {pos_expr_clean[:100]}")
                    else:
                        # Fallback to old regex method (for simple cases)
                        vec_match = re.search(r'App\.Vector\s*\(\s*([^)]+)\s*\)', pos_expr_clean, re.DOTALL)
                        if vec_match:
                            pos_parts_str = vec_match.group(1)
                            # Split by comma, handling multi-line and whitespace
                            pos_parts = [p.strip() for p in pos_parts_str.split(',')]
                            
                            x_val = self._safe_eval(pos_parts[0], code_content) if len(pos_parts) > 0 else 0
                            y_val = self._safe_eval(pos_parts[1], code_content) if len(pos_parts) > 1 else 0
                            z_val = self._safe_eval(pos_parts[2], code_content) if len(pos_parts) > 2 else 0
                            
                            position = {'x': x_val, 'y': y_val, 'z': z_val}
                else:
                    # Variable reference - try to resolve the variable
                    # Example: thread_pos_left = App.Vector(-(bend_radius + thickness), thread_center_y, thread_center_z)
                    try:
                        # First, try to find the variable definition
                        var_name = pos_expr_clean.strip()
                        # Remove any indexing or attribute access for now
                        if '.' in var_name:
                            var_name = var_name.split('.')[0]
                        if '[' in var_name:
                            var_name = var_name.split('[')[0]
                        
                        # Find variable assignment: var_name = App.Vector(...)
                        # Use a simpler pattern that matches the variable assignment line
                        var_pattern = rf'^\s*{re.escape(var_name)}\s*=\s*App\.Vector\s*\('
                        var_match = re.search(var_pattern, code_content, re.MULTILINE)
                        
                        if var_match:
                            # Found the variable definition, now extract the full App.Vector(...)
                            match_start = var_match.end() - 1  # Position of '(' after App.Vector
                            
                            # Find the matching closing parenthesis
                            paren_count = 0
                            close_paren_pos = -1
                            for i in range(match_start, len(code_content)):
                                if code_content[i] == '(':
                                    paren_count += 1
                                elif code_content[i] == ')':
                                    paren_count -= 1
                                    if paren_count == 0:
                                        close_paren_pos = i
                                        break
                            
                            if close_paren_pos != -1:
                                # Extract the content inside App.Vector(...)
                                pos_parts_str = code_content[match_start + 1:close_paren_pos].strip()
                                
                                # Split by comma, handling nested parentheses correctly
                                pos_parts = []
                                current_part = ""
                                paren_count = 0
                                bracket_count = 0
                                
                                i = 0
                                while i < len(pos_parts_str):
                                    char = pos_parts_str[i]
                                    
                                    if char == '(':
                                        paren_count += 1
                                        current_part += char
                                    elif char == ')':
                                        paren_count -= 1
                                        current_part += char
                                    elif char == '[':
                                        bracket_count += 1
                                        current_part += char
                                    elif char == ']':
                                        bracket_count -= 1
                                        current_part += char
                                    elif char == ',' and paren_count == 0 and bracket_count == 0:
                                        # This comma is a separator, not inside parentheses/brackets
                                        pos_parts.append(current_part.strip())
                                        current_part = ""
                                    else:
                                        current_part += char
                                    
                                    i += 1
                                
                                # Add the last part
                                if current_part.strip():
                                    pos_parts.append(current_part.strip())
                                
                                if len(pos_parts) >= 3:
                                    x_val = self._safe_eval(pos_parts[0], code_content)
                                    y_val = self._safe_eval(pos_parts[1], code_content)
                                    z_val = self._safe_eval(pos_parts[2], code_content)
                                    position = {'x': x_val, 'y': y_val, 'z': z_val}
                                    logger.debug(f"[ThreadedAnalyzer] Resolved variable {var_name} to App.Vector({x_val}, {y_val}, {z_val})")
                                elif len(pos_parts) > 0:
                                    logger.warning(f"[ThreadedAnalyzer] Could not parse App.Vector from variable {var_name}: expected 3 parts, got {len(pos_parts)}. Parts: {pos_parts}")
                                else:
                                    logger.warning(f"[ThreadedAnalyzer] Could not parse App.Vector from variable {var_name}: no parts found")
                            else:
                                logger.warning(f"[ThreadedAnalyzer] Could not find matching closing parenthesis for App.Vector in variable {var_name}")
                        else:
                            logger.warning(f"[ThreadedAnalyzer] Could not find variable definition for {var_name}")
                    except Exception as e:
                        logger.warning(f"[ThreadedAnalyzer] Failed to resolve position variable {pos_expr_clean}: {e}")
                        position = {'x': 0, 'y': 0, 'z': 0}
                
                # Check for duplicates
                pos_key = (position['x'], position['y'])
                if pos_key in loop_positions:
                    logger.debug(f"[ThreadedAnalyzer] Skipping duplicate position from single call: {pos_key}")
                    continue
                
                # Extract thread type from comment if available
                thread_type = None
                
                # 🔥 FIX: Search for M-size in multiple places:
                # 1. Comment AFTER the call (e.g., Part.makeThreaded(...)  # M4)
                call_end_line = code_content.find('\n', call_end)
                if call_end_line != -1:
                    comment_line = code_content[call_end:call_end_line]
                    thread_match = re.search(r'#\s*(M\d+(?:\.\d+)?)', comment_line, re.IGNORECASE)
                    if thread_match:
                        thread_type = thread_match.group(1).upper()
                
                # 2. Comment BEFORE the call (e.g., # M4 threaded hole\nPart.makeThreaded(...))
                if not thread_type:
                    # Search up to 5 lines before the call
                    call_start_line_start = max(0, char_pos - 500)
                    before_call_text = code_content[call_start_line_start:char_pos]
                    thread_match = re.search(r'#.*?(M\d+(?:\.\d+)?)', before_call_text, re.IGNORECASE)
                    if thread_match:
                        thread_type = thread_match.group(1).upper()
                
                # 3. Variable names (e.g., m4_drill_rad, m4_depth, m4_center_y)
                if not thread_type:
                    # Find variable name used in the call (e.g., threaded_hole_m4)
                    # Look for variable assignment before the call
                    var_pattern = r'(\w+)\s*=\s*Part\.makeThreaded'
                    var_match = re.search(var_pattern, code_content[max(0, char_pos-200):char_pos+50])
                    if var_match:
                        var_name = var_match.group(1)
                        # Check if variable name contains M-size (e.g., threaded_hole_m4, m4_hole)
                        m_match = re.search(r'm(\d+(?:\.\d+)?)', var_name, re.IGNORECASE)
                        if m_match:
                            thread_type = f"M{m_match.group(1)}"
                
                # 4. Variable names in parameters (e.g., m4_drill_rad, m4_depth)
                if not thread_type:
                    # Extract the call text to find parameter variable names
                    call_text = code_content[char_pos:call_end]
                    # Look for variables like m4_drill_rad, m4_depth, etc.
                    param_m_match = re.search(r'\b(m\d+(?:\.\d+)?)_(?:drill|depth|rad|diam|center)', call_text, re.IGNORECASE)
                    if param_m_match:
                        thread_type = f"M{param_m_match.group(1)[1:]}"  # Remove 'm' prefix
                
                # 5. Check drill diameter ONLY (not nominal diameter)
                # Only map if diameter matches exact drill size
                if not thread_type:
                    diameter = radius * 2  # Calculate diameter from radius
                    
                    # Common drill diameters for metric threads
                    drill_to_thread = {
                        2.0: "M2",    # M2 drill
                        2.5: "M2.5",  # M2.5 drill
                        3.3: "M4",    # M4 drill (3.3mm)
                        4.2: "M5",    # M5 drill (4.2mm)
                        5.0: "M6",    # M6 drill (5.0mm)
                        6.8: "M8",    # M8 drill (6.8mm)
                        8.5: "M10",   # M10 drill (8.5mm)
                        10.2: "M12",  # M12 drill (10.2mm)
                    }
                    if diameter in drill_to_thread:
                        thread_type = drill_to_thread[diameter]
                        logger.debug(f"[ThreadedAnalyzer] Matched drill diameter {diameter} to {thread_type}")
                
                # Create metadata entry
                threaded_holes.append({
                    'position': position,
                    'diameter': radius * 2,
                    'depth': depth,
                    'thread_type': thread_type,
                    'source': 'Part.makeThreaded'
                })
                
            except Exception as e:
                logger.warning(f"[ThreadedAnalyzer] Error processing single threaded call at line {line_num}: {e}")
                continue
        
        # All single calls have been processed above
                
                # Find where "Part.makeThreaded" actually appears in the matched text
                # Look backwards from match_pos to find the actual call start
                match_text_start = max(0, match_pos - 100)
                match_text = code_content[match_text_start:match_pos + 50]
                
                # Find "Part.makeThreaded" in this context
                part_make_threaded_in_match = match_text.rfind('Part.makeThreaded')
                if part_make_threaded_in_match == -1:
                    # Not found in context, skip
                    continue
                
                actual_call_pos = match_text_start + part_make_threaded_in_match
                
                # Find the line containing the actual Part.makeThreaded call
                line_start = code_content.rfind('\n', 0, actual_call_pos) + 1
                line_end = code_content.find('\n', actual_call_pos)
                if line_end == -1:
                    line_end = len(code_content)
                line_content = code_content[line_start:line_end]
                
                # Skip if the line containing Part.makeThreaded starts with # (comment)
                stripped_line = line_content.strip()
                if stripped_line.startswith('#'):
                    logger.debug(f"[ThreadedAnalyzer] Skipping match in comment line: {stripped_line[:60]}")
                    continue
                
                # Also skip if there's a # before Part.makeThreaded on the same line
                part_make_threaded_pos_in_line = line_content.find('Part.makeThreaded')
                comment_pos_in_line = line_content.find('#')
                if comment_pos_in_line != -1 and part_make_threaded_pos_in_line != -1 and comment_pos_in_line < part_make_threaded_pos_in_line:
                    logger.debug(f"[ThreadedAnalyzer] Skipping match - Part.makeThreaded is in comment: {stripped_line[:60]}")
                    continue
                
                # Check if this makeThreaded call is inside a loop (skip it, already handled by loop detection)
                
                # Check if this makeThreaded call is inside a loop (skip it, already handled by loop detection)
                # Find all loop headers before this position
                context_before = code_content[:match_pos]
                
                # Pattern 1: for (x, y) in positions:
                loop_header_pattern1 = r'for\s*\(([^,]+),\s*([^)]+)\)\s+in\s+([a-zA-Z_]\w*)\s*:'
                
                # Pattern 2: for i in range(N):
                loop_header_pattern2 = r'for\s+(\w+)\s+in\s+range\s*\(\s*([^)]+)\s*\)\s*:'
                
                # Check each loop header to see if this call is in its body
                for loop_match in re.finditer(loop_header_pattern1, context_before):
                    loop_start = loop_match.end()
                    # Find the end of this loop body (next unindented line)
                    loop_body_start = code_content.find('\n', loop_start) + 1
                    if loop_body_start == 0:
                        continue
                    
                    # Find end of loop body - look for next line with same or less indentation
                    loop_body_end = loop_body_start
                    indent_level = None
                    for i in range(loop_body_start, min(loop_body_start + 2000, len(code_content))):
                        if code_content[i] == '\n':
                            line_start = i + 1
                            if line_start >= len(code_content):
                                loop_body_end = len(code_content)
                                break
                            line_end = code_content.find('\n', line_start)
                            if line_end == -1:
                                line_end = len(code_content)
                            line = code_content[line_start:line_end]
                            
                            if not line.strip():
                                continue
                            
                            leading = len(line) - len(line.lstrip())
                            if indent_level is None:
                                indent_level = leading
                            
                            # If line has same or less indentation and is not empty, we've reached end of loop
                            if leading <= indent_level and line.strip() and not (line.startswith(' ') or line.startswith('\t')):
                                loop_body_end = i
                                break
                    
                    # Check if match_pos is within this loop body
                    # match_pos is the character position, loop_body_start/end are also character positions
                    if loop_body_start <= match_pos <= loop_body_end:
                        logger.debug(f"[ThreadedAnalyzer] Skipping makeThreaded call in loop body (char pos {match_pos} is in range {loop_body_start}-{loop_body_end})")
                        continue
                
                # Check range loops too
                for loop_match in re.finditer(loop_header_pattern2, context_before):
                    loop_start = loop_match.end()
                    loop_body_start = code_content.find('\n', loop_start) + 1
                    if loop_body_start == 0:
                        continue
                    
                    loop_body_end = loop_body_start
                    indent_level = None
                    for i in range(loop_body_start, min(loop_body_start + 2000, len(code_content))):
                        if code_content[i] == '\n':
                            line_start = i + 1
                            if line_start >= len(code_content):
                                loop_body_end = len(code_content)
                                break
                            line_end = code_content.find('\n', line_start)
                            if line_end == -1:
                                line_end = len(code_content)
                            line = code_content[line_start:line_end]
                            
                            if not line.strip():
                                continue
                            
                            leading = len(line) - len(line.lstrip())
                            if indent_level is None:
                                indent_level = leading
                            
                            if leading <= indent_level and line.strip() and not (line.startswith(' ') or line.startswith('\t')):
                                loop_body_end = i
                                break
                    
                    if loop_body_start <= match_pos <= loop_body_end:
                        logger.debug(f"[ThreadedAnalyzer] Skipping makeThreaded call in range loop body (pos {match_pos} is in range {loop_body_start}-{loop_body_end})")
                        continue
                
                radius_expr, depth_expr, pos_expr, dir_expr = match.groups()
                
                # Evaluate expressions
                radius = self._safe_eval(radius_expr.strip(), code_content)
                depth = self._safe_eval(depth_expr.strip(), code_content)
                
                # Parse position - handle both inline vectors and variables
                pos_expr = pos_expr.strip()
                if 'App.Vector' in pos_expr:
                    # Inline vector: App.Vector(x, y, z) - handle multi-line
                    vec_match = re.search(r'App\.Vector\s*\(\s*([^)]+)\s*\)', pos_expr, re.DOTALL)
                    if vec_match:
                        pos_parts_str = vec_match.group(1)
                        # Split by comma, handling multi-line and whitespace
                        pos_parts = [p.strip() for p in pos_parts_str.split(',')]
                        
                        x_val = self._safe_eval(pos_parts[0], code_content) if len(pos_parts) > 0 else 0
                        y_val = self._safe_eval(pos_parts[1], code_content) if len(pos_parts) > 1 else 0
                        z_val = self._safe_eval(pos_parts[2], code_content) if len(pos_parts) > 2 else 0
                        
                        # Debug logging if parsing failed
                        if x_val == 0.0 and pos_parts[0] not in ['0', '0.0']:
                            logger.debug(f"[ThreadedAnalyzer] Failed to parse x from '{pos_parts[0]}' in App.Vector, got 0.0")
                            logger.debug(f"[ThreadedAnalyzer] pos_expr = '{pos_expr[:100]}'")
                        if y_val == 0.0 and pos_parts[1] not in ['0', '0.0']:
                            logger.debug(f"[ThreadedAnalyzer] Failed to parse y from '{pos_parts[1]}' in App.Vector, got 0.0")
                        
                        position = {'x': x_val, 'y': y_val, 'z': z_val}
                    else:
                        position = {'x': 0, 'y': 0, 'z': 0}
                else:
                    # Variable: look for variable definition
                    # Pattern: var_name = App.Vector(x, y, z)
                    var_pattern = rf'{re.escape(pos_expr)}\s*=\s*App\.Vector\s*\(([^)]+)\)'
                    var_match = re.search(var_pattern, code_content)
                    if var_match:
                        pos_parts = [p.strip() for p in var_match.group(1).split(',')]
                        position = {
                            'x': self._safe_eval(pos_parts[0], code_content) if len(pos_parts) > 0 else 0,
                            'y': self._safe_eval(pos_parts[1], code_content) if len(pos_parts) > 1 else 0,
                            'z': self._safe_eval(pos_parts[2], code_content) if len(pos_parts) > 2 else 0
                        }
                    else:
                        # Can't resolve variable, use default
                        position = {'x': 0, 'y': 0, 'z': 0}
                
                # Extract thread size from comment on same line or nearby lines
                # For multi-line calls like:
                #   Part.makeThreaded(
                #       radius,
                #       depth,
                #       pos,
                #       dir
                #   )  # M5
                # We need to search from start of call to a few lines after the closing )
                
                line_start = code_content.rfind('\n', 0, match.start()) + 1
                # Search up to 3 lines after match.end() to catch comments after closing )
                search_end = match.end()
                for _ in range(3):
                    next_newline = code_content.find('\n', search_end)
                    if next_newline == -1:
                        search_end = len(code_content)
                        break
                    search_end = next_newline + 1
                
                search_text = code_content[line_start:search_end]
                
                # Look for M-size in comment (e.g., # M6, # M8 threaded hole)
                thread_match = re.search(r'#.*?(M\d+(?:\.\d+)?)', search_text, re.IGNORECASE)
                thread_type = thread_match.group(1).upper() if thread_match else None
                
                # Calculate diameter from radius
                diameter = radius * 2
                
                # Check if this position was already found in a loop
                pos_key = (position['x'], position['y'])
                if pos_key in loop_positions:
                    logger.debug(f"[ThreadedAnalyzer] Skipping duplicate hole at ({position['x']}, {position['y']}) - already found in loop")
                    continue
                
                # Also skip if position is (0, 0, 0) and we already have holes from loops
                # This catches cases where single call detection failed to parse position correctly
                if position['x'] == 0.0 and position['y'] == 0.0 and position['z'] == 0.0 and len(loop_holes) > 0:
                    # Check if this might be a duplicate by comparing radius/diameter
                    hole_diameter = radius * 2
                    for loop_hole in loop_holes:
                        if abs(loop_hole['diameter'] - hole_diameter) < 0.1:
                            logger.debug(f"[ThreadedAnalyzer] Skipping hole with position (0,0,0) - likely duplicate of loop hole with diameter {hole_diameter}")
                            continue
                
                threaded_holes.append({
                    "position": position,
                    "diameter": diameter,
                    "radius": radius,
                    "thread_type": thread_type,
                    "depth": depth,
                    "confidence": 0.95 if thread_type else 0.85,
                    "source": "Part.makeThreaded"
                })
                
                logger.debug(f"[ThreadedAnalyzer] Found makeThreaded: radius={radius}, thread={thread_type}, pos=({position['x']}, {position['y']}, {position['z']})")
                
            except Exception as e:
                logger.warning(f"[ThreadedAnalyzer] Failed to parse makeThreaded call: {e}")
                continue
        
        # Final deduplication: remove holes with position (0,0,0) if we have valid holes from loops
        # This catches cases where single call detection failed to parse position
        if len(loop_holes) > 0:
            filtered_holes = []
            for hole in threaded_holes:
                pos = hole['position']
                # Skip holes with (0,0,0) position if we have loop holes (likely parsing failures)
                if pos['x'] == 0.0 and pos['y'] == 0.0 and pos['z'] == 0.0:
                    # Check if this might be a duplicate by comparing with loop holes
                    hole_diameter = hole.get('diameter', 0)
                    is_duplicate = False
                    for loop_hole in loop_holes:
                        if abs(loop_hole.get('diameter', 0) - hole_diameter) < 0.1:
                            is_duplicate = True
                            logger.debug(f"[ThreadedAnalyzer] Filtering duplicate hole with (0,0,0) position, diameter={hole_diameter}")
                            break
                    if is_duplicate:
                        continue
                filtered_holes.append(hole)
            
            if len(filtered_holes) < len(threaded_holes):
                logger.debug(f"[ThreadedAnalyzer] Filtered {len(threaded_holes) - len(filtered_holes)} duplicate (0,0,0) holes")
            threaded_holes = filtered_holes
        
        logger.debug(f"[ThreadedAnalyzer] Total threaded holes: {len(threaded_holes)} (loops={len(loop_holes)}, single={len(threaded_holes) - len(loop_holes)})")
        
        # 🎯 STEP 3: Apply transforms to positions (if any transforms detected after threaded holes creation)
        threaded_holes = self._apply_transforms_to_positions(threaded_holes, code_content)
        
        return threaded_holes
    
    def _apply_transforms_to_positions(self, threaded_holes: List[Dict[str, Any]], code_content: str) -> List[Dict[str, Any]]:
        """
        Detect transforms applied after threaded holes creation and apply them to positions.
        
        Detects common transforms:
        - rotateX(angle), rotateY(angle), rotateZ(angle)
        - transformGeometry(matrix) where matrix has rotateX/Y/Z
        - translate(vector)
        - scale(factor)
        
        Args:
            threaded_holes: List of threaded hole dictionaries with positions
            code_content: Full code content to search for transforms
        
        Returns:
            List of threaded holes with transformed positions
        """
        if not threaded_holes:
            return threaded_holes
        
        # Find all Part.makeThreaded call positions
        make_threaded_positions = []
        for match in re.finditer(r'Part\.makeThreaded\s*\(', code_content):
            make_threaded_positions.append(match.start())
        
        if not make_threaded_positions:
            return threaded_holes
        
        # Find the last Part.makeThreaded call position
        last_threaded_pos = max(make_threaded_positions)
        
        # Find transforms after the last threaded hole creation
        transforms = []
        
        # Pattern 1: rotateX/Y/Z with Matrix
        # mat.rotateX(math.radians(180))
        rotate_patterns = [
            (r'\.rotateX\s*\(\s*math\.radians\s*\(\s*([^)]+)\s*\)\s*\)', 'rotateX'),
            (r'\.rotateY\s*\(\s*math\.radians\s*\(\s*([^)]+)\s*\)\s*\)', 'rotateY'),
            (r'\.rotateZ\s*\(\s*math\.radians\s*\(\s*([^)]+)\s*\)\s*\)', 'rotateZ'),
            (r'\.rotateX\s*\(\s*([^)]+)\s*\)', 'rotateX'),  # Direct angle
            (r'\.rotateY\s*\(\s*([^)]+)\s*\)', 'rotateY'),
            (r'\.rotateZ\s*\(\s*([^)]+)\s*\)', 'rotateZ'),
        ]
        
        # Pattern 2: transformGeometry with Matrix
        # final_shape.transformGeometry(mat)
        transform_geometry_pattern = r'\.transformGeometry\s*\(\s*([a-zA-Z_]\w+)\s*\)'
        
        # Search for transforms after last threaded hole
        code_after_threaded = code_content[last_threaded_pos:]
        
        # Check for transformGeometry first (most common)
        transform_match = re.search(transform_geometry_pattern, code_after_threaded)
        if transform_match:
            matrix_var = transform_match.group(1)
            # Find matrix definition and its rotations
            matrix_pattern = rf'{re.escape(matrix_var)}\s*=\s*App\.Matrix\s*\(\)'
            matrix_match = re.search(matrix_pattern, code_content[:last_threaded_pos + transform_match.end()])
            
            if matrix_match:
                # Find all rotateX/Y/Z calls on this matrix
                matrix_start = matrix_match.start()
                matrix_end = last_threaded_pos + transform_match.end()
                matrix_code = code_content[matrix_start:matrix_end]
                
                for pattern, transform_type in rotate_patterns:
                    rot_match = re.search(rf'{re.escape(matrix_var)}\s*{pattern}', matrix_code)
                    if rot_match:
                        try:
                            angle_expr = rot_match.group(1)
                            # Evaluate angle (handle math.radians)
                            if 'math.radians' in angle_expr:
                                inner_angle = re.search(r'math\.radians\s*\(\s*([^)]+)\s*\)', angle_expr)
                                if inner_angle:
                                    angle_deg = self._safe_eval(inner_angle.group(1), code_content)
                                else:
                                    angle_deg = self._safe_eval(angle_expr, code_content)
                            else:
                                angle_deg = self._safe_eval(angle_expr, code_content)
                            
                            transforms.append({
                                'type': transform_type,
                                'angle': angle_deg
                            })
                            logger.debug(f"[Transform] Detected {transform_type}({angle_deg}°) after threaded holes")
                        except Exception as e:
                            logger.warning(f"[Transform] Failed to parse {transform_type} angle: {e}")
        
        # Apply transforms to positions
        if transforms:
            logger.info(f"[Transform] Applying {len(transforms)} transform(s) to {len(threaded_holes)} threaded hole position(s)")
            
            for hole in threaded_holes:
                pos = hole.get('position', {'x': 0, 'y': 0, 'z': 0})
                x, y, z = pos.get('x', 0), pos.get('y', 0), pos.get('z', 0)
                
                # Apply transforms in order
                for transform in transforms:
                    transform_type = transform['type']
                    angle = transform.get('angle', 0)
                    
                    if transform_type == 'rotateX':
                        # rotateX: X unchanged, Y -> Y*cos - Z*sin, Z -> Y*sin + Z*cos
                        import math
                        angle_rad = math.radians(angle)
                        cos_a = math.cos(angle_rad)
                        sin_a = math.sin(angle_rad)
                        y_new = y * cos_a - z * sin_a
                        z_new = y * sin_a + z * cos_a
                        y, z = y_new, z_new
                    elif transform_type == 'rotateY':
                        # rotateY: Y unchanged, X -> X*cos + Z*sin, Z -> -X*sin + Z*cos
                        import math
                        angle_rad = math.radians(angle)
                        cos_a = math.cos(angle_rad)
                        sin_a = math.sin(angle_rad)
                        x_new = x * cos_a + z * sin_a
                        z_new = -x * sin_a + z * cos_a
                        x, z = x_new, z_new
                    elif transform_type == 'rotateZ':
                        # rotateZ: Z unchanged, X -> X*cos - Y*sin, Y -> X*sin + Y*cos
                        import math
                        angle_rad = math.radians(angle)
                        cos_a = math.cos(angle_rad)
                        sin_a = math.sin(angle_rad)
                        x_new = x * cos_a - y * sin_a
                        y_new = x * sin_a + y * cos_a
                        x, y = x_new, y_new
                
                # Update position
                hole['position'] = {'x': round(x, 3), 'y': round(y, 3), 'z': round(z, 3)}
                logger.debug(f"[Transform] Transformed position: {pos} -> {hole['position']}")
        
        return threaded_holes
    
    def _detect_threaded_loop_patterns(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Detect threaded holes created in loops.
        
        Pattern:
        ```python
        positions = [(x1, y1), (x2, y2), ...]
        for (x, y) in positions:
            threaded_hole = Part.makeThreaded(radius, depth, App.Vector(x, y, z), direction)
        ```
        
        Returns:
            List of threaded hole metadata for each position in the loop
        """
        threaded_holes = []
        
        # Pattern 1: for (x, y) in positions_list: or for(x, y) in positions_list:
        #   threaded_hole = Part.makeThreaded(radius, depth, App.Vector(x, y, z), direction)
        # First, find all loops with positions list (handle both with and without space after 'for')
        loop_header_pattern = r'for\s*\(([^,]+),\s*([^)]+)\)\s+in\s+([a-zA-Z_]\w*)\s*:'
        
        for loop_match in re.finditer(loop_header_pattern, code_content):
            try:
                x_var, y_var, positions_var = loop_match.groups()
                x_var = x_var.strip()
                y_var = y_var.strip()
                
                # Find the loop body (until next unindented line or end of block)
                loop_start = loop_match.end()
                loop_body_start = code_content.find('\n', loop_start) + 1
                if loop_body_start == 0:
                    continue
                
                # Find end of loop body (next line with same or less indentation)
                loop_body_end = loop_body_start
                indent_level = 0
                for i in range(loop_body_start, min(loop_body_start + 2000, len(code_content))):
                    if code_content[i] == '\n':
                        line_start = i + 1
                        if line_start >= len(code_content):
                            loop_body_end = len(code_content)
                            break
                        # Check next line
                        line_end = code_content.find('\n', line_start)
                        if line_end == -1:
                            line_end = len(code_content)
                        line = code_content[line_start:line_end]
                        # If line is empty or has less indentation, we've reached end of loop
                        if not line.strip() or (line and not (line.startswith(' ') or line.startswith('\t'))):
                            loop_body_end = i
                            break
                        # Count leading spaces/tabs
                        leading = len(line) - len(line.lstrip())
                        if i == loop_body_start:
                            indent_level = leading
                        elif leading <= indent_level and line.strip():
                            loop_body_end = i
                            break
                
                loop_body = code_content[loop_body_start:loop_body_end]
                
                # Check if loop body contains Part.makeThreaded
                if 'Part.makeThreaded' not in loop_body:
                    continue
                
                # Find makeThreaded call in loop body
                make_threaded_pattern = (
                    r'Part\.makeThreaded\s*\(\s*'
                    r'([^,]+),\s*'  # radius
                    r'([^,]+),\s*'  # depth
                    r'App\.Vector\s*\(\s*' + re.escape(x_var) + r'\s*,\s*' + re.escape(y_var) + r'\s*,\s*([^)]+)\s*\)\s*,\s*'  # position
                    r'([^)]+)'      # direction
                    r'\s*\)'
                )
                
                threaded_match = re.search(make_threaded_pattern, loop_body)
                if not threaded_match:
                    continue
                
                radius_expr, depth_expr, z_expr, dir_expr = threaded_match.groups()
                
                # Find the positions list definition
                # Pattern: positions_var = [(x1, y1), (x2, y2), ...]
                # Also handle multi-line lists and variable references
                positions_pattern = rf'{re.escape(positions_var)}\s*=\s*\[(.*?)\]'
                positions_match = re.search(positions_pattern, code_content, re.DOTALL)
                
                if not positions_match:
                    # Try to find if positions_var is defined elsewhere (could be a variable reference)
                    # Look for: positions_var = some_other_var or positions_var = func_call()
                    var_def_pattern = rf'{re.escape(positions_var)}\s*=\s*([a-zA-Z_]\w*)'
                    var_def_match = re.search(var_def_pattern, code_content)
                    if var_def_match:
                        # Try to find the referenced variable's definition
                        ref_var = var_def_match.group(1)
                        ref_pattern = rf'{re.escape(ref_var)}\s*=\s*\[(.*?)\]'
                        ref_match = re.search(ref_pattern, code_content, re.DOTALL)
                        if ref_match:
                            positions_match = ref_match
                    
                    if not positions_match:
                        logger.warning(f"[ThreadedLoop] Could not find positions list '{positions_var}'")
                        continue
                
                # Parse positions from list
                positions_str = positions_match.group(1)
                # Pattern: (x, y) or (x_expr, y_expr) - handle multi-line
                # Remove comments from positions_str
                positions_str_clean = re.sub(r'#.*?$', '', positions_str, flags=re.MULTILINE)
                tuple_pattern = r'\(\s*([^,)]+)\s*,\s*([^)]+)\s*\)'
                position_tuples = re.findall(tuple_pattern, positions_str_clean)
                
                if not position_tuples:
                    logger.warning(f"[ThreadedLoop] No positions found in list '{positions_var}'")
                    continue
                
                # Evaluate radius, depth, z, direction (once for all positions)
                radius = self._safe_eval(radius_expr.strip(), code_content)
                depth = self._safe_eval(depth_expr.strip(), code_content)
                z = self._safe_eval(z_expr.strip(), code_content)
                
                # Extract thread type from comment near the loop
                # Search 5 lines before and after the loop
                search_start = max(0, code_content.rfind('\n', 0, loop_match.start() - 500))
                search_end = min(len(code_content), code_content.find('\n', loop_body_end + 500))
                search_text = code_content[search_start:search_end]
                
                thread_match = re.search(r'#.*?(M\d+(?:\.\d+)?)', search_text, re.IGNORECASE)
                thread_type = thread_match.group(1).upper() if thread_match else None
                
                # Create a threaded hole for each position
                holes_from_this_loop = 0
                for x_expr, y_expr in position_tuples:
                    try:
                        x = self._safe_eval(x_expr.strip(), code_content)
                        y = self._safe_eval(y_expr.strip(), code_content)
                        
                        position = {'x': x, 'y': y, 'z': z}
                        diameter = radius * 2
                        
                        threaded_holes.append({
                            "position": position,
                            "diameter": diameter,
                            "radius": radius,
                            "thread_type": thread_type,
                            "depth": depth,
                            "confidence": 0.95 if thread_type else 0.90,
                            "source": "Part.makeThreaded_loop"
                        })
                        holes_from_this_loop += 1
                        
                        logger.debug(f"[ThreadedLoop] Found threaded hole in loop: thread={thread_type}, pos=({x}, {y}, {z})")
                    except Exception as e:
                        logger.warning(f"[ThreadedLoop] Failed to parse position ({x_expr}, {y_expr}): {e}")
                        continue
                
                logger.info(f"[ThreadedLoop] Detected {holes_from_this_loop} threaded holes from loop with {len(position_tuples)} positions")
                
            except Exception as e:
                logger.warning(f"[ThreadedLoop] Failed to parse loop pattern: {e}")
                continue
        
        # Pattern 2: for i in range(N): with calculated positions
        # Pattern: for i in range(N):
        #   px = center_x + hole_circle_rad * math.sin(theta)  # Complex expressions
        #   py = center_y + hole_circle_rad * math.cos(theta)
        #   threaded_hole = Part.makeThreaded(..., App.Vector(px, py, z), ...)
        range_loop_header_pattern = r'for\s+(\w+)\s+in\s+range\s*\(\s*([^)]+)\s*\)\s*:'
        
        for range_match in re.finditer(range_loop_header_pattern, code_content):
            try:
                i_var, range_expr = range_match.groups()
                i_var = i_var.strip()
                
                # Find the loop body
                loop_start = range_match.end()
                loop_body_start = code_content.find('\n', loop_start) + 1
                if loop_body_start == 0:
                    continue
                
                # Find end of loop body
                loop_body_end = loop_body_start
                indent_level = None
                for i in range(loop_body_start, min(loop_body_start + 2000, len(code_content))):
                    if code_content[i] == '\n':
                        line_start = i + 1
                        if line_start >= len(code_content):
                            loop_body_end = len(code_content)
                            break
                        line_end = code_content.find('\n', line_start)
                        if line_end == -1:
                            line_end = len(code_content)
                        line = code_content[line_start:line_end]
                        
                        if not line.strip():
                            continue
                        
                        leading = len(line) - len(line.lstrip())
                        if indent_level is None:
                            indent_level = leading
                        
                        if leading <= indent_level and line.strip() and not (line.startswith(' ') or line.startswith('\t')):
                            loop_body_end = i
                            break
                
                loop_body = code_content[loop_body_start:loop_body_end]
                
                # Check if loop body contains Part.makeThreaded
                if 'Part.makeThreaded' not in loop_body:
                    continue
                
                # Find makeThreaded call in loop body
                make_threaded_pattern = (
                    r'Part\.makeThreaded\s*\(\s*'
                    r'([^,]+),\s*'  # radius
                    r'([^,]+),\s*'  # depth
                    r'App\.Vector\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)\s*,\s*'  # position (px, py, z)
                    r'([^)]+)'      # direction
                    r'\s*\)'
                )
                
                threaded_match = re.search(make_threaded_pattern, loop_body)
                if not threaded_match:
                    continue
                
                radius_expr, depth_expr, x_var_in_vector, y_var_in_vector, z_expr, dir_expr = threaded_match.groups()
                x_var_in_vector = x_var_in_vector.strip()
                y_var_in_vector = y_var_in_vector.strip()
                
                # Find px and py variable assignments in loop body (before makeThreaded call)
                # Pattern: px = expression or x_var_in_vector = expression
                # Need to find assignments in order (theta might be defined before px, py)
                px_pattern = rf'({re.escape(x_var_in_vector)})\s*=\s*([^\n]+)'
                py_pattern = rf'({re.escape(y_var_in_vector)})\s*=\s*([^\n]+)'
                
                px_match = re.search(px_pattern, loop_body)
                py_match = re.search(py_pattern, loop_body)
                
                if not px_match or not py_match:
                    logger.warning(f"[ThreadedLoop] Could not find px/py assignments in range loop (px={x_var_in_vector}, py={y_var_in_vector})")
                    continue
                
                px_expr = px_match.group(2).strip()
                py_expr = py_match.group(2).strip()
                
                # Remove comments from expressions
                px_expr = re.sub(r'#.*$', '', px_expr).strip()
                py_expr = re.sub(r'#.*$', '', py_expr).strip()
                
                # Find intermediate variables used in px/py (e.g., theta)
                # Extract all variable names from px_expr and py_expr
                all_vars = set(re.findall(r'\b([a-zA-Z_]\w*)\b', px_expr + ' ' + py_expr))
                # Remove known functions and constants
                all_vars -= {'math', 'sin', 'cos', 'tan', 'radians', 'degrees', 'pi', 'e', 'sqrt', 'pow', 'abs', 'round', 'min', 'max', i_var}
                
                # Find assignments for these variables in loop body (before px/py assignments)
                loop_body_before_px = loop_body[:px_match.start()]
                intermediate_vars = {}
                for var in all_vars:
                    var_pattern = rf'{re.escape(var)}\s*=\s*([^\n]+)'
                    var_match = re.search(var_pattern, loop_body_before_px)
                    if var_match:
                        var_expr = var_match.group(1).strip()
                        var_expr = re.sub(r'#.*$', '', var_expr).strip()
                        intermediate_vars[var] = var_expr
                        logger.debug(f"[ThreadedLoop] Found intermediate variable {var} = {var_expr}")
                
                # Evaluate range
                range_val = self._safe_eval(range_expr.strip(), code_content)
                if not isinstance(range_val, (int, float)) or range_val <= 0:
                    logger.warning(f"[ThreadedLoop] Invalid range value: {range_expr}")
                    continue
                
                # Evaluate radius, depth, z (once)
                radius = self._safe_eval(radius_expr.strip(), code_content)
                depth = self._safe_eval(depth_expr.strip(), code_content)
                z = self._safe_eval(z_expr.strip(), code_content)
                
                # Extract thread type
                search_start = max(0, code_content.rfind('\n', 0, range_match.start() - 500))
                search_end = min(len(code_content), code_content.find('\n', loop_body_end + 500))
                search_text = code_content[search_start:search_end]
                thread_match = re.search(r'#.*?(M\d+(?:\.\d+)?)', search_text, re.IGNORECASE)
                thread_type = thread_match.group(1).upper() if thread_match else None
                
                # Get context before loop for variable resolution (for math.sin, math.cos, etc.)
                context_before_loop = code_content[:range_match.start()]
                
                # Create holes for each iteration
                holes_from_this_loop = 0
                for i_val in range(int(range_val)):
                    try:
                        # Build local_vars dict with i_var and intermediate variables
                        local_vars = {i_var: i_val}
                        
                        # Evaluate intermediate variables first (e.g., theta)
                        for var_name, var_expr in intermediate_vars.items():
                            # Replace i_var in intermediate expression
                            var_expr_sub = re.sub(rf'\b{re.escape(i_var)}\b', str(i_val), var_expr)
                            var_value = self._safe_eval_with_math(var_expr_sub, code_content, local_vars)
                            local_vars[var_name] = var_value
                            logger.debug(f"[ThreadedLoop] i={i_val}: {var_name} = {var_value:.4f}")
                        
                        # Replace loop variable in px/py expressions
                        px_eval_sub = re.sub(rf'\b{re.escape(i_var)}\b', str(i_val), px_expr)
                        py_eval_sub = re.sub(rf'\b{re.escape(i_var)}\b', str(i_val), py_expr)
                        
                        # Evaluate expressions with math functions support
                        px_result = self._safe_eval_with_math(px_eval_sub, code_content, local_vars)
                        py_result = self._safe_eval_with_math(py_eval_sub, code_content, local_vars)
                        
                        if px_result == 0.0 and py_result == 0.0:
                            logger.warning(f"[ThreadedLoop] Failed to evaluate px/py for i={i_val}, expressions: px={px_expr}, py={py_expr}")
                            continue
                        
                        position = {'x': float(px_result), 'y': float(py_result), 'z': float(z)}
                        diameter = radius * 2
                        
                        threaded_holes.append({
                            "position": position,
                            "diameter": diameter,
                            "radius": radius,
                            "thread_type": thread_type,
                            "depth": depth,
                            "confidence": 0.90 if thread_type else 0.85,
                            "source": "Part.makeThreaded_range_loop"
                        })
                        holes_from_this_loop += 1
                        
                        logger.debug(f"[ThreadedLoop] Found threaded hole in range loop (i={i_val}): thread={thread_type}, pos=({px_result:.2f}, {py_result:.2f}, {z})")
                    except Exception as e:
                        logger.warning(f"[ThreadedLoop] Failed to calculate position for i={i_val}: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                        continue
                
                logger.info(f"[ThreadedLoop] Detected {holes_from_this_loop} threaded holes from range loop (expected {int(range_val)})")
                
            except Exception as e:
                logger.warning(f"[ThreadedLoop] Failed to parse range loop pattern: {e}")
                continue
        
        return threaded_holes
    
    def _analyze_oblongs_with_regex(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Regex-based analysis for oblongs (Part.makeOblong calls).
        Handles both inline vectors and vector variables.
        Supports keyword arguments (e.g. length=10, pnt=App.Vector(...)).
        
        [ENHANCED] Now detects oblongs in helper functions and traces calls.
        """
        oblongs = []
        kwarg_p = r'(?:[a-zA-Z_]\w*\s*=\s*)?'  # Optional keyword argument prefix
        
        # [NEW] STEP 0: Detect helper functions that create oblongs
        # Pattern: def func_name(...): ... Part.makeOblong(...) ... return tool
        helper_functions = self._detect_oblong_helper_functions(code_content)
        
        if helper_functions:
            logger.info(f"[OblongAnalyzer] Found {len(helper_functions)} helper function(s) creating oblongs")
            # Process function calls
            for func_name, func_info in helper_functions.items():
                func_calls = self._find_function_calls(code_content, func_name)
                logger.info(f"[OblongAnalyzer] Function '{func_name}' called {len(func_calls)} time(s)")
                
                for call in func_calls:
                    oblong = self._parse_oblong_from_function_call(
                        func_name, call, func_info, code_content
                    )
                    if oblong:
                        oblongs.append(oblong)
                        logger.debug(f"[OblongAnalyzer] Parsed oblong from {func_name}() call")
        
        # Pattern 1: Inline vectors
        # Part.makeOblong(length, width, height, App.Vector(x, y, z), App.Vector(dx, dy, dz))
        # Supports: Part.makeOblong(length=l, width=w, height=h, pnt=App.Vector(x,y,z), dir=App.Vector(dx,dy,dz))
        pattern1 = (
            r'Part\.makeOblong\s*\(\s*'
            rf'{kwarg_p}([^,]+),\s*'                      # length
            rf'{kwarg_p}([^,]+),\s*'                      # width
            rf'{kwarg_p}([^,]+),\s*'                      # height
            rf'{kwarg_p}App\.Vector\s*\(\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^)]+)\s*\)\s*,\s*' # pnt
            rf'{kwarg_p}App\.Vector\s*\(\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^)]+)\s*\)'        # dir
        )
        
        for match in re.finditer(pattern1, code_content, re.DOTALL):
            try:
                # Skip if this makeOblong is inside a function definition
                match_pos = match.start()
                # Find the line containing this match
                line_start = code_content.rfind('\n', 0, match_pos) + 1
                line_end = code_content.find('\n', match_pos)
                if line_end == -1:
                    line_end = len(code_content)
                
                # Check if we're inside a function by looking for indentation
                line = code_content[line_start:line_end]
                if line.startswith('    ') or line.startswith('\t'):
                    # This is indented, likely inside a function
                    # Check if we're inside a DETECTED helper function
                    # (we want to skip makeOblong in helper functions since we process their calls instead)
                    context_before = code_content[max(0, match_pos - 500):match_pos]
                    
                    # Check if any detected helper function contains this position
                    skip_this = False
                    for helper_name in helper_functions.keys():
                        # Check if we're inside this helper function definition
                        helper_pattern = rf'def\s+{re.escape(helper_name)}\s*\('
                        if re.search(helper_pattern, context_before):
                            logger.debug(f"[OblongAnalyzer] Skipping makeOblong inside helper function {helper_name}")
                            skip_this = True
                            break
                    
                    if skip_this:
                        continue
                
                length_expr, width_expr, height_expr, x_expr, y_expr, z_expr, dx_expr, dy_expr, dz_expr = match.groups()
                
                oblong = self._parse_oblong_params(
                    length_expr, width_expr, height_expr,
                    x_expr, y_expr, z_expr,
                    dx_expr, dy_expr, dz_expr,
                    code_content
                )
                if oblong:
                    oblongs.append(oblong)
                    
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeOblong (inline): {e}")
                continue
        
        # Pattern 2: Vector variables
        # Part.makeOblong(length, width, height, vector_var, App.Vector(dx, dy, dz))
        pattern2 = (
            r'Part\.makeOblong\s*\(\s*'
            rf'{kwarg_p}([^,]+),\s*'                      # length
            rf'{kwarg_p}([^,]+),\s*'                      # width
            rf'{kwarg_p}([^,]+),\s*'                      # height
            rf'{kwarg_p}([a-zA-Z_]\w*)\s*,\s*'            # vector variable
            rf'{kwarg_p}App\.Vector\s*\(\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^)]+)\s*\)' # dir
        )
        
        for match in re.finditer(pattern2, code_content, re.DOTALL):
            try:
                length_expr, width_expr, height_expr, vec_var, dx_expr, dy_expr, dz_expr = match.groups()
                
                # Extract vector variable value
                # Also handle kwargs in vector definition: v = App.Vector(x=1, y=2, z=3)
                vec_pattern = rf'{re.escape(vec_var)}\s*=\s*App\.Vector\s*\(\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^,]+),\s*{kwarg_p}([^)]+)\s*\)'
                vec_match = re.search(vec_pattern, code_content)
                
                if vec_match:
                    x_expr, y_expr, z_expr = vec_match.groups()
                    oblong = self._parse_oblong_params(
                        length_expr, width_expr, height_expr,
                        x_expr, y_expr, z_expr,
                        dx_expr, dy_expr, dz_expr,
                        code_content
                    )
                    if oblong:
                        oblongs.append(oblong)
                        
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeOblong (vector var): {e}")
                continue
        
        # 🎯 PARSE TRANSFORMS (rotate + translate)
        # Handle oblongs created at origin then transformed
        oblongs = self._apply_transforms_to_oblongs(oblongs, code_content)
        
        # 🎯 DEDUPLICATE OBLONGS
        # Remove duplicates based on center position (within 0.1mm tolerance)
        unique_oblongs = []
        seen_centers = []
        
        for oblong in oblongs:
            center = oblong.get('center', {})
            cx, cy, cz = center.get('x', 0), center.get('y', 0), center.get('z', 0)
            
            # Check if this center already exists
            is_duplicate = False
            for seen_cx, seen_cy, seen_cz in seen_centers:
                if (abs(cx - seen_cx) < 0.1 and 
                    abs(cy - seen_cy) < 0.1 and 
                    abs(cz - seen_cz) < 0.1):
                    is_duplicate = True
                    logger.debug(f"[FeatureAnalyzer] Skipping duplicate oblong at ({cx}, {cy}, {cz})")
                    break
            
            if not is_duplicate:
                unique_oblongs.append(oblong)
                seen_centers.append((cx, cy, cz))
        
        if len(unique_oblongs) < len(oblongs):
            logger.info(f"[FeatureAnalyzer] Removed {len(oblongs) - len(unique_oblongs)} duplicate oblong(s)")
        
        return unique_oblongs
    
    def _detect_oblong_helper_functions(self, code_content: str) -> Dict[str, Dict]:
        """
        Detect helper functions that create oblongs.
        
        Returns:
            Dict mapping function_name -> {params, has_rotate, rotate_around_origin}
        """
        import re
        
        helper_funcs = {}
        
        # Pattern: def func_name(...): ... Part.makeOblong(...) ... [tool.rotate(...)] ... return tool
        func_pattern = r'def\s+(\w+)\s*\(([^)]*)\):\s*(.*?)(?=\ndef\s|\nclass\s|\Z)'
        
        for match in re.finditer(func_pattern, code_content, re.DOTALL):
            func_name = match.group(1)
            params_str = match.group(2)
            func_body = match.group(3)
            
            # Check if function contains Part.makeOblong
            if 'Part.makeOblong' not in func_body:
                continue
            
            # Parse parameters
            params = [p.strip().split('=')[0].strip() for p in params_str.split(',') if p.strip()]
            
            # Check for rotation
            has_rotate = '.rotate(' in func_body
            rotate_around_origin = False
            
            if has_rotate:
                # Check if rotation is around origin (0, 0, 0)
                rotate_around_origin = 'App.Vector(0, 0, 0)' in func_body or 'App.Vector(0,0,0)' in func_body
            
            helper_funcs[func_name] = {
                'params': params,
                'has_rotate': has_rotate,
                'rotate_around_origin': rotate_around_origin,
                'body': func_body
            }
            
            logger.debug(f"[OblongAnalyzer] Detected helper function: {func_name}({', '.join(params)}), rotate={has_rotate}")
        
        return helper_funcs
    
    def _find_function_calls(self, code_content: str, func_name: str) -> List[Dict]:
        """
        Find all calls to a specific function.
        
        [FIXED] Now properly handles keyword arguments (e.g., func(cx=20, cy=25))
        
        Returns:
            List of dicts with 'args' (list of evaluated argument values)
        """
        import re
        
        calls = []
        
        # Pattern: func_name(arg1, arg2, ...)
        pattern = rf'{re.escape(func_name)}\s*\(([^)]*)\)'
        
        for match in re.finditer(pattern, code_content):
            # Check if this is a function definition by looking at context
            start_pos = match.start()
            # Look back up to 10 characters for "def "
            context_start = max(0, start_pos - 10)
            context = code_content[context_start:start_pos]
            
            if 'def ' in context:
                # This is a function definition, skip it
                continue
            
            args_str = match.group(1)
            
            # Split by comma (simple approach - may fail with nested calls)
            arg_exprs = [arg.strip() for arg in args_str.split(',') if arg.strip()]
            
            # [FIXED] Handle both positional and keyword arguments
            evaluated_args = []
            for arg_expr in arg_exprs:
                try:
                    # Check if keyword argument (e.g., "cx=20")
                    if '=' in arg_expr:
                        # Extract value part after '='
                        value_expr = arg_expr.split('=', 1)[1].strip()
                        val = self._safe_eval(value_expr, code_content)
                    else:
                        # Positional argument
                        val = self._safe_eval(arg_expr, code_content)
                    
                    evaluated_args.append(val)
                except:
                    # If eval fails, keep as string
                    evaluated_args.append(arg_expr)
            
            calls.append({'args': evaluated_args, 'raw_args': arg_exprs})
        
        return calls
    
    def _parse_oblong_from_function_call(self, func_name: str, call: Dict, 
                                         func_info: Dict, code_content: str) -> Optional[Dict]:
        """
        Parse oblong parameters from a function call.
        
        For helper functions that create oblongs with rotation around origin,
        we need to:
        1. Extract the call arguments (cx, cy, total_len, width, depth)
        2. Calculate the ACTUAL position after rotation
        3. Return oblong with correct bbox and center
        """
        import math
        
        try:
            args = call['args']  # Already evaluated
            raw_args = call.get('raw_args', [])  # Raw arg expressions
            params = func_info['params']
            
            logger.debug(f"[OblongAnalyzer] Parsing {func_name} call:")
            logger.debug(f"  Params: {params}")
            logger.debug(f"  Args: {args}")
            logger.debug(f"  Raw args: {raw_args}")
            
            # [FIXED] Map arguments to parameters, handling keyword args
            arg_map = {}
            
            for i, (raw_arg, value) in enumerate(zip(raw_args, args)):
                # Check if keyword argument (e.g., "cx=20")
                if '=' in raw_arg:
                    # Extract parameter name
                    param_name = raw_arg.split('=')[0].strip()
                    arg_map[param_name] = value
                    logger.debug(f"  {param_name} = {value} (keyword)")
                else:
                    # Positional argument - map by index
                    if i < len(params):
                        arg_map[params[i]] = value
                        logger.debug(f"  {params[i]} = {value} (positional)")
            
            # Common parameter names
            cx = arg_map.get('cx', arg_map.get('center_x', 0))
            cy = arg_map.get('cy', arg_map.get('center_y', 0))
            total_len = arg_map.get('total_len', arg_map.get('total_length', arg_map.get('length', 0)))
            width = arg_map.get('width', 0)
            depth = arg_map.get('depth', arg_map.get('height', 7.0))
            start_z = arg_map.get('start_z', -1.0)
            
            logger.debug(f"  Mapped: cx={cx}, cy={cy}, total_len={total_len}, width={width}, depth={depth}, start_z={start_z}")
            
            if total_len == 0 or width == 0:
                logger.warning(f"[OblongAnalyzer] Invalid dimensions for {func_name} call: len={total_len}, width={width}")
                return None
            
            # Calculate straight length
            straight_length = total_len - width
            fillet_radius = width / 2.0
            
            # If function rotates around origin, the final position is (cx, cy)
            # The oblong is created at a calculated position, then rotated 90°, ending at (cx, cy)
            if func_info.get('rotate_around_origin'):
                # After 90° rotation around origin:
                # Final center = (cx, cy) as specified in the call
                # This is the ACTUAL position we want
                
                center = {'x': cx, 'y': cy, 'z': start_z + depth / 2.0}
                
                # For vertical oblong (rotated 90°), bbox is:
                # Width along X, Length along Y
                bbox = {
                    'min': {
                        'x': cx - width / 2.0,
                        'y': cy - total_len / 2.0,
                        'z': start_z
                    },
                    'max': {
                        'x': cx + width / 2.0,
                        'y': cy + total_len / 2.0,
                        'z': start_z + depth
                    }
                }
                
                # Corner for vertical oblong
                corner = {
                    'x': cx - width / 2.0,
                    'y': cy - total_len / 2.0,
                    'z': start_z
                }
                
                logger.info(f"[OblongAnalyzer] Vertical oblong at ({cx}, {cy}): bbox X=[{bbox['min']['x']:.1f}, {bbox['max']['x']:.1f}], Y=[{bbox['min']['y']:.1f}, {bbox['max']['y']:.1f}]")
                
            else:
                # No rotation or rotation around center - use standard calculation
                center = {'x': cx, 'y': cy, 'z': start_z + depth / 2.0}
                
                bbox = {
                    'min': {
                        'x': cx - total_len / 2.0,
                        'y': cy - width / 2.0,
                        'z': start_z
                    },
                    'max': {
                        'x': cx + total_len / 2.0,
                        'y': cy + width / 2.0,
                        'z': start_z + depth
                    }
                }
                
                corner = {
                    'x': cx - total_len / 2.0,
                    'y': cy - width / 2.0,
                    'z': start_z
                }
            
            return {
                'straight_length': straight_length,
                'width': width,
                'total_length': total_len,
                'fillet_radius': fillet_radius,
                'depth': depth,
                'corner': corner,
                'direction': {'x': 0.0, 'y': 0.0, 'z': 1.0},
                'bounding_box': bbox,
                'center': center,
                'expected_faces': {'planar': 2, 'cylindrical': 4, 'total': 6},
                'geometry_type': 'filleted_rectangle',
                'confidence': 0.95,
                'source': f'{func_name}_call',
                'units': 'mm',
                'id': f'oblong_{hash(str(center)) % 10000:04x}',
                'is_through_cut': start_z < 0,
                'cut_direction': 'z_up',
                '_skip_transform': True  # Already transformed in helper function
            }
            
        except Exception as e:
            logger.warning(f"[OblongAnalyzer] Failed to parse {func_name} call: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _apply_transforms_to_oblongs(self, oblongs: List[Dict], code_content: str) -> List[Dict]:
        """
        Apply rotate() and translate() transforms to oblong centers.
        
        [ENHANCED] Now handles:
        1. .copy() pattern
        2. .rotate() + .translate() pattern (CRITICAL for vertical oblongs!)
        """
        import math
        
        # Step 1: Find ALL rotate() calls with rotation center
        # Pattern: var.rotate(App.Vector(cx, cy, cz), axis_vector, angle)
        # We need to extract the rotation center (cx, cy, cz) and angle
        rotate_pattern = r'(\w+)\.rotate\s*\(\s*App\.Vector\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\s*\)\s*,\s*App\.Vector\([^)]+\)\s*,\s*([^)]+)\s*\)'
        
        rotate_calls = {}  # var_name -> {'angle': float, 'center': (x, y, z)}
        for match in re.finditer(rotate_pattern, code_content):
            var_name = match.group(1)
            cx_expr = match.group(2).strip()
            cy_expr = match.group(3).strip()
            cz_expr = match.group(4).strip()
            angle_expr = match.group(5).strip()
            
            try:
                angle = self._safe_eval(angle_expr, code_content)
                cx = self._safe_eval(cx_expr, code_content)
                cy = self._safe_eval(cy_expr, code_content)
                cz = self._safe_eval(cz_expr, code_content)
                
                rotate_calls[var_name] = {
                    'angle': angle,
                    'center': (cx, cy, cz)
                }
                logger.debug(f"[Transform] Found rotate for {var_name}: {angle}° around ({cx}, {cy}, {cz})")
            except Exception as e:
                logger.warning(f"[Transform] Failed to eval rotate for {var_name}: {e}")
        
        # Step 2: Find ALL translate() calls
        translate_pattern = r'(\w+)\.translate\s*\(\s*App\.Vector\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\s*\)\s*\)'
        
        translate_calls = {}
        for match in re.finditer(translate_pattern, code_content):
            var_name = match.group(1)
            dx_expr = match.group(2).strip()
            dy_expr = match.group(3).strip()
            dz_expr = match.group(4).strip()
            
            try:
                dx = self._safe_eval(dx_expr, code_content)
                dy = self._safe_eval(dy_expr, code_content)
                dz = self._safe_eval(dz_expr, code_content)
                
                translate_calls[var_name] = (dx, dy, dz)
                logger.debug(f"[Transform] Found translate for {var_name}: ({dx}, {dy}, {dz})")
            except Exception as e:
                logger.warning(f"[Transform] Failed to eval translate for {var_name}: {e}")
        
        # Step 3: Find .Placement assignments
        # Pattern: var.Placement = App.Placement(App.Vector(x, y, z), ...)
        placement_pattern = r'(\w+)\.Placement\s*=\s*App\.Placement\s*\(\s*App\.Vector\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\s*\)'
        
        for match in re.finditer(placement_pattern, code_content):
            var_name = match.group(1)
            x_expr = match.group(2).strip()
            y_expr = match.group(3).strip()
            z_expr = match.group(4).strip()
            
            try:
                x = self._safe_eval(x_expr, code_content)
                y = self._safe_eval(y_expr, code_content)
                z = self._safe_eval(z_expr, code_content)
                
                # Placement sets absolute position, not relative
                # Store as absolute position, will handle differently
                translate_calls[var_name] = (x, y, z, True)  # True = absolute position
                logger.debug(f"[Transform] Found Placement for {var_name}: ({x}, {y}, {z}) [absolute]")
            except Exception as e:
                logger.warning(f"[Transform] Failed to eval Placement for {var_name}: {e}")
        
        if not translate_calls and not rotate_calls:
            return oblongs
        
        # Step 4: Build variable relationship map (handle .copy())
        # Pattern: var2 = var1.copy()
        copy_pattern = r'(\w+)\s*=\s*(\w+)\.copy\s*\(\s*\)'
        var_relationships = {}  # var2 -> var1 (var2 is copy of var1)
        
        for match in re.finditer(copy_pattern, code_content):
            target_var = match.group(1)
            source_var = match.group(2)
            var_relationships[target_var] = source_var
            logger.debug(f"[Transform] Found copy: {target_var} = {source_var}.copy()")
        
        # Step 3: Find all makeOblong variable assignments
        var_pattern = r'(\w+)\s*=\s*Part\.makeOblong'
        var_matches = list(re.finditer(var_pattern, code_content))
        
        # Step 4: Match oblongs to makeOblong variables
        if len(var_matches) != len(oblongs):
            logger.warning(f"[Transform] Variable count mismatch: {len(var_matches)} vars vs {len(oblongs)} oblongs")
            # Still try to apply transforms
        
        # Step 5: Apply transforms
        for i, oblong in enumerate(oblongs):
            # Skip oblongs that are already transformed (e.g., from helper functions)
            if oblong.get('_skip_transform'):
                logger.debug(f"[Transform] Skipping {oblong.get('id')} - already transformed in helper function")
                continue
            
            if i >= len(var_matches):
                break
            
            makeoblong_var = var_matches[i].group(1)
            
            # Find which variable has the translate (could be makeoblong_var or a copy of it)
            transform_var = None
            transform_delta = None
            
            # Check direct translate on makeoblong_var
            if makeoblong_var in translate_calls:
                transform_var = makeoblong_var
                transform_delta = translate_calls[makeoblong_var]
            else:
                # Check if any copied variable has translate
                for copy_var, source_var in var_relationships.items():
                    if source_var == makeoblong_var and copy_var in translate_calls:
                        transform_var = copy_var
                        transform_delta = translate_calls[copy_var]
                        logger.debug(f"[Transform] Found translate on copy: {copy_var} (copy of {makeoblong_var})")
                        break
            
            # Find rotation for this variable
            rotation_info = None
            if makeoblong_var in rotate_calls:
                rotation_info = rotate_calls[makeoblong_var]
            else:
                # Check if any copied variable has rotate
                for copy_var, source_var in var_relationships.items():
                    if source_var == makeoblong_var and copy_var in rotate_calls:
                        rotation_info = rotate_calls[copy_var]
                        logger.debug(f"[Transform] Found rotate on copy: {copy_var} (copy of {makeoblong_var})")
                        break
            
            # STEP 1: Apply rotation if present (rotate bbox around rotation center)
            if rotation_info:
                rotation_angle = rotation_info['angle']
                rotation_center = rotation_info['center']
                cx, cy, cz = rotation_center
                
                logger.info(f"[Transform] Applying rotation {rotation_angle}° around ({cx}, {cy}, {cz}) to {oblong.get('id')}")
                
                angle_rad = math.radians(rotation_angle)
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)
                
                # Rotate bbox around rotation center (not origin!)
                # Algorithm: translate to origin, rotate, translate back
                bbox = oblong.get('bounding_box', {})
                if bbox:
                    min_x, min_y = bbox['min']['x'], bbox['min']['y']
                    max_x, max_y = bbox['max']['x'], bbox['max']['y']
                    
                    # Rotate all 4 corners around rotation center
                    corners = [
                        (min_x, min_y),
                        (max_x, min_y),
                        (min_x, max_y),
                        (max_x, max_y)
                    ]
                    
                    rotated_corners = []
                    for x, y in corners:
                        # Translate to origin
                        x_rel = x - cx
                        y_rel = y - cy
                        
                        # Rotate around origin
                        x_rot = x_rel * cos_a - y_rel * sin_a
                        y_rot = x_rel * sin_a + y_rel * cos_a
                        
                        # Translate back
                        new_x = x_rot + cx
                        new_y = y_rot + cy
                        rotated_corners.append((new_x, new_y))
                    
                    # Find new bbox from rotated corners
                    xs = [c[0] for c in rotated_corners]
                    ys = [c[1] for c in rotated_corners]
                    
                    bbox['min']['x'] = min(xs)
                    bbox['min']['y'] = min(ys)
                    bbox['max']['x'] = max(xs)
                    bbox['max']['y'] = max(ys)
                    
                    logger.debug(f"[Transform] Rotated bbox (before translation): X=[{bbox['min']['x']:.1f}, {bbox['max']['x']:.1f}], Y=[{bbox['min']['y']:.1f}, {bbox['max']['y']:.1f}]")
            
            # STEP 2: Apply translation to bbox (not center)
            if transform_delta:
                # Check if this is absolute position (Placement) or relative (translate)
                is_absolute = len(transform_delta) == 4 and transform_delta[3] == True
                
                bbox = oblong.get('bounding_box', {})
                
                if is_absolute:
                    # Absolute position - set bbox directly
                    x, y, z = transform_delta[0], transform_delta[1], transform_delta[2]
                    
                    if bbox:
                        # Calculate bbox size
                        width = bbox['max']['x'] - bbox['min']['x']
                        height = bbox['max']['y'] - bbox['min']['y']
                        depth = bbox['max']['z'] - bbox['min']['z']
                        
                        # Set bbox centered at new position
                        bbox['min']['x'] = x - width / 2
                        bbox['min']['y'] = y - height / 2
                        bbox['min']['z'] = z - depth / 2
                        bbox['max']['x'] = x + width / 2
                        bbox['max']['y'] = y + height / 2
                        bbox['max']['z'] = z + depth / 2
                    
                    logger.info(f"[Transform] Applied Placement({x}, {y}, {z}) to {oblong.get('id')} via {transform_var}")
                else:
                    # Relative translation - translate the (possibly rotated) bbox
                    dx, dy, dz = transform_delta[0], transform_delta[1], transform_delta[2]
                    
                    if bbox:
                        bbox['min']['x'] += dx
                        bbox['min']['y'] += dy
                        bbox['min']['z'] += dz
                        bbox['max']['x'] += dx
                        bbox['max']['y'] += dy
                        bbox['max']['z'] += dz
                    
                    logger.info(f"[Transform] Applied translate({dx}, {dy}, {dz}) to {oblong.get('id')} via {transform_var}")
            
            # STEP 3: Calculate final center from final bbox (after all transforms)
            bbox = oblong.get('bounding_box', {})
            if bbox:
                oblong['center']['x'] = (bbox['min']['x'] + bbox['max']['x']) / 2
                oblong['center']['y'] = (bbox['min']['y'] + bbox['max']['y']) / 2
                oblong['center']['z'] = (bbox['min']['z'] + bbox['max']['z']) / 2
                
                logger.debug(f"[Transform] Final center: ({oblong['center']['x']:.1f}, {oblong['center']['y']:.1f}, {oblong['center']['z']:.1f})")
                logger.debug(f"[Transform] Final bbox: X=[{bbox['min']['x']:.1f}, {bbox['max']['x']:.1f}], Y=[{bbox['min']['y']:.1f}, {bbox['max']['y']:.1f}], Z=[{bbox['min']['z']:.1f}, {bbox['max']['z']:.1f}]")
        
        return oblongs
    
    def _analyze_bending_with_regex(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Regex-based analysis for bending features.
        
        Detects:
        - Part.makeTub() → 4 bends (tub type)
        - Part.makeLShape() → 1 bend (L-shape type)
        - Part.makeUShape() → 2 bends (U-shape type)
        - Part.makeZShape() → 2 bends (Z-shape type)
        
        Returns metadata to help step_converter.py avoid conflicts with fillets.
        """
        bending_features = []

        def strip_expr(expr):
            """Strip comments and extra whitespace from expression"""
            if not expr:
                return ""
            # Remove everything after comma (if accidentally matched next parameter)
            if ',' in expr:
                expr = expr.split(',')[0]
            # Remove inline comments (# ...)
            expr = re.sub(r'#.*$', '', expr, flags=re.MULTILINE)
            # Strip whitespace and newlines
            expr = re.sub(r'\s+', ' ', expr)  # Replace all whitespace with single space
            return expr.strip()
        
        # 🔥 PRIORITY 1: Detect Part.makeTub() FIRST (to avoid conflicts)
        # Pattern: Part.makeTub(thickness=2.0, bend_radius=2.0, width=150, length=200, height=30)
        tub_pattern = r'Part\.makeTub\s*\(\s*thickness\s*=\s*([^,]+),\s*bend_radius\s*=\s*([^,]+),\s*(?:width|length)\s*=\s*([^,]+),\s*(?:length|width)\s*=\s*([^,]+),\s*height\s*=\s*([^)]+)\s*\)'
        
        tub_matches = list(re.finditer(tub_pattern, code_content))
        if tub_matches:
            for match in tub_matches:
                try:
                    thickness_expr, bend_radius_expr, dim1_expr, dim2_expr, height_expr = match.groups()
                    
                    thickness = self._safe_eval(thickness_expr, code_content)
                    bend_radius = self._safe_eval(bend_radius_expr, code_content)
                    dim1 = self._safe_eval(dim1_expr, code_content)
                    dim2 = self._safe_eval(dim2_expr, code_content)
                    height = self._safe_eval(height_expr, code_content)
                    
                    # Determine length and width (order may vary in code)
                    length = max(dim1, dim2)
                    width = min(dim1, dim2)
                    
                    # Calculate outer radius
                    outer_radius = bend_radius + thickness
                    
                    # TUB has 4 bends (one at each corner)
                    # We create ONE metadata entry representing all 4 bends
                    bending_features.append({
                        'bend_type': 'tub',  # 🔥 CRITICAL: Mark as 'tub' to distinguish from L/U/Z shapes
                        'bend_radius': round(bend_radius, 3),
                        'bend_angle': 90.0,  # Tub always has 90° bends
                        'thickness': round(thickness, 3),
                        'dimensions': {
                            'length': round(length, 3),
                            'width': round(width, 3),
                            'height': round(height, 3)
                        },
                        'outer_radius': round(outer_radius, 3),
                        'expected_faces': {
                            'inner_cylindrical': 4,  # 4 inner bend faces
                            'outer_cylindrical': 4,  # 4 outer bend faces
                            'total': 8
                        },
                        'bend_count': 4,  # 🔥 CRITICAL: 4 bends for tub
                        'confidence': 0.95,  # High confidence from explicit makeTub call
                        'source': 'makeTub_call_regex',
                        # 🔥 EXCLUSION RULES for step_converter.py
                        'exclude_short_cylinders': True,  # Don't treat short cylinders as bends
                        'min_arc_length': 30.0  # Minimum arc length for tub bends (avoid fillets)
                    })
                    
                    logger.info(f"[REGEX] ✅ Detected TUB: 4 bends, R_inner={bend_radius}mm, R_outer={outer_radius}mm, {length}x{width}x{height}mm")
                    
                except Exception as e:
                    logger.warning(f"[FeatureAnalyzer] Failed to parse makeTub: {e}")
                    continue
        
        # PRIORITY 2: Detect Part.makeLShape()
        # Pattern for Part.makeLShape(dim_x, dim_y, thickness, flange_height, bend_angle_deg, bend_radius)
        # ✅ FIX: Support multi-line calls - use .*? (non-greedy) with DOTALL to match across newlines
        # Pattern stops at comma (with optional whitespace and comments)
        lshape_pattern = r'Part\.makeLShape\s*\(\s*dim_x\s*=\s*(.*?)\s*,\s*dim_y\s*=\s*(.*?)\s*,\s*thickness\s*=\s*(.*?)\s*,\s*flange_height\s*=\s*(.*?)\s*,\s*bend_angle_deg\s*=\s*(.*?)\s*,\s*bend_radius\s*=\s*([^)]*?)\s*\)'
        
        for match in re.finditer(lshape_pattern, code_content, re.DOTALL):
            try:
                dim_x_expr, dim_y_expr, thickness_expr, flange_height_expr, bend_angle_expr, bend_radius_expr = match.groups()
                

                
                dim_x = self._safe_eval(strip_expr(dim_x_expr), code_content)
                dim_y = self._safe_eval(strip_expr(dim_y_expr), code_content)
                thickness = self._safe_eval(strip_expr(thickness_expr), code_content)
                flange_height = self._safe_eval(strip_expr(flange_height_expr), code_content)
                bend_angle = self._safe_eval(strip_expr(bend_angle_expr), code_content)
                bend_radius = self._safe_eval(strip_expr(bend_radius_expr), code_content)
                
                # Calculate outer radius
                outer_radius = bend_radius + thickness
                
                # Calculate bend line
                bend_x = bend_radius + thickness
                
                bending_features.append({
                    'bend_type': 'L_shape',
                    'bend_radius': round(bend_radius, 3),
                    'bend_angle': round(bend_angle, 1),
                    'thickness': round(thickness, 3),
                    'dimensions': {
                        'dim_x': round(dim_x, 3),
                        'dim_y': round(dim_y, 3),
                        'flange_height': round(flange_height, 3)
                    },
                    'bend_line_start': {'x': round(bend_x, 3), 'y': 0, 'z': 0},
                    'bend_line_end': {'x': round(bend_x, 3), 'y': round(dim_y, 3), 'z': 0},
                    'outer_radius': round(outer_radius, 3),
                    'expected_faces': {
                        'inner_toroidal': 1,
                        'outer_cylindrical': 1,
                        'total': 2
                    },
                    'bend_count': 1,
                    'confidence': 0.90,
                    'source': 'makeLShape_call_regex'
                })
                
                logger.info(f"[REGEX] Detected L-shape bending: R_inner={bend_radius}mm, R_outer={outer_radius}mm")
                
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeLShape: {e}")
                continue
        
        # PRIORITY 3: Detect Part.makeUShape()
        # Pattern for Part.makeUShape(dim_x, dim_y, thickness, flange_height, bend_angle_deg, bend_radius)
        # U-shape has 2 bends (one on each side)
        ushape_pattern = r'Part\.makeUShape\s*\(\s*dim_x\s*=\s*([^,]+),\s*dim_y\s*=\s*([^,]+),\s*thickness\s*=\s*([^,]+),\s*flange_height\s*=\s*([^,]+),\s*bend_angle_deg\s*=\s*([^,]+),\s*bend_radius\s*=\s*([^)]+)\s*\)'
        
        for match in re.finditer(ushape_pattern, code_content):
            try:
                dim_x_expr, dim_y_expr, thickness_expr, flange_height_expr, bend_angle_expr, bend_radius_expr = match.groups()
                
                dim_x = self._safe_eval(dim_x_expr, code_content)
                dim_y = self._safe_eval(dim_y_expr, code_content)
                thickness = self._safe_eval(thickness_expr, code_content)
                flange_height = self._safe_eval(flange_height_expr, code_content)
                bend_angle = self._safe_eval(bend_angle_expr, code_content)
                bend_radius = self._safe_eval(bend_radius_expr, code_content)
                
                # Calculate outer radius
                outer_radius = bend_radius + thickness
                
                # U-shape has 2 bends:
                # - Left bend at x = -(bend_radius + thickness) (negative X direction)
                # - Right bend at x = dim_x + bend_radius + thickness (positive X direction)
                # Both bends run along Y direction (from y=0 to y=dim_y)
                left_bend_x = -(bend_radius + thickness)
                right_bend_x = dim_x + bend_radius + thickness
                
                # Create bend lines for both bends
                bend_lines = [
                    {
                        'start': {'x': round(left_bend_x, 3), 'y': 0, 'z': 0},
                        'end': {'x': round(left_bend_x, 3), 'y': round(dim_y, 3), 'z': 0}
                    },
                    {
                        'start': {'x': round(right_bend_x, 3), 'y': 0, 'z': 0},
                        'end': {'x': round(right_bend_x, 3), 'y': round(dim_y, 3), 'z': 0}
                    }
                ]
                
                bending_features.append({
                    'bend_type': 'U_shape',
                    'bend_radius': round(bend_radius, 3),
                    'bend_angle': round(bend_angle, 1),
                    'thickness': round(thickness, 3),
                    'dimensions': {
                        'dim_x': round(dim_x, 3),
                        'dim_y': round(dim_y, 3),
                        'flange_height': round(flange_height, 3)
                    },
                    'bend_line_start': {'x': round(left_bend_x, 3), 'y': 0, 'z': 0},  # First bend (left)
                    'bend_line_end': {'x': round(left_bend_x, 3), 'y': round(dim_y, 3), 'z': 0},
                    'bend_lines': bend_lines,  # Both bends
                    'outer_radius': round(outer_radius, 3),
                    'expected_faces': {
                        'inner_toroidal': 2,  # 2 inner bend faces (one per side)
                        'outer_cylindrical': 2,  # 2 outer bend faces
                        'total': 4
                    },
                    'bend_count': 2,  # U-shape has 2 bends
                    'confidence': 0.90,
                    'source': 'makeUShape_call_regex'
                })
                
                logger.info(f"[REGEX] ✅ Detected U-shape bending: 2 bends, R_inner={bend_radius}mm, R_outer={outer_radius}mm, dim_x={dim_x}mm, dim_y={dim_y}mm, flange_height={flange_height}mm")
                
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeUShape: {e}")
                continue
        
        # Pattern for Part.makeZShape
        zshape_pattern = r'Part\.makeZShape\s*\('
        if re.search(zshape_pattern, code_content):
            logger.info(f"[REGEX] Detected makeZShape call (not yet implemented)")

        # PRIORITY 4: Detect Part.makeCircularLShape()
        circular_lshape_pattern = r'Part\.makeCircularLShape\s*\(\s*diameter\s*=\s*(.*?)\s*,\s*thickness\s*=\s*(.*?)\s*,\s*offset_x\s*=\s*(.*?)\s*,\s*bend_angle_deg\s*=\s*(.*?)\s*,\s*bend_radius\s*=\s*([^)]*?)\s*\)'
        for match in re.finditer(circular_lshape_pattern, code_content, re.DOTALL):
            try:
                diameter_expr, thickness_expr, offset_x_expr, bend_angle_expr, bend_radius_expr = match.groups()
                
                diameter = self._safe_eval(strip_expr(diameter_expr), code_content)
                thickness = self._safe_eval(strip_expr(thickness_expr), code_content)
                offset_x = self._safe_eval(strip_expr(offset_x_expr), code_content)
                bend_angle = self._safe_eval(strip_expr(bend_angle_expr), code_content)
                bend_radius = self._safe_eval(strip_expr(bend_radius_expr), code_content)
                
                outer_radius = bend_radius + thickness
                
                bending_features.append({
                    'bend_type': 'L_shape_circular',
                    'bend_radius': round(bend_radius, 3),
                    'bend_angle': round(bend_angle, 1),
                    'thickness': round(thickness, 3),
                    'dimensions': {
                        'diameter': round(diameter, 3),
                        'offset_x': round(offset_x, 3)
                    },
                    'outer_radius': round(outer_radius, 3),
                    'bend_count': 1,
                    'confidence': 0.90,
                    'source': 'makeCircularLShape_call_regex'
                })
                
                logger.info(f"[REGEX] Detected Circular L-shape bending: R_inner={bend_radius}mm, R_outer={outer_radius}mm")
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeCircularLShape: {e}")

        # PRIORITY 5: Detect Part.makeCircularUShape()
        circular_ushape_pattern = r'Part\.makeCircularUShape\s*\(\s*diameter\s*=\s*(.*?)\s*,\s*thickness\s*=\s*(.*?)\s*,\s*offset_x_left\s*=\s*(.*?)\s*,\s*offset_x_right\s*=\s*(.*?)\s*,\s*bend_angle_left\s*=\s*(.*?)\s*,\s*bend_angle_right\s*=\s*(.*?)\s*,\s*bend_radius\s*=\s*([^)]*?)\s*\)'
        for match in re.finditer(circular_ushape_pattern, code_content, re.DOTALL):
            try:
                diameter_expr, thickness_expr, offset_x_left_expr, offset_x_right_expr, bend_angle_left_expr, bend_angle_right_expr, bend_radius_expr = match.groups()
                
                diameter = self._safe_eval(strip_expr(diameter_expr), code_content)
                thickness = self._safe_eval(strip_expr(thickness_expr), code_content)
                offset_x_left = self._safe_eval(strip_expr(offset_x_left_expr), code_content)
                offset_x_right = self._safe_eval(strip_expr(offset_x_right_expr), code_content)
                bend_angle_left = self._safe_eval(strip_expr(bend_angle_left_expr), code_content)
                bend_angle_right = self._safe_eval(strip_expr(bend_angle_right_expr), code_content)
                bend_radius = self._safe_eval(strip_expr(bend_radius_expr), code_content)
                
                outer_radius = bend_radius + thickness
                
                bending_features.append({
                    'bend_type': 'U_shape_circular',
                    'bend_radius': round(bend_radius, 3),
                    'bend_angle': round(bend_angle_left, 1),
                    'thickness': round(thickness, 3),
                    'dimensions': {
                        'diameter': round(diameter, 3),
                        'offset_x_left': round(offset_x_left, 3),
                        'offset_x_right': round(offset_x_right, 3)
                    },
                    'outer_radius': round(outer_radius, 3),
                    'bend_count': 2,
                    'confidence': 0.90,
                    'source': 'makeCircularUShape_call_regex'
                })
                logger.info(f"[REGEX] Detected Circular U-shape bending: 2 bends, R_inner={bend_radius}mm")
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeCircularUShape: {e}")

        # PRIORITY 6: Detect Part.makeCircularZShape()
        circular_zshape_pattern = r'Part\.makeCircularZShape\s*\(\s*diameter\s*=\s*(.*?)\s*,\s*thickness\s*=\s*(.*?)\s*,\s*offset_x_left\s*=\s*(.*?)\s*,\s*offset_x_right\s*=\s*(.*?)\s*,\s*bend_angle_left\s*=\s*(.*?)\s*,\s*bend_angle_right\s*=\s*(.*?)\s*,\s*bend_radius\s*=\s*([^)]*?)\s*\)'
        for match in re.finditer(circular_zshape_pattern, code_content, re.DOTALL):
            try:
                diameter_expr, thickness_expr, offset_x_left_expr, offset_x_right_expr, bend_angle_left_expr, bend_angle_right_expr, bend_radius_expr = match.groups()
                
                diameter = self._safe_eval(strip_expr(diameter_expr), code_content)
                thickness = self._safe_eval(strip_expr(thickness_expr), code_content)
                offset_x_left = self._safe_eval(strip_expr(offset_x_left_expr), code_content)
                offset_x_right = self._safe_eval(strip_expr(offset_x_right_expr), code_content)
                bend_angle_left = self._safe_eval(strip_expr(bend_angle_left_expr), code_content)
                bend_angle_right = self._safe_eval(strip_expr(bend_angle_right_expr), code_content)
                bend_radius = self._safe_eval(strip_expr(bend_radius_expr), code_content)
                
                outer_radius = bend_radius + thickness
                
                bending_features.append({
                    'bend_type': 'Z_shape_circular',
                    'bend_radius': round(bend_radius, 3),
                    'bend_angle': round(bend_angle_left, 1),
                    'thickness': round(thickness, 3),
                    'dimensions': {
                        'diameter': round(diameter, 3),
                        'offset_x_left': round(offset_x_left, 3),
                        'offset_x_right': round(offset_x_right, 3)
                    },
                    'outer_radius': round(outer_radius, 3),
                    'bend_count': 2,
                    'confidence': 0.90,
                    'source': 'makeCircularZShape_call_regex'
                })
                logger.info(f"[REGEX] Detected Circular Z-shape bending: 2 bends, R_inner={bend_radius}mm")
            except Exception as e:
                logger.warning(f"[FeatureAnalyzer] Failed to parse makeCircularZShape: {e}")
        
        return bending_features
    
    def _analyze_countersinks_with_regex(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Regex-based analysis for countersink features.
        Detects add_countersink_leg1/leg2 helper functions.
        """
        countersinks = []
        
        # Pattern for add_countersink_leg2(shape, hole_rad, cs_radius, cs_angle, hole_y, hole_z, ...)
        # Match only variable names (word characters), ignore comments
        cs_leg2_pattern = r'add_countersink_leg2\s*\(\s*[\w_]+\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)'
        
        # Pattern for add_countersink_leg1(shape, hole_rad, cs_radius, cs_angle, hole_x, hole_y, ...)
        cs_leg1_pattern = r'add_countersink_leg1\s*\(\s*[\w_]+\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)\s*,\s*([\w_]+)'
        
        # Detect leg2 countersinks (vertical flange)
        for match in re.finditer(cs_leg2_pattern, code_content):
            try:
                hole_rad_expr, cs_radius_expr, cs_angle_expr, hole_y_expr, hole_z_expr = match.groups()
                
                hole_rad = self._safe_eval(hole_rad_expr.strip(), code_content)
                cs_radius = self._safe_eval(cs_radius_expr.strip(), code_content)
                cs_angle = self._safe_eval(cs_angle_expr.strip(), code_content)
                hole_y = self._safe_eval(hole_y_expr.strip(), code_content)
                hole_z = self._safe_eval(hole_z_expr.strip(), code_content)
                
                # Calculate diameters
                hole_diameter = hole_rad * 2
                cs_diameter = cs_radius * 2
                
                # Position (x calculated from leg geometry, typically at bend)
                # For now, use placeholder - will be enriched later
                position_x = 0  # Will be calculated from bend geometry
                
                countersinks.append({
                    'position': {'x': position_x, 'y': round(hole_y, 3), 'z': round(hole_z, 3)},
                    'hole_diameter': round(hole_diameter, 3),
                    'cs_diameter': round(cs_diameter, 3),
                    'cs_angle': round(cs_angle, 1),
                    'depth': 'through',
                    'leg': 'leg2',  # vertical flange
                    'confidence': 0.92,
                    'source': 'add_countersink_leg2_regex'
                })
                
                logger.info(f"[REGEX] Detected countersink on leg2: hole_d={hole_diameter:.1f}mm, cs_d={cs_diameter:.1f}mm, angle={cs_angle}deg")
                
            except Exception as e:
                logger.warning(f"[REGEX] Failed to parse add_countersink_leg2: {e}")
                continue
        
        # Detect leg1 countersinks (horizontal flange)
        for match in re.finditer(cs_leg1_pattern, code_content):
            try:
                hole_rad_expr, cs_radius_expr, cs_angle_expr, hole_x_expr, hole_y_expr = match.groups()
                
                hole_rad = self._safe_eval(hole_rad_expr.strip(), code_content)
                cs_radius = self._safe_eval(cs_radius_expr.strip(), code_content)
                cs_angle = self._safe_eval(cs_angle_expr.strip(), code_content)
                hole_x = self._safe_eval(hole_x_expr.strip(), code_content)
                hole_y = self._safe_eval(hole_y_expr.strip(), code_content)
                
                hole_diameter = hole_rad * 2
                cs_diameter = cs_radius * 2
                
                # Position z at bottom of horizontal flange
                position_z = 0  # Will be calculated from geometry
                
                countersinks.append({
                    'position': {'x': round(hole_x, 3), 'y': round(hole_y, 3), 'z': position_z},
                    'hole_diameter': round(hole_diameter, 3),
                    'cs_diameter': round(cs_diameter, 3),
                    'cs_angle': round(cs_angle, 1),
                    'depth': 'through',
                    'leg': 'leg1',  # horizontal flange
                    'confidence': 0.92,
                    'source': 'add_countersink_leg1_regex'
                })
                
                logger.info(f"[REGEX] Detected countersink on leg1: hole_d={hole_diameter:.1f}mm, cs_d={cs_diameter:.1f}mm, angle={cs_angle}deg")
                
            except Exception as e:
                logger.warning(f"[REGEX] Failed to parse add_countersink_leg1: {e}")
                continue
        
        return countersinks

    def _analyze_box_holes_with_regex(self, code_content: str) -> List[Dict[str, Any]]:
        """
        Detect square/rectangular holes made with Part.makeBox cutters.

        Important: Part.makeBox is also used for base plates. A box is considered
        a hole only when the created variable is cut from another shape, or is
        appended to a list/compound that is cut.
        """
        box_holes: List[Dict[str, Any]] = []
        cut_vars, list_cut_vars = self._find_cut_box_variables(code_content)
        if not cut_vars and not list_cut_vars:
            return box_holes

        for call in self._find_part_makebox_calls(code_content):
            var_name = call.get('var_name')
            if not var_name:
                continue

            if var_name not in cut_vars and not self._is_makebox_appended_to_cut_list(call, code_content, list_cut_vars):
                continue

            loop_contexts = self._get_enclosing_range_loops(code_content, call['start'])
            if loop_contexts:
                box_holes.extend(self._expand_box_hole_loop(call, code_content, loop_contexts))
            else:
                parsed = self._parse_box_hole_call(call, code_content)
                if parsed:
                    box_holes.append(parsed)

        unique = []
        seen = set()
        for hole in box_holes:
            c = hole.get('center', {})
            key = (
                round(c.get('x', 0), 3),
                round(c.get('y', 0), 3),
                round(c.get('z', 0), 3),
                round(hole.get('width', 0), 3),
                round(hole.get('length', 0), 3),
            )
            if key in seen:
                continue
            seen.add(key)
            hole['id'] = f"{hole['type']}_{len(unique) + 1:03d}"
            unique.append(hole)

        if unique:
            logger.info(f"[BoxHoleAnalyzer] Detected {len(unique)} square/rectangular hole(s)")
        return unique

    def _find_cut_box_variables(self, code_content: str) -> Tuple[set, set]:
        """Find variables that are directly cut, plus list variables used in cut compounds."""
        cut_vars = set()
        list_cut_vars = set()

        for match in re.finditer(r'\.cut\s*\(\s*([a-zA-Z_]\w*)\s*\)', code_content):
            cut_vars.add(match.group(1))

        for match in re.finditer(r'([a-zA-Z_]\w*)\s*=\s*Part\.makeCompound\s*\(\s*([a-zA-Z_]\w*)\s*\)', code_content):
            compound_var, list_var = match.groups()
            if compound_var in cut_vars:
                list_cut_vars.add(list_var)

        return cut_vars, list_cut_vars

    def _find_part_makebox_calls(self, code_content: str) -> List[Dict[str, Any]]:
        """Return assigned Part.makeBox calls with balanced argument text."""
        calls = []
        pattern = r'(?m)^\s*([a-zA-Z_]\w*)\s*=\s*Part\.makeBox\s*\('

        for match in re.finditer(pattern, code_content):
            var_name = match.group(1)
            line_start = code_content.rfind('\n', 0, match.start()) + 1
            if code_content[line_start:match.start()].strip().startswith('#'):
                continue

            open_paren = code_content.find('(', match.end() - 1)
            close_paren = self._find_matching_paren(code_content, open_paren)
            if close_paren == -1:
                continue

            args_text = code_content[open_paren + 1:close_paren]
            calls.append({
                'var_name': var_name,
                'start': match.start(),
                'end': close_paren + 1,
                'args_text': args_text
            })

        return calls

    def _find_matching_paren(self, text: str, open_pos: int) -> int:
        if open_pos < 0 or open_pos >= len(text) or text[open_pos] != '(':
            return -1
        depth = 0
        for i in range(open_pos, len(text)):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _split_top_level_args(self, args_text: str) -> List[str]:
        args = []
        current = []
        paren = 0
        bracket = 0
        for ch in args_text:
            if ch == '(':
                paren += 1
            elif ch == ')':
                paren -= 1
            elif ch == '[':
                bracket += 1
            elif ch == ']':
                bracket -= 1

            if ch == ',' and paren == 0 and bracket == 0:
                args.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if ''.join(current).strip():
            args.append(''.join(current).strip())
        return args

    def _is_makebox_appended_to_cut_list(self, call: Dict[str, Any], code_content: str, list_cut_vars: set) -> bool:
        if not list_cut_vars:
            return False
        after = code_content[call['end']: min(len(code_content), call['end'] + 500)]
        var_name = call['var_name']
        for list_var in list_cut_vars:
            append_pattern = rf'{re.escape(list_var)}\.append\s*\(\s*{re.escape(var_name)}\s*\)'
            if re.search(append_pattern, after):
                return True
        return False

    def _get_enclosing_range_loops(self, code_content: str, pos: int) -> List[Dict[str, Any]]:
        """Find simple enclosing `for var in range(expr):` loops around a position."""
        loops = []
        lines = code_content.splitlines(True)
        offsets = []
        offset = 0
        for line in lines:
            offsets.append(offset)
            offset += len(line)

        call_line_idx = 0
        for idx, start in enumerate(offsets):
            if start <= pos < start + len(lines[idx]):
                call_line_idx = idx
                break

        call_indent = len(lines[call_line_idx]) - len(lines[call_line_idx].lstrip())
        for idx in range(call_line_idx - 1, -1, -1):
            line = lines[idx]
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            indent = len(line) - len(line.lstrip())
            if indent >= call_indent:
                continue
            match = re.match(r'for\s+([a-zA-Z_]\w*)\s+in\s+range\s*\(\s*([^)]+)\s*\)\s*:', stripped)
            if match:
                var_name, count_expr = match.groups()
                count = int(self._eval_box_expr(count_expr, code_content, {}))
                if count > 0:
                    loops.append({'var': var_name, 'count': count})
                call_indent = indent
        loops.reverse()
        return loops

    def _expand_box_hole_loop(self, call: Dict[str, Any], code_content: str, loops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        expanded = []

        def recurse(index: int, local_vars: Dict[str, Any]):
            if index >= len(loops):
                parsed = self._parse_box_hole_call(call, code_content, local_vars)
                if parsed:
                    expanded.append(parsed)
                return
            loop = loops[index]
            for value in range(loop['count']):
                next_vars = dict(local_vars)
                next_vars[loop['var']] = value
                recurse(index + 1, next_vars)

        recurse(0, {})
        return expanded

    def _parse_box_hole_call(self, call: Dict[str, Any], code_content: str, local_vars: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        try:
            args = self._split_top_level_args(call['args_text'])
            if len(args) < 4:
                return None

            width = self._eval_box_expr(self._strip_kwarg(args[0]), code_content, local_vars or {})
            length = self._eval_box_expr(self._strip_kwarg(args[1]), code_content, local_vars or {})
            depth = self._eval_box_expr(self._strip_kwarg(args[2]), code_content, local_vars or {})
            corner = self._parse_app_vector_arg(args[3], code_content, local_vars or {})
            direction = self._parse_app_vector_arg(args[4], code_content, local_vars or {}) if len(args) >= 5 else {'x': 0.0, 'y': 0.0, 'z': 1.0}

            if width <= 0 or length <= 0 or depth <= 0 or not corner:
                return None

            hole_type = 'square_hole' if abs(width - length) < 0.1 else 'rectangular_hole'
            bbox = self._calculate_box_hole_bbox(corner, width, length, depth, direction)
            center = {
                'x': (bbox['min']['x'] + bbox['max']['x']) / 2.0,
                'y': (bbox['min']['y'] + bbox['max']['y']) / 2.0,
                'z': (bbox['min']['z'] + bbox['max']['z']) / 2.0,
            }

            base_bbox = self._infer_base_plate_bbox(code_content)
            is_open_cutout = self._box_touches_base_boundary(bbox, base_bbox) if base_bbox else False

            return {
                'type': hole_type,
                'width': round(width, 3),
                'length': round(length, 3),
                'depth': round(depth, 3),
                'corner': {k: round(v, 3) for k, v in corner.items()},
                'center': {k: round(v, 3) for k, v in center.items()},
                'direction': {k: round(v, 6) for k, v in direction.items()},
                'bounding_box': {
                    'min': {k: round(v, 3) for k, v in bbox['min'].items()},
                    'max': {k: round(v, 3) for k, v in bbox['max'].items()},
                },
                'expected_faces': {
                    'planar_walls': 4 if not is_open_cutout else 2,
                    'total_min': 4 if not is_open_cutout else 2
                },
                'is_through_cut': True,
                'is_open_cutout': is_open_cutout,
                'confidence': 0.95,
                'source': 'Part.makeBox_cut',
                'units': 'mm'
            }
        except Exception as e:
            logger.warning(f"[BoxHoleAnalyzer] Failed to parse makeBox cutter {call.get('var_name')}: {e}")
            return None

    def _eval_box_expr(self, expr: str, code_content: str, local_vars: Dict[str, Any]) -> float:
        """Evaluate makeBox cutter expressions with loop variables taking priority."""
        expr = expr.strip()
        try:
            return float(expr)
        except Exception:
            pass

        eval_expr = expr
        for name, value in (local_vars or {}).items():
            eval_expr = re.sub(rf'\b{re.escape(name)}\b', str(value), eval_expr)

        variables = re.findall(r'\b[a-zA-Z_]\w*\b', eval_expr)
        for var in sorted(set(variables), key=len, reverse=True):
            if var in {'int', 'float', 'min', 'max', 'abs', 'round', 'math', 'sin', 'cos', 'tan', 'sqrt'}:
                continue
            if var in (local_vars or {}):
                continue
            assign_match = re.search(rf'(?m)^\s*{re.escape(var)}\s*=\s*([^#\n]+)', code_content)
            if not assign_match:
                continue
            value_expr = assign_match.group(1).strip().rstrip(',').strip()
            value = self._eval_box_expr(value_expr, code_content, local_vars)
            eval_expr = re.sub(rf'\b{re.escape(var)}\b', str(value), eval_expr)

        try:
            safe_dict = {
                '__builtins__': {},
                'int': int,
                'float': float,
                'min': min,
                'max': max,
                'abs': abs,
                'round': round,
                'math': math,
            }
            return float(eval(eval_expr, safe_dict, {}))
        except Exception:
            return self._safe_eval_with_math(eval_expr, code_content, {})

    def _parse_app_vector_arg(self, arg: str, code_content: str, local_vars: Dict[str, Any]) -> Optional[Dict[str, float]]:
        arg = self._strip_kwarg(arg)
        match = re.search(r'App\.Vector\s*\((.*)\)\s*$', arg, re.DOTALL)
        if not match:
            var_match = re.match(r'^([a-zA-Z_]\w*)$', arg)
            if var_match:
                vec_pattern = rf'{re.escape(var_match.group(1))}\s*=\s*App\.Vector\s*\((.*?)\)'
                found = re.search(vec_pattern, code_content, re.DOTALL)
                if found:
                    return self._parse_app_vector_arg(f"App.Vector({found.group(1)})", code_content, local_vars)
            return None

        parts = self._split_top_level_args(match.group(1))
        if len(parts) < 3:
            return None
        return {
            'x': self._eval_box_expr(self._strip_kwarg(parts[0]), code_content, local_vars),
            'y': self._eval_box_expr(self._strip_kwarg(parts[1]), code_content, local_vars),
            'z': self._eval_box_expr(self._strip_kwarg(parts[2]), code_content, local_vars),
        }

    def _calculate_box_hole_bbox(self, corner: Dict[str, float], width: float, length: float,
                                  depth: float, direction: Dict[str, float]) -> Dict[str, Dict[str, float]]:
        if abs(direction.get('z', 0)) > 0.9:
            return {
                'min': {'x': corner['x'], 'y': corner['y'], 'z': corner['z']},
                'max': {'x': corner['x'] + width, 'y': corner['y'] + length, 'z': corner['z'] + depth}
            }
        if abs(direction.get('x', 0)) > 0.9:
            return {
                'min': {'x': corner['x'], 'y': corner['y'], 'z': corner['z']},
                'max': {'x': corner['x'] + depth, 'y': corner['y'] + width, 'z': corner['z'] + length}
            }
        if abs(direction.get('y', 0)) > 0.9:
            return {
                'min': {'x': corner['x'], 'y': corner['y'], 'z': corner['z']},
                'max': {'x': corner['x'] + width, 'y': corner['y'] + depth, 'z': corner['z'] + length}
            }
        return {
            'min': {'x': corner['x'], 'y': corner['y'], 'z': corner['z']},
            'max': {'x': corner['x'] + width, 'y': corner['y'] + length, 'z': corner['z'] + depth}
        }

    def _infer_base_plate_bbox(self, code_content: str) -> Optional[Dict[str, Dict[str, float]]]:
        base_names = r'(?:base(?:_plate)?(?:_shape)?|base_shape|plate_shape|base_plate_shape)'
        pattern = rf'(?m)^\s*({base_names})\s*=\s*Part\.makeBox\s*\('
        match = re.search(pattern, code_content)
        if not match:
            return None
        open_paren = code_content.find('(', match.end() - 1)
        close_paren = self._find_matching_paren(code_content, open_paren)
        if close_paren == -1:
            return None
        args = self._split_top_level_args(code_content[open_paren + 1:close_paren])
        if len(args) < 4:
            return None
        try:
            width = self._safe_eval_with_math(self._strip_kwarg(args[0]), code_content, {})
            length = self._safe_eval_with_math(self._strip_kwarg(args[1]), code_content, {})
            depth = self._safe_eval_with_math(self._strip_kwarg(args[2]), code_content, {})
            corner = self._parse_app_vector_arg(args[3], code_content, {}) or {'x': 0.0, 'y': 0.0, 'z': 0.0}
            return self._calculate_box_hole_bbox(corner, width, length, depth, {'x': 0.0, 'y': 0.0, 'z': 1.0})
        except Exception:
            return None

    def _box_touches_base_boundary(self, bbox: Dict, base_bbox: Dict, tol: float = 0.01) -> bool:
        return (
            abs(bbox['min']['x'] - base_bbox['min']['x']) <= tol or
            abs(bbox['max']['x'] - base_bbox['max']['x']) <= tol or
            abs(bbox['min']['y'] - base_bbox['min']['y']) <= tol or
            abs(bbox['max']['y'] - base_bbox['max']['y']) <= tol
        )
    
    def _parse_oblong_params(self, length_expr, width_expr, height_expr,
                             x_expr, y_expr, z_expr,
                             dx_expr, dy_expr, dz_expr,
                             code_content) -> Optional[Dict[str, Any]]:
        """Parse and evaluate oblong parameters."""
        try:
            param1_val = self._safe_eval(self._strip_kwarg(length_expr), code_content)
            param2_val = self._safe_eval(self._strip_kwarg(width_expr), code_content)
            height = self._safe_eval(self._strip_kwarg(height_expr), code_content)
            
            # [FIX] Oblong can be rotated 90 degrees, so parameters can be in either order:
            # - makeOblong(total_length, width, ...) - horizontal orientation
            # - makeOblong(width, total_length, ...) - vertical orientation (rotated 90 deg)
            # Logic: The LARGER value is always total_length, the SMALLER value is always width
            # This is because total_length = straight_length + width, so total_length >= width
            param1_name = length_expr.strip().lower()
            param2_name = width_expr.strip().lower()
            
            if param1_val > 0 and param2_val > 0:
                # Determine which is total_length and which is width based on values
                # Larger value = total_length, smaller value = width
                if param1_val >= param2_val:
                    # param1 is total_length, param2 is width
                    length = param1_val
                    width = param2_val
                    logger.debug(f"[OblongAnalyzer] Detected: param1={param1_val} (total_length), param2={param2_val} (width)")
                else:
                    # param2 is total_length, param1 is width (rotated 90 deg)
                    length = param2_val
                    width = param1_val
                    logger.debug(f"[OblongAnalyzer] Detected rotated oblong: param1={param1_val} (width), param2={param2_val} (total_length)")
            else:
                # Fallback: use parameter order if values are invalid
                # Try to infer from variable names
                if ('width' in param1_name or 'diam' in param1_name) and \
                   ('total' in param2_name or 'length' in param2_name or 'len' in param2_name):
                    # param1 is width, param2 is total_length
                    width = param1_val
                    length = param2_val
                    logger.warning(f"[OblongAnalyzer] Using variable names to infer: param1={param1_val} (width), param2={param2_val} (total_length)")
                else:
                    # Default: assume param1 is length, param2 is width
                    length = param1_val
                    width = param2_val
                    logger.warning(f"[OblongAnalyzer] Using default order: param1={param1_val} (length), param2={param2_val} (width)")
            
            corner = {
                "x": self._safe_eval(self._strip_kwarg(x_expr), code_content),
                "y": self._safe_eval(self._strip_kwarg(y_expr), code_content),
                "z": self._safe_eval(self._strip_kwarg(z_expr), code_content)
            }
            
            direction = {
                "x": self._safe_eval(self._strip_kwarg(dx_expr), code_content),
                "y": self._safe_eval(self._strip_kwarg(dy_expr), code_content),
                "z": self._safe_eval(self._strip_kwarg(dz_expr), code_content)
            }
            
            # Calculate accurate bounding box based on oblong geometry
            # Oblong = filleted rectangle with rounded ends
            # For standard Z-up extrusion:
            # - Length is along X axis
            # - Width is along Y axis  
            # - Height is extrusion depth (Z)
            
            straight_length = length - width  # Length of straight section
            end_radius = width / 2.0  # Radius of rounded ends
            
            # Calculate bbox based on direction
            if abs(direction['z']) > 0.9:  # Z-up extrusion (most common)
                # Oblong bbox must include the rounded ends
                # The rounded ends extend beyond the corner by end_radius
                
                # Handle Z-coordinate correctly for all cases:
                # - Negative Z (e.g., corner.z = -1.0): Keep actual value for cut operations
                # - Zero Z (e.g., corner.z = 0.0): Standard case
                # - Positive Z (e.g., corner.z = 2.0): Elevated oblong
                z_min = corner['z']  # Keep actual corner Z value
                z_max = corner['z'] + height  # End Z = start Z + depth
                
                bbox = {
                    "min": {
                        "x": corner['x'] - end_radius,  # Extended for left rounded end
                        "y": corner['y'],
                        "z": z_min  # Use actual corner Z (can be negative)
                    },
                    "max": {
                        "x": corner['x'] + length + end_radius,  # Extended for right rounded end
                        "y": corner['y'] + width,
                        "z": z_max  # End Z based on actual start + height
                    }
                }
                
                # Center is at middle of bbox
                center = {
                    "x": corner['x'] + length / 2.0,
                    "y": corner['y'] + width / 2.0,
                    "z": corner['z'] + height / 2.0
                }
                
            elif abs(direction['y']) > 0.9:  # Y-up extrusion
                bbox = {
                    "min": {
                        "x": corner['x'],
                        "y": corner['y'],
                        "z": corner['z']
                    },
                    "max": {
                        "x": corner['x'] + length,
                        "y": corner['y'] + height,
                        "z": corner['z'] + width
                    }
                }
                
                center = {
                    "x": corner['x'] + length / 2.0,
                    "y": corner['y'] + height / 2.0,
                    "z": corner['z'] + width / 2.0
                }
                
            elif abs(direction['x']) > 0.9:  # X-up extrusion
                # For X-direction: length along Z, width along Y, height along X
                # Corner position: corner.y is at one end (not center), corner.z is at width center
                # Need to extend bbox to include rounded ends
                end_radius = width / 2.0
                
                # Y extends from corner.y - length (backward) to corner.y + end_radius (forward for rounded end)
                # But corner.y might be at start or end, so we need to check
                # Based on code pattern: corner.y = center_y + total/2, so corner.y is at one end
                y_min = corner['y'] - length - end_radius  # Extend backward for full length + rounded end
                y_max = corner['y'] + end_radius  # Extend forward for rounded end
                
                # Z extends from corner.z - width/2 to corner.z + width/2 (width centered at corner.z)
                z_min = corner['z'] - width / 2.0
                z_max = corner['z'] + width / 2.0
                
                bbox = {
                    "min": {
                        "x": corner['x'],
                        "y": y_min,
                        "z": z_min
                    },
                    "max": {
                        "x": corner['x'] + height,
                        "y": y_max,
                        "z": z_max
                    }
                }
                
                center = {
                    "x": corner['x'] + height / 2.0,
                    "y": corner['y'] - length / 2.0,  # Center is at middle of length
                    "z": corner['z']  # Z center is at corner.z
                }
            else:
                # Fallback for non-standard orientations
                bbox = {
                    "min": {
                        "x": corner['x'],
                        "y": corner['y'],
                        "z": corner['z']
                    },
                    "max": {
                        "x": corner['x'] + length,
                        "y": corner['y'] + width,
                        "z": corner['z'] + height
                    }
                }
                
                center = {
                    "x": corner['x'] + length / 2.0,
                    "y": corner['y'] + width / 2.0,
                    "z": corner['z'] + height / 2.0
                }
            
            return {
                # 🔥 MATCH FREECAD CODE DEFINITION:
                # oblong_straight_len = straight section length (NOT total length)
                # oblong_width = width
                "straight_length": round(straight_length, 3),  # Length of straight section (10.0)
                "width": round(width, 3),                       # Width (5.0)
                
                # Additional metadata for step_converter
                "total_length": round(length, 3),               # Total length including rounded ends (15.0)
                "fillet_radius": round(end_radius, 3),          # Radius of rounded ends (2.5)
                "depth": round(height, 3),                      # Extrusion depth (7.0)
                
                "corner": {
                    "x": round(corner['x'], 3),
                    "y": round(corner['y'], 3),
                    "z": round(corner['z'], 3)
                },
                "direction": direction,
                "bounding_box": {
                    "min": {
                        "x": round(bbox['min']['x'], 3),
                        "y": round(bbox['min']['y'], 3),
                        "z": round(bbox['min']['z'], 3)
                    },
                    "max": {
                        "x": round(bbox['max']['x'], 3),
                        "y": round(bbox['max']['y'], 3),
                        "z": round(bbox['max']['z'], 3)
                    }
                },
                "center": {
                    "x": round(center['x'], 3),
                    "y": round(center['y'], 3),
                    "z": round(center['z'], 3)
                },
                "expected_faces": {
                    "planar": 2,         # Top and bottom rectangular faces
                    "cylindrical": 4,    # Four rounded cylindrical faces at ends
                    "total": 6
                },
                "geometry_type": "filleted_rectangle",
                "confidence": 0.95,
                "source": "makeOblong_call",
                "units": "mm",  # Explicit units
                "id": f"oblong_{hash((corner['x'], corner['y'], corner['z'])) % 10000:04d}"  # Unique ID
            }
            
        except Exception as e:
            logger.warning(f"[FeatureAnalyzer] Failed to evaluate oblong params: {e}")
            return None

    def _strip_kwarg(self, expr: str) -> str:
        """Remove 'param=' prefix if present (fallback cleanup)."""
        if '=' in expr:
            parts = expr.split('=', 1)
            # Simple check: if left side looks like a variable name and not a complex expression
            # Avoiding split on 'var = val' inside a lambda or complex string, 
            # but here we deal with function args.
            if re.match(r'^\s*[a-zA-Z_]\w*\s*$', parts[0]):
                return parts[1].strip()
        return expr.strip()
    
    # Old _enrich_oblong removed - use the one defined earlier in class
    
    def _calculate_oblong_bbox(self, pnt: Dict, length: float, width: float, 
                                height: float, direction: Dict) -> Dict[str, Dict[str, float]]:
        """
        Calculate bounding box from makeOblong parameters.
        
        makeBox(length, width, height, pnt, dir) creates a box where:
        - pnt is the corner position
        - length, width, height are dimensions
        - dir is the extrusion direction
        """
        dx, dy, dz = direction['x'], direction['y'], direction['z']
        
        # Standard case: dir = (0, 0, 1) - vertical extrusion (XY plane)
        if abs(dz) > 0.9:
            return {
                "min": {"x": pnt['x'], "y": pnt['y'], "z": pnt['z']},
                "max": {
                    "x": pnt['x'] + length,
                    "y": pnt['y'] + width,
                    "z": pnt['z'] + height
                }
            }
        
        # Horizontal X: dir = (1, 0, 0) - extrusion along X (YZ plane)
        elif abs(dx) > 0.9:
            return {
                "min": {"x": pnt['x'], "y": pnt['y'], "z": pnt['z']},
                "max": {
                    "x": pnt['x'] + height,
                    "y": pnt['y'] + length,
                    "z": pnt['z'] + width
                }
            }
        
        # Horizontal Y: dir = (0, 1, 0) - extrusion along Y (XZ plane)
        elif abs(dy) > 0.9:
            return {
                "min": {"x": pnt['x'], "y": pnt['y'], "z": pnt['z']},
                "max": {
                    "x": pnt['x'] + length,
                    "y": pnt['y'] + height,
                    "z": pnt['z'] + width
                }
            }
        
        # Custom direction - use standard case as fallback
        else:
            logger.warning(f"[FeatureAnalyzer] Custom direction vector {direction}, using standard bbox calculation")
            return {
                "min": {"x": pnt['x'], "y": pnt['y'], "z": pnt['z']},
                "max": {
                    "x": pnt['x'] + length,
                    "y": pnt['y'] + width,
                    "z": pnt['z'] + height
                }
            }
    
    def _extract_positions(self, code_content: str) -> List[Dict[str, float]]:
        """Extract hole positions from code."""
        positions = []
        
        # Pattern 1: positions = [(x1, y1), (x2, y2), ...]
        pos_pattern = r'positions\s*=\s*\[(.*?)\]'
        pos_match = re.search(pos_pattern, code_content, re.DOTALL)
        
        if pos_match:
            pos_str = pos_match.group(1)
            tuple_pattern = r'\(\s*([^,]+)\s*,\s*([^)]+)\s*\)'
            tuples = re.findall(tuple_pattern, pos_str)
            
            for x_expr, y_expr in tuples:
                try:
                    x = self._safe_eval(x_expr, code_content)
                    y = self._safe_eval(y_expr, code_content)
                    positions.append({"x": x, "y": y, "z": 0})
                except:
                    continue
        
        # Pattern 2: sketch.addGeometry(Part.Circle(App.Vector(x, y, z), ...))
        if not positions:
            sketch_pattern = r'sketch\.addGeometry\s*\(\s*Part\.Circle\s*\(\s*App\.Vector\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)'
            sketch_matches = re.findall(sketch_pattern, code_content)
            
            for x_expr, y_expr, z_expr in sketch_matches:
                try:
                    x = self._safe_eval(x_expr, code_content)
                    y = self._safe_eval(y_expr, code_content)
                    z = self._safe_eval(z_expr, code_content)
                    positions.append({"x": x, "y": y, "z": z})
                except:
                    continue
        
        return positions if positions else [{"x": 0, "y": 0, "z": 0}]
    
    def _extract_depth(self, code_content: str) -> float:
        """Extract hole depth from code."""
        depth_pattern = r'hole_depth\s*=\s*([^#\n]+)'
        depth_match = re.search(depth_pattern, code_content)
        
        if depth_match:
            try:
                return self._safe_eval(depth_match.group(1), code_content)
            except:
                pass
        
        return 10.0  # Default depth
    
    def _safe_eval(self, expr: str, code_content: str, _depth: int = 0) -> float:
        """
        Safely evaluate simple expressions with recursive variable resolution.
        
        🎯 ENHANCED: Now supports attribute access (e.g., slot1_center.x)
        
        Args:
            expr: Expression to evaluate
            code_content: Full code content for variable lookup
            _depth: Recursion depth (prevents infinite loops)
        """
        if _depth > 10:  # Prevent infinite recursion
            return 0.0
        
        expr = expr.strip()
        
        # Direct number
        try:
            return float(expr)
        except:
            pass
        
        # 🎯 NEW: Handle attribute access (e.g., slot1_center.x)
        attr_match = re.match(r'^([a-zA-Z_]\w*)\.([xyz])$', expr)
        if attr_match:
            var_name, attr = attr_match.groups()
            
            # Find the vector variable definition
            # Pattern: var_name = App.Vector(x_expr, y_expr, z_expr)
            vec_pattern = rf'{re.escape(var_name)}\s*=\s*App\.Vector\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\s*\)'
            vec_match = re.search(vec_pattern, code_content, re.DOTALL)
            
            if vec_match:
                x_expr, y_expr, z_expr = vec_match.groups()
                
                # Get the appropriate component
                if attr == 'x':
                    return self._safe_eval(x_expr.strip(), code_content, _depth + 1)
                elif attr == 'y':
                    return self._safe_eval(y_expr.strip(), code_content, _depth + 1)
                elif attr == 'z':
                    return self._safe_eval(z_expr.strip(), code_content, _depth + 1)
        
        # 🎯 NEW: Handle tuple indexing (e.g., p1[0], p1[1])
        tuple_match = re.match(r'^([a-zA-Z_]\w*)\[(\d+)\]$', expr)
        if tuple_match:
            var_name, index_str = tuple_match.groups()
            index = int(index_str)
            
            # Find the tuple variable definition
            # Pattern: var_name = (expr1, expr2) or var_name = (expr1, expr2, expr3)
            tuple_pattern = rf'{re.escape(var_name)}\s*=\s*\(([^)]+)\)'
            tuple_match_found = re.search(tuple_pattern, code_content, re.DOTALL)
            
            if tuple_match_found:
                tuple_content = tuple_match_found.group(1)
                # Split by comma, handling nested parentheses
                parts = []
                current_part = ""
                paren_count = 0
                for char in tuple_content:
                    if char == '(':
                        paren_count += 1
                        current_part += char
                    elif char == ')':
                        paren_count -= 1
                        current_part += char
                    elif char == ',' and paren_count == 0:
                        parts.append(current_part.strip())
                        current_part = ""
                    else:
                        current_part += char
                if current_part.strip():
                    parts.append(current_part.strip())
                
                # Get the element at the specified index
                if 0 <= index < len(parts):
                    element_expr = parts[index].strip()
                    # Remove trailing comments
                    element_expr = re.sub(r'#.*$', '', element_expr).strip()
                    return self._safe_eval(element_expr, code_content, _depth + 1)
        
        # If it's a simple variable, try to find its value
        if re.match(r'^[a-zA-Z_]\w*$', expr):
            # Try to find variable assignment (handle both single-line and multi-line)
            # Pattern: var_name = expression (may span multiple lines or have comments)
            # IMPORTANT: Filter out function call arguments (e.g., "dim_x=dim_x," in function calls)
            var_pattern = rf'{re.escape(expr)}\s*=\s*([^#\n=]+)'
            var_matches = list(re.finditer(var_pattern, code_content))
            if var_matches:
                # Filter matches: only keep actual variable assignments, not function call arguments
                # Strategy: A real assignment has a numeric/expression value, not another variable name
                valid_matches = []
                for match in var_matches:
                    match_start = match.start()
                    line_start = code_content.rfind('\n', 0, match_start) + 1
                    line_end = code_content.find('\n', match_start)
                    if line_end == -1:
                        line_end = len(code_content)
                    line_content = code_content[line_start:line_end]
                    
                    # ✅ FIX: Skip comment lines (lines starting with #)
                    if line_content.strip().startswith('#'):
                        continue
                    
                    value_expr_raw = match.group(1).strip()
                    # Remove trailing comments and commas
                    value_expr_clean = re.sub(r'#.*$', '', value_expr_raw).strip().rstrip(',').strip()
                    
                    # Real assignment should have a numeric value or expression, not just a variable name
                    # Check if value is a number or contains operators (expression)
                    is_numeric = re.match(r'^[\d.+\-*/()\s]+$', value_expr_clean)
                    is_simple_var = re.match(r'^[a-zA-Z_]\w*$', value_expr_clean)
                    
                    # Prefer numeric/expression values over simple variable references
                    # Simple variable reference in assignment context is likely a function call argument
                    if is_numeric or (not is_simple_var):
                        valid_matches.append(match)
                    # Also check if it's at start of line (more likely to be real assignment)
                    else:
                        line_before_match = code_content[line_start:match_start]
                        # If at start of line with only whitespace, it's likely a real assignment
                        if re.match(r'^\s*(?:#.*)?$', line_before_match):
                            valid_matches.append(match)
                
                # If no valid matches found, use all matches (fallback)
                if not valid_matches:
                    valid_matches = var_matches
                
                # Use the FIRST valid match (variable assignment usually appears before function calls)
                var_match = valid_matches[0]
                value_expr = var_match.group(1).strip()
                # Remove trailing comments
                value_expr = re.sub(r'#.*$', '', value_expr).strip()
                # Remove trailing commas if any
                value_expr = value_expr.rstrip(',').strip()
                # Recursively evaluate the value
                result = self._safe_eval(value_expr, code_content, _depth + 1)
                # Return result even if 0.0 (might be valid)
                if result != 0.0:
                    return result
                # For 0.0, check if the expression actually evaluates to 0
                if value_expr == '0' or value_expr == '0.0' or value_expr.strip() == '':
                    return 0.0
                # If we got 0.0 but expression is not '0', try one more time with better parsing
                # This handles cases like "plate_length / 2.0" where plate_length might not be resolved yet
                if _depth < 5:  # Limit recursion
                    return result
        
        # Try to evaluate as expression (replace variables first)
        try:
            # Find all variables in the expression (including attribute access)
            # Match: var_name or var_name.attr
            variables = re.findall(r'[a-zA-Z_]\w*(?:\.[xyz])?', expr)
            eval_expr = expr
            
            for var in variables:
                # Skip Python keywords and functions
                if var in ['App', 'Vector', 'Part', 'min', 'max', 'abs', 'round', 'math']:
                    continue
                
                # Skip if it's a math function call (e.g., math.sin)
                if '.' in var and var != 'math':
                    continue
                
                # Get variable value
                var_value = self._safe_eval(var, code_content, _depth + 1)
                # Only replace if we got a valid value (not 0.0 unless it's actually 0)
                if var_value != 0.0:
                    # Replace in expression (use word boundary for exact match)
                    eval_expr = re.sub(rf'\b{re.escape(var)}\b', str(var_value), eval_expr)
                elif var in ['plate_length', 'plate_width', 'center_x', 'center_y']:
                    # For known variables that might be 0, try harder to resolve
                    var_pattern = rf'{re.escape(var)}\s*=\s*([^#\n]+)'
                    var_match = re.search(var_pattern, code_content)
                    if var_match:
                        value_expr = var_match.group(1).strip()
                        value_expr = re.sub(r'#.*$', '', value_expr).strip()
                        var_value = self._safe_eval(value_expr, code_content, _depth + 1)
                        if var_value != 0.0:
                            eval_expr = re.sub(rf'\b{re.escape(var)}\b', str(var_value), eval_expr)
            
            # Evaluate the expression (safe for numbers and math)
            if re.match(r'^[0-9+\-*/(). ]+$', eval_expr):
                result = float(eval(eval_expr))
                if result != 0.0 or '0' in eval_expr:
                    return result
        except Exception as e:
            logger.debug(f"[_safe_eval] Failed to eval expression '{expr}': {e}")
            pass
        
        return 0.0
    
    def _safe_eval_with_math(self, expr: str, code_content: str, local_vars: Dict[str, Any] = None) -> float:
        """
        Safely evaluate expressions with math functions support.
        
        Args:
            expr: Expression to evaluate (may contain math.sin, math.cos, etc.)
            code_content: Full code content for variable lookup
            local_vars: Dictionary of local variables (e.g., {i: 0})
            
        Returns:
            Evaluated float value
        """
        import math
        
        if local_vars is None:
            local_vars = {}
        
        expr = expr.strip()
        
        # Direct number
        try:
            return float(expr)
        except:
            pass
        
        # Try to resolve variables from code_content first
        # Replace variables in expression with their values
        eval_expr = expr
        
        # First, resolve variables from code_content
        variables = re.findall(r'[a-zA-Z_]\w*(?:\.[xyz])?', expr)
        for var in variables:
            # Skip Python keywords and functions
            if var in ['App', 'Vector', 'Part', 'min', 'max', 'abs', 'round', 'math', 'sin', 'cos', 'radians']:
                continue
            
            # Skip if it's a math function call (e.g., math.sin)
            if '.' in var:
                continue
            
            # Get variable value from code_content
            var_value = self._safe_eval(var, code_content)
            if var_value != 0.0 or var in local_vars:
                # Replace in expression
                eval_expr = re.sub(rf'\b{re.escape(var)}\b', str(var_value), eval_expr)
        
        # Apply local_vars (overrides code_content values)
        for var_name, var_value in local_vars.items():
            eval_expr = re.sub(rf'\b{re.escape(var_name)}\b', str(var_value), eval_expr)
        
        # Create safe evaluation context with math functions
        safe_dict = {
            'math': math,
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'radians': math.radians,
            'degrees': math.degrees,
            'pi': math.pi,
            'e': math.e,
            'sqrt': math.sqrt,
            'pow': math.pow,
            'abs': abs,
            'round': round,
            'min': min,
            'max': max
        }
        
        try:
            # Evaluate the expression
            result = eval(eval_expr, {"__builtins__": {}}, safe_dict)
            return float(result)
        except Exception as e:
            logger.debug(f"[_safe_eval_with_math] Failed to eval '{expr}': {e}")
            return 0.0
    
    def _safe_eval_with_math(self, expr: str, code_content: str, local_vars: Dict[str, Any] = None) -> float:
        """
        Safely evaluate expressions with math functions support.
        
        Args:
            expr: Expression to evaluate (may contain math.sin, math.cos, etc.)
            code_content: Full code content for variable lookup
            local_vars: Dictionary of local variables (e.g., {i: 0})
            
        Returns:
            Evaluated float value
        """
        import math
        
        if local_vars is None:
            local_vars = {}
        
        expr = expr.strip()
        
        # Direct number
        try:
            return float(expr)
        except:
            pass
        
        # Try to resolve variables from code_content first
        # Replace variables in expression with their values
        eval_expr = expr
        
        # First, resolve variables from code_content
        variables = re.findall(r'[a-zA-Z_]\w*(?:\.[xyz])?', expr)
        for var in variables:
            # Skip Python keywords and functions
            if var in ['App', 'Vector', 'Part', 'min', 'max', 'abs', 'round', 'math', 'sin', 'cos', 'radians']:
                continue
            
            # Skip if it's a math function call (e.g., math.sin)
            if '.' in var and var != 'math':
                continue
            
            # Get variable value from code_content
            var_value = self._safe_eval(var, code_content)
            if var_value != 0.0 or var in local_vars:
                # Replace in expression
                eval_expr = re.sub(rf'\b{re.escape(var)}\b', str(var_value), eval_expr)
        
        # Apply local_vars (overrides code_content values)
        for var_name, var_value in local_vars.items():
            eval_expr = re.sub(rf'\b{re.escape(var_name)}\b', str(var_value), eval_expr)
        
        # Create safe evaluation context with math functions
        safe_dict = {
            'math': math,
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'radians': math.radians,
            'degrees': math.degrees,
            'pi': math.pi,
            'e': math.e,
            'sqrt': math.sqrt,
            'pow': math.pow,
            'abs': abs,
            'round': round,
            'min': min,
            'max': max
        }
        
        try:
            # Evaluate the expression
            result = eval(eval_expr, {"__builtins__": {}}, safe_dict)
            return float(result)
        except Exception as e:
            logger.debug(f"[_safe_eval_with_math] Failed to eval '{expr}': {e}")
            return 0.0
    
    def _merge_oblongs(self, llm_oblongs: List[Dict], regex_oblongs: List[Dict]) -> List[Dict]:
        """
        Merge LLM and regex oblong results, deduplicating by position.
        LLM results take priority (more accurate parameters).
        
        Args:
            llm_oblongs: Oblongs detected by LLM
            regex_oblongs: Oblongs detected by regex
            
        Returns:
            Merged list of oblongs (deduplicated)
        """
        merged = []
        
        # Add all LLM oblongs first (higher quality)
        for llm_oblong in llm_oblongs:
            merged.append(llm_oblong)
        
        # Add regex oblongs that don't overlap with LLM oblongs
        for regex_oblong in regex_oblongs:
            regex_corner = regex_oblong.get('corner', {})
            regex_x = regex_corner.get('x', 0)
            regex_y = regex_corner.get('y', 0)
            
            # Check if this position already exists in LLM results
            is_duplicate = False
            for llm_oblong in llm_oblongs:
                llm_corner = llm_oblong.get('corner', {})
                llm_x = llm_corner.get('x', 0)
                llm_y = llm_corner.get('y', 0)
                
                # Consider duplicate if within 5mm (tolerance for calculation differences)
                if abs(regex_x - llm_x) < 5.0 and abs(regex_y - llm_y) < 5.0:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                # This is a NEW oblong that LLM missed!
                logger.info(f"[Merge] Regex found additional oblong at ({regex_x:.1f}, {regex_y:.1f}) that LLM missed")
                merged.append(regex_oblong)
        
        return merged
    
    def _merge_holes(self, llm_holes: List[Dict], regex_holes: List[Dict]) -> List[Dict]:
        """
        Merge LLM and regex threaded hole results, deduplicating by position.
        LLM results take priority (more accurate parameters).
        
        Args:
            llm_holes: Holes detected by LLM
            regex_holes: Holes detected by regex
            
        Returns:
            Merged list of holes (deduplicated)
        """
        merged = []
        
        # Add all LLM holes first (higher quality)
        for llm_hole in llm_holes:
            # Ensure diameter is calculated before adding
            self._enrich_threaded_hole(llm_hole)
            merged.append(llm_hole)
        
        # Add regex holes that don't overlap with LLM holes
        for regex_hole in regex_holes:
            # Ensure diameter is calculated before adding
            self._enrich_threaded_hole(regex_hole)
            regex_pos = regex_hole.get('position', {})
            regex_x = regex_pos.get('x', 0)
            regex_y = regex_pos.get('y', 0)
            
            # Check if this position already exists in LLM results
            is_duplicate = False
            for llm_hole in llm_holes:
                llm_pos = llm_hole.get('position', {})
                llm_x = llm_pos.get('x', 0)
                llm_y = llm_pos.get('y', 0)
                
                # Consider duplicate if within 2mm
                if abs(regex_x - llm_x) < 2.0 and abs(regex_y - llm_y) < 2.0:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                # This is a NEW hole that LLM missed!
                logger.info(f"[Merge] Regex found additional hole at ({regex_x:.1f}, {regex_y:.1f}) that LLM missed")
                merged.append(regex_hole)
        
        return merged
    

    def _analyze_shape_type_with_regex(self, code_content: str) -> Optional[str]:
        """
        Extract shape type from code using regex patterns.
        Fallback when LLM analysis fails or is unavailable.
        
        Detects:
        - L-shape: "L-shaped", "L-bracket", "equerre", Part.makeLShape()
        - Tube: "tube", "hollow", "wall_thickness"
        - Capot/U-shape: "U-shaped", "U-channel", "capot", Part.makeUShape()
        - Coffert: "coffert", "coffer"
        - Box: "box", "rectangle", Part.makeBox()
        
        Returns:
            Shape type string or None if not detected
        """
        import re
        
        # Pattern 1: Comment-based detection (highest priority)
        comment_patterns = [
            (r'#\s*Shape\s*type:\s*([\w-]+)', 1),  # # Shape type: L-bracket
            (r'#\s*(L-shaped|L-bracket|equerre)', 'l-shape'),  # # L-shaped bracket
            (r'#\s*(U-shaped|U-channel|capot)', 'capot'),  # # U-shaped channel
            (r'#\s*(tube|Tube)\s*(carré|rond)?', 'tube'),  # # Tube carré
            (r'#\s*(coffert|coffer)', 'coffert'),  # # Coffert
        ]
        
        for pattern, group in comment_patterns:
            match = re.search(pattern, code_content, re.IGNORECASE)
            if match:
                if isinstance(group, int):
                    shape_str = match.group(group).lower()
                    # Normalize to standard names
                    if 'circular' in shape_str:
                        if 'l' in shape_str:
                            return 'l-bracket-circular'
                        elif 'u' in shape_str:
                            return 'u-shaped-circular'
                        elif 'z' in shape_str:
                            return 'z-shaped-circular'
                        elif 'sheet' in shape_str or 'plate' in shape_str or 'cylinder' in shape_str:
                            return 'sheet-circular'
                    
                    if 'l' in shape_str and ('shape' in shape_str or 'bracket' in shape_str):
                        return 'l-shape'
                    elif 'u' in shape_str and ('shape' in shape_str or 'channel' in shape_str):
                        return 'capot'
                    elif 'tube' in shape_str:
                        return 'tube'
                    elif 'capot' in shape_str:
                        return 'capot'
                    elif 'coffert' in shape_str or 'coffer' in shape_str:
                        return 'coffert'
                    elif 'box' in shape_str or 'rectangle' in shape_str:
                        return 'box'
                else:
                    return group
         
        # Pattern 2: Function call detection
        if re.search(r'Part\.makeCircularLShape\s*\(', code_content):
            return 'l-bracket-circular'
        elif re.search(r'Part\.makeCircularUShape\s*\(', code_content):
            return 'u-shaped-circular'
        elif re.search(r'Part\.makeCircularZShape\s*\(', code_content):
            return 'z-shaped-circular'
        elif re.search(r'Part\.makeCylinder\s*\(', code_content) or re.search(r'Part\.makeCircularFoldedPlate\s*\(', code_content):
            return 'sheet-circular'
        elif re.search(r'Part\.makeLShape\s*\(', code_content):
            return 'l-shape'
        elif re.search(r'Part\.makeUShape\s*\(', code_content):
            return 'capot'
        
        # Pattern 3: Variable name detection
        if re.search(r'\b(tube_wall_thickness|wall_thickness)\s*=', code_content):
            # Check if it's actually a hollow tube (has cut operation)
            if re.search(r'\.cut\s*\(', code_content):
                return 'tube'
        
        # Pattern 4: L-shape specific patterns
        if re.search(r'\b(leg1|leg2)_\w+', code_content):
            # Has leg1/leg2 variables, likely L-shape
            return 'l-shape'
        
        return None
    
    def _diameter_to_thread_type(self, diameter: float) -> Optional[str]:
        """Convert diameter to thread type (e.g., 4.0 -> M4)."""
        for std_dia, thread in self.THREAD_SPECS.items():
            if abs(diameter - std_dia) < 0.3:
                return thread
        return None
    
    def _thread_type_to_diameter(self, thread_type: str) -> float:
        """Convert thread type to diameter (e.g., M4 -> 4.0)."""
        match = re.match(r'M(\d+(?:\.\d+)?)', thread_type, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return 4.0  # Default
    
    async def generate_metadata_file_async(self, code_path: str, output_path: Optional[str] = None, shape_type: Optional[str] = None) -> str:
        """
        Analyze code file and generate metadata JSON file (async version).

        Args:
            code_path: Path to FreeCAD Python code file
            output_path: Path to save metadata JSON (default: same dir as code with _metadata.json suffix)
            shape_type: Shape type of the generated part, if known — forwarded to analyze_code()
                to skip the LLM pass for Perforated Sheet (see analyze_code() docstring).

        Returns:
            Path to generated metadata file
        """
        # Read code
        with open(code_path, 'r', encoding='utf-8') as f:
            code_content = f.read()

        # Analyze (async)
        metadata = await self.analyze_code(code_content, shape_type=shape_type)
        
        # Add file info
        metadata["source_file"] = os.path.basename(code_path)
        metadata["generated_at"] = Path(code_path).stat().st_mtime
        
        # Determine output path
        if output_path is None:
            code_file = Path(code_path)
            output_path = code_file.parent / f"{code_file.stem}_metadata.json"
        
        # Save metadata
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"[FeatureAnalyzer] Metadata saved to: {output_path}")
        return str(output_path)
    
    def generate_metadata_file(self, code_path: str, output_path: Optional[str] = None) -> str:
        """
        Analyze code file and generate metadata JSON file (sync wrapper).
        
        Args:
            code_path: Path to FreeCAD Python code file
            output_path: Path to save metadata JSON (default: same dir as code with _metadata.json suffix)
            
        Returns:
            Path to generated metadata file
        """
        import asyncio
        
        # Run async version in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, create a new task
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(self.generate_metadata_file_async(code_path, output_path))
            else:
                return loop.run_until_complete(self.generate_metadata_file_async(code_path, output_path))
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(self.generate_metadata_file_async(code_path, output_path))


# Convenience function for quick analysis
def analyze_freecad_code(code_path: str) -> Dict[str, Any]:
    """
    Quick function to analyze FreeCAD code for features.
    
    Args:
        code_path: Path to FreeCAD Python code
        
    Returns:
        Metadata dictionary
    """
    import asyncio
    
    analyzer = FeatureMetadataAnalyzer()
    
    with open(code_path, 'r', encoding='utf-8') as f:
        code_content = f.read()
    
    # Run async analyze_code in event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(analyzer.analyze_code(code_content))
        else:
            return loop.run_until_complete(analyzer.analyze_code(code_content))
    except RuntimeError:
        return asyncio.run(analyzer.analyze_code(code_content))
    
    def _analyze_shape_type_with_regex(self, code_content: str) -> Optional[str]:
        """
        Detect shape type using regex patterns (fallback when LLM fails).
        
        Detects:
        - l-shape: L-shaped brackets
        - tube: Hollow tubes (square or round)
        - capot/u-shape: U-shaped channels
        - coffret: Coffret shapes
        - box: Simple rectangular boxes
        
        Returns:
            Shape type string or None
        """
        code_lower = code_content.lower()
        
        # Priority 1: Comments with explicit shape type
        comment_patterns = [
            (r'#\s*shape\s*type\s*:\s*l[-_]?shape', 'l-shape'),
            (r'#\s*shape\s*type\s*:\s*u[-_]?shape', 'u-shape'),
            (r'#\s*shape\s*type\s*:\s*capot', 'capot'),
            (r'#\s*shape\s*type\s*:\s*coffret', 'coffret'),
            (r'#\s*shape\s*type\s*:\s*tube', 'tube'),
            (r'#\s*l[-_]?shaped?\s+bracket', 'l-shape'),
            (r'#\s*u[-_]?shaped?\s+channel', 'u-shape'),
            (r'#\s*tube\s+carré', 'tube'),
            (r'#\s*tube\s+rond', 'tube'),
        ]
        
        for pattern, shape_type in comment_patterns:
            if re.search(pattern, code_lower):
                logger.info(f"[SHAPE_REGEX] Detected {shape_type} from comment")
                return shape_type
        
        # Priority 2: Function calls
        if 'maketub(' in code_lower:
            logger.info(f"[SHAPE_REGEX] Detected capot from makeTub() call")
            return 'capot'
        
        if 'makerectangulartube(' in code_lower or 'makecirculartube(' in code_lower:
            logger.info(f"[SHAPE_REGEX] Detected tube from makeRectangularTube/makeCircularTube() call")
            return 'tube'
        
        # Priority 3: Variable names
        if re.search(r'\b(leg1|leg2)_\w+', code_content):
            logger.info(f"[SHAPE_REGEX] Detected l-shape from leg1/leg2 variables")
            return 'l-shape'
        
        if re.search(r'\btube_wall_thickness\b', code_lower):
            logger.info(f"[SHAPE_REGEX] Detected tube from tube_wall_thickness variable")
            return 'tube'
        
        if re.search(r'\bu_channel_\w+', code_lower):
            logger.info(f"[SHAPE_REGEX] Detected u-shape from u_channel variables")
            return 'u-shape'
        
        # Priority 4: Cut operation for hollow structures (tube detection)
        if re.search(r'\.cut\s*\(', code_content):
            # Check if it's a tube (has both outer and inner box/cylinder)
            if ('outer' in code_lower and 'inner' in code_lower):
                if 'box' in code_lower or 'cylinder' in code_lower:
                    logger.info(f"[SHAPE_REGEX] Detected tube from cut operation (outer/inner)")
                    return 'tube'
        
        logger.debug(f"[SHAPE_REGEX] No shape type detected")
        return None

    def _merge_oblongs(self, llm_oblongs: List[Dict], regex_oblongs: List[Dict]) -> List[Dict]:
        """
        Intelligent merge: LLM-first (better loop detection) with regex verification.
        
        Strategy:
        - LLM is PRIMARY (understands loops, context, variable syntax)
        - Regex is VERIFICATION (counts actual makeOblong calls)
        - If counts match: trust LLM
        - If LLM > Regex: LLM might have hallucinated, warn but use LLM
        - If LLM < Regex: LLM missed some, warn and use LLM + fill from regex
        
        Returns:
            Merged list (prioritizes LLM understanding)
        """
        llm_count = len(llm_oblongs)
        regex_count = len(regex_oblongs)
        
        logger.info(f"[MERGE] Oblongs: LLM={llm_count}, Regex={regex_count}")
        
        # Perfect match: trust LLM
        if llm_count == regex_count:
            logger.info(f"[MERGE] ✅ Counts match, using LLM oblongs (better context understanding)")
            return llm_oblongs
        
        # LLM detected more: might be correct (loops) or hallucination
        elif llm_count > regex_count:
            logger.warning(f"[MERGE] ⚠️  LLM detected MORE oblongs ({llm_count}) than regex ({regex_count})")
            logger.warning(f"[MERGE] → This is EXPECTED if code has loops (LLM counts iterations)")
            logger.warning(f"[MERGE] → Using LLM results (loop-aware)")
            
            #Slightly lower confidence for all
            for oblong in llm_oblongs:
                oblong['confidence'] = min(oblong.get('confidence', 0.95), 0.90)
                oblong['verification_status'] = 'llm_only'
            
            return llm_oblongs
        
        # LLM detected fewer: missed some oblongs
        else:  # llm_count < regex_count
            logger.warning(f"[MERGE] ⚠️  LLM MISSED oblongs! LLM={llm_count}, Regex={regex_count}")
            logger.warning(f"[MERGE] → Using LLM results + flagging as incomplete")
            
            # Mark LLM oblongs as potentially incomplete
            for oblong in llm_oblongs:
                oblong['confidence'] = min(oblong.get('confidence', 0.95), 0.85)
                oblong['verification_status'] = 'incomplete_detection'
            
            # Could add regex oblongs here, but they lack proper enrichment
            # Better to return LLM with warning
            return llm_oblongs
    
    def _merge_holes(self, llm_holes: List[Dict], regex_holes: List[Dict]) -> List[Dict]:
        """
        Merge threaded holes with same strategy as oblongs.
        
        LLM-first with regex verification.
        """
        llm_count = len(llm_holes)
        regex_count = len(regex_holes)
        
        logger.info(f"[MERGE] Threaded holes: LLM={llm_count}, Regex={regex_count}")
        
        if llm_count == regex_count:
            logger.info(f"[MERGE] ✅ Counts match, using LLM holes")
            return llm_holes
        
        elif llm_count > regex_count:
            logger.warning(f"[MERGE] ⚠️  LLM detected MORE holes ({llm_count}) than regex ({regex_count})")
            logger.warning(f"[MERGE] → Using LLM results")
            
            for hole in llm_holes:
                hole['confidence'] = min(hole.get('confidence', 0.95), 0.90)
                hole['verification_status'] = 'llm_only'
            
            return llm_holes
        
        else:  # llm_count < regex_count
            logger.warning(f"[MERGE] ⚠️  LLM MISSED holes! LLM={llm_count}, Regex={regex_count}")
            logger.warning(f"[MERGE] → Falling back to regex")
            
            # For holes, regex is quite reliable, so fallback
            return regex_holes if regex_holes else llm_holes


# Backward compatibility aliases
ThreadedMetadataAnalyzer = FeatureMetadataAnalyzer

__all__ = [
    'FeatureMetadataAnalyzer',
    'ThreadedMetadataAnalyzer',  # Alias for backward compatibility
    'analyze_freecad_code'
]

