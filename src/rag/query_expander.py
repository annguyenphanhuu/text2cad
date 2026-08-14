"""
Query Expansion for Manufacturing Terminology using LLM with Few-Shot Learning

This module uses LLM with few-shot examples to intelligently expand user queries 
by APPENDING shape type classification at the end to improve semantic search retrieval accuracy.

The LLM learns from examples to identify English manufacturing terminology
and append the canonical shape type.

Strategy: SHAPE TYPE APPEND (keep original, add classification at end)
- "sheet with one bend" → "sheet with one bend\nShape type: L-bracket"
- "angle bracket 55x50" → "angle bracket 55x50\nShape type: L-bracket"
- "square tube" → "square tube\nShape type: Tube-Rectangular"
"""

import logging
from typing import Optional, Dict
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

def log_query_expansion(original_query: str, expanded_query: str, detected_shape: str):
    """Log the query expansion process to a dedicated file."""
    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "query_expansion.log"
        
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}]\n")
            f.write(f"Original Query: {original_query}\n")
            f.write(f"Expanded Query: {expanded_query}\n")
            f.write(f"Detected Shape: {detected_shape}\n")
            f.write("-" * 80 + "\n\n")
    except Exception as e:
        logger.error(f"[ERROR] Failed to write to query_expansion.log: {e}")

# Global LLM instance for query expansion
_expansion_llm = None


def _detect_triangle_shape(query: str) -> Optional[str]:
    """Deterministic fallback for the Triangle shape family.

    NOTE: This is a quick pre-check only — keep it narrow to avoid false positives.
    The LLM (expand_query_with_llm) is the primary detector and handles typos,
    paraphrases, and edge cases much better than keyword matching.
    Only list terms that are unambiguous standalone identifiers.
    """
    query_lower = query.lower()
    triangle_terms = [
        # Pre-declared type markers (highest priority)
        "shape type: triangle",
        "type: triangle",
        # Clear triangle identifiers
        "equilateral triangle",
        "isosceles triangle",
        "scalene triangle",
        "right triangle",
        "triangular sheet",
        "triangular plate",
        "triangular tray",
        "triangular gusset",
        "triangular bracket",
        "triangular base",
        "gusset plate",
        "triangle tray",
        "triangle",
        "triangular",
    ]
    if any(term in query_lower for term in triangle_terms):
        return "Triangle"
    return None


def set_expansion_llm(llm):
    """Set the LLM to use for query expansion."""
    global _expansion_llm
    _expansion_llm = llm


def _clean_shape_type(value) -> Optional[str]:
    """
    Normalise a detected shape type, or return None if it isn't a shape token.

    The old text fallback matched `Shape type:\\s*(\\S+)`, which swallowed the
    JSON punctuation that followed and produced values like `Sheet",`. That
    string then reached the retriever's shape filter, matched no known shape, and
    silently disabled example filtering for that request — it looked like a
    detected shape while behaving like none.
    """
    if not isinstance(value, str):
        return None
    # Shape types are single tokens of letters/digits/hyphen/underscore
    # (Sheet-Circular, CAPOT-mixed-direction, Tube-Rectangular, ...).
    m = re.match(r'\s*["\']?\s*([A-Za-z][A-Za-z0-9_-]*)', value)
    if not m:
        return None
    token = m.group(1)
    return None if token.lower() in ("null", "none", "unknown") else token


