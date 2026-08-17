import os
import time
from typing import List, Union, Optional
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
import logging

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# OpenAI embedding model configuration
MODEL_NAME = 'text-embedding-3-large'
CLIENT: Optional[OpenAI] = None

# Rate limiting configuration
REQUEST_DELAY = 0.0  # No delay for rate limit testing
MAX_RETRIES = 3      # Maximum number of retries for failed requests
BATCH_SIZE = 65   # Process embeddings in larger batches for aggressive testing

def get_embedding_model() -> OpenAI:
    """
    Initializes and returns the OpenAI client.
    Requires OPENAI_API_KEY environment variable to be set.
    """
    global CLIENT
    if CLIENT is None:
        try:
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable is required")
            
            CLIENT = OpenAI(api_key=api_key)
            logger.debug(f"OpenAI embedding client ready ({MODEL_NAME})")
        except Exception as e:
            logger.error(f"Error initializing OpenAI embedding client: {e}")
            raise
    return CLIENT

def _embed_single_text_with_retry(client: OpenAI, text: str, task_type: str) -> Optional[np.ndarray]:
    """
    Generates embedding for a single text with retry mechanism.
    
    Args:
        client: The OpenAI client
        text: Text to embed
        task_type: Task type (not used by OpenAI, kept for compatibility)
        
    Returns:
        Embedding array or None if failed
    """
    for attempt in range(MAX_RETRIES):
        try:
            result = client.embeddings.create(
                model=MODEL_NAME,
                input=text
            )
            # Extract the embedding values from the result
            if result.data and len(result.data) > 0:
                embedding_values = result.data[0].embedding
            else:
                raise ValueError("No embeddings found in result")
            
            return np.array(embedding_values, dtype=np.float32)
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate_limit" in error_msg.lower():
                wait_time = (2 ** attempt) * REQUEST_DELAY  # Exponential backoff
                print(f"Rate limit hit, waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
                time.sleep(wait_time)
                continue
            elif "quota" in error_msg.lower():
                print(f"Quota exceeded error: {e}")
                return None
            else:
                print(f"Error on attempt {attempt + 1}: {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
                time.sleep(REQUEST_DELAY)
    
    return None

def get_embeddings(texts: Union[str, List[str]], task_type: str = "RETRIEVAL_DOCUMENT") -> Optional[np.ndarray]:
    """
    Generates embeddings for the given text(s) using OpenAI embedding model with rate limiting.

    Args:
        texts (Union[str, List[str]]): A single text string or a list of text strings.
        task_type (str): The task type for optimization. Default is "RETRIEVAL_DOCUMENT".
                        Options: SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING, 
                        RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, QUESTION_ANSWERING, 
                        FACT_VERIFICATION, CODE_RETRIEVAL_QUERY

    Returns:
        Optional[np.ndarray]: A numpy array of embeddings, or None if an error occurs.
                              If a single string is input, a 2D array with one row is returned.
                              If a list of strings is input, a 2D array with multiple rows is returned.
    """
    try:
        # Handle single string vs list of strings
        if isinstance(texts, str):
            texts_list = [texts]
        else:
            texts_list = texts
        
        # Filter out empty texts
        valid_texts = [text for text in texts_list if text and text.strip()]
        if not valid_texts:
            logger.warning("No valid texts provided for embedding")
            return None
        
        total_texts = len(valid_texts)
        logger.debug(f"Processing {total_texts} texts for embeddings...")

        all_embeddings = []
        
        # Initialize client
        client = get_embedding_model()
        
        # Process texts in batches
        for i in range(0, total_texts, BATCH_SIZE):
            batch = valid_texts[i:i + BATCH_SIZE]
            batch_embeddings = []
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total_texts + BATCH_SIZE - 1) // BATCH_SIZE

            # A retrieval embeds one query = one batch, so this was two lines of
            # "1/1" and "Done" per search. Only an index build has enough batches
            # for progress to mean anything, and that runs at DEBUG too.
            logger.debug(f"Embedding batch {batch_num}/{total_batches}")

            for j, text in enumerate(batch):
                embedding = _embed_single_text_with_retry(client, text, task_type)
                if embedding is not None:
                    batch_embeddings.append(embedding)
                else:
                    logger.error(f"Failed to get embedding for text at index {i + j}")
                    return None

                # Add delay between requests to respect rate limits
                if j < len(batch) - 1:  # Don't delay after the last item in batch
                    time.sleep(REQUEST_DELAY)

            all_embeddings.extend(batch_embeddings)

            # Add extra delay between batches
            if i + BATCH_SIZE < total_texts:
                time.sleep(REQUEST_DELAY * 2)

        if not all_embeddings:
            logger.error("No embeddings were successfully generated")
            return None

        # Convert to numpy array
        embeddings_array = np.array(all_embeddings)

        logger.debug(f"Generated {len(embeddings_array)} embeddings from API")
        
        return embeddings_array
        
    except Exception as e:
        logger.error(f"Error generating embeddings with OpenAI: {e}")
        return None

if __name__ == "__main__":
    # Check if API key is available
    if not os.getenv('OPENAI_API_KEY'):
        print("Warning: OPENAI_API_KEY environment variable not set.")
        print("Please set your OpenAI API key to test the embedding functionality.")
        print("Example: export OPENAI_API_KEY='your_api_key_here'")
        exit(1)
    
    # Example usage:
    sample_texts = [
        "This is an example sentence.",
        "Each sentence is converted to a vector.",
        "Part.makeBox(10, 20, 5)",
        "def create_sphere(radius):\n    return Part.makeSphere(radius)"
    ]
    
    print("Testing OpenAI embeddings...")
    embeddings_array = get_embeddings(sample_texts)
    
    if embeddings_array is not None:
        print(f"\nGenerated embeddings for {len(sample_texts)} texts.")
        print(f"Shape of embeddings array: {embeddings_array.shape}")
        print(f"Embedding for the first text (first 5 dimensions): {embeddings_array[0][:5]}")

    print("\nTesting single text embedding...")
    single_text_embedding = get_embeddings("Just one piece of text.")
    if single_text_embedding is not None:
        print(f"Shape of single text embedding array: {single_text_embedding.shape}")
        print(f"Single text embedding (first 5 dimensions): {single_text_embedding[0][:5]}")

    # Test with different task types
    print("\nTesting with RETRIEVAL_DOCUMENT task type...")
    doc_embedding = get_embeddings("This is a document for retrieval.", task_type="RETRIEVAL_DOCUMENT")
    if doc_embedding is not None:
        print(f"Document embedding shape: {doc_embedding.shape}")

    # Test with chunks from chunking.py (requires chunking.py to be in the same path or PYTHONPATH)
    try:
        from chunking import chunk_guide_en
        print("\nTesting with guide chunks...")
        guide_chunks_data = chunk_guide_en()
        if guide_chunks_data:
            # Take a small sample of text_content for embedding
            sample_guide_texts = [chunk['text_content'] for chunk in guide_chunks_data[:2]]
            guide_embeddings = get_embeddings(sample_guide_texts, task_type="RETRIEVAL_DOCUMENT")
            if guide_embeddings is not None:
                print(f"Generated embeddings for 2 guide chunks.")
                print(f"Shape of guide_embeddings array: {guide_embeddings.shape}")
    except ImportError:
        print("\nSkipping chunking.py import test as it's not found in the current path.")
    except Exception as e:
        print(f"\nError during test with chunking.py: {e}")

    print("\nOpenAI embedding test completed!")
