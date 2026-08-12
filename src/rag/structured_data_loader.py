import csv
from typing import Dict, List, Any

def load_csv_to_structured_data(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Loads a CSV file into a dictionary for structured lookup.
    The 'Name' column is used as the primary key in the dictionary.
    
    Args:
        file_path (str): The path to the CSV file.

    Returns:
        Dict[str, Dict[str, Any]]: A dictionary where keys are from the 'Name'
                                     column and values are dictionaries of
                                     row data (column_header: value).
                                     Returns an empty dictionary if file not found
                                     or CSV is improperly formatted.
    """
    structured_data: Dict[str, Dict[str, Any]] = {}
    try:
        with open(file_path, mode='r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            if not reader.fieldnames or "Name" not in reader.fieldnames:
                print(f"Error: CSV file {file_path} must contain a 'Name' column.")
                return structured_data
                
            for row in reader:
                name = row.get("Name")
                if name:
                    # Store the rest of the row data, keyed by column headers
                    structured_data[name] = {k: v for k, v in row.items() if k != "Name"}
                else:
                    print(f"Warning: Row in {file_path} is missing 'Name' field: {row}")
                    
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
    except Exception as e:
        print(f"An error occurred while reading {file_path}: {e}")
        
    return structured_data

if __name__ == "__main__":
    # Example usage with new structure:
    perforated_sheet_data = load_csv_to_structured_data("data/Materials/Sheet_Metal/Perforated_Sheet/dictionary.csv")
    if perforated_sheet_data:
        print(f"Successfully loaded 'Perforated Sheet' CSV into {len(perforated_sheet_data)} structured entries.")
        if "R12 U27.72" in perforated_sheet_data:
            print("\nExample entry for 'R12 U27.72':")
            print(perforated_sheet_data["R12 U27.72"])
        if "LC5x50 U25x60" in perforated_sheet_data:
            print("\nExample entry for 'LC5x50 U25x60':")
            print(perforated_sheet_data["LC5x50 U25x60"])
    else:
        print("No data loaded from 'Perforated Sheet' CSV.")

    # Test with legacy path for backward compatibility
    print("\nTesting legacy path...")
    legacy_data = load_csv_to_structured_data("data/Class/Perforated sheet/dictionary.csv")
    if legacy_data:
        print(f"Successfully loaded legacy 'Perforated sheet' CSV into {len(legacy_data)} structured entries.")
    else:
        print("No data loaded from legacy 'Perforated sheet' CSV.")

    tole_data = load_csv_to_structured_data("data/Class/Tole/dictionary.csv")
    if tole_data:
        print(f"\nSuccessfully loaded 'Tole' CSV into {len(tole_data)} structured entries.")
        # You can add a specific key check here if you know one from tole/dictionary.csv
    else:
        print("No data loaded from 'Tole' CSV (or file does not exist/is empty).")
