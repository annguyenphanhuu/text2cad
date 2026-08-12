import logging
import json
import re
from typing import List, Dict, Any, Optional
from langchain_core.documents import Document
from langchain_community.callbacks import get_openai_callback

from src.utils.cost_tracker import resolve_model_name

logger = logging.getLogger(__name__)


def _repair_json(text: str) -> str:
    """
    Attempt to fix common JSON formatting issues produced by LLMs:
    - Literal '...' placeholders left inside arrays/objects
    - Missing commas between adjacent objects/arrays
    - Trailing commas before closing brackets
    """
    # Remove literal ellipsis lines that LLMs sometimes echo from the prompt examples
    text = re.sub(r',?\s*\.\.\.\s*', '', text)

    # Insert missing comma between adjacent } { or ] [ (e.g. {...}{...} → {...},{...})
    text = re.sub(r'}\s*\n(\s*)\{', r'},\n\1{', text)
    text = re.sub(r']\s*\n(\s*)\[', r'],\n\1[', text)

    # Remove trailing commas before closing bracket/brace (makes json.loads fail)
    text = re.sub(r',\s*([\]}])', r'\1', text)

    return text


def _rule_id(doc: Document) -> str:
    """Return the normalized manufacturing rule id from document metadata."""
    return str(doc.metadata.get("rule_id", "")).strip()


def _ensure_rule_dependencies(reranked_docs: List[Document], documents: List[Document]) -> List[Document]:
    """Add table/reference rules required by selected rules, when available."""
    selected_ids = {_rule_id(doc) for doc in reranked_docs}
    needs_b03 = any(
        rule_id.startswith("B_05") or rule_id in {"B_04a", "B_04b", "B_06"}
        for rule_id in selected_ids
    )

    if not needs_b03 or "B_03" in selected_ids:
        return reranked_docs

    for doc in documents:
        if _rule_id(doc) == "B_03":
            doc.metadata.setdefault("llm_relevance_score", 1.0)
            doc.metadata["llm_reason"] = "Dependency table required by selected B_04/B_05/B_06 rule"
            reranked_docs.append(doc)
            logger.info("[RERANKER] Added dependency rule B_03 for selected bending distance rule")
            break
    else:
        logger.warning("[RERANKER] B_03 dependency required but not present in candidate documents")

    return reranked_docs


