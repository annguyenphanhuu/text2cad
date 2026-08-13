import re
import json
from pathlib import Path



def _build_script_chunk(script_name: str, script_lines: list[str], file_path) -> dict | None:
    """
    Turn one accumulated script example into a chunk dict.

    The leading comment lines (everything before the first import) are the request
    description; a '# shape type: ...' comment supplies the shape type. Only the
    request and shape type go into text_content for embedding — never the code.

    Returns None when the script has no content worth indexing.
    """
    script_content = '\n'.join(script_lines).strip()
    if not script_content:
        return None

    request_lines = []
    shape_type = None

    for script_line in script_lines:
        line_stripped = script_line.strip()

        # Stop at first import statement
        if line_stripped.startswith('import ') or line_stripped.startswith('from '):
            break

        # Extract shape type
        if line_stripped.startswith('#') and 'shape type:' in line_stripped.lower():
            # Extract shape type value (full value, may contain spaces)
            match = re.search(r'#\s*shape type:\s*(.+)', line_stripped, re.IGNORECASE)
            if match:
                shape_type = match.group(1).strip()
        elif line_stripped.startswith('#'):
            # Regular comment line (part of request description)
            request_lines.append(line_stripped.lstrip('#').strip())

    request_description = ' '.join(request_lines).strip()

    text_content_parts = []
    if request_description:
        text_content_parts.append(request_description)
    if shape_type:
        text_content_parts.append(f"Shape type: {shape_type}")

    text_content = '\n'.join(text_content_parts) if text_content_parts else script_name

    return {
        "script_name": script_name,
        "request_description": request_description,  # Request only
        "shape_type": shape_type,
        "script_content": script_content,  # Full code for generation
        "text_content": text_content,  # Only request + shape type for embedding
        "source": str(file_path)
    }


def chunk_script_files(base_dir: str = "data/Example") -> list[dict[str, str]]:
    """
    Chunks all .txt files in a directory structure by individual script examples.

    Each script, identified by a line starting with '#example' or '#Example', is a chunk.
    Processes multiple files and combines all chunks into a single list.
    """
    all_chunks = []

    base_path = Path(base_dir)
    if not base_path.is_dir():
        print(f"Error: Base directory not found at {base_dir}")
        return all_chunks

    file_paths = list(base_path.rglob("*.txt"))

    for file_path in file_paths:
        chunks = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Error: File not found at {file_path}")
            continue

        # Use regex to split by both '#example ' and '#Example ' (case insensitive)
        import re
        # Split by lines starting with #example or #Example (case insensitive)
        lines = content.split('\n')
        current_script = []
        current_name = None

        for line in lines:
            # Check if line starts with #example or #Example
            if re.match(r'^#[Ee]xample\s+(.+)', line):
                # If we have a previous script, save it
                if current_name and current_script:
                    chunk = _build_script_chunk(current_name, current_script, file_path)
                    if chunk:
                        chunks.append(chunk)

                # Start new script
                match = re.match(r'^#[Ee]xample\s+(.+)', line)
                current_name = match.group(1).strip()
                current_script = [line]  # Include the #Example line itself
            else:
                # Add line to current script
                if current_name is not None:
                    current_script.append(line)

        # Don't forget the last script
        if current_name and current_script:
            chunk = _build_script_chunk(current_name, current_script, file_path)
            if chunk:
                chunks.append(chunk)

        # Add chunks from this file to the overall list
        all_chunks.extend(chunks)

    return all_chunks

def chunk_example_txt(file_path: str = "data/example.txt") -> list[dict[str, str]]:
    """
    Backward compatibility wrapper for chunk_script_files.
    Chunks a single example.txt file by individual script examples.
    """
    return chunk_script_files([file_path])


