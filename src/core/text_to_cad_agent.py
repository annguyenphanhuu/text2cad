"""
TextToCADAgent: the conversation front door of the CAD pipeline.

greeting / process-question classification -> IR pipeline (src/core/ir_flow.py)
-> stub script -> remote FreeCAD worker -> STEP / OBJ / PDF copied to outputs/.
"""
import os
import re
import time
import traceback
import asyncio
from typing import Optional
import logging

from .error_codes import CadError, as_user_error, error_code_of
from .agent_chains import create_greeting_classification_chain
from src.utils.cost_tracker import CostTracker
from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

logger = logging.getLogger(__name__)

# Fixed replies for PROCESS_QUESTION classifications (see greeting_classification_template).
# These are business/ordering-process questions the chatbot cannot act on itself
# (pricing, file/PDF export) — deliberately NOT LLM-generated so the wording never
# drifts from what was agreed with the customer.
PROCESS_QUESTION_RESPONSES = {
    "pricing": (
        "To get a price for your part, please download the file and create a "
        "new quote."
    ),
    "file_export": (
        "To get the file and/or the PDF, you need to download your file."
    ),
}
PROCESS_QUESTION_DEFAULT_RESPONSE = (
    "This request is not about CAD modelling. Please download your file to "
    "continue (quote, PDF, etc.)."
)


def get_process_question_response(sub_type: Optional[str]) -> str:
    return PROCESS_QUESTION_RESPONSES.get(sub_type, PROCESS_QUESTION_DEFAULT_RESPONSE)


class EnhancedFaceProcessor:
    """Enhanced face identification and processing system for AI-driven face editing."""

    def __init__(self):
        self.face_pattern = re.compile(
            r'Face Selection: ID\[(.*?)\] Type\[(.*?)\] Position\[(.*?)\] '
            r'BBox\[Min\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*Max\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*Size\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\] '
            r'Geometry\[(.*?)\] Context\[(.*?)\]'
        )
        logger.debug("Enhanced Face Processor initialized")

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


