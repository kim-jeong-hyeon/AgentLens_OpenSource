# Copyright 2026 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A Multimodal Autonomous Agent for Android (M3A)."""

import time

from absl import logging
from m3a_agent import agent_utils
from m3a_agent import base_agent
from m3a_agent import infer
from m3a_agent import m3a_utils
from m3a_agent.env import interface
from m3a_agent.env import json_action
from m3a_agent.env import representation_utils
from m3a_agent import screen_parser


ACTION_SELECTION_PROMPT_TEMPLATE = (
    'You are an agent who can operate an Android phone on behalf of a user.'
    ' Based on the user\'s goal or request, you may:\n'
    '- Communicate with the user by speaking to them or asking them a'
    ' question.\n'
    '- Complete tasks described in the user\'s request by performing actions'
    ' step by step on the phone.\n\n'
    'When given a user request, you will try to complete it step by step.'
    ' At each step, you will be given the current screenshot, including both'
    ' the original screenshot and the same screenshot with bounding boxes and'
    ' numeric indexes added to some UI elements, as well as a history of what'
    ' you have already done in text. Based on these inputs and the user\'s'
    ' goal, you must choose exactly one next action and output it in the'
    ' correct JSON format.\n\n'
    'There are two categories of actions:\n'
    '1. App actions, which operate the Android phone.\n'
    '2. Communication actions, which communicate with the user.\n\n'
    'Choose exactly one action for the current step. Do not combine an app'
    ' action with a communication action in the same step.\n\n'
    'The available actions are:\n'
    '- If you think the task has been completed, finish the task by using the'
    ' status action with complete as goal_status:'
    ' {{"action_type": "status", "goal_status": "complete"}}\n'
    '- If you think the task is not feasible, including cases where you do not'
    ' have enough information or cannot perform some necessary actions, finish'
    ' by using the status action with infeasible as goal_status:'
    ' {{"action_type": "status", "goal_status": "infeasible"}}\n'
    '- Speak to the user:'
    ' {{"action_type": "speak", "text": "<message_to_user>",'
    ' "visualization": <visualization_option>}}\n'
    '- Ask the user for input, confirmation, or a choice:'
    ' {{"action_type": "ask", "text": "<question_to_user>",'
    ' "visualization": <visualization_option>}}\n'
    '- Click or tap on an element on the screen. We have added marks, which'
    ' are bounding boxes with numeric indexes on their top-left corner, to'
    ' most UI elements in the screenshot. Use the numeric index to indicate'
    ' which element you want to click:'
    ' {{"action_type": "click", "index": <target_index>}}\n'
    '- Long press on an element on the screen:'
    ' {{"action_type": "long_press", "index": <target_index>}}\n'
    '- Type text into a text field. This action includes clicking the text'
    ' field, typing the text, and pressing Enter, so there is no need to'
    ' click the field first:'
    ' {{"action_type": "input_text", "text": "<text_input>",'
    ' "index": <target_index>}}\n'
    '- Press the Enter key: {{"action_type": "keyboard_enter"}}\n'
    '- Navigate to the home screen: {{"action_type": "navigate_home"}}\n'
    '- Navigate back: {{"action_type": "navigate_back"}}\n'
    '- Scroll the screen or a scrollable UI element in one of the four'
    ' directions. Use the same numeric index if you want to scroll a specific'
    ' UI element, and omit index when scrolling the whole screen:'
    ' {{"action_type": "scroll", "direction": "<up|down|left|right>",'
    ' "index": <optional_target_index>}}\n'
    '- Open an app. Nothing will happen if the app is not installed:'
    ' {{"action_type": "open_app", "app_name": "<name>"}}\n'
    '- Wait for the screen to update: {{"action_type": "wait"}}\n\n'
    'Communication actions include a visualization. Visualization is only'
    ' allowed for speak and ask actions. Do not attach visualization to app'
    ' actions such as click, input_text, scroll, open_app, navigate_back, or'
    ' any other action that changes the app screen, because such actions'
    ' would immediately invalidate the visualization.\n\n'
    'The available visualization types are:\n'
    '- Communicate only through voice:'
    ' {{"visualization_type": "none"}}\n'
    '- Show the whole current app screen to the user:'
    ' {{"visualization_type": "show_app"}}\n'
    '- Show one or more specific visible UI elements to the user, identified'
    ' by their target index or indexes:'
    ' {{"visualization_type": "show_element",'
    ' "index": [<target_index_1>, <target_index_2>]}}\n'
    '- Use the Generative UI Agent to generate a new user-facing UI:'
    ' {{"visualization_type": "generate_ui",'
    ' "instruction": "<self_contained_instruction_for_ui_generation>"}}\n\n'
    'Examples of how to use communication actions:\n'
    '- Speak with voice only:'
    ' {{"action_type": "speak", "text": "I have opened the app.",'
    ' "visualization": {{"visualization_type": "none"}}}}\n'
    '- Speak with a full-screen visualization:'
    ' {{"action_type": "speak",'
    ' "text": "I have finished your task. Here is the app.",'
    ' "visualization": {{"visualization_type": "show_app"}}}}\n'
    '- Ask with show_element:'
    ' {{"action_type": "ask",'
    ' "text": "What reply would you like to post?",'
    ' "visualization": {{"visualization_type": "show_element",'
    ' "index": [3, 25]}}}}\n'
    '- Speak with show_element:'
    ' {{"action_type": "speak",'
    ' "text": "Here is your current progress.",'
    ' "visualization": {{"visualization_type": "show_element",'
    ' "index": [41]}}}}\n'
    '- Ask with generated UI:'
    ' {{"action_type": "ask",'
    ' "text": "Which option do you want?",'
    ' "visualization": {{"visualization_type": "generate_ui",'
    ' "instruction": "Generate a simple selection UI with three buttons'
    ' labeled Option 1, Option 2, and Option 3."}}}}\n'
    '- Speak with generated UI:'
    ' {{"action_type": "speak",'
    ' "text": "Here is a summary of the results.",'
    ' "visualization": {{"visualization_type": "generate_ui",'
    ' "instruction": "Generate a concise report UI showing the key findings'
    ' as a title and three bullet points."}}}}\n'
    '- Speak with generated UI for a dense informational screen:'
    ' {{"action_type": "speak",'
    ' "text": "Here is today\'s weather summary.",'
    ' "visualization": {{"visualization_type": "generate_ui",'
    ' "instruction": "Generate a concise weather summary card for today.'
    ' Show the date, the overall morning conditions, the expected temperature'
    ' range, and the times when rain is expected. Present only the relevant'
    ' information in a clean and easy-to-read format."}}}}\n\n'
    'The current user goal/request is: {goal}\n\n'
    'The current screenshot and the same screenshot with bounding boxes and'
    ' labels added are also given to you.\n\n'
    'Here is a list of detailed information for some of the UI elements'
    ' (notice that some elements in this list may not be visible on the'
    ' current screen and so you cannot interact with them; you may need to'
    ' scroll the screen to reveal them first). The numeric indexes are'
    ' consistent with the ones in the labeled screenshot: {ui_elements}\n\n'
    'Here is a history of what you have done so far:\n{history}\n\n'
    'Here are some useful guidelines you need to follow:\n\n'
    '# General\n'
    '- Usually there will be multiple ways to complete a task. Pick the'
    ' easiest one.\n'
    '- When something does not work as expected, a simple retry can sometimes'
    ' solve the problem, but if it does not, and you can see that from the'
    ' history, switch to another solution.\n'
    '- Sometimes you may need to navigate the phone to gather information'
    ' needed to complete the task. For example, if the user asks "What is my'
    ' schedule tomorrow?", you may open the Calendar app, look up the'
    ' information there, communicate it to the user using speak, and then'
    ' finish using the status action with complete as goal_status.\n'
    '- If the desired state is already achieved, you can complete the task.\n'
    '- Use communication actions only when you genuinely need to tell the user'
    ' something or ask the user for information, confirmation, or a decision.'
    ' Most steps should still be app actions.\n\n'
    '# Communication\n'
    '- Use speak when you need to inform the user of something, such as'
    ' progress, observations, important app state, or the result of a'
    ' completed task.\n'
    '- Use ask when you need the user\'s input, confirmation, decision, or any'
    ' other response before you can continue.\n'
    '- Every speak and ask action must include a valid visualization field.\n'
    '- Before finishing the task with {{"action_type": "status",'
    ' "goal_status": "complete"}}, always use a speak action first to inform'
    ' the user of the final result or completion status.\n'
    '- Never fabricate content on behalf of the user. If the task requires'
    ' composing user-authored content, such as a message, email body, social'
    ' media post, reply, search query, comment, or review, and the user did'
    ' not specify what to write, you must use ask to ask them. Do not invent,'
    ' guess, or use placeholder content.\n'
    '- Never guess when multiple options match. If there are multiple contacts'
    ' named John, multiple Settings entries, multiple accounts, and so on,'
    ' ask the user which one they mean.\n'
    '- **Never assume unstated preferences.** If the task requires choosing'
    ' a size, quantity, flavor, address, payment method, time slot, etc.'
    ' is not specified, ask the user.\n'
    '- In general, if proceeding requires information that only the user can'
    ' provide, ask. If proceeding requires a choice the user would care'
    ' about, ask.\n\n'
    '# Visualization\n'
    '## Core decision rule\n'
    '- For informational requests where the user mainly wants an answer,'
    ' summary, extracted result, or status from the current screen, prefer'
    ' generate_ui by default.\n'
    '- Use show_element when the user needs to inspect a specific visible part'
    ' of the real app UI.\n'
    '- Use show_app only as a conservative last resort when the exact full'
    ' current app screen must be shown as-is and neither show_element nor'
    ' generate_ui is sufficient.\n'
    '- Exception: if the user explicitly asks to see, view, or show the app'
    ' page or screen itself, prefer show_app because the real screen is the'
    ' requested output.\n'
    '- Do not use show_app merely because the answer is visible on the current'
    ' screen. If the user mainly needs a concise answer or summary, prefer'
    ' generate_ui.\n\n'
    '## How and when to use show_element\n'
    '- Prefer show_element whenever a bounded visible region or parent'
    ' container provides sufficient context, because it is less intrusive and'
    ' takes less space than show_app.\n'
    '- Use show_element broadly. It does not have to refer to a single small'
    ' widget. You may use it to show a larger visible parent UI element or a'
    ' grouped region of the interface, as long as that indexed element'
    ' contains enough context for the communication.\n'
    '- Do not visualize only the exact UI element you intend to interact with'
    ' if that element alone is insufficient to understand the situation.\n'
    '- For example, if you ask the user to approve or write a reply, show not'
    ' only the reply field but also the relevant surrounding content, such as'
    ' the message or post being replied to.\n'
    '- You may provide multiple indexes for show_element.\n'
    '- In general, prefer show_element over show_app whenever it is'
    ' sufficient.\n\n'
    '## How and when to use generate_ui\n'
    '- Prefer generate_ui when the visible app screen contains substantially'
    ' more information than the user needs, even if the answer could be read'
    ' directly from the app.\n'
    '- Prefer generate_ui over show_app when the relevant information is'
    ' cluttered across the screen, spread across multiple regions, shown in a'
    ' long scrollable list, or would be easier for the user to understand in'
    ' a simplified and focused interface.\n'
    '- When answering informational questions from dense or cluttered app'
    ' screens, use generate_ui to present only the relevant extracted facts'
    ' in a concise user-facing view.\n'
    '- Use generate_ui when you want to present structured summaries,'
    ' extracted results, simplified choices, or custom user-facing controls'
    ' that are clearer than showing the raw app screen.\n'
    '- For generate_ui, the instruction must be concrete, self-contained, and'
    ' specific. It should clearly describe what information or controls the'
    ' generated UI must include.\n'
    '- Do not write vague instructions for generate_ui. The instruction should'
    ' contain all necessary details so that the generated UI is'
    ' understandable without relying on hidden context.\n'
    '- Never use generate_ui for tasks involving money or task-stakes such as'
    ' finance, purchases, ordering, payments, or similarly sensitive'
    ' decisions. In such cases, prefer showing the actual app UI instead.\n\n'
    '## How and when to use show_app\n'
    '- Use show_app only conservatively, when the full current app screen'
    ' itself is necessary for the user\'s understanding, and neither'
    ' show_element nor generate_ui is sufficient.\n'
    '- Use show_app when the user explicitly asks to see, view, or show the'
    ' current page or screen of the app itself. In such cases, the real app'
    ' screen is part of the requested output.\n'
    '- Do not use show_app simply because relevant information appears in'
    ' multiple places on the screen. If a simplified or focused presentation'
    ' would better serve the user, prefer generate_ui.\n'
    '- Do not use show_app when a bounded region, parent container, or small'
    ' set of indexed elements would provide enough context.\n'
    '- Use show_app only when the exact real-screen layout, full-screen'
    ' spatial context, or raw app fidelity is important for the user to'
    ' inspect directly.\n\n'
    '## Visualization summary table\n'
    '| Visualization | When to use | When not to use |\n'
    '|---|---|---|\n'
    '| none | Voice alone is sufficient. Simple status updates,'
    ' acknowledgements, or questions not depending on screen content. |'
    ' User needs to inspect app content, compare options, confirm a'
    ' selection, or view results visually. |\n'
    '| show_element | Default choice when a bounded visible region or small'
    ' set of visible UI elements provides enough context. | Selected region'
    ' is too small, too fragmented, or lacks surrounding context. |\n'
    '| generate_ui | Screen is dense, cluttered, fragmented, or contains'
    ' substantially more information than the user needs. | Never for'
    ' high-stakes and money involving tasks such as finance, purchases,'
    ' ordering, payments. |\n'
    '| show_app | Full current app screen is truly necessary. User explicitly'
    ' asks to see the app page. | Another method is sufficient. Do not use'
    ' merely because relevant info is visible on screen. |\n\n'
    '# Action related\n'
    '- Use the open_app action whenever you want to open an app. Do not use'
    ' the app drawer to open an app unless other ways have failed.\n'
    '- Use the input_text action whenever you want to type something,'
    ' including passwords, instead of clicking keyboard characters one by'
    ' one.\n'
    '- Sometimes there is default text in a text field. Delete it first if'
    ' needed.\n'
    '- For click, long_press, input_text, and scroll with an index, the index'
    ' you pick must be visible in the screenshot and also in the UI element'
    ' list.\n'
    '- Consider exploring the screen by using the scroll action in different'
    ' directions to reveal additional content.\n'
    '- The direction parameter for the scroll action can be confusing because'
    ' it is opposite to swipe. For example, to view content at the bottom,'
    ' the scroll direction should be set to down. If one direction does not'
    ' work, try the opposite as well.\n'
    '\n'
    '# Text related operations\n'
    '- Normally, to select certain text on the screen, first enter text'
    ' selection mode by long pressing the area where the text is. Then some'
    ' nearby words may be selected, and a text selection bar may appear with'
    ' options like copy, paste, and select all. Second, adjust the selection'
    ' if needed. Usually the initially selected text is not exactly what you'
    ' want.\n'
    '- At this point, you do not have the ability to drag arbitrary things'
    ' around the screen, so in general you cannot select arbitrary text ranges'
    ' reliably.\n'
    '- To delete text, the most traditional way is to place the cursor at the'
    ' right place and use the backspace button on the keyboard to delete'
    ' characters one by one. Another approach is to first select the text and'
    ' then press backspace.\n'
    '- To copy text, first select the exact text you want, then click the copy'
    ' button in the text selection bar.\n'
    '- To paste text into a text box, first long press the text box, then'
    ' click the paste button if it appears.\n'
    '- When typing into a text field, an auto-complete dropdown list may'
    ' appear. This usually indicates an enum-like field, and you should try to'
    ' select the best match from the list.\n'
    '{additional_guidelines}'
    'Now output exactly one action from the above list in the correct JSON'
    ' format, following the reason why you do that.\n'
    'Your answer should look like:\n'
    'Reason: ... Action: {{"action_type": ...}}\n\n'
    'Your Answer:\n'
)


