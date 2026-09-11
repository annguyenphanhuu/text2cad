"""
SQLAlchemy models for session-related operations.
"""
from sqlalchemy import Column, String, Integer, Text, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database.database import Base


class Session(Base):
    """
    Session model for storing chat sessions.
    """
    __tablename__ = "sessions"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), index=True, unique=True)
    name = Column(String(255))  # Session name (user name or description)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationship with ChatHistory
    chat_histories = relationship("ChatHistory", back_populates="session", cascade="all, delete-orphan")


class ChatHistory(Base):
    """
    ChatHistory model for storing chat messages.
    """
    __tablename__ = "chat_history"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), ForeignKey("sessions.session_id"), index=True)
    message = Column(Text)  # User message
    image_path = Column(String(255), nullable=True)  # Image path (if any)
    part_file_name = Column(String(255), nullable=True)  # Part file name (if any)
    export_format = Column(String(255), nullable=True)  # Export format (if any)
    material_choice = Column(String(255), nullable=True)  # Material choice (if any)
    selected_feature_uuid = Column(String(255), nullable=True)  # Selected feature UUID (if any)
    output = Column(Text, nullable=True)  # Output (if any)
    obj_export = Column(String(255), nullable=True)  # Path to obj export file
    step_export = Column(String(255), nullable=True)  # Path to step export file
    technical_drawing_export = Column(String(255), nullable=True)  # Path to technical drawing PDF file
    lasted_code = Column(Text, nullable=True)  # Store latest generated FreeCAD code for edit mode
    response = Column(Text, nullable=True)  # Store chat response from the chatbot
    response_at = Column(TIMESTAMP, nullable=True)  # Timestamp when AI response is generated
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationship with Session
    session = relationship("Session", back_populates="chat_histories")
