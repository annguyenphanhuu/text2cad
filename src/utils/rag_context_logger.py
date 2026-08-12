"""
RAG Context Logger - Detailed logging for RAG retrieval
Logs comprehensive information about RAG context for each request
"""
import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from langchain_core.documents import Document

# Configure RAG context logger
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
RAG_LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(RAG_LOG_DIR, exist_ok=True)

RAG_CONTEXT_LOG_PATH = os.path.join(RAG_LOG_DIR, "rag_context.log")
RAG_RERANK_LOG_PATH = os.path.join(RAG_LOG_DIR, "rag_context_rerank.log")

def _setup_logger(name: str, log_file: str):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
    if logger.handlers:
        logger.handlers.clear()
        
    handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger

# Create dedicated loggers
rag_context_logger = _setup_logger("rag_context", RAG_CONTEXT_LOG_PATH)
rag_rerank_logger = _setup_logger("rag_rerank", RAG_RERANK_LOG_PATH)


def log_rag_context_retrieval(
    session_id: str,
    query: str,
    documents: List[Document],
    context: str,
    retrieval_time: float,
    detected_classes: Optional[List[str]] = None,
    success: bool = True,
    error: Optional[str] = None,
    is_reranked: bool = False,
    expanded_query: Optional[str] = None  # NEW: Track query expansion
):
    """
    Log detailed RAG context retrieval information.
    
    Args:
        session_id: Session/request ID
        query: User query
        documents: List of retrieved documents
        context: Formatted context string
        retrieval_time: Time taken for retrieval (seconds)
        detected_classes: List of detected classes (if any)
        success: Whether retrieval was successful
        error: Error message (if failed)
        is_reranked: If True, log to rag_context_rerank.log, else rag_context.log
        expanded_query: Expanded query (if query expansion was applied)
    """
    target_logger = rag_rerank_logger if is_reranked else rag_context_logger
    log_prefix = "[RAG_RERANK]" if is_reranked else "[RAG_CONTEXT]"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Separator
    separator = "=" * 100
    sub_separator = "-" * 100
    
    # Start log entry
    log_lines = [
        "",
        separator,
        f"{log_prefix} {timestamp}",
        f"Session ID: {session_id}",
        f"Query: \"{query}\"",
    ]
    
    # Add query expansion info if applicable
    if expanded_query and expanded_query != query:
        added_words = len(expanded_query.split()) - len(query.split())
        log_lines.extend([
            f"🔍 Query Expansion: +{added_words} synonyms",
            f"Expanded Query: \"{expanded_query}\"",
        ])
    
    log_lines.append(sub_separator)
    
    if not success:
        log_lines.extend([
            "",
            "❌ RETRIEVAL FAILED",
            f"Error: {error}",
            separator,
            ""
        ])
        target_logger.info("\n".join(log_lines))
        return
    
    # Analyze documents - distinguish between examples, info, and rules
    example_docs = []
    info_docs = []
    rules_docs = []
    
    for d in documents:
        doc_type = d.metadata.get('type', '')
        source = d.metadata.get('source', '')
        
        if source == 'manufacturing_rules' or 'rules' in source.lower():
            rules_docs.append(d)
        elif doc_type == 'info':
            info_docs.append(d)
        else:
            example_docs.append(d)
    
    # Calculate sizes
    total_size = len(context)
    total_size_kb = total_size / 1024
    
    # Determine retrieval type
    if rules_docs and not example_docs:
        retrieval_type = "RULES RETRIEVAL (Unified Processing)"
    elif example_docs and not rules_docs:
        retrieval_type = "EXAMPLES RETRIEVAL (Code Generation)"
    else:
        retrieval_type = "MIXED RETRIEVAL"
        
    if is_reranked:
        retrieval_type += " (RERANKED RESULT)"
    else:
        retrieval_type += " (RAW RETRIEVAL)"
    
    # Simple header
    log_lines.extend([
        "",
        f"🎯 TYPE: {retrieval_type}",
        f"📊 STATS: {len(example_docs)} examples | {len(info_docs)} info | {len(rules_docs)} rules | {total_size_kb:.2f}KB | {retrieval_time:.2f}s",
        ""
    ])
    
    # Detected classes section (compact)
    if detected_classes:
        log_lines.append(f"🏷️  CLASSES: {', '.join(detected_classes)}")
        log_lines.append("")
    
    # Examples section with full content
    if example_docs:
        log_lines.extend([
            f"📄 EXAMPLES RETRIEVED ({len(example_docs)}):",
            ""
        ])
        for i, doc in enumerate(example_docs, 1):
            source = doc.metadata.get('source', 'Unknown')
            # Get relative path for cleaner display
            if PROJECT_ROOT in source:
                source = source.replace(PROJECT_ROOT, '').lstrip(os.sep).lstrip('/')
            
            similarity = doc.metadata.get('similarity_score', 'N/A')
            if isinstance(similarity, float):
                similarity_str = f"{similarity:.3f}"
            else:
                similarity_str = str(similarity)
            
            # Show Rerank Score if available
            rerank_score = doc.metadata.get('rerank_score')
            score_display = ""
            if rerank_score is not None:
                score_display = f" | Rerank Score: {rerank_score:.4f}"
            
            content_size = len(doc.page_content)
            content_size_kb = content_size / 1024
            
            # Header for this example
            log_lines.append(f"  {'='*95}")
            log_lines.append(f"  EXAMPLE {i}/{len(example_docs)}")
            log_lines.append(f"  Source: {source}")
            log_lines.append(f"  Similarity: {similarity_str}{score_display} | Size: {content_size_kb:.2f}KB ({content_size:,} chars)")
            log_lines.append(f"  {'='*95}")
            log_lines.append("")
            
            # Full content of the example
            log_lines.append("  CONTENT:")
            log_lines.append(f"  {'-'*95}")
            # Indent each line of content for better readability
            for line in doc.page_content.split('\n'):
                log_lines.append(f"  {line}")
            log_lines.append(f"  {'-'*95}")
            log_lines.append("")
        
        log_lines.append("")
    
    # Info files section with full content
    if info_docs:
        # Group info docs by class
        info_by_class: Dict[str, List[Document]] = {}
        for doc in info_docs:
            class_name = doc.metadata.get('class', 'Unknown')
            if class_name not in info_by_class:
                info_by_class[class_name] = []
            info_by_class[class_name].append(doc)
        
        log_lines.extend([
            f"ℹ️  INFO FILES RETRIEVED ({len(info_docs)}):",
            ""
        ])
        
        info_counter = 0
        for class_name, class_docs in info_by_class.items():
            log_lines.append(f"  📁 CLASS: {class_name} ({len(class_docs)} info files)")
            log_lines.append("")
            
            for doc in class_docs:
                info_counter += 1
                info_id = doc.metadata.get('info_id', 'N/A')
                
                # Extract title from page_content
                title = "Unknown"
                for line in doc.page_content.split('\n'):
                    if line.startswith('Title:'):
                        title = line.replace('Title:', '').strip()
                        break
                
                content_size = len(doc.page_content)
                content_size_kb = content_size / 1024

                # Show Rerank Score if available
                rerank_score = doc.metadata.get('rerank_score')
                score_display = ""
                if rerank_score is not None:
                    score_display = f" | Rerank Score: {rerank_score:.4f}"
                
                # Header for this info file
                log_lines.append(f"  {'='*95}")
                log_lines.append(f"  INFO FILE {info_counter}/{len(info_docs)}")
                log_lines.append(f"  Info ID: {info_id}")
                log_lines.append(f"  Title: {title}")
                log_lines.append(f"  Class: {class_name}")
                log_lines.append(f"  Size: {content_size_kb:.2f}KB ({content_size:,} chars){score_display}")
                log_lines.append(f"  {'='*95}")
                log_lines.append("")
                
                # Full content of the info file
                log_lines.append("  CONTENT:")
                log_lines.append(f"  {'-'*95}")
                # Indent each line of content for better readability
                for line in doc.page_content.split('\n'):
                    log_lines.append(f"  {line}")
                log_lines.append(f"  {'-'*95}")
                log_lines.append("")
            
        log_lines.append("")
    
    # Rules section with full content
    if rules_docs:
        # Group rules docs by class
        rules_by_class: Dict[str, List[Document]] = {}
        for doc in rules_docs:
            class_name = doc.metadata.get('class', 'Unknown')
            if class_name not in rules_by_class:
                rules_by_class[class_name] = []
            rules_by_class[class_name].append(doc)
        
        log_lines.extend([
            f"⚠️  MANUFACTURING RULES RETRIEVED ({len(rules_docs)}):",
            ""
        ])
        
        rule_counter = 0
        for class_name, class_docs in rules_by_class.items():
            log_lines.append(f"  📁 CLASS: {class_name} ({len(class_docs)} rules)")
            log_lines.append("")
            
            for doc in class_docs:
                rule_counter += 1
                rule_id = doc.metadata.get('rule_id', 'N/A')
                severity = doc.metadata.get('severity', 'info').upper()
                
                # Extract title from page_content
                title = "Unknown"
                for line in doc.page_content.split('\n'):
                    if line.startswith('Title:'):
                        title = line.replace('Title:', '').strip()
                        break
                
                content_size = len(doc.page_content)
                content_size_kb = content_size / 1024

                # Show Rerank Score if available
                rerank_score = doc.metadata.get('rerank_score')
                score_display = ""
                if rerank_score is not None:
                    score_display = f" | Rerank Score: {rerank_score:.4f}"
                
                # Header for this rule
                log_lines.append(f"  {'='*95}")
                log_lines.append(f"  RULE {rule_counter}/{len(rules_docs)}")
                log_lines.append(f"  Rule ID: {rule_id}")
                log_lines.append(f"  Title: {title}")
                log_lines.append(f"  Class: {class_name}")
                log_lines.append(f"  Severity: {severity}")
                log_lines.append(f"  Size: {content_size_kb:.2f}KB ({content_size:,} chars){score_display}")
                log_lines.append(f"  {'='*95}")
                log_lines.append("")
                
                # Full content of the rule
                log_lines.append("  CONTENT:")
                log_lines.append(f"  {'-'*95}")
                # Indent each line of content for better readability
                for line in doc.page_content.split('\n'):
                    log_lines.append(f"  {line}")
                log_lines.append(f"  {'-'*95}")
                log_lines.append("")
            
        log_lines.append("")
    
    # End separator
    log_lines.extend([
        separator,
        ""
    ])
    
    # Write to log
    target_logger.info("\n".join(log_lines))


def log_rag_context_summary(
    session_id: str,
    query: str,
    num_examples: int,
    num_info: int,
    total_size_kb: float,
    retrieval_time: float,
    detected_classes: Optional[List[str]] = None
):
    """
    Log a concise summary of RAG context retrieval (for console/main logs).
    
    Args:
        session_id: Session/request ID
        query: User query (will be truncated)
        num_examples: Number of examples retrieved
        num_info: Number of info files retrieved
        total_size_kb: Total context size in KB
        retrieval_time: Time taken for retrieval (seconds)
        detected_classes: List of detected classes (if any)
    """
    query_preview = query[:80] + "..." if len(query) > 80 else query
    
    class_str = ""
    if detected_classes:
        class_str = f" [{', '.join(detected_classes)}]"
    
    summary = (
        f"[RAG] ✅ Retrieved {num_examples} examples + {num_info} info{class_str} "
        f"({total_size_kb:.1f}KB, {retrieval_time:.2f}s)"
    )
    
    return summary


def get_rag_context_log_path() -> str:
    """Get the path to the RAG context log file."""
    return RAG_CONTEXT_LOG_PATH
