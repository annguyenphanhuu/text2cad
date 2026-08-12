#!/usr/bin/env python3
"""
Async Optimizations Module
Implements parallel processing and async optimizations for bottleneck operations.
"""

import asyncio
import concurrent.futures
try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False
    
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable
from datetime import datetime
import json
import shutil
from functools import wraps

logger = logging.getLogger(__name__)

class AsyncOptimizer:
    """
    Async optimization utilities for parallel processing
    """
    
    def __init__(self, max_workers: int = 4):
        """
        Initialize async optimizer
        
        Args:
            max_workers: Maximum number of worker threads for CPU-bound tasks
        """
        self.max_workers = max_workers
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
        
    async def cleanup(self):
        """Clean up thread and process pools"""
        self.thread_pool.shutdown(wait=True)
        self.process_pool.shutdown(wait=True)

def async_timer(func_name: str = None):
    """Decorator to time async functions"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            name = func_name or func.__name__
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"[ASYNC_TIMER] {name} completed in {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"[ASYNC_TIMER] {name} failed after {duration:.2f}s: {e}")
                raise
        return wrapper
    return decorator

class AsyncFileOperations:
    """Async file operations for parallel I/O"""
    
    @staticmethod
    @async_timer("file_save")
    async def save_file_async(content: str, file_path: Path) -> bool:
        """
        Save file asynchronously
        
        Args:
            content: File content to save
            file_path: Path to save the file
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(content)
            else:
                # Fallback to synchronous file operations in thread pool
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: file_path.write_text(content, encoding='utf-8')
                )
            
            logger.info(f"[ASYNC_FILE] Saved file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"[ASYNC_FILE] Error saving file {file_path}: {e}")
            return False
    
    @staticmethod
    @async_timer("file_copy")
    async def copy_file_async(src_path: Path, dst_path: Path) -> bool:
        """
        Copy file asynchronously
        
        Args:
            src_path: Source file path
            dst_path: Destination file path
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Ensure destination directory exists
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Use thread pool for file copy (CPU-bound operation)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, shutil.copy2, str(src_path), str(dst_path))
            
            logger.info(f"[ASYNC_FILE] Copied file: {src_path} -> {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"[ASYNC_FILE] Error copying file {src_path} -> {dst_path}: {e}")
            return False
    
    @staticmethod
    @async_timer("json_save")
    async def save_json_async(data: Dict[str, Any], file_path: Path) -> bool:
        """
        Save JSON data asynchronously
        
        Args:
            data: Data to save as JSON
            file_path: Path to save the JSON file
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert to JSON string in thread pool (CPU-bound)
            loop = asyncio.get_event_loop()
            json_content = await loop.run_in_executor(
                None, 
                json.dumps, 
                data, 
                {"indent": 2, "ensure_ascii": False}
            )
            
            # Write file asynchronously
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(json_content)
            else:
                # Fallback to synchronous file operations in thread pool
                await loop.run_in_executor(
                    None,
                    lambda: file_path.write_text(json_content, encoding='utf-8')
                )
            
            logger.info(f"[ASYNC_JSON] Saved JSON file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"[ASYNC_JSON] Error saving JSON file {file_path}: {e}")
            return False

class AsyncProcessingPipeline:
    """Async processing pipeline for parallel task execution"""
    
    def __init__(self, optimizer: AsyncOptimizer):
        self.optimizer = optimizer
    
    @async_timer("parallel_file_operations")
    async def parallel_file_operations(
        self, 
        operations: List[Tuple[Callable, tuple, dict]]
    ) -> List[Any]:
        """
        Execute multiple file operations in parallel
        
        Args:
            operations: List of (function, args, kwargs) tuples
            
        Returns:
            List of results from all operations
        """
        tasks = []
        for func, args, kwargs in operations:
            task = asyncio.create_task(func(*args, **kwargs))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log results
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"[PARALLEL_FILES] Completed {success_count}/{len(results)} operations successfully")
        
        return results
    
    @async_timer("parallel_processing")
    async def parallel_processing_with_fallback(
        self,
        primary_tasks: List[Callable],
        fallback_tasks: List[Callable] = None,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        Execute tasks in parallel with fallback options
        
        Args:
            primary_tasks: Primary tasks to execute
            fallback_tasks: Fallback tasks if primary fails
            timeout: Timeout for task execution
            
        Returns:
            Dictionary with results and status
        """
        results = {
            "primary_results": [],
            "fallback_results": [],
            "success": False,
            "errors": []
        }
        
        try:
            # Execute primary tasks with timeout
            primary_results = await asyncio.wait_for(
                asyncio.gather(*[task() for task in primary_tasks], return_exceptions=True),
                timeout=timeout
            )
            
            results["primary_results"] = primary_results
            
            # Check if any primary task failed
            failed_tasks = [r for r in primary_results if isinstance(r, Exception)]
            
            if failed_tasks and fallback_tasks:
                logger.warning(f"[PARALLEL_PROC] {len(failed_tasks)} primary tasks failed, executing fallbacks")
                
                fallback_results = await asyncio.gather(
                    *[task() for task in fallback_tasks], 
                    return_exceptions=True
                )
                results["fallback_results"] = fallback_results
            
            # Determine overall success
            successful_primary = len([r for r in primary_results if not isinstance(r, Exception)])
            results["success"] = successful_primary > 0
            
            logger.info(f"[PARALLEL_PROC] Completed with {successful_primary} successful primary tasks")
            
        except asyncio.TimeoutError:
            error_msg = f"Parallel processing timed out after {timeout}s"
            logger.error(f"[PARALLEL_PROC] {error_msg}")
            results["errors"].append(error_msg)
            
        except Exception as e:
            error_msg = f"Parallel processing failed: {e}"
            logger.error(f"[PARALLEL_PROC] {error_msg}")
            results["errors"].append(error_msg)
        
        return results

class AsyncLLMProcessor:
    """Async LLM processing with connection pooling and optional rate limiting"""
    
    def __init__(self, max_concurrent: int = 3, rate_limit_per_minute: Optional[int] = None):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        # Optional simple rate limiter (requests per rolling 60 seconds)
        self.rate_limit_per_minute = rate_limit_per_minute
        self._request_timestamps: List[float] = []

    async def _apply_rate_limit(self):
        if not self.rate_limit_per_minute:
            return
        # Clean up timestamps older than 60s
        now = time.time()
        window_start = now - 60
        self._request_timestamps = [t for t in self._request_timestamps if t >= window_start]
        # If over the limit, wait until one slot frees
        while len(self._request_timestamps) >= self.rate_limit_per_minute:
            # Sleep for a short interval to allow window to slide
            await asyncio.sleep(0.1)
            now = time.time()
            window_start = now - 60
            self._request_timestamps = [t for t in self._request_timestamps if t >= window_start]
        # Record this request timestamp
        self._request_timestamps.append(time.time())
    
    @async_timer("llm_request")
    async def process_llm_request_async(
        self, 
        llm_client, 
        prompt: str, 
        **kwargs
    ) -> Dict[str, Any]:
        """
        Process LLM request asynchronously with rate limiting
        
        Args:
            llm_client: LLM client instance
            prompt: Prompt to process
            **kwargs: Additional arguments for LLM
            
        Returns:
            Dictionary with LLM response
        """
        async with self.semaphore:
            await self._apply_rate_limit()
            try:
                # Execute LLM request in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                
                if hasattr(llm_client, 'ainvoke'):
                    # Async LLM client
                    result = await llm_client.ainvoke(prompt, **kwargs)
                else:
                    # Sync LLM client - run in thread pool
                    result = await loop.run_in_executor(
                        None, 
                        llm_client.ainvoke, 
                        prompt
                    )
                
                return {
                    "success": True,
                    "result": result,
                    "error": None
                }
                
            except Exception as e:
                logger.error(f"[ASYNC_LLM] LLM request failed: {e}")
                return {
                    "success": False,
                    "result": None,
                    "error": str(e)
                }

    @async_timer("generic_request")
    async def process_request(self, func: Callable, *args, **kwargs):
        """
        Generic async processor for callables (e.g., chain.ainvoke(func, inputs)).
        Applies concurrency control and optional rate limiting.
        
        Args:
            func: Callable to execute (async preferred). For sync functions, runs in executor.
            *args: Positional args for callable
            **kwargs: Keyword args for callable
        
        Returns:
            Raw result from the callable.
        """
        async with self.semaphore:
            await self._apply_rate_limit()
            try:
                loop = asyncio.get_event_loop()
                # Try to call function and detect awaitable result
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                # If function itself is async but detected differently
                if hasattr(func, '__call__') and asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                # Fallback to running sync callable in executor
                return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            except Exception as e:
                logger.error(f"[ASYNC_GENERIC] Request failed: {e}")
                # Propagate error to preserve original behavior in caller
                raise

class AsyncRAGProcessor:
    """Async RAG processing with parallel retrieval"""
    
    def __init__(self, max_concurrent: int = 20):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    @async_timer("rag_retrieval")
    async def retrieve_context_async(
        self, 
        retriever, 
        query: str, 
        k: int = 5
    ) -> Dict[str, Any]:
        """
        Retrieve RAG context asynchronously
        
        Args:
            retriever: RAG retriever instance
            query: Query for context retrieval
            k: Number of documents to retrieve
            
        Returns:
            Dictionary with retrieved context
        """
        async with self.semaphore:
            try:
                # Execute retrieval in thread pool
                loop = asyncio.get_event_loop()
                
                if hasattr(retriever, 'aretrieve'):
                    # Async retriever
                    documents = await retriever.aretrieve(query, k=k)
                else:
                    # Sync retriever - run in thread pool
                    documents = await loop.run_in_executor(
                        None, 
                        retriever.get_relevant_documents, 
                        query
                    )
                
                return {
                    "success": True,
                    "documents": documents,
                    "query": query,
                    "error": None
                }
                
            except Exception as e:
                logger.error(f"[ASYNC_RAG] RAG retrieval failed: {e}")
                return {
                    "success": False,
                    "documents": [],
                    "query": query,
                    "error": str(e)
                }

# Global async optimizer instance
_async_optimizer = None

async def get_async_optimizer() -> AsyncOptimizer:
    """Get or create global async optimizer instance"""
    global _async_optimizer
    if _async_optimizer is None:
        _async_optimizer = AsyncOptimizer(max_workers=4)
    return _async_optimizer

async def cleanup_async_optimizer():
    """Cleanup global async optimizer"""
    global _async_optimizer
    if _async_optimizer:
        await _async_optimizer.cleanup()
        _async_optimizer = None