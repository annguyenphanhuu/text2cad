"""
RAG Singleton - Shared FAISS Index with Thread-Safe Access

This module provides a singleton pattern for RAG retrieval with:
- FAISS index loaded once and shared across all requests
- Thread-safe async access with asyncio.Lock
- Memory optimization: N×500MB → 500MB total
- No complex pooling logic

Key Benefits:
- Simple interface: get_rag_context(query, k)
- Automatic initialization on first use
- Blocking RAG retrieval in executor (non-blocking for async)
- Multi-user concurrent access support
"""

import asyncio
import logging
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
    global _shared_rag_retriever, _shared_faiss_index, _shared_metadata, _initialization_time
    
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
            
            logger.info(f"[RAG] ✓ Index ready ({duration:.2f}s)")
            
            return True
            
        except Exception as e:
            logger.error(f"[RAG] ✗ Initialization failed: {e}")
            logger.exception(e)
            return False


async def get_rag_context(
    query: str, 
    k: int = 5,
    classification_llm: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Get RAG context with shared FAISS index
    
    This function provides thread-safe access to the shared FAISS index.
    Arguments 'k' and 'classification_llm' are kept for compatibility 
    but mapped to the split context retrieval parameters.
    
    Args:
        query: User query for context retrieval
        k: Number of documents to retrieve (mapped to k_examples)
        classification_llm: Optional LLM (unused in new flow)
    
    Returns:
        Dictionary with retrieved context (combined rules + examples)
    """
    global _retrieval_count
    
    try:
        # Use the split context function and combine results
        split_result = await get_rag_split_context(
            query=query,
            k_rules=10,
            k_examples=k,
            reranking_llm=None, # Use default or None
            session_id="legacy_context_request"
        )
        
        if not split_result['success']:
             return {
                'success': False,
                'context': '',
                'documents': [],
                'query': query,
                'retrieval_time': 0.0,
                'error': split_result.get('error', 'Unknown error')
            }

        # Combine contexts
        combined_context = f"{split_result['rules_context']}\n\n{split_result['examples_context']}"
        
        return {
            'success': True,
            'context': combined_context,
            'documents': split_result.get('rules_documents', []) + split_result.get('examples_documents', []),
            'query': query,
            'retrieval_time': split_result.get('retrieval_time', 0.0),
            'error': None
        }

    except Exception as e:
        logger.error(f"[RAG] ✗ Query failed: {str(e)}")
        logger.debug(f"[RAG] Failed query details: {query[:50]}...")
        logger.exception(e)

        return {
            'success': False,
            'context': '',
            'documents': [],
            'query': query,
            'retrieval_time': 0.0,
            'error': str(e)
        }


async def get_rag_rules_only(
    query: str,
    k: int = 15,
    classification_llm: Optional[Any] = None,
    session_id: str = "unknown"
) -> Dict[str, Any]:
    """
    Get ONLY manufacturing rules for unified processing chain
    
    This function retrieves manufacturing rules for parameter validation
    without including example code.
    
    Args:
        query: User query for rules retrieval
        k: Number of rules to retrieve per detected class (default: 15)
        classification_llm: Optional LLM for query classification
    
    Returns:
        Dictionary with retrieved rules:
        {
            'success': bool,
            'context': str,  # Formatted rules text
            'documents': List[Document],
            'query': str,
            'retrieval_time': float,
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
                'context': '',
                'documents': [],
                'query': query,
                'retrieval_time': 0.0,
                'error': 'Failed to initialize RAG singleton'
            }
    
    try:
        start_time = datetime.now()

        # Import retrieve_rules_only function
        from src.rag.retriever import (
            retrieve_rules_only,
            set_classification_llm,
            faiss_index_instance
        )

        # Set classification LLM if provided
        if classification_llm:
            set_classification_llm(classification_llm)

        # Direct async call (retrieve_rules_only is now async)
        documents = await retrieve_rules_only(
            query=query,
            faiss_index_instance=faiss_index_instance,
            classification_llm=classification_llm,
            reranking_llm=None,  # No reranking in this legacy function
            k_rules=k,
            session_id=session_id
        )

        # Format documents into context string
        from src.core.agent_utils import format_retrieved_context
        context = format_retrieved_context(documents)

        retrieval_time = (datetime.now() - start_time).total_seconds()
        _retrieval_count += 1

        logger.info(f"[RAG_RULES] Retrieved {len(documents)} rules in {retrieval_time:.3f}s")
        
        # Log detailed RAG context for rules retrieval
        try:
            from src.utils.rag_context_logger import log_rag_context_retrieval
            
            # Get detected classes from documents
            detected_classes = list(set(d.metadata.get('class', '') for d in documents if d.metadata.get('class')))
            
            # Log to dedicated RAG context file
            log_rag_context_retrieval(
                session_id=session_id,
                query=query,
                documents=documents,
                context=context,
                retrieval_time=retrieval_time,
                detected_classes=detected_classes if detected_classes else None,
                success=True,
                error=None,
                is_reranked=True
            )
        except Exception as log_error:
            logger.warning(f"[RAG_RULES] Failed to log RAG context: {log_error}")

        return {
            'success': True,
            'context': context,
            'documents': documents,
            'query': query,
            'retrieval_time': retrieval_time,
            'error': None
        }

    except Exception as e:
        logger.error(f"[RAG_RULES] ✗ Query failed: {str(e)}")
        logger.exception(e)
        
        # Log failed retrieval
        try:
            from src.utils.rag_context_logger import log_rag_context_retrieval
            
            log_rag_context_retrieval(
                session_id=session_id,
                query=query,
                documents=[],
                context="",
                retrieval_time=0.0,
                detected_classes=None,
                success=False,
                error=str(e)
            )
        except Exception as log_error:
            logger.warning(f"[RAG_RULES] Failed to log error: {log_error}")

        return {
            'success': False,
            'context': '',
            'documents': [],
            'query': query,
            'retrieval_time': 0.0,
            'error': str(e)
        }



async def get_rag_stats() -> Dict[str, Any]:
    """
    Get RAG singleton statistics
    
    Returns:
        Dictionary with RAG stats:
        {
            'initialized': bool,
            'initialization_time': str,
            'retrieval_count': int,
            'uptime_seconds': float
        }
    """
    global _shared_rag_retriever, _initialization_time, _retrieval_count
    
    if _shared_rag_retriever is None:
        return {
            'initialized': False,
            'initialization_time': None,
            'retrieval_count': 0,
            'uptime_seconds': 0.0
        }
    
    uptime = (datetime.now() - _initialization_time).total_seconds() if _initialization_time else 0.0
    
    return {
        'initialized': True,
        'initialization_time': _initialization_time.isoformat() if _initialization_time else None,
        'retrieval_count': _retrieval_count,
        'uptime_seconds': uptime
    }


async def reset_rag_singleton():
    """
    Reset RAG singleton (for testing purposes)
    
    This will force re-initialization on next retrieval.
    Use with caution in production.
    """
    global _shared_rag_retriever, _shared_faiss_index, _shared_metadata, _initialization_time, _retrieval_count
    
    async with _rag_lock:
        logger.warning("[RAG] Resetting singleton...")
        _shared_rag_retriever = None
        _shared_faiss_index = None
        _shared_metadata = None
        _initialization_time = None
        _retrieval_count = 0
        logger.info("[RAG] Reset complete")



async def get_rag_split_context(
    query: str,
    k_rules: int = 10,
    k_examples: int = 5,
    reranking_llm: Optional[Any] = None,
    cost_tracker: Optional[Any] = None,
    session_id: str = "unknown"
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
        
        logger.info(f"[RAG_SPLIT] 🎯 Single retrieval for query: {query[:80]}...")
        
        # PARALLEL RETRIEVAL: Rules and Examples at the same time
        rules_documents, examples_documents = await asyncio.gather(
            retrieve_rules_only(
                query=query,
                faiss_index_instance=faiss_index_instance,
                classification_llm=None,  # Force keyword matching
                reranking_llm=reranking_llm,  # GPT-4.1-nano
                k_rules=k_rules,
                cost_tracker=cost_tracker,
                session_id=session_id
            ),
            retrieve_examples_only(
                query=query,
                faiss_index_instance=faiss_index_instance,
                classification_llm=None,  # Force keyword matching
                reranking_llm=reranking_llm,  # GPT-4.1-nano
                k_examples=k_examples,
                cost_tracker=cost_tracker,
                session_id=session_id
            )
        )
        
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
        
        # Compact summary report
        logger.info(
            f"\n🎯 [RAG SUMMARY] Query: '{query[:]}...'\n"
            f"   📝 Rules: {rule_count} | 💡 Examples: {examples_count} | ℹ️  Info: {info_count}\n"
            f"   🏷️  Classes: {detected_classes}\n"
            f"   ⏱️  Time: {retrieval_time:.2f}s | ✅ Success"
        )
        
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