class TextToCADAgent:
    # Max confirm rounds before the part is built without asking again
    MAX_CONFIRM_ATTEMPTS = 3

    def __init__(self, llm, greeting_llm=None):
        """`llm` extracts and patches the part IR; `greeting_llm` (a small, cheap model)
        classifies greetings and confirm replies. Both fall back to `llm`."""
        self.llm = llm
        self.greeting_llm = greeting_llm or llm
        self.model_names = {
            'llm': getattr(llm, 'model_name', 'unknown'),
            'greeting': getattr(self.greeting_llm, 'model_name', 'unknown'),
        }
        logger.info("Models: " + " | ".join(f"{role}={name}" for role, name in self.model_names.items()))

        self._session_states = {}
        self._session_cost_trackers = {}

        self.greeting_classification_chain = create_greeting_classification_chain(self.greeting_llm)
        self.face_processor = EnhancedFaceProcessor()
        from src.core.ir_flow import IRFlow
        self._ir_flow = IRFlow(self)

    def _get_session_state(self, session_id):
        """Per-session state (in memory; the DB keeps the transcript and the last script)."""
        if session_id not in self._session_states:
            self._session_states[session_id] = {
                'latest_code': None,        # last stub script sent to the worker
                'latest_ir': None,          # last part IR built
                'latest_title': None,
                'pending_questions': [],
                'confirm_count': 0,
                'awaiting_confirm': False,
                'confirmed_description': '',
                'ir_pending': None,         # IR waiting for the user's yes
                'material_choice': "STEEL",
            }
        return self._session_states[session_id]

    def _update_session_state(self, session_id, **updates):
        session_state = self._get_session_state(session_id)
        session_state.update(updates)
        return session_state

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

        This:
        - Is NEVER suppressed (no _logged flag).
        - Always reports only the cost since the last start_request() call.
        - Returns the summary dict so callers can embed it in final_result.

        Call this in the `finally` block of process_request_with_progress().
        """
        if session_id in self._session_cost_trackers:
            tracker = self._session_cost_trackers[session_id]
            return tracker.log_request_cost(label=label)
        return {}

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
            Handles: sentences, emoji, mixed intent.

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

        logger.debug(
            f"[CONFIRM_DETECT] Classifying | session={session_id} "
            f"| input='{clean_text[:80]}'"
        )

        # ══════════════════════════════════════════════════════════════════
        # LAYER 1 — Fuzzy pre-check (zero LLM cost)
        # Only applies when the message is SHORT (≤ 20 chars).
        # A long message almost certainly contains extra instructions → CHANGE.
        # ══════════════════════════════════════════════════════════════════
        YES_KEYWORDS = {
            "yes", "yep", "yeah", "yea", "sure", "ok", "okay", "y",
            "go", "proceed", "generate", "correct", "right",
            "perfect", "fine", "good", "great", "alright", "agreed",
            # Symbols / emoji
            "✓", "✔", "👍", "🆗",
        }

        if len(clean_text) <= 20:
            word = clean_text.lower()

            # 1a. Exact match in YES keyword list
            if word in YES_KEYWORDS:
                logger.debug(
                    f"[CONFIRM_DETECT] Pre-check EXACT match YES "
                    f"| word='{word}' | session={session_id}"
                )
                return "YES"

            # 1b. Fuzzy similarity against each YES keyword
            #     Threshold 0.75 — catches "yeas"→"yes" (0.86),
            #     "okey"→"okay" (0.75), "yse"→"yes" (0.67→try vs more kws),
            #     "yess"→"yes" (0.86),
            #     but NOT "no" or "cancel" (all < 0.50 vs yes keywords).
            best_ratio = 0.0
            best_kw    = ""
            for kw in YES_KEYWORDS:
                ratio = difflib.SequenceMatcher(None, word, kw).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_kw    = kw

            if best_ratio >= 0.75:
                logger.debug(
                    f"[CONFIRM_DETECT] Pre-check FUZZY match YES "
                    f"| word='{word}' → '{best_kw}' (ratio={best_ratio:.2f}) "
                    f"| session={session_id}"
                )
                return "YES"

            logger.debug(
                f"[CONFIRM_DETECT] Pre-check inconclusive "
                f"(best_ratio={best_ratio:.2f} < 0.75) → calling mini LLM | session={session_id}"
            )

        # ══════════════════════════════════════════════════════════════════
        # LAYER 2 — Mini LLM (gpt-4.1-nano)
        # Handles: long text, sentences, phrasings the keyword list misses.
        # ══════════════════════════════════════════════════════════════════
        try:
            detect_prompt = ChatPromptTemplate.from_template(confirm_detector_template)
            detect_chain  = detect_prompt | self.greeting_llm | StrOutputParser()

            cost_tracker = self._get_cost_tracker(session_id)
            raw_str: str = await ainvoke_with_cost_tracking(
                "confirm_intent_detection",
                detect_chain.ainvoke,
                {"user_text": truncated_text},
                cost_tracker,
                self.model_names["greeting"],
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

        logger.debug(f"[CONFIRM_DETECT] ✅ Detected intent={intent} | session={session_id}")
        return intent

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

    async def _execute_freecad_remote(self, code_filepath, session_id, priority: int = 0, shape_type=None):
        """Execute a FreeCAD script on the remote server via MQTT workflow.
        
        Encapsulates the 4-phase MQTT-based async workflow:
          Phase 1: Submit job with file + user_id + metadata
          Phase 2: Verify job was created successfully
          Phase 3: Listen to MQTT for completion
          Phase 4: Download result files
        
        Args:
            code_filepath: Path to the saved Python script file
            session_id: Session ID (used as user_id for FreeCAD server)
            priority: FreeCAD queue priority from 0 to 100
            
        Returns:
            Tuple of (result_dict, downloaded_files_dict)

        Raises:
            CadError carrying the code of the exact failure (see error_codes.py)
        """
        from src.core.freecad_remote_client import (
            FreeCADServerError,
            create_freecad_client
        )
        import datetime

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
                    logger.error(f"[FreeCAD] ❌ Health check failed | url={client.base_url}")
                    raise CadError("102.1", detail=f"Health check failed for {client.base_url}")

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
                    f"[FreeCAD] Submitting | user_id={user_id} | file={filename} | size={file_size}B | priority={priority}"
                )

                submit_result = await client.generate(
                    script_path=code_filepath,
                    user_id=user_id,
                    auto_download=False,
                    priority=priority
                )

                # The submission response is fully validated inside
                # client.generate() -- missing user_id (102.3), user_id mismatch
                # (102.4), status != 'queued' (102.5) and mqtt_published false
                # (102.6) all raise there before it returns. The block that used
                # to repeat those four checks here could never run, so a real
                # handshake failure only ever surfaced through the generic
                # handler below as "execution failed".
                logger.debug(
                    f"[FreeCAD] Phase 1 complete | user_id={user_id} | "
                    f"status={submit_result.get('status')} | mqtt={submit_result.get('mqtt_published')}"
                )

                # ============================================================
                # PHASE 2: VERIFY JOB EXISTS ON SERVER
                # ============================================================
                logger.debug(f"[FreeCAD] Phase 2: Verifying | user_id={user_id}")
                await asyncio.sleep(0.5)

                try:
                    verification_status = await client.get_execution_status_async(user_id)

                    if not verification_status:
                        logger.error(f"[FreeCAD] ❌ Server returned empty status for user_id={user_id}")
                        raise CadError("102.7", detail=f"Empty status payload for {user_id}")

                    server_user_id = verification_status.get('user_id')
                    server_status = verification_status.get('status')

                    if server_user_id != user_id:
                        logger.error(f"[FreeCAD] ❌ user_id mismatch! Expected: {user_id}, Server returned: {server_user_id}")
                        raise CadError("102.4", detail=f"Status belongs to {server_user_id}, not {user_id}")

                    logger.debug(f"[FreeCAD] Phase 2 complete | user_id={user_id} | status={server_status}")

                except CadError:
                    raise
                except FreeCADServerError as verify_error:
                    # Only "the server has no such job" (102.7) is fatal here:
                    # the job was accepted moments ago, so anything else is the
                    # status endpoint being slow or flaky while the job runs.
                    if error_code_of(verify_error) == "102.7":
                        logger.error(f"[FreeCAD] ❌ CRITICAL: Job NOT FOUND on server. Error: {verify_error}")
                        raise CadError("102.7", detail=str(verify_error))
                    logger.warning(f"[FreeCAD] ⚠️ Job verification warning (continuing): {verify_error}")
                except Exception as verify_error:
                    logger.error(f"[FreeCAD] ❌ Job verification failed with unexpected error: {verify_error}")
                    logger.warning("[FreeCAD] ⚠️ Continuing despite verification error - job may still be processing")

                # ============================================================
                # PHASE 3: LISTEN TO MQTT FOR COMPLETION (USER_ID ONLY)
                # ============================================================
                logger.debug(f"[FreeCAD] Phase 3: Listening MQTT | user_id={user_id}")

                async def progress_callback(status_dict):
                    """Callback function to handle MQTT progress updates"""
                    progress = status_dict.get('progress', 0)
                    current_process = status_dict.get('current_process', 'Processing...')
                    logger.debug(f"[FreeCAD] Progress: {progress}% - {current_process}")

                monitoring_result = await client.wait_for_mqtt_completion_async(
                    user_id=user_id,
                    max_duration=None,
                    progress_callback=progress_callback
                )

                # ============================================================
                # CHECK MQTT LISTENING RESULT
                # ============================================================
                if not monitoring_result.get('success'):
                    # The client classified the failure while it still had the
                    # server's own words (message + specific_exception + hint);
                    # re-guessing here from a truncated string is what used to
                    # turn every failure into 104.1.
                    raw_error = monitoring_result.get('error', 'Unknown error during execution')
                    code = monitoring_result.get('code') or "104.1"
                    logger.error(
                        f"[FreeCAD] ❌ PHASE 3 FAILED: Job failed | "
                        f"user_id={user_id} | code={code} | raw_error={raw_error}"
                    )
                    raise CadError(code, detail=str(raw_error))

                total_time = monitoring_result.get('total_time', 0)
                logger.debug(f"[FreeCAD] Phase 3 complete | user_id={user_id} | time={total_time:.2f}s")

                # ============================================================
                # PHASE 4: DOWNLOAD RESULT FILES FROM SERVER (WITH RETRY)
                # ============================================================
                logger.debug(f"[FreeCAD] Phase 4: Downloading | user_id={user_id}")

                initial_delay = 3
                logger.debug(f"[FreeCAD] Waiting {initial_delay}s for server | user_id={user_id}")
                await asyncio.sleep(initial_delay)

                max_download_retries = 3
                base_retry_delay = 3
                result = None
                last_error = None

                for attempt in range(1, max_download_retries + 1):
                    try:
                        logger.debug(f"[FreeCAD] Download attempt {attempt}/{max_download_retries} | user_id={user_id}")
                        result = await client.get_job_result_async(user_id, auto_download=True, expect_obj=expect_obj)
                        logger.debug(f"[FreeCAD] Download success | user_id={user_id} | attempt={attempt}")
                        break

                    except Exception as download_error:
                        last_error = download_error
                        # Retry transport failures only: the job is finished, the
                        # files are simply not fetchable yet. A 104.x means the
                        # server has nothing to hand over, so retrying just
                        # delays an error that will not change.
                        is_retryable = error_code_of(download_error) in {
                            "101.2", "102.1", "102.2", "102.7", "102.8"
                        }

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
                    raise CadError("102.8", detail=str(last_error))

                total_exec_time = monitoring_result.get('total_time', 0)
                logger.info(
                    f"[FreeCAD] Job complete in {total_exec_time:.1f}s | "
                    f"{len(result.get('files', {}))} files | user_id={user_id}"
                )
            
            if not result["success"]:
                message = result.get('message', 'Unknown error')
                code = result.get('code') or "104.4"
                logger.error(f"[FreeCAD] ❌ Result reports failure | code={code} | {message}")
                raise CadError(code, detail=str(message))

            logger.debug("Remote FreeCAD execution successful")

            downloaded_files = result.get("files", {})
            return result, downloaded_files

        except CadError:
            # Already carries the code of the exact failure.
            raise
        except FreeCADServerError as e:
            # The client sets `code` at every raise site, so the failure is
            # already identified. The keyword sniffing that used to live here
            # ran on messages that were empty (asyncio.TimeoutError) or already
            # re-wrapped, which is why unrelated failures all ended up as 104.1.
            code = error_code_of(e) or "102.1"
            logger.error(
                f"[FreeCAD] Server error | code={code} | user_id={current_user_id} | "
                f"session_id={session_id}: {e}"
            )
            raise CadError(code, detail=str(e))
        except asyncio.TimeoutError:
            logger.error(f"[FreeCAD] Timed out | user_id={current_user_id} | session_id={session_id}")
            raise CadError("101.2", detail="asyncio.TimeoutError while talking to the FreeCAD server")
        except Exception as e:
            logger.error(
                f"[FreeCAD] Unexpected error executing script on remote server | "
                f"user_id={current_user_id} | session_id={session_id}: {e}"
            )
            raise CadError("105.6", detail=f"{type(e).__name__}: {e}")

    async def save_outputs(self, code, shape_type, title, user_text="", session_id=None, priority: int = 0):
        """Save the script, run it on the FreeCAD worker and copy STEP / OBJ / PDF into outputs/."""
        from src.utils.file_manager import save_code_file
        from src.utils.path_manager import OBJ_OUTPUT_DIR, CAD_OUTPUT_DIR, PDF_OUTPUT_DIR
        import datetime

        logger.debug(f"Starting save_outputs with remote FreeCAD server (priority={priority})")
        shape_type = shape_type or "part"
        try:
            code_filepath = await asyncio.get_event_loop().run_in_executor(None, save_code_file, code, shape_type, title)
        except Exception as e:
            logger.error(f"Error saving code file: {e}")
            raise CadError("105.4", detail=str(e))

        result, downloaded_files = await self._execute_freecad_remote(
            code_filepath, session_id, priority=priority, shape_type=shape_type
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
        # labels such as "Perforated Sheet" must not put spaces into download URLs
        safe_shape = re.sub(r"[^A-Za-z0-9_-]+", "-", str(shape_type or "part")).strip("-") or "part"
        base_filename = f"{safe_shape}_{timestamp}_{_session_hash}"

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
                        logger.error(f"[ERROR] Could not copy {file_type.upper()} file after {max_retries} attempts: {e}")
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

        # The one place every artefact path is reported. Each path sits alone at
        # the end of its line, with no trailing punctuation, so terminals detect
        # it as a link and Ctrl+Click opens it. Individual "saved X to …" lines
        # elsewhere in this flow are DEBUG for that reason — they used to print
        # the same code path three times before this block repeated it a fourth.
        artefacts = [
            ("code", code_filepath),
            ("step", step_path_to_return),
            ("obj", obj_path_to_return),
            ("pdf", pdf_path_to_return),
        ]
        logger.info("[FILES] Generated\n" + "\n".join(
            f"  {kind:<9} {path}" for kind, path in artefacts if path
        ))

        # Check if required output files were created. These are two different
        # failures and used to share one code: the server never producing a STEP
        # (104.3) is nothing like this API failing to store one it did receive
        # (105.5), and only the second is worth retrying as-is.
        if not step_path_to_return:
            if "step" not in downloaded_files:
                logger.error("No STEP file was generated by the remote server")
                raise CadError("104.3", detail=f"downloaded types: {sorted(downloaded_files)}")
            logger.error(f"STEP file was downloaded but could not be stored | src={downloaded_files['step']}")
            raise CadError("105.5", detail=f"copy failed for {downloaded_files['step']}")


        # PDF is optional — pdf_path_to_return may be None (not generated/failed to copy)
        # without affecting STEP/OBJ, which are the required outputs.
        return obj_path_to_return, step_path_to_return, pdf_path_to_return

    async def process_request_with_progress(self, user_text, is_edit_request=False, session_id=None, request_origin='api', material_choice=None, priority: int = 0):
        """One conversation turn as a stream of progress dicts, ending with {"final_result": ...}."""
        start_time = time.time()
        logger.info(f"[AGENT_PRIORITY] process_request_with_progress priority={priority} | session={session_id}")

        # A missing session_id here is a bug upstream — generate_cad_realtime_stream
        # is supposed to resolve it — so only the failure is worth a log line.
        if session_id is None:
            import uuid
            import random
            rand_digits = random.randint(100000, 999999)
            rand_uuid   = uuid.uuid4().hex[:6]
            session_id  = f"session_{rand_uuid}_{rand_digits}"
            logger.warning(
                f"[SESSION_WARN] process_request_with_progress got session_id=None — "
                f"generated {session_id}. It should have been resolved upstream in "
                f"generate_cad_realtime_stream."
            )

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
            if material_choice:
                state['material_choice'] = material_choice

            # ── reply to a pending confirmation ────────────────────────────
            if state.get('awaiting_confirm') and state.get('ir_pending'):
                _ir_intent = await self._detect_confirm_intent(user_text, session_id)
                if _ir_intent == "YES":
                    async for _upd in self._ir_flow.build_pending(session_id, priority, start_time, user_text):
                        yield _upd
                    return
                # CHANGE: drop the pending IR and re-read the whole conversation below
                self._update_session_state(session_id, awaiting_confirm=False, ir_pending=None)


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
                logger.debug(f"[AGENT_GREETING] Checking for greeting/casual conversation for session {session_id}")
                try:
                    cost_tracker = self._get_cost_tracker(session_id)
                    greeting_result = await ainvoke_with_cost_tracking(
                        "greeting_classification_progress",
                        self.greeting_classification_chain.ainvoke,
                        {"user_text": user_text},
                        cost_tracker,
                        self.model_names['greeting']
                    )
                    if greeting_result.get('classification') != 'cad_request':
                        logger.info(
                            f"[GREETING] {greeting_result.get('classification')} "
                            f"({greeting_result.get('confidence')})"
                        )
                    else:
                        logger.debug(
                            f"[GREETING] cad_request ({greeting_result.get('confidence')})"
                        )

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

                    # A message that carries dimensions is a CAD request whatever the classifier
                    # says: "Can you provide a 3D file for a guardrail of 1200 mm..." was read as
                    # a greeting by the nano tier and answered with "Hello! I'm your assistant".
                    if (greeting_result.get('classification') in ('greeting', 'information_request', 'process_question')
                            and re.search(r'\d\s*(mm|cm|m|x\s*\d|×)|Ø|diam', user_text, re.I)):
                        logger.info("[AGENT_GREETING] Overriding '%s' -> cad_request (message carries dimensions)",
                                    greeting_result.get('classification'))
                        greeting_result['classification'] = 'cad_request'

                    # If it's a greeting with high confidence, return the response immediately
                    if (greeting_result.get('classification') == 'greeting' and
                        greeting_result.get('confidence', 0) > 0.8 and
                        greeting_result.get('response')):

                        logger.debug(f"[AGENT_GREETING] Detected greeting/casual conversation for session {session_id}")
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

                except Exception as e:
                    logger.warning(f"[AGENT_GREETING] Error in greeting classification: {e}")
                    # Continue with normal processing if greeting classification fails

            # ── every other request, new or edit: the IR pipeline ───────────
            async for _upd in self._ir_flow.turn(user_text, session_id, is_edit_request, priority, start_time):
                yield _upd

        except Exception as e:
            total_duration = time.time() - start_time
            logger.error(f"[AGENT_PROGRESS] Error during process_request_with_progress after {total_duration:.2f}s for session {session_id}: {str(e)}")
            logger.error(f"[AGENT_PROGRESS] Traceback: {traceback.format_exc()}")
            result = {"error": as_user_error(e), "code": None}
            yield {"final_result": result}
            return
        finally:
            # Log cost for THIS request turn only (not session total).
            # _log_request_cost uses start_request() snapshot → never suppressed,
            # always shows correct per-turn breakdown.
            self._log_request_cost(session_id, label="process_request_with_progress")

