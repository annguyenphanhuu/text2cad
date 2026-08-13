import json
import re
import logging
import os
from datetime import datetime
from langchain_core.runnables import RunnableLambda, RunnableParallel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

# Template logging paths
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
UNIFIED_TEMPLATE_LOG = os.path.join(LOGS_DIR, "unified_analysis_and_parameter_check_template.log")
CODE_GEN_TEMPLATE_LOG = os.path.join(LOGS_DIR, "code_generation_template.log")
DFM_VALIDATION_LOG = os.path.join(LOGS_DIR, "dfm_validation_template.log")
CODE_EDIT_TEMPLATE_LOG = os.path.join(LOGS_DIR, "code_editing_template.log")

# Ensure logs directory exists
os.makedirs(LOGS_DIR, exist_ok=True)

def log_template_io(log_file, session_id, template_name, inputs, output, error=None):
    """Log template inputs and outputs to dedicated log file"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        separator = "=" * 100
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{separator}\n")
            f.write(f"[{timestamp}] Template: {template_name} | Session: {session_id}\n")
            f.write(f"{separator}\n\n")
            
            # Log inputs - FULL CONTENT, NO TRUNCATION
            f.write("📥 INPUTS:\n")
            f.write("-" * 80 + "\n")
            for key, value in inputs.items():
                f.write(f"{key}:\n{str(value)}\n\n")
            
            # Log output or error - FULL CONTENT, NO TRUNCATION
            if error:
                f.write("❌ ERROR:\n")
                f.write("-" * 80 + "\n")
                f.write(f"{error}\n\n")
            else:
                f.write("📤 OUTPUT:\n")
                f.write("-" * 80 + "\n")
                f.write(f"{str(output)}\n\n")
            
            f.write(f"{separator}\n\n")
    except Exception as e:
        logger.error(f"Failed to log template I/O: {e}")

from .templates import (
    greeting_classification_template,
    unified_analysis_and_parameter_check_template,
    perforated_parameter_extraction_template,
    dfm_rule_validation_template,
    code_generation_template,
    code_editing_template,
    step_planner_template,
    build_confirm_template,
    shape_change_detector_template,
    edit_summary_template,
)
from .agent_utils import (
    parse_unified_analysis, clean_code, detect_detailed_explanation_request
)
from .models import DFMValidationOutput


def parse_greeting_classification(raw_output):
    """Parse greeting classification output with enhanced language feedback detection"""
    try:
        # Extract JSON from the output
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', raw_output, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            result = json.loads(json_str)
            return result
        else:
            # Try to parse the entire output as JSON
            result = json.loads(raw_output.strip())
            return result
    except Exception as e:
        print(f"[ERROR] Failed to parse greeting classification: {e}")
        # Default to CAD request if parsing fails
        return {
            "classification": "cad_request",
            "confidence": 0.5,
            "response": ""
        }


# Removed enhanced_greeting_classification function - now using pure AI-based classification





def create_greeting_classification_chain(default_llm):
    """Create the greeting classification chain using pure AI-based classification"""

    # Built once at chain-creation time rather than per invocation.
    greeting_prompt = ChatPromptTemplate.from_template(greeting_classification_template)

    async def ai_only_classification(inputs):
        """
        Pure AI-based classification - no rule-based fallback.

        Must be `async` + `ainvoke`. As a sync function this ran inside
        LangChain's thread executor, which (a) broke the `get_openai_callback`
        contextvar so the call's tokens were logged as 0 and its cost went
        untracked, and (b) blocked a worker thread for the whole round-trip.
        """
        user_text = inputs.get("user_text", "")

        # Only log first 100 chars to avoid cluttering logs with web content
        user_text_preview = user_text[:100] + "..." if len(user_text) > 100 else user_text
        print(f"[AI_CLASSIFICATION] Using pure AI classification for: '{user_text_preview}'")

        ai_chain = (
            greeting_prompt
            | default_llm
            | StrOutputParser()
            | RunnableLambda(parse_greeting_classification)
        )

        try:
            ai_result = await ai_chain.ainvoke(inputs)
            print(f"[AI_CLASSIFICATION] Result: {ai_result}")
            return ai_result
        except Exception as e:
            print(f"[AI_CLASSIFICATION] Error: {e}, using fallback response")
            # Simple fallback if AI completely fails
            return {
                "classification": "cad_request",
                "confidence": 0.5,
                "response": ""
            }

    return RunnableLambda(ai_only_classification)


def create_unified_processing_chain(expert_llm):
    """Create the unified processing chain for analysis and parameter checking
    
    NOTE: This chain NO LONGER retrieves RAG internally.
    Rules context must be provided from external single RAG call.
    """

    unified_analysis_prompt = ChatPromptTemplate.from_template(unified_analysis_and_parameter_check_template)

    async def process_and_log_context_async(x):
        # Use original user text directly without translation
        user_text = x["user_text"]
        session_id = x.get("session_id", "unknown")
        
        # Preview user text for logging (first 100 chars)
        user_text_preview = user_text[:100] + "..." if len(user_text) > 100 else user_text

        examples_context = x.get("examples_context", "")  # Pass through for code gen (NOT used in unified analysis)
        
        # Session prefix for logging
        session_prefix = f"{session_id[:15]}..." if len(session_id) > 15 else session_id
        
        print(f"[UNIFIED] Session: {session_prefix} | (examples: {len(examples_context)} chars passed to code gen)")

        print(f"[UNIFIED_ANALYSIS] 🤖 Sending to LLM...")
        
        return {
            "user_text": user_text,  # Contains full conversation history (original request + Q&A)
            "examples_context": examples_context,  # Pass through for code gen (NOT used in unified template)
            "detailed_explanation_requested": detect_detailed_explanation_request(user_text),
            "session_id": session_id,  # Pass session_id for logging
            "material": x.get("material", ""),  # Material choice for template
            "user_language": x.get("user_language", "English")  # User language for template
        }

    def parse_and_log_unified_result(x):
        """Parse unified analysis result and log completion"""
        print(f"[UNIFIED_ANALYSIS] ✅ LLM response received, parsing...")
        
        # Print the raw JSON output for debugging
        raw_json = x["unified_analysis"]
        template_inputs = x["template_inputs"]
        
        # Log template I/O
        try:
            session_id = template_inputs.get("session_id", "unknown")
            log_inputs = {
                "user_text": template_inputs.get("user_text", ""),
                "detailed_explanation_requested": template_inputs.get("detailed_explanation_requested", False),
                "material": template_inputs.get("material", "")
            }
            log_template_io(
                UNIFIED_TEMPLATE_LOG,
                session_id,
                "unified_analysis_and_parameter_check_template",
                log_inputs,
                raw_json
            )
        except Exception as log_error:
            logger.error(f"Failed to log unified template I/O: {log_error}")
        
        # The raw JSON is not returned: it is fully captured by parse_unified_analysis
        # into unified_output_obj and written verbatim to UNIFIED_TEMPLATE_LOG above.
        # No prompt downstream consumes it.
        result = {
            "unified_output_obj": parse_unified_analysis(raw_json, template_inputs["user_text"]),
            "retrieved_context_for_code_gen": template_inputs.get("examples_context", "")  # Examples for code gen
        }
        print(f"[UNIFIED_ANALYSIS] ✅ Analysis complete - parsed successfully\n")
        return result

    chain = RunnableLambda(process_and_log_context_async) | RunnableParallel(
        {
            "unified_analysis": unified_analysis_prompt | expert_llm | StrOutputParser(),
            "template_inputs": lambda x: x
        }
    ) | RunnableLambda(parse_and_log_unified_result)

    return chain


def create_dfm_validation_chain(expert_llm):
    """Create the DFM rule validation chain.
    
    This chain validates user parameters against manufacturing rules.
    It runs IN PARALLEL with the unified processing chain.
    
    Input:  { user_text, retrieved_context (rules), material, session_id }
    Output: DFMValidationOutput
    """
    dfm_prompt = ChatPromptTemplate.from_template(dfm_rule_validation_template)

    def parse_dfm_result(raw_json: str) -> DFMValidationOutput:
        """Parse DFM validation JSON output into Pydantic model."""
        try:
            # Extract JSON from markdown if needed
            if "```json" in raw_json:
                match = re.search(r'```json\s*(.*?)\s*```', raw_json, re.DOTALL)
                if match:
                    raw_json = match.group(1)
            
            # Try to find JSON object
            json_match = re.search(r'\{.*\}', raw_json, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return DFMValidationOutput(**data)
            
            data = json.loads(raw_json.strip())
            return DFMValidationOutput(**data)
        except Exception as e:
            logger.error(f"[DFM_VALIDATION] Failed to parse output: {e}")
            logger.error(f"[DFM_VALIDATION] Raw output: {raw_json[:500]}")
            # Return safe default (no violations) on parse failure
            return DFMValidationOutput()

    async def dfm_chain_ainvoke(inputs: dict) -> DFMValidationOutput:
        """Per-invocation wrapper for DFM validation (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")
        
        chain_input = {
            "user_text": inputs.get("user_text", ""),
            "retrieved_context": inputs.get("retrieved_context", ""),
            "material": inputs.get("material", ""),
            "user_language": inputs.get("user_language", "English"),  # ✅ Forward pre-detected language to template
        }
        
        # Log inputs
        log_inputs = {
            "user_text": chain_input["user_text"],
            "retrieved_context": chain_input["retrieved_context"],
            "material": chain_input["material"],
            "detected_language": chain_input["user_language"],  # ← show which language was detected
        }
        
        try:
            inner_chain = (
                dfm_prompt
                | expert_llm
                | StrOutputParser()
            )
            raw_output = await inner_chain.ainvoke(chain_input)
            
            # Log template I/O
            try:
                log_template_io(
                    DFM_VALIDATION_LOG,
                    session_id,
                    "dfm_rule_validation_template",
                    log_inputs,
                    raw_output
                )
            except Exception as log_err:
                logger.error(f"[DFM_VALIDATION] Failed to log: {log_err}")
            
            result = parse_dfm_result(raw_output)
            logger.info(
                f"[DFM_VALIDATION] ✅ Complete | session={session_id} | "
                f"violations={len(result.violations)} | "
                f"has_violations={result.has_violations} | "
                f"override={result.override_intent_detected} | "
                f"thickness_warning={'yes' if result.thickness_warning else 'no'}"
            )
            return result
        except Exception as e:
            logger.error(f"[DFM_VALIDATION] Chain error: {e}")
            log_template_io(DFM_VALIDATION_LOG, session_id, "dfm_rule_validation_template", log_inputs, None, error=str(e))
            return DFMValidationOutput()  # Safe default

    return RunnableLambda(dfm_chain_ainvoke)


def create_description_confirm_chain(default_llm):
    """Create the description confirm chain.

    Runs ONCE after unified analysis completes (missing_info=false),
    BEFORE code generation. Produces a high-quality technical description
    and a user-facing confirm message.

    Input:  { user_text, validated_params, confirm_round, user_language }
    Output: { final_description, confirm_message }

    Multi-user safe: all inputs are per-invocation, no shared state.
    """
    CONFIRM_TEMPLATE_LOG = os.path.join(LOGS_DIR, "description_confirm_template.log")

    def parse_confirm_output(raw: str) -> dict:
        """Parse JSON output from description confirm chain."""
        try:
            # Try direct JSON parse
            result = json.loads(raw.strip())
            if "final_description" in result and "confirm_message" in result:
                return result
        except Exception:
            pass
        # Try extracting JSON block
        try:
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if "final_description" in result:
                    return result
        except Exception:
            pass
        # Fallback: treat raw output as both description and confirm message
        logger.warning("[DESC_CONFIRM] Failed to parse JSON, using raw output as fallback")
        fallback_msg = f"\ud83d\udccb **Description:**\n{raw}\n\n\u2705 *Reply yes/oui/ok to generate, or add more details.*"
        return {
            "final_description": raw,
            "confirm_message": fallback_msg
        }

    # NOTE: No shared closure dict — log context is per-coroutine (multi-user safe).
    # The actual logging happens inside confirm_chain_ainvoke via make_clean_and_log.

    def make_clean_and_log(session_id_ref: list, log_inputs_ref: list):
        """Returns a closure that captures log context via mutable list (per-invocation)."""
        def inner(raw: str) -> dict:
            result = parse_confirm_output(raw)
            try:
                if log_inputs_ref and session_id_ref:
                    session_id = session_id_ref[0]
                    log_inputs = log_inputs_ref[0]
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    separator = "=" * 100
                    try:
                        with open(CONFIRM_TEMPLATE_LOG, "a", encoding="utf-8") as f:
                            f.write(f"\n{separator}\n")
                            f.write(f"[{timestamp}] Template: description_confirm_template | Session: {session_id}\n")
                            f.write(f"{separator}\n\n")
                            # Inputs
                            f.write("📥 INPUTS:\n")
                            f.write("-" * 80 + "\n")
                            for key, value in log_inputs.items():
                                f.write(f"{key}:\n{str(value)}\n\n")
                            # Outputs — rendered directly, no raw JSON
                            f.write("📤 OUTPUT:\n")
                            f.write("-" * 80 + "\n")
                            f.write("final_description:\n")
                            f.write(result.get("final_description", "").replace("\\n", "\n") + "\n\n")
                            f.write("confirm_message:\n")
                            f.write(result.get("confirm_message", "").replace("\\n", "\n") + "\n\n")
                            f.write(f"{separator}\n\n")
                    except Exception as e:
                        logger.error(f"[DESC_CONFIRM] Failed to write log: {e}")
            except Exception as e:
                logger.error(f"[DESC_CONFIRM] Failed to log: {e}")
            return result
        return inner

    # Build the chain using a wrapper that captures log context per-invocation
    async def confirm_chain_ainvoke(inputs: dict) -> dict:
        """Invoke description confirm chain with per-invocation log context (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")
        shape_type = inputs.get("shape_type", "unknown")
        log_inputs = {
            "user_text": inputs.get("user_text", ""),
            "confirm_round": inputs.get("confirm_round", 1),
            "user_language": inputs.get("user_language", "English"),
            "shape_type": shape_type,
            "perf_info": inputs.get("perf_info", ""),   # ← forward pre-computed % vide
        }
        # These refs are local to this coroutine — no shared state
        session_id_ref = [session_id]
        log_inputs_ref = [log_inputs]

        clean_and_log = make_clean_and_log(session_id_ref, log_inputs_ref)

        # ── Build shape-specific template per-request (Option A) ──────────────
        template_str = build_confirm_template(shape_type)
        logger.info(f"[DESC_CONFIRM] shape_type={shape_type!r} → template built ({len(template_str)} chars)")
        confirm_prompt = ChatPromptTemplate.from_template(template_str)
        # ─────────────────────────────────────────────────────────────────────

        inner_chain = (
            confirm_prompt
            | default_llm
            | StrOutputParser()
            | RunnableLambda(clean_and_log)
        )
        return await inner_chain.ainvoke(inputs)

    # Wrap as a RunnableLambda so it integrates with ainvoke_with_cost_tracking
    chain = RunnableLambda(confirm_chain_ainvoke)
    return chain


def create_step_planner_chain(default_llm):
    """Create the step planner chain.

    Analyzes complex CAD requests and generates a step-by-step build plan.
    Runs BEFORE description_confirm when complexity_level >= threshold.

    Input:  { description, complexity_level, user_language, session_id }
    Output: { steps, total_steps, plan_summary, user_message }

    Multi-user safe: all inputs are per-invocation, no shared state.
    """
    step_planner_prompt = ChatPromptTemplate.from_template(step_planner_template)

    STEP_PLANNER_LOG = os.path.join(LOGS_DIR, "step_planner_template.log")

    def parse_step_plan_output(raw: str) -> dict:
        """Parse JSON output from step planner chain."""
        try:
            result = json.loads(raw.strip())
            if "steps" in result and "user_message" in result:
                return result
        except Exception:
            pass
        try:
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if "steps" in result:
                    return result
        except Exception:
            pass
        # Fallback: return empty plan — caller will handle
        logger.warning("[STEP_PLANNER] Failed to parse JSON output, using fallback")
        return {
            "steps": [],
            "total_steps": 0,
            "plan_summary": "",
            "user_message": ""
        }

    async def step_planner_ainvoke(inputs: dict) -> dict:
        """Per-invocation wrapper for step planner chain (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")

        chain_input = {
            "description": inputs.get("description", ""),
            "complexity_level": inputs.get("complexity_level", 1),
            "user_language": inputs.get("user_language", "French"),
        }

        log_inputs = {
            "description": chain_input["description"],
            "complexity_level": chain_input["complexity_level"],
            "user_language": chain_input["user_language"],
        }

        try:
            inner_chain = (
                step_planner_prompt
                | default_llm
                | StrOutputParser()
            )
            raw_output = await inner_chain.ainvoke(chain_input)

            # Log template I/O
            try:
                log_template_io(
                    STEP_PLANNER_LOG,
                    session_id,
                    "step_planner_template",
                    log_inputs,
                    raw_output
                )
            except Exception as log_err:
                logger.error(f"[STEP_PLANNER] Failed to log: {log_err}")

            result = parse_step_plan_output(raw_output)
            logger.info(
                f"[STEP_PLANNER] ✅ Complete | session={session_id} | "
                f"steps={result.get('total_steps', len(result.get('steps', [])))}"
            )
            return result

        except Exception as e:
            logger.error(f"[STEP_PLANNER] Chain error: {e}")
            log_template_io(STEP_PLANNER_LOG, session_id, "step_planner_template",
                            log_inputs, None, error=str(e))
            return {"steps": [], "total_steps": 0, "plan_summary": "", "user_message": ""}

    return RunnableLambda(step_planner_ainvoke)


def create_code_generation_chain(advanced_llm):
    """Create the RAG code generation chain
    
    MULTI-USER SAFE: No shared mutable state between invocations.
    Each request gets its own context via per-invocation async wrapper.
    """

    code_generation_prompt = ChatPromptTemplate.from_template(code_generation_template)

    async def code_gen_ainvoke(inputs: dict) -> str:
        """
        Per-invocation wrapper — all context is local to this coroutine.
        This is the ONLY function that touches session_id / log_inputs,
        so there is ZERO shared state between concurrent requests.
        """
        # ── 1. Extract per-invocation context ──────────────────────────
        session_id = inputs.get("session_id", "unknown")
        design_req = inputs.get("design_requirements_obj")

        import re as _re
        if design_req is not None:
            _description = design_req.description or ""
            # complexity_level=1 → unified template skips description → fallback to raw user_text
            user_text_for_chain = _description if _description.strip() else inputs.get("user_text", "")
            raw_title = getattr(design_req, "title", "") or ""
            sanitized_title = (
                ''.join(c for c in _re.sub(r'[^\w\s-]', '', raw_title).strip()
                         if ord(c) < 128).replace(' ', '_')
                or "generated_cad"
            )
        else:
            user_text_for_chain = inputs.get("user_text", "")
            sanitized_title = "generated_cad"

        retrieved_context = inputs.get("retrieved_context", "")

        # ── 2. Sanitize string inputs before sending to OpenAI ─────────
        # Root cause: null bytes (\x00) and ASCII control chars (except \t \n \r)
        # are valid Python strings but produce invalid JSON at HTTP body level,
        # causing OpenAI to return: "Error code: 400 – could not parse JSON body".
        def _sanitize_for_openai(text: str, field_name: str) -> str:
            if not text:
                return text
            # Remove: null byte + all C0 control chars except \t (9), \n (10), \r (13)
            cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            removed = len(text) - len(cleaned)
            if removed > 0:
                logger.warning(
                    f"[CODE_GEN_SANITIZE] Removed {removed} invalid control char(s) "
                    f"from '{field_name}' (session={session_id}). "
                    f"This was likely causing the OpenAI 400 JSON body error."
                )
            return cleaned

        retrieved_context   = _sanitize_for_openai(retrieved_context,   "retrieved_context")
        user_text_for_chain = _sanitize_for_openai(user_text_for_chain, "user_text")

        # ── 3. Build chain input (pure, no shared dict) ────────────────
        # NOTE: code_generation_template takes only retrieved_context / user_text /
        # sanitized_title. It does NOT accept the user's material choice — the
        # GeometryAnalyzer calls in its export snippet hardcode material="steel".
        chain_input = {
            "retrieved_context": retrieved_context,
            "user_text":         user_text_for_chain,
            "session_id":        session_id,
            "sanitized_title":   sanitized_title,
        }

        # Log INPUTS (before LLM call)
        log_inputs = {
            "user_text":         user_text_for_chain,
            "retrieved_context": retrieved_context,
        }

        # ── 4. Run the LLM chain ───────────────────────────────────────
        inner_chain = (
            code_generation_prompt
            | advanced_llm
            | StrOutputParser()
        )
        raw_code = await inner_chain.ainvoke(chain_input)

        # ── 5. Log AFTER output received — all vars are LOCAL ──────────
        try:
            log_template_io(
                CODE_GEN_TEMPLATE_LOG,
                session_id,          # ← local to this coroutine, never shared
                "code_generation_template",
                log_inputs,          # ← local to this coroutine
                raw_code,
            )
        except Exception as log_err:
            logger.error(f"[CODE_GEN] Failed to log template I/O: {log_err}")

        # ── 6. Clean and return ────────────────────────────────────────
        try:
            return clean_code(raw_code)
        except Exception as e:
            logger.error(f"[SAFE_CLEAN_CODE] Error cleaning code: {e}")
            return raw_code.strip() if raw_code else ""

    # Wrap as RunnableLambda so it integrates with ainvoke_with_cost_tracking
    chain = RunnableLambda(code_gen_ainvoke)
    return chain


def create_code_editing_chain(expert_llm):
    """Create the code editing chain with logging.

    MULTI-USER SAFE: All context is local to each coroutine invocation.
    Logs inputs and outputs to code_editing_template.log for debugging.
    """

    code_editing_prompt = ChatPromptTemplate.from_template(code_editing_template)

    async def code_edit_ainvoke(inputs: dict) -> str:
        """
        Per-invocation async wrapper — all context is local to this coroutine.
        Logs user_request, retrieved_context length, and generated code.
        """
        session_id = inputs.get("session_id", "unknown")

        # code_editing_template takes only these three placeholders.
        chain_input = {
            "original_code":     inputs.get("original_code", ""),
            "user_request":      inputs.get("user_request", ""),
            "retrieved_context": inputs.get("retrieved_context", ""),
        }

        # Log inputs (before LLM call)
        log_inputs = {
            "original_code":         chain_input["original_code"],
            "user_request":          chain_input["user_request"],
            "retrieved_context":     chain_input["retrieved_context"],
        }

        inner_chain = (
            code_editing_prompt
            | expert_llm
            | StrOutputParser()
        )

        try:
            raw_code = await inner_chain.ainvoke(chain_input)
        except Exception as e:
            logger.error(f"[CODE_EDIT] LLM chain error: {e}")
            log_template_io(CODE_EDIT_TEMPLATE_LOG, session_id, "code_editing_template", log_inputs, None, error=str(e))
            raise

        # Log after output received
        try:
            log_template_io(
                CODE_EDIT_TEMPLATE_LOG,
                session_id,
                "code_editing_template",
                log_inputs,
                raw_code,
            )
        except Exception as log_err:
            logger.error(f"[CODE_EDIT] Failed to log template I/O: {log_err}")

        # Clean and return
        try:
            return clean_code(raw_code)
        except Exception as e:
            logger.error(f"[CODE_EDIT] Error cleaning code: {e}")
            return raw_code.strip() if raw_code else ""

    return RunnableLambda(code_edit_ainvoke)


def create_shape_change_detector_chain(default_llm):
    """Create the shape change detector chain for edit mode.

    Lightweight classifier that determines whether an edit request
    changes the fundamental shape type of the part (e.g., bending a
    flat plate into an L-Bracket) or is a normal feature edit.

    Input:  { user_request, original_code, session_id }
    Output: { shape_change, current_shape_type, new_shape_type, merged_description, reason }

    Multi-user safe: all context is local to each coroutine invocation.
    """
    SHAPE_DETECT_LOG = os.path.join(LOGS_DIR, "shape_change_detector.log")

    shape_detect_prompt = ChatPromptTemplate.from_template(shape_change_detector_template)

    def parse_shape_change_output(raw: str) -> dict:
        """Parse JSON output from shape change detector."""
        try:
            result = json.loads(raw.strip())
            if "shape_change" in result:
                return result
        except Exception:
            pass
        try:
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if "shape_change" in result:
                    return result
        except Exception:
            pass
        # Safe default: no shape change detected
        logger.warning("[SHAPE_DETECT] Failed to parse JSON, defaulting to no shape change")
        return {
            "shape_change": False,
            "current_shape_type": "unknown",
            "new_shape_type": "unknown",
            "merged_description": "",
            "reason": "Failed to parse detector output"
        }

    async def shape_detect_ainvoke(inputs: dict) -> dict:
        """Per-invocation async wrapper (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")

        chain_input = {
            "user_request":        inputs.get("user_request", ""),
            "current_description": inputs.get("current_description", ""),
        }

        log_inputs = {
            "user_request":        chain_input["user_request"],
            "current_description": chain_input["current_description"],
        }

        try:
            inner_chain = (
                shape_detect_prompt
                | default_llm
                | StrOutputParser()
            )
            try:
                raw_output = await inner_chain.ainvoke(chain_input)
            except Exception as call_err:
                logger.warning(f"[SHAPE_DETECT] LLM call failed, retrying once: {call_err}")
                raw_output = await inner_chain.ainvoke(chain_input)

            # Log template I/O
            try:
                log_template_io(
                    SHAPE_DETECT_LOG,
                    session_id,
                    "shape_change_detector_template",
                    log_inputs,
                    raw_output,
                )
            except Exception as log_err:
                logger.error(f"[SHAPE_DETECT] Failed to log: {log_err}")

            result = parse_shape_change_output(raw_output)
            logger.info(
                f"[SHAPE_DETECT] ✅ Complete | session={session_id} | "
                f"shape_change={result.get('shape_change')} | "
                f"current={result.get('current_shape_type')} | "
                f"new={result.get('new_shape_type')} | "
                f"reason={result.get('reason', '')[:80]}"
            )
            return result

        except Exception as e:
            logger.error(f"[SHAPE_DETECT] Chain error: {e}")
            log_template_io(SHAPE_DETECT_LOG, session_id, "shape_change_detector_template",
                            log_inputs, None, error=str(e))
            # Safe default on error: no shape change
            return {
                "shape_change": False,
                "current_shape_type": "unknown",
                "new_shape_type": "unknown",
                "merged_description": "",
                "reason": f"Detector error: {e}"
            }

    return RunnableLambda(shape_detect_ainvoke)


def create_edit_summary_chain(default_llm):
    """Create the edit-running-summary chain for edit mode.

    Lightweight chain (same model tier as shape_change_detector) that folds
    a new edit request into the previous net-state description, producing
    an updated description that reflects only what currently exists
    (ADD/MOVE/RESIZE/DELETE already resolved) — used as `current_description`
    context for shape_change_detector, unified_processing_chain, and
    dfm_validation_chain during edit mode, instead of replaying the raw
    edit_history_since_confirm transcript.

    Input:  { previous_description, new_edit_request, session_id }
    Output: str (updated description)

    Multi-user safe: all context is local to each coroutine invocation.
    """
    EDIT_SUMMARY_LOG = os.path.join(LOGS_DIR, "edit_summary_chain.log")

    edit_summary_prompt = ChatPromptTemplate.from_template(edit_summary_template)

    async def edit_summary_ainvoke(inputs: dict) -> str:
        """Per-invocation async wrapper (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")

        chain_input = {
            "previous_description": inputs.get("previous_description", ""),
            "new_edit_request":     inputs.get("new_edit_request", ""),
        }

        try:
            inner_chain = (
                edit_summary_prompt
                | default_llm
                | StrOutputParser()
            )
            raw_output = await inner_chain.ainvoke(chain_input)
            updated_description = raw_output.strip()

            try:
                log_template_io(
                    EDIT_SUMMARY_LOG,
                    session_id,
                    "edit_summary_template",
                    chain_input,
                    updated_description,
                )
            except Exception as log_err:
                logger.error(f"[EDIT_SUMMARY] Failed to log: {log_err}")

            logger.info(
                f"[EDIT_SUMMARY] ✅ Complete | session={session_id} | "
                f"updated_len={len(updated_description)} chars"
            )
            return updated_description

        except Exception as e:
            logger.error(f"[EDIT_SUMMARY] Chain error: {e}")
            log_template_io(EDIT_SUMMARY_LOG, session_id, "edit_summary_template",
                            chain_input, None, error=str(e))
            # Safe fallback: keep previous description unchanged rather than losing state
            fallback = chain_input["previous_description"] or chain_input["new_edit_request"]
            return fallback

    return RunnableLambda(edit_summary_ainvoke)


# ═══════════════════════════════════════════════════════════════════════════
def create_perforated_param_chain(default_llm):
    """
    Create the Perforated Sheet Parameter Extraction chain.

    Dedicated, focused LLM chain that extracts hole shape, pitch, and
    % vide from user text (both notation style AND free natural language).
    Called ONLY when unified chain has already set shape_type="Perforated Sheet".

    Input:
        user_text     : full conversation history ([USER]/[CHATBOT] format)
        user_language : pre-detected language string (e.g. "French")
        sheet_dims    : pre-parsed sheet dims string (e.g. "L=200 W=200 T=2")

    Output dict:
        shape_notation   : "R12" | "C20" | "LR5x20" | None
        pitch_notation   : "T16" | "U40" | "T" | None
        pitch_type_known : bool
        pct_vide         : float | None
        calc_mode        : "forward" | "reverse_C" | "reverse_D" | "unknown"
        missing          : list[str]  (— empty = ready to compute)
        questions        : list[str]  (— focused questions in user's language)

    Multi-user safe: all inputs are per-invocation, no shared state.
    """
    PERF_PARAM_LOG = os.path.join(LOGS_DIR, "perforated_param_extraction.log")

    perf_prompt = ChatPromptTemplate.from_template(perforated_parameter_extraction_template)

    def parse_perf_param_output(raw: str) -> dict:
        """Parse JSON output from perforated param extraction chain."""
        try:
            result = json.loads(raw.strip())
            if "calc_mode" in result:
                return result
        except Exception:
            pass
        try:
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if "calc_mode" in result:
                    return result
        except Exception:
            pass
        logger.warning("[PERF_PARAM] Failed to parse JSON output, returning unknown state")
        return {
            "shape_notation": None,
            "pitch_notation": None,
            "pitch_type_known": False,
            "pct_vide": None,
            "calc_mode": "unknown",
            "missing": ["parse_error"],
            "questions": [],
            "sheet_length_mm": None,
            "sheet_width_mm": None,
            "sheet_thickness_mm": None,
        }

    async def perf_param_ainvoke(inputs: dict) -> dict:
        """Per-invocation async wrapper (multi-user safe)."""
        session_id = inputs.get("session_id", "unknown")

        chain_input = {
            "user_text":    inputs.get("user_text", ""),
            "user_language": inputs.get("user_language", "French"),
            "sheet_dims":   inputs.get("sheet_dims", "unknown"),
        }

        log_inputs = {
            "user_text_len":  f"{len(chain_input['user_text'])} chars",
            "user_language":  chain_input["user_language"],
            "sheet_dims":     chain_input["sheet_dims"],
        }

        try:
            inner_chain = (
                perf_prompt
                | default_llm
                | StrOutputParser()
            )
            raw_output = await inner_chain.ainvoke(chain_input)

            # Log template I/O
            try:
                log_template_io(
                    PERF_PARAM_LOG,
                    session_id,
                    "perforated_parameter_extraction_template",
                    log_inputs,
                    raw_output,
                )
            except Exception as log_err:
                logger.error(f"[PERF_PARAM] Failed to log: {log_err}")

            result = parse_perf_param_output(raw_output)
            logger.info(
                f"[PERF_PARAM] ✅ Complete | session={session_id} | "
                f"shape={result.get('shape_notation')} | "
                f"pitch={result.get('pitch_notation')} | "
                f"pct={result.get('pct_vide')} | "
                f"mode={result.get('calc_mode')} | "
                f"missing={result.get('missing')}"
            )
            return result

        except Exception as e:
            logger.error(f"[PERF_PARAM] Chain error: {e}")
            log_template_io(PERF_PARAM_LOG, session_id,
                            "perforated_parameter_extraction_template",
                            log_inputs, None, error=str(e))
            return {
                "shape_notation": None,
                "pitch_notation": None,
                "pitch_type_known": False,
                "pct_vide": None,
                "calc_mode": "unknown",
                "missing": ["chain_error"],
                "questions": [],
            }

    return RunnableLambda(perf_param_ainvoke)
