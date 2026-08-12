"""
Query Expansion for Manufacturing Terminology using LLM with Few-Shot Learning

This module uses LLM with few-shot examples to intelligently expand user queries 
by APPENDING shape type classification at the end to improve semantic search retrieval accuracy.

The LLM learns from examples to identify manufacturing terminology
in multiple languages (English, French, Vietnamese) and append the canonical English shape type.

Strategy: SHAPE TYPE APPEND (keep original, add classification at end)
- "tôle avec un pli" → "tôle avec un pli\nShape type: L-bracket"
- "cornière 55x50" → "cornière 55x50\nShape type: L-bracket"
- "tube carré" → "tube carré\nShape type: Tube-Rectangular"
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
    """Deterministic fallback for the Triangle shape family (EN + FR only).
    
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
        # English — clear triangle identifiers
        "equilateral triangle",
        "isosceles triangle",
        "triangular sheet",
        "triangular plate",
        "triangular tray",
        "triangle tray",
        "triangle",
        "triangular",
        # French — generic
        "plaque triangulaire",
        "tole triangulaire",          # ASCII fallback for tôle
        "piece triangulaire",         # ASCII fallback for pièce
        "forme triangulaire",
        "platine triangulaire",
        "flan triangulaire",
        "ebauche triangulaire",       # ASCII fallback for ébauche
        "decoupe triangulaire",       # ASCII fallback for découpe
        # French — industrial / usage
        "gousset triangulaire",
        "gousset",
        "renfort triangulaire",
        "equerre triangulaire",       # ASCII fallback for équerre triangulaire
        "plaque de renfort triangulaire",
        "base triangulaire",
        "socle triangulaire",
    ]
    # Also check with Unicode accents present
    triangle_terms_unicode = [
        "tôle triangulaire",
        "pièce triangulaire",
        "ébauche triangulaire",
        "découpe triangulaire",
        "équerre triangulaire",
    ]
    if any(term in query_lower for term in triangle_terms + triangle_terms_unicode):
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
- "arrière", "dessus", "haut(e)", "du haut", "plan du haut", "côté du haut", "grand côté du haut", "face longitudinale du haut", "face transversale"
  → Replace with: **"Face arrière"**

- "avant", "dessous", "bas(se)", "du bas", "plan du bas", "côté du bas", "grand côté du bas", "face longitudinale du bas"
  → Replace with: **"Face avant"**

- "base", "inférieur(e)", "embase", "référence", "plan inférieur", "face de référence"
  → Replace with: **"Base"**

- "gauche", "à gauche", "de gauche", "plan gauche", "côté gauche", "latéral gauche", "petit côté gauche", "extrémité gauche"
  → Replace with: **"Face de gauche"**

- "droite", "à droite", "de droite", "plan droit", "côté droit", "latéral droit", "petit côté droit", "extrémité droite"
  → Replace with: **"Face de droite"**

**Normalization Examples:**
- "Sur le plan du haut" → "Sur la Face arrière"
- "du bas avec perçage" → "Face avant avec perçage"
- "côté gauche et côté droit" → "Face de gauche et Face de droite"

**STEP 2: SHAPE TYPE DETECTION**

After normalization, detect shape type and APPEND it.

**Output Format:**
[NORMALIZED QUERY]
Shape type: [DETECTED SHAPE]

**Synonym Mapping & Core Categories:**
*Note: French terms 'languette', 'patte', 'oreille', 'rebord', 'flasque', 'bride' and English 'tab', 'lug', 'ear', 'lip', 'flange' represent folds/bends/returns/flanges.*

1. **Sheet Metal (Flat, Unbent):**
   - French/English flat plate keywords: tôle, platine, plaque, sheet, plate, flat panel, flat part.
   - If rectangular or unspecified: **Sheet**
   - If circular/round/disc: **Sheet-Circular** (keywords: disque, tôle ronde, plaque ronde, rond, disc, round plate, circular sheet, circular disc, diameter/Ø with no length, bride, mặt bích).

2. **L-bracket (1 bend/tab/lug):**
   - French/English L keywords: cornière, équerre, console en L, tôle un pli, équerre pliée, une languette/patte pliée.
   - If base is rectangular: **L-bracket**
   - If base is circular/round: **L-bracket-Circular** (keywords: équerre ronde, tôle ronde un pli, circular L-bracket, bride un pli).
   - **CRITICAL KEYWORD OVERRIDE**: If the query contains `cornière` or `équerre` (without the adjective "triangulaire"), classify as **L-bracket** regardless of the number of bends mentioned. Example: "équerre pliée avec 2 plis" → **L-bracket** (not U-shaped), because équerre is an explicit L-bracket keyword.

