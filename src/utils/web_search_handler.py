import re
import logging
import os
import asyncio
from typing import Dict, Tuple, List
from urllib.parse import urlparse
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class WebSearchProcessor:
    """
    Processor for detecting URLs in user input and extracting web content
    using OpenAI's web search tool.

    All methods are now async to prevent blocking the event loop.
    """

    def __init__(self, cost_tracker=None):
        """Initialize the WebSearchProcessor with AsyncOpenAI client.
        
        Args:
            cost_tracker: Optional CostTracker instance to track OpenAI costs
        """
        try:
            self.client = AsyncOpenAI()
            self.cost_tracker = cost_tracker
            logger.debug("WebSearchProcessor initialized successfully with AsyncOpenAI")
        except Exception as e:
            logger.error(f"Failed to initialize AsyncOpenAI client: {e}")
            self.client = None
    
    def detect_urls(self, text: str) -> List[str]:
        """
        Detect URLs in the given text.
        
        Args:
            text (str): Input text to scan for URLs
            
        Returns:
            List[str]: List of detected URLs
        """
        # Enhanced URL regex pattern to handle complex URLs with special characters
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+(?:[^\s<>"{}|\\^`\[\].,!?;:])'
        urls = re.findall(url_pattern, text)
        
        # Also check for common URL patterns without protocol
        no_protocol_pattern = r'(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?'
        potential_urls = re.findall(no_protocol_pattern, text)
        
        # Add http:// to URLs without protocol
        for url in potential_urls:
            if not url.startswith(('http://', 'https://')):
                full_url = f'https://{url}'
                if self._is_valid_url(full_url) and full_url not in urls:
                    urls.append(full_url)
        
        return urls
    
    def _is_valid_url(self, url: str) -> bool:
        """
        Validate if a string is a valid URL.

        Args:
            url (str): URL to validate

        Returns:
            bool: True if valid URL, False otherwise
        """
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def _extract_dimension_values(self, text: str, dimension_type: str = "length") -> List[str]:
        """
        Extract all dimension values of a specific type from text.

        Args:
            text (str): Text to analyze
            dimension_type (str): Type of dimension (length, width, height, thickness, diameter, etc.)

        Returns:
            List[str]: List of unique dimension values found
        """
        # Pattern to match dimension values (numbers with units like mm, cm, etc.)
        # Matches: 100, 1000mm, 50.5cm, 10x20, etc.
        patterns = [
            r'\d+\.?\d*\s*(?:mm|cm|m|inches?|")?',  # Single values with units
        ]

        dimension_values = []
        for pattern in patterns:
            matches = re.findall(pattern, text.lower())
            dimension_values.extend(matches)

        # Remove duplicates while preserving order
        unique_values = []
        seen = set()
        for val in dimension_values:
            if val not in seen:
                unique_values.append(val)
                seen.add(val)

        return unique_values

    # ── Canonical shape tag mapping ─────────────────────────────────────────
    # Maps the product_type strings already detected from the URL path to the
    # canonical shape_type values expected by unified_analysis_template.
    # Only covers the product_type values that _extract_product_info_from_url
    # can produce — no new keyword detection added here.
    _URL_PRODUCT_TYPE_TO_CANONICAL = {
        "Perforated sheet": "Perforated Sheet",
        "Tube":             "Tube-Circular",   # generic tube from URL → circular (rectangular URLs usually spell out "square"/"rectangular")
        "sheet":            "Sheet",
        "L-shaped bracket": "L-bracket",
        "U-shaped":         "U-shaped",
        "I-Shaped":         "I-Shaped",
        "T-Shaped":         "T-Shaped",
    }

    def _normalize_shape_tag(self, content: str) -> str:
        """
        Normalize conservative web-search fallback tags before unified analysis.

        Web extraction can return a useful flat-part description with an
        ``unknown`` tag. For clear plate/sheet wording, keep the description and
        only promote the machine tag to Sheet. Perforated tags stay untouched.
        """
        if not content:
            return content

        if re.match(r"^\[Shape type:\s*Perforated(?:\s*Sheet)?\]", content, re.IGNORECASE):
            return content

        unknown_match = re.match(r"^\[Shape type:\s*unknown\](.*)$", content, re.IGNORECASE | re.DOTALL)
        if not unknown_match:
            return content

        description = unknown_match.group(1).strip()
        desc_lower = description.lower()
        sheet_terms = (
            "sheet", "plate", "panel", "disc", "disk",
            "washer", "blank", "cover", "flange plate",
        )

        if any(term in desc_lower for term in sheet_terms):
            return f"[Shape type: Sheet] {description}"

        return content

    def _extract_product_info_from_url(self, url: str) -> str:
        """
        Extract product information from URL when web content extraction fails.
        Enhanced to detect all perforated sheet patterns: R, C, LR, LC with T, U, Z pitch.
        Always prepends [Shape type: <canonical>] so unified_analysis can read it directly.

        Args:
            url (str): URL to extract information from

        Returns:
            str: Product description starting with "[Shape type: X] ..."
        """
        try:
            from urllib.parse import unquote

            # Parse URL to get path
            parsed = urlparse(url)
            path = unquote(parsed.path)  # Decode URL encoding

            # Look for product type keywords first.
            # NOTE: the French keywords below are NOT product language — they are
            # literal URL slugs published by the supplier sites we scrape
            # (tolery.io and similar). They must stay to keep URL-only fallback
            # working; the chatbot's own output is English regardless.
            product_type = None
            path_lower = path.lower()

            if 'perfor' in path_lower or 'perforé' in path_lower or 'perfore' in path_lower:
                product_type = "Perforated sheet"
            elif 'tube' in path_lower:
                product_type = "Tube"
            elif (
                'tôle' in path_lower or 'tole' in path_lower or 'sheet' in path_lower
                or 'plaque' in path_lower or 'platine' in path_lower
            ):
                product_type = "sheet"
            elif 'cornière' in path_lower or 'corner' in path_lower or 'l-shaped' in path_lower:
                product_type = "L-shaped bracket"
            elif 'u-shaped' in path_lower or 'chute' in path_lower:
                product_type = "U-shaped"
            elif 'poutre' in path_lower or 'ipe' in path_lower or 'i-shaped' in path_lower:
                product_type = "I-Shaped"
            elif 't-shaped' in path_lower or 'fer en t' in path_lower:
                product_type = "T-Shaped"

            # Map product_type to canonical shape_type (no new keywords — reuses existing var)
            canonical_shape = self._URL_PRODUCT_TYPE_TO_CANONICAL.get(product_type, "unknown")
            shape_tag = f"[Shape type: {canonical_shape}]"

            # ENHANCED PATTERN DETECTION for perforated sheets
            product_code = None

            # Pattern 1: LR/LC oblong patterns (e.g., lr5x20-z9x24, lc10x100-u30x40, lr5x20)
            oblong_pattern = r'(l[rc]\d+x\d+(?:[-_\s]?[tuz]\d+(?:x\d+)?)?)'
            oblong_matches = re.findall(oblong_pattern, path_lower)

            if oblong_matches:
                match = oblong_matches[0]
                parts = re.split(r'[-_\s]+', match)
                shape_part = parts[0].upper()
                shape_part = shape_part.replace('x', 'X').replace('X', 'x', 1).replace('x', 'X')

                pitch_part = None
                if len(parts) > 1 and parts[1]:
                    pitch_part = parts[1].upper()
                    pitch_part = pitch_part.replace('x', 'X').replace('X', 'x', 1).replace('x', 'X')

                product_code = f"{shape_part} {pitch_part}" if pitch_part else shape_part
                # If product_code looks like a perforated pattern, override canonical
                if canonical_shape == "unknown":
                    shape_tag = "[Shape type: Perforated Sheet]"

            # Pattern 2: C square patterns (e.g., c20-u40, c25-u30, c20)
            if not product_code:
                square_pattern = r'(c\d+(?:[-_\s]?[tu]\d+)?)'
                square_matches = re.findall(square_pattern, path_lower)

                if square_matches:
                    match = square_matches[0]
                    parts = re.split(r'[-_\s]+', match)
                    shape_part = parts[0].upper()
                    pitch_part = parts[1].upper() if len(parts) > 1 and parts[1] else None
                    product_code = f"{shape_part} {pitch_part}" if pitch_part else shape_part
                    if canonical_shape == "unknown":
                        shape_tag = "[Shape type: Perforated Sheet]"

            # Pattern 3: R round patterns (e.g., r12-t16, r10-u20, r12)
            # ⚠️ Validate diameter is 1–250mm to avoid false-positives like
            # "inox-304", "inox-316" being matched as r304 or r316.
            if not product_code:
                round_pattern = r'(?<![a-z])(r(\d+(?:\.\d+)?)(?:[-_\s]?[tu]\d+(?:\.\d+)?)?)(?![a-z\d])'
                round_matches = re.findall(round_pattern, path_lower)

                if round_matches:
                    # round_matches is list of (full_match, diameter_str)
                    full_match = round_matches[0][0]
                    diameter_str = round_matches[0][1]
                    try:
                        diameter_val = float(diameter_str)
                    except ValueError:
                        diameter_val = 0
                    # Only accept as a perforation hole if diameter is plausible (1–250mm)
                    # Reject steel/stainless grades like 304, 316, 430, etc.
                    if 1 <= diameter_val <= 250:
                        parts = re.split(r'[-_\s]+', full_match)
                        shape_part = parts[0].upper()
                        pitch_part = parts[1].upper() if len(parts) > 1 and parts[1] else None
                        product_code = f"{shape_part} {pitch_part}" if pitch_part else shape_part
                        if canonical_shape == "unknown":
                            shape_tag = "[Shape type: Perforated Sheet]"

            # Build description — always prepend shape_tag
            if product_type and product_code:
                desc = "sheet " + product_code if product_type == "sheet" else f"{product_type} {product_code}"
            elif product_code:
                desc = f"Product {product_code}"
            elif product_type:
                desc = product_type
            else:
                desc = url

            result = f"{shape_tag} {desc}"
            logger.info(f"[WEB_EXTRACT] URL fallback description: '{result}'")
            return result

        except Exception as e:
            logger.debug(f"Error extracting product info from URL {url}: {e}")
            return f"[Shape type: unknown] {url}"
    
    def _is_error_response(self, content: str) -> bool:
        """
        Check if the extracted content is an error message instead of actual product specs.
        
        Args:
            content (str): Content to check
            
        Returns:
            bool: True if content appears to be an error message
        """
        if not content:
            return True
            
        content_lower = content.lower()
        
        # Error indicators
        error_indicators = [
            "i attempted",
            "could not load",
            "unable to",
            "cannot access",
            "blocked",
            "appears to block",
            "options — tell me",
            "paste the product",
            "allow me to try",
            "i'm unable",
            "i can't",
            "error accessing",
            "failed to",
            "could not retrieve"
        ]
        
        # Check if content contains error indicators
        for indicator in error_indicators:
            if indicator in content_lower:
                return True
        
        # Check if content is too long and contains error-like phrases
        if len(content) > 200 and any(phrase in content_lower for phrase in ["tell me which", "prefer", "options"]):
            return True
            
        return False

    def _validate_multiple_values(self, text: str) -> Dict[str, any]:
        """
        Validate if text contains multiple different values for the same dimension.

        Args:
            text (str): Text to analyze

        Returns:
            Dict: Dictionary containing analysis results with keys:
                - has_multiple_values: bool
                - dimension_conflicts: dict of dimension->list of values
                - should_use_placeholder: bool
        """
        # Common dimension keywords in text
        dimension_keywords = {
            "length": [r"length\s*:?\s*(\d+)", r"l\s*=\s*(\d+)", r"(\d+)\s*length"],
            "width": [r"width\s*:?\s*(\d+)", r"w\s*=\s*(\d+)", r"(\d+)\s*width"],
            "height": [r"height\s*:?\s*(\d+)", r"h\s*=\s*(\d+)", r"(\d+)\s*height"],
            "diameter": [r"diameter\s*:?\s*(\d+)", r"dia\s*:?\s*(\d+)", r"(\d+)\s*diameter"],
            "thickness": [r"thickness\s*:?\s*(\d+)", r"t\s*=\s*(\d+)", r"(\d+)\s*thickness"],
        }

        conflicts = {}
        has_multiple = False

        text_lower = text.lower()

        for dimension, patterns in dimension_keywords.items():
            values = []
            for pattern in patterns:
                matches = re.findall(pattern, text_lower)
                values.extend(matches)

            # Remove duplicates
            unique_values = list(set(values))

            if len(unique_values) > 1:
                conflicts[dimension] = unique_values
                has_multiple = True
                logger.warning(f"Multiple {dimension} values detected: {unique_values}")

        return {
            "has_multiple_values": has_multiple,
            "dimension_conflicts": conflicts,
            "should_use_placeholder": has_multiple
        }
    
    def _filter_geometric_only(self, content: str) -> str:
        """
        Post-process OpenAI output to keep only shape/dimension info.

        KEPT (needed for CAD generation):
          - Shape + dimensions: diameter, width, height, thickness, length
          - Perforated pitch codes: T16, U40, Z9x24, R12, C20, LR5x20, LC5x50
          - M-size for Countersink/Threaded: M6, M8, M12 (these are dimensions, not standards)
          - Countersink angle: 90°, 82°, 120° (needed to lookup ISO table)

        REMOVED (noise for chatbot):
          - Material grades: S235, E24, A36, Fe360
          - Material standards: EN 10060, DIN 1025, NF A35 (4+ digit norm codes)
          - Surface state: as-rolled, galvanised, hot-rolled
          - Weight: weight per metre, kg/m
          - Cutting tolerances: un-deburred cut, +/- 1mm
          - Commercial: price, stock

        Args:
            content (str): Raw extracted content from OpenAI

        Returns:
            str: Filtered content with only geometric information
        """
        if not content:
            return content

        # NOTE: the French patterns below are NOT product language — they strip
        # commercial boilerplate that the supplier pages we scrape publish in
        # French. They are a defensive post-filter on third-party page text; the
        # extraction prompt already asks for English output.
        non_geometric_patterns = [
            # --- Material grades ---
            # "nuance S235", "nuance E24 ou S235"
            r',?\s*nuance\s+[^,]+',
            # "matière acier standard"
            r',?\s*matière\s+[^,]+',
            # "acier standard de construction / inoxydable / galvanisé"
            r',?\s*acier\s+(?:standard|de\s+construction|inoxydable|galvanis\w*)[^,]*',
            # "steel grade S355"
            r',?\s*steel\s+grade\s+[^,]+',
            # "grade S235" — only letter+digits steel codes, NOT "M6"
            r',?\s*\bgrade\s+[A-EG-LN-Z]\d+\w*\b[^,]*',

            # --- Material norms (4+ digit codes only, to preserve M6/M8) ---
            # "norme EN 10060", "norme DIN 1025"
            r',?\s*norme\s+[^,]+',
            # "EN 10060" (5-digit norm) — NOT "EN" alone or M-size
            r',?\s*\bEN\s+\d{4,}[^,]*',
            # "DIN 1025" etc.
            r',?\s*\bDIN\s+\d{3,}[^,]*',
            # "ISO 1234" (material standard with 4+ digits) — NOT "ISO 9001" style kept for context
            r',?\s*\bISO\s+\d{4,}[^,]*',
            # "NF A35-501" etc.
            r',?\s*\bNF\s+[A-Z]\d+[^,]*',
            # "ASTM A36"
            r',?\s*\bASTM\s+[^,]+',

            # --- Surface state / finish ---
            r',?\s*état\s+brut[^,]*',
            r',?\s*brut\s+de\s+laminage[^,]*',
            r',?\s*laminé\s+à\s+chaud[^,]*',
            r',?\s*laminé\s+à\s+froid[^,]*',
            r',?\s*galvanis[eé][^,]*',
            r',?\s*\bhot[- ]rolled[^,]*',
            r',?\s*\bcold[- ]rolled[^,]*',

            # --- Weight per unit ---
            r',?\s*poids\s+(?:au|par)\s+m[eè]tre[^,]*',
            r',?\s*\d+[\.,]\d*\s*kg\s*/\s*m[^,]*',
            r',?\s*weight[^,]*\bkg\b[^,]*',

            # --- Cutting tolerances / delivery notes ---
            r',?\s*coupe\s+non\s+[^,]+',
            r',?\s*ébavur[eé][^,]*',
            r',?\s*tolérance\s+[^,]+',
            # "+/- 1 mm" style tolerances — NOT hole diameter tolerances like "±0.1"
            r',?\s*[+\-±]{1,2}\s*/?\s*[+\-]?\s*\d+\s*mm\b[^,]*',

            # --- Commercial info ---
            r',?\s*\bprix\b[^,]+',
            r',?\s*\bprice\b[^,]+',
            r',?\s*\bstock\b[^,]+',
        ]

        filtered = content
        for pattern in non_geometric_patterns:
            filtered = re.sub(pattern, '', filtered, flags=re.IGNORECASE)

        # Cleanup: remove duplicate commas / leading-trailing commas / extra spaces
        filtered = re.sub(r',\s*,', ',', filtered)
        filtered = re.sub(r'^[,\s]+', '', filtered)
        filtered = re.sub(r'[,\s]+$', '', filtered)
        filtered = filtered.strip()

        if filtered != content:
            logger.info(f"[WEB_EXTRACT] 🔍 Filtered non-geometric info → '{filtered}'")

        return filtered

    async def extract_web_content(self, url: str) -> Dict[str, str]:
        """
        Extract content from a web URL using OpenAI's web search tool (ASYNC).

        Args:
            url (str): URL to extract content from

        Returns:
            Dict[str, str]: Dictionary containing extracted content and metadata
        """
        if not self.client:
            logger.warning(f"[WEB_EXTRACT] AsyncOpenAI client not initialized, extracting info from URL")
            url_based_description = self._extract_product_info_from_url(url)
            logger.info(f"[WEB_EXTRACT] Using URL-based description: {url_based_description}")
            return {
                "success": False,
                "error": "AsyncOpenAI client not initialized",
                "content": url_based_description,  # Return URL-based description as fallback
                "url": url,
                "used_url_fallback": True
            }

        try:
            # Natural language prompt for simple, readable output
            prompt = f"""
Access the product page whose URL is given in the `TARGET URL` section at the
very END of this prompt, and analyze it to extract technical specifications.

CRITICAL EXTRACTION RULES:
1. Extract ONLY the dimensions and specifications that are clearly provided
2. Remove any external links or references from the output
3. Use only the dimensions that are actually specified on the product page
4. **IMPORTANT**: If the page shows multiple different values for the same dimension (e.g., length: 1/2/3/4/5/6), keep the placeholder [length] and do NOT extract specific values. Only use specific values when exactly ONE value is provided.
5. **EXTRACT HOLE POSITION INFORMATION**: If the product has holes (perforations, cutouts, mounting holes, etc.), extract and describe their positions/locations when available
6. **LANGUAGE RULE (MANDATORY)**: Write ALL output in English, whatever language the web page uses. Translate descriptive labels (shape names, dimension labels, position descriptions) into English — "sheet", "thickness", "length", "width", "bracket", "tube", "perforated sheet", "holes at corners", etc. Technical codes (R12, T16, C20, U40, LR5x20, Z9x24, etc.) and numeric values are universal — keep them unchanged.
7. **EXCLUDE non-geometric information (MANDATORY)**: Do NOT include any of the following in your output:
   - Material grades or steel grades (e.g., S235, E24, A36, S355, Fe360, St37, etc.)
   - Norms or standards (e.g., EN 10060, DIN 1025, ISO, NF, ASTM, etc.)
   - Surface finish or state (e.g., as-rolled, galvanised, hot-rolled, mill finish, etc.)
   - Weight per meter or unit weight (e.g., kg/m, lbs/ft, etc.)
   - Cutting tolerances or delivery notes (e.g., unde-burred cut, +/- 1 mm, etc.)
   - Price, stock, or commercial information
   - Keep ONLY: shape type, geometric dimensions (length, width, height, thickness, diameter), and hole pattern/position

SHAPE TYPE CLASSIFICATION (MANDATORY — do this FIRST before writing the description):
Before writing the product description, identify which canonical shape best matches the product.
Use your understanding of the product — name, form, cross-section — to pick ONE from this list:

| Canonical shape_type    | What it is                                                                |
|-------------------------|--------------------------------------------------------------------------|
| Sheet                   | Flat plate / flat sheet (rectangular OR round/disc/washer). May have INDIVIDUAL holes (drilled holes, tapped holes, mounting holes, cutouts, notches, slots, a centre hole, corner holes). ⚠️ The presence of individual holes does NOT make the part a Perforated Sheet. |
| Perforated Sheet         | Perforated sheet. Detected if AT LEAST ONE of the following is present: (1) a perforation code (R12 T16, C20 U40, LR5x20 Z9x24, LC10x100 U30x40...) OR (2) an explicit keyword ("perforated" / "perforated sheet" / "sheet with a hole pattern"). ⚠️ Individual holes (mounting, corners, centre) alone WITHOUT these indicators → Sheet. |
| Tube-Circular           | (A) Hollow tube: round tube / cylindrical tube / pipe / round pipe. Has wall thickness. (B) Solid bar: **solid round bar / round rod** — NO wall thickness. Both subtypes → Tube-Circular. |
| Tube-Rectangular        | Square or rectangular hollow section: square tube, rectangular tube, solid square bar (also maps here), square profile, SHS, RHS, box section, hollow profile |
| L-bracket               | Angle / angle bracket / L-shaped bracket / angle iron                    |
| U-shaped                | U-channel / U-profile / U-shaped / channel section                       |
| Z-shaped                | Z-section / Z-profile / Z-shaped                                         |
| CAPOT                   | Box / enclosure / cover / hood / folded housing                          |
| I-Shaped              | I-beam / I-profile / joist      |
| T-Shaped              | T-beam / T-profile / T-bar      |
| unknown                 | Cannot determine shape from available information                         |

⚠️ DISAMBIGUATION — Sheet vs Perforated Sheet:
→ Perforated Sheet if AT LEAST ONE indicator is present:
   - Code: "R12 T16", "C20 U40", "LR5x20 Z9x24", "40% open area", "pitch Xmm" (complete pattern)
   - Keyword: "perforated", "perforated sheet", "sheet with a hole pattern"
→ Sheet if ONLY individual holes, WITHOUT a code or the "perforated" keyword:
   - "4 mounting holes Ø10 at the corners"  → [Shape type: Sheet]
   - "centre hole Ø12 + 4 holes Ø8"         → [Shape type: Sheet]
   - "tapped hole M12 at the centre"        → [Shape type: Sheet]
   - "square central cutout 40mm"           → [Shape type: Sheet]
   - "mounting plate"                       → [Shape type: Sheet]

→ Prepend **[Shape type: <canonical_value>]** as the VERY FIRST token of your output, on the same line as the description.
→ Use ONLY the exact canonical string from the table above (case-sensitive).
→ Example output line: "[Shape type: Tube-Rectangular] Tube 8x8, thickness [t], length 300"
→ If truly unsure: "[Shape type: unknown] <description>"

RETURN FORMAT - Description with dimensions and hole positions from product page (always in English):

For simple sheets:
- "sheet [length]x[width], thickness [t]"
- Example missing width: "sheet 100x[width], thickness 2"
- Example missing thickness: "sheet 100x200, thickness [t]"
- Example: "sheet 100x100, thickness 2"
- **Example with multiple lengths available**: "sheet [length]x200, thickness 2" (if page shows length: 1/2/3/4/5/6)
- **Example with holes**: "sheet 100x100, thickness 2, holes at corners 10mm from edges, 4 mounting holes 5mm diameter"
- **Example with positioned holes**: "sheet 200x300, thickness 2, hole at center, 2 holes at 50mm from left edge, 30mm from top"

For perforated sheets:
- **Format**: "Perforated sheet [CODE], [dimensions]"
- **CODE PATTERNS**:
  * **Round holes**: R<Diameter> (e.g., R12, R10, R5)
    - Example: R12 = round hole with 12mm diameter
  * **Square holes**: C<Side> (e.g., C20, C25, C10)
    - Example: C20 = square hole with 20mm side
  * **Oblong round-ended**: LR<Width>x<Length> (e.g., LR5x20, LR10x30)
    - Example: LR5x20 = oblong with 5mm width, 20mm length, rounded ends
  * **Oblong square-ended**: LC<Width>x<Length> (e.g., LC5x50, LC10x100)
    - Example: LC10x100 = oblong with 10mm width, 100mm length, square ends
  * **Pitch - Straight/Grid**: U<Pitch> (e.g., U40, U30, U27.72)
    - Example: U40 = straight grid pattern, 40mm center-to-center spacing
  * **Pitch - Staggered 60°**: T<Pitch> (e.g., T16, T13, T8)
    - Example: T16 = staggered 60° pattern, 16mm pitch
  * **Pitch - Generic Staggered**: Z<PitchY>x<PitchX> (e.g., Z9x24, Z12x20)
    - Example: Z9x24 = staggered pattern, 9mm Y-pitch (between rows), 24mm X-pitch (same row)

- **COMPLETE EXAMPLES WITH ALL PATTERN COMBINATIONS**:
  * Round + Straight: "Perforated sheet R12 U27.72, 1000x500x2"
  * Round + Staggered 60°: "Perforated sheet R12 T16, 200x200x2"
  * Round + Generic Staggered: "Perforated sheet R10 T13, 500x500x2"
  * Square + Straight: "Perforated sheet C20 U40, 1200x600x2"
  * Square + Straight: "Perforated sheet C25 U30, 1000x1000x2"
  * Square + Straight: "Perforated sheet C10 U30, [length]x[width]x2"
  * Oblong Round + Generic Staggered: "Perforated sheet LR5x20 Z9x24, 500x500x2"
  * Oblong Round + Straight: "Perforated sheet LR10x30 U25, 1000x500x2"
  * Oblong Square + Straight (2D pitch): "Perforated sheet LC5x50 U25x60, 1000x500x2"
  * Oblong Square + Straight (2D pitch): "Perforated sheet LC10x100 U30x40, 1200x600x2"
  * Shape only (no pitch): "Perforated sheet R12, 200x200x2"
  * Shape only (no pitch): "Perforated sheet C20, 300x300x2"
  * Shape only (no pitch): "Perforated sheet LR5x20, 400x400x2"
  * Shape only (no pitch): "Perforated sheet LC10x100, 500x500x2"

- **EXAMPLES WITH MISSING VALUES**:
  * "Perforated sheet R12 T16, 200x[width]x2" (width not specified)
  * "Perforated sheet C25 U30, [length]x600x2" (length not specified)
  * "Perforated sheet LR5x20 Z9x24, [length]x[width]x2" (both dimensions missing)
  * "Perforated sheet LC10x100 U30x40, 1000x500x[thickness]" (thickness missing)
  * "Perforated sheet R10 T13, [length]x[width]x[thickness]" (all dimensions missing)

- **SPECIAL RULE FOR PERFORATED SHEETS WITH CODE SYMBOLS (T, U, Z)**:
  * If the product has a code with symbols like T, U, Z (e.g., R12 T16, C20 U40, LR5x20 Z9x24), the code already defines the hole pattern and position
  * In this case, DO NOT include hole position description - the code symbol (T, U, Z) already specifies the pattern
  * Examples with code symbols (NO hole position needed):
    - "Perforated sheet R12 T16, 200x200x2" (T16 defines pattern, no position description needed)
    - "Perforated sheet C20 U40, 1000x500x2" (U40 defines pattern, no position description needed)
    - "Perforated sheet LR5x20 Z9x24, 500x500x2" (Z9x24 defines pattern, no position description needed)
    - "Perforated sheet LC5x50 U25x60, 1000x500x2" (U25x60 defines pattern, no position description needed)
  * Examples WITHOUT code symbols (hole position should be included if available):
    - "Perforated sheet 200x200x2, round hole 12mm diameter, staggered pattern, hole-spacing 16"
    - "Perforated sheet 1200x600x2, square hole 20mm side, straight pattern, hole-spacing 40"
    - "Perforated sheet 500x500x2, oblong hole 5x20mm, staggered pattern, hole-spacing 9x24"

- **Example with multiple values**: "Perforated sheet [length]x200x2, round hole 12mm diameter" (if length options: 1/2/3/4/5/6)

HOLE POSITION DESCRIPTION FORMAT:
- If holes are positioned at specific locations, describe them clearly:
  * "holes at corners, [distance]mm from edges"
  * "hole at center"
  * "holes along edges, [distance]mm from corners"
  * "holes in grid pattern, [distance]mm from all edges"
  * "holes cover full surface, [margin]mm margin from edges"
  * "holes at [position]: [description] (e.g., 'holes at 4 corners, 10mm from each edge')"
  * "mounting holes at [locations]: [description] (e.g., 'mounting holes at 4 corners, 15mm from edges, 6mm diameter')"
  * "holes positioned [relative_position]: [description] (e.g., 'holes positioned symmetrically, 50mm from center')"
- If hole position is not specified, omit the position description
- Use [position_description] placeholder if position is mentioned but exact values are not provided

For tubes (circular) — HOLLOW (tube creux / tube rond):
- "Tube [diameter], thickness [t], length [l]"
- Example: "Tube 55mm diameter, thickness 2, length 750"
- Example missing diameter: "Tube [diameter], thickness 2, length 750"
- Example missing length: "Tube 55mm diameter, thickness 2, length [l]"
- **Example with multiple lengths**: "Tube 55mm diameter, thickness 2, length [l]" (if length options: 1/2/3/4/5/6)
- **Example with holes**: "Tube 55mm diameter, thickness 2, length 750, holes at both ends, 4 mounting holes 6mm diameter at 20mm from ends"
- **Example with positioned holes**: "Tube 55mm diameter, thickness 2, length 750, drainage holes at bottom, 3 holes spaced 200mm apart"

For solid round bars (Tube-Circular — SOLID: solid round bar / round rod):
⚠️ Solid bars have NO wall thickness — do NOT invent a thickness value.
- "Solid round bar [diameter]mm, length [l]"
- Example: "Solid round bar 4mm, length 1000"
- Example missing length: "Solid round bar 12mm, length [length]"
- Example multiple lengths: "Solid round bar 4mm, length [length]" (if site shows multiple standard lengths)

For tubes (rectangular):
- "Tube [width]x[height], thickness [t], length [l]"
- Example: "Tube 10x15, thickness 2, length 100"
- Example missing height: "Tube 10x[height], thickness 2, length 100"
- Example missing thickness: "Tube 10x15, thickness [t], length 100"
- **Example with holes**: "Tube 10x15, thickness 2, length 100, mounting holes at corners, 5mm diameter, 10mm from edges"

For tubes (square):
- "Tube [side]x[side], thickness [t], length [l]"
- Example: "Tube 60x60, thickness 2, length 920"
- Example missing side: "Tube [side]x[side], thickness 2, length 920"
- Example missing thickness and length: "Tube 60x60, thickness [t], length [l]"
- **Example with holes**: "Tube 60x60, thickness 2, length 920, holes at 4 corners of each end, 8mm diameter, 15mm from edges"

For L-shaped / support brackets:
- "L-shaped bracket [base_length]x[vertical_length]x[thickness], width [width], bend radius [bend_radius], bend angle [bend_angle]"
- Example: "L-shaped bracket 100x200x2, width 50, bend radius 2, bend angle 90"
- **Example with holes**: "L-shaped bracket 100x200x2, width 50, bend radius 2, bend angle 90, mounting holes at ends, 2 holes per leg, 6mm diameter, 20mm from edges"
- **Example with positioned holes**: "L-shaped bracket 100x200x2, width 50, bend radius 2, bend angle 90, holes at [position_description]"

For U-shaped/chutes de U:
- "U-shaped/chutes de U [base_length]x[vertical_length]x[thickness], width [width], bend radius [bend_radius], bend angle [bend_angle]"
- Example: "U-shaped/chutes de U 100x200x2, width 50, bend radius 2, bend angle 90"
- **Example with holes**: "U-shaped/chutes de U 100x200x2, width 50, bend radius 2, bend angle 90, drainage holes at bottom, 4 holes 8mm diameter, 25mm spacing"
- **Example with positioned holes**: "U-shaped/chutes de U 100x200x2, width 50, bend radius 2, bend angle 90, mounting holes at flanges, 2 holes per side, 6mm diameter, 15mm from edges"

FOR OTHER SPECIAL SHAPES (T-shaped, U-shaped, Z-shaped, V-shaped, Angles, etc.):
- If the product is a special/unusual shape not covered above:
  * First try to identify the shape name (T-shaped, U-shaped, Z-shaped, V-shaped, etc.)
  * Extract available dimensions
  * Use format: "[Shape] [key_dimensions]"
  * **Include hole positions if available**: "[Shape] [key_dimensions], holes at [position_description]"
  * Examples:
    - "T-shaped 100x50, thickness 2, stem length 80"
    - "U-shaped 200x150, thickness 2"
    - "Z-shaped 100x75x50, thickness 1.5"
    - "V-shaped angle 120°, width 100, thickness 2"
    - "Angle iron 100x100, thickness 2, length 1000"
    - "T-shaped 100x50, thickness 2, stem length 80, mounting holes at base, 4 holes 6mm diameter, 20mm from edges"
    - "Z-shaped 100x75x50, thickness 1.5, holes at connection points, 2 holes per section, 5mm diameter"
    - "Angle iron 100x100, thickness 2, length 1000, holes along length, 6 holes spaced 150mm apart, 8mm diameter"
  * If exact shape is unclear but product is defined by angle/bend: "Bent profile [angle]°, dimensions [measurements], thickness [t]"
  * For any missing dimension use [placeholder] format
  * For missing hole position information, omit the position description


IMPORTANT RULES:
1. For perforated sheets, if product code exists (like R12 T16, C20 U40, LR5x20 Z9x24, LC5x50 U25x60), include it
2. Return ONLY the description, no extra text, links, or explanations
3. **When dimension options are presented (like "Available in: 1/2/3/4/5/6" or "Select from: 100/200/300"), use [placeholder] instead of any specific value**
4. **ALWAYS extract hole position/location information if available on the product page**:
   - Include distances from edges, corners, or center
   - Describe hole patterns and their distribution
   - Specify mounting hole locations if mentioned
   - Include hole spacing and arrangement details
   - If hole positions are mentioned but exact values are unclear, use [position_description] placeholder
   - If no hole position information is provided, omit the position description entirely
5. **SPECIAL RULE FOR PERFORATED SHEETS**: If the product code contains symbols like T, U, or Z (e.g., R12 T16, C20 U40, LR5x20 Z9x24), DO NOT include hole position description because the code symbol already defines the hole pattern and position. Only include hole position for perforated sheets WITHOUT code symbols or when additional positioning information is explicitly provided beyond the standard code pattern.
6. **LANGUAGE ENFORCEMENT**: The final output description MUST be written in English, regardless of the web page language. Technical codes and numbers are always kept as-is.
7. **SHAPE TYPE PREFIX (MANDATORY)**: Every output line MUST start with `[Shape type: <canonical>]`. This prefix is a machine tag — do NOT translate it, do NOT omit it, do NOT place it anywhere other than the very beginning of the output. The canonical value must match exactly one entry from the SHAPE TYPE CLASSIFICATION table above.

# ═══════════════════════════════════════════════════════════════════════════
# TARGET URL — MUST STAY LAST. Everything above is identical on every call and
# is served from the provider's prompt cache; moving the URL above this marker
# truncates the cacheable prefix there and bills the rest in full every time.
# ═══════════════════════════════════════════════════════════════════════════

TARGET URL: {url}
"""

            logger.debug(f"[WEB_EXTRACT] Extracting content from URL: {url}")

            # Use async client - non-blocking
            response = await self.client.responses.create(
                model="gpt-5.4-2026-03-05",
                tools=[{"type": "web_search_preview"}],
                input=prompt
            )

            # Track cost if cost_tracker is available
            if self.cost_tracker:
                try:
                    logger.debug(f"[WEB_EXTRACT] Checking response for usage data: hasattr={hasattr(response, 'usage')}")
                    if hasattr(response, 'usage'):
                        logger.debug(f"[WEB_EXTRACT] Response usage: {response.usage}")
                    usage = response.usage if hasattr(response, 'usage') else None
                    if usage:
                        # ResponseUsage uses input_tokens/output_tokens instead of prompt_tokens/completion_tokens
                        input_tokens = getattr(usage, 'input_tokens', getattr(usage, 'prompt_tokens', 0))
                        output_tokens = getattr(usage, 'output_tokens', getattr(usage, 'completion_tokens', 0))
                        
                        if input_tokens > 0 or output_tokens > 0:
                            self.cost_tracker.add_direct_api_cost(
                                chain_name="web_search",
                                prompt_tokens=input_tokens,
                                completion_tokens=output_tokens,
                                model_name="gpt-5.4-2026-03-05"
                            )
                            logger.info(f"[WEB_EXTRACT] ✅ Tracked cost: {input_tokens} input + {output_tokens} output tokens")
                        else:
                            logger.warning(f"[WEB_EXTRACT] Usage object has no token counts")
                    else:
                        logger.warning(f"[WEB_EXTRACT] Response does not have usage attribute - cannot track cost")
                except Exception as e:
                    logger.warning(f"[WEB_EXTRACT] Failed to track cost: {e}")
                    import traceback
                    logger.debug(f"[WEB_EXTRACT] Traceback: {traceback.format_exc()}")
            else:
                logger.debug(f"[WEB_EXTRACT] No cost_tracker available for this request")

            extracted_content = response.output_text

            # Post-process: remove non-geometric info (material, norms, weight, surface...)
            extracted_content = self._filter_geometric_only(extracted_content)

            # Check if the response is an error message instead of actual content
            if self._is_error_response(extracted_content):
                logger.warning(f"[WEB_EXTRACT] Response appears to be an error message, extracting info from URL instead")
                # Extract product info from URL as fallback
                url_based_description = self._extract_product_info_from_url(url)
                logger.info(f"[WEB_EXTRACT] Using URL-based description: {url_based_description}")
                
                return {
                    "success": False,
                    "error": "Could not extract content from page (blocked or inaccessible)",
                    "content": url_based_description,
                    "url": url,
                    "used_url_fallback": True
                }

            # Validate and process the extracted content for multiple values
            validation_result = self._validate_multiple_values(extracted_content)

            if validation_result["should_use_placeholder"]:
                logger.info(f"[WEB_EXTRACT] Multiple dimension values detected. Conflicts: {validation_result['dimension_conflicts']}")
                logger.info(f"[WEB_EXTRACT] Content will use placeholders for conflicting dimensions")

            # Log extracted content in a single readable line
            logger.info(f"[WEB_EXTRACT] ✅ OpenAI result ({len(extracted_content)} chars): '{extracted_content}'")

            return {
                "success": True,
                "content": extracted_content,
                "url": url,
                "has_multiple_values": validation_result["has_multiple_values"],
                "conflicts": validation_result["dimension_conflicts"]
            }

        except Exception as e:
            logger.warning(f"[WEB_EXTRACT] Error extracting content from {url}: {e}. Extracting info from URL as fallback.")
            # Extract product info from URL as fallback
            url_based_description = self._extract_product_info_from_url(url)
            logger.info(f"[WEB_EXTRACT] Using URL-based description: {url_based_description}")
            return {
                "success": False,
                "error": f"Error accessing link: {str(e)}",
                "content": url_based_description,  # Return URL-based description when extraction fails
                "url": url,
                "used_url_fallback": True
            }
    
    async def process_text_with_urls(self, text: str) -> Tuple[bool, str, Dict]:
        """
        Process text that may contain URLs, extract web content if found (ASYNC).

        Args:
            text (str): Input text to process

        Returns:
            Tuple[bool, str, Dict]:
                - bool: True if URLs were found and processed
                - str: Natural language description with specifications
                - Dict: Metadata about the web search process
        """
        urls = self.detect_urls(text)

        if not urls:
            return False, text, {"urls_found": 0}

        # Remove URLs from original text to get base request
        base_request = text
        for url in urls:
            base_request = base_request.replace(url, '').strip()

        logger.debug(f"[WEB_PROCESS] Found {len(urls)} URL(s) to process")

        # Extract natural language descriptions from URLs
        web_contents = []
        successful_extractions = 0
        natural_descriptions = []
        multiple_value_warnings = []

        for idx, url in enumerate(urls, 1):
            logger.debug(f"[WEB_PROCESS] Processing URL {idx}/{len(urls)}: {url}")
            # Extract web content - returns natural language description
            result = await self.extract_web_content(url)

            if result.get("success") and result.get("content"):
                web_content = result["content"].strip()
                web_contents.append({
                    "url": url,
                    "content": web_content
                })

                # Collect natural language descriptions
                natural_descriptions.append(web_content)

                # Check for multiple values warning
                if result.get("has_multiple_values"):
                    conflicts_str = ", ".join([f"{k}: {v}" for k, v in result.get("conflicts", {}).items()])
                    warning_msg = f"URL {idx}: Multiple dimension values detected ({conflicts_str}) - using placeholders"
                    multiple_value_warnings.append(warning_msg)
                    logger.warning(f"[WEB_PROCESS] {warning_msg}")

                successful_extractions += 1
                logger.debug(f"[WEB_PROCESS] ✅ Successfully extracted content from URL {idx}/{len(urls)}")
            else:
                # When extraction fails, use URL as fallback content
                error_msg = result.get("error", "Unknown error")
                fallback_content = result.get("content", url)  # Use URL if content is empty
                
                web_contents.append({
                    "url": url,
                    "content": fallback_content,
                    "extraction_failed": True
                })
                
                # Add URL to descriptions so it can still be analyzed
                natural_descriptions.append(fallback_content)
                logger.warning(f"[WEB_PROCESS] ⚠️ Failed to extract content from URL {idx}/{len(urls)}: {error_msg}. Using URL as fallback: {fallback_content}")

        # Create final natural language text
        if natural_descriptions:
            # Combine base request with natural language descriptions
            if base_request:
                # If there's a base request like "Create", combine it with the description
                final_text = f"{base_request} {', '.join(natural_descriptions)}"
            else:
                # If only URL was provided, use the description directly
                final_text = ', '.join(natural_descriptions)

            # Log final combined text sent to AI
            logger.info(f"[WEB_PROCESS] ✅ Final text ({successful_extractions}/{len(urls)} URL(s)): '{final_text}'")
        else:
            # No successful extractions, return original text
            final_text = text
            logger.info(f"[WEB_PROCESS] ⚠️ No content extracted ({successful_extractions}/{len(urls)} URLs). Using original text.")

        metadata = {
            "urls_found": len(urls),
            "successful_extractions": successful_extractions,
            "urls_processed": urls,
            "web_contents": web_contents,
            "has_extracted_content": successful_extractions > 0,
            "multiple_value_warnings": multiple_value_warnings,
            "has_multiple_values": len(multiple_value_warnings) > 0
        }

        return True, final_text, metadata

    def _smart_fallback_analysis(self, text: str, urls: List[str]) -> bool:
        """
        Smart fallback analysis when AI is not available.
        Uses pattern matching to determine web search intent.

        Args:
            text (str): Text to analyze
            urls (List[str]): URLs found in text

        Returns:
            bool: True if likely needs web search
        """
        if not urls:
            return False

        text_lower = text.lower()

        # Strong positive indicators (definitely need web search)
        strong_positive = [
            "access link", "visit", "get information from", "extract from",
            "based on information from", "according to specs from", "from page",
            "view information", "get info from", "based on",
            "specifications from", "specs from"
        ]

        # Negative indicators (likely don't need web search)
        negative_indicators = [
            "my website", "website of", "company website",
            "like page", "like the site", "our site", "my company", "homepage"
        ]

        # Task-oriented indicators (suggest web search for building/creating)
        task_indicators = [
            "create model", "build", "design", "make", "manufacture",
            "3d", "dimension", "specification", "generate", "fabricate"
        ]

        # Check for strong positive indicators
        positive_score = sum(1 for indicator in strong_positive if indicator in text_lower)

        # Check for negative indicators
        negative_score = sum(1 for indicator in negative_indicators if indicator in text_lower)

        # Check for task indicators
        task_score = sum(1 for indicator in task_indicators if indicator in text_lower)

        # Decision logic
        if positive_score > 0:
            return True  # Explicit web search request

        if negative_score > 0:
            return False  # URL mentioned for other purposes

        if task_score > 0 and len(urls) == 1:
            # Single URL + task = likely want info from that URL
            return True

        # If multiple URLs with no clear intent, be conservative
        if len(urls) > 2:
            return False

        # Default for single/few URLs with task context
        result = task_score > 0

        logger.info(f"Smart fallback decision: positive={positive_score}, negative={negative_score}, task={task_score} -> {result}")
        return result
    
    async def is_web_search_request(self, text: str) -> bool:
        """
        Use AI to intelligently analyze if the text indicates a web search request (ASYNC).

        Args:
            text (str): Text to analyze

        Returns:
            bool: True if AI determines web search is needed
        """
        # First check if there are any URLs at all
        urls = self.detect_urls(text)
        if not urls:
            return False

        # If no AsyncOpenAI client, fallback to basic detection
        if not self.client:
            logger.warning("AsyncOpenAI client not available, using fallback URL detection")
            return bool(urls)

        try:
            # Use AI to analyze intent
            analysis_prompt = f"""
You are analyzing a request for a CAD/3D modeling system. Decide whether to access URLs to retrieve product specifications and technical information.

Text: "{text}"

URLs found: {urls}

CONTEXT: This is a CAD generation system that creates 3D models for metal fabrication including:
- Plates (flat sheets, perforated sheets)
- Covers/Hoods (bent sheet metal)
- Boxes/Cabinets (folded enclosures)
- Tubes (circular, square, rectangular)
- Brackets/Supports (L-shaped, angled)
- Cladding/Panels (decorative panels)

Consider these factors:
1. Does the URL contain product specifications, dimensions, or technical data for metal parts?
2. Are there manufacturing-related keywords (create, build, make, design, model, generate, fabricate)?
3. Does the URL appear to be a product page, catalog, or specification sheet for metal components?
4. Would accessing the URL provide technical details (dimensions, material, hole patterns, bends, etc.) needed for CAD generation?

IMPORTANT: If the text contains manufacturing keywords (create, build, make, design, model, generate) combined with product URLs,
the answer should typically be "YES" to retrieve specifications.

Special cases to consider "YES":
- Product catalog URLs with manufacturing requests
- Specification sheets or technical documentation for metal parts
- URLs containing product codes, part numbers, or technical terms
- E-commerce or manufacturer websites with product details
- URLs mentioning dimensions, materials, or fabrication details

Answer only "YES" if the URL should be accessed, "NO" if not needed.
No further explanation, just answer YES or NO.
"""

            # Use async client - non-blocking
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                max_tokens=10,
                temperature=0
            )

            decision = response.choices[0].message.content.strip().upper()
            should_search = decision.startswith("YES")

            logger.info(f"AI decision for web search: {decision} -> {should_search}")
            return should_search

        except Exception as e:
            logger.warning(f"Error in AI web search analysis, using smart fallback: {e}")
            # Smart fallback: analyze text patterns for web search intent
            return self._smart_fallback_analysis(text, urls)
