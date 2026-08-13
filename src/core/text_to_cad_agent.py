import os
import sys
import json
import re
import subprocess
import time
import traceback
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging
# Import from local modules
from .models import (
    ShapeRequirement, ExtractedShapeInfo, Operation,
    DesignRequirements, AnalysisAndParameterCheckOutput
)
from .agent_utils import detect_detailed_explanation_request
from .agent_chains import (
    create_greeting_classification_chain,
    create_unified_processing_chain,
    create_dfm_validation_chain,
    create_code_generation_chain,
    create_code_editing_chain,
    create_description_confirm_chain,
    create_step_planner_chain,
    create_shape_change_detector_chain,
    create_edit_summary_chain,
    create_perforated_param_chain,
)

from src.utils import path_manager
from src.utils.material_mapper import map_material_to_geometry_analyzer
from src.rag.retriever import set_classification_llm
from src.rag.query_expander import expand_query_with_llm, set_expansion_llm
from src.utils.file_manager import save_code_file, save_metadata_file
from src.utils.path_manager import get_output_path, get_unique_filepath
from src.utils.file_finder import find_step_file, find_obj_files
from src.utils.cost_tracker import CostTracker
from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure UTF-8 encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# Fixed replies for PROCESS_QUESTION classifications (see greeting_classification_template).
# These are business/ordering-process questions the chatbot cannot act on itself
# (pricing, file/PDF export) — deliberately NOT LLM-generated so the wording never
# drifts from what was agreed with the customer.
PROCESS_QUESTION_RESPONSES = {
    "pricing": (
        "Pour chiffrer votre pièce, veuillez télécharger le fichier et créer un "
        "nouveau devis."
    ),
    "file_export": (
        "Pour avoir le fichier et/ou le PDF, vous devez télécharger votre fichier."
    ),
}
PROCESS_QUESTION_DEFAULT_RESPONSE = (
    "Cette demande ne concerne pas la modélisation CAO. Veuillez télécharger "
    "votre fichier pour la suite (devis, PDF, etc.)."
)


def get_process_question_response(sub_type: Optional[str]) -> str:
    return PROCESS_QUESTION_RESPONSES.get(sub_type, PROCESS_QUESTION_DEFAULT_RESPONSE)


# Session-based log deduplication tracker
class SessionLogTracker:
    """Track logged messages per session to prevent duplicates in multi-turn conversations."""
    def __init__(self):
        self._session_logs = {}  # {session_id: {log_key: count}}
    
    def should_log(self, session_id: str, log_key: str, max_count: int = 1) -> bool:
        """Check if a log message should be printed for this session."""
        if session_id not in self._session_logs:
            self._session_logs[session_id] = {}
        
        current_count = self._session_logs[session_id].get(log_key, 0)
        if current_count < max_count:
            self._session_logs[session_id][log_key] = current_count + 1
            return True
        return False
    
    def reset_session(self, session_id: str):
        """Reset log tracking for a session."""
        if session_id in self._session_logs:
            del self._session_logs[session_id]
    
    def cleanup_old_sessions(self, keep_recent: int = 100):
        """Keep only the most recent N sessions to prevent memory bloat."""
        if len(self._session_logs) > keep_recent:
            # Keep only the last 'keep_recent' sessions
            session_ids = list(self._session_logs.keys())
            for old_session in session_ids[:-keep_recent]:
                del self._session_logs[old_session]

# Global session log tracker
_session_log_tracker = SessionLogTracker()

# Define model choices (kept for reference, actual models passed in)
MODELS = {
    "default": "o4-mini-2025-04-16",
    "advanced": "o4-mini-2025-04-16",
    "expert": "o4-mini-2025-04-16",
}

# ═══════════════════════════════════════════════════════════════════════════
# ERROR CODE SYSTEM (sub-numbered for easy debugging)
# ═══════════════════════════════════════════════════════════════════════════
ERROR_CODES = {
    # Erreur 101.x — Délai d'attente dépassé
    "101.1": "101.1",
    # Erreur 102.x — Communication avec le serveur FreeCAD
    "102.1": "102.1",
    "102.2": "102.2",
    "102.3": "102.3",
    "102.4": "102.4",
    "102.5": "102.5",
    "102.6": "102.6",
    "102.7": "102.7",
    "102.8": "102.8",
    # Erreur 104.x — Échec d'exécution FreeCAD
    "104.1": "104.1",
    "104.2": "104.2",
    "104.3": "104.3",
    "104.4": "104.4",
    "104.5": "104.5",
    "104.6": "104.6",
    # Erreur 105.x — Échec de génération du code
    "105.1": "105.1",
    "105.2": "105.2",
    "105.3": "105.3",
    "105.4": "105.4",
}

class EnhancedFaceProcessor:
    """Enhanced face identification and processing system for AI-driven face editing."""

    def __init__(self):
        self.face_pattern = re.compile(
            r'Face Selection: ID\[(.*?)\] Type\[(.*?)\] Position\[(.*?)\] '
            r'BBox\[Min\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*Max\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*Size\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\] '
            r'Geometry\[(.*?)\] Context\[(.*?)\]'
        )
        logger.info("Enhanced Face Processor initialized")

    def parse_face_selection(self, text):
        """Parse enhanced face selection format from user input."""
        match = self.face_pattern.search(text)
        if not match:
            return None

        groups = match.groups()
        return {
            'face_id': groups[0],
            'shape_type': groups[1],
            'spatial_context': groups[2],
            'bbox': {
                'min': {'x': float(groups[3]), 'y': float(groups[4]), 'z': float(groups[5])},
                'max': {'x': float(groups[6]), 'y': float(groups[7]), 'z': float(groups[8])},
                'size': {'x': float(groups[9]), 'y': float(groups[10]), 'z': float(groups[11])}
            },
            'geometry': groups[12],
            'context': groups[13],
            'original_text': match.group(0)
        }

    def enhance_user_request(self, user_text):
        """Enhance user request with parsed face information for better AI understanding."""
        face_data = self.parse_face_selection(user_text)
        if not face_data:
            return user_text

        # Clean spatial context to only include the center portion
        spatial_context = face_data['spatial_context']
        if "center" in spatial_context:
            spatial_context = spatial_context[spatial_context.find("center"):]

        # Create enhanced context for AI
        enhanced_context = f"""
FACE SELECTION CONTEXT:
- Face ID: {face_data['face_id']}
- Shape Type: {face_data['shape_type']}
- Spatial Position: {spatial_context}
- Bounding Box: Min({face_data['bbox']['min']['x']}, {face_data['bbox']['min']['y']}, {face_data['bbox']['min']['z']}) Max({face_data['bbox']['max']['x']}, {face_data['bbox']['max']['y']}, {face_data['bbox']['max']['z']})
- Size: {face_data['bbox']['size']['x']} x {face_data['bbox']['size']['y']} x {face_data['bbox']['size']['z']}
- Geometric Properties: {face_data['geometry']}
- Relationship Context: {face_data['context']}

FACE-TO-CODE MAPPING INSTRUCTIONS:
1. Use the bounding box center ({(face_data['bbox']['min']['x'] + face_data['bbox']['max']['x'])/2:.1f}, {(face_data['bbox']['min']['y'] + face_data['bbox']['max']['y'])/2:.1f}, {(face_data['bbox']['min']['z'] + face_data['bbox']['max']['z'])/2:.1f}) for precise face targeting
2. Consider spatial context "{face_data['spatial_context']}" for operation appropriateness
3. Validate operation against face geometry: {face_data['geometry']}
4. Use relationship context "{face_data['context']}" for manufacturing constraints

USER REQUEST: {user_text.replace(face_data['original_text'], '').strip()}
"""

        logger.info(f"Enhanced user request with face context: {face_data['face_id']}")
        return enhanced_context

    def generate_face_selection_code(self, face_data):
        """Generate FreeCAD code for precise face selection based on enhanced identification."""
        if not face_data:
            return ""

        bbox = face_data['bbox']
        center_x = (bbox['min']['x'] + bbox['max']['x']) / 2
        center_y = (bbox['min']['y'] + bbox['max']['y']) / 2
        center_z = (bbox['min']['z'] + bbox['max']['z']) / 2

        return f"""
# ⚠️ MAPPING INSTRUCTION: Use this target_center to deduce the face AT GENERATION TIME.
# Do NOT wrap your tool generation in a mutually exclusive `if/elif target_face == ...` block.
# ALL tool arrays for ALL faces must be processed every time.
# This selection snippet is ONLY for finding the face_index for the final JSON export.

def get_face_index(obj, target_center):
    tolerance = 1.0  # Tolerance for face center matching
    for i, face in enumerate(obj.Shape.Faces):
        if face.CenterOfMass.distanceToPoint(target_center) < tolerance:
            return i+1, face
    return None, None

# Example usage for JSON export (DO NOT use this to skip cuts on other faces!):
# current_target_center = FreeCAD.Vector({center_x:.3f}, {center_y:.3f}, {center_z:.3f})
# face_index, target_face = get_face_index(main_object, current_target_center)
"""

# --- Local RAG Setup Paths (Adjusted) ---
LOCAL_GUIDE_PATH = "../../data/guide_en.txt"
FAISS_INDEX_PATH = "../../faiss_guide_index"

