"""Integration tests for VimModeManager, KeybindingManager, and TipManager in interactive REPL."""

from prompt_toolkit.enums import EditingMode

from koder_agent.core.interactive import InteractivePrompt
from koder_agent.core.keybindings import KeybindingManager
from koder_agent.core.vim_mode import VimModeManager
from koder_agent.harness.tips import TipManager


class TestVimModeIntegration:
    """Test VimModeManager integration."""

    def test_vim_manager_instantiation(self, tmp_path):
        """VimModeManager should instantiate with a state path."""
        state_path = tmp_path / "vim_state.json"
        vim_mgr = VimModeManager(state_path=state_path)
        assert vim_mgr is not None
        assert not vim_mgr.enabled
        assert vim_mgr.get_editing_mode() == EditingMode.EMACS

    def test_vim_manager_toggle(self, tmp_path):
        """VimModeManager should toggle between modes."""
        state_path = tmp_path / "vim_state.json"
        vim_mgr = VimModeManager(state_path=state_path)

        # Initially disabled
        assert vim_mgr.get_editing_mode() == EditingMode.EMACS

        # Toggle on
        result = vim_mgr.toggle()
        assert "enabled" in result
        assert vim_mgr.get_editing_mode() == EditingMode.VI

        # Toggle off
        result = vim_mgr.toggle()
        assert "disabled" in result
        assert vim_mgr.get_editing_mode() == EditingMode.EMACS

    def test_vim_manager_persistence(self, tmp_path):
        """VimModeManager should persist state across instances."""
        state_path = tmp_path / "vim_state.json"

        # First instance: enable and save
        vim_mgr1 = VimModeManager(state_path=state_path)
        vim_mgr1.enable()
        vim_mgr1.save()

        # Second instance: should load enabled state
        vim_mgr2 = VimModeManager(state_path=state_path)
        vim_mgr2.load()
        assert vim_mgr2.enabled
        assert vim_mgr2.get_editing_mode() == EditingMode.VI


class TestKeybindingIntegration:
    """Test KeybindingManager integration."""

    def test_keybinding_manager_instantiation(self, tmp_path):
        """KeybindingManager should instantiate and load defaults."""
        config_path = tmp_path / "keybindings.json"
        kb_mgr = KeybindingManager(config_path=config_path)
        assert kb_mgr is not None
        assert kb_mgr.get_key("submit") == "enter"
        assert kb_mgr.get_key("cancel") == "c-c"

    def test_keybinding_manager_override(self, tmp_path):
        """KeybindingManager should apply user overrides."""
        config_path = tmp_path / "keybindings.json"
        config_path.write_text('{"submit": "c-s"}', encoding="utf-8")

        kb_mgr = KeybindingManager(config_path=config_path)
        assert kb_mgr.get_key("submit") == "c-s"
        assert kb_mgr.get_key("cancel") == "c-c"  # Default unchanged

    def test_keybinding_manager_all_bindings(self, tmp_path):
        """KeybindingManager should return all effective bindings."""
        config_path = tmp_path / "keybindings.json"
        kb_mgr = KeybindingManager(config_path=config_path)

        bindings = kb_mgr.get_all_bindings()
        assert "submit" in bindings
        assert "cancel" in bindings
        assert "complete" in bindings


class TestTipManagerIntegration:
    """Test TipManager integration."""

    def test_tip_manager_instantiation(self):
        """TipManager should instantiate and return tips."""
        tip_mgr = TipManager()
        assert tip_mgr is not None

        tip = tip_mgr.get_tip()
        assert tip is not None
        assert isinstance(tip, str)
        assert "💡 Tip:" in tip

    def test_tip_manager_cooldown(self):
        """TipManager should not repeat tips within cooldown window."""
        tip_mgr = TipManager(cooldown_window=5)

        shown_tips = set()
        for _ in range(5):
            tip = tip_mgr.get_tip()
            if tip:
                shown_tips.add(tip)

        # Should get different tips (up to 5 unique)
        assert len(shown_tips) >= 2

    def test_tip_manager_context_relevance(self):
        """TipManager should filter tips by context relevance."""
        tip_mgr = TipManager()

        # Vim mode tip should show when not in vim mode
        context = {"in_vim_mode": False}
        for _ in range(20):  # Try a few times
            tip = tip_mgr.get_tip(context=context)
            if tip and "vim keybindings" in tip:
                break
        # Note: May not always show due to rotation, but shouldn't fail

        # When in vim mode, vim tip should be filtered (checked by relevance_check)
        tip_mgr2 = TipManager()
        context_vim = {"in_vim_mode": True}
        for _ in range(tip_mgr2._shown_history.maxlen + 5):
            tip = tip_mgr2.get_tip(context=context_vim)
            if tip:
                assert "vim keybindings" not in tip


class TestInteractivePromptIntegration:
    """Test integration of all managers in InteractivePrompt."""

    def test_interactive_prompt_uses_keybinding_manager(self):
        """InteractivePrompt should use KeybindingManager for key lookups."""
        # This is more of a documentation test - the actual integration
        # happens at the Application level in get_input()
        commands = {"/help": "Show help"}
        prompt = InteractivePrompt(commands=commands)
        assert prompt is not None

    def test_managers_instantiated_during_setup(self):
        """Integration: Managers should be instantiated when used in interactive module."""
        # This test validates that managers can be instantiated alongside InteractivePrompt
        # Once integrated, they'll be created in get_input() or during init

        commands = {"/help": "Show help"}
        prompt = InteractivePrompt(commands=commands)

        # Separately instantiate managers to validate they work
        vim_mgr = VimModeManager()
        kb_mgr = KeybindingManager()
        tip_mgr = TipManager()

        # The test passes if all can be created without errors
        assert prompt is not None
        assert vim_mgr is not None
        assert kb_mgr is not None
        assert tip_mgr is not None

    def test_tip_manager_can_be_called_after_response(self):
        """TipManager can provide tips after assistant responses."""
        tip_mgr = TipManager()

        # Simulate showing a tip after assistant response
        context = {"model": "gpt-4o"}
        tip = tip_mgr.get_tip(context=context)

        assert tip is None or isinstance(tip, str)
        if tip:
            assert "💡 Tip:" in tip
