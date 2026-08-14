"""
Shared prompt text for media (image / PDF) classification.

`image_handler` and `pdf_handler` used to carry byte-identical copies of this
prompt apart from the word "image"/"PDF". They drifted independently; a single
builder keeps the classification contract — and therefore the parsed output
format — identical for both media types.
"""

_MEDIA_CLASSIFICATION_PROMPT = (
    "Analyze this {medium} and identify if it shows a "
    "['Perforated sheet','Sheet metal','Countersink hole']. "
    "Return in this exact format: "
    "'Class: [perforated sheet/sheet metal/countersink hole], Name/code: [value]'. "
    "If you can see specific dimensions, hole patterns, or model codes, include them. "
    "No other text."
)


def build_media_classification_prompt(medium: str, user_input: str = "") -> str:
    """
    Build the classification prompt for `medium` ("image" or "PDF").

    `user_input`, when non-blank, is appended as additional context exactly as
    both handlers did before.
    """
    prompt = _MEDIA_CLASSIFICATION_PROMPT.format(medium=medium)
    if user_input and user_input.strip():
        return f"{prompt} Additional context: {user_input}"
    return prompt