SUMMARY_PROMPT_TEMPLATE = (
    'You are an agent who can operate an Android phone on behalf of a user.\n'
    'The (overall) user goal/request is: {goal}\n'
    'Now I want you to summarize the latest step.\n'
    'You will be given the screenshot before you performed the action (which'
    ' has a text label "before" on the bottom right), the action you chose'
    ' (together with the reason) and the screenshot after the action was'
    ' performed (which has a text label "after" on the bottom right).\n'
    'Also here is the list of detailed information for some UI elements'
    ' in the before screenshot:\n{before_elements}\n'
    'Here is the list for the after screenshot:\n{after_elements}\n'
    'This is the action you picked: {action}\n'
    'Based on the reason: {reason}\n\n'
    'By comparing the two screenshots (plus the UI element lists) and the'
    ' action performed, give a brief summary of this step. This summary'
    ' will be added to action history and used in future action selection,'
    ' so try to include essential information you think that will be most'
    ' useful for future action selections like what you'
    ' intended to do, why, if it worked as expected, if not'
    ' what might be the reason (be critical, the action/reason might be'
    ' wrong), what should/should not be done next and so on. Some more'
    ' rules/tips you should follow:\n'
    '- Keep it short (better less than 50 words) and in a single line\n'
    '- Communication actions (like speak, ask, wait) don\'t involve screen'
    ' change, you can just assume they work as expected.\n'
    '- Given this summary will be added into action history, it can be used as'
    ' memory to include information that needs to be remembered, or shared'
    ' between different apps.\n\n'
    'Summary of this step: '
)


