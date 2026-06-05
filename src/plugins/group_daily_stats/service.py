from __future__ import annotations

from .store import FlushResult, GroupDailyStatsStore, GroupStatsSnapshot, InteractionStats


stats_store = GroupDailyStatsStore()


async def is_stats_enabled(group_id: str) -> bool:
    return await stats_store.is_enabled(str(group_id))


async def enable_stats(group_id: str) -> bool:
    return await stats_store.enable_group(str(group_id))


async def disable_stats(group_id: str) -> bool:
    return await stats_store.disable_group(str(group_id))


async def list_enabled_stats_groups() -> list[str]:
    return await stats_store.list_enabled_groups()


async def record_message_interaction(
    *,
    group_id: str,
    sender_id: str,
    interaction: InteractionStats,
) -> None:
    await stats_store.record_interaction(
        group_id=str(group_id),
        sender_id=str(sender_id),
        interaction=interaction,
    )


async def record_homophone_usage(
    *,
    group_id: str,
    user_id: str,
    boxed_member_ids: list[str],
) -> None:
    await stats_store.record_homophone_usage(
        group_id=str(group_id),
        user_id=str(user_id),
        boxed_member_ids=boxed_member_ids,
    )


async def get_group_stats_snapshot(group_id: str) -> GroupStatsSnapshot:
    return await stats_store.get_group_snapshot(str(group_id))


async def flush_daily_stats() -> FlushResult:
    return await stats_store.flush_daily_snapshots()