3. **U-Shaped (2 bends/tabs in same direction):**
   - French/English U keywords: profilé U, U, tôle deux plis, console en U, channel, deux retours/ailes parallèles, deux languettes/pattes pliées (dans le même sens / même côté).
   - If base is rectangular: **U-shaped**
   - If base is circular/round: **U-shaped-Circular** (keywords: tôle ronde deux plis même sens, circular U-shaped, bride deux plis, mặt bích hai nếp gấp).

4. **Z-Shaped (2 bends/tabs in opposite directions OR "selon Z" / "en Z"):**
   - French/English Z keywords: profilé Z, Z, tôle en Z, deux plis opposés, plis en sens opposés, pliage en Z, pli en Z.
   - If base is rectangular: **Z-shaped**
   - If base is circular/round: **Z-shaped-Circular** (keywords: tôle ronde en Z, circular Z-shaped, bride pliée en Z, mặt bích gập chữ Z).

5. **Tubes:**
   - If circular cross-section: **Tube-Circular** (French: tube rond, tube cylindrique, pipe, cylindre creux, solid round bar, rond plein, barre ronde).
   - If rectangular/square cross-section: **Tube-Rectangular** (French: tube carré, tube rectangulaire, square tube).

6. **CAPOT / Covers (3-4 walls/bends):**
   - French: bac, couvercle, capot, boîte ouverte, capot ouvert, tôle trois/quatre plis, plis sur 3/4 sides.
   - Shape type: **CAPOT** (default — all walls bend the same angle/direction, built with makeTub)
   - **SUBTYPE OVERRIDE** — if walls bend in independent/different directions or each wall has its own distinct angle (e.g. "chaque côté a son propre pli", "plis indépendants", "un côté vers le haut, l'autre vers le bas", "mixed direction"), use **CAPOT-mixed-direction** instead.
   - **CRITICAL DISAMBIGUATION** — `couvercle` + shape adjective (circulaire, carré, rectangulaire, rond) = **flat plate**, NOT CAPOT.
     Examples: "couvercle circulaire Ø200" → Sheet-Circular. "couvercle carré 300x300" → Sheet. "couvercle rectangulaire" → Sheet.
     Only classify as CAPOT when "couvercle" describes an enclosure/box with walls (e.g. "capot avec trois parois", "bac acier avec dimensions L×l×H").
   - **CRITICAL DISAMBIGUATION** — `fond circulaire` / `base circulaire` / `base ronde` as standalone parts = **flat plate** (Sheet-Circular). Only treat as face names when they appear in a capot context.

7. **I-Shaped / T-Shaped Profiles:**
   - I-Shaped: profilé I, poutre en I, I-beam → **I-Shaped**
   - T-Shaped: profilé T, fer en T, T-bar → **T-Shaped**

8. **Triangle (triangular sheet/plate, flat or with edge flanges):**
   - Keywords (EN): triangle, triangular, triangular plate, triangular sheet, equilateral triangle, isosceles triangle, triangular tray.
   - Keywords (FR — unambiguous compounds only): plaque triangulaire, tôle triangulaire, platine triangulaire, flan triangulaire, plaque en triangle, gousset triangulaire, renfort triangulaire, équerre triangulaire.
   - Shape type: **Triangle**
   - A triangular base with edge flanges remains **Triangle**. Do NOT reclassify as L-bracket, U-shaped, Z-shaped, or CAPOT based only on flange count.
   - **CRITICAL**: "équerre triangulaire" → **Triangle**, NOT L-bracket. "gousset triangulaire" → **Triangle**, NOT plate.

