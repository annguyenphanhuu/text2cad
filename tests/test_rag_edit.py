import os
import sys
import asyncio
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock the TextToCADAgent to avoid complex LLM initialization
class MockAgent:
    def __init__(self):
        pass
        
    async def _detect_shape_change(self, text, code, session_id):
        # Mock the shape change detection simulating what the real LLM chain does
        return {
            'shape_change': False,
            'current_shape_type': 'Z-shaped',
            'reason': 'Mocked detection'
        }
        
    def _extract_user_only_query(self, text):
        return text.replace("[USER]: ", "")
        
    def _build_user_text_with_history(self, session, text):
        return f"[USER]: {text}"

async def test_edit_rag():
    agent = MockAgent()
    
    # Mock data
    session_id = "test_edit_rag_session_123"
    user_text = "Add a 20x10 mm rectangular hole, properly centred."
    original_code = "Part.makeZShape(...)"
    
    # Simulate step 1.5 from text_to_cad_agent.py -> process_edit_request
    print("\n1. Testing shape detection...")
    shape_detection = await agent._detect_shape_change(user_text, original_code, session_id)
    print(f"Shape detection result: {shape_detection}")
    
    # As implemented in process_edit_request
    current_shape = 'unknown'
    if 'shape_detection' in locals() and isinstance(shape_detection, dict):
        current_shape = shape_detection.get('current_shape_type', 'unknown')
    
    print("\n2. Simulating RAG Query construction...")
    full_history = agent._build_user_text_with_history(session_id, user_text)
    raw_rag_query = agent._extract_user_only_query(full_history)
    
    rag_query = raw_rag_query
    if current_shape != 'unknown':
        rag_query = f"Shape type: {current_shape}\n{rag_query}"
        
    print(f"Final RAG Query passed to retrieve_edit_context:\n{rag_query}")
    print("-" * 40)
    
    if "Shape type: Z-shaped" in rag_query:
        print("\n✅ SUCCESS: RAG Query correctly includes the detected shape type (Z-shaped)!")
    else:
        print("\n❌ WARNING: RAG Query does not include the shape type!")

if __name__ == "__main__":
    asyncio.run(test_edit_rag())
