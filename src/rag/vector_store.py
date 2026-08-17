from .faiss_config import faiss
import logging
import numpy as np
import os
import sys # Add sys for path manipulation
from typing import List, Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Configure FAISS to use CPU only and suppress GPU warnings
os.environ['FAISS_DISABLE_GPU'] = '1'
faiss.omp_set_num_threads(1)  # Set number of threads for CPU operations

# Ensure src is in path for direct execution if needed
if __name__ == '__main__' and (__package__ is None or "." not in __package__):
    # This allows running the script directly from the project root (e.g., python src/rag/vector_store.py)
    # by adding the 'src' directory to sys.path so that 'from rag.embedding' works.
    # However, the functions within this file use relative imports like 'from .embedding',
    # which expect 'vector_store.py' to be part of the 'rag' package.
    # For the main execution block, we'll use absolute-like imports from 'rag'.
    # For the functions themselves, the relative imports are correct for package usage.

    # If running src/rag/vector_store.py directly, the top-level 'from .embedding' will fail.
    # We need to ensure that when this script is run directly, it can find 'embedding' and 'chunking'.
    # The functions themselves (build_and_save_index etc.) use 'from .embedding', which is fine
    # when vector_store is imported as a module.
    # The __main__ block needs a different import strategy.

    # Let's adjust the top-level import for when the script is run directly.
    # This is a bit tricky because the functions *inside* this file use relative imports.
    # A common pattern is to have test code separate or use a test runner that handles paths.
    # For now, we'll make the __main__ block robust to finding its sibling modules.

    # If running `python src/rag/vector_store.py`, `src` needs to be in path for `from rag...`
    # If running `python vector_store.py` from within `src/rag/`, then `from .embedding` might work
    # if Python treats `src/rag` as the current package context.

    # The original error was "ImportError: attempted relative import with no known parent package"
    # on the line "from .embedding import get_embeddings, get_embedding_model"
    # This means when `python src/rag/vector_store.py` is run, `vector_store.py` is __main__
    # and has no defined parent package.

    # We will modify the imports within the __main__ block to be absolute from `rag`
    # and keep the top-level relative import for when this file is imported as a module.
    # The top-level relative import will only work if this file is part of a package.
    # This is a common Python import challenge.

    # Let's assume the script is run from the project root.
    # The functions in this file will use the relative import.
    # The __main__ block will use an absolute import from `rag`.
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # When run directly (__name__ == '__main__'), use absolute-like import after sys.path modification
    from src.rag.embedding import get_embeddings, get_embedding_model
else:
    # When imported as a module, use relative import
    from .embedding import get_embeddings, get_embedding_model


# Ensure the index directory exists
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
INDEX_DIR = os.path.join(PROJECT_ROOT, "data", "index")
os.makedirs(INDEX_DIR, exist_ok=True)

# We'll store original texts/metadata separately as FAISS only stores vectors.
# This will be a simple list of dictionaries for now.
# In a more robust system, this might be a database or a more structured file.
METADATA_STORE: List[Dict[str, Any]] = []
METADATA_FILE_PATH = os.path.join(INDEX_DIR, "metadata_store.json") # Simple JSON for persistence

import json
from pathlib import Path

class PathEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Path):
            return str(obj)
        return json.JSONEncoder.default(self, obj)

def save_metadata_store():
    """Saves the current METADATA_STORE to a JSON file."""
    try:
        with open(METADATA_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(METADATA_STORE, f, indent=4, cls=PathEncoder)
    except Exception as e:
        print(f"Error saving metadata store: {e}")

def load_metadata_store():
    """Loads the METADATA_STORE from a JSON file if it exists."""
    global METADATA_STORE
    if os.path.exists(METADATA_FILE_PATH):
        try:
            with open(METADATA_FILE_PATH, "r", encoding="utf-8") as f:
                METADATA_STORE = json.load(f)
        except Exception as e:
            print(f"Error loading metadata store: {e}")
            METADATA_STORE = [] # Reset if loading fails
    else:
        METADATA_STORE = []


def build_and_save_index(
    documents: List[Dict[str, Any]],
    index_name: str = "faiss_index.index"
) -> Optional[faiss.Index]:
    """
    Builds a FAISS index from a list of document dictionaries and saves it.
    Each document dictionary should have a 'text_content' field for embedding,
    and other fields will be stored as metadata.

    Args:
        documents (List[Dict[str, Any]]): A list of dictionaries, where each dict
                                          represents a document and must contain
                                          a 'text_content' key for the text to embed.
        index_name (str): Name of the FAISS index file to save.

    Returns:
        Optional[faiss.Index]: The created FAISS index, or None if an error occurs.
    """
    global METADATA_STORE
    texts_to_embed = [doc.get("text_content", "") for doc in documents if doc.get("text_content")]
    if not texts_to_embed:
        print("No text content found in documents to build index.")
        return None

    print("Generating embeddings...")
    embeddings = get_embeddings(texts_to_embed)
    if embeddings is None or embeddings.shape[0] == 0:
        print("Failed to generate embeddings or no embeddings generated.")
        return None

    print("Building FAISS index...")
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)  # Using L2 distance for similarity
    index.add(np.array(embeddings, dtype=np.float32))

    print("Saving index and metadata...")
    index_path = os.path.join(INDEX_DIR, index_name)
    try:
        faiss.write_index(index, index_path)
        print(f"FAISS index built with {index.ntotal} vectors and saved to {index_path}")

        # Store metadata for the documents that were successfully embedded
        METADATA_STORE = [] # Reset before building a new index
        doc_idx = 0
        for i, doc_meta in enumerate(documents):
            if doc_meta.get("text_content"): # Only store metadata for docs that had content
                 METADATA_STORE.append({
                    "id": doc_idx, # This ID corresponds to the FAISS index position
                    **doc_meta # Store all original fields from the document
                })
                 doc_idx +=1
        save_metadata_store()
        print(f"Metadata for {len(METADATA_STORE)} documents saved.")
        print("Done")

    except Exception as e:
        print(f"Error saving FAISS index or metadata: {e}")
        return None

    return index