def _loads_lenient(raw: str) -> dict:
    """
    json.loads, retrying once with raw newlines inside string literals escaped.

    The prompt asks for `expanded_query` to end with a `\\nShape type: X` suffix,
    and the model frequently emits that as a real newline inside the JSON string,
    which is invalid JSON. That was sending ~1 in 5 responses down the text
    fallback path. Repairing it here keeps them on the structured path.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = re.sub(
            r'"((?:[^"\\]|\\.)*)"',
            lambda m: '"' + m.group(1).replace('\n', '\\n').replace('\r', '') + '"',
            raw,
            flags=re.DOTALL,
        )
        return json.loads(repaired)   # still raises JSONDecodeError if hopeless


def _salvage_from_text(response_text: str, query: str) -> Dict[str, Optional[str]]:
    """
    Last resort when the response is not usable JSON.

    Recovers the `expanded_query` value if the field is recognisable, rather than
    handing the whole raw JSON blob to FAISS as the search query — which is what
    the previous fallback did, degrading retrieval on exactly the requests that
    had already gone wrong.
    """
    m = re.search(r'"expanded_query"\s*:\s*"(.*?)"\s*(?:,|\})', response_text, re.DOTALL)
    if m:
        expanded = m.group(1).replace('\\n', '\n').strip()
    elif '{' in response_text:
        expanded = query          # a JSON blob is not a search query
    else:
        expanded = response_text.strip('"').strip("'").strip() or query

    # Look for the shape in the RAW response, not in `expanded` — when the JSON
    # was unusable `expanded` has fallen back to the original query, which never
    # carries a "Shape type:" suffix, so searching it would always miss a shape
    # that is sitting right there in the response.
    shape = None
    for pattern in (r'"detected_shape_type"\s*:\s*([^,}\n]+)',
                    r'Shape type:\s*([^\n"]+)'):
        found = re.search(pattern, response_text, re.IGNORECASE)
        if found:
            shape = _clean_shape_type(found.group(1))
            if shape:
                break

    log_query_expansion(query, expanded, str(shape))
    logger.info(f"[QUERY_EXPAND] salvaged from non-JSON response | shape={shape}")
    return {"expanded_query": expanded or query, "detected_shape_type": shape}


async def expand_query_with_llm(
    query: str,
    llm=None,
    max_synonyms: int = 5,
    cost_tracker=None
) -> Dict[str, Optional[str]]:
    """
    Use LLM with few-shot learning to expand query with manufacturing terminology.
    
    The LLM learns from examples to identify manufacturing terms and returns both
    the expanded query and the detected shape type as structured data.
    
    Args:
        query: Original user query
        llm: LLM instance to use (uses global if not provided)
        max_synonyms: Maximum number of synonyms to add
        cost_tracker: Optional CostTracker instance for tracking costs
        
    Returns:
        Dict with:
            - "expanded_query": Original query with shape type appended
            - "detected_shape_type": Detected shape type (e.g., "L-bracket", "U-shaped") or None
    """
    if llm is None:
        llm = _expansion_llm
    
    if llm is None:
        detected = _detect_triangle_shape(query)
        if detected:
            expanded = query if "shape type:" in query.lower() else f"{query}\nShape type: {detected}"
            log_query_expansion(query, expanded, detected)
            return {"expanded_query": expanded, "detected_shape_type": detected}
        logger.warning("[QUERY_EXPAND] No LLM available, returning original query")
        return {"expanded_query": query, "detected_shape_type": None}
    
    # Few-shot prompt teaching LLM to identify shape type and APPEND it at the end
    expansion_prompt = f"""You are a manufacturing terminology expert. Your task is to:
1. **NORMALIZE capot face synonyms** to standard terms (if capot-related query)
2. Analyze manufacturing queries and APPEND the detected shape type at the end

**STEP 1: CAPOT FACE NORMALIZATION (Apply FIRST if query mentions capot faces)**

If the query mentions any capot face names, replace ALL synonym variants with these 5 STANDARD terms:

**Normalization Rules:**
- "back", "rear", "top", "upper", "top side", "top plane", "long top side", "longitudinal top face", "transverse face"
  → Replace with: **"Back face"**

- "front", "underside", "bottom", "lower", "bottom side", "bottom plane", "long bottom side", "longitudinal bottom face"
  → Replace with: **"Front face"**

- "base", "bottom plate", "seat", "datum", "lower plane", "reference face"
  → Replace with: **"Base"**

- "left", "on the left", "left plane", "left side", "left lateral", "short left side", "left end"
  → Replace with: **"Left face"**

- "right", "on the right", "right plane", "right side", "right lateral", "short right side", "right end"
  → Replace with: **"Right face"**

**Normalization Examples:**
- "On the top plane" → "On the Back face"
- "bottom with drilling" → "Front face with drilling"
- "left side and right side" → "Left face and Right face"