def _generate_ui_element_description(
    ui_element: representation_utils.UIElement, index: int
) -> str:
  """Generate a description for a given UI element with important information.

  Args:
    ui_element: UI elements for the current screen.
    index: The numeric index for the UI element.

  Returns:
    The description for the UI element.
  """
  element_description = f'UI element {index}: {{"index": {index}, '
  if ui_element.text:
    element_description += f'"text": "{ui_element.text}", '
  if ui_element.content_description:
    element_description += (
        f'"content_description": "{ui_element.content_description}", '
    )
  if ui_element.hint_text:
    element_description += f'"hint_text": "{ui_element.hint_text}", '
  if ui_element.tooltip:
    element_description += f'"tooltip": "{ui_element.tooltip}", '
  element_description += (
      f'"is_clickable": {"True" if ui_element.is_clickable else "False"}, '
  )
  element_description += (
      '"is_long_clickable":'
      f' {"True" if ui_element.is_long_clickable else "False"}, '
  )
  element_description += (
      f'"is_editable": {"True" if ui_element.is_editable else "False"}, '
  )
  if ui_element.is_scrollable:
    element_description += '"is_scrollable": True, '
  if ui_element.is_focusable:
    element_description += '"is_focusable": True, '
  element_description += (
      f'"is_selected": {"True" if ui_element.is_selected else "False"}, '
  )
  element_description += (
      f'"is_checked": {"True" if ui_element.is_checked else "False"}, '
  )
  return element_description[:-2] + '}'


