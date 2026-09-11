"""
Load FreeCadUtil/ir_frames.py WITHOUT importing the FreeCadUtil package (whose
__init__ pulls in FreeCAD, which the API process does not have).
"""
import importlib.util
import os

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "FreeCadUtil", "ir_frames.py"))
_spec = importlib.util.spec_from_file_location("ir_frames", _PATH)
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)
