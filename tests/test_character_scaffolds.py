import pytest

from sakaki_gm.ui.modules.characters import _is_stat_block_scaffold, _optional_int


def test_stat_block_scaffold_detection():
    assert _is_stat_block_scaffold({
        "stat_block_status": "Design scaffold; mechanics unassigned.",
    })
    assert not _is_stat_block_scaffold({"stat_block_status": "Complete"})
    assert not _is_stat_block_scaffold({})


def test_optional_integer_preserves_unassigned_fields():
    assert _optional_int("") is None
    assert _optional_int("  ") is None
    assert _optional_int("18") == 18
    with pytest.raises(ValueError):
        _optional_int("not a number")