async def llm_rerank_documents(
    query: str,
    documents: List[Document],
    llm,
    top_k: int = 15,
    doc_type: str = "rules",
    cost_tracker = None,
    session_id: str = "unknown"
) -> List[Document]:
    """
    Rerank documents using GPT-4.1-nano LLM with cost tracking
    
    Args:
        query: User query
        documents: List of retrieved documents from FAISS
        llm: GPT-4.1-nano LLM instance
        top_k: Number of documents to return (for examples/info only; rules are LLM-decided)
        doc_type: "rules", "examples", or "info"
        cost_tracker: Optional CostTracker instance for tracking costs
        session_id: Session ID for cost attribution
    
    Returns:
        Reranked documents (top_k most relevant for examples/info, all relevant for rules)
    """
    if not documents:
        logger.warning(f"[RERANKER] No documents to rerank for {doc_type}")
        return []
    
    # For rules: always rerank regardless of count — order matters for validation stability
    # For examples/info: skip rerank only if already within top_k (order is less critical)
    if doc_type != "rules" and len(documents) <= top_k:
        logger.info(f"[RERANKER] {len(documents)} docs <= {top_k}, skipping rerank")
        return documents

    model_name = resolve_model_name(llm)
    logger.info(f"[RERANKER] Reranking {len(documents)} {doc_type} with {model_name}...")
    
    # Prepare documents for LLM (optimized preview based on doc type)
    docs_text = []
    
    for i, doc in enumerate(documents):
        metadata = doc.metadata
        
        # Extract content based on doc type
        if doc_type == "examples":
            # For examples: Extract description up to first import statement
            # This captures the full design intent and requirements in comments
            content = doc.page_content
            import_pos = content.find('import ')
            if import_pos != -1:
                content_preview = content[:import_pos].strip()
            else:
                # Fallback if no import found (shouldn't happen for examples)
                content_preview = content[:2000]
        elif doc_type == "rules":
            # Rules: Short preview (rule + error_message usually < 750 chars)
            content_preview = doc.page_content[:750]
        else:  # info
            # Info: Medium preview (standards + specs)
            content_preview = doc.page_content[:500]
        
        doc_info = f"""Doc {i+1}:
Source: {metadata.get('source', 'Unknown')}
Shape Type: {metadata.get('shape_type', 'Unknown')}
Content: {content_preview}...
"""
        docs_text.append(doc_info)
    
    # Create LLM prompt based on document type
    if doc_type == "rules":
        # For rules, let LLM decide how many to return (no hard limit)
        # Use soft cap of 25 to avoid overwhelming downstream
        max_rules = min(len(documents), 25)
        
        prompt = f"""You are a manufacturing expert. Select ALL rules that are relevant to the user's query.

User Query: "{query}"

Rules ({len(documents)} total):
{''.join(docs_text)}

SELECTION CRITERIA (in order of importance):
1. **Direct Match**: Does the rule directly address the user's question or requirements?
2. **Constraint Validation**: Does it provide validation logic or constraints for the design?
3. **Error Guidance**: Does the error_message help answer the user's information need?
4. **Completeness**: Does it provide complete information (thicknesses, distances, clearances, etc.)?

MANDATORY INCLUSION RULES (always add these when applicable):
- If query mentions holes/cutouts: ALWAYS include LC_02 (hole-to-edge distance rule)
- If any B_05_L / B_05_U / B_05_Z / B_05_TUBE / B_05_CAPOT is selected: ALWAYS include B_05_COMMON (shared bend zone reference)
- If any B_04a / B_04b / B_05_* / B_06 is selected: ALWAYS include B_03 (bending distance reference table)
- If any bend/bracket shape is detected: include B_05_COMMON and the matching B_05 variant
- Z-shape / Z-bracket with holes: ALWAYS include B_05_Z (covers top flange 1-bend, web 2-bends, bot flange 1-bend)

INSTRUCTIONS:
- Select ALL rules that meet at least one of the criteria above
- For complex shapes (capot, U-shape, holes, etc.), include ALL related rules (bend, thickness, hole clearance, etc.)
- Do NOT limit yourself to a fixed number - select as many or as few as truly relevant
- Maximum {max_rules} rules to keep response manageable
- Only exclude rules that are clearly unrelated to the query

Return as JSON (no other text):
{{
    "rankings": [
        {{"doc_id": 1, "score": 0.95, "reason": "Direct match for thickness constraint"}},
        {{"doc_id": 3, "score": 0.88, "reason": "Bend radius validation"}},
        ...
    ]
}}

CRITICAL: Better to include more relevant rules than risk missing important constraints."""
    
    elif doc_type == "examples":
        # Use exactly top_k examples as requested by caller
        min_examples = top_k
        
        prompt = f"""Rank code examples STRICTLY by shape type match to the user query.

User Query: "{query}"

Examples ({len(documents)} total):
{''.join(docs_text)}

MANDATORY SHAPE TYPE FILTERING - FOLLOW THIS PROCESS:

Step 1 - Identify query shape type from query text:
   Look for these EXACT keywords in the query (case-insensitive):
   - **Z-shaped**: "Z-shaped", "Z-shape", "Z shape", "Z profil", "forme de Z", "en forme de Z", "en Z", "Z-bend", "Z-Bracket"
   - **U-shaped**: "U-shaped", "U shape", "U profil", "channel", "2 plis", "2 bends", "patte de fixation", "forme de U"
   - **L-bracket**: "L-bracket", "L shape", "cornière", "1 pli", "1 bend", "L profil", "angle bracket"
   - **I-Shaped**: "I-Shaped", "I shape", "I profil", "forme de I", "poutre en I"
   - **T-Shaped**: "T-Shaped", "T shape", "T profil", "forme de T", "fer en T"
   - **capot**: "capot", "cover", "box", "4 plis", "4 bends", "tub", "bac", "quatre plis"
   - **Triangle**: "Triangle", "triangular", "triangular plate", "triangular sheet", "equilateral triangle", "isosceles triangle",
     "plaque triangulaire", "gousset triangulaire", "renfort triangulaire", "équerre triangulaire", "platine triangulaire"
   - **plate**: "plate", "plaque", "flat", "without bends", "no bends", "plan", "2D"
   - **tube**: "tube", "circular tube", "cylindrical", "round tube"

Step 2 - Identify shape type for EACH example (check in order):
   1. Check `# Shape type: <type>` comment (HIGHEST PRIORITY - this appears at the start before imports)
   2. Check description/comments for shape keywords (same keywords as Step 1)

Step 3 - FILTER OUT MISMATCHES:
   ⚠️ CRITICAL: IMMEDIATELY DISCARD any example where shape type does NOT match query shape type
   - If query is Z-shaped → ONLY keep examples with makeZShape or "Z-shaped" shape type
   - If query is U-shaped → ONLY keep examples with makeUShape or "U-shaped" shape type
   - If query is I-Shaped → ONLY keep examples with makeIShape or "I-Shaped" shape type
   - If query is T-Shaped → ONLY keep examples with makeTShape or "T-Shaped" shape type
   - If query is capot → ONLY keep examples with makeTub or "capot" shape type
   - If query is Triangle -> ONLY keep examples with "Triangle" shape type
   - etc.

Step 4 - RANK ONLY MATCHING EXAMPLES:
   After filtering, reason silently about the manufacturing intent before assigning scores:
   1. Identify the query's primary operation family (examples: plain plate, holes/cuts, slots/pockets, notches, edge return/hem/crushed fold, 90° profile bend, mixed bends).
   2. Identify each example's primary operation family from its description and comments.
   3. Prefer examples whose operation family matches the query operation family, even if another example has similar dimensions or generic plate wording.
   4. For Triangle examples, prefer exact subtype/operation match in this order: right/right-isosceles triangle with hypotenuse flange, flat equilateral plate, equilateral triangle with three edge flanges, isosceles triangle with one base flange.
   5. Then rank remaining ties by dimension similarity (flange heights, thicknesses, etc.), hole pattern similarity, and material similarity.
   Do not expose chain-of-thought; put only a short conclusion in each JSON reason.
   
RANKING STRATEGY (2-tier priority system):
🥇 **TIER 1 (HIGHEST PRIORITY)**: Examples with EXACT shape type match
   - If query is "Z-shaped" → prioritize Z-shaped examples
   - If query is "U-shaped" → prioritize U-shaped examples
   - If query is "L-bracket" → prioritize L-bracket examples

   - If query is "Triangle" -> prioritize Triangle examples, then rank by right/hypotenuse-flange vs flat vs all-edge-flanges vs base-flange similarity

🥈 **TIER 2 (FILL REMAINING SLOTS)**: Best similar examples if Tier 1 < {top_k}
   - Use examples with similar bending/geometry characteristics
   - Sort by relevance score (keywords, dimensions, hole patterns)

OUTPUT REQUIREMENTS:
⚠️ MANDATORY: Return EXACTLY {top_k} examples in the JSON "rankings" array
- If {top_k}+ Tier 1 (exact matches) exist → return top {top_k} from Tier 1
- If fewer than {top_k} Tier 1 exist → return ALL Tier 1 + fill remaining with best Tier 2 to reach {top_k} total
- ALWAYS return exactly {top_k} examples (prioritize shape match, then fill with similar)

Return JSON (no other text):
{{
    "query_shape": "<detected_shape_type>",
    "rankings": [
        {{"doc_id": 1, "score": 0.95, "shape_type": "Z-shaped", "reason": "Exact shape match + similar dimensions"}},
        {{"doc_id": 4, "score": 0.88, "shape_type": "Z-shaped", "reason": "Exact shape match + hole pattern similar"}},
        ...
    ]
}}

FINAL VALIDATION (check before returning):
✓ All examples in rankings have "shape_type" matching "query_shape"
✓ No shape mismatches included
✓ At least {top_k} examples (if available in filtered set)"""
    
    else:  # info
        prompt = f"""You are a technical standards expert. Rank these info files by relevance to the user's query.

User Query: "{query}"

Info Files ({len(documents)} total):
{''.join(docs_text)}

RANKING CRITERIA (in order of importance):
1. **Standard Applicability**: Does it provide relevant ISO/ANSI/DIN standards?
2. **Dimensional Data**: Does it contain specific measurements/tolerances needed?
3. **Material Specs**: Does it address material-specific constraints?
4. **Completeness**: Does it provide complete specification tables?

Return ONLY top {top_k} info files as JSON (no other text):
{{
    "rankings": [
        {{"doc_id": 1, "score": 0.90}},
        {{"doc_id": 4, "score": 0.82}},
        ...
    ]
}}

IMPORTANT: Focus on info files that provide TECHNICAL DATA the user needs."""
    
    MAX_RETRIES = 2
    last_exception = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"[RERANKER] LLM call attempt {attempt}/{MAX_RETRIES}")

            # Wrap LLM call with cost tracking
            cost_info = None
            with get_openai_callback() as cb:
                response = await llm.ainvoke(prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)

                # Track cost if tracker provided — model_name derived from the
                # actual llm instance, not hardcoded.
                if cost_tracker:
                    chain_name = f"rag_rerank_{doc_type}"
                    cost_info = cost_tracker.add_chain_cost(chain_name, cb, model_name)

            # Log LLM response for debugging
            logger.debug(f"[RERANKER] LLM Response (first 500 chars): {response_text[:500]}")

            # ── Parse JSON ──────────────────────────────────────────────────
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                logger.warning(f"[RERANKER] Attempt {attempt}: no JSON block found in response, retrying...")
                continue  # retry LLM call

            raw_json = json_match.group(0)

            # Try direct parse first
            try:
                result = json.loads(raw_json)
            except json.JSONDecodeError as json_err:
                logger.warning(f"[RERANKER] Attempt {attempt}: JSON parse failed ({json_err}), attempting repair...")
                raw_json = _repair_json(raw_json)
                try:
                    result = json.loads(raw_json)
                    logger.info(f"[RERANKER] Attempt {attempt}: JSON repaired successfully")
                except json.JSONDecodeError as json_err2:
                    last_exception = json_err2
                    logger.error(f"[RERANKER] Attempt {attempt}: JSON repair also failed: {json_err2}")
                    logger.debug(f"[RERANKER] Broken JSON: {raw_json[:500]}")
                    if attempt < MAX_RETRIES:
                        logger.info(f"[RERANKER] Retrying LLM call (attempt {attempt + 1}/{MAX_RETRIES})...")
                    continue  # retry LLM call

            # ── Process valid result ────────────────────────────────────────
            rankings = result.get('rankings', [])

            # Log rankings for debugging
            if doc_type == "examples":
                logger.info(f"[RERANKER] 📊 LLM Rankings: {json.dumps(rankings, indent=2)}")

            if not rankings:
                logger.warning(f"[RERANKER] ⚠️ No rankings in response, using original order")
                if doc_type == "examples":
                    return documents[:min(len(documents), top_k)]
                return documents[:top_k]

            # For rules, trust LLM's judgment on how many to return (no top_k limit)
            # For examples, ensure minimum 5 examples; for info, use top_k
            if doc_type == "rules":
                limit = len(rankings)
            elif doc_type == "examples":
                limit = min(top_k, len(rankings))
            else:  # info
                limit = min(top_k, len(rankings))

            # Reorder documents based on LLM rankings
            reranked_docs = []
            processed_doc_ids = set()

            for rank in rankings[:limit]:
                doc_id = rank.get('doc_id', 0) - 1  # Convert to 0-indexed
                if 0 <= doc_id < len(documents) and doc_id not in processed_doc_ids:
                    doc = documents[doc_id]

                    # Add LLM metadata
                    doc.metadata['llm_relevance_score'] = rank.get('score', rank.get('relevance_score', 0.0))

                    # Add reasoning if provided (useful for debugging)
                    if 'reason' in rank:
                        doc.metadata['llm_reason'] = rank['reason']

                    reranked_docs.append(doc)
                    processed_doc_ids.add(doc_id)
                else:
                    if doc_id >= len(documents):
                        logger.warning(f"[RERANKER] ⚠️ Invalid doc_id {doc_id+1} (max: {len(documents)})")
                    # Skip duplicates silently

            # Log if LLM didn't return enough examples
            if doc_type == "examples":
                if len(reranked_docs) < top_k:
                    logger.warning(
                        f"[RERANKER] ⚠️ LLM returned only {len(reranked_docs)} examples "
                        f"(requested: {top_k}). Prompt should ensure enough examples are returned."
                    )

            if doc_type == "rules":
                reranked_docs = _ensure_rule_dependencies(reranked_docs, documents)

            # Compact summary with cost.
            # Prefer the tracker's computed cost: cb.total_cost is 0 whenever
            # LangChain doesn't recognise the model, which is the common case here.
            retry_tag = f" (retry {attempt})" if attempt > 1 else ""
            reported_cost = cost_info.total_cost if cost_info else cb.total_cost
            selected_note = " (LLM-selected)" if doc_type == "rules" else ""
            logger.info(
                f"🔄 [RERANK-{doc_type.upper()}]{retry_tag} "
                f"{len(documents)} → {len(reranked_docs)} docs{selected_note} | "
                f"model={model_name} | Cost: ${reported_cost:.4f} ({cb.total_tokens}t) | ✅ Success"
            )

            # Log top 3 for debugging
            if reranked_docs:
                logger.debug(f"[RERANKER] Top 3 {doc_type}:")
                for i, doc in enumerate(reranked_docs[:3]):
                    score = doc.metadata.get('llm_relevance_score', 0)
                    source = doc.metadata.get('source', 'Unknown')[:40]
                    logger.debug(f"  {i+1}. Score: {score:.2f} - {source}")

            return reranked_docs

        except Exception as e:
            last_exception = e
            logger.error(f"[RERANKER] Attempt {attempt}: LLM reranking failed: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"[RERANKER] Retrying after unexpected error...")
            else:
                logger.exception(e)

    # All attempts exhausted — fallback to original document order
    logger.error(
        f"[RERANKER] ❌ All {MAX_RETRIES} attempts failed. "
        f"Last error: {last_exception}. Falling back to original order."
    )
    return documents[:min(len(documents), top_k)]

