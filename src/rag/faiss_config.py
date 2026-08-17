import os
import logging
import psutil

def log_system_memory():
    """Log current system memory usage."""
    try:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # Convert bytes to GB for readability
        total_ram_gb = memory.total / (1024**3)
        available_ram_gb = memory.available / (1024**3)
        used_ram_gb = memory.used / (1024**3)
        ram_percent = memory.percent
        
        total_swap_gb = swap.total / (1024**3)
        used_swap_gb = swap.used / (1024**3)
        swap_percent = swap.percent
        
        logger = logging.getLogger('faiss_config')

        
        # Warning if memory usage is high

        
        return {
            'total_ram_gb': total_ram_gb,
            'available_ram_gb': available_ram_gb,
            'used_ram_gb': used_ram_gb,
            'ram_percent': ram_percent,
            'total_swap_gb': total_swap_gb,
            'used_swap_gb': used_swap_gb,
            'swap_percent': swap_percent
        }
    except Exception as e:
        logger = logging.getLogger('faiss_config')
        logger.error(f"Failed to get memory information: {e}")
        return None

def configure_faiss_cpu_only():
    """
    Configure FAISS to use CPU only and suppress GPU-related warnings.
    This should be called before importing faiss in any module.
    """
    # Log system memory before FAISS configuration
    memory_info = log_system_memory()
    
    logger = logging.getLogger('faiss_config')
    logger.debug("Configuring FAISS for CPU-only usage...")
    
    # Disable GPU support to avoid warnings
    os.environ['FAISS_DISABLE_GPU'] = '1'
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    
    # Handle OpenMP library conflicts
    os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
    os.environ['OMP_NUM_THREADS'] = '1'
    
    # Suppress FAISS logging for GPU warnings
    logging.getLogger('faiss').setLevel(logging.ERROR)
    
    # Import faiss after setting environment variables
    import faiss
    
    # Set CPU thread count for optimal performance
    faiss.omp_set_num_threads(1)  # Use single thread to avoid conflicts
    
    logger.debug("FAISS CPU-only configuration completed successfully")
    
    # Log memory after FAISS import
    memory_after = log_system_memory()
    if memory_info and memory_after:
        memory_diff = memory_after['used_ram_gb'] - memory_info['used_ram_gb']
        logger.debug(f"Memory usage change after FAISS import: {memory_diff:+.3f} GB")
    
    return faiss

# Configure FAISS when this module is imported
faiss = configure_faiss_cpu_only() 