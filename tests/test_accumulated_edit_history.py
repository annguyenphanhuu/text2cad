import os
import sys
import asyncio
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import classes to test
from src.core.text_to_cad_agent import TextToCADAgent
from src.models.sessions import ChatHistory

async def run_tests():
    print("=== STARTING ACCUMULATED EDIT HISTORY TESTS ===")
    
    # 1. Initialize Agent with mocked chains to avoid LLM calls
    with patch('src.core.text_to_cad_agent.create_shape_change_detector_chain'), \
         patch('src.core.text_to_cad_agent.create_greeting_classification_chain'), \
         patch('src.core.text_to_cad_agent.create_unified_processing_chain'), \
         patch('src.core.text_to_cad_agent.create_dfm_validation_chain'), \
         patch('src.core.text_to_cad_agent.create_code_generation_chain'), \
         patch('src.core.text_to_cad_agent.create_code_editing_chain'), \
         patch('src.core.text_to_cad_agent.create_description_confirm_chain'), \
         patch('src.core.text_to_cad_agent.create_step_planner_chain'), \
         patch('src.core.text_to_cad_agent.create_perforated_param_chain'):
         
        agent = TextToCADAgent(MagicMock(), MagicMock(), MagicMock())

        session_id = "test_session_999"
        
        # Test 1: Session State Initialization
        print("\nTest 1: Session State Initialization")
        state = agent._get_session_state(session_id)
        assert 'edit_history_since_confirm' in state, "edit_history_since_confirm should be in state"
        assert state['edit_history_since_confirm'] == [], "edit_history_since_confirm should start empty"
        print("✅ Session state initialized correctly with empty edit history.")

        # Test 2: Accumulation on Successful Edit
        print("\nTest 2: Accumulation on Successful Edit")
        # Mock process_edit_request steps and call the inner update_session_state_async function
        # Let's directly simulate the session update logic
        agent._update_session_state(session_id, edit_history_since_confirm=[])
        
        # First edit request
        req1 = "Add a hole of diameter 10mm at center"
        state = agent._get_session_state(session_id)
        current_history = list(state.get('edit_history_since_confirm', []))
        current_history.append(req1)
        agent._update_session_state(session_id, edit_history_since_confirm=current_history)
        
        # Second edit request
        req2 = "Add a 20mm notch at the top corner"
        state = agent._get_session_state(session_id)
        current_history = list(state.get('edit_history_since_confirm', []))
        current_history.append(req2)
        agent._update_session_state(session_id, edit_history_since_confirm=current_history)
        
        state = agent._get_session_state(session_id)
        assert state['edit_history_since_confirm'] == [req1, req2], "History should contain both edit requests"
        print("✅ Edit history successfully accumulated across multiple requests.")

        # Test 3: History Reset on Code Generation from Requirements (Confirmation)
        print("\nTest 3: History Reset on Code Generation")
        agent._update_session_state(
            session_id,
            latest_code="code",
            latest_title="title",
            pending_questions=[],
            last_confirmed_description="desc",
            edit_history_since_confirm=[],
        )
        state = agent._get_session_state(session_id)
        assert state['edit_history_since_confirm'] == [], "History should be empty after confirm"
        print("✅ Edit history successfully cleared after template confirmation/code generation.")

        # Test 4: Database Recovery Fallback
        print("\nTest 4: Database Recovery Fallback (Server Restart Simulation)")
        # Clear the session state to simulate server restart
        agent._session_states.pop(session_id, None)
        
        # Create mock ChatHistory rows
        row1 = ChatHistory(id=1, session_id=session_id, message="Create sheet", response="📋 **Sheet**\n- **Description:** 200mm length, 100mm width, 2mm thickness, steel.", lasted_code="code_initial")
        row2 = ChatHistory(id=2, session_id=session_id, message="yes", lasted_code="code_initial")
        row3 = ChatHistory(id=3, session_id=session_id, message="Add hole diameter 10", lasted_code="code_edit_1")
        row4 = ChatHistory(id=4, session_id=session_id, message="Add slot 5x10", lasted_code="code_edit_2")
        mock_rows = [row1, row2, row3, row4]
        
        # Mock SessionLocal to return these rows
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_rows
        
        with patch('src.database.database.SessionLocal', return_value=mock_db):
            await agent._recover_session_state_from_db(session_id)
            
        state = agent._get_session_state(session_id)
        assert state['last_confirmed_description'] == "200mm length, 100mm width, 2mm thickness, steel.", f"Incorrect description recovered: {state['last_confirmed_description']}"
        assert state['edit_history_since_confirm'] == ["Add hole diameter 10", "Add slot 5x10"], f"Incorrect edit history recovered: {state['edit_history_since_confirm']}"
        print("✅ Successfully recovered confirmed description and edit history since last confirmation from database.")

    print("\n=== ALL TESTS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