class TextToCADAgent:
    def __init__(self, default_llm, advanced_llm, expert_llm, confirm_llm=None):
        self.default_llm = default_llm
        self.advanced_llm = advanced_llm
        self.expert_llm = expert_llm
        # confirm_llm: dedicated LLM for description_confirm (e.g. gpt-5.4 for higher accuracy)
        # Falls back to default_llm if not provided (backward-compatible)
        self.confirm_llm = confirm_llm if confirm_llm is not None else default_llm
        # self.local_retriever is no longer needed as we use the pool everywhere

        set_classification_llm(self.default_llm)

        self.MAX_QUESTION_ATTEMPTS = 10


        self._session_states = {}
        
        # Cost tracking per session
        self._session_cost_trackers = {}
        
        # Extract and store model names for cost tracking
        self.model_names = {
            'default': getattr(default_llm, 'model_name', 'unknown'),
            'advanced': getattr(advanced_llm, 'model_name', 'unknown'),
            'expert': getattr(expert_llm, 'model_name', 'unknown'),
            'confirm': getattr(self.confirm_llm, 'model_name', 'unknown'),
        }
        logger.info(f"[INIT] Model names: default={self.model_names['default']}, "
                   f"advanced={self.model_names['advanced']}, expert={self.model_names['expert']}")
        
        # Initialize GPT-4.1-nano for RAG reranking
        from langchain_openai import ChatOpenAI
        try:
            self.reranking_llm = ChatOpenAI(
                model="gpt-4.1-nano-2025-04-14",
                temperature=0
            )
            self.model_names['reranking'] = 'gpt-4.1-nano-2025-04-14'
            logger.info(f"[INIT] Reranking LLM: {self.model_names['reranking']}")
        except Exception as e:
            logger.error(f"[INIT] Failed to initialize reranking LLM: {e}")
            self.reranking_llm = None

        # Query expansion normalises capot face synonyms and detects shape_type.
        # STAYS ON THE DEFAULT TIER. It is the largest remaining cost in the RAG
        # path (~4.3k input tokens per retrieval, already 95% cached), so the nano
        # tier was measured against it over all 169 fixture prompts —
        # tests/check_expansion.py, which is kept for re-testing this:
        #
        #   same detected_shape_type   133/169 (78%)
        #   bracket/CAPOT -> "Sheet"   16 cases
        #   no shape returned at all    6 cases
        #
        # detected_shape_type is load-bearing: the retriever merges it with its
        # regex pass and filters which code examples reach the generator, so those
        # 16 cases would feed flat-plate examples to bent parts. Unlike the
        # greeting classifier (180/180 on nano), this task is a 15-way
        # classification over FR/EN/VI synonyms and nano is not good enough.
        self.expansion_llm = self.default_llm
        self.model_names['expansion'] = self.model_names['default']
        set_expansion_llm(self.expansion_llm)
        logger.info(f"[INIT] Query expander LLM: {self.model_names['expansion']}")

        # Greeting classification is a 4-way label + confidence, ~40 output tokens.
        # It ran on the default tier at ~2.7k input tokens on every non-edit turn,
        # which made it one of the larger per-turn line items for the cheapest
        # decision in the pipeline. The nano tier used for reranking is the right
        # size for it; fall back to default_llm if that model failed to init.
        self.greeting_llm = self.reranking_llm or self.default_llm
        self.model_names['greeting'] = getattr(
            self.greeting_llm, 'model_name', None) or self.model_names['default']
        logger.info(f"[INIT] Greeting classification LLM: {self.model_names['greeting']}")
        self.greeting_classification_chain = create_greeting_classification_chain(self.greeting_llm)
        self.unified_processing_chain = create_unified_processing_chain(self.expert_llm)
        self.dfm_validation_chain = create_dfm_validation_chain(self.expert_llm)
        self.rag_code_generation_chain = create_code_generation_chain(self.advanced_llm)
        self.code_editing_chain = create_code_editing_chain(self.expert_llm)
        self.description_confirm_chain = create_description_confirm_chain(self.confirm_llm)
        self.step_planner_chain = create_step_planner_chain(self.default_llm)
        self.shape_change_detector_chain = create_shape_change_detector_chain(self.default_llm)
        self.edit_summary_chain = create_edit_summary_chain(self.default_llm)
        self.perforated_param_chain = create_perforated_param_chain(self.default_llm)

        # Enhanced face identification processor
        self.face_processor = EnhancedFaceProcessor()

    async def _invoke_unified_with_rag(self, user_text, session_id, **kwargs):
        """
        Centralized wrapper to invoke unified chain with automatic RAG retrieval.
        
        This ensures ALL unified chain calls get proper RAG contexts with reranking.
        
        Args:
            user_text: User query text (current question only)
            session_id: Session ID for cost tracking
            **kwargs: Additional parameters for unified chain (previous_responses_formatted, etc.)
                k_rules (int): how many rules to retrieve. Information requests ask
                    for more than a CAD turn does — they are answered *from* the
                    rules rather than merely validated against them.
                inline_rules_context (bool): also feed the retrieved rules to the
                    unified chain by appending them to its user_text. The unified
                    template has no rules placeholder (only DFM consumes
                    rules_context), so this is the only way to ground an
                    information-request answer in the knowledge base.

        Returns:
            Unified chain output with parsed analysis
        """
        import logging
        logger = logging.getLogger(__name__)

        from src.core.rag_singleton import get_rag_split_context
        from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

        # Popped up-front so they never leak into the chain inputs built below.
        k_rules = kwargs.pop('k_rules', 10)
        inline_rules_context = kwargs.pop('inline_rules_context', False)

        # RAG QUERY: Extract only [USER] blocks from conversation history.
        # We deliberately exclude [CHATBOT] responses to avoid duplicate/noisy retrieval —
        # chatbot responses can confuse semantic search and reduce retrieval accuracy.
        # The full user_text (with [USER]/[CHATBOT] turns) is still passed to the unified chain.
        #
        # ⚠️  Use regex (DOTALL) — NOT a line-by-line filter — so that multi-line [USER]
        #     messages (e.g. long descriptions with paragraph breaks) are captured in full.
        #     A line-based filter would only capture the first line of each [USER] block.
        import re as _re
        user_sections = _re.findall(
            r'\[USER\]:(.*?)(?=\[(?:USER|CHATBOT)\]:|$)',
            user_text,
            _re.DOTALL
        )
        user_only_parts = [s.strip() for s in user_sections if s.strip()]
        rag_query = "\n\n".join(user_only_parts) if user_only_parts else user_text
        logger.info(
            f"[RAG] Using user-only query ({len(rag_query)} chars, "
            f"{len(user_only_parts)} user turn(s)) — chatbot responses excluded from RAG input"
        )
        
        # ── [TIMING] Step 1: Query Expansion ─────────────────────────────────
        # 🆕 EXPAND QUERY with manufacturing terminology
        _t_expand = time.time()
        logger.info(f"[TIMING] expand_query START | session={session_id}")
        # Initialised up-front so the value survives every failure path below and
        # can be forwarded to the retriever (which otherwise re-expands and pays
        # for a second LLM call).
        detected_shape_type = None
        try:
            # Expand query if LLM available (intelligent synonym addition + shape type detection)
            cost_tracker = self._get_cost_tracker(session_id) if session_id else None
            # Use default_llm for expansion (same as set_expansion_llm in __init__)
            try:
                expansion_result = await expand_query_with_llm(
                    rag_query,  # Use rag_query not user_text
                    llm=self.expansion_llm,  # nano tier — see __init__
                    cost_tracker=cost_tracker
                )

                # Handle both old (string) and new (dict) return formats
                if isinstance(expansion_result, dict):
                    expanded_rag_query = expansion_result.get("expanded_query", rag_query)
                    detected_shape_type = expansion_result.get("detected_shape_type")
                    if detected_shape_type:
                        logger.info(f"[QUERY_EXPAND] Shape type detected: {detected_shape_type}")
                    else:
                        logger.info(f"[QUERY_EXPAND] No specific shape type detected.")
                else:
                    # Fallback for old string format
                    expanded_rag_query = expansion_result

                if expanded_rag_query != rag_query:
                    logger.info("[QUERY_EXPAND] Query expanded for better semantic matching")
                else:
                    logger.info("[QUERY_EXPAND] No expansion needed (no manufacturing terms detected)")
            except Exception as e:
                logger.warning(f"[QUERY_EXPAND] Expansion failed: {e}, using original query")
                expanded_rag_query = rag_query
        except Exception as e:
            logger.warning(f"[QUERY_EXPAND] Expansion handling failed: {e}, using original query")
            expanded_rag_query = rag_query
        logger.info(f"[TIMING] expand_query DONE | elapsed={time.time()-_t_expand:.2f}s | session={session_id}")

        # ── [TIMING] Step 2: RAG Retrieval ───────────────────────────────────
        # 🎯 SINGLE RAG RETRIEVAL with reranking (using EXPANDED query)
        _t_rag = time.time()
        logger.info(f"[TIMING] rag_split_context START | session={session_id}")
        rag_result = await get_rag_split_context(
            query=expanded_rag_query,  # ✅ Use expanded query for better semantic matching
            k_rules=k_rules,
            k_examples=4,
            reranking_llm=self.reranking_llm,
            cost_tracker=self._get_cost_tracker(session_id),
            session_id=session_id,
            # Hand our expansion result down so the examples retriever doesn't
            # run a second expansion on top of the already-expanded text.
            # detected_shape_type must travel with it — it is the LLM half of
            # the shape detection the retriever merges with its regex pass.
            pre_expanded_query=expanded_rag_query,
            pre_detected_shape_type=detected_shape_type,
        )
        logger.info(f"[TIMING] rag_split_context DONE | elapsed={time.time()-_t_rag:.2f}s | session={session_id}")
        
        if rag_result['success']:
            rules_context = rag_result['rules_context']
            examples_context = rag_result['examples_context']
        else:
            error_msg = rag_result.get('error', 'Unknown error')
            logger.error(f"❌ [RAG] Retrieval failed: {error_msg}")
            rules_context = ""
            examples_context = ""
        
        # Build chain input with RAG contexts
        # - rules_context: For DFM VALIDATION AGENT ONLY (rule checking)
        # - examples_context: For CODE GENERATION ONLY (examples + info files)
        # NOTE: user_text (full [USER]/[CHATBOT] history) is passed to the unified chain
        #       so the LLM retains full conversation context.
        #       expanded_rag_query (user-only) was used ONLY for RAG retrieval above.
        # ── [TIMING] Step 3: Language Detection ──────────────────────────────
        # Detect user language using gpt-4.1-nano (reranking_llm).
        # Strategy: extract the FIRST substantive [USER] block (>= 10 chars) so we detect
        # from the original CAD request, not from a short confirmation word ("ok", "oui").
        # Result is cached in _SESSION_LANG_CACHE[session_id] → all subsequent turns are FREE.
        from src.utils.language_utils import detect_language_llm, set_session_language, lang_code_to_name
        import re as _re_lang
        _user_blocks = _re_lang.findall(
            r'\[USER\]:\s*(.*?)(?=\s*\[(?:USER|CHATBOT)\]:|$)',
            user_text, _re_lang.DOTALL
        )
        _user_blocks = [b.strip() for b in _user_blocks if b.strip()]
        substantive_msgs = [m for m in _user_blocks if len(m) >= 10]
        detect_target = substantive_msgs[0] if substantive_msgs else (_user_blocks[-1] if _user_blocks else "")
        # detect_language_llm: session cache hit → zero LLM cost on turn 2+
        lang_code = await detect_language_llm(detect_target, self.reranking_llm, session_id) if detect_target else "fr"
        user_language = lang_code_to_name(lang_code)
        # Cache user_language in session state so _run_perforated_param_chain() can access it
        self._update_session_state(session_id, user_language=user_language)

        # Get material from kwargs or session state
        material = kwargs.pop('material', '')

        # Information requests are answered FROM the rules, so they get them
        # appended to the prompt text. The RAG query was already built above from
        # the raw user turns, so this never pollutes retrieval.
        unified_user_text = user_text
        if inline_rules_context and rules_context:
            unified_user_text = (
                f"{user_text}\n\n"
                f"[RETRIEVED MANUFACTURING RULES]\n{rules_context}"
            )
            logger.info(
                f"[UNIFIED] Inlined {len(rules_context)} chars of rules context into "
                f"unified prompt | session={session_id}"
            )

        unified_chain_input = {
            "user_text": unified_user_text,  # ✅ Full [USER]/[CHATBOT] history for unified analysis context
            "rules_context": rules_context,  # ✅ RULES → Unified Analysis ONLY
            "examples_context": examples_context,  # ✅ EXAMPLES + INFO → Code Generation ONLY
            "session_id": session_id,
            "material": material,  # Material choice for template
            "user_language": user_language,  # ✅ Provide user_language to template
            **kwargs
        }
        
        dfm_chain_input = {
            "user_text": user_text,  # ✅ Full [USER]/[CHATBOT] history so DFM agent can understand context (e.g. "use 3" means selecting thickness 3mm from a list)
            "retrieved_context": rules_context,  # ✅ RULES → DFM validation
            "material": material,
            "session_id": session_id,
            "user_language": user_language,  # ✅ Pre-detected language → enforces consistent reply language
        }
        
        # ── [TIMING] Step 4: Unified + DFM parallel gather ───────────────────
        # 🆕 Run BOTH agents in PARALLEL via asyncio.gather
        # asyncio.wait_for(timeout=50s): fail fast instead of waiting for OpenAI
        # auto-retry (60s+) which would silently block the SSE stream.
        cost_tracker = self._get_cost_tracker(session_id)
        logger.info(f"[TIMING] unified+dfm gather START | session={session_id}")
        _t_gather = time.time()
        logger.info(f"[🔀 MULTI-AGENT] Running unified analysis + DFM validation in parallel | session={session_id}")
        
        try:
            unified_result, dfm_result = await asyncio.wait_for(
                asyncio.gather(
                    ainvoke_with_cost_tracking(
                        "unified_processing",
                        self.unified_processing_chain.ainvoke,
                        unified_chain_input,
                        cost_tracker,
                        self.model_names['expert']
                    ),
                    ainvoke_with_cost_tracking(
                        "dfm_validation",
                        self.dfm_validation_chain.ainvoke,
                        dfm_chain_input,
                        cost_tracker,
                        self.model_names['expert']
                    ),
                ),
                timeout=50.0  # Fail fast — OpenAI auto-retry can block for 60s+
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[TIMING] unified+dfm gather TIMEOUT after 50s | "
                f"session={session_id} | "
                f"This is likely the root cause of SSE 'Erreur de connexion'"
            )
            raise RuntimeError(
                f"[UNIFIED_CHAIN] Timeout after 50s waiting for OpenAI response. "
                f"session={session_id}"
            )
        logger.info(f"[TIMING] unified+dfm gather DONE | elapsed={time.time()-_t_gather:.2f}s | session={session_id}")
        
        # 🆕 MERGE DFM results into unified output
        unified_output_obj = unified_result.get("unified_output_obj")
        if unified_output_obj and isinstance(dfm_result, dict) is False:
            # dfm_result is a DFMValidationOutput Pydantic model
            logger.info(
                f"[🔀 MULTI-AGENT] Merging DFM results | "
                f"violations={len(dfm_result.violations)} | "
                f"override={dfm_result.override_intent_detected} | "
                f"thickness_warning={'yes' if dfm_result.thickness_warning else 'no'}"
            )
            
            # If DFM agent detects override intent, propagate to unified output
            if dfm_result.override_intent_detected:
                unified_output_obj.override_intent_detected = True
                logger.info(f"[🔀 MULTI-AGENT] Override intent detected by DFM agent → propagated")
            
            # If DFM agent found violations AND user is NOT overriding
            if dfm_result.has_violations and not dfm_result.override_intent_detected:
                # Inject violations into questions
                for violation in dfm_result.violations:
                    if violation not in unified_output_obj.questions:
                        unified_output_obj.questions.append(violation)
                
                # Inject thickness warning
                if dfm_result.thickness_warning:
                    if dfm_result.thickness_warning not in unified_output_obj.questions:
                        unified_output_obj.questions.append(dfm_result.thickness_warning)
                
                # Set missing_info if there are new violations
                if dfm_result.violations or dfm_result.thickness_warning:
                    unified_output_obj.missing_info = True
                    logger.info(
                        f"[🔀 MULTI-AGENT] ⚠️ DFM violations injected → "
                        f"missing_info=True, {len(unified_output_obj.questions)} total questions"
                    )
        
        # ── PERFORATED SHEET ROUTING GATE ───────────────────────────────────────────────────
        # shape_type="Perforated Sheet" detected by unified chain.
        # Unified chain validates L/W/T only. Hole shape / pitch / % vide are handled
        # by the dedicated perforated_param_chain (LLM, focused prompt), called
        # from _unified_request_processor() or process_request_with_progress() after this returns.
        if unified_output_obj:
            _shape = getattr(unified_output_obj, 'shape_type', '') or ''
            if 'perforated' in _shape.lower() or _shape.lower() == 'perforated sheet':
                # Luôn chạy perf chain khi là Perforated Sheet
                # _run_perforated_param_chain sẽ tự quyết định extract-only hay extract+calc
                unified_result["_needs_perf_param_chain"] = True
                # Lưu flag để _run_perforated_param_chain biết có thể calc hay không
                unified_result["_perf_dims_complete"] = not unified_output_obj.missing_info
            else:
                # Clear stale result if shape changed away from Perforated Sheet
                self._update_session_state(session_id, perf_calc_result=None)
                unified_result["_needs_perf_param_chain"] = False

        # Add expanded_user_text to result for downstream use (code generation)
        unified_result["expanded_user_text"] = user_text
        return unified_result

    def _get_session_state(self, session_id):
        """Get the state for a specific session."""
        if session_id not in self._session_states:
            self._session_states[session_id] = {
                # Code / requirements (kept for edit flow)
                'latest_code': None,
                'latest_title': None,
                'latest_requirements': None,
                # Q&A tracking (questions currently pending user reply)
                'pending_questions': [],
                # Edit-specific
                'edit_request_history': [],
                'edit_context': {},
                # Description confirm flow (per-session, multi-user safe)
                'confirm_count': 0,
                'awaiting_confirm': False,
                'confirmed_description': '',
                'last_confirmed_description': '',
                'edit_history_since_confirm': [],
                # ── Running edit summary (net current-state description) ─────
                # Updated after every successful edit turn by edit_summary_chain.
                # Used as `current_description` context for shape_change/unified/DFM
                # checks instead of replaying the raw edit_history transcript.
                'edit_running_summary': '',
                # Waiting for user to acknowledge a DFM/unifier warning raised
                # during edit mode (shape_change=False branch).
                'awaiting_edit_ack': False,
                'pending_edit_text': '',
                # ── Confirm fast-path cache (isolated per session_id) ────────
                # Populated by _save_confirm_cache() right before confirm chain,
                # consumed and cleared by the fast-path gate on the next turn.
                'cached_raw_unified_json': '',
                'cached_retrieved_context': '',
                'cached_unified_obj_snapshot': None,  # unified_output_obj.dict()
                'cached_expanded_user_text': '',
                'cached_perf_calc_result': None,
                # ── Step Plan state ──────────────────────────────────────────
                'awaiting_step_plan_confirm': False,   # Waiting for user reply after step plan shown (YES/NO)
                # ── Language (cached from first unified call — free on subsequent turns) ──
                'user_language': 'French',
                # ── Perforated Sheet function-calling result ──────────────────
                # Populated by _run_perf_vide_function_call() right after unified chain.
                # Consumed by _run_description_confirm() (display) and code gen (params).
                'perf_calc_result': None,
                'perf_extracted_params': None,
            }
        return self._session_states[session_id]

    def _update_session_state(self, session_id, **updates):
        session_state = self._get_session_state(session_id)
        session_state.update(updates)
        return session_state

    def _get_perforated_export_eta(self, session_id: str) -> dict | None:
        """
        Build export ETA metadata for Perforated Sheet SSE events.

        The estimate is available after the perforated calculator has produced
        a finite hole_count. Reverse calculations attach forward geometry once
        the missing pitch/hole size has been resolved.
        """
        try:
            state = self._get_session_state(session_id)
            perf_calc = state.get('perf_calc_result') or {}

            open_area = perf_calc.get('open_area') or {}
            hole_count = open_area.get('hole_count')
            raw_notation = (
                perf_calc.get('resolved_notation')
                or perf_calc.get('input_notation')
                or ''
            )

            if hole_count is None and perf_calc.get('mode') == 'reverse' and raw_notation:
                sheet = perf_calc.get('sheet') or {}
                sheet_length = sheet.get('length_mm')
                sheet_width = sheet.get('width_mm')
                sheet_thickness = sheet.get('thickness_mm')
                if sheet_length and sheet_width:
                    from src.utils.perforated_sheet_calculator import compute_perforated_sheet

                    forward_calc = compute_perforated_sheet(
                        raw_notation,
                        sheet_length=sheet_length,
                        sheet_width=sheet_width,
                        sheet_thickness=sheet_thickness,
                    )
                    open_area = forward_calc.get('open_area') or {}
                    hole_count = open_area.get('hole_count')

            if hole_count is None:
                return None

            from src.utils.perf_time_estimator import (
                estimate_freecad_seconds,
                extract_shape_letter,
            )

            shape_letter = extract_shape_letter(raw_notation)
            estimated_seconds = estimate_freecad_seconds(int(hole_count), shape_letter)
            return {
                "initial_estimated_time_seconds": estimated_seconds,
                "hole_count": int(hole_count),
                "perforation_notation": raw_notation,
            }
        except Exception as e:
            logger.warning(f"[EXPORT_ETA] Could not build perforated ETA | session={session_id} | error={e}")
            return None

    def _build_export_eta_payload(self, session_id: str, export_start_time: float) -> dict:
        """Return countdown fields for the current export step, if ETA is available."""
        eta = self._get_perforated_export_eta(session_id)
        if not eta:
            return {}

        from src.utils.perf_time_estimator import format_duration

        initial_seconds = eta["initial_estimated_time_seconds"]
        elapsed_seconds = max(0, int(time.time() - export_start_time))
        remaining_seconds = max(initial_seconds - elapsed_seconds, 0)
        return {
            "estimated_time_seconds": remaining_seconds,
            "estimated_time_label": format_duration(remaining_seconds),
            "initial_estimated_time_seconds": initial_seconds,
            "initial_estimated_time_label": format_duration(initial_seconds),
            "estimated_elapsed_seconds": elapsed_seconds,
            "is_estimate_overrun": elapsed_seconds > initial_seconds,
            "hole_count": eta["hole_count"],
            "perforation_notation": eta["perforation_notation"],
        }
    
    def _get_cost_tracker(self, session_id):
        """Get or create cost tracker for a session."""
        if session_id not in self._session_cost_trackers:
            # Extract user_id from context for logging
            from src.utils.context_manager import get_user_id
            user_id = get_user_id() or "anonymous"
            self._session_cost_trackers[session_id] = CostTracker(session_id=session_id, user_id=user_id)
        return self._session_cost_trackers[session_id]

    def _log_request_cost(self, session_id, label: str = "REQUEST_COMPLETE") -> dict:
        """
        Log and return cost for the CURRENT request turn only.

        Unlike the old _log_session_cost, this:
        - Is NEVER suppressed (no _logged flag).
        - Always reports only the cost since the last start_request() call.
        - Returns the summary dict so callers can embed it in final_result.

        Call this in the `finally` block of process_request_with_progress().
        """
        if session_id in self._session_cost_trackers:
            tracker = self._session_cost_trackers[session_id]
            return tracker.log_request_cost(label=label)
        return {}

    # Keep legacy wrapper for backward-compat — delegates to new method
    def _log_session_cost(self, session_id, force=False):
        """
        Deprecated: use _log_request_cost instead.
        Legacy callers still get a summary dict; the _logged suppression
        is intentionally removed so every turn is logged correctly.
        """
        return self._log_request_cost(session_id)


    def reset_conversation(self, session_id: Optional[str] = None):
        if session_id:
            if session_id in self._session_states:
                print(f"[PROCESS] Resetting conversation state for session {session_id}")
                self._session_states[session_id] = {
                    'latest_code': None,
                    'latest_title': None,
                    'latest_requirements': None,
                    'pending_questions': [],
                    'edit_request_history': [],
                    'edit_context': {},
                    # Reset confirm state
                    'confirm_count': 0,
                    'awaiting_confirm': False,
                    'confirmed_description': '',
                    'last_confirmed_description': '',
                    'edit_history_since_confirm': [],
                    'edit_running_summary': '',
                    'awaiting_edit_ack': False,
                    'pending_edit_text': '',
                    # Reset confirm fast-path cache
                    'cached_raw_unified_json': '',
                    'cached_retrieved_context': '',
                    'cached_unified_obj_snapshot': None,
                    'cached_expanded_user_text': '',
                    'cached_perf_calc_result': None,
                    'perf_calc_result': None,
                    'perf_extracted_params': None,
                }
                print(f"[SUCCESS] Conversation reset complete for session {session_id}")
            else:
                print(f"[WARNING] Attempted to reset non-existent session: {session_id}")
        else:
            print("[PROCESS] Resetting all session states.")
            self._session_states.clear()
            print("[SUCCESS] All session states reset complete.")

    # ── Description Confirm ─────────────────────────────────────────────────
    # Max confirm rounds before auto-generating code
    MAX_CONFIRM_ATTEMPTS = 3

    def _save_confirm_cache(
        self,
        session_id: str,
        unified_output_obj,
        raw_unified_json: str,
        retrieved_context: str,
        expanded_user_text: str,
    ):
        """
        Persist unified chain results into the per-session state so that the
        NEXT turn (user typing "yes" / "oui" / etc.) can bypass the entire
        unified chain, RAG, DFM, and description-confirm chain entirely.

        Called right BEFORE _run_description_confirm() so the cache always
        contains the freshest data that was used to produce the confirm message.

        Multi-user safe: all writes are keyed by session_id.
        """
        state = self._get_session_state(session_id)
        self._update_session_state(
            session_id,
            cached_raw_unified_json=raw_unified_json,
            cached_retrieved_context=retrieved_context,
            cached_unified_obj_snapshot=unified_output_obj.dict(),
            cached_expanded_user_text=expanded_user_text,
            cached_perf_calc_result=state.get('perf_calc_result'),
        )
        logger.info(
            f"[CONFIRM_CACHE] ✅ Cached for fast-path | session={session_id} "
            f"| desc={len(unified_output_obj.description)} chars "
            f"| ctx={len(retrieved_context)} chars"
        )

    async def _run_step_planner(
        self,
        session_id: str,
        unified_output_obj,
        raw_unified_json: str,
        retrieved_context_for_code_gen: str,
        expanded_user_text: str,
        empty_steps_text: str,
    ):
        """
        Run the Step Planner chain and set up the turn that hands the plan back.

        Invokes the planner in the session's cached language, builds the user-facing
        message (formatting the steps locally when the LLM returns no user_message),
        primes the confirm cache so the next turn skips the unified chain, and flags
        the session as awaiting step-plan confirmation.

        Shared by _unified_request_processor() and process_request_with_progress();
        each caller wraps the result in its own response shape.

        Args:
            empty_steps_text: placeholder for a plan that came back with no steps.
                The two callers word this differently, so it stays a parameter.

        Returns:
            (total_steps, user_message)
        """
        # Use session-cached language (set on first turn by gpt-4.1-nano)
        from src.utils.language_utils import get_session_language, lang_code_to_name
        _user_language = lang_code_to_name(get_session_language(session_id))
        logger.info(f"[STEP_PLAN] Using cached language: {_user_language} | session={session_id}")

        # Run Step Planner — input: description derived from user request
        cost_tracker     = self._get_cost_tracker(session_id)
        step_plan_result = await ainvoke_with_cost_tracking(
            "step_planner",
            self.step_planner_chain.ainvoke,
            {
                "description":      unified_output_obj.description,
                "complexity_level": unified_output_obj.complexity_level,
                "user_language":    _user_language,
                "session_id":       session_id,
            },
            cost_tracker,
            self.model_names['default']
        )

        _total    = step_plan_result.get("total_steps", len(step_plan_result.get("steps", [])))
        _user_msg = step_plan_result.get("user_message", "")

        logger.info(f"[STEP_PLAN] Plan generated: {_total} steps | session={session_id}")
        print(f"[STEP_PLAN] Plan: {_total} steps → {[s.get('title','?') for s in step_plan_result.get('steps',[])]}")

        # Fallback message if LLM returned empty user_message
        if not _user_msg:
            _lang_steps = [
                f"  Étape {i+1} — {s.get('title', 'Step')}\n  {s.get('description', '')}"
                for i, s in enumerate(step_plan_result.get('steps', []))
            ]
            _steps_text = "\n\n".join(_lang_steps) if _lang_steps else empty_steps_text
            _user_msg = (
                f"🔧 **Plan de construction en {_total} étape(s) :**\n\n"
                f"{_steps_text}\n\n"
                f"---\n"
                f"💡 **Pour générer chaque étape :**\n"
                f"Ouvrez une **nouvelle conversation** et copiez-collez **une étape à la fois** dans le chat.\n"
                f"Chaque étape sera générée séparément pour plus de précision."
            )
            logger.warning(f"[STEP_PLAN] LLM returned empty user_message, using fallback | session={session_id}")

        # Prime confirm cache so next turn (user reply) skips re-running unified chain
        self._save_confirm_cache(
            session_id, unified_output_obj, raw_unified_json,
            retrieved_context_for_code_gen, expanded_user_text
        )
        # Set flag — next turn = GATE 1 (either YES or NO → fall through to Confirm)
        self._update_session_state(session_id, awaiting_step_plan_confirm=True)

        logger.info(f"[STEP_PLAN] ↩ Returning plan to user | session={session_id}")
        return _total, _user_msg

    async def _detect_confirm_intent(self, user_text: str, session_id: str) -> str:
        """
        Classify user's confirm reply as YES or CHANGE.

        Two-layer approach:
          Layer 1 — Fuzzy pre-check (zero cost, no LLM):
            For short messages (≤ 20 chars), use difflib to fuzzy-match
            against a curated YES keyword list. Catches common typos like
            "yeas", "yse", "ouio", "okey", "yeah", etc.

          Layer 2 — Mini LLM (gpt-4.1-nano, ~$0.00005):
            For longer or ambiguous messages where pre-check is inconclusive.
            Handles: sentences, multilingual, emoji, mixed intent.

        Returns "YES" or "CHANGE" (never raises).
        Multi-user safe: only session_id for cost logging, no shared state.
        """
        import re as _re
        import json as _json
        import difflib
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking
        from src.core.templates import confirm_detector_template

        clean_text = user_text.strip()
        # Use last 300 chars when passing to LLM (avoids huge prompts)
        truncated_text = clean_text[-300:]

        logger.info(
            f"[CONFIRM_DETECT] 🔍 Classifying | session={session_id} "
            f"| input='{clean_text[:80]}'"
        )

        # ══════════════════════════════════════════════════════════════════
        # LAYER 1 — Fuzzy pre-check (zero LLM cost)
        # Only applies when the message is SHORT (≤ 20 chars).
        # A long message almost certainly contains extra instructions → CHANGE.
        # ══════════════════════════════════════════════════════════════════
        YES_KEYWORDS = {
            # English
            "yes", "yep", "yeah", "yea", "sure", "ok", "okay", "y",
            "go", "proceed", "generate", "correct", "right",
            "perfect", "fine", "good", "great", "alright", "agreed",
            # French
            "oui", "ouais", "parfait", "bien", "accord", "aller",
            # Vietnamese
            "có", "đúng", "được", "tiếp", "oke",
            # Symbols / emoji
            "✓", "✔", "👍", "🆗",
        }

        if len(clean_text) <= 20:
            word = clean_text.lower()

            # 1a. Exact match in YES keyword list
            if word in YES_KEYWORDS:
                logger.info(
                    f"[CONFIRM_DETECT] ✅ Pre-check EXACT match YES "
                    f"| word='{word}' | session={session_id}"
                )
                return "YES"

            # 1b. Fuzzy similarity against each YES keyword
            #     Threshold 0.75 — catches "yeas"→"yes" (0.86),
            #     "okey"→"okay" (0.75), "yse"→"yes" (0.67→try vs more kws),
            #     "ouio"→"oui" (0.86), "yess"→"yes" (0.86),
            #     but NOT "no" or "cancel" (all < 0.50 vs yes keywords).
            best_ratio = 0.0
            best_kw    = ""
            for kw in YES_KEYWORDS:
                ratio = difflib.SequenceMatcher(None, word, kw).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_kw    = kw

            if best_ratio >= 0.75:
                logger.info(
                    f"[CONFIRM_DETECT] ✅ Pre-check FUZZY match YES "
                    f"| word='{word}' → '{best_kw}' (ratio={best_ratio:.2f}) "
                    f"| session={session_id}"
                )
                return "YES"

            logger.info(
                f"[CONFIRM_DETECT] Pre-check inconclusive "
                f"(best_ratio={best_ratio:.2f} < 0.75) → calling mini LLM | session={session_id}"
            )

        # ══════════════════════════════════════════════════════════════════
        # LAYER 2 — Mini LLM (gpt-4.1-nano)
        # Handles: long text, sentences, languages without good keyword lists.
        # ══════════════════════════════════════════════════════════════════
        try:
            if self.reranking_llm is None:
                raise RuntimeError("reranking_llm not available")

            detect_prompt = ChatPromptTemplate.from_template(confirm_detector_template)
            detect_chain  = detect_prompt | self.reranking_llm | StrOutputParser()

            cost_tracker = self._get_cost_tracker(session_id)
            raw_str: str = await ainvoke_with_cost_tracking(
                "confirm_intent_detection",
                detect_chain.ainvoke,
                {"user_text": truncated_text},
                cost_tracker,
                self.model_names.get("reranking", "gpt-4.1-nano-2025-04-14"),
            )

            # Parse {"intent": "YES"} | {"intent": "CHANGE"}
            match = _re.search(r'\{[^}]+\}', raw_str)
            if match:
                data   = _json.loads(match.group())
                intent = str(data.get("intent", "CHANGE")).upper().strip()
            else:
                intent = "CHANGE"   # safe fallback if JSON is malformed

        except Exception as exc:
            logger.warning(
                f"[CONFIRM_DETECT] Mini LLM failed ({exc}). "
                f"Defaulting to CHANGE (safe fallback) | session={session_id}"
            )
            intent = "CHANGE"

        # Validate — only YES or CHANGE allowed
        if intent not in ("YES", "CHANGE"):
            logger.warning(
                f"[CONFIRM_DETECT] Unknown intent '{intent}', defaulting to CHANGE"
            )
            intent = "CHANGE"

        logger.info(f"[CONFIRM_DETECT] ✅ Detected intent={intent} | session={session_id}")
        return intent


    # ─────────────────────────────────────────────────────────────────────────
    # PERFORATED SHEET — FUNCTION CALLING
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_perf_sheet_dims_mm(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Extract (sheet_length_mm, sheet_width_mm, sheet_thickness_mm) from free text.

        Handles two situations that the old single 3-number regex
        (r'NxNxN') silently dropped to None on:
          1. L/W and thickness given in DIFFERENT turns, e.g.
             turn1="épaisseur 3mm" then turn2="145cm x 125cm"
             → old regex never sees 3 numbers chained by x/× → None,None,None
          2. Per-number units (cm/m), e.g. "145cm x 125cm" → must scale to mm,
             not read as raw 145/125 mm.

        Strategy: scan the WHOLE text (conversation history + current turn,
        chronological), take the LAST occurrence of each pattern (most recent
        turn wins), then combine:
          - If a 3-number "LxWxT" chain exists → use it (L, W, T all from
            the same chain), unless a separate explicit "épaisseur/thickness"
            mention appears later in the text (that one is more explicit/recent).
          - Else fall back to a 2-number "LxW" chain (T = None unless an
            explicit thickness mention exists elsewhere in the text).
        """
        import re as _re
        num = r'(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?'

        def _to_mm(raw: str, unit: Optional[str]) -> float:
            value = float(raw.replace(',', '.'))
            unit = (unit or 'mm').lower()
            if unit == 'cm':
                return value * 10.0
            if unit == 'm':
                return value * 1000.0
            return value

        chain3 = list(_re.finditer(
            rf'{num}\s*[xX×]\s*{num}\s*[xX×]\s*{num}', text
        ))
        chain2 = list(_re.finditer(
            rf'{num}\s*[xX×]\s*{num}(?!\s*[xX×]\s*\d)', text
        ))
        thick_mentions = list(_re.finditer(
            r'(?:épaisseur|epaisseur|thickness)\s*(?:de|:|=)?\s*'
            rf'{num}',
            text, _re.IGNORECASE,
        ))

        explicit_thickness = None
        if thick_mentions:
            m = thick_mentions[-1]
            explicit_thickness = _to_mm(m.group(1), m.group(2))

        if chain3:
            m = chain3[-1]
            L = _to_mm(m.group(1), m.group(2))
            W = _to_mm(m.group(3), m.group(4))
            T = _to_mm(m.group(5), m.group(6))
            if explicit_thickness is not None:
                T = explicit_thickness
            return L, W, T

        if chain2:
            m = chain2[-1]
            L = _to_mm(m.group(1), m.group(2))
            W = _to_mm(m.group(3), m.group(4))
            return L, W, explicit_thickness

        return None, None, None

    def _run_perf_vide_function_call(
        self, unified_output_obj, session_id: str, user_text: str = ''
    ) -> dict | None:
        """
        Function Calling Gate for Perforated Sheet % vide calculation.

        Responsibilities:
          • Parse hole/pitch/% params from unified description (Python regex, no LLM).
          • Determine which param is missing → choose calculation mode:
              - forward     : D + C given          → compute % vide
              - reverse_D   : % + C given, D miss  → compute D (hole diameter)
              - reverse_C   : % + D given, C miss  → compute pitch
          • Call compute_perforated_sheet() — pure Python math, always accurate.
          • Log clearly with [PERF_VIDE] prefix.
          • Return result dict or None on failure.

        Flow position:
          unified_processing_chain (LLM parses text)
              ↓
          _run_perf_vide_function_call()   ← HERE (Python math)
              ↓
          session_state['perf_calc_result'] stored
              ↓
          _run_description_confirm()  reads & displays (no recompute)
        """
        import re as _re
        try:
            from src.utils.perforated_sheet_calculator import (
                compute_perforated_sheet,
                parse_open_area_pct_from_text,
            )
        except ImportError as e:
            logger.warning(f"[PERF_VIDE] ❌ Cannot import calculator: {e}")
            return None

        # Parse from user_text (raw input — always has R12, T16, 200x200x2).
        # unified_output_obj.description is empty at this stage (populated by confirm downstream).
        search_text = user_text + ' ' + (getattr(unified_output_obj, 'title', '') or '')
        logger.info(
            f"[PERF_VIDE] 🔍 Parsing params from user_text ({len(user_text)} chars) | session={session_id}"
        )

        # ── 1. Parse params from user_text (regex — no LLM) ─────────────────────
        def _fmt(v: float) -> str:
            """Format float: drop '.0' suffix for whole numbers (e.g. 5.0 → '5')."""
            return str(int(v)) if v == int(v) else str(v)

        # Round hole: R12 → diameter=12
        m_r = _re.search(r'\bR(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        # Square hole: C12 → side=12
        m_c = _re.search(r'\bC(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        # Oblong hole: LR5x20 (rounded/stadium) or LC5x20 (rectangular slot)
        m_lr = _re.search(r'\b(L[CR])(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        # Bare shape-letter detection (no number) — used for reverse_D mode when user specifies
        # shape family but not size (e.g. "C U40 25%" or "LR Z9x24 30%").
        # Must be checked AFTER full-notation matches so "LR5x20" is not misread as bare "LR".
        m_lr_bare = _re.search(r'\b(L[CR])\b(?!\d)', search_text, _re.IGNORECASE)
        m_r_bare  = _re.search(r'\bR\b(?!\d)', search_text, _re.IGNORECASE)
        m_c_bare  = _re.search(r'\bC\b(?!\d)', search_text, _re.IGNORECASE)

        if m_r:
            hole_d = float(m_r.group(1))
            hole_shape_type = 'R'
            hole_notation = f"R{_fmt(hole_d)}"
        elif m_c:
            hole_d = float(m_c.group(1))
            hole_shape_type = 'C'
            hole_notation = f"C{_fmt(hole_d)}"
        elif m_lr:
            # Preserve exact prefix (LR vs LC) and BOTH dimensions
            _prefix = m_lr.group(1).upper()   # 'LR' or 'LC'
            _w = float(m_lr.group(2))
            _l = float(m_lr.group(3))
            hole_d = _w                        # width = key dimension
            hole_shape_type = _prefix
            hole_notation = f"{_prefix}{_fmt(_w)}x{_fmt(_l)}"
        elif m_lr_bare:
            # Bare oblong letter (no dimensions) — shape type known but size unknown
            hole_d = None
            hole_shape_type = m_lr_bare.group(1).upper()   # 'LR' or 'LC'
            hole_notation = None
        elif m_r_bare:
            hole_d = None
            hole_shape_type = 'R'
            hole_notation = None
        elif m_c_bare:
            hole_d = None
            hole_shape_type = 'C'
            hole_notation = None
        else:
            hole_d = None
            hole_shape_type = None
            hole_notation = None

        # Pitch: T16 (staggered 60°), U16 (square grid), U25x60 (rectangular grid), Z9x24 (staggered)
        # U<py>x<px> MUST be tried before U<P> so "U25x60" is not parsed as U25.
        # Also detect bare T/U without number → reverse_C mode (infer pitch value)
        m_t = _re.search(r'\bT(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        m_u_rect = _re.search(
            r'\bU(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE
        )
        m_u = _re.search(r'\bU(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        m_z = _re.search(r'\bZ(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', search_text, _re.IGNORECASE)
        m_z_bare = _re.search(r'\bZ\b(?!\d)', search_text, _re.IGNORECASE)
        m_t_bare = _re.search(r'\bT\b(?!\d)', search_text, _re.IGNORECASE)
        m_u_bare = _re.search(r'\bU\b(?!\d)', search_text, _re.IGNORECASE)
        u_rect_pitch = False
        if m_t:
            pitch_val = float(m_t.group(1))
            pitch_type = 'T'
            pitch_notation = f"T{_fmt(pitch_val)}"
        elif m_u_rect:
            _upy = float(m_u_rect.group(1))
            _upx = float(m_u_rect.group(2))
            pitch_val = min(_upy, _upx)
            pitch_type = 'U'
            pitch_notation = f"U{_fmt(_upy)}x{_fmt(_upx)}"
            u_rect_pitch = True
        elif m_u:
            pitch_val = float(m_u.group(1))
            pitch_type = 'U'
            pitch_notation = f"U{_fmt(pitch_val)}"
        elif m_z:
            # Z<py>x<px> — must store BOTH values
            _py = float(m_z.group(1))
            _px = float(m_z.group(2))
            pitch_val = _py                    # py = primary pitch value
            pitch_type = 'Z'
            pitch_notation = f"Z{_fmt(_py)}x{_fmt(_px)}"
        elif m_z_bare:
            pitch_val = None
            pitch_type = 'Z'
            pitch_notation = 'Z'
        elif m_t_bare:
            pitch_val = None
            pitch_type = 'T'
            pitch_notation = 'T'
        elif m_u_bare:
            pitch_val = None
            pitch_type = 'U'
            pitch_notation = 'U'
        else:
            pitch_val = None
            pitch_type = None
            pitch_notation = None



        # % vide if user provided it: "51%" or "open area 51" or "vide 51%"
        target_pct = parse_open_area_pct_from_text(search_text)

        # Sheet dimensions: handles "200x200x2" in one message, AND
        # "épaisseur 3mm" + "145cm x 125cm" split across turns (see
        # _extract_perf_sheet_dims_mm docstring).
        sheet_length, sheet_width, sheet_thickness = self._extract_perf_sheet_dims_mm(search_text)

        # ── 2. Determine mode ─────────────────────────────────────────────────
        # Priority: forward > reverse_D > reverse_C
        #
        # _has_shape_full : shape type AND numeric size are both known
        #                   (hole_d is the scalar proxy for R/C; for LR/LC hole_d = width)
        # _has_shape_type : shape family letter is known (may be without dimensions)
        # _has_pitch_full : pitch has at least one numeric value (or u_rect_pitch flag)
        _has_shape_full = hole_d is not None           # True for R12, C20, LR5x20, LC5x20
        _has_shape_type = hole_shape_type is not None  # True even for bare R, C, LR, LC
        _has_pitch_full = pitch_val is not None or u_rect_pitch

        if _has_shape_full and _has_pitch_full:
            mode = 'forward'
            notation = f"{hole_notation} {pitch_notation}"
        elif target_pct is not None and _has_pitch_full and not _has_shape_full:
            # reverse_D: % + pitch given → infer hole size.
            # Guard: LR/LC oblong has 2 unknowns (W and L); cannot solve with 1 equation.
            # The user must fix one dimension first — return None so the agent asks.
            if hole_shape_type in ('LR', 'LC'):
                logger.info(
                    f"[PERF_VIDE] ⚠️ reverse_D for oblong shape={hole_shape_type} is underdetermined "
                    f"(2 unknowns W+L, 1 equation). Need user to fix W or L. | session={session_id}"
                )
                return None
            if hole_shape_type is None:
                logger.info(f"[PERF_VIDE] ⚠️ reverse_D blocked — shape=null, NEVER default to R | session={session_id}")
                return None
            mode = 'reverse_D'
            # Use known shape letter or fall back to 'R' ONLY when no letter was detected at all.
            # This prevents silent R default when user wrote e.g. "C U40 25%" without a size.
            _shape_letter = hole_shape_type.rstrip('0123456789.')
            notation = f"{_shape_letter} {pitch_notation}"
        elif target_pct is not None and _has_shape_full and not _has_pitch_full:
            # reverse_C needs a pitch *family* (T / U / Z): formulas differ. Do not default to T.
            # Bare letter in text → OK. No letter at all → caller must ask user (param chain / questions).
            if pitch_type is None:
                logger.info(
                    f"[PERF_VIDE] reverse_C blocked — no pitch grid type (T/U/Z) in text | "
                    f"shape={hole_notation} target_pct={target_pct}% | session={session_id}"
                )
                return None
            mode = 'reverse_C'
            pitch_notation = pitch_notation or pitch_type
            notation = f"{hole_notation} {pitch_notation}"
        else:
            logger.info(
                f"[PERF_VIDE] ⚠️ Cannot determine mode — insufficient params | "
                f"shape_type={hole_shape_type} has_shape_full={_has_shape_full} "
                f"has_shape_type={_has_shape_type} has_pitch_full={_has_pitch_full} "
                f"target_pct={target_pct} | session={session_id}"
            )
            return None

        logger.info(
            f"[PERF_VIDE] 🔧 Function call triggered | "
            f"mode={mode} | notation='{notation}' | "
            f"dims={sheet_length}x{sheet_width}x{sheet_thickness} | "
            f"target_pct={target_pct} | session={session_id}"
        )

        # Reverse modes: notation already built correctly above
        # (no need to rebuild — hole_notation and pitch_notation are pre-computed)
        calc_reverse_mode = {
            'forward':   None,
            'reverse_D': 'hole_size',
            'reverse_C': 'pitch',
        }.get(mode, None)

        try:
            result = compute_perforated_sheet(
                notation_str    = notation,
                sheet_length    = sheet_length,
                sheet_width     = sheet_width,
                sheet_thickness = sheet_thickness,
                target_pct      = target_pct,
                reverse_mode    = calc_reverse_mode,
            )
        except Exception as e:
            logger.warning(f"[PERF_VIDE] ❌ Calculation failed: {e} | session={session_id}")
            return None

        # Guard: calculator returned a structured error dict (e.g. underdetermined)
        # rather than a valid result. Return None so the upstream safety-net can
        # ask the user for the missing constraint.
        if result.get("error"):
            logger.info(
                f"[PERF_VIDE] ⚠️ Calculator returned error | "
                f"error={result['error']} | message={result.get('message', '')[:120]} | "
                f"session={session_id}"
            )
            return None

        # ── 4. Log result clearly ─────────────────────────────────────────────
        calc_mode = result.get('mode', mode)  # use calculator's own mode key
        if calc_mode == 'forward':
            oa  = result.get('open_area', {})
            dfm = result.get('dfm', {}) or {}
            logger.info(
                f"[PERF_VIDE] ✅ Result | "
                f"mode={mode} | "
                f"theoretical={oa.get('theoretical_pct')}% | "
                f"actual={oa.get('actual_pct')}% | "
                f"holes={oa.get('hole_count')} | "
                f"dfm_warnings={len(dfm.get('warnings', []))} | "
                f"dfm_errors={len(dfm.get('errors', []))} | "
                f"session={session_id}"
            )
        else:
            # reverse mode: result has inferred_param / inferred_value_mm
            logger.info(
                f"[PERF_VIDE] 🔁 Reverse Result | "
                f"mode={mode} | "
                f"inferred_param={result.get('inferred_param')} | "
                f"inferred_value={result.get('inferred_value_mm')} mm | "
                f"target_pct={result.get('target_open_area_pct')}% | "
                f"session={session_id}"
            )
            # Normalize reverse result to add 'mode' key for session_state consumers
            result['_display_mode'] = mode

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # PERFORATED SHEET — DEDICATED LLM PARAM CHAIN (Step 4E)
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_perforated_param_chain(
        self,
        unified_output_obj,
        user_text: str,
        session_id: str,
    ):
        """
        Step 4E — Dedicated Perforated Sheet Parameter Extraction.

        Called from _unified_request_processor() when:
          - unified chain detected shape_type = "Perforated Sheet"
          - unified chain confirmed L/W/T are present (missing_info=False)
          - _invoke_unified_with_rag() set _needs_perf_param_chain=True

        Responsibilities:
          1. Invoke self.perforated_param_chain (focused LLM prompt) to extract
             hole shape / pitch / % vide from user_text.
             Handles BOTH notation (R12 T16) AND free language (entraxe triangulaire).
          2. params complete (missing=[])  →
               call _run_perf_vide_function_call() to compute result
               store in session_state['perf_calc_result']
          3. params incomplete (missing≠[]) →
               inject focused questions into unified_output_obj.questions
               set unified_output_obj.missing_info = True

        Returns unified_output_obj (possibly updated with questions).
        Multi-user safe: all state keyed by session_id.
        """
        import re as _re
        from src.utils.perforated_sheet_calculator import compute_perforated_sheet_from_extracted_params
        from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

        logger.info(
            f"[PERF_PARAM_CHAIN] 🔷 Starting | session={session_id} | "
            f"user_text_len={len(user_text)}"
        )

        # ── 1. User language (cached from unified chain pass) ───────────────
        state = self._get_session_state(session_id)
        user_language = state.get('user_language', 'French')

        # ── 2. Extract sheet dims hint (L/W/T, mm) ───────────────────────────
        # Cheap regex pre-parse, passed to the LLM as a grounding hint (see
        # _extract_perf_sheet_dims_mm docstring: handles "NxNxN", split-turn
        # "NxN" + separate thickness, and cm/m → mm). The LLM chain below does
        # its OWN (more capable, free-language-aware) extraction and returns
        # sheet_length_mm/width_mm/thickness_mm — that is the authoritative
        # source; this hint is only a fallback if the LLM omits a field.
        sheet_dims = "unknown"
        hint_length, hint_width, hint_thickness = self._extract_perf_sheet_dims_mm(user_text)
        if hint_length is not None and hint_width is not None:
            sheet_dims = f"L={hint_length} W={hint_width} T={hint_thickness}"
            logger.info(f"[PERF_PARAM_CHAIN] sheet_dims hint: {sheet_dims} | session={session_id}")

        dims_complete = not unified_output_obj.missing_info

        cached_params = state.get('perf_extracted_params')
        if cached_params and dims_complete:
            logger.info(f"[PERF_PARAM_CHAIN] ♻️ Using cached perf params from previous turn | session={session_id}")
            perf_params = cached_params
            perf_params['missing'] = []
            perf_params['questions'] = []
            self._update_session_state(session_id, perf_extracted_params=None)
        else:
            # ── 3. Invoke the perforated param chain ────────────────────────────
            cost_tracker = self._get_cost_tracker(session_id)
            try:
                perf_params = await ainvoke_with_cost_tracking(
                    "perf_param_extraction",
                    self.perforated_param_chain.ainvoke,
                    {
                        "user_text":     user_text,
                        "user_language": user_language,
                        "sheet_dims":    sheet_dims,
                        "session_id":    session_id,
                    },
                    cost_tracker,
                    self.model_names.get('default', 'unknown'),
                )
            except Exception as e:
                logger.error(
                    f"[PERF_PARAM_CHAIN] ❌ Chain invocation failed: {e} | session={session_id} — "
                    f"falling back to regex-based _run_perf_vide_function_call()"
                )
                # Fallback: try old regex path
                calc_result = self._run_perf_vide_function_call(
                    unified_output_obj, session_id, user_text
                )
                if calc_result:
                    self._update_session_state(session_id, perf_calc_result=calc_result)
                return unified_output_obj

        calc_mode = perf_params.get('calc_mode', 'unknown')
        missing   = perf_params.get('missing', [])
        questions = perf_params.get('questions', [])

        # LLM's own extraction is authoritative (handles free-language + units
        # across the WHOLE conversation, e.g. Vietnamese "chiều dài 1000 m" or
        # dims split across turns); fall back to the cheap regex hint only
        # when the LLM didn't return a value for a field.
        sheet_length    = perf_params.get('sheet_length_mm')    if perf_params.get('sheet_length_mm')    is not None else hint_length
        sheet_width     = perf_params.get('sheet_width_mm')     if perf_params.get('sheet_width_mm')     is not None else hint_width
        sheet_thickness = perf_params.get('sheet_thickness_mm') if perf_params.get('sheet_thickness_mm') is not None else hint_thickness

        logger.info(
            f"[PERF_PARAM_CHAIN] 📊 Extracted | "
            f"shape={perf_params.get('shape_notation')} | "
            f"pitch={perf_params.get('pitch_notation')} | "
            f"pct={perf_params.get('pct_vide')} | "
            f"calc_mode={calc_mode} | missing={missing} | "
            f"dims(mm)=L{sheet_length}xW{sheet_width}xT{sheet_thickness} "
            f"(llm=L{perf_params.get('sheet_length_mm')}xW{perf_params.get('sheet_width_mm')}xT{perf_params.get('sheet_thickness_mm')}) | "
            f"session={session_id}"
        )

        calc_result = None

        # ── 4A. Params complete → compute ────────────────────────────────────
        if not missing and calc_mode in ('forward', 'reverse_C', 'reverse_D'):
            if dims_complete:
                if sheet_length is None or sheet_width is None:
                    # unified_output_obj says dims are complete, but neither the LLM
                    # extraction nor the regex hint found them — compute would silently
                    # get hole_count=0 / actual_pct=None. Surface loudly so it's caught.
                    logger.warning(
                        f"[PERF_PARAM_CHAIN] ⚠️ dims_complete=True but sheet_length/width "
                        f"unresolved (L={sheet_length} W={sheet_width}) — % vide result will "
                        f"be theoretical-only | session={session_id}"
                    )
                shape_notation = perf_params.get('shape_notation') or ''
                pitch_notation = perf_params.get('pitch_notation') or ''
                pct_vide       = perf_params.get('pct_vide')

                # Compute directly from structured extraction; no synthetic text re-parse.
                calc_result = compute_perforated_sheet_from_extracted_params(
                    shape_notation=shape_notation,
                    pitch_notation=pitch_notation,
                    pct_vide=pct_vide,
                    calc_mode=calc_mode,
                    sheet_length=sheet_length,
                    sheet_width=sheet_width,
                    sheet_thickness=sheet_thickness,
                )
                if calc_result and not calc_result.get("error"):
                    self._update_session_state(session_id, perf_calc_result=calc_result)
                    logger.info(
                        f"[PERF_PARAM_CHAIN] ✅ Calculation done | "
                        f"mode={calc_result.get('mode')} | "
                        f"notation={calc_result.get('resolved_notation') or calc_result.get('input_notation')} | "
                        f"session={session_id}"
                    )
                else:
                    logger.warning(
                        f"[PERF_PARAM_CHAIN] ⚠️ Calculator returned None | session={session_id} — "
                        f"description_confirm will still display what it can"
                    )
                    # LLM may still emit reverse_C + missing=[] without a real T/U/Z in text — force ask.
                    if calc_mode == 'reverse_C':
                        from src.utils.language_utils import (
                            get_perforated_pitch_type_question,
                            get_session_language,
                        )
                        _lang_code = get_session_language(session_id)
                        _fq = get_perforated_pitch_type_question(_lang_code)
                        if _fq not in (unified_output_obj.questions or []):
                            unified_output_obj.questions.append(_fq)
                        unified_output_obj.missing_info = True
                        logger.info(
                            f"[PERF_PARAM_CHAIN] ❓ Injected pitch_type question (reverse_C calc refused) "
                            f"| session={session_id}"
                        )
            else:
                self._update_session_state(session_id,
                    perf_extracted_params={
                        'shape_notation': perf_params.get('shape_notation'),
                        'pitch_notation': perf_params.get('pitch_notation'),
                        'pct_vide': perf_params.get('pct_vide'),
                        'calc_mode': calc_mode,
                    }
                )
                logger.info(f"[PERF_PARAM_CHAIN] 💾 Extract-only mode (dims missing) | saved to session | session={session_id}")

        # ── 4B. Params incomplete → inject focused questions ───────────────────────
        else:
            logger.info(
                f"[PERF_PARAM_CHAIN] ❓ Missing: {missing} | "
                f"Injecting {len(questions)} question(s) | session={session_id}"
            )
            injected = 0
            for q in questions:
                if q and q not in unified_output_obj.questions:
                    unified_output_obj.questions.append(q)
                    injected += 1
            if injected:
                unified_output_obj.missing_info = True
                logger.info(
                    f"[PERF_PARAM_CHAIN] ✅ Injected {injected} question(s) → "
                    f"missing_info=True | session={session_id}"
                )
            else:
                # No questions generated but still marked missing:
                # fall back to regex path as last resort
                logger.warning(
                    f"[PERF_PARAM_CHAIN] ⚠️ No questions generated for missing={missing} | "
                    f"falling back to regex path | session={session_id}"
                )
                calc_result = self._run_perf_vide_function_call(
                    unified_output_obj, session_id, user_text
                )
                if calc_result:
                    self._update_session_state(session_id, perf_calc_result=calc_result)
                    # Chỉ reset missing_info nếu calc thực sự thành công VÀ missing chỉ là parse_error
                    if 'parse_error' in missing or 'chain_error' in missing:
                        unified_output_obj.missing_info = False

        return unified_output_obj

    async def _run_description_confirm(
        self, unified_output_obj, user_text_with_history: str, session_id: str
    ) -> dict:
        """Run Description Confirm chain and return confirm message for user.

        Multi-user safe: all state accessed/written through session_id key.
        Description is now derived entirely from user_text (conversation history).
        unified_output_obj is used only for session/round metadata.
        """
        from src.core.async_optimizations import async_timer
        state = self._get_session_state(session_id)
        confirm_round = state['confirm_count'] + 1

        # Language is already detected and cached in _SESSION_LANG_CACHE by
        # _invoke_unified_with_rag() on the first turn (using gpt-4.1-nano).
        # Here we just read the cache — zero LLM cost, guaranteed consistency.
        from src.utils.language_utils import get_session_language, lang_code_to_name
        lang_code = get_session_language(session_id)
        user_language = lang_code_to_name(lang_code)
        logger.info(f"[CONFIRM] Using cached lang={user_language} | session={session_id}")

        logger.info(f"[CONFIRM] Round {confirm_round}/{self.MAX_CONFIRM_ATTEMPTS} | session={session_id} | lang={user_language}")

        # Extract shape_type from unified_output_obj for shape-conditional template (Option A)
        shape_type = getattr(unified_output_obj, 'shape_type', 'unknown') or 'unknown'
        logger.info(f"[CONFIRM] shape_type={shape_type!r} → shape-conditional template will be used")

        # ── Read pre-computed Perforated Sheet result (function calling output) ──
        # Computed by _run_perf_vide_function_call() — LLM does NOT recompute here.
        # Template uses this to display % vide section in the confirm message.
        perf_calc = state.get('perf_calc_result')
        perf_info_str = ""
        if perf_calc:
            calc_mode = perf_calc.get('mode', 'forward')  # 'forward' or 'reverse'

            if calc_mode == 'forward':
                # Forward result: has open_area / hole / pitch / dfm sub-dicts
                oa  = perf_calc.get('open_area', {}) or {}
                # Estimate FreeCAD generation time from hole count + shape type
                _n_holes = oa.get('hole_count') or 0
                _raw_notation = perf_calc.get('input_notation', '') or ''
                try:
                    from src.utils.perf_time_estimator import estimate_freecad_time, extract_shape_letter
                    _shape_letter = extract_shape_letter(_raw_notation)
                    _est_time = estimate_freecad_time(int(_n_holes), _shape_letter, lang=user_language)
                except Exception:
                    _est_time = "~unknown"
                perf_info_str = (
                    f"mode=forward | "
                    f"notation={_raw_notation} | "
                    f"theoretical_pct={oa.get('theoretical_pct')}% | "
                    f"actual_pct={oa.get('actual_pct')}% | "
                    f"hole_count={_n_holes} | "
                    f"est_time={_est_time}"
                )
            else:
                # Reverse result: flat dict with inferred_param / inferred_value_mm
                # display_mode stored by _run_perf_vide_function_call
                display_mode   = perf_calc.get('_display_mode', 'reverse_C')
                inferred_param = perf_calc.get('inferred_param', '')
                inferred_value = perf_calc.get('inferred_value_mm', '')   # float or str, NO mm suffix
                target_pct     = perf_calc.get('target_open_area_pct', '')
                raw_notation   = perf_calc.get('input_notation', '')

                resolved_notation = perf_calc.get('resolved_notation') or raw_notation

                # Calculator runs a forward pass on resolved_notation when sheet dims
                # are available (perforated_sheet_calculator.py line 824-833).
                # Read those results so we can display actual_pct, hole_count, and est_time
                # for reverse modes too — not just for forward mode.
                _oa_rev = perf_calc.get('open_area') or {}
                _n_holes_rev   = _oa_rev.get('hole_count') or 0
                _actual_pct    = _oa_rev.get('actual_pct')
                _theo_pct      = _oa_rev.get('theoretical_pct')
                _est_time_rev  = "~unknown"
                if _n_holes_rev:
                    try:
                        from src.utils.perf_time_estimator import estimate_freecad_time, extract_shape_letter
                        _shape_letter_rev = extract_shape_letter(resolved_notation)
                        _est_time_rev = estimate_freecad_time(
                            int(_n_holes_rev), _shape_letter_rev, lang=user_language
                        )
                    except Exception:
                        _est_time_rev = "~unknown"

                perf_info_str = (
                    f"mode={display_mode} | "
                    f"target_pct={target_pct}% | "
                    f"notation={resolved_notation} | "      # ← fully resolved: R8.001 T16
                    f"inferred_param={inferred_param} | "
                    f"inferred_value={inferred_value}mm"    # ← no double-mm
                )
                # Append actual stats + est_time only when the forward pass succeeded
                if _n_holes_rev:
                    perf_info_str += (
                        f" | actual_pct={_actual_pct}%"
                        f" | theoretical_pct={_theo_pct}%"
                        f" | hole_count={_n_holes_rev}"
                        f" | est_time={_est_time_rev}"
                    )

            logger.info(f"[CONFIRM] 📊 perf_info injected | {perf_info_str[:140]}...")

        cost_tracker = self._get_cost_tracker(session_id)
        result = await ainvoke_with_cost_tracking(
            f"description_confirm_round{confirm_round}",
            self.description_confirm_chain.ainvoke,
            {
                'user_text': user_text_with_history,
                'confirm_round': confirm_round,
                'user_language': user_language,
                'shape_type': shape_type,
                'session_id': session_id,
                'perf_info': perf_info_str,   # ← PRE-COMPUTED, no LLM recompute
            },
            cost_tracker,
            self.model_names['confirm']
        )

        final_description = result.get('final_description', '')
        confirm_message = result.get('confirm_message', f"\U0001f4cb {final_description}\n\n\u2705 Reply yes/ok to generate.")

        # Update confirm state (per-session, thread-safe via session isolation)
        self._update_session_state(
            session_id,
            confirm_count=confirm_round,
            awaiting_confirm=True,
            confirmed_description=final_description,
        )

        return {
            'code': None,
            'message': confirm_message,
            'explanation': f'Awaiting description confirmation (round {confirm_round}/{self.MAX_CONFIRM_ATTEMPTS})'
        }


    def _has_db_history(self, session_id: str) -> bool:
        """
        Check if DB has any prior chat turns for this session.
        Used to decide whether to skip greeting check and build conversation history.
        Replaces the old conversation_state == 'collecting_info' gate.
        """
        try:
            from src.database.database import SessionLocal
            from src.models.sessions import ChatHistory
            db = SessionLocal()
            try:
                count = db.query(ChatHistory).filter(
                    ChatHistory.session_id == session_id
                ).count()
                return count > 0
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[HISTORY] _has_db_history check failed for {session_id}: {e}")
            return False

    async def _recover_session_state_from_db(self, session_id: str) -> None:
        """
        Recover last_confirmed_description and edit_history_since_confirm from the database
        in case of a server restart or cache expiration.
        """
        try:
            from src.database.database import SessionLocal
            from src.models.sessions import ChatHistory
            import re

            loop = asyncio.get_event_loop()

            def perform_query():
                db = SessionLocal()
                try:
                    return (
                        db.query(ChatHistory)
                        .filter(ChatHistory.session_id == session_id)
                        .order_by(ChatHistory.id.asc())
                        .all()
                    )
                finally:
                    db.close()

            rows = await loop.run_in_executor(None, perform_query)
            if not rows:
                logger.warning(f"[RECOVER_STATE] No ChatHistory rows found for session {session_id} — cannot recover")
                return

            # Find the last row where the user confirmed a template.
            last_confirm_index = -1
            CONFIRM_WORDS = {"yes", "oui", "ok", "y", "confirm", "approve", "xác nhận", "d'accord", "dac", "agree"}

            for i in range(len(rows) - 1, -1, -1):
                msg = rows[i].message or ""
                clean_msg = msg.strip().lower().rstrip('.!')
                if clean_msg in CONFIRM_WORDS and rows[i].lasted_code:
                    last_confirm_index = i
                    break

            if last_confirm_index == -1:
                # Fallback: find the first code-generating turn
                for i in range(len(rows)):
                    if rows[i].lasted_code:
                        last_confirm_index = i
                        break

            if last_confirm_index == -1:
                logger.warning(
                    f"[RECOVER_STATE] No row with lasted_code found for session {session_id} "
                    f"({len(rows)} rows checked) — cannot recover last_confirmed_description"
                )
                return

            confirmed_desc = ""
            for idx in range(last_confirm_index, -1, -1):
                resp = rows[idx].response or rows[idx].output or ""
                if "📋" in resp or "Répondez" in resp or "Reply yes" in resp:
                    match = re.search(r'(?i)(?:description|description\s+technique)\s*:\s*(.*?)(?=\n-|\n\n|\Z)', resp, re.DOTALL)
                    if match:
                        confirmed_desc = match.group(1).strip().strip("* ")
                        break
                    else:
                        lines = resp.split('\n')
                        clean_lines = []
                        for line in lines:
                            if not any(kw in line for kw in ["📋", "Répondez", "Reply yes", "yes/ok", "Modifier", "Valider"]):
                                clean_lines.append(line)
                        confirmed_desc = "\n".join(clean_lines).strip()
                        break

            if not confirmed_desc:
                confirmed_desc = rows[0].message or ""

            edit_history = []
            for idx in range(last_confirm_index + 1, len(rows)):
                msg = rows[idx].message or ""
                clean_msg = msg.strip().lower().rstrip('.!')
                if msg.strip() and clean_msg not in CONFIRM_WORDS:
                    edit_history.append(msg.strip())

            self._update_session_state(
                session_id,
                last_confirmed_description=confirmed_desc,
                edit_history_since_confirm=edit_history
            )
            logger.info(
                f"[RECOVER_STATE] Successfully recovered state for session {session_id}. "
                f"last_confirmed_description={confirmed_desc[:50]}... "
                f"edit_history_since_confirm={edit_history}"
            )
        except Exception as e:
            logger.warning(f"[RECOVER_STATE] Failed to recover session state from DB: {e}")

    def _build_user_text_with_history(self, session_id: str, current_user_input: str) -> str:
        """
        Build user_text with full conversation history in [USER]/[CHATBOT] format.

        DB is the single source of truth — every turn (Q&A, info, code gen)
        is persisted by generate_cad_realtime_stream() and handle_chat_request(),
        so no session-state fallback is needed.

        Format:
            [USER]: first message
            [CHATBOT]: first response  (question, info, or success msg)
            [USER]: second message
            ...
            [USER]: current message

        Args:
            session_id: Session identifier
            current_user_input: Current user message (appended at the end)

        Returns:
            Formatted string with full conversation history + current input
        """
        try:
            from src.database.database import SessionLocal
            from src.models.sessions import ChatHistory

            db = SessionLocal()
            try:
                chat_entries = db.query(ChatHistory).filter(
                    ChatHistory.session_id == session_id
                ).order_by(ChatHistory.created_at.asc()).all()

                formatted = ""
                for entry in chat_entries:
                    if entry.message:
                        formatted += f"[USER]: {entry.message}\n"
                    chatbot_response = entry.response or entry.output
                    if chatbot_response:
                        formatted += f"[CHATBOT]: {chatbot_response}\n"

                if chat_entries:
                    logger.info(f"[HISTORY] Built from DB: {len(chat_entries)} entries for session {session_id}")
                else:
                    logger.info(f"[HISTORY] No prior history — first turn for session {session_id}")

                if current_user_input:
                    formatted += f"[USER]: {current_user_input}\n"

                return formatted

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[HISTORY] Failed to build history for session {session_id}: {e}")
            return f"[USER]: {current_user_input}\n"

    @staticmethod
    def _extract_user_only_query(user_text: str) -> str:
        """
        Extract only [USER] turns from a full [USER]/[CHATBOT] conversation string.

        Rationale: RAG vector search degrades when chatbot responses (which can be
        very verbose — markdown, HTML spans, bullet lists) are included in the query.
        The semantic signal we want comes exclusively from the user's own words.

        Uses DOTALL regex so multi-line [USER] blocks are captured in full.

        Args:
            user_text: Full conversation history with [USER] and [CHATBOT] turns.

        Returns:
            Concatenation of all [USER] blocks, separated by newlines.
            Falls back to `user_text` unchanged if no [USER] markers are found.
        """
        import re as _re
        user_sections = _re.findall(
            r'\[USER\]:(.*?)(?=\[(?:USER|CHATBOT)\]:|$)',
            user_text,
            _re.DOTALL
        )
        user_only_parts = [s.strip() for s in user_sections if s.strip()]
        return "\n\n".join(user_only_parts) if user_only_parts else user_text

    def _build_post_codegen_history(self, session_id: str, current_user_input: str) -> str:
        """
        Build conversation history containing ONLY messages sent AFTER the last
        successful code generation for this session.

        Rationale: In edit mode, passing the full chat history (including the
        original shape-creation request) confuses the LLM. It sees e.g.
        "offset_z = 50" already in original_code and in the old request, and
        concludes the edit has already been applied — returning unchanged code.

        By limiting user_request to post-codegen messages, the LLM only sees
        the actual edit instructions, preventing this false-positive match.

        Strategy:
            1. Find the latest ChatHistory row that has a non-null `lasted_code`.
               That row's `id` marks the boundary of the last successful code gen.
            2. Collect all rows AFTER that boundary → these are the edit turns.
            3. Format them as [USER]/[CHATBOT] lines and append current_user_input.

        Falls back to full history (via _build_user_text_with_history) if:
            - DB access fails
            - No prior code-gen row exists (first generation)
            - No post-codegen rows exist yet (current message IS the first edit)

        Args:
            session_id:         Session identifier.
            current_user_input: The current user message (appended at the end).

        Returns:
            Formatted [USER]/[CHATBOT] string covering only the edit phase.
        """
        try:
            from src.database.database import SessionLocal
            from src.models.sessions import ChatHistory

            db = SessionLocal()
            try:
                # ── Step 1: Find the last row that carried generated code ─────
                last_codegen_row = (
                    db.query(ChatHistory)
                    .filter(
                        ChatHistory.session_id == session_id,
                        ChatHistory.lasted_code.isnot(None),
                    )
                    .order_by(ChatHistory.id.desc())
                    .first()
                )

                if last_codegen_row is None:
                    # No prior code gen found — fall back to full history
                    logger.info(
                        f"[POST_CODEGEN_HISTORY] No prior codegen row found for "
                        f"session {session_id}; falling back to full history"
                    )
                    return self._build_user_text_with_history(session_id, current_user_input)

                boundary_id = last_codegen_row.id
                logger.info(
                    f"[POST_CODEGEN_HISTORY] Codegen boundary id={boundary_id} | "
                    f"session={session_id}"
                )

                # ── Step 2: Collect all rows AFTER the boundary ───────────────
                post_rows = (
                    db.query(ChatHistory)
                    .filter(
                        ChatHistory.session_id == session_id,
                        ChatHistory.id > boundary_id,
                    )
                    .order_by(ChatHistory.id.asc())
                    .all()
                )

                if not post_rows:
                    # No rows after code gen yet — current message is the first edit.
                    # Return only the current message so the LLM sees just the edit intent.
                    logger.info(
                        f"[POST_CODEGEN_HISTORY] No post-codegen rows yet; "
                        f"returning current message only | session={session_id}"
                    )
                    return f"[USER]: {current_user_input}\n" if current_user_input else ""

                # ── Step 3: Format post-codegen turns ─────────────────────────
                formatted = ""
                for entry in post_rows:
                    if entry.message:
                        formatted += f"[USER]: {entry.message}\n"
                    chatbot_response = entry.response or entry.output
                    if chatbot_response:
                        formatted += f"[CHATBOT]: {chatbot_response}\n"

                if current_user_input:
                    formatted += f"[USER]: {current_user_input}\n"

                logger.info(
                    f"[POST_CODEGEN_HISTORY] Built from {len(post_rows)} post-codegen "
                    f"rows ({len(formatted)} chars) | session={session_id}"
                )
                return formatted

            finally:
                db.close()

        except Exception as e:
            logger.warning(
                f"[POST_CODEGEN_HISTORY] Failed for session {session_id}: {e}; "
                f"falling back to full history"
            )
            return self._build_user_text_with_history(session_id, current_user_input)


    def _create_smart_response(self, unified_output_obj, user_text: str) -> dict:
        """Create smart response that uses questions to interact with user"""
        # Check if user explicitly requested detailed explanation
        wants_details = detect_detailed_explanation_request(user_text)

        # Let AI determine if this is an information request through unified analysis
        # No more rule-based keyword checking

        if not unified_output_obj.questions:
            return {
                "code": None,
                "message": "No additional information needed.",
                "explanation": None
            }

        # Format questions without numbering
        questions_str = "\n".join(unified_output_obj.questions)

        return {
            "code": None,
            "message": questions_str,
            "explanation": questions_str if wants_details else None
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # SINGLE DECISION ENGINE - sole place to determine action
    # Both PATH A (_unified_request_processor) and PATH B (streaming generator)
    # must call _make_decision() instead of writing separate if/elif blocks.
    # ═══════════════════════════════════════════════════════════════════════════

    class CADDecision:
        """
        Sole decision result after unified analysis.
        Only 3 actions - no ambiguity.
        """
        ASK_QUESTIONS = "ask_questions"  # missing_info=true + questions → ask user
        RETURN_INFO   = "return_info"    # missing_info=false + questions + not skip → return info list / warning
        GENERATE_CODE = "generate_code"  # missing_info=false + no questions (or skip=true) → gen code

        def __init__(self, action: str, payload: dict):
            self.action  = action
            self.payload = payload

        def __repr__(self):
            return f"CADDecision(action={self.action}, payload_keys={list(self.payload.keys())})"

    def _make_decision(
        self,
        unified_output_obj,
        skip_questions: bool,
        override_intent: bool = False,
        max_attempts_reached: bool = False,
    ) -> "TextToCADAgent.CADDecision":
        """
        SINGLE SOURCE OF TRUTH for decision making after unified analysis.

        Rules (in order of priority):
          1. skip=True  OR  max_attempts_reached  OR  override_intent
             + missing_info=False  →  GENERATE_CODE
          2. missing_info=True + questions            →  ASK_QUESTIONS
          3. missing_info=False + questions + !skip   →  RETURN_INFO  (list, warning)
          4. All other cases                         →  GENERATE_CODE

        Args:
            unified_output_obj: Result from unified analysis chain
            skip_questions:     User requests to skip questions
            override_intent:    User requests to override rule violations
            max_attempts_reached: Reached the maximum limit of asked questions
        """
        missing   = unified_output_obj.missing_info
        questions = unified_output_obj.questions  # list[str]
        has_q     = bool(questions)

        # ── Rule 1: Force proceed (skip / override / max_attempts) ──────────
        if skip_questions or override_intent or max_attempts_reached:
            logger.info(
                f"[DECISION] → GENERATE_CODE (force: skip={skip_questions}, "
                f"override={override_intent}, max_attempts={max_attempts_reached})"
            )
            return self.CADDecision(
                self.CADDecision.GENERATE_CODE,
                {"requirements": unified_output_obj, "reason": "force_proceed"}
            )

        # ── Rule 2: Missing info → ask questions ──────────────────────────
        if missing and has_q:
            logger.info(
                f"[DECISION] → ASK_QUESTIONS "
                f"(missing_info=True, {len(questions)} questions)"
            )
            return self.CADDecision(
                self.CADDecision.ASK_QUESTIONS,
                {"questions": questions}
            )

        # ── Rule 3: Not missing but has content → return info/warning ────────
        if not missing and has_q:
            logger.info(
                f"[DECISION] → RETURN_INFO "
                f"(missing_info=False, {len(questions)} questions as info)"
            )
            return self.CADDecision(
                self.CADDecision.RETURN_INFO,
                {"info": "\n".join(questions)}
            )

        # ── Rule 4: Nothing else to ask → gen code ─────────────────────────
        logger.info(
            f"[DECISION] → GENERATE_CODE "
            f"(missing_info=False, no questions)"
        )
        return self.CADDecision(
            self.CADDecision.GENERATE_CODE,
            {"requirements": unified_output_obj, "reason": "all_complete"}
        )

    async def _unified_request_processor(
        self,
        user_text: str,
        session_id: str,
    ) -> dict:
        """
        Run the unified analysis → confirm/code-generation flow for one turn.

        The only caller is process_edit_request(), which routes here when a
        session in edit mode receives a confirm reply. Greeting / information-
        request classification is deliberately NOT run here: the caller has
        already established this is a CAD turn, and process_request_with_progress()
        owns that classification for every other entry point.

        Args:
            user_text: User input text (may contain full conversation history)
            session_id: Session identifier

        Returns:
            Response dict with code, message, explanation
        """
        from src.core.async_optimizations import async_timer

        # Async session state loading
        @async_timer(f"session_state_load_{session_id}")
        async def load_session_state_async():
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._get_session_state, session_id)

        state = await load_session_state_async()

        try:
            # Step 1: Build user_text with conversation history using unified format
            @async_timer(f"build_user_text_cad_{session_id}")
            async def build_user_text_async():
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, self._build_user_text_with_history, session_id, user_text)
            
            user_text_with_history = await build_user_text_async()
            print(f"[CAD_REQUEST] Built unified user_text with history (length: {len(user_text_with_history)} chars)")

            # Step 2: Unified processing with RAG
            @async_timer(f"unified_processing_{session_id}")
            async def process_unified_async():
                return await self._invoke_unified_with_rag(
                    user_text=user_text_with_history,  # ✅ Unified [USER]/[CHATBOT] format
                    session_id=session_id,
                    previous_responses_formatted="",  # No longer needed - history in user_text
                    latest_requirements_for_guidance=state['latest_requirements'],
                    material=state.get('material_choice', ''),  # Pass material choice to template
                    mapped_material=state.get('mapped_material', 'steel')  # Pass mapped material
                )
            
            unified_chain_result = await process_unified_async()

            unified_output_obj = unified_chain_result["unified_output_obj"]
            raw_unified_json = unified_chain_result["raw_unified_json"]
            retrieved_context_for_code_gen = unified_chain_result["retrieved_context_for_code_gen"]
            expanded_user_text = unified_chain_result.get("expanded_user_text", user_text_with_history)

            # ── Perforated Sheet dedicated chain (LLM, focused) ─────────────────────────────
            # Runs when unified signalled _needs_perf_param_chain=True.
            # Extracts hole shape/pitch/% from both notation AND natural language.
            # If params are complete → compute_perforated_sheet() is called here.
            # If params are missing → questions injected into unified_output_obj.
            if unified_chain_result.get("_needs_perf_param_chain"):
                unified_output_obj = await self._run_perforated_param_chain(
                    unified_output_obj, user_text_with_history, session_id
                )

            print(f"\n[SUCCESS] Unified analysis and parameter check successful for session {session_id}:")
            print(f"Raw Unified JSON: {raw_unified_json[:500]}...") if len(raw_unified_json) > 500 else print(f"Raw Unified JSON: {raw_unified_json}")
            print(f"Parsed Output: {json.dumps(unified_output_obj.dict(), indent=2)}")

            # Step 3: Update session state
            @async_timer(f"session_state_update_{session_id}")
            async def update_session_state_async():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self._update_session_state(
                        session_id,
                        latest_requirements=unified_output_obj,
                    )
                )
            
            await update_session_state_async()

            # Step 4: Handle parsing errors
            if unified_output_obj.title == "Unable to parse requirements":
                error_details = "\n".join(unified_output_obj.questions) if unified_output_obj.questions else "Unable to parse requirements"
                print(f"[ERROR] Parsing error for session {session_id}: {error_details}")
                
                # Error path: just reset pending_questions so next turn starts fresh
                self._update_session_state(session_id, pending_questions=[])


                return {
                    "code": None,
                    "message": f"I had trouble understanding your request. Could you please try rephrasing it? Details: {error_details}",
                    "explanation": error_details
                }

            # Step 5: Handle conversational responses
            if unified_output_obj.title == "Conversational Response":
                print(f"[CONVERSATIONAL] Detected conversational response for session {session_id}")
                response_msg = "\n".join(unified_output_obj.questions) if unified_output_obj.questions else "Conversational response provided"
                return {
                    "code": None,
                    "message": response_msg,
                    "explanation": response_msg
                }

            # Step 6: Extract AI-determined flags
            skip_questions  = unified_output_obj.skip_questions_requested
            override_intent = unified_output_obj.override_intent_detected

            # ── Log raw AI flags để debug ───────────────────────────────────
            print(f"\n{'='*60}")
            print(f"[DEBUG_FLAGS] Session: {session_id}")
            print(f"[DEBUG_FLAGS] missing_info               = {unified_output_obj.missing_info}")
            print(f"[DEBUG_FLAGS] questions                  = {unified_output_obj.questions}")
            print(f"[DEBUG_FLAGS] skip_questions_requested   = {skip_questions}")
            print(f"[DEBUG_FLAGS] override_intent_detected   = {override_intent}")
            print(f"{'='*60}\n")

            # ════════════════════════════════════════════════════════════════
            # SINGLE DECISION ENGINE - single decision point
            # ════════════════════════════════════════════════════════════════
            # max_attempts_reached is always False on this path: the caller
            # (process_edit_request) reaches here with a confirm reply, not with
            # an unresolved clarifying-question round. The MAX_QUESTION_ATTEMPTS
            # cap is enforced in process_request_with_progress(), which owns the
            # question-asking loop.
            decision = self._make_decision(
                unified_output_obj,
                skip_questions=skip_questions,
                override_intent=override_intent,
                max_attempts_reached=False,
            )
            print(f"[DECISION] → {decision.action} | session={session_id}")

            # ── ASK_QUESTIONS: missing info, ask user ───────────
            if decision.action == self.CADDecision.ASK_QUESTIONS:
                print(f"\n❓ Missing information detected for session {session_id}. Questions: {unified_output_obj.questions}")

                # LLM already filters answered questions from full DB history
                # embedded in user_text. No manual keyword matching needed.
                pending_questions = unified_output_obj.questions or []

                if pending_questions:
                    self._update_session_state(session_id, pending_questions=pending_questions)
                    print(f"[DEBUG] Stored {len(pending_questions)} pending questions for session {session_id}")
                    return self._create_smart_response(unified_output_obj, user_text)
                else:
                    # All questions resolved — fall through to GENERATE_CODE
                    print(f"[SUCCESS] All questions appear to have been answered for session {session_id}.")

            # ── RETURN_INFO: not missing but has info/warning to return ──────
            elif decision.action == self.CADDecision.RETURN_INFO:
                print(f"\n[INFO] Returning information/warning to user for session {session_id}")
                print(f"[INFO] Content: {decision.payload['info'][:200]}...")
                return {
                    "code": None,
                    "message": decision.payload["info"],
                    "explanation": decision.payload["info"]
                }

            # ── GENERATE_CODE: đủ thông tin → confirm or gen code ──────────
            # (cả GENERATE_CODE lẫn ASK_QUESTIONS khi pending_questions rỗng đều reach đây)
            print(f"\n[SUCCESS] ✅ Proceeding to confirm/code generation for session {session_id}")
            print(f"[SUCCESS] Decision reason: {decision.payload.get('reason', 'normal')}")

            # Clear pending_questions now that we are generating
            self._update_session_state(session_id, pending_questions=[])

            # ══════════════════════════════════════════════════════════════════
            # CONFIRM / STEP-PLAN FLOW
            # Priority order (evaluated top-to-bottom, first match wins):
            #   [GATE 1] awaiting_step_plan_confirm → user replied YES/NO → fall through to Confirm
            #   [GATE 2] step_by_step_requested     → explicit intent → run Step Planner → return
            #   [GATE 3] awaiting_confirm + confirm  → CASE 1 → Generate Code
            #   [GATE 4] skip_confirm or Confirm chain → CASE 2/3
            # ══════════════════════════════════════════════════════════════════
            confirm_state              = self._get_session_state(session_id)
            confirm_count              = confirm_state.get('confirm_count', 0)
            awaiting_confirm           = confirm_state.get('awaiting_confirm', False)
            confirmed_description      = confirm_state.get('confirmed_description', '')
            awaiting_step_plan_confirm = confirm_state.get('awaiting_step_plan_confirm', False)

            print(f"\n{'='*60}")
            print(f"[FLOW_STATE] session={session_id}")
            print(f"[FLOW_STATE] awaiting_step_plan_confirm = {awaiting_step_plan_confirm}")
            print(f"[FLOW_STATE] awaiting_confirm           = {awaiting_confirm}")
            print(f"[FLOW_STATE] confirm_count              = {confirm_count}")
            print(f"[FLOW_STATE] confirm_intent_detected    = {unified_output_obj.confirm_intent_detected}")
            print(f"[FLOW_STATE] step_by_step_requested     = {unified_output_obj.step_by_step_requested}")
            print(f"[FLOW_STATE] complexity_level           = {unified_output_obj.complexity_level}")
            print(f"{'='*60}\n")

            # ── GATE 1: User replied to step plan (YES or NO) → both fall through to Confirm ──
            if awaiting_step_plan_confirm:
                logger.info(
                    f"[STEP_PLAN] User replied to plan (YES/NO — both → Description Confirm) "
                    f"| session={session_id}"
                )
                print(f"[STEP_PLAN] User replied to step plan → proceeding to Description Confirm")
                self._update_session_state(session_id, awaiting_step_plan_confirm=False)
                # Fall through to Description Confirm (GATE 3/4) below ↓

            # ── GATE 2: Unified detected explicit step-by-step request → Run Step Planner ──
            elif unified_output_obj.step_by_step_requested:
                logger.info(
                    f"[STEP_PLAN] 🔧 User explicitly requested step plan → Running Step Planner "
                    f"| session={session_id}"
                )
                print(f"[STEP_PLAN] 🔧 step_by_step_requested=True → firing Step Planner chain...")

                _total, _user_msg = await self._run_step_planner(
                    session_id, unified_output_obj, raw_unified_json,
                    retrieved_context_for_code_gen, expanded_user_text,
                    empty_steps_text="  (aucune étape générée)",
                )

                return {
                    "code":        None,
                    "message":     _user_msg,
                    "explanation": f"Step plan generated ({_total} steps)"
                }

            # ── GATE 3: CASE 1 — User confirmed 📋 description → Generate Code ──────────
            if awaiting_confirm and unified_output_obj.confirm_intent_detected:
                logger.info(
                    f"[CONFIRM] ✅ CASE 1 — User confirmed description "
                    f"after round {confirm_count} → Generate Code | session={session_id}"
                )
                print(f"[CONFIRM] ✅ CASE 1 — description confirmed, generating code...")
                self._update_session_state(session_id,
                    awaiting_confirm=False,
                    confirm_count=0,
                )
                if confirmed_description:
                    unified_output_obj.description = confirmed_description
                    logger.info(f"[CONFIRM] Using confirmed_description ({len(confirmed_description)} chars) for code gen")
                return await self.generate_code_from_requirements(
                    unified_output_obj, raw_unified_json, retrieved_context_for_code_gen,
                    session_id=session_id, current_user_message=user_text, expanded_user_text=expanded_user_text
                )

            # ── GATE 4: CASE 2/3 — Determine skip confirm or run Description Confirm ────
            # NOTE: _has_operations is checked against user_text (not description which is always
            # empty at this point — description_confirm_template is what populates it).
            _user_text_lower = (user_text or "").lower()
            _has_operations = any(kw in _user_text_lower for kw in [
                "hole", "trou", "trous", "perçage", "percage",
                "bend", "pli", "plis", "pliage",
                "cut", "découpe", "decoupe", "slot", "rainure",
                "fillet", "congé", "conge", "chamfer", "chanfrein",
                "countersink", "fraisage", "taraudage", "tapping",
                "notch", "pocket", "embossing",
            ])
            logger.info(
                f"[CONFIRM] has_operations={_has_operations} "
                f"| complexity={unified_output_obj.complexity_level} "
                f"| confirm_count={confirm_count} | session={session_id}"
            )

            # skip_confirm: only when user explicitly requests skip OR max attempts reached.
            # complexity_level==1 no longer skips confirm — all requests go through description_confirm
            # so that code_gen always receives a structured description instead of raw user_text.
            skip_confirm = (
                (unified_output_obj.skip_questions_requested and not unified_output_obj.override_intent_detected)
                or confirm_count >= self.MAX_CONFIRM_ATTEMPTS
            )

            if skip_confirm:
                if confirm_count >= self.MAX_CONFIRM_ATTEMPTS:
                    logger.info(
                        f"[CONFIRM] SKIP — Max rounds ({self.MAX_CONFIRM_ATTEMPTS}) reached "
                        f"→ auto generate | session={session_id}"
                    )
                    print(f"[CONFIRM] Max confirm rounds reached → auto generate")
                    if confirmed_description:
                        unified_output_obj.description = confirmed_description
                else:
                    logger.info(
                        f"[CONFIRM] SKIP — Level={unified_output_obj.complexity_level}, "
                        f"skip_requested={unified_output_obj.skip_questions_requested} "
                        f"→ direct generate | session={session_id}"
                    )
                    print(f"[CONFIRM] Skipping confirm → direct code generation")
                self._update_session_state(
                    session_id,
                    awaiting_confirm=False,
                    confirm_count=0,
                    confirmed_description='',
                )
                return await self.generate_code_from_requirements(
                    unified_output_obj, raw_unified_json, retrieved_context_for_code_gen,
                    session_id=session_id, current_user_message=user_text, expanded_user_text=expanded_user_text
                )

            # CASE 3: Run Description Confirm chain → show 📋 message
            logger.info(
                f"[CONFIRM] → CASE 3 — Running Description Confirm chain "
                f"(round {confirm_count + 1}) | session={session_id}"
            )
            print(f"[CONFIRM] → CASE 3 — Description Confirm chain (round {confirm_count + 1})")
            # Save confirm cache so that the fast-path gate (in generate_cad_realtime_stream)
            # can restore the unified result on the next turn when user confirms ("oui"/"yes").
            self._save_confirm_cache(
                session_id, unified_output_obj, raw_unified_json,
                retrieved_context_for_code_gen, expanded_user_text
            )
            return await self._run_description_confirm(
                unified_output_obj, user_text_with_history, session_id  # ← full history, not just current input
            )


        except Exception as e:

            import traceback
            error_traceback = traceback.format_exc()
            print(f"[ERROR] Error during unified request processing for session {session_id}: {e}")
            print(f"Traceback: {error_traceback}")
            return {"error": f"Error processing request: {str(e)}", "code": None}

    async def process_request(self, user_text, is_edit_request=False, session_id=None, request_origin='api', material_choice=None):
        """
        Wrapper around process_request_with_progress to execute it synchronously/awaitable
        and return the final result. Consumes the generator.
        """
        final_res = {"error": "No result yielded from agent", "code": None}
        async for update in self.process_request_with_progress(
            user_text=user_text,
            is_edit_request=is_edit_request,
            session_id=session_id,
            request_origin=request_origin,
            material_choice=material_choice
        ):
            if isinstance(update, dict) and "final_result" in update:
                final_res = update["final_result"]
        return final_res



    def _extract_user_text_from_state(self, state, session_id: str = None) -> str:
        """
        Extract the original (first) user message for use in file metadata.
        Reads from DB so it works even after session_state has been cleaned up.
        Falls back to latest_title on the state if DB is empty.
        """
        if session_id:
            try:
                from src.database.database import SessionLocal
                from src.models.sessions import ChatHistory
                db = SessionLocal()
                try:
                    first_entry = db.query(ChatHistory).filter(
                        ChatHistory.session_id == session_id
                    ).order_by(ChatHistory.created_at.asc()).first()
                    if first_entry and first_entry.message:
                        return first_entry.message
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"[EXTRACT_TEXT] DB lookup failed for session {session_id}: {e}")
        # Final fallback: session title stored during code gen
        return state.get('latest_title') or ""

    async def generate_code_from_requirements(self, design_requirements_obj: AnalysisAndParameterCheckOutput, raw_design_requirements_json: str, retrieved_context: str, session_id=None, save_files=True, current_user_message: str = None, expanded_user_text: str = None):
        """
        Generate FreeCAD code from design requirements.
        
        Note: retrieved_context parameter is kept for backward compatibility but is ignored.
        RAG context is retrieved separately within this function for code generation.
        
        Args:
            current_user_message: The latest user message (not yet saved in database)
        """
        from src.core.async_optimizations import async_timer
        
        if session_id is None:
            print("ERROR: session_id is None in generate_code_from_requirements. Cannot proceed.")
            return {"error": ERROR_CODES["105.2"], "code": None}

        state = self._get_session_state(session_id)
        confirmed_rag_description = (design_requirements_obj.description or "").strip()

        # Single log: description input to CHAIN 3
        logger.info(f"[CODE_GEN_INPUT] 🎯 description → CHAIN 3: {design_requirements_obj.description}")

        # ═══════════════════════════════════════════════════════════════════════
        # SIMPLE FIX: Use database history + current user message
        # The database history may not include the latest user message yet,
        # so we explicitly pass and append it.
        # ═══════════════════════════════════════════════════════════════════════
        # Use expanded_user_text if provided (from unified analysis), otherwise build from history
        if expanded_user_text:
            user_text = expanded_user_text
            logger.info(f"[CODE_GEN] ✅ Using expanded_user_text from unified analysis ({len(user_text)} chars)")
        else:
            user_text = self._build_user_text_with_history(session_id, current_user_message or "")
            logger.info(f"[CODE_GEN] Using original user_text from history ({len(user_text)} chars)")
        
        # Remove the trailing "[USER]: \n" from empty current input
        if user_text.endswith("[USER]: \n"):
            user_text = user_text[:-len("[USER]: \n")]
        
        try:
            # Check if we already have retrieved context from Unified Analysis
            rag_context_content = ""
            
            def append_confirmed_info_context(context: str) -> str:
                if not confirmed_rag_description:
                    return context
                try:
                    from langchain_core.documents import Document
                    from src.core.agent_utils import format_retrieved_context
                    from src.rag.retriever import (
                        classify_user_query_for_info,
                        search_info_by_class,
                        faiss_index_instance,
                    )

                    info_classes = classify_user_query_for_info(confirmed_rag_description)
                    if not info_classes:
                        return context

                    info_docs = []
                    context_lower = context.lower()
                    for class_name in info_classes:
                        if "type: info" in context_lower and class_name.lower() in context_lower:
                            continue
                        for info in search_info_by_class(class_name, faiss_index_instance, confirmed_rag_description, k=100):
                            description = info.get('description', '')
                            if isinstance(description, list):
                                description = '\n'.join(description) if description else ''
                            elif not isinstance(description, str):
                                description = str(description) if description else ''

                            page_content = f"Info for {class_name}:\n"
                            page_content += f"Info ID: {info.get('info_id', 'Unknown')}\n"
                            page_content += f"Title: {info.get('title', 'Unknown Info')}\n"
                            page_content += f"Description: {description}\n"
                            page_content += f"Rule: {info.get('rule', '')}\n"
                            page_content += f"Parameters: {', '.join(info.get('parameters') or [])}\n"

                            info_docs.append(Document(
                                page_content=page_content,
                                metadata={
                                    "source": info.get('source', ''),
                                    "type": "info",
                                    "class": class_name,
                                    "info_id": info.get('info_id', ''),
                                    "category": info.get('category', ''),
                                }
                            ))

                    if not info_docs:
                        return context

                    logger.info(
                        f"[CODE_GEN] Appending {len(info_docs)} info docs from confirmed description: {info_classes}"
                    )
                    return f"{context}\n\n{format_retrieved_context(info_docs)}"
                except Exception as e:
                    logger.warning(f"[CODE_GEN] Failed to append confirmed-description info: {e}")
                    return context

            if retrieved_context and len(retrieved_context) > 100:
                logger.info(f"[CODE_GEN] Using pre-retrieved RAG examples ({len(retrieved_context)} chars). Skipping redundant retrieval.")
                rag_context_content = append_confirmed_info_context(retrieved_context)
            else:
                # Fallback: Retrieve if missing
                logger.info(f"[CODE_GEN] No pre-retrieved context found. Initiating RAG retrieval...")
                
                # RAG context retrieval for code generation (EXAMPLES + INFO via split context)
                @async_timer(f"rag_retrieval_code_gen_{session_id}")
                async def retrieve_rag_context_async():
                    from src.core.rag_singleton import get_rag_split_context
                    from src.utils.rag_context_logger import log_rag_context_retrieval, log_rag_context_summary
                    
                    # Extract USER-only turns for RAG query — chatbot responses are verbose
                    # markdown/HTML that pollutes semantic search. The full user_text is still
                    # passed to the LLM chain below for full context.
                    raw_query = user_text if user_text else design_requirements_obj.title
                    query_text = self._extract_user_only_query(raw_query)
                    query_preview = query_text[:80] + "..." if len(query_text) > 80 else query_text

                    logger.info(
                        f"[RAG] Code Gen (user-only, {len(query_text)} chars): \"{query_preview}\""
                    )
                    
                    # CRITICAL: Use get_rag_split_context to get examples + info (split from unified chain)
                    # This retrieves ONCE and returns examples_context which includes info.json
                    rag_result = await get_rag_split_context(
                        query_text, 
                        k_rules=10,  # For unified chain (not used here)
                        k_examples=7,  # For code generation
                        reranking_llm=self.reranking_llm,
                        session_id=session_id
                    )
                    
                    if rag_result['success']:
                        # Extract examples context (includes examples + info.json)
                        retrieved_context = rag_result['examples_context']
                        docs = rag_result.get('examples_documents', [])
                        retrieval_time = rag_result.get('retrieval_time', 0.0)
                        
                        # Count examples and info
                        info_docs = [d for d in docs if d.metadata.get('type') == 'info']
                        example_docs = [d for d in docs if d.metadata.get('type') != 'info']
                        
                        # Get info classes if any
                        info_classes = list(set(d.metadata.get('class', '') for d in info_docs if d.metadata.get('class')))
                        
                        # Format size
                        size_kb = len(retrieved_context) / 1024
                        
                        # Log concise summary to main log (detailed log already done by get_rag_split_context)
                        summary = log_rag_context_summary(
                            session_id=session_id,
                            query=query_text,
                            num_examples=len(example_docs),
                            num_info=len(info_docs),
                            total_size_kb=size_kb,
                            retrieval_time=retrieval_time,
                        )
                        logger.info(summary)
                        
                        return retrieved_context
                    else:
                        logger.error(f"[RAG] Retrieval failed: {rag_result.get('error')}")
                        return ""

                # Execute async retrieval
                rag_context_content = await retrieve_rag_context_async()
            
            # Async LLM processing with timing + retry on JSON/400 errors
            @async_timer(f"code_generation_session_{session_id}")
            async def generate_code_async():
                cost_tracker = self._get_cost_tracker(session_id)

                _MAX_RETRIES = 3
                _retry_delay = 2.0  # seconds, doubles each attempt (exponential backoff)
                _last_error: Exception = None

                # Keywords that indicate a retriable serialization / parsing failure
                _RETRIABLE_SIGNALS = (
                    "400",
                    "invalid_request_error",
                    "could not parse the json",
                    "not valid json",
                    "jsondecodeerror",
                    "outputparserexception",
                    "output parsing error",
                    "invalid json",
                    "failed to parse",
                )

                _chain_input = {
                    "design_requirements_obj": design_requirements_obj,
                    "raw_design_requirements_json": raw_design_requirements_json,
                    "retrieved_context": rag_context_content,  # Use retrieved context
                    "user_text": user_text,
                    "material": self._get_session_state(session_id).get('material_choice', ''),  # Pass material choice to template
                    "mapped_material": self._get_session_state(session_id).get('mapped_material', 'steel'),  # Pass mapped material
                    "session_id": session_id  # Pass session_id for logging
                }

                for _attempt in range(1, _MAX_RETRIES + 1):
                    try:
                        if _attempt > 1:
                            logger.warning(
                                f"[CODE_GEN_RETRY] Attempt {_attempt}/{_MAX_RETRIES} "
                                f"for session={session_id} (backoff={_retry_delay:.1f}s)"
                            )
                            await asyncio.sleep(_retry_delay)
                            _retry_delay *= 2  # exponential backoff

                        return await ainvoke_with_cost_tracking(
                            "code_generation",
                            self.rag_code_generation_chain.ainvoke,
                            _chain_input,
                            cost_tracker,
                            self.model_names['advanced']
                        )

                    except Exception as _err:
                        _last_error = _err
                        _err_str = str(_err).lower()

                        _is_retriable = any(sig in _err_str for sig in _RETRIABLE_SIGNALS)

                        if not _is_retriable:
                            # Non-retriable (auth failure, rate limit 429, etc.) — fail fast
                            logger.error(
                                f"[CODE_GEN_RETRY] Non-retriable error on attempt {_attempt}: {_err}"
                            )
                            raise

                        if _attempt >= _MAX_RETRIES:
                            logger.error(
                                f"[CODE_GEN_RETRY] All {_MAX_RETRIES} attempts exhausted "
                                f"for session={session_id}. Last error: {_err}"
                            )
                            raise

                        logger.warning(
                            f"[CODE_GEN_RETRY] Retriable error on attempt {_attempt}/{_MAX_RETRIES} "
                            f"for session={session_id}: {_err}"
                        )

                # Should never reach here, but satisfy type checker
                raise _last_error
            
            # Execute code generation with async optimization
            generated_code = await generate_code_async()
            print(f"[SUCCESS] FreeCAD code generation successful for session {session_id}.")

            # Async session state update
            async def update_session_async():
                loop = asyncio.get_event_loop()
                
                # CRITICAL: Preserve user_text_history when code is generated successfully
                # This allows edit mode to access full conversation context
                # Only reset history when starting a completely new request
                await loop.run_in_executor(None, lambda: self._update_session_state(
                    session_id,
                    latest_code=generated_code,
                    latest_title=design_requirements_obj.title,
                    pending_questions=[],
                    last_confirmed_description=design_requirements_obj.description,
                    edit_history_since_confirm=[],
                ))


            # CRITICAL FIX: Save latest_code to session BEFORE attempting file save
            # This ensures code is preserved even if FreeCAD execution fails
            await update_session_async()
            
            # Parallel execution of file saving (if needed)
            if save_files:
                try:
                    # Execute file saving
                    save_task = self.save_outputs(generated_code, design_requirements_obj, user_text=user_text, session_id=session_id)
                    obj_path, step_path, pdf_path = await save_task

                    return {
                        "code": generated_code,
                        "obj_path": obj_path,
                        "step_path": step_path,
                        "pdf_path": pdf_path,
                        "message": "Code generated successfully."
                    }
                except Exception as save_error:
                    # CRITICAL FIX: Return generated code for ALL save errors
                    # Code has already been saved to session state above
                    # Common errors: complex, server communication, file I/O, etc.
                    error_str = str(save_error)
                    
                    # Log the specific error type
                    error_keywords = ["Shape too complex", "Forme trop complexe", "Forma demasiado compleja", "Form zu komplex", "形状过于复杂", "形状が複雑すぎます", "모양이 너무 복잡합니다"]
                    if any(keyword in error_str for keyword in error_keywords):
                        print(f"[ERROR] FreeCAD execution failed (complex shape) for session {session_id}: {save_error}")
                    elif any(keyword in error_str.lower() for keyword in ['server', 'connection', 'network', 'communication']):
                        print(f"[ERROR] FreeCAD server communication failed for session {session_id}: {save_error}")
                    else:
                        print(f"[ERROR] FreeCAD export failed for session {session_id}: {save_error}")
                    
                    # Return code to user even though export failed
                    # This allows users to see and edit the generated code
                    return {"error": error_str, "code": generated_code}
            else:
                # Update session state and return code only
                await update_session_async()
                return {
                    "code": generated_code,
                    "message": "Code generated successfully."
                }

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            print(f"[ERROR] Error during code generation for session {session_id}: {e}\n{error_traceback}")
            return {"error": ERROR_CODES["105.1"], "code": None}
        finally:
            # Cost logging handled by process_request_with_progress
            pass

    async def process_edit_request(self, user_text, session_id=None, priority: int = 0):
        from src.core.async_optimizations import async_timer

        if session_id is None:
            print("ERROR: session_id is None in process_edit_request. Cannot proceed.")
            return {"error": "Session ID missing in process_edit_request", "code": None}

        # ── Load session state ─────────────────────────────────────────────────
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(None, self._get_session_state, session_id)

        if not state['latest_code']:
            print(f"[EDIT] No existing code to edit for session {session_id}.")
            return {"error": "No existing code to edit. Please generate code first.", "code": None}

        # ── Step 0: Intercept confirm replies ────────────────────────────────
        # When shape change was detected → _run_description_confirm was shown
        # → awaiting_confirm=True. The user's "oui" must go to the fast-path
        # gate (process_request_with_progress) which bypasses edit routing.
        # This branch only fires if the EDIT_GATE somehow did not catch it.
        if state.get('awaiting_confirm', False):
            logger.info(
                f"[EDIT] awaiting_confirm=True → intercepting confirm reply, "
                f"routing to _unified_request_processor | session={session_id}"
            )
            print(f"[EDIT] ✅ Confirm reply detected → routing to unified processor (not code editing)")
            return await self._unified_request_processor(
                user_text=user_text,
                session_id=session_id,
            )

        # ── Step 0b: Intercept edit-ack replies ────────────────────────────────
        # A previous turn found a DFM violation / missing_info on a non-shape-
        # change edit and asked the user "continue anyway?" (awaiting_edit_ack=True).
        if state.get('awaiting_edit_ack', False):
            intent = await self._detect_confirm_intent(user_text, session_id)
            if intent == "YES":
                staged_text = state.get('pending_edit_text', '') or user_text
                logger.info(f"[EDIT_ACK] ✅ YES → proceeding with staged edit | session={session_id}")
                self._update_session_state(session_id, awaiting_edit_ack=False, pending_edit_text='')
                face_data = None
                try:
                    face_data = await loop.run_in_executor(None, self.face_processor.parse_face_selection, staged_text)
                except Exception as e:
                    logger.warning(f"[EDIT_ACK] Face processing failed: {e}")
                return await self._execute_code_edit(staged_text, staged_text, face_data, state, session_id, priority)
            else:
                logger.info(f"[EDIT_ACK] 🔄 Non-YES reply → treating as a new edit request | session={session_id}")
                self._update_session_state(session_id, awaiting_edit_ack=False, pending_edit_text='')
                # Fall through — user_text below is processed as a brand-new edit request.

        # ── Step 1: Face processing on raw edit prompt ─────────────────────────
        enhanced_user_text = user_text
        face_data = None
        try:
            enhanced_user_text = await loop.run_in_executor(None, self.face_processor.enhance_user_request, user_text)
            face_data = await loop.run_in_executor(None, self.face_processor.parse_face_selection, user_text)
            if face_data:
                print(f"[FACE_PROCESSING] Detected: {face_data['face_id']} ({face_data['shape_type']})")
            else:
                enhanced_user_text = user_text
                print(f"[FACE_PROCESSING] No face selection detected, using original request")
        except Exception as e:
            enhanced_user_text = user_text
            logger.warning(f"[EDIT] Face processing failed: {e}")

        print(f"\n[EDIT] Processing edit request for session {session_id}: '{user_text[:80]}'...")

        # ── Step 2: Fold the edit into the running summary + classify the message,
        #            then (only for real edits) run shape_change + unified + DFM ──
        # Rationale: edit mode used to only run shape_change_detector, so an edit
        # that keeps the same shape (e.g. "add a hole here") never went through
        # unified_processing_chain / dfm_validation_chain — meaning missing-info
        # and manufacturing-rule violations were silently ignored during edits.
        # Now every edit turn is checked by all three, using edit_running_summary
        # (a net-effect description folded turn-by-turn by edit_summary_chain,
        # NOT a raw replay of edit_history_since_confirm) as the shared context —
        # avoids the LLM having to re-derive final state from a contradictory
        # transcript of add/move/delete operations.
        #
        # The intent classifier runs in phase 1, concurrently with edit_summary
        # rather than inside phase 2's gather. It is a nano call that finishes well
        # inside edit_summary's latency, so a genuine edit pays nothing for the
        # earlier placement — but a non-CAD message ("combien ça coûte ?", "merci")
        # now short-circuits at Step 2b BEFORE phase 2 spends a rules retrieval
        # (with its rerank call), a shape-change detection and two expert-tier
        # calls analyzing a design nobody asked to change.
        shape_detection = {"shape_change": False, "current_shape_type": "unknown", "reason": ""}
        unified_output_obj = None
        dfm_result = None
        # Fail-open default: if the classifier errors/times out below, this stays
        # "cad_request" so behavior is unchanged from before this gate existed.
        intent_result = {"classification": "cad_request", "confidence": 0.0}
        updated_summary = state.get('edit_running_summary') or state.get('last_confirmed_description', '')
        # Bound outside the try blocks below: phase 2 needs it even if phase 1 raised.
        cost_tracker = self._get_cost_tracker(session_id)

        try:
            if not state.get('last_confirmed_description'):
                await self._recover_session_state_from_db(session_id)
                state = self._get_session_state(session_id)
                updated_summary = state.get('edit_running_summary') or state.get('last_confirmed_description', '')

            # ── Last-resort bootstrap ────────────────────────────────────────
            # DB recovery above depends on ChatHistory rows carrying a parseable
            # confirm description + a populated `lasted_code` column. When that
            # fails (e.g. cold session state after a restart, code synced from
            # DB by a different path — see _get_latest_code call sites), we'd
            # otherwise feed shape_change/unified/DFM an EMPTY baseline, so
            # edit_summary_chain has nothing to ground dimensions on and every
            # downstream check silently misses real violations. Fall back to
            # the actual generated code, which is always available in edit mode
            # (checked at the top of this function) — LLMs can read real
            # dimensions out of the FreeCAD script directly.
            if not updated_summary and state.get('latest_code'):
                logger.warning(
                    f"[EDIT] No baseline description recoverable — bootstrapping from "
                    f"latest_code instead | session={session_id}"
                )
                updated_summary = (
                    "[No stored description available — inferring current state from "
                    "the existing generated code below]\n```python\n"
                    f"{state['latest_code'][:6000]}\n```"
                )

            updated_summary, intent_result = await asyncio.wait_for(
                asyncio.gather(
                    ainvoke_with_cost_tracking(
                        "edit_summary",
                        self.edit_summary_chain.ainvoke,
                        {
                            "previous_description": updated_summary,
                            "new_edit_request":     enhanced_user_text,
                            "session_id":           session_id,
                        },
                        cost_tracker,
                        self.model_names['default']
                    ),
                    # Classifies the raw new message only (NOT edit_mode_user_text /
                    # updated_summary) since we want to know what THIS message is
                    # about, not the whole design.
                    ainvoke_with_cost_tracking(
                        "greeting_classification_edit",
                        self.greeting_classification_chain.ainvoke,
                        {"user_text": enhanced_user_text},
                        cost_tracker,
                        self.model_names['greeting']
                    ),
                ),
                timeout=50.0
            )

        except Exception as phase1_err:
            logger.warning(
                f"[EDIT] edit summary / intent classification failed, continuing with normal edit: {phase1_err}"
            )

        # ── Step 2b: non-CAD intent (pricing/pdf/greeting) → answer directly ─────
        # Fail-open: only short-circuits on a confident, explicit non-CAD read.
        # Anything else (including classifier errors, which default to
        # "cad_request" above) falls through to the normal edit flow unchanged.
        intent_classification = intent_result.get('classification')
        intent_confidence = intent_result.get('confidence', 0)
        if intent_classification == 'process_question' and intent_confidence > 0.7:
            sub_type = intent_result.get('sub_type')
            logger.info(
                f"[EDIT] 💬 process_question detected (sub_type={sub_type}, "
                f"confidence={intent_confidence}) → answering directly, skipping code edit "
                f"| session={session_id}"
            )
            # Deliberately do NOT persist `updated_summary` here: it was produced by
            # edit_summary_chain treating this message as an edit delta, which is
            # wrong for a non-edit message — saving it could corrupt the running
            # design summary (e.g. misfolding "chiffre-moi *4" in as a real change).
            return {
                "code": None,
                "message": get_process_question_response(sub_type),
                "explanation": f"Process question response during edit mode (sub_type={sub_type})",
            }
        if intent_classification == 'greeting' and intent_confidence > 0.8 and intent_result.get('response'):
            logger.info(f"[EDIT] 💬 greeting detected mid-conversation → answering directly | session={session_id}")
            return {
                "code": None,
                "message": intent_result['response'],
                "explanation": "Greeting response during edit mode",
            }

        # NOTE: a MIXED message (real CAD edit + a pricing/file ask riding along,
        # e.g. "add a 5mm hole and also tell me the price") stays classified
        # cad_request above and falls through to the normal edit flow below.
        # The pricing/file ask is intentionally NOT answered here — only the CAD
        # edit itself is processed, matching how a plain edit request behaves.

        # ── Step 2c: confirmed CAD edit → shape_change + unified + DFM in parallel ──
        try:
            rules_context = await self._retrieve_edit_rules_context(enhanced_user_text, session_id)

            # Note for unified/DFM prompts: this is edit mode — updated_summary IS
            # the current confirmed state (already includes all prior edits), the
            # user_request below is only the NEW delta to analyze on top of it.
            edit_mode_user_text = (
                f"[EDIT MODE] Current confirmed design (net state after all prior edits):\n"
                f"{updated_summary}\n\n"
                f"[USER] {enhanced_user_text}"
            )
            unified_chain_input = {
                "user_text":         edit_mode_user_text,
                "rules_context":     "",
                "examples_context":  "",
                "session_id":        session_id,
                "material":          state.get('material_choice', ''),
                "mapped_material":   state.get('mapped_material', 'steel'),
                "user_language":     state.get('user_language', 'French'),
            }
            dfm_chain_input = {
                "user_text":         edit_mode_user_text,
                "retrieved_context": rules_context,
                "material":          state.get('material_choice', ''),
                "session_id":        session_id,
                "user_language":     state.get('user_language', 'French'),
            }

            shape_detection, unified_result, dfm_result = await asyncio.wait_for(
                asyncio.gather(
                    self._detect_shape_change(enhanced_user_text, updated_summary, session_id),
                    ainvoke_with_cost_tracking(
                        "unified_processing_edit",
                        self.unified_processing_chain.ainvoke,
                        unified_chain_input,
                        cost_tracker,
                        self.model_names['expert']
                    ),
                    ainvoke_with_cost_tracking(
                        "dfm_validation_edit",
                        self.dfm_validation_chain.ainvoke,
                        dfm_chain_input,
                        cost_tracker,
                        self.model_names['expert']
                    ),
                ),
                timeout=50.0
            )
            unified_output_obj = unified_result.get("unified_output_obj") if isinstance(unified_result, dict) else None

        except Exception as parallel_err:
            logger.warning(
                f"[EDIT] shape_change/unified/DFM check failed, continuing with normal edit: {parallel_err}"
            )

        # ── Step 3: shape_change=True → route to full description-confirm ──────
        if shape_detection.get('shape_change', False):
            new_shape = shape_detection.get('new_shape_type', 'unknown')
            merged_desc = shape_detection.get('merged_description', '')
            reason = shape_detection.get('reason', '')
            logger.info(
                f"[EDIT] 🔄 SHAPE CHANGE detected: {shape_detection.get('current_shape_type')} → {new_shape} "
                f"| reason={reason} | session={session_id}"
            )
            print(f"[EDIT] 🔄 Shape change detected → re-routing to full generation flow")
            print(f"[EDIT]    Current: {shape_detection.get('current_shape_type')} → New: {new_shape}")
            print(f"[EDIT]    Merged description ({len(merged_desc)} chars): {merged_desc[:200]}...")

            # Fold in any DFM/unifier warnings so the user only has to answer
            # ONE confirm round covering both the shape change and the warnings.
            combined_questions = []
            if unified_output_obj is not None:
                combined_questions += list(getattr(unified_output_obj, 'questions', []) or [])
            if dfm_result is not None and getattr(dfm_result, 'has_violations', False) and not getattr(dfm_result, 'override_intent_detected', False):
                combined_questions += list(dfm_result.violations)
                if dfm_result.thickness_warning:
                    combined_questions.append(dfm_result.thickness_warning)

            # ── Shape change: run description_confirm chain then wait for YES ──
            # Build a minimal req_obj from merged_desc, save to confirm cache,
            # then run _run_description_confirm() exactly like the normal flow does.
            # On the next turn, EDIT_GATE sees awaiting_confirm=True → bypasses
            # edit routing → fast-path gate handles YES → generate_code_from_requirements.
            logger.info(
                f"[EDIT] ⏸ SHAPE CHANGE → running description_confirm | "
                f"shape={new_shape} | session={session_id}"
            )
            print(f"[EDIT] ⏸ Shape change → running description_confirm chain")

            shape_change_req_obj = AnalysisAndParameterCheckOutput(
                title=new_shape,
                description=merged_desc,
                complexity_level=2,
                missing_info=bool(combined_questions),
                questions=combined_questions,
                shape_type=new_shape,
            )

            # Prime session state for fresh confirm round.
            # NOTE: confirmed_description is NOT pre-set — it will be written
            # by _run_description_confirm from the chain's final_description output.
            self._update_session_state(session_id,
                awaiting_confirm=False,   # _run_description_confirm sets True
                confirm_count=0,
                confirmed_description='',
            )

            # Save cache so fast-path gate can reconstruct req_obj on YES
            self._save_confirm_cache(
                session_id,
                shape_change_req_obj,
                merged_desc,          # raw_unified_json substitute
                "",                   # retrieved_context — fetched fresh in code gen
                merged_desc,          # expanded_user_text
            )

            # Pass merged_desc as user_text_with_history to description_confirm chain.
            # merged_desc already synthesizes ALL context: original shape dimensions,
            # existing features (cutouts, holes…) AND the new edit request —
            # so the chain can extract every parameter without '?' placeholders.
            # (Passing only user_text = latest message loses the original numbers.)
            return await self._run_description_confirm(
                shape_change_req_obj, merged_desc, session_id
            )

        # ── Step 4: shape_change=False — persist the running summary regardless ──
        logger.info(
            f"[EDIT] ✅ No shape change detected | reason={shape_detection.get('reason', '')[:80]} | "
            f"session={session_id}"
        )
        self._update_session_state(session_id, edit_running_summary=updated_summary)

        # ── Step 4b: Perforated Sheet — unrecognized named pattern guard ────────
        # A user may ask for a named/branded hole pattern (e.g. "motif AUBE de
        # chez ACIANOV CREATION") that has no equivalent in our supported
        # notation (R/C/LR/LC + T/U/Z — see data/Info/Perforated_Sheet/info.json).
        # Nothing downstream understands "AUBE"/"ACIANOV", so code_editing_chain
        # would silently leave the code unchanged (or hallucinate something) and
        # still report success. Catch it here before it reaches code editing.
        # Scope: only fires when (a) current shape is Perforated Sheet, (b) the
        # message names a pattern/model ("motif", "modèle", "pattern"...), AND
        # (c) it contains NONE of our supported notation tokens — a genuine
        # edit like "increase thickness to 3mm" or "change to R12 T20" never
        # matches both conditions, so it falls through unaffected.
        _summary_for_shape_check = state.get('edit_running_summary') or state.get('last_confirmed_description', '')
        _shape_match_pattern = re.search(r'Type:\s*([A-Za-z0-9_\- ]+)', _summary_for_shape_check)
        _current_shape_for_pattern_check = (_shape_match_pattern.group(1).strip() if _shape_match_pattern else '').lower()
        if 'perforated' in _current_shape_for_pattern_check:
            _pattern_kw = re.search(r'\b(motif|mod[eè]le|pattern|dessin|d[ée]cor)\b', enhanced_user_text, re.IGNORECASE)
            _notation_kw = re.search(
                r'\b(R\d|C\d|LR\d|LC\d|T\d|U\d|Z\d|%|vide|rond|circulaire|carr[ée]|oblong|quinconce|'
                r'triangulaire|align[ée]|grille|square|round|staggered|inline)\b',
                enhanced_user_text, re.IGNORECASE
            )
            if _pattern_kw and not _notation_kw:
                from src.utils.language_utils import get_session_language, get_perforated_unknown_pattern_message
                _lang_code = get_session_language(session_id)
                logger.info(
                    f"[EDIT] 🚫 Unrecognized named perforation pattern reference (no supported "
                    f"notation found) → answering directly, skipping code edit | session={session_id}"
                )
                return {
                    "code": None,
                    "message": get_perforated_unknown_pattern_message(_lang_code),
                    "explanation": "Unrecognized perforation pattern name — no matching notation",
                }

        # ── Step 5: shape_change=False but unifier/DFM raised a concern ─────────
        # Warn the user and wait for an explicit ack before touching the code —
        # this is the gap the old edit flow had (it never ran unifier/DFM at all).
        has_missing_info = bool(unified_output_obj is not None and getattr(unified_output_obj, 'missing_info', False))
        has_dfm_violation = bool(
            dfm_result is not None and getattr(dfm_result, 'has_violations', False)
            and not getattr(dfm_result, 'override_intent_detected', False)
        )

        if has_missing_info or has_dfm_violation:
            warning_questions = []
            if dfm_result is not None and dfm_result.has_violations:
                warning_questions += list(dfm_result.violations)
                if dfm_result.thickness_warning:
                    warning_questions.append(dfm_result.thickness_warning)
            if unified_output_obj is not None:
                warning_questions += list(getattr(unified_output_obj, 'questions', []) or [])

            warning_text = "\n".join(f"- {q}" for q in warning_questions) or \
                "Cette modification peut manquer d'informations ou enfreindre les règles de fabrication."

            logger.info(
                f"[EDIT] ⚠️ DFM/unifier warning on non-shape-change edit → awaiting ack "
                f"| missing_info={has_missing_info} | dfm_violation={has_dfm_violation} | session={session_id}"
            )
            self._update_session_state(session_id, awaiting_edit_ack=True, pending_edit_text=enhanced_user_text)
            # NOTE: deliberately do NOT include a "code" key here. _process_agent_result()
            # (src/crud/chat_processing.py) treats any non-None "code" as "generation
            # succeeded" and overwrites this message with a generic success string, and
            # _handle_export_paths() then treats it as "code_generated=True" and searches
            # for a recent (stale, unrelated) obj/step file to attach — silently masking
            # this warning entirely. No code was produced this turn, so "code" must be absent.
            return {
                "warning": True,
                "message": (
                    f"⚠️ Merci de vérifier avant de continuer :\n{warning_text}\n\n"
                    f"Répondez OK pour continuer quand même, ou précisez/ajustez votre demande."
                ),
                "questions": warning_questions,
            }

        # ── Step 6: Clean edit — run code editing chain as before ───────────────
        return await self._execute_code_edit(enhanced_user_text, user_text, face_data, state, session_id, priority)

    async def _execute_code_edit(self, enhanced_user_text, user_text, face_data, state, session_id, priority: int = 0):
        """
        Run the code_editing_chain against the existing code and save outputs.

        Extracted from process_edit_request so both the "clean edit" path and
        the "user acked a DFM/unifier warning" path (awaiting_edit_ack=True →
        YES) can call the same execution logic without repeating shape/unified/
        DFM validation a second time.
        """
        from src.core.async_optimizations import async_timer

        loop = asyncio.get_event_loop()

        # Safe defaults — overwritten inside the try block below.
        rag_query         = enhanced_user_text
        post_codegen_text = enhanced_user_text
        retrieved_context  = ""

        try:
            # ── RAG with user-only history as query ──────────────────────────
            # Build full history then extract ONLY [USER] turns.
            # [CHATBOT] responses (markdown, HTML spans, verbose descriptions)
            # degrade semantic search — we only want the user's intent signal.
            full_history      = self._build_user_text_with_history(session_id, user_text)
            rag_query         = self._extract_user_only_query(full_history)

            # Inject current shape type into RAG query to improve retrieval accuracy.
            # Derived from edit_running_summary's "Type: X" header rather than the
            # shape_change_detector result, so this also works from the ack (YES) path.
            current_shape = 'unknown'
            summary_text = state.get('edit_running_summary', '') or state.get('last_confirmed_description', '')
            shape_match = re.search(r'Type:\s*([A-Za-z0-9\-]+)', summary_text)
            if shape_match:
                current_shape = shape_match.group(1)

            if current_shape != 'unknown':
                rag_query = f"Shape type: {current_shape}\n{rag_query}"

            post_codegen_text = self._build_post_codegen_history(session_id, user_text)
            logger.info(
                f"[EDIT_RAG] history built: full={len(full_history)} chars | "
                f"rag_query={len(rag_query)} chars | "
                f"post_codegen={len(post_codegen_text)} chars | session={session_id}"
            )
            retrieved_context = await self._retrieve_edit_context(rag_query, session_id)
            print(f"[SUCCESS] Context retrieval successful for edit session {session_id}.")

            # ── Add face selection code if detected ────────────────────────────
            if face_data:
                face_selection_code = await loop.run_in_executor(
                    None, self.face_processor.generate_face_selection_code, face_data
                )
                if face_selection_code:
                    print(f"[FACE_PROCESSING] Generated face selection code ({len(face_selection_code)} chars)")
                    retrieved_context = f"{retrieved_context}\n\nFACE SELECTION CODE:\n{face_selection_code}"

        except Exception as e:
            print(f"[ERROR] Error during context retrieval: {e}")
            retrieved_context = ""

        try:
            print(f"\nEditing FreeCAD code for session {session_id}...")

            sanitized_title = self._sanitize_title(state)
            print(f"Using sanitized_title: '{sanitized_title}' for code editing in session {session_id}")

            # ── Build effective user_request for the LLM ──────────────────────
            # Use post_codegen_text so the LLM only sees messages sent AFTER the
            # last successful code generation.  This prevents it from reading the
            # original shape-creation request and falsely concluding that the edit
            # (e.g. "shift notch 50mm") was already applied in the existing code.
            # Fall back to enhanced_user_text if post_codegen_text is empty.
            effective_user_request = (
                post_codegen_text.strip()
                if post_codegen_text and post_codegen_text.strip()
                else (rag_query if rag_query and rag_query.strip() else enhanced_user_text)
            )

            print(f"🔍 [EDIT MODE INPUT] Session: {session_id}")
            print(f"   - User Request (latest):   {enhanced_user_text[:200]}")
            print(f"   - User Request (effective): {effective_user_request[:200]}")
            print(f"   - Original Code Len:  {len(state['latest_code'])} chars")
            print(f"   - Context Len:        {len(retrieved_context)} chars")
            print(f"   - Sanitized Title:    {sanitized_title}")

            cost_tracker = self._get_cost_tracker(session_id)
            edited_code = await ainvoke_with_cost_tracking(
                "code_editing",
                self.code_editing_chain.ainvoke,
                {
                    "original_code":     state['latest_code'],
                    "user_request":      effective_user_request,
                    "retrieved_context": retrieved_context,
                    "sanitized_title":   sanitized_title,
                    "mapped_material":   state.get('mapped_material', 'steel'),
                    "session_id":        session_id,
                },
                cost_tracker,
                self.model_names['expert']
            )
            print(f"[SUCCESS] FreeCAD code editing successful for session {session_id}.")

            # CRITICAL FIX: Save latest_code to session BEFORE attempting file save
            # Async session state update
            @async_timer(f"session_state_update_edit_{session_id}")
            async def update_session_state_async():
                loop = asyncio.get_event_loop()
                state_fresh = self._get_session_state(session_id)
                current_history = list(state_fresh.get('edit_history_since_confirm', []))
                if user_text and user_text.strip():
                    current_history.append(user_text.strip())

                await loop.run_in_executor(
                    None,
                    lambda: self._update_session_state(
                        session_id,
                        latest_code=edited_code,
                        pending_questions=[],
                        edit_history_since_confirm=current_history,
                    )
                )

            await update_session_state_async()

        except Exception as e:
            print(f"[ERROR] Error during code editing for session {session_id}: {e}")
            return {"error": f"Error editing code: {e}", "code": None}

        # ── Save files and return ───────────────────────────────────────────────
        current_requirements = self._prepare_current_requirements(state)
        user_text_for_save   = self._extract_user_text_from_state(state, session_id)

        try:
            obj_path, step_path, pdf_path = await self.save_outputs(
                edited_code, current_requirements,
                base_filename=sanitized_title, user_text=user_text_for_save, session_id=session_id,
                priority=priority
            )
            print(f"[SUCCESS] Code updated successfully for session {session_id}.")
            return {
                "code": edited_code,
                "obj_path": obj_path,
                "step_path": step_path,
                "pdf_path": pdf_path,
            }
        except Exception as save_error:
            error_str = str(save_error)
            if any(k in error_str.lower() for k in ['server', 'connection', 'network', 'communication']):
                print(f"[ERROR] FreeCAD server communication failed for edit session {session_id}: {save_error}")
            else:
                print(f"[ERROR] FreeCAD export failed for edit session {session_id}: {save_error}")
            return {"error": error_str, "code": edited_code}

    async def _retrieve_edit_context(self, rag_query: str, session_id: str) -> str:
        """
        RAG retrieval for edit mode — EXAMPLES ONLY.
        Edit chain already has `original_code` as its primary context, so it only
        needs code-pattern examples to guide the edit.
        Rules and info are NOT needed (and add noise to the editing prompt).

        Uses get_rag_split_context directly so the correct session_id appears in
        rag_context.log (not 'legacy_context_request').
        Returns empty string on failure — edit chain can still run without context.
        """
        from src.core.rag_singleton import get_rag_split_context
        try:
            result = await get_rag_split_context(
                query=rag_query,
                k_rules=0,          # ← no rules for edit mode
                k_examples=5,
                reranking_llm=self.reranking_llm,
                cost_tracker=self._get_cost_tracker(session_id),
                session_id=session_id
            )
            if result['success']:
                # Examples only — rules_context intentionally excluded
                examples_context = result.get('examples_context', '').strip()
                logger.info(
                    f"[EDIT_RAG] Retrieved examples-only context: "
                    f"{len(examples_context)} chars | session={session_id}"
                )
                return examples_context
            logger.error(f"[EDIT_RAG] Retrieval failed session={session_id}: {result.get('error')}")
        except Exception as e:
            logger.error(f"[EDIT_RAG] Exception session={session_id}: {e}")
        return ""

    async def _retrieve_edit_rules_context(self, rag_query: str, session_id: str) -> str:
        """
        RAG retrieval for edit mode — RULES ONLY, used by the DFM validation
        chain when it now runs on every edit turn (see process_edit_request).
        Mirrors _retrieve_edit_context() but fetches rules instead of examples.
        Returns empty string on failure — DFM chain treats that as no extra rules.
        """
        from src.core.rag_singleton import get_rag_split_context
        try:
            result = await get_rag_split_context(
                query=rag_query,
                k_rules=10,
                # NOTE: k_examples=0 crashes FAISS (`assert k > 0` in vector_store.search_index)
                # — the examples retrieval path always does a vector search regardless of k,
                # unlike the rules path which is metadata/class-based and tolerates k=0.
                # Use the minimum nonzero value and simply discard examples_context below.
                k_examples=1,
                reranking_llm=self.reranking_llm,
                cost_tracker=self._get_cost_tracker(session_id),
                session_id=session_id
            )
            if result['success']:
                rules_context = result.get('rules_context', '').strip()
                logger.info(
                    f"[EDIT_RAG] Retrieved rules-only context for DFM: "
                    f"{len(rules_context)} chars | session={session_id}"
                )
                return rules_context
            logger.error(f"[EDIT_RAG] Rules retrieval failed session={session_id}: {result.get('error')}")
        except Exception as e:
            logger.error(f"[EDIT_RAG] Rules retrieval exception session={session_id}: {e}")
        return ""

    async def _detect_shape_change(self, user_request: str, current_description: str, session_id: str) -> dict:
        """Detect whether an edit request changes the fundamental shape type.

        Uses a lightweight LLM classifier (default_llm) to determine if the edit
        involves a structural shape change (e.g., bending a flat plate into a bracket)
        vs. a normal feature edit (add holes, resize, etc.).

        Returns:
            dict with keys: shape_change (bool), current_shape_type, new_shape_type,
                           merged_description, reason
        """
        from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

        cost_tracker = self._get_cost_tracker(session_id)

        result = await ainvoke_with_cost_tracking(
            "shape_change_detection",
            self.shape_change_detector_chain.ainvoke,
            {
                "user_request":        user_request,
                "current_description": current_description,
                "session_id":          session_id,
            },
            cost_tracker,
            self.model_names['default']
        )
        return result

    
    def _sanitize_title(self, state):
        """Helper method to sanitize title synchronously"""
        sanitized_title = ""
        if state['latest_title']:
            sanitized_title = re.sub(r'[^\w\s-]', '', state['latest_title']).strip()
            sanitized_title = ''.join(c for c in sanitized_title if ord(c) < 128).replace(' ', '_')

        if not sanitized_title and state['latest_requirements'] and state['latest_requirements'].title:
            sanitized_title = re.sub(r'[^\w\s-]', '', state['latest_requirements'].title).strip()
            sanitized_title = ''.join(c for c in sanitized_title if ord(c) < 128).replace(' ', '_')

        if not sanitized_title:
            sanitized_title = "edited_model"
        
        return sanitized_title
    
    def _prepare_current_requirements(self, state):
        """Helper method to prepare current requirements synchronously"""
        return state['latest_requirements'] if state['latest_requirements'] else DesignRequirements(
            title=state['latest_title'] or "Edited_Model",
            shapes=[ShapeRequirement(shape_type="unknown", dimensions={"unknown": 0.0})],
            complexity_level=1
        )
    






    def _extract_override_parameter(self, user_text):
        """Extract parameter from override intent (e.g., 'continue use M1.2' -> 'M1.2')"""
        import re

        # Common patterns for parameter extraction
        patterns = [
            r'continue use\s+([A-Za-z0-9.]+)',      # "continue use M1.2"
            r'proceed with\s+([A-Za-z0-9.]+)',      # "proceed with M1.2"
            r'proceed anyway.*?([A-Za-z0-9.]+)',    # "proceed anyway with M1.2"
            r'go with\s+([A-Za-z0-9.]+)',           # "go with M1.2"
            r'keep\s+([A-Za-z0-9.]+)',              # "keep M1.2"
            r'use\s+([A-Za-z0-9.]+)\s+anyway',      # "use M1.2 anyway"
            r'anyway.*?([A-Za-z0-9.]+)',            # "anyway use M1.2"
            r'ignore warning.*?([A-Za-z0-9.]+)',    # "ignore warning and use M1.2"
            r'tiếp tục dùng\s+([A-Za-z0-9.]+)',     # Vietnamese
        ]

        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    async def _execute_freecad_remote(self, code_filepath, session_id, threaded_metadata_path=None, priority: int = 0, shape_type=None):
        """Execute a FreeCAD script on the remote server via MQTT workflow.
        
        Encapsulates the 4-phase MQTT-based async workflow:
          Phase 1: Submit job with file + user_id + metadata
          Phase 2: Verify job was created successfully
          Phase 3: Listen to MQTT for completion
          Phase 4: Download result files
        
        Args:
            code_filepath: Path to the saved Python script file
            session_id: Session ID (used as user_id for FreeCAD server)
            threaded_metadata_path: Optional path to threaded holes metadata
            priority: FreeCAD queue priority from 0 to 100
            
        Returns:
            Tuple of (result_dict, downloaded_files_dict)
            
        Raises:
            Exception with appropriate ERROR_CODES on failure
        """
        from src.core.freecad_remote_client import (
            FreeCADConnectionError,
            FreeCADTimeoutError,
            FreeCADServerNotAvailableError,
            FreeCADProcessingError,
            create_freecad_client
        )
        import datetime
        import asyncio

        current_user_id = "unknown"
        # Perforated Sheet jobs intentionally skip OBJ generation (see templates.py
        # MANDATORY OBJ/STEP EXPORT section) -- every other shape still generates
        # OBJ and must keep the hard requirement, so a real generation bug for a
        # non-perforated shape still fails loudly instead of succeeding silently.
        expect_obj = (shape_type or "").strip().lower() != "perforated sheet"
        try:
            async with create_freecad_client(use_async=True) as client:
                # Check server health before proceeding
                if not await client.check_server_health():
                    logger.error(ERROR_CODES["102.1"])
                    print(f"[ERROR] {ERROR_CODES['102.1']}")
                    raise Exception(ERROR_CODES["102.1"])

                # Use session_id as user_id, or generate fallback
                if session_id:
                    user_id = session_id
                else:
                    user_id = f"user_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

                current_user_id = user_id

                # ============================================================
                # PHASE 1: SUBMIT JOB TO SERVER
                # ============================================================
                filename = os.path.basename(code_filepath)
                file_size = os.path.getsize(code_filepath)
                
                logger.debug(
                    f"[FreeCAD] Submitting | user_id={user_id} | file={filename} | size={file_size}B | "
                    f"metadata={'yes' if threaded_metadata_path else 'no'} | priority={priority}"
                )

                submit_result = await client.generate(
                    script_path=code_filepath,
                    user_id=user_id,
                    auto_download=False,
                    metadata_path=str(threaded_metadata_path) if threaded_metadata_path else None,
                    priority=priority
                )

                # ============================================================
                # VERIFY SUBMISSION RESPONSE
                # ============================================================
                logger.debug(f"[FreeCAD] Response received | user_id={user_id} | status={submit_result.get('status')}")

                if not submit_result:
                    logger.error(f"[FreeCAD] ❌ CHECK 1 FAILED: {ERROR_CODES['102.2']}")
                    raise Exception(ERROR_CODES["102.2"])

                if not submit_result.get('user_id'):
                    logger.error(f"[FreeCAD] ❌ CHECK 3 FAILED: No user_id returned. Response: {submit_result}")
                    raise Exception(ERROR_CODES["102.3"])

                if submit_result.get('user_id') != user_id:
                    logger.error(f"[FreeCAD] ❌ CHECK 4 FAILED: user_id mismatch. Expected: {user_id}, Got: {submit_result.get('user_id')}")
                    raise Exception(ERROR_CODES["102.4"])

                response_status = submit_result.get('status')
                if response_status != 'queued':
                    logger.error(f"[FreeCAD] ❌ CHECK 5 FAILED: Invalid status. Expected: 'queued', Got: '{response_status}'. Response: {submit_result}")
                    raise Exception(ERROR_CODES["102.5"])

                mqtt_published = submit_result.get('mqtt_published', False)
                if not mqtt_published:
                    logger.error(f"[FreeCAD] ❌ CHECK 6 FAILED: MQTT not published. Response: {submit_result}")
                    raise Exception(ERROR_CODES["102.6"])

                logger.info(f"[FreeCAD] Phase 1 complete | user_id={user_id} | status={response_status} | mqtt={mqtt_published}")

                # ============================================================
                # PHASE 2: VERIFY JOB EXISTS ON SERVER
                # ============================================================
                logger.info(f"[FreeCAD] Phase 2: Verifying | user_id={user_id}")
                await asyncio.sleep(0.5)

                try:
                    verification_status = await client.get_execution_status_async(user_id)

                    if not verification_status:
                        logger.error(f"[FreeCAD] ❌ CHECK 5 FAILED: Server returned empty status for user_id={user_id}")
                        raise Exception(ERROR_CODES["102.7"])

                    server_user_id = verification_status.get('user_id')
                    server_status = verification_status.get('status')

                    if server_user_id != user_id:
                        logger.error(f"[FreeCAD] ❌ CHECK 6 FAILED: user_id mismatch! Expected: {user_id}, Server returned: {server_user_id}")
                        raise Exception(ERROR_CODES["102.7"])

                    logger.info(f"[FreeCAD] Phase 2 complete | user_id={user_id} | status={server_status}")

                except FreeCADProcessingError as verify_error:
                    if "404" in str(verify_error) or "not found" in str(verify_error).lower():
                        logger.error(f"[FreeCAD] ❌ CRITICAL: Job NOT FOUND on server. Error: {verify_error}")
                        raise Exception(ERROR_CODES["102.7"])
                    else:
                        logger.warning(f"[FreeCAD] ⚠️ Job verification warning (continuing): {verify_error}")
                except Exception as verify_error:
                    logger.error(f"[FreeCAD] ❌ Job verification failed with unexpected error: {verify_error}")
                    logger.warning(f"[FreeCAD] ⚠️ Continuing despite verification error - job may still be processing")

                # ============================================================
                # PHASE 3: LISTEN TO MQTT FOR COMPLETION (USER_ID ONLY)
                # ============================================================
                logger.info(f"[FreeCAD] Phase 3: Listening MQTT | user_id={user_id}")

                async def progress_callback(status_dict):
                    """Callback function to handle MQTT progress updates"""
                    progress = status_dict.get('progress', 0)
                    current_process = status_dict.get('current_process', 'Processing...')
                    logger.info(f"[FreeCAD] Progress: {progress}% - {current_process}")

                monitoring_result = await client.wait_for_mqtt_completion_async(
                    user_id=user_id,
                    max_duration=None,
                    progress_callback=progress_callback
                )

                # ============================================================
                # CHECK MQTT LISTENING RESULT
                # ============================================================
                if not monitoring_result.get('success'):
                    raw_error = monitoring_result.get('error', 'Unknown error during execution')
                    logger.error(
                        f"[FreeCAD] ❌ PHASE 3 FAILED: Job failed | "
                        f"user_id={user_id} | "
                        f"raw_error={raw_error}"
                    )

                    if isinstance(raw_error, str):
                        if raw_error.startswith('TIMEOUT:') or 'timed out' in raw_error.lower():
                            logger.error(f"[FreeCAD] ⏱️ TIMEOUT | user_id={user_id} | detail={raw_error}")
                            raise Exception(ERROR_CODES["101.1"])
                        if raw_error.startswith('PARTIAL_SUCCESS:') or 'partial_success' in raw_error.lower():
                            logger.error(f"[FreeCAD] ⚠️ PARTIAL SUCCESS | user_id={user_id} | detail={raw_error}")
                            raise Exception(ERROR_CODES["104.6"])

                    raise Exception(ERROR_CODES["104.1"])

                total_time = monitoring_result.get('total_time', 0)
                logger.info(f"[FreeCAD] Phase 3 complete | user_id={user_id} | time={total_time:.2f}s")

                # ============================================================
                # PHASE 4: DOWNLOAD RESULT FILES FROM SERVER (WITH RETRY)
                # ============================================================
                logger.info(f"[FreeCAD] Phase 4: Downloading | user_id={user_id}")

                initial_delay = 3
                logger.debug(f"[FreeCAD] Waiting {initial_delay}s for server | user_id={user_id}")
                await asyncio.sleep(initial_delay)

                completion_ts = time.time()

                max_download_retries = 3
                base_retry_delay = 3
                result = None
                last_error = None

                for attempt in range(1, max_download_retries + 1):
                    try:
                        logger.info(f"[FreeCAD] Download attempt {attempt}/{max_download_retries} | user_id={user_id}")
                        result = await client.get_job_result_async(user_id, auto_download=True, expect_obj=expect_obj)
                        logger.info(f"[FreeCAD] Download success | user_id={user_id} | attempt={attempt}")
                        break

                    except Exception as download_error:
                        last_error = download_error
                        error_str = str(download_error).lower()

                        is_retryable = any(keyword in error_str for keyword in [
                            'not found', '404', 'connection', 'temporary', 'timeout', 'timed out'
                        ])

                        if is_retryable and attempt < max_download_retries:
                            retry_delay = base_retry_delay * attempt
                            logger.warning(
                                f"[FreeCAD] ⚠️ Download attempt {attempt}/{max_download_retries} failed | "
                                f"user_id={user_id} | "
                                f"error={download_error} | "
                                f"retrying_in={retry_delay}s"
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            if attempt >= max_download_retries:
                                logger.error(
                                    f"[FreeCAD] ❌ Download failed after {max_download_retries} attempts | "
                                    f"user_id={user_id} | "
                                    f"total_wait_time={initial_delay + sum(base_retry_delay * i for i in range(1, attempt))}s | "
                                    f"final_error={download_error}"
                                )
                            else:
                                logger.error(
                                    f"[FreeCAD] ❌ Download failed with non-retryable error | "
                                    f"user_id={user_id} | "
                                    f"attempt={attempt}/{max_download_retries} | "
                                    f"error={download_error}"
                                )
                            raise

                if result is None:
                    total_wait_time = initial_delay + sum(base_retry_delay * i for i in range(1, max_download_retries))
                    logger.error(
                        f"[FreeCAD] ❌ Failed to download results after {max_download_retries} attempts "
                        f"(total wait time: {total_wait_time}s). Last error: {last_error}"
                    )
                    raise Exception(ERROR_CODES["102.8"])

                total_exec_time = monitoring_result.get('total_time', 0)
                logger.info(f"[FreeCAD] All phases complete | user_id={user_id} | total={total_exec_time:.2f}s")
            
            if not result["success"]:
                logger.error(f"Remote FreeCAD execution failed: {result.get('message', 'Unknown error')}")
                print(f"[ERROR] Remote FreeCAD execution failed: {result.get('message', 'Unknown error')}")
                if any(keyword in str(result.get('message', '')).lower() for keyword in ['charmap', 'codec', 'encode', 'unicode']):
                    raise Exception(ERROR_CODES["104.2"])
                raise Exception(ERROR_CODES["104.1"])
            
            logger.info("Remote FreeCAD execution successful")
            print(f"[SUCCESS] Successfully executed FreeCAD script on remote server")
            
            downloaded_files = result.get("files", {})
            return result, downloaded_files
            
        except (FreeCADConnectionError, FreeCADServerNotAvailableError) as e:
            logger.error(f"[FreeCAD] Server error | user_id={current_user_id} | session_id={session_id}: {e}")
            if any(keyword in str(e).lower() for keyword in ['timeout', 'timed out']):
                logger.error(f"[FreeCAD] Timeout-related error: {e}")
                raise Exception(ERROR_CODES["101.1"])
            if any(keyword in str(e).lower() for keyword in ['partial_success']):
                logger.error(f"[FreeCAD] Partial success error: {e}")
                raise Exception(ERROR_CODES["104.6"])
            raise Exception(ERROR_CODES["102.1"])
        except FreeCADProcessingError as e:
            error_str = str(e).lower()
            logger.error(f"[FreeCAD] Processing error | user_id={current_user_id} | session_id={session_id}: {e}")
            if any(keyword in error_str for keyword in ['timeout', 'timed out']):
                logger.error(f"[FreeCAD] Timeout-related error: {e}")
                raise Exception(ERROR_CODES["101.1"])
            if any(keyword in error_str for keyword in ['partial_success']):
                logger.error(f"[FreeCAD] Partial success error: {e}")
                raise Exception(ERROR_CODES["104.6"])
            raise Exception(ERROR_CODES["104.1"])
        except Exception as e:
            logger.error(f"[FreeCAD] Error executing script on remote server | user_id={current_user_id} | session_id={session_id}: {e}")
            # Re-raise immediately if already an ERROR_CODE (format: "NNN.N")
            if re.match(r'^\d{3}\.\d+$', str(e).strip()):
                raise

            if any(keyword in str(e).lower() for keyword in ['charmap', 'codec', 'encode', 'unicode']):
                raise Exception(ERROR_CODES["104.2"])

            if any(keyword in str(e).lower() for keyword in ['timeout', 'timed out']):
                logger.error(f"[FreeCAD] Timeout-related error: {e}")
                raise Exception(ERROR_CODES["101.1"])

            if any(keyword in str(e).lower() for keyword in ['partial_success']):
                logger.error(f"[FreeCAD] Partial success error: {e}")
                raise Exception(ERROR_CODES["104.6"])

            if any(keyword in str(e).lower() for keyword in ['server', 'connection', 'network', 'unavailable']):
                logger.error(f"[FreeCAD] Server-related error: {e}")
                raise Exception(ERROR_CODES["102.1"])
            raise Exception(ERROR_CODES["104.1"])



    async def save_outputs(self, code, design_requirements, base_filename="generated_cad", user_text="", session_id=None, priority: int = 0):

        from src.utils.file_manager import save_code_file, save_metadata_file
        from src.core.freecad_remote_client import (
    FreeCADConnectionError, 
    FreeCADTimeoutError, 
    FreeCADServerNotAvailableError, 
    FreeCADProcessingError,
    create_freecad_client
)
        from src.utils.path_manager import OBJ_OUTPUT_DIR, CAD_OUTPUT_DIR, PDF_OUTPUT_DIR, PROJECT_ROOT
        import datetime
        import asyncio

        logger.info(f"Starting save_outputs with remote FreeCAD server (async optimized, priority={priority})")

        shape_type = "unknown"
        dimensions = {}

        # AnalysisAndParameterCheckOutput (the actual type passed in here today)
        # carries shape_type as a top-level field, not nested under `.shapes[0]`
        # -- that legacy nested path is kept below only for any older/other
        # caller that might still pass an object shaped that way.
        if getattr(design_requirements, 'shape_type', 'unknown') not in (None, '', 'unknown'):
            shape_type = design_requirements.shape_type
        elif hasattr(design_requirements, 'shapes') and design_requirements.shapes:
            primary_shape = design_requirements.shapes[0]
            shape_type = primary_shape.shape_type

            if hasattr(primary_shape, 'dimensions') and primary_shape.dimensions:
                dimensions = primary_shape.dimensions

        if not dimensions:
            dimensions = design_requirements.title or base_filename

        if shape_type == "unknown" and design_requirements.title:
            shape_type = design_requirements.title

        # Save code file
        async def save_code_async():
            try:
                loop = asyncio.get_event_loop()
                code_filepath = await loop.run_in_executor(
                    None, save_code_file, code, shape_type, dimensions, design_requirements.dict()
                )
                logger.info(f"Saved generated code to: {code_filepath}")
                print(f"💾 Saved generated code to: {code_filepath}")
                return code_filepath
            except Exception as e:
                logger.error(f"Error saving code file: {e}")
                print(f"[ERROR] Error saving code file: {e}")
                return None

        # Save code file
        code_filepath = await save_code_async()

        # Handle exceptions
        if isinstance(code_filepath, Exception):
            logger.error(f"Code file saving failed: {code_filepath}")
            code_filepath = None

        if not code_filepath:
            logger.error("Code file saving failed")
            raise Exception(ERROR_CODES["105.4"])

        # 🆕 Generate threaded holes metadata (after code is saved)
        threaded_metadata_path = None
        try:
            from src.utils.file_manager import save_threaded_metadata_file_async

            logger.debug(f"[THREADED] Analyzing code for threaded holes: {code_filepath}")

            # Get cost tracker for this session
            cost_tracker = self._get_cost_tracker(session_id) if session_id else None

            # ── Perforated Sheet detection for metadata-skip, robust across new + edit flows ──
            # `shape_type` alone is unreliable in the edit flow: _prepare_current_requirements()
            # falls back to shape_type="unknown" whenever latest_requirements wasn't refreshed
            # (text_to_cad_agent.py:3625-3631) -- this is exactly the flow that caused the
            # original incident (session_0a8b30_297471, edit request "fais plus de rectangles").
            # perf_calc_result is populated by _run_perf_vide_function_call() whenever the
            # unified chain detects Perforated Sheet on ANY turn (new or edit) and persists in
            # session state, so use it as a second, more reliable signal here.
            metadata_shape_type = shape_type
            if 'perforated' not in (shape_type or '').lower() and session_id:
                if self._get_session_state(session_id).get('perf_calc_result') is not None:
                    metadata_shape_type = "Perforated Sheet"

            # Save threaded metadata (async - no executor needed)
            threaded_metadata_path = await save_threaded_metadata_file_async(
                Path(code_filepath),
                metadata_shape_type,
                dimensions,
                design_requirements.dict() if design_requirements else None,
                cost_tracker  # ← Pass cost tracker for OpenAI cost tracking
            )
            
            if threaded_metadata_path:
                logger.info(f"[THREADED] ✅ Threaded metadata generated: {threaded_metadata_path}")
            else:
                logger.debug(f"[THREADED] No threaded holes detected in code")
                
        except Exception as e:
            logger.warning(f"[THREADED] Failed to generate threaded metadata: {e}")
            threaded_metadata_path = None

        # Execute FreeCAD script on remote server with progress monitoring
        result, downloaded_files = await self._execute_freecad_remote(
            code_filepath, session_id, threaded_metadata_path, priority=priority, shape_type=shape_type
        )

        # Prepare output directories (parallel directory creation)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        obj_dir = OBJ_OUTPUT_DIR / today
        step_dir = CAD_OUTPUT_DIR / today
        pdf_dir = PDF_OUTPUT_DIR / today

        # Create directories in parallel
        async def create_dir_async(directory):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: directory.mkdir(parents=True, exist_ok=True))

        await asyncio.gather(
            create_dir_async(obj_dir),
            create_dir_async(step_dir),
            create_dir_async(pdf_dir),
            return_exceptions=True
        )

        # Generate unique filename — include session_id hash to prevent
        # filename collisions when multiple users generate at the same second
        import hashlib as _hashlib
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        _session_hash = _hashlib.md5((session_id or "unknown").encode()).hexdigest()[:8]
        base_filename = f"{shape_type}_{timestamp}_{_session_hash}"

        # Process downloaded files in parallel
        async def copy_file_async(file_type, src_path, dst_path):
            """
            Copy file with retry logic and validation for multi-user scenarios.
            
            This function handles:
            - Source file validation (exists, readable, non-empty)
            - Retry logic with exponential backoff
            - File size verification after copy
            - Comprehensive error logging for debugging
            """
            max_retries = 3
            base_delay = 1.0  # Start with 1 second delay
            
            for attempt in range(1, max_retries + 1):
                try:
                    # STEP 1: Validate source file exists and is readable
                    if not os.path.exists(src_path):
                        error_msg = f"Source file does not exist: {src_path}"
                        logger.error(f"[FILE_COPY] ❌ {error_msg} (attempt {attempt}/{max_retries})")
                        
                        if attempt < max_retries:
                            # Wait and retry - source file might not be ready yet
                            retry_delay = base_delay * attempt
                            logger.info(f"[FILE_COPY] Retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            # Last attempt failed
                            raise FileNotFoundError(error_msg)
                    
                    # STEP 2: Check source file size (must be > 0)
                    src_size = os.path.getsize(src_path)
                    if src_size == 0:
                        error_msg = f"Source file is empty: {src_path}"
                        logger.error(f"[FILE_COPY] ❌ {error_msg} (attempt {attempt}/{max_retries})")
                        
                        if attempt < max_retries:
                            retry_delay = base_delay * attempt
                            logger.info(f"[FILE_COPY] Retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            raise ValueError(error_msg)
                    
                    logger.debug(f"[FILE_COPY] Source file validated: {src_path} ({src_size} bytes)")
                    
                    # STEP 3: Perform the copy
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: __import__('shutil').copy2(src_path, dst_path))
                    
                    # STEP 4: Verify destination file
                    if not os.path.exists(dst_path):
                        error_msg = f"Copy failed - destination file not created: {dst_path}"
                        logger.error(f"[FILE_COPY] ❌ {error_msg} (attempt {attempt}/{max_retries})")
                        
                        if attempt < max_retries:
                            retry_delay = base_delay * attempt
                            logger.info(f"[FILE_COPY] Retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            raise IOError(error_msg)
                    
                    # STEP 5: Verify file size matches
                    dst_size = os.path.getsize(dst_path)
                    if dst_size != src_size:
                        error_msg = f"Copy incomplete - size mismatch: src={src_size} bytes, dst={dst_size} bytes"
                        logger.error(f"[FILE_COPY] ❌ {error_msg} (attempt {attempt}/{max_retries})")
                        
                        if attempt < max_retries:
                            # Delete incomplete file
                            try:
                                os.remove(dst_path)
                            except:
                                pass
                            
                            retry_delay = base_delay * attempt
                            logger.info(f"[FILE_COPY] Retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            raise IOError(error_msg)
                    
                    # SUCCESS!
                    logger.info(f"[FILE_COPY] ✅ {file_type.upper()} copied successfully: {dst_size} bytes (attempt {attempt}/{max_retries})")
                    return str(dst_path)
                    
                except Exception as e:
                    logger.error(f"[FILE_COPY] ❌ Error copying {file_type.upper()} file (attempt {attempt}/{max_retries}): {e}")
                    logger.error(f"[FILE_COPY] Source: {src_path}")
                    logger.error(f"[FILE_COPY] Destination: {dst_path}")
                    
                    if attempt < max_retries:
                        retry_delay = base_delay * attempt
                        logger.info(f"[FILE_COPY] Retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        # Last attempt failed
                        logger.error(
                            f"[FILE_COPY] ❌ FAILED after {max_retries} attempts | "
                            f"type={file_type.upper()} | "
                            f"src={src_path} | "
                            f"dst={dst_path} | "
                            f"last_error={e}"
                        )
                        print(f"[ERROR] Could not copy {file_type.upper()} file after {max_retries} attempts: {e}")
                        return None


        # Prepare file copy tasks
        copy_tasks = []
        file_mappings = {}

        if "obj" in downloaded_files:
            obj_path = obj_dir / f"{base_filename}.obj"
            copy_tasks.append(copy_file_async("obj", downloaded_files["obj"], obj_path))
            file_mappings["obj"] = obj_path

        if "step" in downloaded_files:
            step_path = step_dir / f"{base_filename}.step"
            copy_tasks.append(copy_file_async("step", downloaded_files["step"], step_path))
            file_mappings["step"] = step_path

        # PDF is optional (visualization only) — copy it when the server produced
        # one, but a missing/failed PDF must never affect STEP/OBJ handling.
        if "pdf" in downloaded_files:
            pdf_path = pdf_dir / f"{base_filename}.pdf"
            copy_tasks.append(copy_file_async("pdf", downloaded_files["pdf"], pdf_path))
            file_mappings["pdf"] = pdf_path

        # Execute all file copy operations in parallel
        if copy_tasks:
            copy_results = await asyncio.gather(*copy_tasks, return_exceptions=True)

            # Map results back to file types
            obj_path_to_return = None
            step_path_to_return = None
            pdf_path_to_return = None

            result_index = 0
            if "obj" in downloaded_files:
                obj_path_to_return = copy_results[result_index] if not isinstance(copy_results[result_index], Exception) else None
                result_index += 1
            if "step" in downloaded_files:
                step_path_to_return = copy_results[result_index] if not isinstance(copy_results[result_index], Exception) else None
                result_index += 1
            if "pdf" in downloaded_files:
                pdf_path_to_return = copy_results[result_index] if not isinstance(copy_results[result_index], Exception) else None
                result_index += 1
        else:
            obj_path_to_return = None
            step_path_to_return = None
            pdf_path_to_return = None

        # Summary of generated files (always display on every conversation)
        session_info = f" [Session: {session_id}]" if session_id else ""
        
        # Always print summary for every conversation to keep users informed
        print(f"\n📁 Generated Files Summary{session_info}:")
        if code_filepath:
            print(f"  - Python Code: {code_filepath}")
        if threaded_metadata_path:
            print(f"  - Threaded Metadata: {threaded_metadata_path}")
        if step_path_to_return:
            print(f"  - STEP Model: {step_path_to_return}")
        if obj_path_to_return:
            print(f"  - OBJ Model: {obj_path_to_return}")
        if pdf_path_to_return:
            print(f"  - PDF: {pdf_path_to_return}")
        print("")

        # Check if required output files were created
        if not step_path_to_return:
            logger.error("No STEP file was generated by remote server")
            print(f"[ERROR] No STEP file was generated by remote server")
            raise Exception(ERROR_CODES["104.3"])

        # Log completion only once per session
        if _session_log_tracker.should_log(session_id or "unknown", f"save_outputs_complete_{session_id}", max_count=1):
            logger.info("save_outputs completed successfully")
        
        # PDF is optional — pdf_path_to_return may be None (not generated/failed to copy)
        # without affecting STEP/OBJ, which are the required outputs.
        return obj_path_to_return, step_path_to_return, pdf_path_to_return

    async def process_request_with_progress(self, user_text, is_edit_request=False, session_id=None, request_origin='api', material_choice=None, priority: int = 0):
        import asyncio
        import logging
        import time
        import traceback

        logger = logging.getLogger(__name__)
        start_time = time.time()
        logger.info(f"[AGENT_PRIORITY] process_request_with_progress priority={priority} | session={session_id}")

        # ════════════════════════════════════════════════════════════════
        # DEBUG LOG: Validate session_id at the entry of process_request_with_progress
        # ════════════════════════════════════════════════════════════════
        if session_id is None:
            import uuid
            import random
            rand_digits = random.randint(100000, 999999)
            rand_uuid   = uuid.uuid4().hex[:6]
            session_id  = f"session_{rand_uuid}_{rand_digits}"
            logger.warning(
                f"\n{'!'*60}\n"
                f"[SESSION_WARN] process_request_with_progress received a NONE session_id!\n"
                f"  → Server forcefully generated a new session_id: {session_id}\n"
                f"  → This may be a BUG - session_id should be resolved upstream in generate_cad_realtime_stream\n"
                f"{'!'*60}"
            )
        else:
            logger.info(
                f"\n{'='*60}\n"
                f"[SESSION] process_request_with_progress received a valid session_id:\n"
                f"  session_id = {session_id}\n"
                f"{'='*60}"
            )
        # ════════════════════════════════════════════════════════════════

        # ── Mark start of this request turn for per-turn cost tracking ──
        # Must be called BEFORE any chain invocations so that
        # tracker.get_request_summary() correctly isolates this turn's cost.
        self._get_cost_tracker(session_id).start_request()

        try:
            logger.info(f"[AGENT_STEP] Starting analysis step for session {session_id}")
            yield {
                "step": "analysis",
                "status": "Analyzing user requirements...",
                "is_complete": False,
                "is_active": True,
                "progress": 10
            }

            logger.debug(f"[AGENT_STATE] Getting session state for session {session_id}")
            state = self._get_session_state(session_id)
            
            # Store material_choice in session state for use in templates
            if material_choice:
                state['material_choice'] = material_choice
                state['mapped_material'] = map_material_to_geometry_analyzer(material_choice)
            elif 'material_choice' not in state:
                state['material_choice'] = "STEEL"  # Default material
                state['mapped_material'] = "steel"  # Mapped default
            
            print(f"[DEBUG] Material choice set in session state: '{state.get('material_choice', 'NOT SET')}' → Mapped: '{state.get('mapped_material', 'NOT SET')}'")
            logger.debug(f"[AGENT_PROGRESS] Material choice stored in session state: {state.get('material_choice')} → Mapped: {state.get('mapped_material')}")

            # ════════════════════════════════════════════════════════════════
            # CONFIRM FAST-PATH GATE
            # ────────────────────────────────────────────────────────────────
            # Activated ONLY when this session is waiting for a confirm reply.
            # Completely isolated per session_id — 100 concurrent users safe.
            # Skips: greeting_chain, query_expander, RAG, unified_chain,
            #        DFM_chain, description_confirm_chain.
            # ════════════════════════════════════════════════════════════════
            if state.get('awaiting_confirm', False):
                logger.info(
                    f"[CONFIRM_GATE] 🚪 Gate OPEN (awaiting_confirm=True) | session={session_id} "
                    f"| user='{user_text[:80]}'"
                )

                # ── Step 1: mini LLM — classify YES / NO / REFINEMENT ─────
                intent = await self._detect_confirm_intent(user_text, session_id)

                # ── Step 2a: CHANGE — user rejects OR wants to modify ──────
                # Both "no" and "yes but change X" are treated identically:
                # clear the gate and fall through to the normal unified chain.
                # The unified chain reads full DB history and will understand
                # what the user wants to change.
                if intent == "CHANGE":
                    logger.info(
                        f"[CONFIRM_GATE] 🔄 CHANGE intent → clearing gate, "
                        f"running normal unified flow | session={session_id}"
                    )
                    # Clear ONLY the gate flag and fast-path cache.
                    # CRITICAL: do NOT touch confirm_count or confirmed_description.
                    # confirm_count must keep its current value so that
                    # _run_description_confirm() can correctly show "Round 2/3",
                    # "Round 3/3" and auto-generate code after MAX_CONFIRM_ATTEMPTS.
                    self._update_session_state(
                        session_id,
                        awaiting_confirm=False,
                        cached_raw_unified_json='',
                        cached_retrieved_context='',
                        cached_unified_obj_snapshot=None,
                        cached_expanded_user_text='',
                        cached_perf_calc_result=None,
                    )
                    # Fall through to normal unified chain flow below

                # ── Step 2b: YES — fast-path code generation ──────────────
                elif intent == "YES":
                    logger.info(
                        f"[CONFIRM_GATE] ✅ YES fast-path | session={session_id}"
                    )
                    # Retrieve cached data (all per session_id — multi-user safe)
                    confirmed_desc = state.get('confirmed_description', '')
                    snapshot       = state.get('cached_unified_obj_snapshot')
                    raw_json_fp    = state.get('cached_raw_unified_json', '')
                    rag_ctx_fp     = state.get('cached_retrieved_context', '')
                    expanded_fp    = state.get('cached_expanded_user_text', '')
                    perf_calc_fp   = state.get('cached_perf_calc_result')

                    if not confirmed_desc or snapshot is None:
                        # Cache miss (e.g. server restart between turns) → graceful fallback
                        logger.warning(
                            f"[CONFIRM_GATE] ⚠️ YES but cache empty "
                            f"(desc={bool(confirmed_desc)}, snapshot={bool(snapshot)}). "
                            f"Falling back to normal flow | session={session_id}"
                        )
                        self._update_session_state(
                            session_id,
                            awaiting_confirm=False,
                            cached_perf_calc_result=None,
                        )
                        # Fall through to normal flow

                    else:
                        # Reconstruct unified_output_obj from snapshot
                        try:
                            # AnalysisAndParameterCheckOutput imported at module top (line 15)
                            unified_output_obj_fp = AnalysisAndParameterCheckOutput(**snapshot)
                            # Use the polished final_description as the description
                            unified_output_obj_fp.description = confirmed_desc
                        except Exception as exc:
                            logger.error(
                                f"[CONFIRM_GATE] Reconstruct failed: {exc}. "
                                f"Falling back to normal flow | session={session_id}"
                            )
                            self._update_session_state(
                                session_id,
                                awaiting_confirm=False,
                                cached_perf_calc_result=None,
                            )
                            # Fall through to normal flow

                        else:
                            # ── Reset confirm state + cache BEFORE gen code ──
                            self._update_session_state(
                                session_id,
                                awaiting_confirm=False,
                                confirm_count=0,
                                confirmed_description='',
                                cached_raw_unified_json='',
                                cached_retrieved_context='',
                                cached_unified_obj_snapshot=None,
                                cached_expanded_user_text='',
                                cached_perf_calc_result=None,
                                perf_calc_result=perf_calc_fp or state.get('perf_calc_result'),
                            )

                            # Emit progress: analysis + parameters (skipped steps)
                            yield {
                                "step": "analysis",
                                "status": "Analysis completed (fast-path).",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 20,
                                "design_type": unified_output_obj_fp.design_type,
                            }
                            yield {
                                "step": "parameters",
                                "status": "Parameters validated (from confirmed description).",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 40,
                            }
                            yield {
                                "step": "generation_code",
                                "status": "Generating FreeCAD Python code...",
                                "is_complete": False,
                                "is_active": True,
                                "progress": 50,
                            }

                            await asyncio.sleep(0.3)

                            # ── Jump straight to code gen — no unified/RAG/DFM ──
                            logger.info(
                                f"[CONFIRM_GATE] 🚀 Calling generate_code_from_requirements "
                                f"(fast-path) | session={session_id}"
                            )
                            fp_codegen_start = time.time()
                            fp_result = await self.generate_code_from_requirements(
                                unified_output_obj_fp,
                                raw_json_fp,
                                rag_ctx_fp,
                                session_id=session_id,
                                save_files=False,
                                expanded_user_text=expanded_fp,
                            )
                            logger.info(
                                f"[CONFIRM_GATE] ✅ Fast-path codegen done in "
                                f"{time.time()-fp_codegen_start:.2f}s | session={session_id}"
                            )

                            yield {
                                "step": "generation_code",
                                "status": "Code generation completed.",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 70,
                            }

                            if fp_result.get("error"):
                                logger.error(
                                    f"[CONFIRM_GATE] Codegen error: {fp_result.get('error')} "
                                    f"| session={session_id}"
                                )
                                yield {"final_result": fp_result}
                                return

                            # Export step — same pattern as normal PATH B
                            fp_export_start_time = time.time()
                            yield {
                                "step": "export",
                                "status": "Exporting files and executing FreeCAD...",
                                "is_complete": False,
                                "is_active": True,
                                "progress": 80,
                                **self._build_export_eta_payload(session_id, fp_export_start_time),
                            }

                            await asyncio.sleep(0.3)

                            try:
                                fp_generated_code = fp_result.get("code")
                                if fp_generated_code:
                                    fp_export_task = asyncio.create_task(self.save_outputs(
                                        fp_generated_code,
                                        unified_output_obj_fp,
                                        user_text=user_text,
                                        session_id=session_id,
                                        priority=priority,
                                    ))
                                    while True:
                                        try:
                                            obj_path_fp, step_path_fp, pdf_path_fp = await asyncio.wait_for(
                                                asyncio.shield(fp_export_task),
                                                timeout=15,
                                            )
                                            break
                                        except asyncio.TimeoutError:
                                            yield {
                                                "step": "export",
                                                "status": "Exporting files and executing FreeCAD...",
                                                "is_complete": False,
                                                "is_active": True,
                                                "progress": 80,
                                                **self._build_export_eta_payload(session_id, fp_export_start_time),
                                            }
                                    fp_result.update({
                                        "obj_path":  obj_path_fp,
                                        "step_path": step_path_fp,
                                        "pdf_path": pdf_path_fp,
                                    })
                            except Exception as export_exc:
                                logger.error(
                                    f"[CONFIRM_GATE] Export error: {export_exc} | session={session_id}"
                                )
                                fp_result["error"] = str(export_exc)

                            yield {
                                "step": "export",
                                "status": "Export completed.",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 95,
                                **self._build_export_eta_payload(session_id, fp_export_start_time),
                            }
                            yield {
                                "step": "complete",
                                "status": "All processing completed!",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 100,
                            }

                            total_fp = time.time() - start_time
                            logger.info(
                                f"[CONFIRM_GATE] 🏁 Fast-path complete in {total_fp:.2f}s | session={session_id}"
                            )
                            yield {"final_result": fp_result}
                            return
            # ════════════════════════════════════════════════════════════════
            # END CONFIRM FAST-PATH GATE — continue normal flow below
            # ════════════════════════════════════════════════════════════════


            # Set by the information_request branch below when the request turns
            # out to carry real CAD intent: the unified + RAG analysis it already
            # ran is handed to the normal flow rather than being recomputed there.
            prefetched_unified_result = None
            prefetched_user_text_with_history = None

            # Check for greeting/casual conversation/process-question first.
            # Runs on EVERY non-edit turn (not just the very first message of the
            # session) — a pre-generation multi-turn conversation (still answering
            # clarifying questions, part not yet created) previously skipped this
            # check once has_db_history became True, so an unrelated question asked
            # on turn 2+ (e.g. "how much would this cost?") fell straight into the
            # CAD requirements pipeline and produced an empty/nonsense confirm
            # prompt instead of a real answer. Confidence thresholds below + the
            # cad_request fallback on low confidence keep this from misreading a
            # genuine clarifying-answer reply (e.g. "50mm", "steel") as off-topic.
            if not is_edit_request:
                logger.info(f"[AGENT_GREETING] Checking for greeting/casual conversation for session {session_id}")
                try:
                    cost_tracker = self._get_cost_tracker(session_id)
                    greeting_result = await ainvoke_with_cost_tracking(
                        "greeting_classification_progress",
                        self.greeting_classification_chain.ainvoke,
                        {"user_text": user_text},
                        cost_tracker,
                        self.model_names['greeting']
                    )
                    logger.info(f"[AGENT_GREETING] Classification: {greeting_result.get('classification')}, Confidence: {greeting_result.get('confidence')}")

                    # Trust AI classification - no keyword override needed

                    # Guard: a short reply is answering a clarifying question we
                    # ourselves just asked (e.g. "option A", "base", "vertical wall")
                    # — the classifier has no conversation context and can misread
                    # these bare answers as a greeting OR an information_request
                    # (it has seen no history, so it guesses from the isolated text
                    # alone). pending_questions is only non-empty while we're
                    # waiting on such a reply, so it overrides both misreads here.
                    # process_question is intentionally left alone — a genuine
                    # pricing/pdf ask can still happen mid-clarification.
                    has_pending_question = bool(state.get('pending_questions'))
                    if has_pending_question and greeting_result.get('classification') in ('greeting', 'information_request'):
                        logger.info(
                            f"[AGENT_GREETING] Overriding '{greeting_result.get('classification')}' classification → cad_request "
                            f"(session {session_id} has {len(state['pending_questions'])} pending question(s))"
                        )
                        greeting_result['classification'] = 'cad_request'

                    # If it's a greeting with high confidence, return the response immediately
                    if (greeting_result.get('classification') == 'greeting' and
                        greeting_result.get('confidence', 0) > 0.8 and
                        greeting_result.get('response')):

                        logger.info(f"[AGENT_GREETING] Detected greeting/casual conversation for session {session_id}")
                        yield {
                            "step": "analysis",
                            "status": "Greeting detected - providing response.",
                            "is_complete": True,
                            "is_active": False,
                            "progress": 100
                        }

                        result = {
                            "code": None,
                            "message": greeting_result['response'],
                            "explanation": "Greeting/casual conversation response"
                        }
                        yield {"final_result": result}
                        return

                    # Non-CAD business/process question (pricing, PDF/file export...) —
                    # answer with the fixed canned reply, never route into CAD generation.
                    if (greeting_result.get('classification') == 'process_question' and
                        greeting_result.get('confidence', 0) > 0.7):

                        sub_type = greeting_result.get('sub_type')
                        logger.info(f"[AGENT_PROCESS_QUESTION] Detected process_question (sub_type={sub_type}) for session {session_id}")
                        yield {
                            "step": "analysis",
                            "status": "Process question detected - providing response.",
                            "is_complete": True,
                            "is_active": False,
                            "progress": 100
                        }

                        result = {
                            "code": None,
                            "message": get_process_question_response(sub_type),
                            "explanation": f"Process question response (sub_type={sub_type})"
                        }
                        yield {"final_result": result}
                        return

                    # If it's an information request, retrieve context and provide informed response
                    if (greeting_result.get('classification') == 'information_request' and
                        greeting_result.get('confidence', 0) > 0.7):

                        user_text_preview = user_text[:100] + "..." if len(user_text) > 100 else user_text
                        logger.info(f"[AGENT_INFO_REQUEST] Retrieving context for: '{user_text_preview}'")

                        yield {
                            "step": "analysis",
                            "status": "Information request detected - retrieving context...",
                            "is_complete": False,
                            "is_active": True,
                            "progress": 50
                        }

                        # Run the SAME pipeline the CAD path runs — query expansion,
                        # RAG retrieval, unified + DFM — exactly once for this turn.
                        # This branch used to hand-roll its own expansion + rules
                        # retrieval + unified call, and then, whenever the request
                        # turned out to carry real CAD intent, fell through to the
                        # normal flow which ran the whole thing a second time.
                        # `prefetched_*` below hands this result to that flow instead.
                        try:
                            user_text_with_history = self._build_user_text_with_history(session_id, user_text)
                            logger.info(
                                f"[AGENT_INFO_REQUEST] History built ({len(user_text_with_history)} chars) "
                                f"→ running unified analysis with RAG | session={session_id}"
                            )

                            unified_chain_result = await self._invoke_unified_with_rag(
                                user_text=user_text_with_history,
                                session_id=session_id,
                                latest_requirements_for_guidance=None,
                                material=state.get('material_choice', ''),
                                mapped_material=state.get('mapped_material', 'steel'),
                                # Answer the question from the knowledge base, not just
                                # validate against it — matches the old k=15 + inlined
                                # "Retrieved Context:" prompt this branch used to build.
                                k_rules=15,
                                inline_rules_context=True,
                            )

                            yield {
                                "step": "analysis",
                                "status": "Generating informed response...",
                                "is_complete": False,
                                "is_active": True,
                                "progress": 80
                            }

                            unified_output_obj = unified_chain_result["unified_output_obj"]

                            # Check if this is PURE information request (no CAD intent)
                            # OR if there are questions that need user answers
                            has_unanswered_questions = (
                                unified_output_obj.questions and 
                                len(unified_output_obj.questions) > 0 and 
                                not unified_output_obj.skip_questions_requested
                            )
                            
                            is_pure_info_request = (
                                unified_output_obj.title == "Unable to parse requirements"
                                or not unified_output_obj.description
                                or unified_output_obj.complexity_level == 0
                            )

                            # CRITICAL: If there are questions and user hasn't skipped them,
                            # we MUST stop and wait for user answers - DO NOT generate code!
                            if is_pure_info_request or has_unanswered_questions:
                                # Pure information request OR questions need answers - return questions only
                                if unified_output_obj.questions:
                                    response_message = "\n".join(unified_output_obj.questions)
                                else:
                                    response_message = greeting_result.get('response', 'Information request detected. Please specify what information you need.')

                                if has_unanswered_questions:
                                    logger.info(f"[AGENT_INFO_REQUEST] ❓ Questions detected - waiting for user answers (NOT generating code)")
                                else:
                                    logger.info(f"[AGENT_INFO_REQUEST] 💬 Pure info request - Response length: {len(response_message)} chars")

                                yield {
                                    "step": "analysis",
                                    "status": "Information request completed." if is_pure_info_request else "Waiting for user answers...",
                                    "is_complete": True,
                                    "is_active": False,
                                    "progress": 100
                                }

                                result = {
                                    "code": None,
                                    "message": response_message,
                                    "explanation": "Information request with context retrieval" if is_pure_info_request else "Questions require user input"
                                }

                                yield {"final_result": result}
                                return
                            
                            # If we reach here, it means info request has CAD potential.
                            # Continue to normal flow (don't return early) — handing it
                            # the analysis we just paid for so it doesn't redo it.
                            logger.info(f"[AGENT_INFO_REQUEST] 🔄 Info request with CAD potential - reusing unified result for normal flow")
                            prefetched_unified_result = unified_chain_result
                            prefetched_user_text_with_history = user_text_with_history

                        except Exception as e:
                            logger.error(f"[AGENT_INFO_REQUEST] Error retrieving context: {e}")
                            # Fallback to basic response
                            yield {
                                "step": "analysis",
                                "status": "Information request completed (fallback).",
                                "is_complete": True,
                                "is_active": False,
                                "progress": 100
                            }

                            result = {
                                "code": None,
                                "message": greeting_result.get('response', 'Information request detected. Please specify what information you need.'),
                                "explanation": "Information request response (fallback)"
                            }

                            yield {"final_result": result}
                            return

                except Exception as e:
                    logger.warning(f"[AGENT_GREETING] Error in greeting classification: {e}")
                    # Continue with normal processing if greeting classification fails
            # ══════════════════════════════════════════════════════════════════════
            # EDIT MODE — all edit requests route to process_edit_request.
            # process_edit_request builds conversation history internally for RAG.
            # ══════════════════════════════════════════════════════════════════════

            # 🔍 DIAGNOSTIC: log gate values to detect sync issues before edit routing
            _latest_code_len = len(state.get('latest_code') or '')
            logger.info(
                f"[EDIT_GATE] session={session_id} | "
                f"is_edit_request={is_edit_request} | "
                f"latest_code={'PRESENT (' + str(_latest_code_len) + ' chars)' if _latest_code_len else 'EMPTY ⚠️'} | "
                f"gate_result={'→ EDIT MODE ✅' if (is_edit_request and _latest_code_len) else '→ NORMAL FLOW ❌ (edit skipped)'}"
            )

            # ── Bypass edit routing when confirm reply pending ─────────────
            # Shape change confirm uses the same awaiting_confirm=True flag as
            # normal description confirm — both go to the fast-path gate.
            if is_edit_request and state['latest_code'] and state.get('awaiting_confirm', False):
                logger.info(
                    f"[EDIT_GATE] awaiting_confirm=True → bypassing edit routing, "
                    f"falling through to FAST-PATH GATE | session={session_id}"
                )
                print(f"[EDIT_GATE] ✅ awaiting_confirm → skipping edit, using fast-path confirm flow")
                is_edit_request = False  # Force into the normal (non-edit) flow

            if is_edit_request and state['latest_code']:
                yield {
                    "step": "analysis",
                    "status": "Analysis completed - edit mode.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 20
                }

                yield {
                    "step": "parameters",
                    "status": "Parameters not needed for edit mode.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 40
                }

                logger.info(f"[AGENT_STEP] Starting code editing step for session {session_id}")
                yield {
                    "step": "generation_code",
                    "status": "Editing FreeCAD Python code...",
                    "is_complete": False,
                    "is_active": True,
                    "progress": 50
                }

                await asyncio.sleep(0.5)

                logger.info(f"[AGENT_EDIT] Calling process_edit_request for session {session_id}")
                edit_start_time = time.time()
                result = await self.process_edit_request(user_text, session_id=session_id)
                edit_duration = time.time() - edit_start_time
                logger.info(f"[AGENT_EDIT] Edit processing completed in {edit_duration:.2f}s for session {session_id}")

                yield {
                    "step": "generation_code",
                    "status": "Code editing completed.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 70
                }

                if result.get("error"):
                    logger.error(f"[AGENT_EDIT] Edit error for session {session_id}: {result.get('error')}")
                    yield {"final_result": result}
                    return

                if result.get("warning"):
                    # DFM/unifier flagged this edit (awaiting_edit_ack) — no code was
                    # touched yet, nothing to export. Surface the warning and stop.
                    logger.info(f"[AGENT_EDIT] Edit warning (awaiting ack) for session {session_id}")
                    yield {"final_result": result}
                    return

                logger.info(f"[AGENT_STEP] Starting export step for session {session_id}")
                yield {
                    "step": "export",
                    "status": "Exporting edited files...",
                    "is_complete": False,
                    "is_active": True,
                    "progress": 80
                }

                await asyncio.sleep(0.3)

                yield {
                    "step": "export",
                    "status": "Export completed.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 95
                }

                logger.info(f"[AGENT_STEP] Starting completion step for session {session_id}")
                yield {
                    "step": "complete",
                    "status": "All editing completed!",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 100
                }

                total_duration = time.time() - start_time
                logger.info(f"[AGENT_PROGRESS] Edit process completed in {total_duration:.2f}s for session {session_id}")
                yield {"final_result": result}
                return
            else:
                logger.info(f"[AGENT_NEW] Processing new/continuation CAD request for session {session_id}")

                # Always build history from DB — covers both first turn (DB empty) and
                # subsequent turns (DB has prior Q&A / code-gen entries).
                # The information_request branch already built it for this turn when
                # it prefetched the analysis; reuse it so the DB read and the string
                # the LLM saw stay identical.
                if prefetched_user_text_with_history is not None:
                    user_text_with_history = prefetched_user_text_with_history
                else:
                    user_text_with_history = self._build_user_text_with_history(session_id, user_text)
                logger.info(f"[AGENT_CHAIN] History built: {len(user_text_with_history)} chars for session {session_id}")

                # ══════════════════════════════════════════════════════════════════
                # FAST-PATH GATE — skip unified chain when waiting for user confirm
                # Applies to BOTH: awaiting_step_plan_confirm AND awaiting_confirm.
                # Cache was saved by _save_confirm_cache() on the previous turn.
                # ══════════════════════════════════════════════════════════════════
                _fast_state = self._get_session_state(session_id)
                _awaiting_step_plan = _fast_state.get('awaiting_step_plan_confirm', False)
                _awaiting_desc      = _fast_state.get('awaiting_confirm', False)
                _cached_snapshot    = _fast_state.get('cached_unified_obj_snapshot')
                _cached_json        = _fast_state.get('cached_raw_unified_json', '')
                _cached_ctx         = _fast_state.get('cached_retrieved_context', '')
                _cached_expanded    = _fast_state.get('cached_expanded_user_text', '')
                _cached_perf_calc   = _fast_state.get('cached_perf_calc_result')

                _can_fast_path = (
                    (_awaiting_step_plan or _awaiting_desc)
                    and _cached_snapshot is not None
                    and _cached_json
                )

                if _can_fast_path:
                    # ── Restore unified output from cache ─────────────────────
                    # AnalysisAndParameterCheckOutput imported at top of file
                    unified_output_obj           = AnalysisAndParameterCheckOutput(**_cached_snapshot)
                    raw_unified_json             = _cached_json
                    retrieved_context_for_code_gen = _cached_ctx
                    expanded_user_text           = _cached_expanded or user_text
                    if _cached_perf_calc and not _fast_state.get('perf_calc_result'):
                        self._update_session_state(session_id, perf_calc_result=_cached_perf_calc)

                    # Inject user's current intent into the cached object
                    # so downstream CASE A / CASE 1 gates can read it correctly.
                    # detect_confirm_intent is a lightweight keyword check — no LLM call.
                    from src.utils.language_utils import detect_confirm_intent
                    _intent = detect_confirm_intent(user_text)
                    unified_output_obj.confirm_intent_detected = _intent

                    logger.info(
                        f"[FAST_PATH] ✅ Using cached unified result "
                        f"(awaiting_step_plan={_awaiting_step_plan}, awaiting_desc={_awaiting_desc}) "
                        f"| confirm_intent={_intent} | session={session_id}"
                    )
                    print(
                        f"[FAST_PATH] ✅ Skipping unified chain — using cache "
                        f"| step_plan={_awaiting_step_plan} desc={_awaiting_desc} intent={_intent}"
                    )

                    yield {
                        "step": "analysis",
                        "status": "Analysis completed (fast-path).",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 20,
                        "design_type": unified_output_obj.design_type,
                        "assembly_warning": unified_output_obj.assembly_warning
                    }
                    yield {
                        "step": "parameters",
                        "status": "Parameters validated (fast-path).",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 40
                    }

                elif prefetched_unified_result is not None:
                    # ── Reuse path: the information_request branch already ran the
                    # full unified + RAG analysis for this turn (it needed the same
                    # result to decide whether the request was pure info). Running
                    # it again here cost a second expansion, a second retrieval with
                    # its reranks, and a second expert-tier unified + DFM pair.
                    logger.info(f"[AGENT_CHAIN] ♻️ Reusing unified result from information_request analysis | session={session_id}")
                    unified_chain_result = prefetched_unified_result

                    unified_output_obj             = unified_chain_result["unified_output_obj"]
                    raw_unified_json               = unified_chain_result["raw_unified_json"]
                    retrieved_context_for_code_gen = unified_chain_result["retrieved_context_for_code_gen"]
                    expanded_user_text             = unified_chain_result.get("expanded_user_text", user_text)

                    if unified_chain_result.get("_needs_perf_param_chain"):
                        unified_output_obj = await self._run_perforated_param_chain(
                            unified_output_obj, user_text_with_history, session_id
                        )

                    yield {
                        "step": "analysis",
                        "status": "Analysis completed.",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 20,
                        "design_type": unified_output_obj.design_type,
                        "assembly_warning": unified_output_obj.assembly_warning
                    }
                    yield {
                        "step": "parameters",
                        "status": "Parameters validated.",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 40
                    }

                else:
                    # ── Normal path: run unified chain + RAG ──────────────────
                    logger.info(f"[AGENT_CHAIN] Invoking unified processing chain with full history for session {session_id}")

                    # ── KEEP-ALIVE: emit intermediate progress before the heavy step ──
                    # _invoke_unified_with_rag() contains 4-5 sequential LLM calls
                    # (expand_query → RAG → asyncio.gather(unified+dfm) → lang_detect)
                    # which can take 20-55s. Without this yield the SSE stream is
                    # completely silent → browser/proxy interprets as connection lost.
                    yield {
                        "step": "analysis",
                        "status": "Analyzing requirements and retrieving knowledge base...",
                        "is_complete": False,
                        "is_active": True,
                        "progress": 15,
                    }

                    chain_start_time = time.time()
                    unified_chain_result = await self._invoke_unified_with_rag(
                        user_text=user_text_with_history,
                        session_id=session_id,
                        latest_requirements_for_guidance=state['latest_requirements'],
                        material=state.get('material_choice', '')
                    )
                    chain_duration = time.time() - chain_start_time
                    logger.info(f"[AGENT_CHAIN] Chain processing completed in {chain_duration:.2f}s | session={session_id}")

                    unified_output_obj             = unified_chain_result["unified_output_obj"]
                    raw_unified_json               = unified_chain_result["raw_unified_json"]
                    retrieved_context_for_code_gen = unified_chain_result["retrieved_context_for_code_gen"]
                    expanded_user_text             = unified_chain_result.get("expanded_user_text", user_text)

                    # Perforated Sheet: same gate as _unified_request_processor — was missing here,
                    # so SSE/realtime never populated perf_calc_result → empty perf_info in description_confirm.
                    if unified_chain_result.get("_needs_perf_param_chain"):
                        unified_output_obj = await self._run_perforated_param_chain(
                            unified_output_obj, user_text_with_history, session_id
                        )

                    print(f"\n[SUCCESS] Unified analysis and parameter check successful for session {session_id}:")
                    print(f"Parsed Output: {json.dumps(unified_output_obj.dict(), indent=2)}")

                    yield {
                        "step": "analysis",
                        "status": "Analysis completed.",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 20,
                        "design_type": unified_output_obj.design_type,
                        "assembly_warning": unified_output_obj.assembly_warning
                    }
                    yield {
                        "step": "parameters",
                        "status": "Parameters validated.",
                        "is_complete": True,
                        "is_active": False,
                        "progress": 40
                    }

                self._update_session_state(session_id, latest_requirements=unified_output_obj)

                if unified_output_obj.title == "Unable to parse requirements":
                    logger.warning(f"[AGENT_PARSE] Unable to parse requirements for session {session_id}")
                    error_details = "\n".join(unified_output_obj.questions) if unified_output_obj.questions else "Unable to parse requirements"
                    result = {
                        "code": None,
                        "message": f"I had trouble understanding your request. Could you please try rephrasing it? Details: {error_details}",
                        "explanation": error_details
                    }
                    yield {"final_result": result}
                    return

                logger.info(f"[AGENT_CHAIN] Missing info: {unified_output_obj.missing_info}")
                logger.info(f"[AGENT_CHAIN] Questions count: {len(unified_output_obj.questions) if unified_output_obj.questions else 0}")

                # ── Extract flags từ unified output ───────────────────────────────
                skip_questions  = unified_output_obj.skip_questions_requested
                override_intent = unified_output_obj.override_intent_detected
                max_attempts_reached = (
                    len(state.get('pending_questions', [])) >= self.MAX_QUESTION_ATTEMPTS
                )

                logger.info(f"[AGENT_CHAIN] missing_info={unified_output_obj.missing_info}, "
                            f"questions={len(unified_output_obj.questions) if unified_output_obj.questions else 0}, "
                            f"skip={skip_questions}, override={override_intent}, "
                            f"max_attempts={max_attempts_reached}")

                
                if unified_output_obj.title == "Conversational Response":
                    logger.info(f"[AGENT_CONVERSATIONAL] Detected conversational response for session {session_id}")
                    # parameters(40%) already emitted via auto-cascade above
                    response_msg = "\n".join(unified_output_obj.questions) if unified_output_obj.questions else "Conversational response provided"
                    yield {"final_result": {"code": None, "message": response_msg, "explanation": response_msg}}
                    return

                # ════════════════════════════════════════════════════════════════════
                # SINGLE DECISION ENGINE -  _make_decision()  PATH A & PATH B
                # ════════════════════════════════════════════════════════════════════
                decision = self._make_decision(
                    unified_output_obj,
                    skip_questions=skip_questions,
                    override_intent=override_intent,
                    max_attempts_reached=max_attempts_reached,
                )
                logger.info(f"[AGENT_DECISION] → {decision.action} | session={session_id}")

                # ── ASK_QUESTIONS ───────────────────────────────────────────────────
                if decision.action == self.CADDecision.ASK_QUESTIONS:
                    logger.info(f"[AGENT_QUESTIONS] Missing info detected, preparing questions for session {session_id}")
                    # parameters(40%) already emitted via auto-cascade above

                    # LLM already filters previously answered questions from full DB history.
                    # No need for manual keyword matching against session state.
                    pending_questions = unified_output_obj.questions or []

                    if pending_questions:
                        logger.info(f"[AGENT_QUESTIONS] {len(pending_questions)} questions to ask for session {session_id}")
                        self._update_session_state(session_id, pending_questions=pending_questions)
                        result = self._create_smart_response(unified_output_obj, user_text_with_history)
                        logger.info(f"[AGENT_QUESTIONS] Returning questions to user for session {session_id}")
                        yield {"final_result": result}
                        return

                # ── RETURN_INFO ─────────────────────────────────────────────────────
                elif decision.action == self.CADDecision.RETURN_INFO:
                    logger.info(f"[AGENT_INFO_RESPONSE] missing_info=False, questions present → returning info for session {session_id}")
                    # parameters(40%) already emitted via auto-cascade above
                    yield {"final_result": {"code": None, "message": decision.payload["info"], "explanation": decision.payload["info"]}}
                    return

                # ── GENERATE_CODE: check confirm gate TRƯỚC khi gen code ────────
                # Retrieve per-session confirm state (multi-user safe)
                confirm_state   = self._get_session_state(session_id)
                confirm_count   = confirm_state.get('confirm_count', 0)
                awaiting_confirm = confirm_state.get('awaiting_confirm', False)
                confirmed_description = confirm_state.get('confirmed_description', '')

                # CASE 1: User just confirmed (said "yes"/"oui" to a 📋 message)
                if awaiting_confirm and unified_output_obj.confirm_intent_detected:
                    logger.info(f"[CONFIRM] ✅ User confirmed description (round {confirm_count}) | session={session_id}")
                    self._update_session_state(session_id, awaiting_confirm=False, confirm_count=0)
                    if confirmed_description:
                        unified_output_obj.description = confirmed_description
                        logger.info(f"[CONFIRM] Using confirmed_description ({len(confirmed_description)} chars)")
                    # parameters(40%) already emitted via auto-cascade above
                    # Fall through to code generation below

                else:
                    # ══════════════════════════════════════════════════════════════
                    # STEP PLAN / CONFIRM GATE — intent-based
                    # ══════════════════════════════════════════════════════════════
                    awaiting_step_plan_confirm = confirm_state.get('awaiting_step_plan_confirm', False)

                    print(f"\n{'='*60}")
                    print(f"[FLOW_STATE] awaiting_step_plan_confirm = {awaiting_step_plan_confirm}")
                    print(f"[FLOW_STATE] confirm_intent_detected    = {unified_output_obj.confirm_intent_detected}")
                    print(f"[FLOW_STATE] step_by_step_requested     = {unified_output_obj.step_by_step_requested}")
                    print(f"[FLOW_STATE] complexity_level           = {unified_output_obj.complexity_level}")
                    print(f"{'='*60}\n")

                    # ── GATE 1: User replied to step plan (YES or NO) → both → Confirm ──
                    if awaiting_step_plan_confirm:
                        logger.info(f"[STEP_PLAN] User replied to plan (YES/NO → Confirm) | session={session_id}")
                        print(f"[STEP_PLAN] User replied to step plan → proceeding to Description Confirm")
                        self._update_session_state(session_id, awaiting_step_plan_confirm=False)
                        # Fall through to Description Confirm below ↓

                    # ── GATE 2: Unified detected explicit step-by-step request → Run Step Planner ──
                    elif unified_output_obj.step_by_step_requested:
                        logger.info(f"[STEP_PLAN] 🔧 User requested step plan → Running Step Planner | session={session_id}")
                        print(f"[STEP_PLAN] 🔧 step_by_step_requested=True → firing Step Planner chain...")

                        _total, _user_msg = await self._run_step_planner(
                            session_id, unified_output_obj, raw_unified_json,
                            retrieved_context_for_code_gen, expanded_user_text,
                            empty_steps_text="  (aucune étape)",
                        )

                        yield {"final_result": {
                            "code":        None,
                            "message":     _user_msg,
                            "explanation": f"Step plan ({_total} steps)"
                        }}
                        return

                    # ── Normal confirm flow (after GATE 1 fall-through, or no step plan) ──
                    # NOTE: check user_text_with_history (not description, which is always empty here)
                    _user_text_lower = (user_text_with_history or "").lower()
                    _has_operations = any(kw in _user_text_lower for kw in [
                        "hole", "trou", "trous", "perçage", "percage",
                        "bend", "pli", "plis", "pliage",
                        "cut", "découpe", "decoupe", "slot", "rainure",
                        "fillet", "congé", "conge", "chamfer", "chanfrein",
                        "countersink", "fraisage", "taraudage", "tapping",
                        "notch", "pocket", "embossing",
                    ])

                    # skip_confirm: only when user explicitly requests skip OR max attempts reached.
                    # complexity_level==1 no longer skips confirm.
                    skip_confirm = (
                        (unified_output_obj.skip_questions_requested and not unified_output_obj.override_intent_detected)
                        or confirm_count >= self.MAX_CONFIRM_ATTEMPTS
                    )

                    if not skip_confirm:
                        self._save_confirm_cache(
                            session_id,
                            unified_output_obj,
                            raw_unified_json,
                            retrieved_context_for_code_gen,
                            expanded_user_text,
                        )
                        logger.info(f"[CONFIRM] Triggering confirm chain | complexity={unified_output_obj.complexity_level} | session={session_id}")
                        confirm_result = await self._run_description_confirm(
                            unified_output_obj, user_text_with_history, session_id
                        )
                        yield {"final_result": confirm_result}
                        return

                    # skip_confirm=True → log reason and fall through to code gen
                    if confirm_count >= self.MAX_CONFIRM_ATTEMPTS:
                        logger.info(f"[CONFIRM] Max rounds ({self.MAX_CONFIRM_ATTEMPTS}) reached → auto generate | session={session_id}")
                        if confirmed_description:
                            unified_output_obj.description = confirmed_description
                    else:
                        logger.info(f"[CONFIRM] Skipped (complexity={unified_output_obj.complexity_level}, ops={_has_operations}) | session={session_id}")
                    self._update_session_state(session_id, awaiting_confirm=False, confirm_count=0, confirmed_description='')






                # ── Fall through: generate code ──────────────────────────────────

                self._update_session_state(session_id, pending_questions=[])

                logger.info(f"[AGENT_STEP] Starting code generation step for session {session_id}")
                yield {
                    "step": "generation_code",
                    "status": "Generating FreeCAD Python code...",
                    "is_complete": False,
                    "is_active": True,
                    "progress": 50
                }

                await asyncio.sleep(0.5)

                logger.info(f"[AGENT_CODEGEN] Calling generate_code_from_requirements for session {session_id}")
                codegen_start_time = time.time()
                result = await self.generate_code_from_requirements(
                    unified_output_obj,
                    raw_unified_json,
                    retrieved_context_for_code_gen,
                    session_id=session_id,
                    save_files=False,
                    expanded_user_text=expanded_user_text
                )

                codegen_duration = time.time() - codegen_start_time
                logger.info(f"[AGENT_CODEGEN] Code generation completed in {codegen_duration:.2f}s for session {session_id}")

                yield {
                    "step": "generation_code",
                    "status": "Code generation completed.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 70
                }

                if result.get("error"):
                    logger.error(f"[AGENT_CODEGEN] Code generation error for session {session_id}: {result.get('error')}")
                    yield {"final_result": result}
                    return

                logger.info(f"[AGENT_STEP] Starting export step for session {session_id}")
                export_start_time = time.time()
                yield {
                    "step": "export",
                    "status": "Exporting files and executing FreeCAD...",
                    "is_complete": False,
                    "is_active": True,
                    "progress": 80,
                    **self._build_export_eta_payload(session_id, export_start_time),
                }

                await asyncio.sleep(0.3)

                try:
                    logger.info(f"[AGENT_EXPORT] Starting file operations for session {session_id}")
                    generated_code = result.get("code")
                    if generated_code:
                        export_task = asyncio.create_task(self.save_outputs(
                            generated_code,
                            unified_output_obj,
                            user_text=user_text,
                            session_id=session_id,
                            priority=priority
                        ))
                        while True:
                            try:
                                obj_path, step_path, pdf_path = await asyncio.wait_for(
                                    asyncio.shield(export_task),
                                    timeout=15,
                                )
                                break
                            except asyncio.TimeoutError:
                                yield {
                                    "step": "export",
                                    "status": "Exporting files and executing FreeCAD...",
                                    "is_complete": False,
                                    "is_active": True,
                                    "progress": 80,
                                    **self._build_export_eta_payload(session_id, export_start_time),
                                }
                        export_duration = time.time() - export_start_time
                        logger.info(f"[AGENT_EXPORT] File operations completed in {export_duration:.2f}s for session {session_id}")

                        result.update({
                            "obj_path": obj_path,
                            "step_path": step_path,
                            "pdf_path": pdf_path,
                        })
                except Exception as e:
                    logger.error(f"[AGENT_EXPORT] Error during file export for session {session_id}: {str(e)}")
                    result["error"] = str(e)
                    # CRITICAL: Clear code so downstream logic (has_code check, _handle_export_paths)
                    # does NOT mistake this as a successful generation and serve old cached files.
                    result["code"] = None

                yield {
                    "step": "export",
                    "status": "Export completed.",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 95,
                    **self._build_export_eta_payload(session_id, export_start_time),
                }

                logger.info(f"[AGENT_STEP] Starting completion step for session {session_id}")
                yield {
                    "step": "complete",
                    "status": "All processing completed!",
                    "is_complete": True,
                    "is_active": False,
                    "progress": 100
                }

                total_duration = time.time() - start_time
                logger.info(f"[AGENT_PROGRESS] Process completed in {total_duration:.2f}s for session {session_id}")
                yield {"final_result": result}
                return

        except Exception as e:
            total_duration = time.time() - start_time
            logger.error(f"[AGENT_PROGRESS] Error during process_request_with_progress after {total_duration:.2f}s for session {session_id}: {str(e)}")
            logger.error(f"[AGENT_PROGRESS] Traceback: {traceback.format_exc()}")
            result = {"error": f"Error processing request: {str(e)}", "code": None}
            yield {"final_result": result}
            return
        finally:
            # Log cost for THIS request turn only (not session total).
            # _log_request_cost uses start_request() snapshot → never suppressed,
            # always shows correct per-turn breakdown.
            self._log_request_cost(session_id, label="process_request_with_progress")