def _generate_ui_elements_description_list(
    ui_elements: list[representation_utils.UIElement],
    screen_width_height_px: tuple[int, int],
) -> str:
  """Generate concise information for a list of UIElement.

  Args:
    ui_elements: UI elements for the current screen.
    screen_width_height_px: The height and width of the screen in pixels.

  Returns:
    Concise information for each UIElement.
  """
  tree_info = ''
  for index, ui_element in enumerate(ui_elements):
    if m3a_utils.validate_ui_element(ui_element, screen_width_height_px):
      tree_info += _generate_ui_element_description(ui_element, index) + '\n'
  return tree_info


def _action_selection_prompt(
    goal: str,
    history: list[str],
    ui_elements: str,
    additional_guidelines: list[str] | None = None,
) -> str:
  """Generate the prompt for the action selection.

  Args:
    goal: The current goal.
    history: Summaries for previous steps.
    ui_elements: A list of descriptions for the UI elements.
    additional_guidelines: Task specific guidelines.

  Returns:
    The text prompt for action selection that will be sent to the LLM.
  """
  if history:
    history = '\n'.join(history)
  else:
    history = 'You just started, no action has been performed yet.'

  extra_guidelines = ''
  if additional_guidelines:
    extra_guidelines = 'For The Current Task:\n'
    for guideline in additional_guidelines:
      extra_guidelines += f'- {guideline}\n'

  return ACTION_SELECTION_PROMPT_TEMPLATE.format(
      goal=goal,
      history=history,
      ui_elements=ui_elements if ui_elements else 'Not available',
      additional_guidelines=extra_guidelines,
  )


