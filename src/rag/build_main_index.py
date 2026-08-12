import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Ensure src is in path for direct execution
if __name__ == '__main__' and (__package__ is None or __package__ == ''):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from src.rag.chunking import chunk_script_files, chunk_all_class_rules, chunk_all_info_files
from src.rag.vector_store import build_and_save_index, INDEX_DIR, METADATA_FILE_PATH
from src.rag.embedding import get_embedding_model # To initialize model

def main():
    print("Building the main RAG index...")

    # 1. Initialize embedding model (important before any embedding operation)
    print("Initializing embedding model...")
    try:
        get_embedding_model()
        
        print("Embedding model initialized.")
    except Exception as e:
        print(f"Fatal: Could not initialize embedding model: {e}")
        return

    # 2. Load and chunk all data sources
    print("Loading and chunking data sources...")

    # Set limits to avoid quota exhaustion for initial testing
    MAX_CHUNKS_EXAMPLES = 200  # Limit example chunks
    MAX_CHUNKS_RULES = 200  # Limit rule chunks
    MAX_CHUNKS_INFO = 200   # Limit info chunks

    example_docs_raw = chunk_script_files()[:MAX_CHUNKS_EXAMPLES]
    example_docs = []
    file_counts = {}
    for doc in example_docs_raw:
        # Use text_content from chunking (contains only request + shape type, NO code)
        # script_content is preserved in metadata for generation
        example_docs.append({
            **doc  # text_content, request_description, shape_type, script_content, source
        })
        source = doc['source']
        file_counts[source] = file_counts.get(source, 0) + 1

    print(f"Loaded {len(example_docs)} total example chunks (limited to {MAX_CHUNKS_EXAMPLES})")
    for file_path, count in file_counts.items():
        print(f"  - from {file_path}: {count} chunks")

    # ── Shape type statistics ──────────────────────────────────────────────
    shape_type_counts: dict[str, int] = {}
    no_shape_type_count = 0
    for doc in example_docs:
        st = doc.get("shape_type")
        if st:
            # Normalize to lowercase for grouping (e.g. "Plate" == "plate")
            key = st.strip().lower()
            shape_type_counts[key] = shape_type_counts.get(key, 0) + 1
        else:
            no_shape_type_count += 1

    print(f"\nShape type statistics ({len(shape_type_counts)} unique types):")
    for shape, count in sorted(shape_type_counts.items(), key=lambda x: -x[1]):
        print(f"  {shape}: {count}")
    if no_shape_type_count:
        print(f"  (no shape type): {no_shape_type_count}")

    # Load class rules
    rules_docs = chunk_all_class_rules()[:MAX_CHUNKS_RULES]
    print(f"Loaded {len(rules_docs)} rule chunks from all classes (limited to {MAX_CHUNKS_RULES})")

    # NEW: Load info files (technical standards like ISO/ANSI countersink specs)
    info_docs = chunk_all_info_files()[:MAX_CHUNKS_INFO]
    print(f"Loaded {len(info_docs)} info chunks from all info files (limited to {MAX_CHUNKS_INFO})")

    # Include rules and info in the index
    all_documents_for_index = example_docs + rules_docs + info_docs

    # ── BUILD SUMMARY ─────────────────────────────────────────────────────
    print("\n--- BUILD SUMMARY ---")
    print(f"  Examples  : {len(example_docs)}")
    print(f"  Shape types: {len(shape_type_counts)} unique types, {sum(shape_type_counts.values()) + no_shape_type_count} chunks")
    print(f"  Rules     : {len(rules_docs)}")
    print(f"  Info      : {len(info_docs)}")
    print(f"  TOTAL     : {len(all_documents_for_index)}")
    print("---------------------")

    if not all_documents_for_index:
        print("No documents found to build the index. Exiting.")
        return

    # 3. Define the main index name
    main_index_name = "main_rag_index.index"

    # Clean up old index and metadata if they exist for a fresh build
    main_index_file_path = os.path.join(INDEX_DIR, main_index_name)
    if os.path.exists(main_index_file_path):
        print(f"Removing existing index file: {main_index_file_path}")
        os.remove(main_index_file_path)
    if os.path.exists(METADATA_FILE_PATH): # METADATA_FILE_PATH is global from vector_store
        print(f"Removing existing metadata file: {METADATA_FILE_PATH}")
        os.remove(METADATA_FILE_PATH)

    # 4. Build and save the main index
    print(f"Building and saving FAISS index to '{main_index_name}' and metadata...")
    built_index = build_and_save_index(all_documents_for_index, index_name=main_index_name)

    if built_index:
        print(f"\nSuccessfully built and saved '{main_index_name}' with {built_index.ntotal} entries.")
        print(f"Metadata store saved to '{METADATA_FILE_PATH}'.")
        print("Main RAG index build complete.")
    else:
        print("\nFailed to build the main RAG index.")

if __name__ == "__main__":
    main()
