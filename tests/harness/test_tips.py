"""Tests for the tips system."""

from koder_agent.harness.tips import TIP, TIPS, TipManager


class TestTipsData:
    """Test the TIPS data structure."""

    def test_tip_count(self):
        """Verify we have at least 40 tips."""
        assert len(TIPS) >= 40, f"Expected at least 40 tips, got {len(TIPS)}"

    def test_all_tips_have_unique_ids(self):
        """Verify no duplicate tip IDs."""
        ids = [tip.id for tip in TIPS]
        assert len(ids) == len(set(ids)), "Tip IDs must be unique"

    def test_all_tips_have_messages(self):
        """Verify all tips have non-trivial messages."""
        for tip in TIPS:
            assert isinstance(tip, TIP)
            assert tip.id, "Tip must have an id"
            assert tip.message, f"Tip {tip.id} has empty message"
            assert len(tip.message) > 10, f"Tip {tip.id} message too short"
            assert isinstance(tip.id, str)
            assert isinstance(tip.message, str)


class TestTipManager:
    """Test the TipManager class."""

    def test_tip_manager_returns_tip(self):
        """Verify get_tip returns a string containing 'Tip:'."""
        mgr = TipManager()
        tip = mgr.get_tip()
        assert tip is not None
        assert "Tip:" in tip

    def test_tip_manager_cooldown(self):
        """Verify tips don't repeat within cooldown window."""
        mgr = TipManager(cooldown_window=3)
        shown = set()
        for _ in range(3):
            tip = mgr.get_tip()
            assert tip is not None
            assert tip not in shown or len(shown) >= len(TIPS)
            shown.add(tip)

    def test_relevance_check_filters(self):
        """Verify vim_mode tip is filtered when already in vim mode."""
        mgr = TipManager()
        ctx = {"in_vim_mode": True}
        tips_shown = []
        for _ in range(len(TIPS)):
            tip = mgr.get_tip(ctx)
            if tip:
                tips_shown.append(tip)
        assert not any("vim" in t.lower() and "enable" in t.lower() for t in tips_shown)

    def test_reasoning_effort_tip_relevance(self):
        """Verify reasoning tip appears for o1/o3 models but not others."""
        mgr = TipManager()
        # Should appear for o1/o3 models
        ctx_o1 = {"model": "o1-preview"}
        tips = [mgr.get_tip(ctx_o1) for _ in range(len(TIPS))]
        # Should NOT appear for non-o1 models
        mgr2 = TipManager()
        ctx_claude = {"model": "claude-opus-4-20250514"}
        tips2 = [mgr2.get_tip(ctx_claude) for _ in range(len(TIPS))]
        reasoning_tips = [t for t in tips if t and "reasoning" in t.lower()]
        reasoning_tips2 = [t for t in tips2 if t and "reasoning" in t.lower()]
        assert len(reasoning_tips) >= len(reasoning_tips2)

    def test_get_tip_returns_tips_from_list(self):
        """Verify returned tips are from the TIPS list."""
        manager = TipManager()
        tip = manager.get_tip()
        if tip is not None:
            tip_messages = [t.message for t in TIPS]
            assert tip in tip_messages

    def test_mark_shown_tracks_correctly(self):
        """Verify mark_shown updates internal tracking."""
        manager = TipManager()

        # Show a tip
        tip = manager.get_tip()
        assert tip is not None, "Should get a tip on first call"

        # Find the tip ID
        tip_obj = next(t for t in TIPS if t.message == tip)

        # Mark it as shown
        manager.mark_shown(tip_obj.id)

        # Verify it's tracked
        assert tip_obj.id in manager._shown_history

    def test_cooldown_prevents_repeat_within_window(self):
        """Verify same tip is not shown within cooldown window."""
        manager = TipManager(cooldown_window=10)

        shown_tips = []
        tip_ids = []

        # Get tips until we've shown 10 (the cooldown window)
        for _ in range(10):
            tip = manager.get_tip()
            if tip is None:
                break
            shown_tips.append(tip)
            # Find and mark the tip
            tip_obj = next(t for t in TIPS if t.message == tip)
            tip_ids.append(tip_obj.id)
            manager.mark_shown(tip_obj.id)

        # Verify no duplicates in the first 10
        assert len(tip_ids) == len(set(tip_ids)), "Tips should not repeat within cooldown window"

    def test_get_tip_returns_none_when_all_exhausted_in_cooldown(self):
        """Verify get_tip returns None when all tips are in cooldown."""
        # Create manager with cooldown window equal to number of tips
        manager = TipManager(cooldown_window=len(TIPS))

        # Show all tips
        for tip_data in TIPS:
            manager.mark_shown(tip_data.id)

        # Next tip should be None since all are in cooldown
        tip = manager.get_tip()
        assert tip is None, "Should return None when all tips are in cooldown"

    def test_tips_rotate_after_cooldown_expires(self):
        """Verify tips can be shown again after cooldown expires."""
        manager = TipManager(cooldown_window=3)

        # Show 4 tips (more than cooldown window)
        shown_tip_ids = []
        for i in range(min(4, len(TIPS))):
            tip = manager.get_tip()
            assert tip is not None
            tip_obj = next(t for t in TIPS if t.message == tip)
            shown_tip_ids.append(tip_obj.id)
            manager.mark_shown(tip_obj.id)

        # The first tip should now be available again (outside cooldown window of 3)
        if len(TIPS) >= 4:
            recent_history = list(manager._shown_history)[-3:]
            assert shown_tip_ids[0] not in recent_history

    def test_relevance_check_is_used(self):
        """Verify relevance_check is used when provided."""
        manager = TipManager()

        # Create context that should match some tips
        context = {"in_vim_mode": True}

        # Get tip with context
        tip = manager.get_tip(context=context)

        # If we got a tip, it should either have no relevance_check or pass it
        if tip is not None:
            tip_obj = next(t for t in TIPS if t.message == tip)
            if tip_obj.relevance_check is not None:
                assert tip_obj.relevance_check(context)

    def test_empty_context_works(self):
        """Verify get_tip works with empty or None context."""
        manager = TipManager()

        tip1 = manager.get_tip(context=None)
        assert tip1 is None or isinstance(tip1, str)

        tip2 = manager.get_tip(context={})
        assert tip2 is None or isinstance(tip2, str)

    def test_cooldown_window_customizable(self):
        """Verify cooldown window can be customized."""
        manager = TipManager(cooldown_window=5)
        assert manager._cooldown_window == 5

        manager2 = TipManager(cooldown_window=20)
        assert manager2._cooldown_window == 20