def _summarize_prompt(
    action: str,
    reason: str,
    goal: str,
    before_elements: str,
    after_elements: str,
) -> str:
  """Generate the prompt for the summarization step.

  Args:
    action: Action picked.
    reason: The reason to pick the action.
    goal: The overall goal.
    before_elements: Information for UI elements on the before screenshot.
    after_elements: Information for UI elements on the after screenshot.

  Returns:
    The text prompt for summarization that will be sent to the LLM.
  """
  return SUMMARY_PROMPT_TEMPLATE.format(
      goal=goal,
      before_elements=before_elements,
      after_elements=after_elements,
      action=action,
      reason=reason,
  )


class M3A(base_agent.EnvironmentInteractingAgent):
  """M3A which stands for Multimodal Autonomous Agent for Android."""

  def __init__(
      self,
      env: interface.AsyncEnv,
      llm: infer.MultimodalLlmWrapper,
      name: str = 'M3A',
      wait_after_action_seconds: float = 2.0,
  ):
    """Initializes a M3A Agent.

    Args:
      env: The environment.
      llm: The multimodal LLM wrapper.
      name: The agent name.
      wait_after_action_seconds: Seconds to wait for the screen to stablize
        after executing an action
    """
    super().__init__(env, name)
    self.llm = llm
    self.history = []
    self.additional_guidelines = None
    self.wait_after_action_seconds = wait_after_action_seconds

  def set_task_guidelines(self, task_guidelines: list[str]) -> None:
    self.additional_guidelines = task_guidelines

  def reset(self, go_home_on_reset: bool = False):
    super().reset(go_home_on_reset)
    # Hide the coordinates on screen which might affect the vision model.
    self.env.hide_automation_ui()
    self.history = []

  def step(self, goal: str) -> base_agent.AgentInteractionResult:
    step_data = {
        'raw_screenshot': None,
        'before_screenshot_with_som': None,
        'before_ui_elements': [],
        'after_screenshot_with_som': None,
        'action_prompt': None,
        'action_output': None,
        'action_output_json': None,
        'action_reason': None,
        'action_raw_response': None,
        'summary_prompt': None,
        'summary': None,
        'summary_raw_response': None,
    }
    logging.info('----------step %s----------', str(len(self.history) + 1))

    state = self.get_post_transition_state()
    logical_screen_size = self.env.logical_screen_size
    orientation = self.env.orientation
    physical_frame_boundary = self.env.physical_frame_boundary

    before_ui_elements = state.ui_elements
    step_data['before_ui_elements'] = before_ui_elements
    before_ui_elements_list = _generate_ui_elements_description_list(
        before_ui_elements, logical_screen_size
    )

    # Parse semantic UI groups for show_element visualization
    raw_xml = self.env.controller.last_xml
    if raw_xml:
      ui_groups = screen_parser.parse_ui_groups(
          raw_xml,
          screen_width=logical_screen_size[0],
          screen_height=logical_screen_size[1],
      )
      ui_groups_text = screen_parser.format_groups_for_llm(ui_groups)
      logging.info('[groups] parsed %d groups:\n%s', len(ui_groups), ui_groups_text)
      step_data['ui_groups'] = ui_groups
    else:
      ui_groups_text = ''
      step_data['ui_groups'] = []
    step_data['raw_screenshot'] = state.pixels.copy()
    before_screenshot = state.pixels.copy()
    for index, ui_element in enumerate(before_ui_elements):
      if m3a_utils.validate_ui_element(ui_element, logical_screen_size):
        m3a_utils.add_ui_element_mark(
            before_screenshot,
            ui_element,
            index,
            logical_screen_size,
            physical_frame_boundary,
            orientation,
        )
    step_data['before_screenshot_with_som'] = before_screenshot.copy()

    action_prompt = _action_selection_prompt(
        goal,
        [
            'Step ' + str(i + 1) + '- ' + step_info['summary']
            for i, step_info in enumerate(self.history)
        ],
        ui_groups_text or before_ui_elements_list,
        self.additional_guidelines,
    )
    step_data['action_prompt'] = action_prompt
    action_output, is_safe, raw_response = self.llm.predict_mm(
        action_prompt,
        [
            step_data['raw_screenshot'],
            before_screenshot,
        ],
    )

    if is_safe == False:  # pylint: disable=singleton-comparison
      #  is_safe could be None
      action_output = f"""Reason: {m3a_utils.TRIGGER_SAFETY_CLASSIFIER}
Action: {{"action_type": "status", "goal_status": "infeasible"}}"""

    if not raw_response:
      raise RuntimeError('Error calling LLM in action selection phase.')
    step_data['action_output'] = action_output
    step_data['action_raw_response'] = raw_response

    reason, action = m3a_utils.parse_reason_action_output(action_output)

    # If the output is not in the right format, add it to step summary which
    # will be passed to next step and return.
    if (not reason) or (not action):
      logging.info('Action prompt output is not in the correct format.')
      step_data['summary'] = (
          'Output for action selection is not in the correct format, so no'
          ' action is performed.'
      )
      self.history.append(step_data)

      return base_agent.AgentInteractionResult(
          False,
          step_data,
      )

    logging.info('Action: %s', action)
    logging.info('Reason: %s', reason)
    step_data['action_reason'] = reason

    try:
      converted_action = json_action.JSONAction(
          **agent_utils.extract_json(action),
      )
      step_data['action_output_json'] = converted_action
    except Exception as e:  # pylint: disable=broad-exception-caught
      logging.info('Failed to convert the output to a valid action.')
      logging.info(str(e))
      step_data['summary'] = (
          'Can not parse the output to a valid action. Please make sure to pick'
          ' the action from the list with required parameters (if any) in the'
          ' correct JSON format!'
      )
      self.history.append(step_data)

      return base_agent.AgentInteractionResult(
          False,
          step_data,
      )

    action_index = converted_action.index
    num_ui_elements = len(before_ui_elements)
    if (
        converted_action.action_type
        in ['click', 'long_press', 'input_text', 'scroll']
        and action_index is not None
    ):
      if action_index >= num_ui_elements:
        logging.info(
            'Index out of range, prediction index is %s, but the'
            ' UI element list only has %d elements.',
            action_index,
            num_ui_elements,
        )
        step_data['summary'] = (
            'The parameter index is out of range. Remember the index must be in'
            ' the UI element list!'
        )
        self.history.append(step_data)
        return base_agent.AgentInteractionResult(False, step_data)

      # Add mark to the target element.
      m3a_utils.add_ui_element_mark(
          step_data['raw_screenshot'],
          before_ui_elements[action_index],
          action_index,
          logical_screen_size,
          physical_frame_boundary,
          orientation,
      )

    if converted_action.action_type == 'status':
      if converted_action.goal_status == 'infeasible':
        logging.info('Agent stopped since it thinks mission impossible.')
      step_data['summary'] = 'Agent thinks the request has been completed.'
      self.history.append(step_data)
      return base_agent.AgentInteractionResult(
          True,
          step_data,
      )

    # Communication actions (speak, ask) don't change the screen.
    if converted_action.action_type in ('speak', 'ask'):
      logging.info(
          'Agent %s: %s', converted_action.action_type, converted_action.text
      )
      if converted_action.visualization:
        logging.info('Visualization: %s', converted_action.visualization)

      try:
        self.env.execute_action(converted_action)
      except Exception as e:  # pylint: disable=broad-exception-caught
        logging.info('Failed to execute communication action: %s', e)

      if converted_action.action_type == 'speak':
        step_data['summary'] = f'Spoke to user: {converted_action.text}'
      else:
        step_data['summary'] = f'Asked user: {converted_action.text}'
      self.history.append(step_data)
      return base_agent.AgentInteractionResult(False, step_data)

    # Legacy answer action — same as speak.
    if converted_action.action_type == 'answer':
      logging.info('Agent answered with: %s', converted_action.text)

    try:
      self.env.execute_action(converted_action)
    except Exception as e:  # pylint: disable=broad-exception-caught
      logging.info('Failed to execute action.')
      logging.info(str(e))
      step_data['summary'] = (
          'Can not execute the action, make sure to select the action with'
          ' the required parameters (if any) in the correct JSON format!'
      )
      return base_agent.AgentInteractionResult(
          False,
          step_data,
      )

    time.sleep(self.wait_after_action_seconds)

    state = self.env.get_state(wait_to_stabilize=False)
    logical_screen_size = self.env.logical_screen_size
    orientation = self.env.orientation
    physical_frame_boundary = self.env.physical_frame_boundary
    after_ui_elements = state.ui_elements
    after_ui_elements_list = _generate_ui_elements_description_list(
        after_ui_elements, logical_screen_size
    )
    after_screenshot = state.pixels.copy()
    for index, ui_element in enumerate(after_ui_elements):
      if m3a_utils.validate_ui_element(ui_element, logical_screen_size):
        m3a_utils.add_ui_element_mark(
            after_screenshot,
            ui_element,
            index,
            logical_screen_size,
            physical_frame_boundary,
            orientation,
        )

    m3a_utils.add_screenshot_label(
        step_data['before_screenshot_with_som'], 'before'
    )
    m3a_utils.add_screenshot_label(after_screenshot, 'after')
    step_data['after_screenshot_with_som'] = after_screenshot.copy()

    summary_prompt = _summarize_prompt(
        action,
        reason,
        goal,
        before_ui_elements_list,
        after_ui_elements_list,
    )
    summary, is_safe, raw_response = self.llm.predict_mm(
        summary_prompt,
        [
            before_screenshot,
            after_screenshot,
        ],
    )

    if is_safe == False:  # pylint: disable=singleton-comparison
      #  is_safe could be None
      summary = """Summary triggered LLM safety classifier."""

    if not raw_response:
      logging.info(
          'Error calling LLM in summarization phase. This should not'
          ' happen: %s',
          summary,
      )
      step_data['summary'] = (
          'Some error occurred calling LLM during summarization phase: %s'
          % summary
      )
      self.history.append(step_data)
      return base_agent.AgentInteractionResult(
          False,
          step_data,
      )

    step_data['summary_prompt'] = summary_prompt
    step_data['summary'] = f'Action selected: {action}. {summary}'
    logging.info('Summary: %s', summary)
    step_data['summary_raw_response'] = raw_response

    self.history.append(step_data)
    return base_agent.AgentInteractionResult(
        False,
        step_data,
    )
