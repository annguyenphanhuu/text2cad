"""
Material mapping utility for GeometryAnalyzer integration.
Maps user-facing material names to GeometryAnalyzer material names.
"""

def map_material_to_geometry_analyzer(user_material: str) -> str:
    """
    Map user-facing material names to GeometryAnalyzer material names.
    
    Args:
        user_material: Material name from UI (e.g., "INOX", "ALUMINUM", "STEEL")
    
    Returns:
        GeometryAnalyzer-compatible material name (steel, stainless_steel, aluminum)
    
    Mapping Rules:
        - STEEL, ACIER → steel
        - INOX, STAINLESS, GALVA → stainless_steel  
        - ALUMINUM, ALUMINIUM, ALU → aluminum
        - Default → steel (if unknown)
    
    Examples:
        >>> map_material_to_geometry_analyzer("INOX")
        'stainless_steel'
        >>> map_material_to_geometry_analyzer("ALUMINUM")
        'aluminum'
        >>> map_material_to_geometry_analyzer("steel")
        'steel'
        >>> map_material_to_geometry_analyzer("")
        'steel'
    """
    if not user_material:
        return "steel"
    
    material_upper = user_material.upper().strip()
    
    # Steel variants
    if material_upper in ["STEEL", "ACIER"]:
        return "steel"
    
    # Stainless steel variants
    if material_upper in ["INOX", "STAINLESS", "GALVA", "STAINLESS_STEEL"]:
        return "stainless_steel"
    
    # Aluminum variants
    if material_upper in ["ALUMINUM", "ALUMINIUM", "ALU"]:
        return "aluminum"
    
    # Default fallback
    return "steel"