def chunk_class_rules(class_name: str) -> list[dict[str, str]]:
    """
    Chunks the rules.json file for a specific class.
    Each rule becomes a separate chunk for RAG indexing.
    Supports both old and new directory structures.
    """
    chunks = []
    rules_path = None

    # Try new structure first (with category/subcategory)
    if "/" in class_name:
        category, subcategory = class_name.split("/", 1)
        # Try Materials structure
        rules_path = Path(f"data/Materials/{category}/{subcategory}/rules.json")
        if not rules_path.exists():
            # Try Manufacturing_Processes structure
            rules_path = Path(f"data/Manufacturing_Processes/{category}/{subcategory}/rules.json")
    else:
        # Fallback search order for single-level class names
        fallback_paths = [
            # New Materials structure where class sits directly under Materials
            Path(f"data/Materials/{class_name}/rules.json"),
            # New Manufacturing_Processes structure where class sits directly under Manufacturing_Processes
            Path(f"data/Manufacturing_Processes/{class_name}/rules.json"),
            # Legacy Class structure (kept for backward compatibility)
            Path(f"data/Class/{class_name}/rules.json"),
        ]

        # Pick the first existing path (if any)
        for p in fallback_paths:
            if p.exists():
                rules_path = p
                break

    if not rules_path or not rules_path.exists():
        print(f"No rules file found for class: {class_name}")
        return chunks

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_data = json.load(f)

        class_name = rules_data.get("class_name", class_name)
        rules = rules_data.get("rules", [])

        for rule in rules:
            # Handle description as either string or array
            description = rule.get("description", "")
            if isinstance(description, list):
                description_str = '\n'.join(description) if description else ''
            elif not isinstance(description, str):
                description_str = str(description) if description else ''
            else:
                description_str = description
            
            chunk = {
                "rule_id": rule.get("id", ""),
                "class_name": class_name,
                "category": rule.get("category", ""),
                "title": rule.get("title", ""),
                "description": description,  # Keep original format
                "rule": rule.get("rule", ""),
                "severity": rule.get("severity", "info"),
                "validation_code": rule.get("validation_code", ""),
                "error_message": rule.get("error_message", ""),
                "parameters": rule.get("parameters", []),
                "source": str(rules_path),  # Use actual path instead of hardcoded old format
                "text_content": f"Rule {rule.get('id', '')}: {rule.get('title', '')}\n"
                              f"Class: {class_name}\n"
                              f"Category: {rule.get('category', '')}\n"
                              f"Description: {description_str}\n"
                              f"Rule: {rule.get('rule', '')}\n"
                              f"Severity: {rule.get('severity', '')}\n"
                              f"Validation: {rule.get('validation_code', '')}\n"
                              f"Error Message: {rule.get('error_message', '')}\n"
                              f"Parameters: {', '.join(rule.get('parameters', []))}"
            }
            chunks.append(chunk)

    except Exception as e:
        print(f"Error loading rules for {class_name}: {e}")

    return chunks

def chunk_all_class_rules() -> list[dict[str, str]]:
    """
    Chunks rules from all available classes in both old and new structures.
    """
    all_chunks = []
    
    # Process old structure for backward compatibility
    old_class_dir = Path("data/Class")
    if old_class_dir.exists():
        for class_path in old_class_dir.iterdir():
            if class_path.is_dir():
                class_name = class_path.name
                class_chunks = chunk_class_rules(class_name)
                all_chunks.extend(class_chunks)
                print(f"Loaded {len(class_chunks)} rules for legacy class: {class_name}")
    
    # Process new Materials structure (supports 1-level and 2-level layouts)
    materials_dir = Path("data/Materials")
    if materials_dir.exists():
        for category_path in materials_dir.iterdir():
            if category_path.is_dir():
                category_name = category_path.name

                # 1-level: rules.json directly inside the category directory
                top_level_rule = category_path / "rules.json"
                if top_level_rule.exists():
                    class_chunks = chunk_class_rules(category_name)
                    all_chunks.extend(class_chunks)
                    print(f"Loaded {len(class_chunks)} rules for material class: {category_name}")

                # 2-level: iterate through subdirectories as before
                for subcategory_path in category_path.iterdir():
                    if subcategory_path.is_dir():
                        subcategory_name = subcategory_path.name
                        rules_file = subcategory_path / "rules.json"
                        if rules_file.exists():
                            class_name = f"{category_name}/{subcategory_name}"
                            class_chunks = chunk_class_rules(class_name)
                            all_chunks.extend(class_chunks)
                            print(f"Loaded {len(class_chunks)} rules for material class: {class_name}")
    
    # Process new Manufacturing_Processes structure (supports 1-level and 2-level layouts)
    processes_dir = Path("data/Manufacturing_Processes")
    if processes_dir.exists():
        for category_path in processes_dir.iterdir():
            if category_path.is_dir():
                category_name = category_path.name

                # 1-level
                top_level_rule = category_path / "rules.json"
                if top_level_rule.exists():
                    class_chunks = chunk_class_rules(category_name)
                    all_chunks.extend(class_chunks)
                    print(f"Loaded {len(class_chunks)} rules for process class: {category_name}")

                # 2-level
                for subcategory_path in category_path.iterdir():
                    if subcategory_path.is_dir():
                        subcategory_name = subcategory_path.name
                        rules_file = subcategory_path / "rules.json"
                        if rules_file.exists():
                            class_name = f"{category_name}/{subcategory_name}"
                            class_chunks = chunk_class_rules(class_name)
                            all_chunks.extend(class_chunks)
                            print(f"Loaded {len(class_chunks)} rules for process class: {class_name}")
            
    return all_chunks