**STEP 2: SHAPE TYPE DETECTION**

After normalization, detect shape type and APPEND it.

**Output Format:**
[NORMALIZED QUERY]
Shape type: [DETECTED SHAPE]

**Synonym Mapping & Core Categories:**
*Note: 'tab', 'lug', 'ear', 'lip', 'flange', 'return' all represent folds/bends/returns/flanges.*

1. **Sheet Metal (Flat, Unbent):**
   - Flat plate keywords: sheet, plate, flat panel, flat part, blank.
   - If rectangular or unspecified: **Sheet**
   - If circular/round/disc: **Sheet-Circular** (keywords: disc, disk, round plate, circular sheet, circular disc, flange plate, diameter/Ø with no length).

2. **L-bracket (1 bend/tab/lug):**
   - L keywords: angle bracket, angle iron, L-bracket, L-profile, L-shaped bracket, sheet with one bend, one bent tab.
   - If base is rectangular: **L-bracket**
   - If base is circular/round: **L-bracket-Circular** (keywords: round bracket, round sheet with one bend, circular L-bracket).
   - **CRITICAL KEYWORD OVERRIDE**: If the query contains `angle bracket` or `angle iron` (without the adjective "triangular"), classify as **L-bracket** regardless of the number of bends mentioned. Example: "angle bracket bent with 2 bends" → **L-bracket** (not U-shaped), because angle bracket is an explicit L-bracket keyword.

3. **U-Shaped (2 bends/tabs in same direction):**
   - U keywords: U-profile, U-channel, channel, U-bracket, sheet with two bends, two parallel returns/flanges, two tabs bent the same way.
   - If base is rectangular: **U-shaped**
   - If base is circular/round: **U-shaped-Circular** (keywords: round sheet with two bends in the same direction, circular U-shaped).

4. **Z-Shaped (2 bends/tabs in opposite directions OR "Z-bend" / "in a Z"):**
   - Z keywords: Z-profile, Z-bracket, Z-shaped sheet, two opposite bends, bends in opposite directions, Z-bend, offset bracket.
   - If base is rectangular: **Z-shaped**
   - If base is circular/round: **Z-shaped-Circular** (keywords: round sheet bent in a Z, circular Z-shaped).

5. **Tubes:**
   - If circular cross-section: **Tube-Circular** (round tube, cylindrical tube, pipe, hollow cylinder, solid round bar, round bar).
   - If rectangular/square cross-section: **Tube-Rectangular** (square tube, rectangular tube).

6. **CAPOT / Covers (3-4 walls/bends):**
   - Keywords: cover, hood, tray, open box, open cover, enclosure, sheet with three/four bends, bends on 3/4 sides.
   - Shape type: **CAPOT** (default — all walls bend the same angle/direction, built with makeTub)
   - **SUBTYPE OVERRIDE** — if walls bend in independent/different directions or each wall has its own distinct angle (e.g. "each side has its own bend", "independent bends", "one side up, the other down", "mixed direction"), use **CAPOT-mixed-direction** instead.
   - **CRITICAL DISAMBIGUATION** — `cover`/`lid` + shape adjective (circular, square, rectangular, round) = **flat plate**, NOT CAPOT.
     Examples: "circular cover Ø200" → Sheet-Circular. "square cover 300x300" → Sheet. "rectangular cover" → Sheet.
     Only classify as CAPOT when "cover" describes an enclosure/box with walls (e.g. "cover with three walls", "steel tray with dimensions L×W×H").
   - **CRITICAL DISAMBIGUATION** — `circular bottom` / `circular base` / `round base` as standalone parts = **flat plate** (Sheet-Circular). Only treat as face names when they appear in a capot context.

7. **I-Shaped / T-Shaped Profiles:**
   - I-Shaped: I-profile, I-beam → **I-Shaped**
   - T-Shaped: T-profile, T-bar → **T-Shaped**

