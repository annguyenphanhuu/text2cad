import sys
import os
import asyncio

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.templates import build_confirm_template
from langchain_core.prompts import ChatPromptTemplate

def test_template_formatting():
    print("Testing build_confirm_template for shape_type='unknown'...")
    template_str = build_confirm_template("unknown")
    
    print(f"Template length: {len(template_str)} characters")
    
    # Try creating ChatPromptTemplate from template_str
    print("Creating ChatPromptTemplate...")
    try:
        confirm_prompt = ChatPromptTemplate.from_template(template_str)
        print("✅ ChatPromptTemplate created successfully!")
        print("Expected variables:", confirm_prompt.input_variables)
        
        # Test formatting with mock inputs
        mock_inputs = {
            "user_text": "Je veux une pièce de forme inconnue...",
            "confirm_round": 1,
            "user_language": "French",
            "shape_type": "unknown",
            "perf_info": "mock_perf_info"
        }
        
        print("Attempting to format template with mock inputs...")
        formatted_prompt = confirm_prompt.format(**mock_inputs)
        print("✅ Template formatted successfully without KeyError!")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_template_formatting()
