import sys
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules BEFORE importing TextToCADAgent to avoid dependency hell in test
mock_rag_singleton = MagicMock()
mock_rag_singleton.get_rag_split_context = AsyncMock()
sys.modules['src.core.rag_singleton'] = mock_rag_singleton

mock_agent_utils = MagicMock()
# Mock specific functions used in TextToCADAgent
mock_agent_utils.parse_unified_analysis = MagicMock()
mock_agent_utils.clean_code = MagicMock()
mock_agent_utils.create_rag_query = MagicMock()
mock_agent_utils.format_retrieved_context = MagicMock()
mock_agent_utils.detect_language = MagicMock(return_value='en')
mock_agent_utils.get_success_message = MagicMock(return_value='Success')
mock_agent_utils.combine_and_format_contexts = MagicMock()
mock_agent_utils.detect_detailed_explanation_request = MagicMock(return_value=False)
sys.modules['src.core.agent_utils'] = mock_agent_utils

sys.modules['src.core.agent_chains'] = MagicMock()
sys.modules['src.rag.retriever'] = MagicMock()
sys.modules['src.rag.query_expander'] = MagicMock()
sys.modules['src.utils.file_finder'] = MagicMock() # Often causes issues
sys.modules['src.utils.cost_tracker'] = MagicMock()

# We need to allow src.core.models to be imported or mocked if it has dependencies
try:
    from src.core.models import AnalysisAndParameterCheckOutput, DFMValidationOutput
except ImportError:
    sys.modules['src.core.models'] = MagicMock()
    AnalysisAndParameterCheckOutput = MagicMock()
    DFMValidationOutput = MagicMock()
    
# Import AFTER mocking
from src.core.text_to_cad_agent import TextToCADAgent

async def test_expansion_integration():
    print("Testing Query Expansion Integration...")
    
    # Mock LLMs
    mock_llm = MagicMock()
    mock_llm.model_name = "mock-gpt"
    
    agent = TextToCADAgent(mock_llm, mock_llm, mock_llm)
    
    # Mock the expansion LLM response
    agent.expansion_llm = MagicMock()
    # Mock expand_query_with_llm to return a specific expanded query
    with patch('src.core.text_to_cad_agent.expand_query_with_llm', new_callable=AsyncMock) as mock_expand:
        mock_expand.return_value = {
            "expanded_query": "Test Query\nShape type: L-bracket",
            "detected_shape_type": "L-bracket"
        }
        
        # Configure get_rag_split_context mock return value
        mock_rag_singleton.get_rag_split_context.return_value = {
            'success': True,
            'rules_context': "Mock Rules",
            'examples_context': "Mock Examples"
        }
        
        # Mock unified_processing_chain
        agent.unified_processing_chain = MagicMock()
        agent.unified_processing_chain.ainvoke = AsyncMock()
        agent.unified_processing_chain.ainvoke.return_value = {
            "unified_output_obj": AnalysisAndParameterCheckOutput(
                description="Test",
                complexity_level=1,
                missing_info=False,
                questions=[],
                shape_class="L-bracket"
            ),
            "raw_unified_json": "{}",
            "retrieved_context_for_code_gen": "Mock Context"
        }
        
        # Mock dfm_validation_chain
        agent.dfm_validation_chain = MagicMock()
        agent.dfm_validation_chain.ainvoke = AsyncMock()
        agent.dfm_validation_chain.ainvoke.return_value = DFMValidationOutput(
            has_violations=False,
            violations=[],
            override_intent_detected=False
        )
        
        # Mock generate_code_from_requirements to just return success
        agent.generate_code_from_requirements = MagicMock()
        agent.generate_code_from_requirements.return_value = {"code": "print('success')"}

        # Run the unified request processor
        user_text = "Test Query"
        session_id = "test_session"
        
        # We need to bypass the greeting check or mock it
        agent.greeting_classification_chain = MagicMock()
        agent.greeting_classification_chain.ainvoke = AsyncMock()
        agent.greeting_classification_chain.ainvoke.return_value = {
            "classification": "information_request", 
            "confidence": 1.0
        }
        
        # Since we want to test the full flow, let's call _invoke_unified_with_rag directly first
        # to verify expansion mapping
        print("1. Testing _invoke_unified_with_rag...")
        result = await agent._invoke_unified_with_rag(user_text, session_id)
        
        print(f"Result expanded_user_text: {result.get('expanded_user_text')}")
        
        if result.get('expanded_user_text') == "Test Query\nShape type: L-bracket":
            print("SUCCESS: _invoke_unified_with_rag returned expanded text.")
        else:
            print(f"FAILURE: Expected expanded text, got {result.get('expanded_user_text')}")
            
        # Verify the chain input used the expanded text
        call_args = agent.unified_processing_chain.ainvoke.call_args
        if call_args:
            chain_input = call_args[0][0]
            if chain_input.get('user_text') == "Test Query\nShape type: L-bracket":
                 print("SUCCESS: Unified chain received expanded text.")
            else:
                 print(f"FAILURE: Unified chain received {chain_input.get('user_text')}")

if __name__ == "__main__":
    asyncio.run(test_expansion_integration())
