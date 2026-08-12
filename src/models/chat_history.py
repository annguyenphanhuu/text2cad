import os
import json
from datetime import datetime

class ChatHistory:
    """
    Class to manage chat history storage.
    """
    def __init__(self, storage_file=None):
        """
        Initialize the chat history storage.
        
        Args:
            storage_file (str, optional): Path to the storage file. 
                                         Defaults to 'chat_history.json' in the data directory.
        """
        if storage_file is None:
            # Get the absolute path to the project root
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            data_dir = os.path.join(project_root, 'data')
            os.makedirs(data_dir, exist_ok=True)
            self.storage_file = os.path.join(data_dir, 'chat_history.json')
        else:
            self.storage_file = storage_file
        
        self.history = self._load_history()
    
    def _load_history(self):
        """
        Load chat history from the storage file.
        
        Returns:
            list: List of chat history entries.
        """
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return []
        else:
            return []
    
    def save_history(self):
        """
        Save chat history to the storage file.
        """
        with open(self.storage_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
    
    def add_entry(self, user_message, generated_code, is_edit_request=False):
        """
        Add a new entry to the chat history.
        
        Args:
            user_message (str): The user's message.
            generated_code (str): The generated code.
            is_edit_request (bool, optional): Whether this was an edit request. Defaults to False.

        Returns:
            dict: The added history entry.
        """
        entry = {
            "user_message": user_message,
            "generated_code": generated_code,
            "is_edit_request": is_edit_request,
            "timestamp": datetime.now().isoformat()
        }
        self.history.append(entry)
        self.save_history()
        return entry
    
    def get_all_history(self):
        """
        Get all chat history entries.
        
        Returns:
            list: List of all chat history entries.
        """
        return self.history
    
    def get_recent_history(self, limit=10):
        """
        Get the most recent chat history entries.
        
        Args:
            limit (int, optional): Maximum number of entries to return. Defaults to 10.

        Returns:
            list: List of recent chat history entries.
        """
        return self.history[-limit:] if self.history else []
    
    def clear_history(self):
        """
        Clear all chat history.
        """
        self.history = []
        self.save_history()
