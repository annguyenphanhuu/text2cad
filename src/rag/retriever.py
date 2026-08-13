import os
import sys
import logging
import re
from typing import List, Dict, Any, Optional, Callable, NamedTuple, Sequence, Tuple
from langchain_core.documents import Document # Import Document
from src.utils.rag_context_logger import log_rag_context_retrieval
import time

# Conditional imports based on execution context
if __name__ == '__main__' and (__package__ is None or __package__ == ''):
    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from src.rag.vector_store import load_index, search_index, METADATA_STORE as faiss_metadata
    from src.rag.structured_data_loader import load_csv_to_structured_data
    from src.rag.embedding import get_embedding_model
    from src.utils.memory_monitor import memory_monitor
else:
    from .vector_store import load_index, search_index, METADATA_STORE as faiss_metadata
    from .structured_data_loader import load_csv_to_structured_data
    from .embedding import get_embedding_model
    from ..utils.memory_monitor import memory_monitor

# --- Configuration ---
FAISS_INDEX_NAME = "main_rag_index.index" # Default name for the combined index
# Use absolute paths based on project root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
FAISS_INDEX_PATH = os.path.join(PROJECT_ROOT, "data", "index", FAISS_INDEX_NAME)
METADATA_JSON_PATH = os.path.join(PROJECT_ROOT, "data", "index", "metadata_store.json")

# Configure logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

# Define paths to CSV files (supporting both old and new structures)
PERFORATED_SHEET_CSV_PATH = os.path.join(PROJECT_ROOT, "data", "Materials", "Sheet_Metal", "Perforated_Sheet", "dictionary.csv")
TOLE_CSV_PATH = os.path.join(PROJECT_ROOT, "data", "Materials", "Sheet_Metal", "Standard_Sheet", "tole_dictionary.csv")
TUBES_CSV_PATH = os.path.join(PROJECT_ROOT, "data", "Materials", "Tubes", "dictionary.csv")
# Fallback paths for backward compatibility
PERFORATED_SHEET_CSV_PATH_OLD = os.path.join(PROJECT_ROOT, "data", "Class", "Perforated sheet", "dictionary.csv")
TOLE_CSV_PATH_OLD = os.path.join(PROJECT_ROOT, "data", "Class", "Tole", "dictionary.csv")

# --- Global Variables to hold loaded data ---
faiss_index_instance = None
structured_perforated_sheet_data: Optional[Dict[str, Dict[str, Any]]] = None
structured_tole_data: Optional[Dict[str, Dict[str, Any]]] = None
structured_tubes_data: Optional[Dict[str, Dict[str, Any]]] = None


# LLM for class classification - will be set by caller
_classification_llm = None


def set_classification_llm(llm):
    """Set the LLM to use for class classification."""
    global _classification_llm
    _classification_llm = llm


