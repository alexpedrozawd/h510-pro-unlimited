import pytest
import script_h510_pro


@pytest.fixture(autouse=True)
def reset_module_state():
    original_running = script_h510_pro.is_running
    original_mode = script_h510_pro._last_mode
    original_key = script_h510_pro.API_KEY
    yield
    script_h510_pro.is_running = original_running
    script_h510_pro._last_mode = original_mode
    script_h510_pro.API_KEY = original_key