def load_index(index_name: str = "faiss_index.index") -> Optional[faiss.Index]:
    """
    Loads a FAISS index from the specified file.
    Also loads the corresponding metadata.
    """
    index_path = os.path.join(INDEX_DIR, index_name)
    if not os.path.exists(index_path):
        logger.error(f"FAISS index file not found at {index_path}")
        return None
    try:
        index = faiss.read_index(index_path)
        load_metadata_store() # Load metadata associated with this index
        # The caller (initialize_retriever) reports vector and metadata counts
        # once the index is installed; this only adds where it came from.
        logger.debug(f"FAISS index loaded from {index_path}")
        return index
    except Exception as e:
        logger.error(f"Error loading FAISS index or metadata: {e}")
        return None

def add_to_index(
    index: faiss.Index,
    new_documents: List[Dict[str, Any]],
    index_name: str = "faiss_index.index" # To re-save the index and metadata
) -> bool:
    """
    Adds new documents to an existing FAISS index and updates metadata.

    Args:
        index (faiss.Index): The existing FAISS index.
        new_documents (List[Dict[str, Any]]): List of new document dictionaries.
        index_name (str): Name of the FAISS index file to re-save.

    Returns:
        bool: True if successful, False otherwise.
    """
    global METADATA_STORE
    texts_to_embed = [doc.get("text_content", "") for doc in new_documents if doc.get("text_content")]
    if not texts_to_embed:
        print("No text content found in new documents to add to index.")
        return False

    new_embeddings = get_embeddings(texts_to_embed)
    if new_embeddings is None or new_embeddings.shape[0] == 0:
        print("Failed to generate embeddings for new documents.")
        return False

    index.add(np.array(new_embeddings, dtype=np.float32))

    # Update metadata store
    start_id = len(METADATA_STORE)
    doc_idx = 0
    for i, doc_meta in enumerate(new_documents):
        if doc_meta.get("text_content"):
            METADATA_STORE.append({
                "id": start_id + doc_idx,
                **doc_meta
            })
            doc_idx +=1

    # Re-save index and metadata
    index_path = os.path.join(INDEX_DIR, index_name)
    try:
        faiss.write_index(index, index_path)
        save_metadata_store()
        print(f"FAISS index updated with {new_embeddings.shape[0]} new vectors. Total vectors: {index.ntotal}.")
        print(f"Metadata store updated. Total entries: {len(METADATA_STORE)}.")
        return True
    except Exception as e:
        print(f"Error saving updated FAISS index or metadata: {e}")
        return False


def search_index(
    index: faiss.Index,
    query_text: str,
    k: int = 5
) -> List[Dict[str, Any]]:
    """
    Performs a similarity search on the FAISS index.

    Args:
        index (faiss.Index): The FAISS index to search.
        query_text (str): The query text.
        k (int): The number of nearest neighbors to retrieve.

    Returns:
        List[Dict[str, Any]]: A list of retrieved document metadata, ordered by similarity.
                              Returns an empty list if search fails or no results.
    """
    query_embedding = get_embeddings([query_text]) # get_embeddings expects a list or single string
    if query_embedding is None or query_embedding.shape[0] == 0:
        print("Failed to generate query embedding.")
        return []

    # FAISS search returns distances (D) and indices (I) of the k nearest neighbors
    # Ensure query_embedding is float32 and 2D
    distances, indices = index.search(np.array(query_embedding, dtype=np.float32).reshape(1, -1), k)

    results = []
    if indices.size > 0:
        for i in range(indices.shape[1]): # Iterate through the k results for the single query
            doc_index = indices[0][i]
            
            # FAISS returns -1 if less than k results are found
            if doc_index == -1:
                continue
                
            if 0 <= doc_index < len(METADATA_STORE):
                # Retrieve metadata using the FAISS index
                # Add distance to the result for potential re-ranking or inspection
                result_doc = METADATA_STORE[doc_index].copy() # Make a copy to avoid modifying original
                result_doc["similarity_score"] = float(distances[0][i]) # L2 distance, smaller is better
                results.append(result_doc)
            else:
                # Only warn if index is valid but out of bounds (which implies sync issue between index and metadata)
                print(f"Warning: Retrieved index {doc_index} is out of bounds for metadata store (size {len(METADATA_STORE)}).")
    return results