**CRITICAL RULES (Priority Order):**
- **PRIORITY 0: Pre-declared Type:** If query has "Type: [Shape]" or "Shape type: [Shape]", use that exact type.
- **PRIORITY 1: Circular vs Rectangular:** Make sure to distinguish circular flat/folded plates (`Sheet-Circular`, `L-bracket-Circular`, `U-shaped-Circular`, `Z-shaped-Circular`) from their rectangular counterparts when the base shape is circular (disc, circle, Ø diameter, disque, tôle ronde, plaque ronde, bride).
- **PRIORITY 2: Structural:** console/tablette/équerre murale with components → Z-shaped shelf-bracket.
- **PRIORITY 3: Explicit Keywords:** cornière/équerre → L-bracket; profilé U/console U → U-shaped; profilé Z/fixation Z → Z-shaped; tube/cylindre → Tube-Circular / Tube-Rectangular; capot/cover → CAPOT.
- **PRIORITY 3b: Triangular base shape:** triangle/triangular/plaque triangulaire/gousset triangulaire → Triangle. Triangle overrides generic sheet/plate wording and its edge flanges do NOT imply L/U/Z/CAPOT. "gousset" WITHOUT "triangulaire" is ambiguous — do NOT auto-classify as Triangle.
- **PRIORITY 4: Return Bends / Secondary Folds & Flat Plate Operations (MANDATORY override):**
  * Before counting bends, decide whether each fold is a **primary profile bend** or a **secondary edge treatment**.
  * Primary profile bends create the main section shape (L/U/Z/CAPOT), usually structural flanges/walls.
  * Secondary edge treatments modify a flat sheet edge without changing the core shape family.
  * Bends folded on top of other bends (e.g. "pli retour", "retour d'aile", "double pli", "return bend", "hem", "lip") are **secondary operations**, not primary profile bends.
  * Crushed folds / plis écrasés (180° hem bends) and offsets / soyages are **secondary operations** / flat plate operations.
  * Crushed fold detection must be semantic, not exact keyword matching. Treat terms such as "pli ecrase"/"pli écrasé", "repli ecrase"/"repli écrasé", "pli a 180"/"pli à 180", "ourlet ouvert", "rabat", "rabattement", "pli anglais"/"plis anglais", "pli aplati", "crushed fold", "hem fold", "open hem", and "return fold" as examples of the same 180° hem/return concept. Also tolerate minor typos when the manufacturing meaning is clear (e.g. "ourlett ouvert", "plis anglai"). These examples are NOT an exhaustive keyword list.
  * If a sheet has ONLY crushed folds (plis écrasés) and/or offsets (soyages) without any other standard profile bends (e.g., 90° bends), it MUST be classified as **Sheet** (or **Sheet-Circular** if circular), NOT L-bracket/U-shaped/Z-shaped.
  * Do **NOT** count return/secondary bends, crushed folds (plis écrasés), or offsets (soyages) when counting bends for shape classification.
  * *Example 1*: 1 primary bend + 1 return bend → count = **1 primary bend** → Shape type: L-bracket.
  * *Example 2*: Flat sheet + 1 crushed fold (pli écrasé) → count = **0 primary bends** → Shape type: Sheet.
  * *Example 3*: Flat sheet + 1 crushed fold + 1 soyage → count = **0 primary bends** → Shape type: Sheet.
- **PRIORITY 5: Bend Pattern Analysis:**
  * 1 bend / 1 tab / 1 flange → Shape type: L-bracket (or L-bracket-Circular if circular)
  * 2 bends/tabs (same direction/parallel/same sens) → Shape type: U-shaped (or U-shaped-Circular if circular)
  * 2 bends/tabs (opposite directions/opposés/selon Z/en Z) → Shape type: Z-shaped (or Z-shaped-Circular if circular)
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

Example 2 (Explicit keyword - cornière):
Input: "cornière de section 55x50x5"
Output: {{"expanded_query": "cornière de section 55x50x5\nShape type: L-bracket", "detected_shape_type": "L-bracket"}}

Example 3 (Explicit keyword - tube):
Input: "tube carré 25x25x2"
Output: {{"expanded_query": "tube carré 25x25x2\nShape type: Tube-Rectangular", "detected_shape_type": "Tube-Rectangular"}}