def extract_shape_type_from_content(content: str) -> Optional[str]:
    """
    Extract shape type from example content using regex.
    Looks for '# Shape type: <type>' comment pattern.
    
    This is a temporary solution until the FAISS index is rebuilt with
    shape_type in metadata.
    
    Args:
        content: The example file content
        
    Returns:
        Shape type string (e.g., 'L-bracket', 'U-shaped', 'capot')
        or None if not found
        
    Examples:
        >>> extract_shape_type_from_content("# Shape type: L-bracket\\nimport FreeCAD...")
        'L-bracket'
        
        >>> extract_shape_type_from_content("# Shape type: U-shaped\\nimport FreeCAD...")
        'U-shaped'
    """
    match = re.search(r'#\s*Shape type:\s*(\S+)', content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

def classify_user_query_for_rules(user_query: str, classification_llm) -> List[str]:
    """
    Use rule-based keyword matching to determine which classes need rules retrieval.
    Returns list of class names that should have their rules retrieved.
    
    Note: classification_llm parameter is kept for backward compatibility but not used.
    
    IMPORTANT: Uses word-boundary matching for short keywords to prevent false positives:
    - "pli" must not match "appliqués" or "réplications"
    - "grille" removed (too generic, means "grid" in French)
    - "tap" must not match "tape", "taper" etc.
    """
    logger.debug("[RAG] Using keyword-based class detection")
    
    detected_classes = []
    query_lower = user_query.lower()
    
    # --- Helper: substring match for long/unique terms, word-boundary for short ---
    def _has_long_term(terms):
        return any(term in query_lower for term in terms)
    
    def _has_word(terms):
        return any(_match_word(query_lower, term) for term in terms)
    
    # Materials/Sheet_Metal/Perforated_Sheet
    # NOTE: "grille" removed - too generic in French (means "grid", triggers on regular hole grids)
    # NOTE: unaccented variants (tole perforee, perforee) added because query encoding may strip
    #       French diacritics (ô→o, é→e) — must still detect as Perforated Sheet.
    if _has_long_term(["perforated", "tôle filtrante", "tôle perforée", "perforated sheet",
                        "tole perforee", "tole perforé", "tôle perforee",
                        "perforee", "perforé"]) or \
       _has_word(["perforé", "mesh", "perfore"]):
        detected_classes.append("Materials/Sheet_Metal/Perforated_Sheet")

    
    # Materials/Sheet_Metal/Standard_Sheet
    if _has_long_term(["platine", "plaque", "tôle", "sheet", "console", "flasque", "socle", "panneau", "carter"]) or \
       _has_word(["patte", "base", "rack", "cadre"]):
        detected_classes.append("Materials/Sheet_Metal/Standard_Sheet")
    
    # Materials/Tubes
    if _has_long_term(["cylindrical", "cylindrique", "portique"]) or \
       _has_word(["tube", "pipe", "paroi"]):
        detected_classes.append("Materials/Tubes")
    
    # Materials/Profile
    if _has_long_term(["profilé", "channel", "extrusion"]) or \
       _has_word(["profile", "cornière", "angle", "beam"]):
        detected_classes.append("Materials/Profile")
    
    # Manufacturing_Processes/Forming/Bending
    # NOTE: "pli" uses word boundary to avoid matching "appliqués", "réplications" etc.
    # NOTE: Structural shape synonyms (u-shaped, capot, wing, etc.) added so that bent shapes
    #       described by structure — not by verb "bent/bend" — still trigger bending rule retrieval.
    #       This matters when the unified analysis output uses "Type: U-Bracket" / "wings: 40mm"
    #       without repeating the word "bent", which would otherwise silently skip bending DFM rules.
    # NOTE: "rectangular tube" / "square tube" added because bent-from-sheet tubes need B_05_TUBE
    #       (round welded/extruded tubes do not, so plain "tube" is excluded from this list).
    if _has_long_term(["l-bracket", "l-shaped", "l shape", "l-shape",
                        "u-bend", "z-bend", "grugeage",
                        "u-shaped", "u-shape", "z-shaped", "z-shape", "capot",
                        "u-bracket", "z-bracket",
                        "rectangular tube", "square tube",
                        "tube rectangulaire", "tube carré", "tube rect"]) or \
       _has_word(["bend", "bent", "flange", "folding", "fold", "forming",
                  "pli", "équerre", "berceau", "aile", "retour", "plié", "cornière",
                  "wing", "ailes", "bracket"]):
        detected_classes.append("Manufacturing_Processes/Forming/Bending")
    
    # Manufacturing_Processes/Cutting/Laser_Cutting
    if _has_long_term(["laser cutting", "laser cut", "découpe laser", "découpe", "drilling",
                        "gueule de loup", "cope cut", "demi-lune", "hexagone",
                        "circular", "cutout", "opening", "diameter", "Ø", "perforat"]) or \
       _has_word(["trous", "hole", "perçage", "slot", "fente", "évidement", "encoche",
                  "mortaise", "tenon", "oblong", "ouïe", "radius", "diametre", "diamètre",
                  "percé", "perce", "trouer"]):
        detected_classes.append("Manufacturing_Processes/Cutting/Laser_Cutting")
    
    # Manufacturing_Processes/Machining/Countersinking
    if _has_long_term(["countersink", "fraisurage", "flat head screw", "countersunk"]) or \
       _has_word(["fraisé", "fraiser", "lamage"]):
        detected_classes.append("Manufacturing_Processes/Machining/Countersinking")
    
    # Manufacturing_Processes/Machining/Threading
    # NOTE: "tap" uses word boundary to avoid matching "tape", "taper" etc.
    if _has_long_term(["threaded", "threading", "taraudage", "filetage", "tapping", "tige filetée", "passe-tige"]) or \
       _has_word(["taraudé", "tapped", "thread", "tap", "fileté", "goujon"]):
        detected_classes.append("Manufacturing_Processes/Machining/Threading")
    
    # Manufacturing_Processes/Machining/Engraving
    if _has_long_term(["engraving", "engrave", "gravure", "emprunte", "empreinte"]) or \
       _has_word(["gravé", "graver"]):
        detected_classes.append("Manufacturing_Processes/Machining/Engraving")
    
    # Manufacturing_Processes/Forming/Stamping
    if _has_long_term(["stamping", "estampage", "metal stamping", "press forming"]) or \
       _has_word(["stamp", "bridge", "estampé"]):
        detected_classes.append("Manufacturing_Processes/Forming/Stamping")

    logger.debug(f"[RAG] Keyword-based detected classes: {detected_classes}")
    return detected_classes


def _match_word(query_lower: str, word: str) -> bool:
    """Check if word appears as a whole word in query (word boundary matching).
    Uses regex \\b to prevent 'pli' from matching inside 'appliqués' etc."""
    import re
    return bool(re.search(r'\b' + re.escape(word) + r'\b', query_lower))

# Mapping from rules class path → info.json class_name.
# Single source of truth: keywords live in classify_user_query_for_rules ONLY.
# To add a new info class: (1) add its keywords to the rules classifier,
# (2) add the rules-path → info-class_name entry below.
_RULES_TO_INFO_CLASS_MAP: dict = {
    "Materials/Tubes":                                   "Tubes",
    "Manufacturing_Processes/Forming/Bending":           "Bending",
    "Manufacturing_Processes/Machining/Countersinking":  "Countersinking",
    "Manufacturing_Processes/Machining/Threading":       "Threading",
    "Materials/Sheet_Metal/Perforated_Sheet":            "Perforated_Sheet",
    "Manufacturing_Processes/Machining/Engraving":       "Engraving",
}

# Normalised shape_type → info.json class_name. Lets a detected shape inject its
# info class even when the raw query lacks the English keywords the rules
# classifier matches on (e.g. the user wrote French/Vietnamese).
_SHAPE_TYPE_TO_INFO_CLASS: dict = {
    "perforated sheet": "Perforated_Sheet",
    "perforated_sheet": "Perforated_Sheet",
}

# For bent-shape requests, suppress Bending info because the code-gen template
# for those shapes already handles bending natively — the info adds no value
# and only bloats the context window.
_SHAPE_INFO_SUPPRESS: dict = {
    "L-bracket":  {"Bending"},
    "U-shaped":   {"Bending"},
    "Z-shaped":   {"Bending"},
    "capot":      {"Bending"},
}


def classify_user_query_for_info(user_query: str, detected_shape_type: Optional[str] = None) -> List[str]:
    """
    Thin wrapper around classify_user_query_for_rules that maps detected rule
    classes to their corresponding info.json class_name values.

    No duplicate keyword logic — keywords live exclusively in
    classify_user_query_for_rules. To add a new info class just update
    _RULES_TO_INFO_CLASS_MAP.

    Args:
        user_query: The raw user query (same string passed to rules classifier).
        detected_shape_type: Optional shape type detected by query expander
            (e.g. "L-bracket"). Used to suppress irrelevant info classes AND
            to inject the matching info class even when keywords are absent
            (e.g. user writes in Vietnamese without "perforated").

    Returns:
        List of info class_name strings (e.g. ["Countersinking", "Threading"]).
    """
    rules_classes = classify_user_query_for_rules(user_query, classification_llm=None)

    info_classes = []
    for cls in rules_classes:
        info_class = _RULES_TO_INFO_CLASS_MAP.get(cls)
        if info_class:
            info_classes.append(info_class)

    # ── Shape-type injection ─────────────────────────────────────────────────
    # When the query expander / regex already identified the shape type,
    # guarantee that its corresponding info class is included — even if the
    # raw query text lacks the English keywords that classify_user_query_for_rules
    # relies on (e.g. user wrote in French/Vietnamese without "perforated").
    # _SHAPE_TYPE_TO_INFO_CLASS (module level) maps normalised shape_type → info class_name.
    if detected_shape_type:
        shape_key = detected_shape_type.lower().replace(" ", "_")
        # Try both "perforated sheet" and "perforated_sheet" keys
        injected = (
            _SHAPE_TYPE_TO_INFO_CLASS.get(detected_shape_type.lower()) or
            _SHAPE_TYPE_TO_INFO_CLASS.get(shape_key)
        )
        if injected and injected not in info_classes:
            info_classes.append(injected)
            logger.info(
                f"[RAG_INFO] Shape-type injection: '{detected_shape_type}' → '{injected}'"
            )

    # Shape-type guard: suppress classes that are irrelevant for this shape
    if detected_shape_type and info_classes:
        suppressed = _SHAPE_INFO_SUPPRESS.get(detected_shape_type, set())
        if suppressed:
            before = info_classes[:]
            info_classes = [c for c in info_classes if c not in suppressed]
            removed = [c for c in before if c not in info_classes]
            if removed:
                logger.info(
                    f"[RAG_INFO] Shape-guard suppressed for '{detected_shape_type}': {removed}"
                )

    logger.info(f"[RAG_INFO] classify_user_query_for_info → {info_classes} "
                f"(shape={detected_shape_type!r})")
    return info_classes


def initialize_retriever(force_reload: bool = False):
    """
    Initializes all components of the retriever:
    - Loads the FAISS index and its metadata.
    - Loads structured data from CSV files.
    - Ensures the embedding model is ready.
    """
    global faiss_index_instance, structured_perforated_sheet_data, structured_tole_data, structured_tubes_data

    logger.debug("Initializing RAG retriever...")

    # Set memory baseline (only logs if there's a problem)
    memory_monitor.set_baseline()
    memory_monitor.log_memory_status("RAG Retriever Initialization Start")

    # Ensure all required directories exist
    index_dir = os.path.dirname(FAISS_INDEX_PATH)
    perforated_sheet_dir = os.path.dirname(PERFORATED_SHEET_CSV_PATH)
    tole_dir = os.path.dirname(TOLE_CSV_PATH)

    os.makedirs(index_dir, exist_ok=True)
    os.makedirs(perforated_sheet_dir, exist_ok=True)
    os.makedirs(tole_dir, exist_ok=True)

    # Ensure embedding model is loaded (important for FAISS search)
    try:
        logger.debug("Loading embedding model...")
        get_embedding_model()
        memory_monitor.log_memory_diff("After Embedding Model Load")
    except Exception as e:
        logger.error(f"Failed to initialize embedding model: {e}")
        # Depending on desired strictness, could raise an error or allow proceeding without embeddings

    if faiss_index_instance is None or force_reload:
        logger.debug(f"Loading FAISS index from {FAISS_INDEX_PATH}...")
        faiss_index_instance = load_index(index_name=FAISS_INDEX_NAME)
        if faiss_index_instance:
            logger.info(f"FAISS index loaded with {faiss_index_instance.ntotal} vectors, {len(faiss_metadata)} metadata entries")
            memory_monitor.log_memory_diff("After FAISS Index Load")
        else:
            logger.error(f"Failed to load FAISS index '{FAISS_INDEX_NAME}'. Semantic search will be unavailable.")

    if structured_perforated_sheet_data is None or force_reload:
        logger.debug(f"Loading structured data from {PERFORATED_SHEET_CSV_PATH}...")
        csv_path = PERFORATED_SHEET_CSV_PATH
        if not os.path.exists(csv_path):
            logger.debug(f"New path not found, trying legacy path: {PERFORATED_SHEET_CSV_PATH_OLD}")
            csv_path = PERFORATED_SHEET_CSV_PATH_OLD

        if os.path.exists(csv_path):
            structured_perforated_sheet_data = load_csv_to_structured_data(csv_path)
            if structured_perforated_sheet_data:
                logger.debug(f"'Perforated sheet' CSV data loaded with {len(structured_perforated_sheet_data)} entries.")
            else:
                logger.warning(f"Failed to load or empty data from '{csv_path}'.")
        else:
            
            structured_perforated_sheet_data = {}

    if structured_tole_data is None or force_reload:
        logger.debug(f"Loading structured data from {TOLE_CSV_PATH}...")
        csv_path = TOLE_CSV_PATH
        if not os.path.exists(csv_path):
            logger.debug(f"New path not found, trying legacy path: {TOLE_CSV_PATH_OLD}")
            csv_path = TOLE_CSV_PATH_OLD

        if os.path.exists(csv_path):
            structured_tole_data = load_csv_to_structured_data(csv_path)
            if structured_tole_data:
                logger.debug(f"'Tole' CSV data loaded with {len(structured_tole_data)} entries.")
            else:
                logger.warning(f"Failed to load or empty data from '{csv_path}'.")
        else:
            
            structured_tole_data = {}

    if structured_tubes_data is None or force_reload:
        logger.debug(f"Loading structured data from {TUBES_CSV_PATH}...")
        if os.path.exists(TUBES_CSV_PATH):
            structured_tubes_data = load_csv_to_structured_data(TUBES_CSV_PATH)
            if structured_tubes_data:
                logger.debug(f"'Tubes' CSV data loaded with {len(structured_tubes_data)} entries.")
            else:
                logger.warning(f"Failed to load or empty data from '{TUBES_CSV_PATH}'.")
        else:
            
            structured_tubes_data = {}


    logger.debug("RAG retriever initialization complete.")

# ═══════════════════════════════════════════════════════════════════════════
# Shared class-scoped metadata retrieval — used by BOTH the rules flow and the
# info flow.
# ───────────────────────────────────────────────────────────────────────────
# Rules and info entries live in the SAME metadata store, are selected by the
# SAME class-matching rules, and are wrapped into Documents the same way. They
# differ ONLY in:
#   (a) which store entries belong to them (rules.json vs info.json / type=info)
#   (b) which fields the page_content renders
#   (c) the metadata each Document carries
# All three differences are declared in the _DocKind values below, so the
# matching / iteration / logging logic exists exactly once.
#
# IMPORTANT: the rendered page_content and metadata are kept byte-identical to
# the previous hand-written blocks — rules feed the unified/DFM chain while info
# feeds the code-generation chain, and both prompts were tuned on these exact
# texts. Changing a label here changes what those chains see.
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_description(value: Any) -> str:
    """`description` may be a plain string, a list of lines, or something else."""
    if isinstance(value, list):
        return '\n'.join(value) if value else ''
    if not isinstance(value, str):
        return str(value) if value else ''
    return value


def _field(key: str, default: str = '') -> Callable[[dict], str]:
    """Render entry[key] exactly like the previous f-string did (missing → default)."""
    return lambda entry: f"{entry.get(key, default)}"


def _joined(key: str) -> Callable[[dict], str]:
    """Render a list field as a comma-separated string (None-safe)."""
    return lambda entry: ', '.join(entry.get(key) or [])


class _DocKind(NamedTuple):
    """Everything that differs between the rules flow and the info flow."""
    log_tag: str                                      # log prefix, e.g. "RAG_RULES"
    label: str                                        # noun used in log lines
    id_field: str                                     # entry id logged per hit
    belongs: Callable[[dict], bool]                   # store entry → is it this kind?
    header: str                                       # first page_content line
    fields: Tuple[Tuple[str, Callable[[dict], str]], ...]  # ("Label", renderer) pairs
    metadata: Callable[[dict, str], Dict[str, Any]]   # (entry, class_name) → metadata


RULES_DOC_KIND = _DocKind(
    log_tag="RAG_RULES",
    label="rules",
    id_field="rule_id",
    belongs=lambda entry: entry.get('source', '').endswith('rules.json'),
    header="Manufacturing Rule for {class_name}:",
    fields=(
        ("Rule ID",         _field('rule_id', 'Unknown')),
        ("Title",           _field('title', 'Unknown Rule')),
        ("Description",     lambda e: _normalize_description(e.get('description', ''))),
        ("Rule",            _field('rule')),
        ("Validation Code", _field('validation_code')),
        ("Severity",        lambda e: f"{e.get('severity', 'info')}".upper()),
        ("Error Message",   _field('error_message')),
        ("Parameters",      _joined('parameters')),
    ),
    metadata=lambda entry, class_name: {
        "source": "manufacturing_rules",
        "class": class_name,
        "rule_id": entry.get('rule_id', ''),
        "category": entry.get('category', ''),
        "severity": entry.get('severity', 'info'),
    },
)

INFO_DOC_KIND = _DocKind(
    log_tag="RAG_INFO",
    label="info",
    id_field="info_id",
    belongs=lambda entry: entry.get('type', '') == 'info' or entry.get('source', '').endswith('info.json'),
    header="Info for {class_name}:",
    fields=(
        ("Info ID",     _field('info_id', 'Unknown')),
        ("Title",       _field('title', 'Unknown Info')),
        ("Description", lambda e: _normalize_description(e.get('description', ''))),
        ("Rule",        _field('rule')),
        ("Parameters",  _joined('parameters')),
    ),
    metadata=lambda entry, class_name: {
        "source": entry.get('source', ''),
        "type": "info",
        "class": class_name,
        "info_id": entry.get('info_id', ''),
        "category": entry.get('category', ''),
    },
)


def _class_matches(entry_class: str, class_name: str) -> bool:
    """
    Flexible class matching, shared by the rules and info lookups.

    IMPORTANT: Guard entry_class != '' before the last condition.
    In Python, "" in "anything" is always True, which would cause every
    document with an empty/missing class_name to match every class lookup.
    This was the root cause of Tubes info leaking into L-Bracket requests.
    """
    result_class = entry_class.lower()
    class_lower = class_name.lower()
    class_short_name = class_name.split('/')[-1].lower()  # e.g. "Bending" from "Manufacturing_Processes/Forming/Bending"
    # Normalize: treat spaces and underscores as equivalent for matching
    result_class_norm = result_class.replace(' ', '_')
    class_short_norm = class_short_name.replace(' ', '_')

    return (
        result_class == class_lower or
        result_class == class_name.replace('/', '_').lower() or
        result_class == class_short_name or
        result_class_norm == class_short_norm or          # ← "laser cut" == "laser_cutting" after normalize
        class_short_norm in result_class_norm or
        (result_class != '' and result_class in class_lower)  # ← guard: prevent empty-string false positive
    )


def _search_by_class(class_name: str, faiss_index_instance, kind: _DocKind, k: int) -> list[dict]:
    """
    Find up to `k` metadata entries of `kind` belonging to `class_name`.

    NOTE: Uses DIRECT METADATA FILTERING instead of FAISS semantic search
    because semantic search returns examples instead of rules/info
    (same embeddings for similar content).
    """
    if not faiss_index_instance:
        logger.warning(f"[RAG] FAISS index not available for {kind.label} search")
        return []

    # Import metadata store directly
    from src.rag.vector_store import METADATA_STORE

    logger.info(f"[{kind.log_tag}] Searching {kind.label} for class: {class_name} "
                f"(short: {class_name.split('/')[-1].lower()})")
    logger.debug(f"[{kind.log_tag}] Total metadata entries: {len(METADATA_STORE)}")

    matches = []
    for entry in METADATA_STORE:
        if not kind.belongs(entry):
            continue

        if _class_matches(entry.get('class_name', ''), class_name):
            matches.append(entry)
            logger.info(f"[{kind.log_tag}] ✅ Found {kind.label}: "
                        f"{entry.get(kind.id_field, 'N/A')} from {entry.get('source', '')}")
            if len(matches) >= k:
                break

    logger.info(f"[{kind.log_tag}] Total {kind.label} found for {class_name}: {len(matches)}")
    return matches


def _build_class_document(entry: dict, class_name: str, kind: _DocKind) -> Document:
    """Render one metadata entry as the Document its chain expects."""
    lines = [kind.header.format(class_name=class_name)]
    lines.extend(f"{label}: {render(entry)}" for label, render in kind.fields)
    return Document(
        page_content="\n".join(lines) + "\n",
        metadata=kind.metadata(entry, class_name),
    )


def collect_class_documents(
    class_names: Sequence[str],
    faiss_index_instance,
    kind: _DocKind,
    k: int,
    skip_class: Optional[Callable[[str], bool]] = None,
) -> List[Document]:
    """
    Class detection → metadata lookup → Document rendering, for either kind.

    Args:
        class_names: Classes detected for the query (rules paths for
            RULES_DOC_KIND, info class_names for INFO_DOC_KIND).
        faiss_index_instance: The loaded FAISS index.
        kind: RULES_DOC_KIND or INFO_DOC_KIND.
        k: Max entries per class.
        skip_class: Optional predicate to skip a class entirely (used by
            callers that already have that class in their context).

    Returns:
        Documents for every matching entry. A failure on one class is logged
        and skipped — the remaining classes still contribute.
    """
    documents: List[Document] = []
    for class_name in class_names:
        if skip_class and skip_class(class_name):
            continue
        try:
            for entry in _search_by_class(class_name, faiss_index_instance, kind, k):
                documents.append(_build_class_document(entry, class_name, kind))
        except Exception as e:
            logger.error(f"[{kind.log_tag}] ❌ Error for {class_name}: {e}")
    return documents


# ═══════════════════════════════════════════════════════════════════════════
# Shape-type vocabulary — SINGLE source of truth
# ───────────────────────────────────────────────────────────────────────────
# Two consumers, one table:
#   1. _regex_extract_shape_type() — reads a shape type out of structured text
#      ("Type: L-Bracket") and returns it in CANONICAL casing.
#   2. _shape_type_for_filter()    — normalises a detected shape type to the
#      LOWERCASE form stored in example metadata, for the STEP 3/4 filters.
# The two used to carry separate hand-maintained copies of this table (80 vs 76
# entries, canonical- vs lower-cased values), so adding a shape meant editing
# two places and the copies had already drifted apart.
# ═══════════════════════════════════════════════════════════════════════════

SHAPE_TYPE_ALIASES: Dict[str, str] = {
    # L-bracket-Circular variants
    "l-bracket-circular": "L-bracket-Circular", "l bracket circular": "L-bracket-Circular",
    "circular-l-bracket": "L-bracket-Circular", "circular l-bracket": "L-bracket-Circular",
    # U-shaped-Circular variants
    "u-shaped-circular": "U-shaped-Circular", "u shaped circular": "U-shaped-Circular",
    "circular-u-shaped": "U-shaped-Circular", "circular u-shaped": "U-shaped-Circular",
    # Z-shaped-Circular variants
    "z-shaped-circular": "Z-shaped-Circular", "z shaped circular": "Z-shaped-Circular",
    "circular-z-shaped": "Z-shaped-Circular", "circular z-shaped": "Z-shaped-Circular",
    # L-bracket variants
    "l-bracket": "L-bracket", "l bracket": "L-bracket",
    "l-shaped": "L-bracket", "l shaped": "L-bracket",
    "l-shape": "L-bracket", "l shape": "L-bracket",
    "cornière": "L-bracket", "equerre": "L-bracket",
    # U-shaped variants
    "u-shaped": "U-shaped", "u shaped": "U-shaped",
    "u-shape": "U-shaped", "u shape": "U-shaped",
    "u-bracket": "U-shaped",
    # Z-shaped variants
    "z-shaped": "Z-shaped", "z shaped": "Z-shaped",
    "z-shape": "Z-shaped", "z shape": "Z-shaped",
    "z-bracket": "Z-shaped",
    # I-Shaped variants
    "i-shaped": "I-Shaped", "i shaped": "I-Shaped",
    "i-shape": "I-Shaped", "i shape": "I-Shaped",
    "i-beam": "I-Shaped", "poutre en i": "I-Shaped",
    # T-Shaped variants
    "t-shaped": "T-Shaped", "t shaped": "T-Shaped",
    "t-shape": "T-Shaped", "t shape": "T-Shaped",
    "t-bar": "T-Shaped", "fer en t": "T-Shaped",
    # Tube variants
    "tube": "tube", "square tube": "tube", "rectangular tube": "tube",
    "round tube": "tube",
    # Capot variants — mixed-direction (independent per-wall bends) MUST be checked
    # before the generic "capot" key so it isn't swallowed by the substring fallback.
    "capot-mixed-direction": "capot-mixed-direction", "capot mixed direction": "capot-mixed-direction",
    # Capot variants (default: uniform bend direction, built with makeTub)
    "capot": "capot", "capot-open": "capot",
    "cover": "capot", "open box": "capot",
    # Triangle variants — keep UNAMBIGUOUS compound terms only.
    # NOTE: By the time retriever runs, query_expander has already appended
    # "Shape type: Triangle" — regex Pattern 1/2 catches it.
    # This fallback only fires when LLM expansion was unavailable.
    # DANGER: avoid single-word matches (substring matching can false-positive).
    "equilateral triangle": "Triangle", "isosceles triangle": "Triangle",
    "triangular plate": "Triangle", "triangular sheet": "Triangle",
    "triangle tray": "Triangle", "triangular tray": "Triangle",
    # French compounds — safe because they all contain 'triangulaire'
    "plaque triangulaire": "Triangle", "tole triangulaire": "Triangle",
    "piece triangulaire": "Triangle", "platine triangulaire": "Triangle",
    "gousset triangulaire": "Triangle", "renfort triangulaire": "Triangle",
    "equerre triangulaire": "Triangle", "flan triangulaire": "Triangle",
    "plaque en triangle": "Triangle", "platine en triangle": "Triangle",
    # Perforated Sheet variants — MUST come before generic "sheet" to prevent map to "plate"
    "perforated sheet": "Perforated Sheet", "perforated_sheet": "Perforated Sheet",
    "tôle perforée": "Perforated Sheet", "tôle filtrante": "Perforated Sheet",
    "perforated": "Perforated Sheet",
    # Sheet-Circular variants
    "sheet-circular": "Sheet-Circular", "circular sheet": "Sheet-Circular", "circular plate": "Sheet-Circular", "disque": "Sheet-Circular",
    # Sheet variants (generic — perforated must be above this block)
    "sheet": "plate", "plate": "plate", "flat": "plate",
}

# Longest alias first, computed once instead of re-sorting 80 keys on every
# lookup (the old code sorted inside the candidate loop, three times per call).
_SHAPE_ALIASES_LONGEST_FIRST: List[str] = sorted(SHAPE_TYPE_ALIASES, key=len, reverse=True)


def _lookup_shape_alias(text: str) -> Optional[str]:
    """
    Resolve `text` (already lowercased) to a canonical shape type, or None.

    Longest alias first so a specific alias beats a prefix of itself —
    "u-shaped-circular" must not be decided by the shorter "u-shaped".
    An exact hit always wins regardless: an alias LONGER than `text` cannot be
    contained in it, and an equal-length one can only match by being equal.
    """
    if text in SHAPE_TYPE_ALIASES:
        return SHAPE_TYPE_ALIASES[text]
    for alias in _SHAPE_ALIASES_LONGEST_FIRST:
        if text.startswith(alias) or alias in text:
            return SHAPE_TYPE_ALIASES[alias]
    return None


def _mixed_capot_override(mapped: Optional[str], full_text: str) -> Optional[str]:
    """Pattern 1 only reads the 'Type:' line, so a generic 'capot' match can hide
    per-wall mixed up/down bends described further down in the same text."""
    if mapped != "capot":
        return mapped
    tl = full_text.lower()
    has_up = any(w in tl for w in ("upward", "vers le haut", "montant"))
    has_down = any(w in tl for w in ("downward", "vers le bas", "descendant"))
    return "capot-mixed-direction" if (has_up and has_down) else mapped


def _regex_extract_shape_type(text: str) -> Optional[str]:
    """
    Extract shape type from structured text using regex (no LLM needed).

    The unified-analysis output uses "Type: L-Bracket" format which the LLM
    expander may not recognise as manufacturing terminology. We normalise it
    here so that detected_shape_type is always reliable.
    """
    # Pattern 1: "Type: L-Bracket" or "Type: Z-shaped" — unified analysis output
    m = re.search(r'^\s*Type:\s*(.+)', text, re.IGNORECASE | re.MULTILINE)
    candidates = []
    if m:
        candidates.append(m.group(1).strip().lower())
    # Pattern 2: "Shape type: L-bracket" — already expanded query or example headers
    for sm in re.finditer(r'shape\s+type:\s*(\S+(?:\s+\S+)?)', text, re.IGNORECASE):
        candidates.append(sm.group(1).strip().lower())

    for raw in candidates:
        canonical = _lookup_shape_alias(raw)
        if canonical:
            return _mixed_capot_override(canonical, text)

    # Targeted free-text fallback for Triangle (EN + FR compound terms only).
    # Keep narrow to avoid false positives; LLM is the primary detector.
    # SAFE rule: only include terms that UNAMBIGUOUSLY identify the part as Triangle.
    text_lower = text.lower()
    triangle_terms = [
        "equilateral triangle", "isosceles triangle",
        "triangular plate", "triangular sheet", "triangular tray",
        "triangle tray",
        # French compounds (all contain 'triangulaire' — safe for substring match)
        "plaque triangulaire", "gousset triangulaire",
        "renfort triangulaire", "equerre triangulaire",
        "platine triangulaire", "piece triangulaire",
    ]
    if any(term in text_lower for term in triangle_terms):
        return "Triangle"
    return None


def _shape_type_for_filter(detected_shape_type: str) -> str:
    """
    Normalise a detected shape type to the lowercase form stored in example
    metadata (`shape_type`), so the STEP 3/4 filters compare like with like.

    Strips trailing punctuation/newlines that leak from bad LLM JSON output
    (e.g. 'Sheet",'). An unknown shape passes through cleaned-but-unmapped:
    canonical names already equal their metadata values, so filtering still
    works for shapes that have no alias entry.
    """
    cleaned = detected_shape_type.strip('", \n\r\t.:;[]{}()').lower()
    return (_lookup_shape_alias(cleaned) or cleaned).lower()


async def retrieve_rules_only(
    query: str,
    faiss_index_instance,
    classification_llm,
    reranking_llm=None,
    k_rules: int = 10,
    cost_tracker=None,
    session_id: str = "unknown"
) -> List[Document]:
    """
    Retrieve ONLY manufacturing rules for unified processing chain.
    Used for parameter validation and constraint checking.
    
    Args:
        query: The search query
        faiss_index_instance: The loaded FAISS index
        classification_llm: The LLM for class classification (NOT USED - keyword only)
        reranking_llm: GPT-4.1-nano LLM for reranking
        k_rules: Final number of rules to return after reranking
        
    Returns:
        List of Document objects containing ONLY rules (no example code)
    """
    retrieved_documents: List[Document] = []
    
    if not faiss_index_instance:
        logger.warning("[RAG_RULES] ❌ FAISS index not available")
        return retrieved_documents
    
    # STEP 1: Class detection (keyword matching only)
    detected_classes = classify_user_query_for_rules(query, None)  # Force keyword matching
    
    if not detected_classes:
        logger.info("[RAG_RULES] ⚠️ No classes detected")
        return retrieved_documents
    
    # STEP 2: Metadata retrieval (get more candidates for reranking)
    initial_k = 15  # Get 15 rules per class for reranking

    retrieved_documents = collect_class_documents(
        detected_classes, faiss_index_instance, RULES_DOC_KIND, k=initial_k
    )

    # LOG RAW RULES (Unranked)
    try:
        from src.core.agent_utils import format_retrieved_context
        raw_context = format_retrieved_context(retrieved_documents)
        log_rag_context_retrieval(
            session_id=session_id, query=query, documents=retrieved_documents,
            context=raw_context, retrieval_time=0.0, detected_classes=detected_classes,
            success=True, error=None, is_reranked=False
        )
    except Exception as e:
        logger.warning(f"Failed to log raw rules: {e}")
    
    # STEP 3: Reranking
    initial_count = len(retrieved_documents)
    if retrieved_documents and reranking_llm:
        from src.rag.reranker import llm_rerank_documents
        
        retrieved_documents = await llm_rerank_documents(
            query=query,
            documents=retrieved_documents,
            llm=reranking_llm,
            top_k=k_rules,
            doc_type="rules",
            cost_tracker=cost_tracker,
            session_id=session_id
        )
        reranked = True
    else:
        retrieved_documents = retrieved_documents[:k_rules]
        reranked = False
    
    # Compact summary with session tracking
    session_prefix = f"{session_id[:15]}..." if len(session_id) > 15 else session_id
    logger.info(
        f"📝 [RULES] Session: {session_prefix} | "
        f"Classes: {detected_classes} | "
        f"Retrieved: {initial_count} → Reranked: {len(retrieved_documents)} | "
        f"Status: {'✅ Reranked' if reranked else '⚠️ No rerank'}"
    )
    
    return retrieved_documents


async def retrieve_examples_only(
    query: str,
    faiss_index_instance,
    classification_llm=None,
    reranking_llm=None,
    k_examples: int = 5,
    cost_tracker=None,
    session_id: str = "unknown",
    pre_expanded_query: Optional[str] = None,
    pre_detected_shape_type: Optional[str] = None,
) -> List[Document]:
    """
    Retrieve example code AND info files for code generation chain.

    Used for generating FreeCAD Python scripts based on:
    - Similar example code patterns
    - Technical info (ISO/ANSI standards, specifications) via class detection

    Args:
        query: The search query
        faiss_index_instance: The loaded FAISS index
        classification_llm: Optional LLM for class classification
        k_examples: Number of example code snippets to retrieve
        pre_expanded_query: Expansion result the CALLER already paid for. When
            given, the internal expansion LLM call is skipped entirely.
            Callers that expand the query themselves (see
            `TextToCADAgent._invoke_unified_with_rag`) were paying for query
            expansion twice per turn — once in the caller, then again here on
            the already-expanded text.
        pre_detected_shape_type: The `detected_shape_type` from that same
            caller-side expansion. MUST be passed alongside
            `pre_expanded_query`, otherwise skipping the internal call would
            silently lose LLM shape detection and leave only the regex path.

    Returns:
        List of Document objects containing example code + info (no rules)
    """
    retrieved_documents: List[Document] = []
    
    if not faiss_index_instance:
        logger.warning("[RAG_EXAMPLES] ❌ FAISS index not available")
        return retrieved_documents
    
    # STEP 1: Query Expansion with Shape Type Detection
    from src.rag.query_expander import expand_query_with_llm, _expansion_llm

    # ── STEP 1a: Regex pre-extraction (fast, zero-cost fallback) ─────────────
    # See _regex_extract_shape_type / SHAPE_TYPE_ALIASES at module level.
    detected_shape_type_regex = _regex_extract_shape_type(query)
    if detected_shape_type_regex:
        logger.info(f"[RAG_FILTER] ⚡ Regex pre-extracted shape type: {detected_shape_type_regex}")
    
    # ── STEP 1b: LLM expansion for semantic enrichment ───────────────────────
    # Reuse the caller's expansion when supplied — see `pre_expanded_query` in
    # the docstring. Without this the query gets expanded a second time (on text
    # that already carries a "Shape type: X" suffix), costing a full extra LLM
    # call per turn for a near-identical result.
    if pre_expanded_query is not None:
        expanded_query = pre_expanded_query
        detected_shape_type_llm = pre_detected_shape_type
        logger.info(
            f"[RAG_EXAMPLES] ♻️  Reusing caller's query expansion "
            f"(shape_type={detected_shape_type_llm}) — skipped duplicate LLM call"
        )
    else:
        expansion_llm = _expansion_llm if _expansion_llm else classification_llm

        if expansion_llm:
            try:
                expansion_result = await expand_query_with_llm(
                    query,
                    llm=expansion_llm,
                    cost_tracker=cost_tracker
                )

                # Handle both old (string) and new (dict) return formats for backward compatibility
                if isinstance(expansion_result, dict):
                    expanded_query = expansion_result.get("expanded_query", query)
                    detected_shape_type_llm = expansion_result.get("detected_shape_type")
                else:
                    # Fallback for old string format
                    expanded_query = expansion_result
                    detected_shape_type_llm = None
            except Exception as e:
                logger.warning(f"[RAG_EXAMPLES] Query expansion failed: {e}, using original query")
                expanded_query = query
                detected_shape_type_llm = None
        else:
            logger.info("[RAG_EXAMPLES] No expansion LLM available, using original query")
            expanded_query = query
            detected_shape_type_llm = None
    
    # ── STEP 1c: Merge — regex takes precedence, LLM fills gap ───────────────
    # Regex result is more reliable for structured unified-analysis format.
    # LLM result is more reliable for free-text manufacturing queries.
    detected_shape_type = detected_shape_type_regex or detected_shape_type_llm
    
    logger.info(f"[RAG_FILTER] Query: '{query[:50]}...'")
    logger.info(
        f"[RAG_FILTER] Detected shape type: {detected_shape_type} "
        f"(regex={detected_shape_type_regex}, llm={detected_shape_type_llm})"
    )
    
    # STEP 2: SEMANTIC SEARCH for example code
    initial_k = k_examples * 10  # Get 50 candidates for better recall (increased from 30)
    semantic_hits = search_index(faiss_index_instance, expanded_query, k=initial_k)
    
    example_candidates = []
    
    for hit in semantic_hits:
            
        page_content = hit.get("text_content", "")
        source = hit.get("source", "")
        doc_type = hit.get("type", "")
        
        # EXCLUDE rules, chat_history, and info from semantic search
        is_rules = source.endswith("rules.json")
        is_chat_history = "chat_history" in source.lower() or "chat_history1.json" in source
        is_info = doc_type == "info" or "info.json" in source.lower()
        
        # Skip non-examples
        if is_rules or is_chat_history or is_info:
            continue
        
        # INCLUDE example code
        is_example = "example" in source.lower() or source.endswith(".txt")
        
        if is_example:
            # Ensure all metadata values are serializable
            metadata = {k: v for k, v in hit.items() if k != "text_content"}
            if 'similarity_score' in metadata and isinstance(metadata['similarity_score'], (float, int)):
                metadata['similarity_score'] = float(metadata['similarity_score'])
            else:
                metadata.pop('similarity_score', None)
            
            # shape_type is now directly available from metadata (from chunking)
            # No need to extract from content anymore
            
            document = Document(page_content=page_content, metadata=metadata)
            example_candidates.append(document)
    
    # Target value for the shape filters, computed ONCE here and reused by STEP 3
    # and STEP 4 — they must filter on the same value, and the old code derived it
    # twice from a map that only existed inside STEP 3's `if` block.
    shape_filter_target = _shape_type_for_filter(detected_shape_type) if detected_shape_type else None
    if shape_filter_target and shape_filter_target != detected_shape_type:
        logger.debug(f"[RAG_FILTER] Shape '{detected_shape_type}' normalised to "
                     f"'{shape_filter_target}' for metadata comparison")

    # STEP 3: EXCLUSIVE filter by shape type before reranking
    # Strategy: reranker ONLY sees same-shape candidates → higher quality, no cross-shape noise.
    # Graceful fallback to full pool ONLY when zero same-shape examples found.
    if detected_shape_type and example_candidates:
        exact_matches = [
            doc for doc in example_candidates
            if str(doc.metadata.get("shape_type", "")).lower() == shape_filter_target
        ]
        other_examples = [
            doc for doc in example_candidates
            if str(doc.metadata.get("shape_type", "")).lower() != shape_filter_target
        ]
        
        if exact_matches:
            # ✅ EXCLUSIVE: send ONLY same-shape candidates to reranker
            example_candidates = exact_matches
            logger.info(
                f"[RAG_FILTER] ✅ Exclusive filter: {len(exact_matches)} same-shape "
                f"('{detected_shape_type}') → reranker "
                f"(discarded {len(other_examples)} other-shape candidates)"
            )
        else:
            # ⚠️ FALLBACK: no same-shape examples found → keep full pool
            logger.warning(
                f"[RAG_FILTER] ⚠️ No '{detected_shape_type}' examples in pool "
                f"({len(other_examples)} other-shape). Falling back to full pool."
            )
    else:
        if not detected_shape_type:
            logger.info("[RAG_FILTER] No shape type detected, using all candidates")
        else:
            logger.warning("[RAG_FILTER] No example candidates found")
    
    # STEP 4: Reranking — input is already same-shape-only (or graceful fallback)
    examples_reranked = False
    if example_candidates and reranking_llm:
        from src.rag.reranker import llm_rerank_documents
        
        reranked_examples = await llm_rerank_documents(
            query=expanded_query,
            documents=example_candidates,
            llm=reranking_llm,
            top_k=k_examples,   # Input is pure same-shape → request exactly k
            doc_type="examples",
            cost_tracker=cost_tracker,
            session_id=session_id
        )
        
        # Safety post-filter: block leaks from llm_rerank internal fallback paths.
        # Uses the SAME shape_filter_target as STEP 3 — no second normalization.
        if detected_shape_type and reranked_examples:
            exact_final = [
                doc for doc in reranked_examples
                if str(doc.metadata.get("shape_type", "")).lower() == shape_filter_target
            ]
            other_final = [
                doc for doc in reranked_examples
                if str(doc.metadata.get("shape_type", "")).lower() != shape_filter_target
            ]
            
            if exact_final:
                retrieved_documents.extend(exact_final[:k_examples])
                logger.info(
                    f"[RAG_FILTER] ✅ Final: {len(exact_final[:k_examples])} "
                    f"same-shape ('{detected_shape_type}') examples selected"
                )
                if other_final:
                    logger.warning(
                        f"[RAG_FILTER] ⚠️ Safety filter blocked {len(other_final)} "
                        f"non-matching examples from reranker output"
                    )
            else:
                # Shouldn't happen — take top k as last resort
                retrieved_documents.extend(reranked_examples[:k_examples])
                logger.warning(
                    f"[RAG_FILTER] ⚠️ Safety fallback: no exact-shape in reranked results, "
                    f"using top-{k_examples}"
                )
        else:
            retrieved_documents.extend(reranked_examples[:k_examples])
        
        examples_reranked = True
    else:
        # No reranking LLM → take top k_examples from filtered candidates
        retrieved_documents.extend(example_candidates[:k_examples])
        examples_reranked = False
    
    # PART 2: INFO FILES RETRIEVAL (Class-based, NO reranking)
    # classify_user_query_for_info now re-uses the rules classifier internally
    # and applies a shape-type guard to suppress irrelevant info classes.
    detected_classes = classify_user_query_for_info(query, detected_shape_type=detected_shape_type)

    info_documents = collect_class_documents(
        detected_classes, faiss_index_instance, INFO_DOC_KIND, k=100
    )
    retrieved_documents.extend(info_documents)
    info_count = len(info_documents)


    # Compact summary - Show full flow: candidates → reranked + info = total
    examples_count = len(retrieved_documents) - info_count
    total_docs = len(retrieved_documents)
    
    # Log retrieved example sources for debugging
    if example_candidates:
        example_sources = []
        for doc in example_candidates[:5]:  # Show top 5
            source = doc.metadata.get('source', 'Unknown')
            # Extract filename only
            filename = os.path.basename(source)
            example_sources.append(filename)
        
        logger.info(
            f"💡 [EXAMPLES] Candidates: {len(example_candidates)} → Reranked: {examples_count} + Info: {info_count} = Total: {total_docs} | "
            f"{'✅ Reranked' if examples_reranked else '⚠️ No rerank'}\n"
            f"  Top 5 sources: {', '.join(example_sources)}"
        )
    else:
        logger.info(
            f"💡 [EXAMPLES] Candidates: {len(example_candidates)} → Reranked: {examples_count} + Info: {info_count} = Total: {total_docs} | "
            f"{'✅ Reranked' if examples_reranked else '⚠️ No rerank'}"
        )

    # LOG RAW EXAMPLES (BEFORE reranking) - This should show all 18 candidates
    try:
        from src.core.agent_utils import format_retrieved_context
        # Log ONLY the example candidates BEFORE reranking (no info yet)
        raw_context = format_retrieved_context(example_candidates)
        log_rag_context_retrieval(
            session_id=session_id, query=expanded_query, documents=example_candidates,
            context=raw_context, retrieval_time=0.0, detected_classes=detected_classes,
            success=True, error=None, is_reranked=False, expanded_query=expanded_query
        )
    except Exception as e:
        logger.warning(f"Failed to log raw examples: {e}")
    
    return retrieved_documents


if __name__ == "__main__":
    # Test initialization
    initialize_retriever(force_reload=True)
    print("\nRAG Retriever initialized. Use retrieve_rules_only() and retrieve_examples_only() for retrieval.")
