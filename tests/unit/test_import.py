from importlib.metadata import version

from litellm.llms.openai.chat.gpt_5_transformation import OpenAIGPT5Config
from packaging.version import Version

from openhands.app_server.utils.logger import openhands_logger
from openhands.sdk import LLM
from server.auth.sheets_client import GoogleSheetsClient


def test_import():
    assert openhands_logger is not None
    assert GoogleSheetsClient is not None


def test_sdk_security_dependencies():
    assert Version(version('openhands-sdk')) >= Version('1.63.0')
    assert Version(version('litellm')) >= Version('1.108.3')
    assert Version(version('filelock')) >= Version('4.2.1')


def test_sdk_gpt5_litellm_compatibility():
    assert OpenAIGPT5Config is not None
    llm = LLM(model='openai/gpt-5', api_key='test-not-a-real-key')
    assert llm.model == 'openai/gpt-5'
