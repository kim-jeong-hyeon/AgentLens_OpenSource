"""GenUI Agent: generates HTML UI from instruction text.

A separate LLM instance that receives only the instruction (no screenshot)
and produces self-contained HTML/CSS for rendering in a mobile overlay WebView.
"""

import logging
from typing import Optional

from m3a_agent import infer


GENUI_SYSTEM_PROMPT = (
    'You are a Generative UI Agent integrated into a mobile assistant'
    ' ecosystem.\n\n'
    'Objective: Translate functional requirements from the Mobile GUI Agent'
    ' into clean, responsive, mobile-first HTML/CSS code.\n\n'
    'Input: A description of the information to display, the question to ask,'
    ' or the data to collect from the user.\n\n'
    'Output: Valid, self-contained HTML5 code with embedded CSS.\n\n'
    'Core Directives:\n'
    '- Component-Only Output: Generate only the specific HTML component'
    ' requested (e.g., a notification card, an input modal, a bottom sheet).'
    ' Do not wrap the output in full-page document tags (<html>, <head>,'
    ' <body>) or include viewport meta tags.\n'
    '- Mobile-Optimized Proportions: Design for fluid mobile constraints. Use'
    ' relative widths (e.g., width: 100%, max-width: 400px) instead of fixed'
    ' desktop dimensions. Ensure all tap targets (buttons, inputs) are'
    ' touch-friendly (minimum 44x44px).\n'
    '- Self-Contained Styling: Output raw HTML with scoped CSS (either in a'
    ' <style> block directly above the component or via inline styles). Do not'
    ' rely on external stylesheets or libraries. Do not output markdown code'
    ' blocks (like ```html).\n'
    '- Semantic & Accessible: Use standard HTML form elements (<form>,'
    ' <input>, <select>, <button>) with associated <label> tags for data'
    ' collection.\n'
    '- Actionable & Integrated: Every interactive element (buttons, list items,'
    ' selections) must call the JavaScript bridge to send the user\'s action'
    ' back to the agent. Use onclick="GenUIBridge.onAction(JSON.stringify({...}))"'
    ' where the JSON object has "action" (e.g., "select", "confirm", "dismiss")'
    ' and "value" (the selected value). For example:\n'
    '  <button onclick="GenUIBridge.onAction(JSON.stringify({action:\'select\','
    ' value:\'starbucks\'}))">Starbucks</button>\n'
    '  <button onclick="GenUIBridge.onAction(JSON.stringify({action:\'dismiss\'}))">Done</button>\n'
    '- Visual Design: Use a clean, modern design with rounded corners,'
    ' appropriate spacing, and a neutral color palette. Use emoji sparingly'
    ' for visual emphasis when appropriate.\n'
    '- Include a small disclosure text at the bottom: "*This UI was generated'
    ' by AI."\n'
)


def generate_html(
    llm: infer.MultimodalLlmWrapper,
    instruction: str,
) -> Optional[str]:
  """Generate HTML from a GenUI instruction.

  Args:
    llm: The LLM wrapper to use for generation.
    instruction: The self-contained instruction describing what UI to generate.

  Returns:
    HTML string, or None if generation failed.
  """
  prompt = f'{GENUI_SYSTEM_PROMPT}\nInstruction: {instruction}\n\nOutput:'

  try:
    output, is_safe, _ = llm.predict(prompt)
  except Exception as e:
    logging.error('GenUI generation failed: %s', e)
    return None

  if is_safe == False:
    logging.warning('GenUI output flagged as unsafe')
    return None

  if not output:
    return None

  # Clean up: remove markdown code fences if the LLM wraps them
  html = output.strip()
  if html.startswith('```html'):
    html = html[7:]
  elif html.startswith('```'):
    html = html[3:]
  if html.endswith('```'):
    html = html[:-3]
  html = html.strip()

  return html
