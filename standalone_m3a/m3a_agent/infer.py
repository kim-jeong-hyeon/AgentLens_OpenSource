"""LLM inference interfaces for M3A agent."""

import abc
import base64
import io
import os
import time
from typing import Any, Optional
import numpy as np
from PIL import Image
import requests


ERROR_CALLING_LLM = 'Error calling LLM'


def array_to_jpeg_bytes(image: np.ndarray) -> bytes:
  """Converts a numpy array into a byte string for a JPEG image."""
  image = Image.fromarray(image)
  return image_to_jpeg_bytes(image)


def image_to_jpeg_bytes(image: Image.Image) -> bytes:
  in_mem_file = io.BytesIO()
  image.save(in_mem_file, format='JPEG')
  in_mem_file.seek(0)
  img_bytes = in_mem_file.read()
  return img_bytes


class LlmWrapper(abc.ABC):
  """Abstract interface for (text only) LLM."""

  @abc.abstractmethod
  def predict(
      self,
      text_prompt: str,
  ) -> tuple[str, Optional[bool], Any]:
    """Calling text-only LLM with a prompt."""


class MultimodalLlmWrapper(abc.ABC):
  """Abstract interface for Multimodal LLM."""

  @abc.abstractmethod
  def predict_mm(
      self, text_prompt: str, images: list[np.ndarray]
  ) -> tuple[str, Optional[bool], Any]:
    """Calling multimodal LLM with a prompt and a list of images."""


class Gpt4Wrapper(LlmWrapper, MultimodalLlmWrapper):
  """OpenAI GPT wrapper.

  Attributes:
    openai_api_key: The OpenAI api key from env variable.
    max_retry: Max number of retries when some error happens.
    temperature: The temperature parameter in LLM to control result stability.
      Ignored for reasoning models.
    model: GPT model to use.
    reasoning_effort: Reasoning effort for reasoning models (e.g. gpt-5.4).
      Set to None to disable explicit reasoning effort.
  """

  RETRY_WAITING_SECONDS = 20

  # Models that use the reasoning API (no temperature, use max_completion_tokens).
  _REASONING_MODELS = ('o1', 'o3', 'o4', 'gpt-5')

  def __init__(
      self,
      model_name: str = 'gpt-5.4',
      max_retry: int = 3,
      temperature: float = 0.0,
      reasoning_effort: str | None = None,
  ):
    if 'OPENAI_API_KEY' not in os.environ:
      raise RuntimeError('OpenAI API key not set. Set OPENAI_API_KEY env var.')
    self.openai_api_key = os.environ['OPENAI_API_KEY']
    if max_retry <= 0:
      max_retry = 3
      print('Max_retry must be positive. Reset it to 3')
    self.max_retry = min(max_retry, 5)
    self.temperature = temperature
    self.reasoning_effort = reasoning_effort
    self.model = model_name

  @property
  def _is_reasoning_model(self) -> bool:
    return any(self.model.startswith(p) for p in self._REASONING_MODELS)

  @classmethod
  def encode_image(cls, image: np.ndarray) -> str:
    return base64.b64encode(array_to_jpeg_bytes(image)).decode('utf-8')

  def predict(
      self,
      text_prompt: str,
  ) -> tuple[str, Optional[bool], Any]:
    return self.predict_mm(text_prompt, [])

  def predict_mm(
      self, text_prompt: str, images: list[np.ndarray]
  ) -> tuple[str, Optional[bool], Any]:
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {self.openai_api_key}',
    }

    payload = {
        'model': self.model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': text_prompt},
            ],
        }],
    }

    if self._is_reasoning_model:
      payload['max_completion_tokens'] = 5000
      if self.reasoning_effort is not None:
        payload['reasoning'] = {'effort': self.reasoning_effort}
    else:
      payload['temperature'] = self.temperature
      payload['max_tokens'] = 1000

    for image in images:
      payload['messages'][0]['content'].append({
          'type': 'image_url',
          'image_url': {
              'url': f'data:image/jpeg;base64,{self.encode_image(image)}'
          },
      })

    # Log every LLM request so we can see exactly what the model receives.
    # Images are logged only as (index, HxWxC) — base64 payloads are huge
    # and would drown the console.
    import logging as _logging
    _img_summary = ', '.join(
        f'#{i}:{img.shape[1]}x{img.shape[0]}' for i, img in enumerate(images)
    ) if images else 'none'
    _logging.info(
        '[LLM] model=%s text_len=%d images=[%s]',
        self.model, len(text_prompt), _img_summary,
    )
    _logging.info('[LLM] >>> PROMPT START >>>\n%s\n<<< PROMPT END <<<',
                  text_prompt)

    counter = self.max_retry
    wait_seconds = self.RETRY_WAITING_SECONDS
    while counter > 0:
      try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=payload,
        )
        if response.ok and 'choices' in response.json():
          _content = response.json()['choices'][0]['message']['content']
          _logging.info('[LLM] >>> RESPONSE START >>>\n%s\n<<< RESPONSE END <<<',
                        _content)
          return (_content, None, response)
        print(
            'Error calling OpenAI API with error message: '
            + response.json()['error']['message']
        )
        time.sleep(wait_seconds)
        wait_seconds *= 2
      except Exception as e:
        time.sleep(wait_seconds)
        wait_seconds *= 2
        counter -= 1
        print('Error calling LLM, will retry soon...')
        print(e)
    return ERROR_CALLING_LLM, None, None
