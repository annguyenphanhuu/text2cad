from typing import List, Optional, Dict, Any
from pydantic.v1 import BaseModel, Field

class ShapeRequirement(BaseModel):
    shape_type: str
    dimensions: Dict[str, Optional[float]]
    position: Optional[List[Optional[float]]] = None
    rotation: Optional[List[Optional[float]]] = None

class ExtractedShapeInfo(BaseModel):
    pass  # Reserved for future shape categorization

class Operation(BaseModel):
    operation_type: str = Field(description="Boolean operation type (cut, fuse, common, chamfer, fillet)")
    base_shape: str = Field(description="Name of the base shape")
    tool_shape: Optional[str] = Field(None, description="Name of the tool shape (optional for chamfer/fillet operations)")
    result_name: str = Field(description="Name of the result after the operation")

class DesignRequirements(BaseModel):
    title: str = Field(description="Brief title describing the design")
    shapes: List[ShapeRequirement] = Field(description="List of required shapes")
    operations: Optional[List[Operation]] = Field(None, description="List of Boolean operations to perform")
    complexity_level: int = Field(description="Design complexity level (1-5)")

class AnalysisAndParameterCheckOutput(BaseModel):
    # Description fields
    title: Optional[str] = Field(default="", description="Brief title describing the design (deprecated, use description)")
    description: str = Field(default="", description="Concise description of the design (now populated by description_confirm, not unified)")
    complexity_level: int = Field(description="Design complexity level (1-5)")

    # Analysis output fields
    missing_info: bool = Field(description="Whether there is missing information to proceed with CAD generation.")
    questions: List[str] = Field(default_factory=list, description="List of questions to ask the user to get missing information.")
    detailed_explanation_requested: bool = Field(default=False, description="Whether user explicitly requested detailed explanation.")
    skip_questions_requested: bool = Field(default=False, description="Whether user requested to skip further questions.")
    override_intent_detected: bool = Field(default=False, description="Whether user intends to override manufacturing rules.")

    # Assembly Detection Fields
    design_type: str = Field(default="part", description="Type of design: 'part' (single object) or 'assembly' (multiple separate objects)")
    assembly_warning: Optional[str] = Field(None, description="Warning message if assembly detected (in user's language: English or French)")
    assembly_confirmed: bool = Field(default=False, description="Whether user confirmed assembly generation after warning")

    # Confirm intent fields (for description confirm flow)
    confirm_intent_detected: bool = Field(
        default=False,
        description="True when the user's latest message is confirming a 📋 description preview (e.g., 'yes', 'oui', 'ok'). False when making a new or modified request."
    )

    # Shape type detection (for description_confirm template selection)
    shape_type: str = Field(
        default="unknown",
        description=(
            "The canonical shape type detected from the user's request. "
            "Must be one of: 'L-bracket', 'L-bracket-Circular', 'U-shaped', 'U-shaped-Circular', 'Z-shaped', 'Z-shaped-Circular', "
            "'CAPOT', 'Tube-Circular', 'Tube-Rectangular', 'Sheet', 'Sheet-Circular', 'Perforated Sheet', 'Triangle'. "
            "Use 'Perforated Sheet' when user mentions perforated sheet / tôle perforée / R+T notation. "
            "Use 'unknown' if ambiguous or not yet determined."
        )
    )

    # Step-by-step plan request (intent-based, NOT complexity-based)
    step_by_step_requested: bool = Field(
        default=False,
        description="True ONLY when user explicitly asks to see a step-by-step build plan before generating (e.g., 'show me the steps', 'montre-moi les étapes'). Never set true based on complexity alone."
    )

class DFMValidationOutput(BaseModel):
    """Output from the DFM Rule Validation Agent."""
    has_violations: bool = Field(default=False, description="Whether any DFM rule violations were detected")
    violations: List[str] = Field(default_factory=list, description="List of violation warning messages in user's language")
    override_intent_detected: bool = Field(default=False, description="Whether user intends to override manufacturing rules")
    thickness_warning: Optional[str] = Field(None, description="Thickness validation warning message if any")
