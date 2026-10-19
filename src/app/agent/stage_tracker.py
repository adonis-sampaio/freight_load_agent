"""Guarded load-stage transitions and per-stop remaining-action queries."""

from datetime import UTC, datetime

from models.common import LoadStage
from models.load import Load, Stop


class StageTransitionError(ValueError):
    """Raised when a requested load stage skips the lifecycle."""


_NEXT_STAGE = {
    LoadStage.DISPATCHED: LoadStage.TO_PICKUP,
    LoadStage.TO_PICKUP: LoadStage.AT_PICKUP,
    LoadStage.AT_PICKUP: LoadStage.TO_DELIVERY,
    LoadStage.TO_DELIVERY: LoadStage.AT_DELIVERY,
    LoadStage.AT_DELIVERY: LoadStage.DELIVERED,
}


class StageTracker:
    def transition(self, load: Load, target: LoadStage | str) -> Load:
        try:
            target_stage = LoadStage(target)
        except ValueError as error:
            raise StageTransitionError("target is not a defined load stage") from error

        current_stage = load.current_stage
        if target_stage == current_stage:
            return load
        if _NEXT_STAGE.get(current_stage) != target_stage:
            raise StageTransitionError(
                f"{target_stage.value} is not the next valid stage after "
                f"{current_stage.value}"
            )
        return load.model_copy(
            update={
                "current_stage": target_stage,
                "updated_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def remaining_stops(
        load: Load,
        *,
        completed_stop_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[Stop, ...]:
        known_ids = {stop.stop_id for stop in load.stops}
        unknown_ids = completed_stop_ids - known_ids
        if unknown_ids:
            raise ValueError(
                f"unknown completed stop identifiers: {', '.join(sorted(unknown_ids))}"
            )
        return tuple(
            stop
            for stop in sorted(load.stops, key=lambda item: item.sequence)
            if stop.stop_id not in completed_stop_ids
        )