8. **Triangle (triangular sheet/plate, flat or with edge flanges):**
   - Keywords: triangle, triangular, triangular plate, triangular sheet, equilateral triangle, isosceles triangle, triangular tray, triangular gusset, gusset plate, triangular bracket.
   - Shape type: **Triangle**
   - A triangular base with edge flanges remains **Triangle**. Do NOT reclassify as L-bracket, U-shaped, Z-shaped, or CAPOT based only on flange count.
   - **CRITICAL**: "triangular bracket" → **Triangle**, NOT L-bracket. "triangular gusset" → **Triangle**, NOT plate.

**CRITICAL RULES (Priority Order):**
- **PRIORITY 0: Pre-declared Type:** If query has "Type: [Shape]" or "Shape type: [Shape]", use that exact type.
- **PRIORITY 1: Circular vs Rectangular:** Make sure to distinguish circular flat/folded plates (`Sheet-Circular`, `L-bracket-Circular`, `U-shaped-Circular`, `Z-shaped-Circular`) from their rectangular counterparts when the base shape is circular (disc, circle, Ø diameter, round plate, round sheet).
- **PRIORITY 2: Structural:** shelf/wall bracket with components → Z-shaped shelf-bracket.
- **PRIORITY 3: Explicit Keywords:** angle bracket/angle iron → L-bracket; U-profile/U-channel → U-shaped; Z-profile/Z-bracket → Z-shaped; tube/cylinder → Tube-Circular / Tube-Rectangular; cover/hood → CAPOT.
- **PRIORITY 3b: Triangular base shape:** triangle/triangular/triangular plate/triangular gusset → Triangle. Triangle overrides generic sheet/plate wording and its edge flanges do NOT imply L/U/Z/CAPOT. "gusset" WITHOUT "triangular" is ambiguous — do NOT auto-classify as Triangle.
- **PRIORITY 4: Return Bends / Secondary Folds & Flat Plate Operations (MANDATORY override):**
  * Before counting bends, decide whether each fold is a **primary profile bend** or a **secondary edge treatment**.
  * Primary profile bends create the main section shape (L/U/Z/CAPOT), usually structural flanges/walls.
  * Secondary edge treatments modify a flat sheet edge without changing the core shape family.
  * Bends folded on top of other bends (e.g. "return bend", "flange return", "double bend", "hem", "lip") are **secondary operations**, not primary profile bends.
  * Crushed folds (180° hem bends) and offsets / joggles are **secondary operations** / flat plate operations.
  * Crushed fold detection must be semantic, not exact keyword matching. Treat terms such as "crushed fold", "flattened fold", "180 degree bend", "open hem", "closed hem", "hem fold", "return fold", and "folded back on itself" as examples of the same 180° hem/return concept. Also tolerate minor typos when the manufacturing meaning is clear (e.g. "opn hem", "crushd fold"). These examples are NOT an exhaustive keyword list.
  * If a sheet has ONLY crushed folds and/or offsets (joggles) without any other standard profile bends (e.g., 90° bends), it MUST be classified as **Sheet** (or **Sheet-Circular** if circular), NOT L-bracket/U-shaped/Z-shaped.
  * Do **NOT** count return/secondary bends, crushed folds, or offsets (joggles) when counting bends for shape classification.
  * *Example 1*: 1 primary bend + 1 return bend → count = **1 primary bend** → Shape type: L-bracket.
  * *Example 2*: Flat sheet + 1 crushed fold → count = **0 primary bends** → Shape type: Sheet.
  * *Example 3*: Flat sheet + 1 crushed fold + 1 joggle → count = **0 primary bends** → Shape type: Sheet.
- **PRIORITY 5: Bend Pattern Analysis:**
  * 1 bend / 1 tab / 1 flange → Shape type: L-bracket (or L-bracket-Circular if circular)
  * 2 bends/tabs (same direction/parallel) → Shape type: U-shaped (or U-shaped-Circular if circular)
  * 2 bends/tabs (opposite directions/Z-bend) → Shape type: Z-shaped (or Z-shaped-Circular if circular)
  * 3 or 4 bends/walls, same angle/direction on every wall (default) → Shape type: CAPOT
  * 3 or 4 bends/walls, each wall with its own independent angle/direction → Shape type: CAPOT-mixed-direction