if __name__ == "__main__":
    # Imports for when this script is run directly.
    # These need to be absolute from the perspective of the project root,
    # which we've added to sys.path.
    from src.rag.embedding import get_embedding_model as main_get_embedding_model # Alias to avoid conflict
    from src.rag.embedding import get_embeddings # Used by search_index and build_and_save_index
    from src.rag.chunking import chunk_script_files

    # --- Test Building and Saving ---
    print("--- Testing Index Building and Saving ---")
    # Ensure embedding model is loaded once using the aliased import
    main_get_embedding_model()

    # Use chunks from chunking.py for testing
    try:
        example_docs = chunk_script_files() # list of dicts


        all_documents_for_index = []
        # Prepare documents with a 'text_content' key
        for doc in example_docs:
            # example_docs already have 'text_content' from chunking
            all_documents_for_index.append(doc)


        if all_documents_for_index:
            # Clean up old index and metadata if they exist for a fresh test
            if os.path.exists(os.path.join(INDEX_DIR, "test_main.index")):
                os.remove(os.path.join(INDEX_DIR, "test_main.index"))
            if os.path.exists(METADATA_FILE_PATH): # Assuming METADATA_FILE_PATH is used by build_and_save_index
                 os.remove(METADATA_FILE_PATH)
            METADATA_STORE = [] # Clear in-memory store too

            built_index = build_and_save_index(all_documents_for_index, index_name="test_main.index")
            if built_index:
                print(f"Successfully built and saved 'test_main.index' with {built_index.ntotal} entries.")
                print(f"Metadata store has {len(METADATA_STORE)} entries.")

                # --- Test Loading ---
                print("\n--- Testing Index Loading ---")
                loaded_index = load_index(index_name="test_main.index")
                if loaded_index:
                    print(f"Successfully loaded 'test_main.index' with {loaded_index.ntotal} entries.")
                    print(f"Loaded metadata store has {len(METADATA_STORE)} entries.")

                    # --- Test Searching ---
                    print("\n--- Testing Index Searching ---")
                    search_query = "How to make a box in FreeCAD?"
                    search_results = search_index(loaded_index, search_query, k=3)
                    if search_results:
                        print(f"\nSearch results for '{search_query}':")
                        for i, res in enumerate(search_results):
                            print(f"\nResult {i+1} (ID: {res['id']}, Score: {res['similarity_score']:.4f}):")
                            print(f"  Source: {res.get('source', 'N/A')}")
                            if 'command_signature' in res: # From guide_en
                                print(f"  Command: {res['command_signature']}")
                            elif 'script_name' in res: # From example.txt
                                print(f"  Script Name: {res['script_name']}")
                            elif 'user_message' in res: # From chat_history
                                print(f"  User Message: {res['user_message']}")
                            print(f"  Text (first 100 chars): {res.get('text_content', '')[:100]}...")
                    else:
                        print("No search results found or search failed.")

                    # --- Test Adding to Index ---
                    print("\n--- Testing Adding to Index ---")
                    new_docs_to_add = [
                        {"text_content": "This is a brand new document about advanced spheres.", "source": "manual_add_test1"},
                        {"text_content": "Part.makeSphereAdvanced(radius=10, segments=64)", "source": "manual_add_test2"}
                    ]
                    initial_total = loaded_index.ntotal
                    initial_meta_total = len(METADATA_STORE)

                    add_success = add_to_index(loaded_index, new_docs_to_add, index_name="test_main.index")
                    if add_success:
                        print(f"Successfully added {len(new_docs_to_add)} new documents.")
                        print(f"Index total changed from {initial_total} to {loaded_index.ntotal}.")
                        print(f"Metadata total changed from {initial_meta_total} to {len(METADATA_STORE)}.")

                        # Verify by searching for the new content
                        search_after_add = search_index(loaded_index, "advanced spheres", k=1)
                        if search_after_add and search_after_add[0]['source'] == "manual_add_test1":
                            print("Search after add successful for 'advanced spheres'.")
                        else:
                            print("Search after add failed or found wrong document for 'advanced spheres'.")
                            if search_after_add: print(search_after_add[0])
                    else:
                        print("Failed to add new documents to the index.")

        else:
            print("No documents to index from chunking outputs.")

    except ImportError as e:
        print(f"ImportError during testing: {e}. Ensure chunking.py is accessible.")
    except Exception as e:
        print(f"An error occurred during testing: {e}")