def chunk_all_info_files() -> list[dict[str, str]]:
    """
    Chunks all info.json files from data/Info/ directory.
    
    Info files contain technical specifications and standards (e.g., ISO/ANSI countersink dimensions)
    that are used during code generation to provide accurate parameters.
    
    Returns:
        List of info chunks with metadata type="info"
    """
    all_chunks = []
    
    info_dir = Path("data/Info")
    if not info_dir.exists():
        print(f"Info directory not found at {info_dir}")
        return all_chunks
    
    # Scan all subdirectories for info.json files
    info_files = list(info_dir.rglob("info.json"))
    
    for info_path in info_files:
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info_data = json.load(f)
            
            # Get category name from directory structure
            # e.g., data/Info/Countersink/info.json -> category = "Countersink"
            category_name = info_path.parent.name
            class_name = info_data.get("class_name", category_name)
            parent_category = info_data.get("parent_category", "")
            
            # Process each entry in the info file
            # CRITICAL: info.json uses 'info_entries' key, not 'rules'
            info_entries = info_data.get("info_entries", []) or info_data.get("rules", [])
            
            for rule in info_entries:
                # Handle description as either string or array
                description = rule.get("description", "")
                if isinstance(description, list):
                    description_str = '\n'.join(description) if description else ''
                elif not isinstance(description, str):
                    description_str = str(description) if description else ''
                else:
                    description_str = description
                
                chunk = {
                    "info_id": rule.get("id", ""),
                    "class_name": class_name,
                    "parent_category": parent_category,
                    "category": rule.get("category", ""),
                    "title": rule.get("title", ""),
                    "description": description,  # Keep original format
                    "rule": rule.get("rule", ""),
                    "severity": rule.get("severity", "info"),
                    "parameters": rule.get("parameters", []),
                    "source": str(info_path),
                    "type": "info",  # CRITICAL: Mark as info type to distinguish from rules
                    "text_content": f"Info {rule.get('id', '')}: {rule.get('title', '')}\n"
                                  f"Class: {class_name}\n"
                                  f"Category: {rule.get('category', '')}\n"
                                  f"Description: {description_str}\n"
                                  f"Rule: {rule.get('rule', '')}\n"
                                  f"Parameters: {', '.join(rule.get('parameters', []))}"
                }
                all_chunks.append(chunk)
            
            print(f"Loaded {len(info_entries)} info entries from: {category_name}")
            
        except Exception as e:
            print(f"Error loading info file {info_path}: {e}")
    
    return all_chunks

if __name__ == "__main__":
    # Example usage for multiple script files:
    script_chunks = chunk_script_files()
    if script_chunks:
        print(f"Successfully chunked script files into {len(script_chunks)} chunks.")
        # Group by source file for better reporting
        file_counts = {}
        for chunk in script_chunks:
            source = chunk['source']
            file_counts[source] = file_counts.get(source, 0) + 1

        for file_path, count in file_counts.items():
            print(f"  - {file_path}: {count} chunks")
    else:
        print("No chunks were created from script files.")

    # Example usage for class rules:
    rules_chunks = chunk_all_class_rules()
    if rules_chunks:
        print(f"\nSuccessfully chunked class rules into {len(rules_chunks)} chunks.")
    else:
        print("No chunks were created from class rules.")

    # Example usage for info files:
    info_chunks = chunk_all_info_files()
    if info_chunks:
        print(f"\nSuccessfully chunked info files into {len(info_chunks)} chunks.")
    else:
        print("No chunks were created from info files.")