Example 4 (No shape - flat sheet):
Input: "platine 200x150 épaisseur 4mm"
Output: {{"expanded_query": "platine 200x150 épaisseur 4mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

Example 5 (Circular flat sheet):
Input: "disque de diamètre 200mm et épaisseur 3mm"
Output: {{"expanded_query": "disque de diamètre 200mm et épaisseur 3mm\nShape type: Sheet-Circular", "detected_shape_type": "Sheet-Circular"}}

Example 6 (Circular L-bracket):
Input: "tôle ronde diamètre 180 mm épaisseur 2 mm avec un pli à 90 degrés à offset 40 mm du centre"
Output: {{"expanded_query": "tôle ronde diamètre 180 mm épaisseur 2 mm avec un pli à 90 degrés à offset 40 mm du centre\nShape type: L-bracket-Circular", "detected_shape_type": "L-bracket-Circular"}}

Example 7 (Circular U-shaped):
Input: "tấm tròn đường kính 200mm dày 2mm gập 2 lần cùng chiều hướng lên ở khoảng cách -30mm et 30mm"
Output: {{"expanded_query": "tấm tròn đường kính 200mm dày 2mm gập 2 lần cùng chiều hướng lên ở khoảng cách -30mm et 30mm\nShape type: U-shaped-Circular", "detected_shape_type": "U-shaped-Circular"}}

Example 8 (Circular Z-shaped):
Input: "plaque ronde Ø150 épaisseur 1.5mm pliée en Z avec un pli vers le haut à -35mm et un pli vers le bas à 35mm"
Output: {{"expanded_query": "plaque ronde Ø150 épaisseur 1.5mm pliée en Z avec un pli vers le haut à -35mm et un pli vers le bas à 35mm\nShape type: Z-shaped-Circular", "detected_shape_type": "Z-shaped-Circular"}}

Example 9 (CONTEXTUAL - 2 parallel bends = U-shaped):
Input: "tôle avec deux plis parallèles"
Output: {{"expanded_query": "tôle avec deux plis parallèles\nShape type: U-shaped", "detected_shape_type": "U-shaped"}}

Example 10 (CONTEXTUAL - 4 bends = CAPOT):
Input: "tôle pliée avec quatre plis à 90° formant un bac"
Output: {{"expanded_query": "tôle pliée avec quatre plis à 90° formant un bac\nShape type: CAPOT", "detected_shape_type": "CAPOT"}}

Example 10b (CAPOT with independent per-wall directions = CAPOT-mixed-direction):
Input: "boite avec 4 plis, plis haut et droit vers le haut, plis bas et gauche vers le bas"
Output: {{"expanded_query": "boite avec 4 plis, plis haut et droit vers le haut, plis bas et gauche vers le bas\nShape type: CAPOT-mixed-direction", "detected_shape_type": "CAPOT-mixed-direction"}}

Example 11 (No manufacturing terms):
Input: "create a simple box"
Output: {{"expanded_query": "create a simple box", "detected_shape_type": null}}

Example 12 (Sheet with crushed fold):
Input: "longueur de la tôle 500mm, largeur de la tôle 300mm, côté vue de gauche: un pli écrasé avec longueur 20mm"
Output: {{"expanded_query": "longueur de la tôle 500mm, largeur de la tôle 300mm, côté vue de gauche: un pli écrasé avec longueur 20mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

Example 13 (Triangle - EN keyword):
Input: "triangular sheet épaisseur 2mm"
Output: {{"expanded_query": "triangular sheet épaisseur 2mm\nShape type: Triangle", "detected_shape_type": "Triangle"}}

Example 14 (Triangle - FR compound term, gousset triangulaire):
Input: "gousset triangulaire épaisseur 5mm"
Output: {{"expanded_query": "gousset triangulaire épaisseur 5mm\nShape type: Triangle", "detected_shape_type": "Triangle"}}

Example 15 (Équerre keyword OVERRIDES bend count → L-bracket):
Input: "équerre pliée avec 2 plis à 90°"
Output: {{"expanded_query": "équerre pliée avec 2 plis à 90°\nShape type: L-bracket", "detected_shape_type": "L-bracket"}}

Example 16 (Couvercle + circulaire = flat plate, NOT CAPOT):
Input: "couvercle circulaire Ø200 épaisseur 3mm avec 8 trous"
Output: {{"expanded_query": "couvercle circulaire Ø200 épaisseur 3mm avec 8 trous\nShape type: Sheet-Circular", "detected_shape_type": "Sheet-Circular"}}

Example 17 (Couvercle + rectangulaire = flat plate, NOT CAPOT):
Input: "couvercle rectangulaire 400x300 épaisseur 4mm"
Output: {{"expanded_query": "couvercle rectangulaire 400x300 épaisseur 4mm\nShape type: Sheet", "detected_shape_type": "Sheet"}}

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


def expand_query_with_synonyms(query: str, max_synonyms: int = 5) -> str:
    """
    Synchronous wrapper for LLM-based query expansion.
    
    Falls back to original query if LLM is not available.
    
    Args:
        query: Original user query
        max_synonyms: Maximum number of synonyms to add
        
    Returns:
        Expanded query string (extracts from dict if needed)
    """
    if _expansion_llm is None:
        logger.debug("[QUERY_EXPAND] No LLM available, returning original query")
        return query
    
    try:
        # Run async expansion in sync context
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import asyncio
            future = asyncio.ensure_future(expand_query_with_llm(query, max_synonyms=max_synonyms))
            logger.warning("[QUERY_EXPAND] Already in async context, skipping expansion")
            return query
        else:
            result = loop.run_until_complete(expand_query_with_llm(query, max_synonyms=max_synonyms))
            
            # Handle both old (string) and new (dict) return format
            if isinstance(result, dict):
                return result.get("expanded_query", query)
            else:
                return result
    except Exception as e:
        logger.error(f"[QUERY_EXPAND] Sync expansion failed: {e}")
        return query