**Instructions:**
1. Analyze the query to identify shape type based on keywords, diameter/shape clues, and bend patterns
2. Keep the ORIGINAL QUERY COMPLETELY UNCHANGED
3. APPEND "Shape type: X" on a new line at the end for expanded_query
4. Extract the detected shape type separately
5. If the request is a Sheet with a secondary crushed fold / open hem / 180° hem edge treatment, also append this line inside `expanded_query`: "Operation intent: secondary crushed fold / open hem on sheet edges"
6. Return ONLY valid JSON with two fields: "expanded_query" and "detected_shape_type"
7. If no shape terms found, set "detected_shape_type" to null

**Few-Shot Examples:**

Example 1 (Pre-declared Type):
Input: "Type: L-bracket\nOperations: Main bend at 160 mm. 20 mm return on the opposite end, 90°"
Output: {{"expanded_query": "Type: L-bracket\nOperations: Main bend at 160 mm. 20 mm return on the opposite end, 90°\nShape type: L-bracket", "detected_shape_type": "L-bracket"}}

Example 2 (Explicit keyword - angle bracket):
Input: "angle bracket with section 55x50x5"
Output: {{"expanded_query": "angle bracket with section 55x50x5\nShape type: L-bracket", "detected_shape_type": "L-bracket"}}

Example 3 (Explicit keyword - tube):
Input: "square tube 25x25x2"
Output: {{"expanded_query": "square tube 25x25x2\nShape type: Tube-Rectangular", "detected_shape_type": "Tube-Rectangular"}}

