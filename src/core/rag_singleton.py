"""
RAG Singleton - Shared FAISS Index with Thread-Safe Access

This module provides a singleton pattern for RAG retrieval with:
- FAISS index loaded once and shared across all requests
- Thread-safe async access with asyncio.Lock
- Memory optimization: N×500MB → 500MB total
- No complex pooling logic

Key Benefits:
- Simple interface: get_rag_split_context(query, k_rules, k_examples)
- Automatic initialization on first use
- Blocking RAG retrieval in executor (non-blocking for async)
- Multi-user concurrent access support
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

# Global singleton instances
_shared_rag_retriever: Optional[Any] = None
_shared_faiss_index: Optional[Any] = None
_shared_metadata: Optional[Dict] = None
_rag_lock = asyncio.Lock()
_initialization_time: Optional[datetime] = None
_retrieval_count = 0
#: Whether "index ready" has been logged — see initialize_rag_singleton().
_index_ready_announced = False

async def initialize_rag_singleton():
    """
    Initialize RAG singleton with shared FAISS index
    
    This function:
    1. Loads FAISS index once (500MB)
    2. Loads metadata store
    3. Initializes retriever with shared resources
    4. Thread-safe initialization with double-check locking
    
    Returns:
        bool: True if initialization successful
    """
    global _shared_rag_retriever, _shared_faiss_index, _shared_metadata
    global _initialization_time, _index_ready_announced
    
    # Double-check locking pattern
    if _shared_rag_retriever is not None:
        return True
    
    async with _rag_lock:
        # Double-check after acquiring lock
        if _shared_rag_retriever is not None:
            return True
        
        try:
            logger.debug("[RAG] Initializing FAISS index...")
            start_time = datetime.now()
            
            # Import here to avoid circular dependencies
            from src.rag.retriever import initialize_retriever
            from src.rag.vector_store import METADATA_STORE as faiss_metadata
            
            # Load FAISS index in executor (blocking operation)
            loop = asyncio.get_event_loop()
            _shared_rag_retriever = await loop.run_in_executor(
                None, initialize_retriever
            )
            
            # Store metadata reference
            _shared_metadata = faiss_metadata
            
            _initialization_time = datetime.now()
            duration = (_initialization_time - start_time).total_seconds()
            
            # Announced once per process. This function currently re-runs on
            # every retrieval — `initialize_retriever()` returns None, so the
            # `_shared_rag_retriever is not None` guard above never trips — and
            # until that is fixed the line would otherwise repeat per request.
            if not _index_ready_announced:
                _index_ready_announced = True
                logger.info(f"[RAG] Index ready ({duration:.2f}s)")
            else:
                logger.debug(f"[RAG] Index re-initialized ({duration:.2f}s)")
            
            return True
            
        except Exception as e:
            logger.error(f"[RAG] ✗ Initialization failed: {e}")
            logger.exception(e)
            return False


async def get_rag_split_context(
    query: str,
    k_rules: int = 10,
    k_examples: int = 5,
    reranking_llm: Optional[Any] = None,
    cost_tracker: Optional[Any] = None,
    session_id: str = "unknown",
    pre_expanded_query: Optional[str] = None,
    pre_detected_shape_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Single RAG retrieval with split context for both chains

    CONTEXT ROUTING:
    - rules_context: For UNIFIED ANALYSIS ONLY (parameter validation, rule checking)
    - examples_context: For CODE GENERATION ONLY (examples + info files)

    This function retrieves ONCE and returns separated contexts.
    Both rules and examples are reranked using GPT-4.1-nano LLM.

    Args:
        query: User query for retrieval
        k_rules: Number of rules to return after reranking (default: 10)
        k_examples: Number of examples to return after reranking (default: 5)
        reranking_llm: GPT-4.1-nano LLM for reranking
        pre_expanded_query: Pass this when the CALLER has already run query
            expansion, to stop the examples retriever from paying for a second,
            near-identical expansion call. Must be paired with
            `pre_detected_shape_type` so LLM shape detection is preserved.
        pre_detected_shape_type: `detected_shape_type` from the caller's expansion.

    Returns:
        {
            'success': bool,
            'rules_context': str,  # For UNIFIED ANALYSIS ONLY
            'examples_context': str,  # For CODE GENERATION ONLY (examples + info)
            'rules_documents': List[Document],
            'examples_documents': List[Document],
            'query': str,
            'retrieval_time': float,
            'detected_classes': List[str],
            'error': Optional[str]
        }
    """
    global _retrieval_count
    
    # Initialize if not already done
    if _shared_rag_retriever is None:
        success = await initialize_rag_singleton()
        if not success:
            return {
                'success': False,
                'rules_context': '',
                'examples_context': '',
                'rules_documents': [],
                'examples_documents': [],
                'query': query,
                'retrieval_time': 0.0,
                'detected_classes': [],
                'error': 'Failed to initialize RAG singleton'
            }
    
    try:
        start_time = datetime.now()
        
        # Import retrieval functions
        from src.rag.retriever import (
            retrieve_rules_only,
            retrieve_examples_only,
            faiss_index_instance
        )
        
        # The query is echoed by the [RAG] summary once the retrieval is done.
        logger.debug(f"[RAG_SPLIT] Single retrieval for query: {query[:80]}...")
        
        # PARALLEL RETRIEVAL: Rules and Examples at the same time.
        # A half the caller asked for zero of is not retrieved at all — its
        # result was discarded downstream anyway, and skipping it drops a
        # vector search plus a nano rerank call. `retrieve_rules_only` also
        # short-circuits on k_rules<=0, but building the coroutine here is what
        # lets the examples half be skipped: calling it with k_examples=0 trips
        # `assert k > 0` in vector_store.search_index.
        async def _no_docs():
            return []

        rules_coro = (
            retrieve_rules_only(
                query=query,
                faiss_index_instance=faiss_index_instance,
                classification_llm=None,  # Force keyword matching
                reranking_llm=reranking_llm,  # GPT-4.1-nano
                k_rules=k_rules,
                cost_tracker=cost_tracker,
                session_id=session_id
            )
            if k_rules > 0 else _no_docs()
        )
        examples_coro = (
            retrieve_examples_only(
                query=query,
                faiss_index_instance=faiss_index_instance,
                classification_llm=None,  # Force keyword matching
                reranking_llm=reranking_llm,  # GPT-4.1-nano
                k_examples=k_examples,
                cost_tracker=cost_tracker,
                session_id=session_id,
                pre_expanded_query=pre_expanded_query,
                pre_detected_shape_type=pre_detected_shape_type,
            )
            if k_examples > 0 else _no_docs()
        )
        if k_rules <= 0 or k_examples <= 0:
            logger.debug(
                f"[RAG_SPLIT] half-retrieval: k_rules={k_rules}, k_examples={k_examples} "
                f"→ skipping the zero half entirely"
            )

        rules_documents, examples_documents = await asyncio.gather(rules_coro, examples_coro)

        # Format contexts
        from src.core.agent_utils import format_retrieved_context
        rules_context = format_retrieved_context(rules_documents)
        examples_context = format_retrieved_context(examples_documents)
        
        # Get detected classes from rules documents
        detected_classes = list(set(
            d.metadata.get('class', '') 
            for d in rules_documents 
            if d.metadata.get('class')
        ))
        
        retrieval_time = (datetime.now() - start_time).total_seconds()
        _retrieval_count += 1
        
        # Get counts
        rule_count = len(rules_documents)
        ex_only = [d for d in examples_documents if d.metadata.get('type') != 'info']
        info_only = [d for d in examples_documents if d.metadata.get('type') == 'info']
        examples_count = len(ex_only)
        info_count = len(info_only)
        
        # One block for the whole retrieval: counts on the first line, then what
        # was actually picked. The retriever used to log its own [RAG_RULES] and
        # [RAG_EXAMPLES] lines here too, restating these counts from the inside;
        # everything they carried is derivable from the documents themselves.
        # The expanded query is multi-line by construction, so it is flattened.
        flat_query = ' · '.join(part.strip() for part in query.splitlines() if part.strip())
        totals = ' · '.join(
            f"{count} {label}"
            for label, count in (('rules', rule_count), ('examples', examples_count),
                                 ('info', info_count))
            if count
        )
        lines = [
            f"[RAG] {totals or 'nothing retrieved'} · {retrieval_time:.1f}s "
            f"| q='{flat_query[:70]}'"
        ]
        if rules_documents:
            rule_ids = ' '.join(d.metadata.get('rule_id', '?') for d in rules_documents)
            lines.append(f"  rules     {', '.join(detected_classes)}: {rule_ids}")
        if ex_only:
            # Deduplicated: the top examples are routinely several chunks of one file.
            sources = dict.fromkeys(
                os.path.basename(d.metadata.get('source', '?')) for d in ex_only
            )
            lines.append(f"  examples  {' '.join(sources)}")
        logger.info('\n'.join(lines))
        
        # Log RERANKED context to rag_context_rerank.log
        try:
            from src.utils.rag_context_logger import log_rag_context_retrieval
            
            all_docs = rules_documents + examples_documents
            
            log_rag_context_retrieval(
                session_id=session_id,
                query=query,
                documents=all_docs,
                context=f"RULES:\n{rules_context}\n\nEXAMPLES:\n{examples_context}",
                retrieval_time=retrieval_time,
                detected_classes=detected_classes,
                success=True,
                error=None,
                is_reranked=True  # This is AFTER reranking
            )
        except Exception as e:
            logger.warning(f"Failed to log reranked RAG context: {e}")
        
        return {
            'success': True,
            'rules_context': rules_context,
            'examples_context': examples_context,
            'rules_documents': rules_documents,
            'examples_documents': examples_documents,
            'query': query,
            'retrieval_time': retrieval_time,
            'detected_classes': detected_classes,
            'error': None
        }
        
    except Exception as e:
        logger.error(f"[RAG_SPLIT] ✗ Query failed: {str(e)}")
        logger.exception(e)
        
        return {
            'success': False,
            'rules_context': '',
            'examples_context': '',
            'rules_documents': [],
            'examples_documents': [],
            'query': query,
            'retrieval_time': 0.0,
            'detected_classes': [],
            'error': str(e)
        }