Example 4 (No shape - flat sheet):
Input: "plate 200x150 thickness 4mm"
Output: {{"expanded_query": "plate 200x150 thickness 4mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

Example 5 (Circular flat sheet):
Input: "disc with diameter 200mm and thickness 3mm"
Output: {{"expanded_query": "disc with diameter 200mm and thickness 3mm\nShape type: Sheet-Circular", "detected_shape_type": "Sheet-Circular"}}

Example 6 (Circular L-bracket):
Input: "round sheet diameter 180 mm thickness 2 mm with one bend at 90 degrees at offset 40 mm from the center"
Output: {{"expanded_query": "round sheet diameter 180 mm thickness 2 mm with one bend at 90 degrees at offset 40 mm from the center\nShape type: L-bracket-Circular", "detected_shape_type": "L-bracket-Circular"}}

Example 7 (Circular U-shaped):
Input: "round plate diameter 200mm thickness 2mm bent twice in the same direction upward at -30mm and 30mm"
Output: {{"expanded_query": "round plate diameter 200mm thickness 2mm bent twice in the same direction upward at -30mm and 30mm\nShape type: U-shaped-Circular", "detected_shape_type": "U-shaped-Circular"}}

Example 8 (Circular Z-shaped):
Input: "round plate Ø150 thickness 1.5mm bent in a Z with one bend upward at -35mm and one bend downward at 35mm"
Output: {{"expanded_query": "round plate Ø150 thickness 1.5mm bent in a Z with one bend upward at -35mm and one bend downward at 35mm\nShape type: Z-shaped-Circular", "detected_shape_type": "Z-shaped-Circular"}}

Example 9 (CONTEXTUAL - 2 parallel bends = U-shaped):
Input: "sheet with two parallel bends"
Output: {{"expanded_query": "sheet with two parallel bends\nShape type: U-shaped", "detected_shape_type": "U-shaped"}}

Example 10 (CONTEXTUAL - 4 bends = CAPOT):
Input: "bent sheet with four bends at 90° forming a tray"
Output: {{"expanded_query": "bent sheet with four bends at 90° forming a tray\nShape type: CAPOT", "detected_shape_type": "CAPOT"}}

Example 10b (CAPOT with independent per-wall directions = CAPOT-mixed-direction):
Input: "box with 4 bends, top and right bends upward, bottom and left bends downward"
Output: {{"expanded_query": "box with 4 bends, top and right bends upward, bottom and left bends downward\nShape type: CAPOT-mixed-direction", "detected_shape_type": "CAPOT-mixed-direction"}}

Example 11 (No manufacturing terms):
Input: "create a simple box"
Output: {{"expanded_query": "create a simple box", "detected_shape_type": null}}

Example 12 (Sheet with crushed fold):
Input: "sheet length 500mm, sheet width 300mm, left side view: a crushed fold with length 20mm"
Output: {{"expanded_query": "sheet length 500mm, sheet width 300mm, left side view: a crushed fold with length 20mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

Example 13 (Triangle - explicit keyword):
Input: "triangular sheet thickness 2mm"
Output: {{"expanded_query": "triangular sheet thickness 2mm\nShape type: Triangle", "detected_shape_type": "Triangle"}}

Example 14 (Triangle - gusset compound term):
Input: "triangular gusset thickness 5mm"
Output: {{"expanded_query": "triangular gusset thickness 5mm\nShape type: Triangle", "detected_shape_type": "Triangle"}}

Example 15 (Angle bracket keyword OVERRIDES bend count → L-bracket):
Input: "angle bracket bent with 2 bends at 90°"
Output: {{"expanded_query": "angle bracket bent with 2 bends at 90°\nShape type: L-bracket", "detected_shape_type": "L-bracket"}}

Example 16 (Cover + circular = flat plate, NOT CAPOT):
Input: "circular cover Ø200 thickness 3mm with 8 holes"
Output: {{"expanded_query": "circular cover Ø200 thickness 3mm with 8 holes\nShape type: Sheet-Circular", "detected_shape_type": "Sheet-Circular"}}

Example 17 (Cover + rectangular = flat plate, NOT CAPOT):
Input: "rectangular cover 400x300 thickness 4mm"
Output: {{"expanded_query": "rectangular cover 400x300 thickness 4mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

**CRITICAL: Return ONLY valid JSON, no other text.**

**Now expand this query:**
Input: "{query}"
Output:"""

    try:
        from langchain_community.callbacks import get_openai_callback
        import json
        import re
        
        with get_openai_callback() as cb:
            # Call LLM
            response = await llm.ainvoke(expansion_prompt)
            response_text = response.content if hasattr(response, 'content') else str(response)
            response_text = response_text.strip()

            # Track cost if tracker provided. The model name is derived from the
            # llm instance — it was hardcoded to "gpt-5-mini" while the caller
            # actually passes default_llm, so spend was billed to a model that
            # never ran.
            if cost_tracker:
                from src.utils.cost_tracker import resolve_model_name
                cost_tracker.add_chain_cost(
                    "query_expansion", cb, resolve_model_name(llm)
                )
        
        # Try to parse JSON response
        try:
            # Extract JSON from response (in case LLM adds extra text)
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = _loads_lenient(json_match.group(0))
                expanded_query = result.get("expanded_query", query)
                detected_shape_type = _clean_shape_type(result.get("detected_shape_type"))
                
                # Log expansion
                if expanded_query != query:
                    logger.info(
                        f"[QUERY_EXPAND] ✅ LLM Expansion:\n"
                        f"  Original:  '{query}'\n"
                        f"  Expanded:  '{expanded_query}'\n"
                        f"  Shape Type: {detected_shape_type}"
                    )
                else:
                    logger.info(f"[QUERY_EXPAND] ℹ️  No expansion needed (no manufacturing terms detected)")
                
                log_query_expansion(query, expanded_query, str(detected_shape_type))
                
                return {
                    "expanded_query": expanded_query,
                    "detected_shape_type": detected_shape_type
                }
            else:
                logger.warning("[QUERY_EXPAND] No JSON found in LLM response, using fallback")
                return _salvage_from_text(response_text, query)

        except json.JSONDecodeError as je:
            logger.warning(f"[QUERY_EXPAND] Failed to parse JSON: {je}, using fallback")
            return _salvage_from_text(response_text, query)

    except Exception as e:
        logger.error(f"[QUERY_EXPAND] LLM expansion failed: {e}")
        detected = _detect_triangle_shape(query)
        if detected:
            expanded = query if "shape type:" in query.lower() else f"{query}\nShape type: {detected}"
            log_query_expansion(query, expanded, detected)
            return {"expanded_query": expanded, "detected_shape_type": detected}
        return {"expanded_query": query, "detected_shape_type": None}


